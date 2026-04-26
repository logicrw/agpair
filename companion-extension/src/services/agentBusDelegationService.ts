import * as childProcess from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

import type { SessionController } from "../sdk/sessionController";
import type { AgentBusMessage } from "./agentBusWatchService";
import type { DelegationTaskTracker } from "../state/delegationTaskTracker";
import { DelegationReceiptWatcher } from "./delegationReceiptWatcher";
import { wrapBodyWithDeliveryId } from "./delegationReceiptWatcher";
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
    | "COMMITTED";
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
  sessionOperationTimeoutMs?: number;
  spawnFn?: typeof childProcess.spawn;
  sendReply?: (reply: AgentBusDelegationReply) => Promise<void>;
}

export class AgentBusDelegationService {
  private static readonly DEFAULT_SESSION_OPERATION_TIMEOUT_MS = 15_000;
  private readonly enabled: boolean;
  private readonly command: string;
  private readonly workspacePathsProvider: () => string[];
  private readonly outputChannel: { appendLine(message: string): void };
  private readonly sessionCtrl: SessionController;
  private readonly spawnFn: typeof childProcess.spawn;
  private readonly sendReplyFn: (
    reply: AgentBusDelegationReply,
  ) => Promise<void>;
  private readonly tracker: DelegationTaskTracker;
  private readonly receiptWatcher: DelegationReceiptWatcher;
  private readonly heartbeatService: DelegationHeartbeatService;
  private readonly receiptDir: string;
  private readonly sessionOperationTimeoutMs: number;
  private readonly ackReplayIntervalMs: number;
  private ackReplayTimer: ReturnType<typeof setInterval> | null = null;
  private readonly inFlightAckTaskIds = new Set<string>();

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
    this.sessionOperationTimeoutMs = Math.max(
      options.sessionOperationTimeoutMs ??
        AgentBusDelegationService.DEFAULT_SESSION_OPERATION_TIMEOUT_MS,
      1_000,
    );
    this.ackReplayIntervalMs = Math.max(options.receiptPollIntervalMs ?? 3000, 500);

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
      this.startAckReplayLoop();
      if (this.tracker.getPendingAckDeliveries().length > 0) {
        const startupReplay = setTimeout(() => {
          this.retryPendingAckDeliveries().catch((err) => {
            this.outputChannel.appendLine(
              `[companion] delegation-ack-replay startup error: ${err instanceof Error ? err.message : String(err)}`,
            );
          });
        }, 0);
        startupReplay.unref?.();
      }
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

