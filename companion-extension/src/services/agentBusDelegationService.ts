import * as childProcess from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

import type { SessionController } from "../sdk/sessionController";
import type { AgentBusMessage } from "./agentBusWatchService";
import type { DelegationTaskTracker } from "../state/delegationTaskTracker";
import { DelegationReceiptWatcher } from "./delegationReceiptWatcher";
import {
  DelegationHeartbeatService,
  DEFAULT_HEARTBEAT_INTERVAL_MS,
} from "./delegationHeartbeatService";

export interface AgentBusDelegationReply {
  taskId: string;
  status:
    | "ACK"
    | "RUNNING"
    | "EVIDENCE_PACK"
    | "BLOCKED"
    | "COMMITTED"
    | "REVIEW_ACK"
    | "REVIEW_NACK"
    | "APPROVE_ACK"
    | "APPROVE_NACK";
  body: string;
}

export interface AgentBusDelegationServiceOptions {
  enabled: boolean;
  command: string;
  workspacePathsProvider: () => string[];
  outputChannel: { appendLine(message: string): void };
  sessionCtrl: SessionController;
  tracker: DelegationTaskTracker;
  receiptDir?: string;
  receiptPollIntervalMs?: number;
  /** Heartbeat cadence in ms. Default: 30 000 (30 s). */
  heartbeatIntervalMs?: number;
  staleAfterMs?: number;
  spawnFn?: typeof childProcess.spawn;
  sendReply?: (reply: AgentBusDelegationReply) => Promise<void>;
}

export class AgentBusDelegationService {
  private readonly enabled: boolean;
  private readonly command: string;
  private readonly workspacePathsProvider: () => string[];
  private readonly outputChannel: { appendLine(message: string): void };
  private readonly sessionCtrl: SessionController;
  private readonly spawnFn: typeof childProcess.spawn;
  private readonly sendReplyFn: (
    reply: AgentBusDelegationReply,
  ) => Promise<void>;
  private readonly processedMessageIds = new Set<number>();
  private readonly tracker: DelegationTaskTracker;
  private readonly receiptWatcher: DelegationReceiptWatcher;
  private readonly heartbeatService: DelegationHeartbeatService;
  private readonly receiptDir: string;

  constructor(options: AgentBusDelegationServiceOptions) {
    this.enabled = options.enabled;
    this.command = resolveAgentBusCommand(options.command);
    this.workspacePathsProvider = options.workspacePathsProvider;
    this.outputChannel = options.outputChannel;
    this.sessionCtrl = options.sessionCtrl;
    this.spawnFn = options.spawnFn ?? childProcess.spawn;
    this.sendReplyFn = options.sendReply ?? ((reply) => this.sendReply(reply));
    this.tracker = options.tracker;
    this.receiptDir =
      options.receiptDir ?? DelegationReceiptWatcher.defaultReceiptDir();

    this.receiptWatcher = new DelegationReceiptWatcher({
      tracker: this.tracker,
      receiptDir: this.receiptDir,
      pollIntervalMs: options.receiptPollIntervalMs ?? 3000,
      staleAfterMs: options.staleAfterMs,
      outputChannel: this.outputChannel,
      sendTerminal: (taskId, status, body) =>
        this.sendReplyFn({
          taskId,
          status: status as "EVIDENCE_PACK" | "BLOCKED" | "COMMITTED",
          body,
        }),
      sessionCtrl: this.sessionCtrl,
    });

    this.heartbeatService = new DelegationHeartbeatService({
      tracker: this.tracker,
      intervalMs: options.heartbeatIntervalMs ?? DEFAULT_HEARTBEAT_INTERVAL_MS,
      outputChannel: this.outputChannel,
      sendRunning: (reply) => this.sendReplyFn(reply),
    });

    if (this.enabled) {
      this.receiptWatcher.start();
      this.heartbeatService.start();
    }
  }

