/**
 * Delegation Heartbeat Service — emits non-terminal RUNNING replies for
 * pending delegated tasks.
 *
 * Semantics:
 *   RUNNING is a **liveness signal only**. It means:
 *     "The extension still has a live delegated task pending terminal receipt."
 *   It does NOT mean:
 *     "The model definitly made new code progress."
 *
 * The heartbeat loop runs at a configurable cadence (default 30 s).
 * For every pending task in the DelegationTaskTracker it:
 *   1. Sends a non-terminal RUNNING reply via the provided sendRunning callback.
 *   2. Updates the tracker's lastHeartbeatAt timestamp (NOT lastActivityAt).
 *
 * IMPORTANT: Heartbeat intentionally does NOT update lastActivityAt.
 * The stale-timeout in DelegationReceiptWatcher keys off lastActivityAt,
 * so heartbeats must not extend the timeout window. A task with no terminal
 * receipt must still auto-BLOCK once the stale window is exceeded,
 * regardless of how many heartbeats were sent.
 *
 * The loop stops automatically once a task reaches terminal state, and
 * restarts cleanly when a reopened task (REVIEW / APPROVED) re-enters
 * the pending path.
 *
 * Heartbeat MUST NOT:
 *   - Count as terminal anywhere
 *   - Interfere with dedup protection for terminal replies
 *   - Replace the receipt watcher timeout fallback
 */

import type { DelegationTaskTracker } from "../state/delegationTaskTracker";

export interface DelegationHeartbeatReply {
  taskId: string;
  status: "RUNNING";
  body: string;
}

export interface DelegationHeartbeatServiceOptions {
  tracker: DelegationTaskTracker;
  /** Heartbeat cadence in milliseconds. Default: 30 000 (30 s). */
  intervalMs?: number;
  outputChannel: { appendLine(message: string): void };
  /** Callback to send the non-terminal RUNNING reply to the desktop side. */
  sendRunning: (reply: DelegationHeartbeatReply) => Promise<void>;
}

/** Default heartbeat interval: 30 seconds */
export const DEFAULT_HEARTBEAT_INTERVAL_MS = 30_000;

export class DelegationHeartbeatService {
  private readonly tracker: DelegationTaskTracker;
  private readonly intervalMs: number;
  private readonly outputChannel: { appendLine(message: string): void };
  private readonly sendRunning: (reply: DelegationHeartbeatReply) => Promise<void>;

  private timer: ReturnType<typeof setInterval> | null = null;
  private _running = false;

  constructor(options: DelegationHeartbeatServiceOptions) {
    this.tracker = options.tracker;
    this.intervalMs = Math.max(options.intervalMs ?? DEFAULT_HEARTBEAT_INTERVAL_MS, 1000);
    this.outputChannel = options.outputChannel;
    this.sendRunning = options.sendRunning;
  }

  /** Start the heartbeat loop. Idempotent. */
  start(): void {
    if (this._running) return;
    this._running = true;
    this.timer = setInterval(() => {
      this.tick().catch((err) => {
        this.outputChannel.appendLine(
          `[companion] delegation-heartbeat error: ${err instanceof Error ? err.message : String(err)}`,
        );
      });
    }, this.intervalMs);
    this.outputChannel.appendLine(
      `[companion] delegation-heartbeat started (interval=${this.intervalMs}ms).`,
    );
  }

  /** Stop the heartbeat loop. */
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

  get heartbeatIntervalMs(): number {
    return this.intervalMs;
  }

  /** Dispose (alias for stop). */
  dispose(): void {
    this.stop();
  }

  /**
   * Execute one heartbeat tick — send RUNNING for every pending task.
   * Exposed for testing (call manually instead of relying on setInterval).
   */
  async tick(): Promise<void> {
    const pending = this.tracker.getPending();
    if (pending.length === 0) return;

    const now = new Date().toISOString();

    for (const task of pending) {
      try {
        const body = buildHeartbeatBody(task.taskId, task.sessionId, task.ackedAt, now);
        await this.sendRunning({
          taskId: task.taskId,
          status: "RUNNING",
          body,
        });
        // Update heartbeat timestamp only — intentionally NOT lastActivityAt.
        // Stale timeout keys off lastActivityAt; heartbeats must not extend it.
        this.tracker.touchHeartbeat(task.taskId, now);
      } catch (err: any) {
        // Heartbeat failures are non-fatal — log and continue
        this.outputChannel.appendLine(
          `[companion] delegation-heartbeat: failed to send RUNNING for ${task.taskId}: ${err?.message ?? String(err)}`,
        );
      }
    }
  }
}

function buildHeartbeatBody(
  taskId: string,
  sessionId: string,
  ackedAt: string,
  now: string,
): string {
  return [
    `Liveness heartbeat for delegated task ${taskId}.`,
    `session_id=${sessionId}`,
    `acked_at=${ackedAt}`,
    `heartbeat_at=${now}`,
    "Semantics: extension still holds a live pending task awaiting terminal receipt. This is NOT a progress indicator.",
  ].join("\n");
}
