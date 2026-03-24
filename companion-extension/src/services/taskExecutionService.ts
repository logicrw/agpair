/**
 * Task Execution Service — orchestrates run_task / continue_task.
 *
 * Connects the bridge HTTP handlers to the SDK session controller.
 * Emits ACK synchronously, then monitors for RUNNING/terminal events
 * via the monitor controller and structured output parsing.
 *
 * Quarantine guard: continueTask() and monitor auto-rebind refuse
 * sessions listed in ~/.agpair/quarantine_registry.json.
 *
 * Spec: codex_antigravity_companion_extension_ts_spec.md §5-§8
 */

import { SessionController } from "../sdk/sessionController";
import { TaskSessionStore, TaskSession } from "../state/taskSessionStore";
import { PendingEventStore, PendingEvent } from "../state/pendingEventStore";
import { MonitorController } from "../sdk/monitorController";

/** Bridge connection info injected after port binding. */
export interface BridgeContext {
  port: number;
  authToken: string;
}

export interface RunTaskRequest {
  task_id: string;
  attempt_no: number;
  review_round: number;
  repo_path: string;
  branch?: string;
  prompt: string;
}

export interface RunTaskResponse {
  ok: boolean;
  status: string;
  task_id: string;
  attempt_no: number;
  review_round: number;
  session_id: string;
  source_event_id: string;
  source_seq: number;
  emitted_at: string;
  message: string;
}

export interface ContinueTaskRequest {
  task_id: string;
  attempt_no: number;
  review_round: number;
  repo_path?: string;
  branch?: string;
  prompt: string;
}

export class TaskExecutionService {
  private bridgeCtx: BridgeContext | null = null;

  constructor(
    private readonly sessionCtrl: SessionController,
    private readonly sessionStore: TaskSessionStore,
    private readonly eventStore: PendingEventStore,
  ) {}

  /** Set bridge context after port binding (called from extension.ts). */
  setBridgeContext(ctx: BridgeContext): void {
    this.bridgeCtx = ctx;
  }

  /**
   * Validate a task_id against a conservative allowed-character policy.
   * Returns null if valid, or an error message string if invalid.
   *
   * Policy: [a-zA-Z0-9._-], max 256 chars, no path traversal (..).
   */
  static validateTaskId(taskId: string): string | null {
    if (!taskId || taskId.length === 0) {
      return "task_id must not be empty";
    }
    if (taskId.length > 256) {
      return `task_id too long (${taskId.length} chars, max 256)`;
    }
    if (taskId.includes("..")) {
      return "task_id must not contain path traversal (..)";
    }
    if (!/^[a-zA-Z0-9._-]+$/.test(taskId)) {
      return "task_id contains disallowed characters (allowed: a-z A-Z 0-9 . _ -)";
    }
    return null;
  }

  /**
   * Prompt hygiene gate: reject obviously unsafe / non-text payloads
   * before they reach the executor path.
   *
   * Checks for:
   * - Empty or missing prompt
   * - NUL bytes (\x00) — indicates binary content
   * - Non-printable control characters (excluding tab, newline, carriage return)
   * - Overlong prompts (> 512 KiB — likely binary blob, not a task brief)
   *
   * Returns null if clean, or a structured error object if rejected.
   */
  static sanitizePrompt(prompt: unknown): { ok: false; status: string; message: string } | null {
    if (prompt === undefined || prompt === null) {
      return { ok: false, status: "ERROR", message: "prompt is required" };
    }
    if (typeof prompt !== "string") {
      return { ok: false, status: "ERROR", message: `prompt must be a string, got ${typeof prompt}` };
    }
    if (prompt.length === 0) {
      return { ok: false, status: "ERROR", message: "prompt must not be empty" };
    }

    // NUL byte check — definitive binary indicator
    if (prompt.includes("\x00")) {
      return {
        ok: false,
        status: "ERROR",
        message: "prompt contains NUL bytes (binary content detected). Only text prompts are accepted.",
      };
    }

    // Non-printable control character check (allow \t \n \r)
    // eslint-disable-next-line no-control-regex
    const controlCharRe = /[\x01-\x08\x0B\x0C\x0E-\x1F\x7F]/;
    if (controlCharRe.test(prompt)) {
      const match = prompt.match(controlCharRe);
      const charCode = match ? match[0].charCodeAt(0) : -1;
      return {
        ok: false,
        status: "ERROR",
        message: `prompt contains non-printable control character (0x${charCode.toString(16).padStart(2, "0")}). Only text prompts are accepted.`,
      };
    }

    // Overlong prompt check (512 KiB)
    const MAX_PROMPT_BYTES = 512 * 1024;
    const byteLength = Buffer.byteLength(prompt, "utf-8");
    if (byteLength > MAX_PROMPT_BYTES) {
      return {
        ok: false,
        status: "ERROR",
        message: `prompt too large (${byteLength} bytes, max ${MAX_PROMPT_BYTES}). This looks like binary content, not a task brief.`,
      };
    }

    return null; // Clean
  }