  async handleMessages(messages: AgentBusMessage[]): Promise<void> {
    if (!this.enabled) {
      return;
    }
    for (const message of messages) {
      await this.handleMessage(message);
    }
  }

  /** Continuation statuses that should be routed into an existing session. */
  private static readonly CONTINUATION_STATUSES = new Set([
    "REVIEW",
    "REVIEW_DELTA",
  ]);

  private async handleMessage(message: AgentBusMessage): Promise<void> {
    if (typeof message.id === "number") {
      if (this.processedMessageIds.has(message.id)) {
        return;
      }
      this.processedMessageIds.add(message.id);
    }

    const status = typeof message.status === "string" ? message.status : "";
    const taskId =
      typeof message.task_id === "string" && message.task_id.length > 0
        ? message.task_id
        : "";

    if (!taskId) {
      return; // no task_id — nothing to act on
    }

    if (AgentBusDelegationService.CONTINUATION_STATUSES.has(status)) {
      await this.handleContinuation(message, taskId, status);
      return;
    }

    if (status === "APPROVED") {
      await this.handleApproved(message, taskId);
      return;
    }

    if (status !== "TASK") {
      return; // unrecognised status — ignore
    }

    // ── New TASK: create a fresh session ──────────────────────
    const repoPath = this.workspacePathsProvider()[0];
    if (!repoPath) {
      await this.sendReplyFn({
        taskId,
        status: "BLOCKED",
        body: "No Antigravity workspace is open. Open the target repo window and resend the task.",
      });
      return;
    }

    const sameTask = this.tracker.get(taskId);
    if (sameTask && !sameTask.terminalSentAt) {
      this.outputChannel.appendLine(
        `[companion] retry/preempt same TASK ${taskId}: terminating old session ${sameTask.sessionId} before creating a fresh retry session...`,
      );
      await this.sessionCtrl.terminateSession(sameTask.sessionId);
      this.tracker.abandon(
        taskId,
        `Superseded by fresh retry for task ${taskId}`,
      );
    }

    const existingTask = this.tracker.getPendingForRepo(repoPath, taskId);
    if (existingTask) {
      this.outputChannel.appendLine(
        `[companion] preempting old TASK ${existingTask.taskId}: workspace ${repoPath} received new TASK ${taskId}. Terminating old session ${existingTask.sessionId}...`,
      );
      // Terminate the old session explicitly
      await this.sessionCtrl.terminateSession(existingTask.sessionId);
      // Remove the old task from the tracker queue
      this.tracker.abandon(
        existingTask.taskId,
        `Preempted by new task ${taskId}`,
      );
    }

    const receiptPath = DelegationReceiptWatcher.receiptPath(
      this.receiptDir,
      taskId,
    );

    const prompt = buildDelegationPrompt({
      taskId,
      repoPath,
      body: typeof message.body === "string" ? message.body : "",
      receiptPath,
    });
    const result = await this.sessionCtrl.createBackgroundSession(prompt, {
      allowInteractiveFallback: true,
      contextLabel: `delegated task ${taskId}`,
    });
    if (!result.ok || !result.session_id) {
      await this.sendReplyFn({
        taskId,
        status: "BLOCKED",
        body: `Failed to start Antigravity executor session: ${result.error ?? "unknown error"}`,
      });
      return;
    }

    if (result.session_id.startsWith("ag-cmd-")) {
      this.outputChannel.appendLine(
        `[companion] delegated TASK ${taskId} resulted in phantom session ${result.session_id}. Rejecting with BLOCKED.`,
      );
      await this.sessionCtrl.terminateSession(result.session_id);
      await this.sendReplyFn({
        taskId,
        status: "BLOCKED",
        body: `Failed to establish a trustworthy session (phantom ID). The UI may not be able to receive prompts reliably.`,
      });
      return;
    }

    this.outputChannel.appendLine(
      `[companion] delegated TASK ${taskId} to Antigravity session ${result.session_id}`,
    );
    // Register in tracker for receipt-based terminal auto-return before ACK.
    const registered = this.tracker.register({
      taskId,
      sessionId: result.session_id,
      repoPath,
      receiptPath,
      taskBody: prompt,
      status: "ACKED",
      ackedAt: new Date().toISOString(),
      lastActivityAt: new Date().toISOString(),
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingTerminalStatus: null,
      pendingTerminalBody: null,
      pendingTerminalPreparedAt: null,
    });
    if (!registered) {
      this.outputChannel.appendLine(
        `[companion] failed to register delegated TASK ${taskId} for session ${result.session_id}; terminating session to avoid orphaned execution.`,
      );
      await this.sessionCtrl.terminateSession(result.session_id);
      await this.sendReplyFn({
        taskId,
        status: "BLOCKED",
        body: `Failed to track delegated task ${taskId} locally after creating session ${result.session_id}. The session was terminated to avoid orphaned execution.`,
      });
      return;
    }

    await this.sendReplyFn({
      taskId,
      status: "ACK",
      body: `Accepted by Antigravity auto-handoff. session_id=${result.session_id} repo_path=${repoPath}`,
    });

    this.outputChannel.appendLine(
      `[companion] delegation auto-return registered for ${taskId} (receipt=${receiptPath})`,
    );
  }

