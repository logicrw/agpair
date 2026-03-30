/**
 * Delegation Receipt Watcher — polls for executor-written receipt files.
 *
 * When a delegation task is dispatched, the executor prompt tells the AI to
 * write a JSON receipt to a well-known path:
 *
 *   /tmp/.delegation_receipts/{taskId}.receipt.json
 *
 * This watcher polls for those files. When found, it:
 *   1. Persists the terminal result durably (survives restarts)
 *   2. Generates a stable delivery ID for desktop-side dedup
 *   3. Sends the terminal status via agent-bus with crash-after-send safety
 *   4. Marks the task as delivered in the DelegationTaskTracker
 *
 * Crash recovery: on restart, phase 1 retries any pending terminal deliveries
 * that were durably recorded but not yet successfully transported. If the
 * inflight marker is set, the send is assumed to have succeeded (crash-after-send)
 * and the task is marked delivered WITHOUT resending.
 */

import * as fs from "fs";
import * as path from "path";

import { parseDelegationReceipt, type DelegationReceipt } from "../protocols/receipt";
import type { DelegationTaskTracker } from "../state/delegationTaskTracker";
import type { SessionController } from "../sdk/sessionController";

export interface DelegationReceiptWatcherOptions {
  tracker: DelegationTaskTracker;
  receiptDir: string;
  pollIntervalMs: number;
  staleAfterMs?: number;
  outputChannel: { appendLine(message: string): void };
  sendTerminal: (taskId: string, status: string, body: string) => Promise<void>;
  /** SessionController for stuck-session recovery. If omitted, recovery is disabled. */
  sessionCtrl?: SessionController;
  /** Maximum session recovery attempts per task before falling back to BLOCKED. Default: 2. */
  recoveryMaxRetries?: number;
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
  private readonly sessionCtrl: SessionController | null;
  private readonly recoveryMaxRetries: number;
  private readonly recoveryAttempts = new Map<string, number>();