  /**
   * Handle run_task: create a new background session and return ACK.
   *
   * 1. Call createBackgroundSession(prompt)
   * 2. Bind task_id + attempt_no → session_id in local store
   * 3. Return structured ACK
   *
   * If createBackgroundSession fails, return a structured error (no ACK).
   */
  async runTask(req: RunTaskRequest): Promise<RunTaskResponse> {
    // ── Validate task_id at dispatch boundary ──────────────────
    const idError = TaskExecutionService.validateTaskId(req.task_id);
    if (idError) {
      return {
        ok: false,
        status: "ERROR",
        task_id: req.task_id,
        attempt_no: req.attempt_no,
        review_round: req.review_round,
        session_id: "",
        source_event_id: "",
        source_seq: 0,
        emitted_at: new Date().toISOString(),
        message: idError,
      };
    }

    // ── Prompt hygiene gate ──────────────────────────────────
    const promptErr = TaskExecutionService.sanitizePrompt(req.prompt);
    if (promptErr) {
      return {
        ok: false,
        status: promptErr.status,
        task_id: req.task_id,
        attempt_no: req.attempt_no,
        review_round: req.review_round,
        session_id: "",
        source_event_id: "",
        source_seq: 0,
        emitted_at: new Date().toISOString(),
        message: promptErr.message,
      };
    }

    // Ensure receipt directory exists in the repo workspace.
    const repoPath = req.repo_path || "/tmp";
    MonitorController.ensureReceiptDir(repoPath);

    // Delete any stale receipt for same triplet (restart/retry within same round)
    this.cleanStaleReceipt(repoPath, req.task_id, req.attempt_no, req.review_round);

    // Append receipt-write instruction so the monitor can read the output.
    // GetConversation RPC is not implemented in this LS version (404),
    // so the agent writes a JSON receipt file instead.
    const outputFile = MonitorController.outputFilePath(repoPath, req.task_id, req.attempt_no, req.review_round);
    const promptWithSuffix = this.appendReceiptInstruction(req.prompt, req, outputFile);

    const result = await this.sessionCtrl.createBackgroundSession(promptWithSuffix);

    if (!result.ok) {
      return {
        ok: false,
        status: "ERROR",
        task_id: req.task_id,
        attempt_no: req.attempt_no,
        review_round: req.review_round,
        session_id: "",
        source_event_id: "",
        source_seq: 0,
        emitted_at: new Date().toISOString(),
        message: result.error || "Failed to create session",
      };
    }

    const sessionId = result.session_id;
    const now = new Date().toISOString();

    // Bind session
    const session: TaskSession = {
      task_id: req.task_id,
      attempt_no: req.attempt_no,
      review_round: req.review_round,
      repo_path: req.repo_path,
      branch: req.branch ?? null,
      session_id: sessionId,
      last_step_count: 0,
      last_heartbeat_at: now,
      last_monitor_state: null,
      last_known_status: "ACK",
    };
    this.sessionStore.bind(req.task_id, req.attempt_no, session);

    // Emit ACK event
    const ackEvent: PendingEvent = {
      source_event_id: `${sessionId}:evt:1`,
      task_id: req.task_id,
      attempt_no: req.attempt_no,
      review_round: req.review_round,
      session_id: sessionId,
      source_seq: 1,
      status: "ACK",
      payload: {},
      emitted_at: now,
      delivered_at: null,
    };
    this.eventStore.push(req.task_id, ackEvent);

    return {
      ok: true,
      status: "ACK",
      task_id: req.task_id,
      attempt_no: req.attempt_no,
      review_round: req.review_round,
      session_id: sessionId,
      source_event_id: ackEvent.source_event_id,
      source_seq: 1,
      emitted_at: now,
      message: "accepted",
    };
  }

