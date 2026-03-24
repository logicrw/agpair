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
  status: "ACK" | "RUNNING" | "EVIDENCE_PACK" | "BLOCKED" | "COMMITTED";
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
  private readonly sendReplyFn: (reply: AgentBusDelegationReply) => Promise<void>;
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
        this.sendReplyFn({ taskId, status: status as "EVIDENCE_PACK" | "BLOCKED" | "COMMITTED", body }),
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
  private static readonly CONTINUATION_STATUSES = new Set(["REVIEW", "REVIEW_DELTA"]);

  private async handleMessage(message: AgentBusMessage): Promise<void> {
    if (typeof message.id === "number") {
      if (this.processedMessageIds.has(message.id)) {
        return;
      }
      this.processedMessageIds.add(message.id);
    }

    const status = typeof message.status === "string" ? message.status : "";
    const taskId = typeof message.task_id === "string" && message.task_id.length > 0
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

    const existingTask = this.tracker.getPendingForRepo(repoPath, taskId);
    if (existingTask) {
      this.outputChannel.appendLine(
        `[companion] rejecting TASK ${taskId}: workspace ${repoPath} already busy with ${existingTask.taskId} (${existingTask.sessionId})`,
      );
      await this.sendReplyFn({
        taskId,
        status: "BLOCKED",
        body:
          `Workspace ${repoPath} already has an active delegated task ` +
          `${existingTask.taskId} in session ${existingTask.sessionId}. ` +
          "Wait for it to reach terminal state before sending a different task.",
      });
      return;
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
    const result = await this.sessionCtrl.createBackgroundSession(prompt);
    if (!result.ok || !result.session_id) {
      await this.sendReplyFn({
        taskId,
        status: "BLOCKED",
        body: `Failed to start Antigravity executor session: ${result.error ?? "unknown error"}`,
      });
      return;
    }

    this.outputChannel.appendLine(
      `[companion] delegated TASK ${taskId} to Antigravity session ${result.session_id}`,
    );
    await this.sendReplyFn({
      taskId,
      status: "ACK",
      body: `Accepted by Antigravity auto-handoff. session_id=${result.session_id} repo_path=${repoPath}`,
    });

    // Register in tracker for receipt-based terminal auto-return
    this.tracker.register({
      taskId,
      sessionId: result.session_id,
      repoPath,
      receiptPath,
      status: "ACKED",
      ackedAt: new Date().toISOString(),
      lastActivityAt: new Date().toISOString(),
      terminalSentAt: null,
      terminalStatus: null,
      terminalBody: null,
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
    if (!tracked) {
      this.outputChannel.appendLine(
        `[companion] ${status} for unknown task ${taskId} — sending BLOCKED`,
      );
      await this.sendReplyFn({
        taskId,
        status: "BLOCKED",
        body: `Cannot continue: task ${taskId} is not tracked by this extension instance.`,
      });
      return;
    }

    if (!tracked.sessionId) {
      this.outputChannel.appendLine(
        `[companion] ${status} for ${taskId} has no sessionId — sending BLOCKED`,
      );
      await this.sendReplyFn({
        taskId,
        status: "BLOCKED",
        body: `Cannot continue: tracked task ${taskId} has no associated session.`,
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
      const result = await this.sessionCtrl.sendPrompt(tracked.sessionId, prompt);
      if (!result.ok) {
        throw new Error(result.error ?? "sendPrompt returned ok=false");
      }
      this.tracker.reopen(taskId, "RUNNING");
      this.outputChannel.appendLine(
        `[companion] ${status} for ${taskId} sent into session ${tracked.sessionId}`,
      );
    } catch (err: any) {
      this.outputChannel.appendLine(
        `[companion] ${status} continuation failed for ${taskId}: ${err.message}`,
      );
      await this.sendReplyFn({
        taskId,
        status: "BLOCKED",
        body: `Failed to send ${status} into session ${tracked.sessionId}: ${err.message}`,
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
    if (!tracked) {
      this.outputChannel.appendLine(
        `[companion] APPROVED for unknown task ${taskId} — sending BLOCKED`,
      );
      await this.sendReplyFn({
        taskId,
        status: "BLOCKED",
        body: `Cannot commit: task ${taskId} is not tracked by this extension instance.`,
      });
      return;
    }

    if (!tracked.sessionId) {
      this.outputChannel.appendLine(
        `[companion] APPROVED for ${taskId} has no sessionId — sending BLOCKED`,
      );
      await this.sendReplyFn({
        taskId,
        status: "BLOCKED",
        body: `Cannot commit: tracked task ${taskId} has no associated session.`,
      });
      return;
    }

    const prompt = buildApprovedCommitPrompt({
      taskId,
      body: typeof message.body === "string" ? message.body : "",
      receiptPath: tracked.receiptPath,
    });

    try {
      const result = await this.sessionCtrl.sendPrompt(tracked.sessionId, prompt);
      if (!result.ok) {
        throw new Error(result.error ?? "sendPrompt returned ok=false");
      }
      this.tracker.reopen(taskId, "RUNNING");
      this.outputChannel.appendLine(
        `[companion] APPROVED for ${taskId} sent into session ${tracked.sessionId} (commit phase)`,
      );
    } catch (err: any) {
      this.outputChannel.appendLine(
        `[companion] APPROVED commit continuation failed for ${taskId}: ${err.message}`,
      );
      await this.sendReplyFn({
        taskId,
        status: "BLOCKED",
        body: `Failed to send APPROVED into session ${tracked.sessionId}: ${err.message}`,
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
        reject(new Error(stderr.trim() || `agent-bus send exited ${code ?? 1}`));
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
    "You are the Antigravity executor for a Codex-reviewed implementation task.",
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
    "When you finish (whether success or blocked), write a JSON receipt file so the",
    "extension can automatically return your result to Codex WITHOUT you needing to",
    "manually invoke agent-bus send. Write this file BEFORE or INSTEAD OF running agent-bus send:",
    "",
    `  Receipt path: ${input.receiptPath}`,
    "",
    "  Receipt format (JSON):",
    "  {",
    `    "task_id": "${input.taskId}",`,
    '    "status": "EVIDENCE_PACK",   // or "BLOCKED"',
    '    "body": "## Evidence\\n- diff stat: ...\\n- key files: ...\\n- validation: ...\\n- residual risks: ..."',
    "  }",
    "",
    "The extension will detect this file and send the terminal status to Codex automatically.",
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
    `--- Codex review continuation (${input.status}) for ${input.taskId} ---`,
    "",
    "The Codex reviewer has sent feedback on your current work.",
    "Address the review comments below and continue working on the task.",
    "Do NOT start from scratch — continue from where you left off.",
    "",
    "Terminal receipt reminder:",
    `- When you finish this continuation round, write the terminal receipt again to: ${input.receiptPath}`,
    `- The receipt must still use task_id=${input.taskId} and status=EVIDENCE_PACK or BLOCKED.`,
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
    `--- Codex APPROVED: commit phase for ${input.taskId} ---`,
    "",
    "The Codex reviewer has APPROVED your work. You are now in the commit phase.",
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
    `    \"task_id\": \"${input.taskId}\",`,
    '    \"status\": \"COMMITTED\",   // or \"BLOCKED\" if commit fails',
    '    \"body\": \"## Commit\\n- commit hash: ...\\n- branch: ...\\n- summary: ...\"',
    "  }",
    "",
    "Use status=COMMITTED if the commit succeeded, or status=BLOCKED if something prevents committing.",
  ].join("\n");
}