  constructor(options: DelegationReceiptWatcherOptions) {
    this.tracker = options.tracker;
    this.receiptDir = options.receiptDir;
    this.pollIntervalMs = Math.max(options.pollIntervalMs, 500);
    this.staleAfterMs = Math.max(options.staleAfterMs ?? 0, 0);
    this.outputChannel = options.outputChannel;
    this.sendTerminal = options.sendTerminal;
    this.sessionCtrl = options.sessionCtrl ?? null;
    this.recoveryMaxRetries = Math.max(options.recoveryMaxRetries ?? 2, 0);
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
   * Also retries any pending terminal deliveries that were durably recorded
   * but not yet successfully transported (survives restart / receipt deletion).
   * Exposed for testing.
   */
  async poll(nowMsProvider: () => number = () => Date.now()): Promise<void> {
    // ── Phase 1: retry any pending terminal deliveries already in tracker ──
    // These survive restarts and do NOT need the original receipt file.
    const pendingDeliveries = this.tracker.getPendingTerminalDeliveries();
    for (const task of pendingDeliveries) {
      await this.retryPendingTerminalDelivery(task.taskId);
    }

    // ── Phase 2: process receipt files for tasks with no pending delivery ──
    const pending = this.tracker.getPending();
    if (pending.length === 0) return;

    for (const task of pending) {
      // Skip if this task already has a pending delivery (handled in phase 1)
      if (this.tracker.hasPendingTerminalDelivery(task.taskId)) continue;

      const receiptPath = task.receiptPath;
      if (!receiptPath) continue;

      try {
        if (!fs.existsSync(receiptPath)) {
          if (this.isTaskStale(task, nowMsProvider())) {
            const recovered = await this.trySessionRecovery(task);
            if (!recovered) {
              await this.prepareAndSendTerminal(
                task.taskId,
                "BLOCKED",
                buildTimeoutBody(task.taskId, task.sessionId, task.lastActivityAt ?? task.ackedAt, this.staleAfterMs),
                null, // no receipt file to clean up
              );
            }
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

        await this.prepareAndSendTerminal(
          task.taskId,
          receipt.status,
          receipt.body,
          receiptPath,
        );
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
    return parseDelegationReceipt(raw, expectedTaskId);
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

  /**
   * Attempt to recover a stuck task by creating a new session and re-sending
   * the original prompt. Returns true if recovery was initiated (task reopened
   * with a fresh session), false if recovery is not possible or exhausted.
   */
  private async trySessionRecovery(
    task: { taskId: string; sessionId: string; taskBody?: string | null; receiptPath: string },
  ): Promise<boolean> {
    if (!this.sessionCtrl || !task.taskBody) {
      return false;
    }
    const attempts = this.recoveryAttempts.get(task.taskId) ?? 0;
    if (attempts >= this.recoveryMaxRetries) {
      this.outputChannel.appendLine(
        `[companion] session-recovery: exhausted ${this.recoveryMaxRetries} retries for ${task.taskId}, falling back to BLOCKED.`,
      );
      return false;
    }
    this.recoveryAttempts.set(task.taskId, attempts + 1);
    this.outputChannel.appendLine(
      `[companion] session-recovery: attempt ${attempts + 1}/${this.recoveryMaxRetries} for ${task.taskId} (old session=${task.sessionId})`,
    );

    try {
      this.outputChannel.appendLine(
        `[companion] session-recovery: terminating old session ${task.sessionId}...`,
      );
      await this.sessionCtrl.terminateSession(task.sessionId);

      const result = await this.sessionCtrl.createBackgroundSession(task.taskBody, {
        allowInteractiveFallback: true,
        contextLabel: `delegation recovery ${task.taskId}`,
      });
      if (!result.ok || !result.session_id) {
        this.outputChannel.appendLine(
          `[companion] session-recovery: failed to create new session for ${task.taskId}: ${result.error ?? "unknown"}`,
        );
        return false;
      }

      // Reopen the task with the new session — resets stale timer and clears terminal fields
      this.tracker.reopen(task.taskId, "RUNNING");
      // Update session ID in tracker (persists the new session ID to disk)
      this.tracker.updateSession(
        task.taskId,
        result.session_id,
        DelegationReceiptWatcher.receiptPath(this.receiptDir, task.taskId),
      );

      this.outputChannel.appendLine(
        `[companion] session-recovery: ${task.taskId} recovered → new session ${result.session_id}`,
      );
      return true;
    } catch (err: any) {
      this.outputChannel.appendLine(
        `[companion] session-recovery: error for ${task.taskId}: ${err?.message ?? String(err)}`,
      );
      return false;
    }
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

  /**
   * Two-phase durable terminal delivery with crash-after-send safety:
   *   1. Persist the terminal result as pending (durable before send)
   *   2. Mark inflight (persisted synchronously — crash-after-send detection)
   *   3. Attempt transport delivery with stable delivery ID
   *   4. If send succeeds → mark delivered and clean receipt file
   *   5. If send fails → clear inflight marker, pending record stays for retry
   */
  private async prepareAndSendTerminal(
    taskId: string,
    status: "EVIDENCE_PACK" | "BLOCKED" | "COMMITTED",
    body: string,
    receiptPath: string | null,
  ): Promise<boolean> {
    if (this.tracker.isTerminalSent(taskId) || this.inFlightTerminalTaskIds.has(taskId)) {
      this.outputChannel.appendLine(
        `[companion] delegation-receipt-watcher: terminal already sent for ${taskId}, skipping duplicate.`,
      );
      return false;
    }

    // Step 1: persist the terminal result durably BEFORE transport send.
    const prepared = this.tracker.preparePendingTerminal(taskId, status, body);
    if (!prepared) {
      this.outputChannel.appendLine(
        `[companion] delegation-receipt-watcher: preparePendingTerminal returned false for ${taskId}, skipping.`,
      );
      return false;
    }

    // Read the stable delivery ID generated by the tracker.
    const deliveryId = this.tracker.get(taskId)?.pendingTerminalDeliveryId;

    // Step 2: mark inflight — persisted synchronously.
    this.tracker.markPendingTerminalInflight(taskId);

    // Step 3: attempt transport delivery with stable delivery identity
    const wireBody = deliveryId ? wrapBodyWithDeliveryId(body, deliveryId) : body;
    this.inFlightTerminalTaskIds.add(taskId);
    try {
      await this.sendTerminal(taskId, status, wireBody);
      // Step 4: mark as delivered
      this.tracker.markPendingTerminalDelivered(taskId);
      this.outputChannel.appendLine(
        `[companion] delegation-receipt-watcher: sent ${status} for ${taskId} via agent-bus.`,
      );
      // Clean up receipt file on success
      if (receiptPath) {
        try {
          fs.unlinkSync(receiptPath);
        } catch {
          // best effort
        }
      }
      return true;
    } catch (err: any) {
      // Step 5: send failed — clear inflight so next poll can retry
      this.tracker.clearPendingTerminalInflight(taskId);
      this.outputChannel.appendLine(
        `[companion] delegation-receipt-watcher: send failed for ${taskId}: ${err?.message ?? String(err)}`,
      );
      return false;
    } finally {
      this.inFlightTerminalTaskIds.delete(taskId);
    }
  }

  /**
   * Retry a pending terminal delivery that was durably recorded but not yet
   * successfully transported. Used on poll phase 1 and across restarts.
   *
   * Crash-after-send safety: if the inflight marker is set, it means a previous
   * send was started (and likely succeeded) but markPendingTerminalDelivered
   * did not persist. We mark as delivered WITHOUT resending to prevent
   * desktop-side duplicates.
   */
  private async retryPendingTerminalDelivery(taskId: string): Promise<boolean> {
    if (this.tracker.isTerminalSent(taskId) || this.inFlightTerminalTaskIds.has(taskId)) {
      return false;
    }

    const task = this.tracker.get(taskId);
    if (!task || !task.pendingTerminalStatus || task.pendingTerminalBody == null) {
      return false;
    }

    // ── Crash-after-send detection ──
    if (task.pendingTerminalInflightAt) {
      this.tracker.markPendingTerminalDelivered(taskId);
      this.outputChannel.appendLine(
        `[companion] delegation-receipt-watcher: ${taskId} had inflight delivery marker ` +
        `(set at ${task.pendingTerminalInflightAt}) — marking delivered without resending ` +
        `(crash-after-send recovery).`,
      );
      if (task.receiptPath) {
        try { fs.unlinkSync(task.receiptPath); } catch { /* best effort */ }
      }
      return false; // did not send — already assumed delivered
    }

    // ── Normal retry: no inflight marker → send was never attempted ──
    this.tracker.markPendingTerminalInflight(taskId);
    const wireBody = task.pendingTerminalDeliveryId
      ? wrapBodyWithDeliveryId(task.pendingTerminalBody, task.pendingTerminalDeliveryId)
      : task.pendingTerminalBody;
    this.inFlightTerminalTaskIds.add(taskId);
    try {
      await this.sendTerminal(taskId, task.pendingTerminalStatus, wireBody);
      this.tracker.markPendingTerminalDelivered(taskId);
      this.outputChannel.appendLine(
        `[companion] delegation-receipt-watcher: retry sent ${task.pendingTerminalStatus} for ${taskId} via agent-bus.`,
      );
      if (task.receiptPath) {
        try { fs.unlinkSync(task.receiptPath); } catch { /* best effort */ }
      }
      return true;
    } catch (err: any) {
      this.tracker.clearPendingTerminalInflight(taskId);
      this.outputChannel.appendLine(
        `[companion] delegation-receipt-watcher: retry failed for ${taskId}: ${err?.message ?? String(err)}`,
      );
      return false;
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

/** Header prefix used to embed the stable delivery identity in the body. */
export const DELIVERY_ID_HEADER = "X-Delivery-Id:";

/**
 * Prepend a parseable delivery-id header line to the body.
 * The header is the first line; the original body follows on the next line.
 */
function wrapBodyWithDeliveryId(body: string, deliveryId: string): string {
  return `${DELIVERY_ID_HEADER} ${deliveryId}\n${body}`;
}