  /**
   * Handle REVIEW / REVIEW_DELTA continuation by sending the review feedback
   * into the tracked session. Does NOT create a new session.
   */
  private async handleContinuation(
    message: AgentBusMessage,
    taskId: string,
    status: string,
  ): Promise<void> {
    const tracked = this.tracker.get(taskId);
    const replyBody = (detail: string) =>
      buildContinuationReplyBody(detail, message.id);
    if (!tracked) {
      this.outputChannel.appendLine(
        `[companion] ${status} for unknown task ${taskId} — sending REVIEW_NACK`,
      );
      await this.sendReplyFn({
        taskId,
        status: "REVIEW_NACK",
        body: replyBody(
          `Cannot continue: task ${taskId} is not tracked by this extension instance.`,
        ),
      });
      return;
    }

    if (!tracked.sessionId) {
      this.outputChannel.appendLine(
        `[companion] ${status} for ${taskId} has no sessionId — sending REVIEW_NACK`,
      );
      await this.sendReplyFn({
        taskId,
        status: "REVIEW_NACK",
        body: replyBody(
          `Cannot continue: tracked task ${taskId} has no associated session.`,
        ),
      });
      return;
    }

    if (tracked.sessionId.startsWith("ag-cmd-")) {
      this.outputChannel.appendLine(
        `[companion] ${status} for synthetic task ${taskId} (session ${tracked.sessionId}) — sending REVIEW_NACK`,
      );
      await this.sendReplyFn({
        taskId,
        status: "REVIEW_NACK",
        body: replyBody(
          `Cannot continue synthetic session ${tracked.sessionId}. Please use --fresh-resume instead.`,
        ),
      });
      return;
    }

    const prompt = buildReviewContinuationPrompt({
      taskId,
      status,
      body: typeof message.body === "string" ? message.body : "",
      receiptPath: tracked.receiptPath,
    });

    try {
      const result = await this.sessionCtrl.sendPrompt(
        tracked.sessionId,
        prompt,
        {
          allowPanelFallback: false,
          contextLabel: `delegated task ${taskId} (${status})`,
        },
      );
      if (!result.ok) {
        throw new Error(result.error ?? "sendPrompt returned ok=false");
      }
      this.tracker.reopen(taskId, "RUNNING");
      this.outputChannel.appendLine(
        `[companion] ${status} for ${taskId} sent into session ${tracked.sessionId}`,
      );
      await this.sendReplyFn({
        taskId,
        status: "REVIEW_ACK",
        body: replyBody(
          `Successfully sent ${status} prompt into session ${tracked.sessionId}`,
        ),
      });
    } catch (err: any) {
      this.outputChannel.appendLine(
        `[companion] ${status} continuation failed for ${taskId}: ${err.message}`,
      );
      await this.sendReplyFn({
        taskId,
        status: "REVIEW_NACK",
        body: replyBody(
          `Failed to send ${status} into session ${tracked.sessionId}: ${err.message}`,
        ),
      });
    }
  }