  /**
   * Handle continue_task: send prompt to existing session.
   *
   * 1. Look up bound session for task_id + attempt_no
   * 2. If not found → return DESYNC (never silently create a new session)
   * 3. Focus session + send prompt
   * 4. Return ACK
   */
  async continueTask(req: ContinueTaskRequest): Promise<RunTaskResponse> {
    // ── Validate task_id at dispatch boundary ──────────────────
    const idError = TaskExecutionService.validateTaskId(req.task_id);
    if (idError) {
      return {
        ok: false,
        status: "ERROR",
        task_id: req.task_id,
        attempt_no: req.attempt_no,
        review_round: req.review_round,
        session_id: "",
        source_event_id: "",
        source_seq: 0,
        emitted_at: new Date().toISOString(),
        message: idError,
      };
    }

    // ── Prompt hygiene gate ──────────────────────────────────
    const promptErr = TaskExecutionService.sanitizePrompt(req.prompt);
    if (promptErr) {
      return {
        ok: false,
        status: promptErr.status,
        task_id: req.task_id,
        attempt_no: req.attempt_no,
        review_round: req.review_round,
        session_id: "",
        source_event_id: "",
        source_seq: 0,
        emitted_at: new Date().toISOString(),
        message: promptErr.message,
      };
    }

    const session = this.sessionStore.get(req.task_id, req.attempt_no);

    if (!session) {
      // DESYNC: no session bound. Must not silently create a new session.
      const now = new Date().toISOString();
      return {
        ok: false,
        status: "DESYNC",
        task_id: req.task_id,
        attempt_no: req.attempt_no,
        review_round: req.review_round,
        session_id: "",
        source_event_id: "",
        source_seq: 0,
        emitted_at: now,
        message: "No session bound for this task/attempt. DESYNC.",
      };
    }

    // ── Quarantine guard ──────────────────────────────────────
    const quarantineEntry = TaskExecutionService.checkQuarantine(session.session_id);
    if (quarantineEntry) {
      const now = new Date().toISOString();
      console.warn(
        `[taskExec] QUARANTINED session refused: session=${session.session_id} ` +
        `task=${req.task_id} signature=${quarantineEntry.signature} ` +
        `count=${quarantineEntry.count}`
      );
      return {
        ok: false,
        status: "QUARANTINED",
        task_id: req.task_id,
        attempt_no: req.attempt_no,
        review_round: req.review_round,
        session_id: session.session_id,
        source_event_id: "",
        source_seq: 0,
        emitted_at: now,
        message: `Session ${session.session_id} is quarantined: ${quarantineEntry.signature} ` +
          `(${quarantineEntry.count} occurrences, quarantined at ${quarantineEntry.quarantined_at}). ` +
          `Refusing continue_task to prevent bad-session loop.`,
      };
    }

    // Delete stale receipt: current round (restart/retry) AND prior rounds
    const repoPath = req.repo_path || session.repo_path || "/tmp";
    MonitorController.ensureReceiptDir(repoPath);
    this.cleanStaleReceipt(repoPath, req.task_id, req.attempt_no, req.review_round);

    // Send prompt to existing session with receipt-write instruction
    const outputFile = MonitorController.outputFilePath(repoPath, req.task_id, req.attempt_no, req.review_round);
    const promptWithSuffix = this.appendReceiptInstruction(req.prompt, req, outputFile);
    const result = await this.sessionCtrl.sendPrompt(session.session_id, promptWithSuffix);

    if (!result.ok) {
      // Session exists in our store but SDK can't reach it → DESYNC
      const now = new Date().toISOString();
      const seq = (session.last_step_count || 0) + 1;

      const desyncEvent: PendingEvent = {
        source_event_id: `${session.session_id}:evt:${seq}`,
        task_id: req.task_id,
        attempt_no: req.attempt_no,
        review_round: req.review_round,
        session_id: session.session_id,
        source_seq: seq,
        status: "DESYNC",
        payload: {
          reason: "send_prompt_failed",
          error: result.error,
        },
        emitted_at: new Date().toISOString(),
        delivered_at: null,
      };
      this.eventStore.push(req.task_id, desyncEvent);

      return {
        ok: false,
        status: "DESYNC",
        task_id: req.task_id,
        attempt_no: req.attempt_no,
        review_round: req.review_round,
        session_id: session.session_id,
        source_event_id: desyncEvent.source_event_id,
        source_seq: seq,
        emitted_at: desyncEvent.emitted_at,
        message: `Session exists but prompt failed: ${result.error}`,
      };
    }

    // ACK the continue
    const now = new Date().toISOString();
    const seq = (session.last_step_count || 0) + 1;

    session.review_round = req.review_round;
    session.last_heartbeat_at = now;
    session.last_known_status = "ACK";

    const ackEvent: PendingEvent = {
      source_event_id: `${session.session_id}:evt:${seq}`,
      task_id: req.task_id,
      attempt_no: req.attempt_no,
      review_round: req.review_round,
      session_id: session.session_id,
      source_seq: seq,
      status: "ACK",
      payload: {},
      emitted_at: now,
      delivered_at: null,
    };
    this.eventStore.push(req.task_id, ackEvent);

    return {
      ok: true,
      status: "ACK",
      task_id: req.task_id,
      attempt_no: req.attempt_no,
      review_round: req.review_round,
      session_id: session.session_id,
      source_event_id: ackEvent.source_event_id,
      source_seq: seq,
      emitted_at: now,
      message: "continued",
    };
  }

