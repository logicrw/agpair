/**
 * Monitor Controller — wraps Antigravity SDK EventMonitor.
 *
 * Translates SDK monitor events into bridge-compatible pending events:
 *   onStepCountChanged     → RUNNING event (monotonic heartbeat)
 *                          → terminal event via structured output parsing
 *   onActiveSessionChanged → DESYNC if session drifts from bound task
 *
 * Terminal output flow:
 *   1. Monitor detects step count increase
 *   2. Reads structured JSON receipt from .agpair/receipts/ in the repo
 *      (file path: {repo_path}/.agpair/receipts/{task_id}_{attempt}_{round}.json)
 *   3. Runs structured output parser on the parsed JSON
 *   4. If parser finds STATUS: EVIDENCE_PACK|BLOCKED → emits terminal event
 *   5. If parser fails → does NOT guess; waits for more output
 *
 * Note: GetConversation RPC returns 404 in Antigravity IDE v25.8.1,
 * so terminal detection uses file-based receipt instead of LS bridge.
 * Receipt is written by the agent via the prompt suffix instruction.
 *
 * Monitor is observer-only for RUNNING. Terminal events are derived from
 * structured output parsing (parser.ts), not from step counts or UI heuristics.
 *
 * Spec: codex_antigravity_companion_extension_ts_spec.md §7.3, §8
 */

import type { AntigravitySDK, IStepCountChange, IActiveSessionChange, IDisposable } from "antigravity-sdk";
import { PendingEventStore, PendingEvent } from "../state/pendingEventStore";
import { TaskSessionStore, TaskSession } from "../state/taskSessionStore";
import { parseStructuredOutput, ParsedOutput } from "../protocols/parser";

/** Tracks per-session state for terminal detection. */
interface SessionMonitorState {
  /** Last step count we checked for terminal output. */
  lastCheckedStep: number;
  /** Whether we've already emitted a terminal event for this session. */
  terminalEmitted: boolean;
}

export class MonitorController {
  private disposables: IDisposable[] = [];
  private _running = false;
  private terminalCheckTimer: ReturnType<typeof setInterval> | null = null;
  /** Track per-session monitor state to avoid redundant checks. */
  private monitorState: Map<string, SessionMonitorState> = new Map();

  /**
   * Canonical receipt file path for a task.
   * Shared by MonitorController (reader) and TaskExecutionService (writer instruction).
   * Uses workspace-relative .agpair/receipts/ directory.
   */
  static outputFilePath(repoPath: string, taskId: string, attemptNo: number, reviewRound: number): string {
    const path = require("path");
    return path.join(repoPath, ".agpair", "receipts", `${taskId}_${attemptNo}_${reviewRound}.json`);
  }

  /**
   * Ensure the receipts directory exists.
   */
  static ensureReceiptDir(repoPath: string): void {
    const path = require("path");
    const fs = require("fs");
    const dir = path.join(repoPath, ".agpair", "receipts");
    fs.mkdirSync(dir, { recursive: true });
  }

  constructor(
    private readonly sdk: AntigravitySDK,
    private readonly eventStore: PendingEventStore,
    private readonly sessionStore: TaskSessionStore,
  ) {}

  /**
   * Start the monitor loop.
   *
   * @param intervalMs - USS polling interval (default 3000ms)
   * @param trajectoryIntervalMs - Trajectory polling interval (default 5000ms)
   */
  start(intervalMs = 3000, trajectoryIntervalMs = 5000): void {
    if (this._running) return;

    // Subscribe to step count changes → emit RUNNING + check terminal
    const stepSub = this.sdk.monitor.onStepCountChanged((change: IStepCountChange) => {
      this.handleStepCountChange(change);
    });
    this.disposables.push(stepSub);

    // Subscribe to active session changes → detect DESYNC
    const sessionSub = this.sdk.monitor.onActiveSessionChanged((change: IActiveSessionChange) => {
      this.handleActiveSessionChange(change);
    });
    this.disposables.push(sessionSub);

    // Periodic re-check for terminal output on RUNNING sessions.
    // This catches the case where step count stopped changing (agent finished)
    // but the LS connection wasn't fixed yet at that time.
    this.terminalCheckTimer = setInterval(() => {
      const sessions = this.sessionStore.getAll();
      for (const session of sessions) {
        if (session.last_known_status === "RUNNING" && session.last_step_count > 0) {
          this.checkTerminalOutput(session, session.last_step_count).catch(() => {});
        }
      }
    }, 5000);

    // Start the SDK monitor
    this.sdk.monitor.start(intervalMs, trajectoryIntervalMs);
    this._running = true;
  }