  /**
   * Handle APPROVED by sending a commit-phase prompt into the tracked session.
   * Does NOT create a new session — reuses the existing one.
   */
  private async handleApproved(
    message: AgentBusMessage,
    taskId: string,
  ): Promise<void> {
    const tracked = this.tracker.get(taskId);
    const replyBody = (detail: string) =>
      buildContinuationReplyBody(detail, message.id);
    if (!tracked) {
      this.outputChannel.appendLine(
        `[companion] APPROVED for unknown task ${taskId} — sending APPROVE_NACK`,
      );
      await this.sendReplyFn({
        taskId,
        status: "APPROVE_NACK",
        body: replyBody(
          `Cannot commit: task ${taskId} is not tracked by this extension instance.`,
        ),
      });
      return;
    }

    if (!tracked.sessionId) {
      this.outputChannel.appendLine(
        `[companion] APPROVED for ${taskId} has no sessionId — sending APPROVE_NACK`,
      );
      await this.sendReplyFn({
        taskId,
        status: "APPROVE_NACK",
        body: replyBody(
          `Cannot commit: tracked task ${taskId} has no associated session.`,
        ),
      });
      return;
    }

    if (tracked.sessionId.startsWith("ag-cmd-")) {
      this.outputChannel.appendLine(
        `[companion] APPROVED for synthetic task ${taskId} (session ${tracked.sessionId}) — sending APPROVE_NACK`,
      );
      await this.sendReplyFn({
        taskId,
        status: "APPROVE_NACK",
        body: replyBody(
          `Cannot commit synthetic session ${tracked.sessionId}. Please use --fresh-resume instead.`,
        ),
      });
      return;
    }

    const prompt = buildApprovedCommitPrompt({
      taskId,
      body: typeof message.body === "string" ? message.body : "",
      receiptPath: tracked.receiptPath,
    });

    try {
      const result = await this.sessionCtrl.sendPrompt(
        tracked.sessionId,
        prompt,
        {
          allowPanelFallback: false,
          contextLabel: `delegated task ${taskId} (APPROVED)`,
        },
      );
      if (!result.ok) {
        throw new Error(result.error ?? "sendPrompt returned ok=false");
      }
      this.tracker.reopen(taskId, "RUNNING");
      this.outputChannel.appendLine(
        `[companion] APPROVED for ${taskId} sent into session ${tracked.sessionId} (commit phase)`,
      );
      await this.sendReplyFn({
        taskId,
        status: "APPROVE_ACK",
        body: replyBody(
          `Successfully sent APPROVED prompt into session ${tracked.sessionId}`,
        ),
      });
    } catch (err: any) {
      this.outputChannel.appendLine(
        `[companion] APPROVED commit continuation failed for ${taskId}: ${err.message}`,
      );
      await this.sendReplyFn({
        taskId,
        status: "APPROVE_NACK",
        body: replyBody(
          `Failed to send APPROVED into session ${tracked.sessionId}: ${err.message}`,
        ),
      });
    }
  }

  /**
   * Get the delegation status summary for health/debug output.
   */
  getDelegationStatus(): {
    enabled: boolean;
    receipt_watcher_running: boolean;
    heartbeat_running: boolean;
    heartbeat_interval_ms: number;
    receipt_dir: string;
    tracker_summary: ReturnType<DelegationTaskTracker["getSummary"]>;
  } {
    return {
      enabled: this.enabled,
      receipt_watcher_running: this.receiptWatcher.isRunning,
      heartbeat_running: this.heartbeatService.isRunning,
      heartbeat_interval_ms: this.heartbeatService.heartbeatIntervalMs,
      receipt_dir: this.receiptDir,
      tracker_summary: this.tracker.getSummary(),
    };
  }