  private async handleMessage(message: AgentBusMessage): Promise<void> {
    const status = typeof message.status === "string" ? message.status : "";
    const taskId =
      typeof message.task_id === "string" && message.task_id.length > 0
        ? message.task_id
        : "";
    const sourceMessageId =
      typeof message.id === "number" && Number.isFinite(message.id)
        ? message.id
        : null;

    if (!taskId) {
      return; // no task_id — nothing to act on
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
      const isRedelivery =
        sourceMessageId != null &&
        sameTask.sourceMessageId != null &&
        sameTask.sourceMessageId === sourceMessageId;
      if (isRedelivery) {
        if (sameTask.ackSentAt) {
          this.outputChannel.appendLine(
            `[companion] redelivered TASK ${taskId} for source message ${sourceMessageId}; ACK already durable, reusing existing session ${sameTask.sessionId}.`,
          );
          return;
        }
        this.outputChannel.appendLine(
          `[companion] redelivered TASK ${taskId} for source message ${sourceMessageId}; replaying pending ACK for existing session ${sameTask.sessionId}.`,
        );
        await this.sendAckDurably(
          taskId,
          sameTask.pendingAckBody ??
            `Accepted by Antigravity auto-handoff. session_id=${sameTask.sessionId} repo_path=${sameTask.repoPath}`,
        );
        return;
      }
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
    let result;
    try {
      result = await this.withSessionTimeout(
        this.sessionCtrl.createBackgroundSession(prompt, {
          allowInteractiveFallback: true,
          contextLabel: `delegated task ${taskId}`,
        }),
        `createBackgroundSession for ${taskId}`,
      );
    } catch (err: any) {
      await this.sendReplyFn({
        taskId,
        status: "BLOCKED",
        body: `Failed to start Antigravity executor session: ${err?.message ?? String(err)}`,
      });
      return;
    }
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
    // Register in tracker before ACK so crash recovery can replay the ACK durably.
    const registered = this.tracker.register({
      taskId,
      sourceMessageId,
      sessionId: result.session_id,
      repoPath,
      receiptPath,
      taskBody: prompt,
      status: "ACKED",
      ackedAt: new Date().toISOString(),
      ackSentAt: null,
      lastActivityAt: new Date().toISOString(),
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
      pendingAckBody: null,
      pendingAckPreparedAt: null,
      pendingAckInflightAt: null,
      pendingAckDeliveryId: null,
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

    const ackBody = `Accepted by Antigravity auto-handoff. session_id=${result.session_id} repo_path=${repoPath}`;
    const ackDelivered = await this.sendAckDurably(taskId, ackBody);
    if (!ackDelivered) {
      this.outputChannel.appendLine(
        `[companion] ACK delivery deferred for ${taskId}; pending durable replay.`,
      );
    }

    this.outputChannel.appendLine(
      `[companion] delegation auto-return registered for ${taskId} (receipt=${receiptPath})`,
    );
  }


  /**
   * Get the delegation status summary for health/debug output.
   */
  getDelegationStatus(): {
    enabled: boolean;
    receipt_watcher_running: boolean;
    heartbeat_running: boolean;
    heartbeat_interval_ms: number;
    ack_replay_running: boolean;
    ack_replay_interval_ms: number;
    receipt_dir: string;
    tracker_summary: ReturnType<DelegationTaskTracker["getSummary"]>;
  } {
    return {
      enabled: this.enabled,
      receipt_watcher_running: this.receiptWatcher.isRunning,
      heartbeat_running: this.heartbeatService.isRunning,
      heartbeat_interval_ms: this.heartbeatService.heartbeatIntervalMs,
      ack_replay_running: this.ackReplayTimer !== null,
      ack_replay_interval_ms: this.ackReplayIntervalMs,
      receipt_dir: this.receiptDir,
      tracker_summary: this.tracker.getSummary(),
    };
  }

  private withSessionTimeout<T>(
    promise: Promise<T>,
    label: string,
  ): Promise<T> {
    let timer: NodeJS.Timeout | null = null;
    const timeoutPromise = new Promise<T>((_, reject) => {
      timer = setTimeout(() => {
        reject(
          new Error(
            `${label} timed out after ${this.sessionOperationTimeoutMs}ms`,
          ),
        );
      }, this.sessionOperationTimeoutMs);
    });
    return Promise.race([promise, timeoutPromise]).finally(() => {
      if (timer) {
        clearTimeout(timer);
      }
    }) as Promise<T>;
  }

  /**
   * Dispose the service and its receipt watcher.
   */
  dispose(): void {
    this.stopAckReplayLoop();
    this.heartbeatService.dispose();
    this.receiptWatcher.dispose();
  }

  private startAckReplayLoop(): void {
    if (this.ackReplayTimer) return;
    this.ackReplayTimer = setInterval(() => {
      this.retryPendingAckDeliveries().catch((err) => {
        this.outputChannel.appendLine(
          `[companion] delegation-ack-replay error: ${err instanceof Error ? err.message : String(err)}`,
        );
      });
    }, this.ackReplayIntervalMs);
    this.ackReplayTimer.unref?.();
    this.outputChannel.appendLine(
      `[companion] delegation-ack-replay started (interval=${this.ackReplayIntervalMs}ms).`,
    );
  }

  private stopAckReplayLoop(): void {
    if (this.ackReplayTimer) {
      clearInterval(this.ackReplayTimer);
      this.ackReplayTimer = null;
    }
  }

  private async sendAckDurably(taskId: string, body: string): Promise<boolean> {
    const task = this.tracker.get(taskId);
    if (!task) {
      return false;
    }
    if (task.ackSentAt) {
      return true;
    }
    if (!task.pendingAckBody) {
      const prepared = this.tracker.preparePendingAck(taskId, body);
      if (!prepared) {
        return this.tracker.get(taskId)?.ackSentAt != null;
      }
    }
    const pending = this.tracker.get(taskId);
    if (!pending?.pendingAckBody) {
      return pending?.ackSentAt != null;
    }
    this.tracker.markPendingAckInflight(taskId);
    this.inFlightAckTaskIds.add(taskId);
    try {
      const wireBody = pending.pendingAckDeliveryId
        ? wrapBodyWithDeliveryId(pending.pendingAckBody, pending.pendingAckDeliveryId)
        : pending.pendingAckBody;
      await this.sendReplyFn({
        taskId,
        status: "ACK",
        body: wireBody,
      });
      this.tracker.markPendingAckDelivered(taskId);
      return true;
    } catch (err: any) {
      this.tracker.clearPendingAckInflight(taskId);
      this.outputChannel.appendLine(
        `[companion] delegation-ack-replay: ACK send failed for ${taskId}: ${err?.message ?? String(err)}`,
      );
      return false;
    } finally {
      this.inFlightAckTaskIds.delete(taskId);
    }
  }

  private async retryPendingAckDeliveries(): Promise<void> {
    const pending = this.tracker.getPendingAckDeliveries();
    if (pending.length === 0) {
      return;
    }
    for (const task of pending) {
      if (this.inFlightAckTaskIds.has(task.taskId)) {
        continue;
      }
      await this.sendAckDurably(task.taskId, task.pendingAckBody ?? "");
    }
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
    "You are an executor for a delegated implementation task.",
    `Operate only inside this workspace: ${input.repoPath}`,
    "",
    "Execution contract:",
    brief,
    "",
    "Response contract:",
    "- ACK has already been sent by the extension handoff layer; do not send another ACK unless asked.",
    "- Work the task directly in the current repo.",
    "- If you get blocked, send BLOCKED via agent-bus and stop.",
    "- When complete, commit your work directly and send a COMMITTED receipt with diff stat, key files, validation, and residual risks.",
    "  (IMPORTANT: You MUST include the exact task ID in the commit message to allow repo-side task tracking.)",
    "- If the task explicitly requires review before commit, send an EVIDENCE_PACK receipt instead.",
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
    '    "status": "COMMITTED",   // or "EVIDENCE_PACK" / "BLOCKED"',
    '    "summary": "Brief result summary",',
    '    "payload": {',
    '      "commit_sha": "...", // required for COMMITTED only',
    '      "branch": "...",     // required for COMMITTED only',
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
    `agent-bus send --sender code --task-id ${input.taskId} --status COMMITTED --body-file /tmp/${input.taskId}.evidence.txt`,
    `agent-bus send --sender code --task-id ${input.taskId} --status BLOCKED --body "blocked reason"`,
  ].join("\n");
}