  /** Stop the monitor loop. */
  stop(): void {
    if (!this._running) return;
    this.sdk.monitor.stop();
    if (this.terminalCheckTimer) {
      clearInterval(this.terminalCheckTimer);
      this.terminalCheckTimer = null;
    }
    for (const d of this.disposables) {
      d.dispose();
    }
    this.disposables = [];
    this._running = false;
    this.monitorState.clear();
  }

  get isRunning(): boolean {
    return this._running;
  }

  /**
   * Handle step count change:
   *   1. Emit RUNNING event (heartbeat)
   *   2. Fetch latest conversation content and check for terminal output
   */
  private handleStepCountChange(change: IStepCountChange): void {
    // Find which task is bound to this session
    let session = this.sessionStore.findBySessionId(change.sessionId);

    // Resilient binding: if the session ID doesn't match any stored session,
    // but we have exactly one active task, auto-rebind.
    // This handles Path 3 (direct vscode commands) where the detected
    // session ID may differ from the real agent session ID.
    if (!session) {
      const allSessions = this.sessionStore.getAll();
      if (allSessions.length === 1) {
        // ── Quarantine guard: refuse rebind onto quarantined session ──
        const { TaskExecutionService } = require("../services/taskExecutionService");
        const quarantineEntry = TaskExecutionService.checkQuarantine(change.sessionId);
        if (quarantineEntry) {
          console.warn(
            `[monitor] QUARANTINE BLOCKED auto-rebind: ` +
            `session=${change.sessionId} signature=${quarantineEntry.signature} ` +
            `count=${quarantineEntry.count}. Refusing rebind.`
          );
          return;
        }

        session = allSessions[0];
        const oldId = session.session_id;
        session.session_id = change.sessionId;
        console.log(`[monitor] Auto-rebound session: ${oldId} -> ${change.sessionId} (task=${session.task_id})`);
      } else {
        return;
      }
    }

    const now = new Date().toISOString();

    // Update session heartbeat
    session.last_step_count = change.newCount;
    session.last_heartbeat_at = now;
    session.last_known_status = "RUNNING";

    // Emit RUNNING event
    const seq = change.newCount + 1; // +1 because seq 1 = ACK
    const runningEvt: PendingEvent = {
      source_event_id: `${session.session_id}:evt:${seq}`,
      task_id: session.task_id,
      attempt_no: session.attempt_no,
      review_round: session.review_round,
      session_id: session.session_id,
      source_seq: seq,
      status: "RUNNING",
      payload: {
        step_count: change.newCount,
        delta: change.delta,
      },
      emitted_at: now,
      delivered_at: null,
    };
    this.eventStore.push(session.task_id, runningEvt);

    // Check for terminal output (async, fire-and-forget)
    this.checkTerminalOutput(session, change.newCount).catch((err) => {
      console.error(`[monitor] Terminal check failed for ${session.task_id}: ${err.message}`);
    });
  }