  /**
   * Append receipt-write instruction to the prompt.
   *
   * Provides two paths for the agent to submit its result:
   *   1. Preferred: POST to the bridge /write_receipt endpoint (no file-write permission needed)
   *   2. Fallback:  Create a JSON file at the given path (original method)
   *
   * The bridge path eliminates the host permission prompt that would otherwise
   * fire each time the agent writes to the workspace .agpair/ directory.
   * Uses the actual bound bridge port and auth token, not hardcoded values.
   */
  private appendReceiptInstruction(
    prompt: string,
    req: RunTaskRequest | ContinueTaskRequest,
    outputFile: string,
  ): string {
    const receipt = JSON.stringify({
      status: "<YOUR_STATUS>",
      task_id: req.task_id,
      attempt_no: req.attempt_no,
      review_round: req.review_round,
      summary: "<YOUR_SUMMARY>",
    });

    let preferred = "";
    if (this.bridgeCtx) {
      const url = `http://127.0.0.1:${this.bridgeCtx.port}/write_receipt`;
      preferred = `PREFERRED: POST JSON to ${url} with body: ${receipt}`;
      if (this.bridgeCtx.authToken) {
        preferred += `\nInclude header: Authorization: Bearer ${this.bridgeCtx.authToken}`;
      }
      preferred += "\n";
    }

    return prompt +
      `\n\nAFTER completing your task, submit your result receipt. ` +
      `Replace <YOUR_STATUS> with one of: EVIDENCE_PACK, BLOCKED, FAILED. ` +
      `Replace <YOUR_SUMMARY> with a brief description.\n` +
      preferred +
      `FALLBACK: If the bridge is unavailable, create the file ${outputFile} ` +
      `containing the same JSON object.\n` +
      `Template: ${receipt}`;
  }

  /**
   * Delete stale receipt files for the same triplet and prior rounds.
   * Prevents false terminal on restart/retry (same round) and
   * cross-round contamination (prior rounds).
   */
  private cleanStaleReceipt(
    repoPath: string,
    taskId: string,
    attemptNo: number,
    currentRound: number,
  ): void {
    try {
      const fs = require("fs");
      // Delete current round AND all prior rounds
      for (let r = 0; r <= currentRound; r++) {
        const staleFile = MonitorController.outputFilePath(repoPath, taskId, attemptNo, r);
        if (fs.existsSync(staleFile)) {
          fs.unlinkSync(staleFile);
          console.log(`[taskExec] Deleted stale receipt: ${staleFile}`);
        }
      }
    } catch (e: any) {
      console.log(`[taskExec] Failed to clean stale receipts: ${e.message}`);
    }
  }

  // ── Quarantine registry check ────────────────────────────────

  /** Quarantine registry path: ~/.agpair/quarantine_registry.json */
  private static readonly QUARANTINE_PATH = (() => {
    const path = require("path");
    const os = require("os");
    return path.join(os.homedir(), ".agpair", "quarantine_registry.json");
  })();

  /**
   * Check if a session_id is in the quarantine registry.
   *
   * Returns the quarantine entry if found, null otherwise.
   * Reads the file synchronously (tiny JSON, local disk, called rarely).
   * If the file doesn't exist or is unreadable, returns null (safe default).
   */
  static checkQuarantine(sessionId: string): {
    session_id: string;
    signature: string;
    count: number;
    quarantined_at: string;
  } | null {
    try {
      const fs = require("fs");
      if (!fs.existsSync(TaskExecutionService.QUARANTINE_PATH)) {
        return null; // No registry → backward-compatible, no quarantine
      }
      const raw = fs.readFileSync(TaskExecutionService.QUARANTINE_PATH, "utf-8");
      const data = JSON.parse(raw);
      const entries: any[] = data.entries || [];
      for (const entry of entries) {
        if (entry.session_id === sessionId) {
          return {
            session_id: entry.session_id,
            signature: entry.signature || "unknown",
            count: entry.count || 0,
            quarantined_at: entry.quarantined_at || "",
          };
        }
      }
    } catch (e: any) {
      // Registry unreadable → safe default: allow
      console.warn(`[taskExec] Failed to read quarantine registry: ${e.message}`);
    }
    return null;
  }
}