  /**
   * Dispose the service and its receipt watcher.
   */
  dispose(): void {
    this.heartbeatService.dispose();
    this.receiptWatcher.dispose();
  }

  private sendReply(reply: AgentBusDelegationReply): Promise<void> {
    return new Promise((resolve, reject) => {
      const child = this.spawnFn(
        this.command,
        [
          "send",
          "--sender",
          "code",
          "--task-id",
          reply.taskId,
          "--status",
          reply.status,
          "--body",
          reply.body,
        ],
        {
          cwd: this.workspacePathsProvider()[0] || os.homedir(),
          env: process.env,
          stdio: ["ignore", "pipe", "pipe"],
        },
      );

      let stderr = "";
      child.stderr.setEncoding("utf8");
      child.stderr.on("data", (chunk: string) => {
        stderr += chunk;
      });
      child.on("error", reject);
      child.on("exit", (code) => {
        if (code === 0) {
          resolve();
          return;
        }
        reject(
          new Error(stderr.trim() || `agent-bus send exited ${code ?? 1}`),
        );
      });
    });
  }
}

function buildContinuationReplyBody(
  detail: string,
  messageId: number | undefined,
): string {
  if (
    typeof messageId !== "number" ||
    !Number.isInteger(messageId) ||
    messageId <= 0
  ) {
    return detail;
  }
  return `reply_to_message_id=${messageId}\n${detail}`;
}

function resolveAgentBusCommand(requestedCommand: string): string {
  const trimmed = requestedCommand.trim() || "agent-bus";
  if (path.isAbsolute(trimmed) && fs.existsSync(trimmed)) {
    return trimmed;
  }
  if (trimmed === "agent-bus") {
    const homeCommand = path.join(os.homedir(), ".local", "bin", "agent-bus");
    if (fs.existsSync(homeCommand)) {
      return homeCommand;
    }
  }
  return trimmed;
}

function buildDelegationPrompt(input: {
  taskId: string;
  repoPath: string;
  body: string;
  receiptPath: string;
}): string {
  const brief = input.body.trim();
  return [
    "You are the Antigravity executor for a Codex/Claude Code-reviewed implementation task.",
    `Operate only inside this workspace: ${input.repoPath}`,
    "",
    "Execution contract:",
    brief,
    "",
    "Response contract:",
    "- ACK has already been sent by the extension handoff layer; do not send another ACK unless asked.",
    "- Work the task directly in the current repo.",
    "- If you get blocked, send BLOCKED via agent-bus and stop.",
    "- When complete, send a real EVIDENCE_PACK via agent-bus with diff stat, key files, validation, and residual risks.",
    "",
    "IMPORTANT — Terminal receipt (auto-return):",
    "When you finish (whether success or blocked), write a structured v1 JSON receipt so the",
    "extension can automatically return your result to Codex/Claude Code WITHOUT you needing to",
    "manually invoke agent-bus send. Write this file BEFORE or INSTEAD OF running agent-bus send:",
    "",
    `  Receipt path: ${input.receiptPath}`,
    "",
    "  Receipt format (JSON):",
    "  {",
    '    "schema_version": "1",',
    `    "task_id": "${input.taskId}",`,
    '    "attempt_no": 1,',
    '    "review_round": 0,',
    '    "status": "EVIDENCE_PACK",   // or "BLOCKED"',
    '    "summary": "Brief result summary",',
    '    "payload": {',
    '      "diff_stat": "...",',
    '      "changed_files": ["path/to/file"],',
    '      "validation": ["npm test"],',
    '      "residual_risks": ["none"]',
    "    }",
    "  }",
    "",
    "  BLOCKED payload example:",
    "  {",
    '    "schema_version": "1",',
    `    "task_id": "${input.taskId}",`,
    '    "attempt_no": 1,',
    '    "review_round": 0,',
    '    "status": "BLOCKED",',
    '    "summary": "Why you are blocked",',
    '    "payload": {',
    '      "blocker_type": "auth|env|dependency|review",',
    '      "message": "Concrete blocker",',
    '      "recoverable": true,',
    '      "suggested_action": "What the controller should do next",',
    '      "last_error_excerpt": "Optional short error excerpt"',
    "    }",
    "  }",
    "",
    "The extension will detect this file and send the terminal status to Codex/Claude Code automatically.",
    "You may ALSO run agent-bus send manually as a fallback — duplicate sends are prevented.",
    "",
    "Command examples (fallback only — prefer writing the receipt file above):",
    `agent-bus send --sender code --task-id ${input.taskId} --status ACK --body "Accepted by Antigravity executor"`,
    `agent-bus send --sender code --task-id ${input.taskId} --status EVIDENCE_PACK --body-file /tmp/${input.taskId}.evidence.txt`,
    `agent-bus send --sender code --task-id ${input.taskId} --status BLOCKED --body "blocked reason"`,
  ].join("\n");
}

