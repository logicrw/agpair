/**
 * Delegation Receipt Watcher — polls for executor-written receipt files.
 *
 * When a delegation task is dispatched, the executor prompt tells the AI to
 * write a JSON receipt to a well-known path:
 *
 *   /tmp/.delegation_receipts/{taskId}.receipt.json
 *
 * This watcher polls for those files. When found, it:
 *   1. Parses the receipt JSON
 *   2. Validates the task ID matches
 *   3. Sends the terminal status (EVIDENCE_PACK / BLOCKED / COMMITTED) via agent-bus
 *   4. Marks the task as terminal in the DelegationTaskTracker (dedup)
 *
 * This removes the dependency on the AI remembering to run `agent-bus send`.
 * The AI only needs to write a JSON file — which is more deterministic and
 * can be verified by the extension.
 *
 * The watcher also handles the case where the AI still uses the old
 * `agent-bus send` path — the dedup guard in DelegationTaskTracker prevents
 * double-sending.
 */

import * as fs from "fs";
import * as path from "path";

import type { DelegationTaskTracker } from "../state/delegationTaskTracker";

export interface DelegationReceipt {
  task_id: string;
  status: "EVIDENCE_PACK" | "BLOCKED" | "COMMITTED";
  body: string;
}

export interface DelegationReceiptWatcherOptions {
  tracker: DelegationTaskTracker;
  receiptDir: string;
  pollIntervalMs: number;
  staleAfterMs?: number;
  outputChannel: { appendLine(message: string): void };
  sendTerminal: (taskId: string, status: string, body: string) => Promise<void>;
}

export class DelegationReceiptWatcher {
  private readonly tracker: DelegationTaskTracker;
  private readonly receiptDir: string;
  private readonly pollIntervalMs: number;
  private readonly staleAfterMs: number;
  private readonly outputChannel: { appendLine(message: string): void };
  private readonly sendTerminal: (
    taskId: string,
    status: string,
    body: string,
  ) => Promise<void>;

  private timer: ReturnType<typeof setInterval> | null = null;
  private _running = false;
  private readonly inFlightTerminalTaskIds = new Set<string>();

  constructor(options: DelegationReceiptWatcherOptions) {
    this.tracker = options.tracker;
    this.receiptDir = options.receiptDir;
    this.pollIntervalMs = Math.max(options.pollIntervalMs, 500);
    this.staleAfterMs = Math.max(options.staleAfterMs ?? 0, 0);
    this.outputChannel = options.outputChannel;
    this.sendTerminal = options.sendTerminal;
  }

  /** Start polling for receipt files. */
  start(): void {
    if (this._running) return;
    this.ensureDir();
    this._running = true;
    this.timer = setInterval(() => {
      this.poll().catch((err) => {
        this.outputChannel.appendLine(
          `[companion] delegation-receipt-watcher poll error: ${err instanceof Error ? err.message : String(err)}`,
        );
      });
    }, this.pollIntervalMs);
    this.outputChannel.appendLine(
      `[companion] delegation-receipt-watcher started (dir=${this.receiptDir}, interval=${this.pollIntervalMs}ms).`,
    );
  }