  /**
   * Check if the agent has produced structured terminal output.
   *
   * Fetches the latest conversation content from the LS bridge and
   * runs the structured output parser. If a terminal status is found,
   * validates that TASK_ID/ATTEMPT_NO match and emits the terminal event.
   *
   * If parsing fails or no structured output is found, does nothing.
   * Monitor never guesses completion — only parses structured headers.
   */
  private async checkTerminalOutput(session: TaskSession, stepCount: number): Promise<void> {
    // Get or create monitor state for this session
    let state = this.monitorState.get(session.session_id);
    if (!state) {
      state = { lastCheckedStep: 0, terminalEmitted: false };
      this.monitorState.set(session.session_id, state);
    }

    // Skip if already emitted terminal or no new steps since last check
    if (state.terminalEmitted) return;
    if (stepCount <= state.lastCheckedStep) return;
    state.lastCheckedStep = stepCount;

    // Strategy: read the structured JSON receipt from the repo workspace.
    // The prompt suffix instructs the agent to write a JSON receipt.
    // This bypasses the LS API entirely (GetConversation returns 404).
    let lastText: string | null = null;

    const repoPath = session.repo_path || "/tmp";
    const outputFile = MonitorController.outputFilePath(
      repoPath, session.task_id, session.attempt_no, session.review_round
    );
    try {
      const fs = require("fs");
      if (fs.existsSync(outputFile)) {
        const raw = fs.readFileSync(outputFile, "utf-8").trim();
        // Try JSON parse first (hardened receipt format)
        try {
          const receipt = JSON.parse(raw);
          // Reconstruct structured text from JSON for the parser
          lastText = [
            `STATUS: ${receipt.status || ""}`,
            `TASK_ID: ${receipt.task_id || ""}`,
            `ATTEMPT_NO: ${receipt.attempt_no ?? ""}`,
            `REVIEW_ROUND: ${receipt.review_round ?? ""}`,
            `SUMMARY: ${receipt.summary || ""}`,
          ].join("\n");
          console.log(
            `[monitor] Read JSON receipt: ${outputFile} → status=${receipt.status}`
          );
        } catch {
          // Fallback: treat as plain text (M52 compat)
          lastText = raw;
          console.log(
            `[monitor] Read plain receipt: ${outputFile} (${raw.length} chars): ` +
            `${raw.substring(0, 200)}`
          );
        }
      } else {
        console.log(`[monitor] Receipt file not found yet: ${outputFile}`);
      }
    } catch (e: any) {
      console.log(`[monitor] Failed to read receipt file: ${e.message}`);
    }

    if (!lastText) return;

    // Run structured output parser
    const parsed = parseStructuredOutput(lastText);
    if (!parsed) return; // No structured output yet

    // Validate the parsed output matches our bound task
    if (parsed.task_id !== session.task_id) {
      console.warn(
        `[monitor] Structured output TASK_ID mismatch: ` +
        `expected=${session.task_id}, got=${parsed.task_id}. Ignoring.`
      );
      return;
    }

    if (parsed.attempt_no !== session.attempt_no) {
      console.warn(
        `[monitor] Structured output ATTEMPT_NO mismatch: ` +
        `expected=${session.attempt_no}, got=${parsed.attempt_no}. Ignoring.`
      );
      return;
    }

    if (parsed.review_round !== session.review_round) {
      console.warn(
        `[monitor] Structured output REVIEW_ROUND mismatch: ` +
        `expected=${session.review_round}, got=${parsed.review_round}. Ignoring.`
      );
      return;
    }

    // Emit terminal event
    this.emitTerminalEvent(session, parsed, stepCount);
    state.terminalEmitted = true;
  }

  /**
   * Emit a terminal pending event (EVIDENCE_PACK, BLOCKED, or FAILED).
   */
  private emitTerminalEvent(
    session: TaskSession,
    parsed: ParsedOutput,
    stepCount: number,
  ): void {
    const now = new Date().toISOString();
    // Use step_count + 1000 as terminal seq to ensure it's after all RUNNING events
    const seq = stepCount + 1000;

    session.last_known_status = parsed.status;

    const evt: PendingEvent = {
      source_event_id: `${session.session_id}:evt:${seq}`,
      task_id: session.task_id,
      attempt_no: session.attempt_no,
      review_round: session.review_round,
      session_id: session.session_id,
      source_seq: seq,
      status: parsed.status,
      payload: {
        summary: parsed.summary,
        raw_text: parsed.raw_text,
        step_count: stepCount,
      },
      emitted_at: now,
      delivered_at: null,
    };
    this.eventStore.push(session.task_id, evt);

    console.log(
      `[monitor] Terminal event emitted: task=${session.task_id} ` +
      `status=${parsed.status} seq=${seq}`
    );
  }

  /**
   * Handle active session change: detect DESYNC if the IDE switched to
   * a session that doesn't match any bound task.
   */
  private handleActiveSessionChange(change: IActiveSessionChange): void {
    const binding = this.sessionStore.findBySessionId(change.previousSessionId);
    if (!binding) return;

    const currentBinding = this.sessionStore.findBySessionId(change.sessionId);
    if (currentBinding) return;

    const session = binding;
    const now = new Date().toISOString();
    const seq = (session.last_step_count || 0) + 100;

    session.last_known_status = "DESYNC";
    session.last_monitor_state = "active_session_drift";

    const evt: PendingEvent = {
      source_event_id: `${session.session_id}:evt:${seq}`,
      task_id: session.task_id,
      attempt_no: session.attempt_no,
      review_round: session.review_round,
      session_id: session.session_id,
      source_seq: seq,
      status: "DESYNC",
      payload: {
        reason: "active_session_drift",
        expected_session: session.session_id,
        actual_session: change.sessionId,
      },
      emitted_at: now,
      delivered_at: null,
    };
    this.eventStore.push(session.task_id, evt);
  }

  // NOTE: ConnectRPC-based findConversation and extractLastAssistantText
  // have been removed. GetConversation RPC returns 404 in Antigravity IDE v25.8.1.
  // Terminal detection now uses the file-based receipt path exclusively.
  // See: taskExecutionService.ts appendReceiptInstruction() for the prompt suffix,
  // and checkTerminalOutput() above for the file reader.
  //
  // When the LS implements GetConversation, restore LS-bridge based terminal
  // detection as a preferred path, keeping file-based receipt as fallback.

  dispose(): void {
    this.stop();
  }
}