function buildReviewContinuationPrompt(input: {
  taskId: string;
  status: string;
  body: string;
  receiptPath: string;
}): string {
  const brief = input.body.trim();
  return [
    `--- Codex/Claude Code review continuation (${input.status}) for ${input.taskId} ---`,
    "",
    "The Codex/Claude Code reviewer has sent feedback on your current work.",
    "Address the review comments below and continue working on the task.",
    "Do NOT start from scratch — continue from where you left off.",
    "",
    "Terminal receipt reminder:",
    `- When you finish this continuation round, write the terminal receipt again to: ${input.receiptPath}`,
    `- The receipt must use schema_version=\"1\", task_id=${input.taskId}, numeric attempt_no/review_round, and status=EVIDENCE_PACK or BLOCKED.`,
    '- For EVIDENCE_PACK, include payload keys: "diff_stat", "changed_files", "validation", "residual_risks".',
    '- For BLOCKED, include payload keys: "blocker_type", "message", "recoverable", "suggested_action", "last_error_excerpt".',
    "",
    "Review feedback:",
    brief || "(no additional details)",
  ].join("\n");
}

function buildApprovedCommitPrompt(input: {
  taskId: string;
  body: string;
  receiptPath: string;
}): string {
  const brief = input.body.trim();
  return [
    `--- Codex/Claude Code APPROVED: commit phase for ${input.taskId} ---`,
    "",
    "The Codex/Claude Code reviewer has APPROVED your work. You are now in the commit phase.",
    "Do NOT start from scratch — continue from where you left off.",
    "",
    "Your task now:",
    "1. Run final validation (tests, lint, typecheck) if not yet done.",
    "2. Stage and commit the changes with a clear, conventional commit message.",
    "3. Push the commit if a remote is configured.",
    "4. Write the terminal receipt to confirm completion.",
    "",
    "Approval details:",
    brief || "(no additional details provided)",
    "",
    "Terminal receipt (REQUIRED):",
    `Write this JSON file when done: ${input.receiptPath}`,
    "",
    "  {",
    '    "schema_version": "1",',
    `    "task_id": "${input.taskId}",`,
    '    "attempt_no": 1,',
    '    "review_round": 0,',
    '    "status": "COMMITTED",   // or "BLOCKED" if commit fails',
    '    "summary": "Committed cleanly",',
    '    "payload": {',
    '      "commit_sha": "...",',
    '      "branch": "...",',
    '      "diff_stat": "...",',
    '      "changed_files": ["path/to/file"],',
    '      "validation": ["npm test"],',
    '      "residual_risks": ["none"]',
    "    }",
    "  }",
    "",
    "Use status=COMMITTED if the commit succeeded, or status=BLOCKED if something prevents committing.",
  ].join("\n");
}