  /** Stop polling. */
  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this._running = false;
  }

  get isRunning(): boolean {
    return this._running;
  }

  /** Dispose the watcher. */
  dispose(): void {
    this.stop();
  }

  /**
   * Read and process all pending receipt files.
   * Exposed for testing.
   */
  async poll(nowMsProvider: () => number = () => Date.now()): Promise<void> {
    const pending = this.tracker.getPending();
    if (pending.length === 0) return;

    for (const task of pending) {
      const receiptPath = task.receiptPath;
      if (!receiptPath) continue;

      try {
        if (!fs.existsSync(receiptPath)) {
          if (this.isTaskStale(task, nowMsProvider())) {
            await this.sendAndMarkTerminal(
              task.taskId,
              "BLOCKED",
              buildTimeoutBody(task.taskId, task.sessionId, task.lastActivityAt ?? task.ackedAt, this.staleAfterMs),
            );
          }
          continue;
        }

        const raw = fs.readFileSync(receiptPath, "utf-8").trim();
        if (!raw) continue;

        const receipt = this.parseReceipt(raw, task.taskId);
        if (!receipt) {
          this.outputChannel.appendLine(
            `[companion] delegation-receipt-watcher: invalid receipt for ${task.taskId}, skipping.`,
          );
          continue;
        }

        const sent = await this.sendAndMarkTerminal(
          task.taskId,
          receipt.status,
          receipt.body,
        );
        if (sent) {
          try {
            fs.unlinkSync(receiptPath);
          } catch {
            // best effort
          }
        }
      } catch (err: any) {
        this.outputChannel.appendLine(
          `[companion] delegation-receipt-watcher: error processing ${task.taskId}: ${err?.message ?? String(err)}`,
        );
      }
    }
  }

  /**
   * Parse a receipt file.
   * Returns null if the receipt is invalid or doesn't match the expected task ID.
   */
  private parseReceipt(
    raw: string,
    expectedTaskId: string,
  ): DelegationReceipt | null {
    try {
      const parsed = JSON.parse(raw);
      const taskId = parsed.task_id;
      const status = parsed.status;
      const body = typeof parsed.body === "string" ? parsed.body : "";

      if (typeof taskId !== "string" || taskId !== expectedTaskId) {
        return null;
      }
      if (status !== "EVIDENCE_PACK" && status !== "BLOCKED" && status !== "COMMITTED") {
        return null;
      }
      return { task_id: taskId, status, body };
    } catch {
      return null;
    }
  }

  private ensureDir(): void {
    try {
      fs.mkdirSync(this.receiptDir, { recursive: true });
    } catch {
      // best effort
    }
  }

  /**
   * Get the canonical receipt file path for a delegation task.
   */
  static receiptPath(receiptDir: string, taskId: string): string {
    return path.join(receiptDir, `${taskId}.receipt.json`);
  }

  /**
   * Get the default receipt directory path.
   */
  static defaultReceiptDir(): string {
    return path.join(require("os").tmpdir(), ".delegation_receipts");
  }

  private isTaskStale(
    task: { ackedAt: string; lastActivityAt?: string | null },
    nowMs: number,
  ): boolean {
    if (this.staleAfterMs <= 0) {
      return false;
    }
    const lastActivityAt = task.lastActivityAt ?? task.ackedAt;
    const lastActivityMs = Date.parse(lastActivityAt);
    if (!Number.isFinite(lastActivityMs)) {
      return false;
    }
    return nowMs - lastActivityMs >= this.staleAfterMs;
  }

  private async sendAndMarkTerminal(
    taskId: string,
    status: "EVIDENCE_PACK" | "BLOCKED" | "COMMITTED",
    body: string,
  ): Promise<boolean> {
    if (this.tracker.isTerminalSent(taskId) || this.inFlightTerminalTaskIds.has(taskId)) {
      this.outputChannel.appendLine(
        `[companion] delegation-receipt-watcher: terminal already sent for ${taskId}, skipping duplicate.`,
      );
      return false;
    }
    this.inFlightTerminalTaskIds.add(taskId);
    try {
      await this.sendTerminal(taskId, status, body);
      const marked = this.tracker.markTerminal(taskId, status, body);
      if (!marked) {
        this.outputChannel.appendLine(
          `[companion] delegation-receipt-watcher: terminal already marked for ${taskId} after send.`,
        );
      }
      this.outputChannel.appendLine(
        `[companion] delegation-receipt-watcher: sent ${status} for ${taskId} via agent-bus.`,
      );
      return true;
    } finally {
      this.inFlightTerminalTaskIds.delete(taskId);
    }
  }
}

function buildTimeoutBody(
  taskId: string,
  sessionId: string,
  lastActivityAt: string,
  staleAfterMs: number,
): string {
  const staleAfterSeconds = Math.max(Math.floor(staleAfterMs / 1000), 1);
  return [
    `Delegated task ${taskId} timed out after ${staleAfterSeconds}s without terminal progress.`,
    `session_id=${sessionId}`,
    `last_activity_at=${lastActivityAt}`,
    "The workspace has been released. Re-dispatch the task to continue with a fresh executor session.",
  ].join("\n");
}
