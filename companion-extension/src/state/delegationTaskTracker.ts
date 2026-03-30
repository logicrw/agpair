/**
 * Delegation Task Tracker — in-memory state for delegation tasks.
 *
 * Tracks delegation tasks from TASK → ACK → terminal (EVIDENCE_PACK / BLOCKED / COMMITTED).
 * Provides deduplication guard so the extension never sends duplicate terminal
 * status messages for the same delegated task.
 *
 * Includes crash-safe two-phase terminal delivery:
 *   - preparePendingTerminal(): durably record terminal result before send
 *   - markPendingTerminalInflight(): mark that a send is about to start
 *   - markPendingTerminalDelivered(): promote pending → delivered after success
 *   - On restart with inflight marker set: assume delivered (crash-after-send)
 *
 * This is separate from the bridge-first TaskSessionStore because delegation
 * tasks use agent-bus transport, not bridge /run_task.
 */

import * as fs from "fs";
import * as path from "path";
import { randomBytes } from "crypto";

export type DelegationTaskStatus =
  | "ACKED"
  | "RUNNING"
  | "EVIDENCE_PACK"
  | "BLOCKED"
  | "COMMITTED";

export interface DelegationTask {
  taskId: string;
  sessionId: string;
  repoPath: string;
  receiptPath: string;
  /** Original task body (prompt). Stored for session recovery on stuck tasks. */
  taskBody?: string | null;
  status: DelegationTaskStatus;
  ackedAt: string;
  lastActivityAt?: string | null;
  /** Timestamp of the most recent RUNNING heartbeat sent for this task. */
  lastHeartbeatAt?: string | null;
  terminalSentAt: string | null;
  terminalStatus: string | null;
  terminalBody: string | null;
  /**
   * Pending terminal delivery fields.
   * These are set when a terminal result is locally known but not yet
   * successfully delivered via transport. Persisted to survive restarts.
   */
  pendingTerminalStatus: string | null;
  pendingTerminalBody: string | null;
  pendingTerminalPreparedAt: string | null;
  /**
   * Set immediately BEFORE calling sendTerminal() and cleared on send failure.
   * If set on restart, the send was attempted (and likely succeeded) but
   * markPendingTerminalDelivered never persisted. The retry path treats
   * this as "already delivered" to prevent desktop-side duplicates.
   */
  pendingTerminalInflightAt?: string | null;
  /**
   * Stable delivery identity for this terminal cycle.
   * Generated once in preparePendingTerminal(), reused for every retry and
   * restart recovery of the SAME logical terminal result. Cleared on reopen()
   * so the next terminal cycle gets a fresh id.
   *
   * Format: del_<24-hex-chars>  (e.g. del_a1b2c3d4e5f6a7b8c9d0e1f2)
   * Uniqueness: (taskId, deliveryId) identifies a single logical terminal.
   */
  pendingTerminalDeliveryId?: string | null;
}

export class DelegationTaskTracker {
  private tasks = new Map<string, DelegationTask>();
  private readonly stateFilePath: string | null;

  constructor(stateFilePath?: string) {
    this.stateFilePath = stateFilePath?.trim() ? stateFilePath : null;
    this.loadFromDisk();
  }

  /**
   * Register a new delegation task after ACK has been sent.
   * Returns false if a task with the same ID is already tracked and has not
   * reached terminal state.
   */
  register(task: DelegationTask): boolean {
    const existing = this.tasks.get(task.taskId);
    if (existing && !existing.terminalSentAt) {
      return false; // already in flight
    }
    this.tasks.set(task.taskId, {
      ...task,
      taskBody: task.taskBody ?? null,
      lastActivityAt: task.lastActivityAt ?? task.ackedAt,
      lastHeartbeatAt: task.lastHeartbeatAt ?? null,
      pendingTerminalStatus: task.pendingTerminalStatus ?? null,
      pendingTerminalBody: task.pendingTerminalBody ?? null,
      pendingTerminalPreparedAt: task.pendingTerminalPreparedAt ?? null,
      pendingTerminalInflightAt: task.pendingTerminalInflightAt ?? null,
      pendingTerminalDeliveryId: task.pendingTerminalDeliveryId ?? null,
    });
    this.persist();
    return true;
  }

  /**
   * Refresh the last activity timestamp for a task. Optionally updates status.
   */
  touch(taskId: string, status?: DelegationTaskStatus, at?: string): boolean {
    const task = this.tasks.get(taskId);
    if (!task || task.terminalSentAt) {
      return false;
    }
    task.lastActivityAt = at ?? new Date().toISOString();
    if (status) {
      task.status = status;
    }
    this.persist();
    return true;
  }

  /**
   * Re-open a previously terminal task for a continuation round.
   * Clears the terminal markers and heartbeat timestamp so the receipt
   * watcher can emit another EVIDENCE_PACK / BLOCKED after REVIEW or
   * REVIEW_DELTA is applied, and the heartbeat loop can resume cleanly.
   */
  reopen(taskId: string, status: DelegationTaskStatus = "RUNNING", at?: string): boolean {
    const task = this.tasks.get(taskId);
    if (!task) {
      return false;
    }
    task.status = status;
    task.lastActivityAt = at ?? new Date().toISOString();
    task.lastHeartbeatAt = null;
    task.terminalSentAt = null;
    task.terminalStatus = null;
    task.terminalBody = null;
    task.pendingTerminalStatus = null;
    task.pendingTerminalBody = null;
    task.pendingTerminalPreparedAt = null;
    task.pendingTerminalInflightAt = null;
    task.pendingTerminalDeliveryId = null;
    this.persist();
    return true;
  }

  /**
   * Update the session ID and receipt path for a task.
   * Used for session recovery when a task gets stuck.
   */
  updateSession(taskId: string, sessionId: string, receiptPath: string): boolean {
    const task = this.tasks.get(taskId);
    if (!task) return false;
    task.sessionId = sessionId;
    task.receiptPath = receiptPath;
    this.persist();
    return true;
  }

  /**
   * Record that a RUNNING heartbeat was sent for this task.
   */
  touchHeartbeat(taskId: string, at?: string): boolean {
    const task = this.tasks.get(taskId);
    if (!task || task.terminalSentAt) {
      return false;
    }
    task.lastHeartbeatAt = at ?? new Date().toISOString();
    this.persist();
    return true;
  }

  /**
   * Get a tracked delegation task by task ID.
   */
  get(taskId: string): DelegationTask | undefined {
    return this.tasks.get(taskId);
  }

  /**
   * Find a non-terminal delegated task by Antigravity session ID.
   * Used by the SDK monitor to translate real step activity back into
   * tracker-level progress updates for delegated tasks.
   */
  findBySessionId(sessionId: string): DelegationTask | undefined {
    for (const task of this.tasks.values()) {
      if (task.sessionId === sessionId && !task.terminalSentAt) {
        return task;
      }
    }
    return undefined;
  }

  /**
   * Mark a task as having sent its terminal status.
   * Returns false if already sent (dedup guard).
   */
  markTerminal(
    taskId: string,
    status: string,
    body: string,
  ): boolean {
    const task = this.tasks.get(taskId);
    if (!task) return false;
    if (task.terminalSentAt) return false; // already sent
    task.terminalSentAt = new Date().toISOString();
    task.terminalStatus = status;
    task.terminalBody = body;
    task.status = status as DelegationTaskStatus;
    this.persist();
    return true;
  }

  /**
   * Forcibly abandon a task (e.g. preempted by a new task).
   * Sets terminalSentAt to prevent it from blocking the workspace.
   */
  abandon(taskId: string, body = "Task abandoned locally"): boolean {
    const task = this.tasks.get(taskId);
    if (!task) return false;
    if (task.terminalSentAt) return false;
    task.terminalSentAt = new Date().toISOString();
    task.terminalStatus = "BLOCKED";
    task.terminalBody = body;
    task.status = "BLOCKED";
    this.persist();
    return true;
  }

  /**
   * Check if terminal has already been sent for a task (dedup query).
   */
  isTerminalSent(taskId: string): boolean {
    const task = this.tasks.get(taskId);
    return task?.terminalSentAt != null;
  }

  /**
   * Persist a terminal result as pending delivery before attempting transport send.
   * This makes the terminal result durable — it survives restarts and receipt file deletion.
   * Also generates a stable pendingTerminalDeliveryId for dedup on the desktop side.
   * Returns false if a pending terminal or delivered terminal already exists (dedup).
   */
  preparePendingTerminal(
    taskId: string,
    status: string,
    body: string,
    at?: string,
  ): boolean {
    const task = this.tasks.get(taskId);
    if (!task) return false;
    if (task.terminalSentAt) return false; // already delivered
    if (task.pendingTerminalStatus) return false; // already pending
    task.pendingTerminalStatus = status;
    task.pendingTerminalBody = body;
    task.pendingTerminalPreparedAt = at ?? new Date().toISOString();
    task.pendingTerminalDeliveryId = `del_${randomBytes(12).toString("hex")}`;
    this.persist();
    return true;
  }

  /**
   * Mark a pending terminal delivery as successfully delivered.
   * Moves the pending fields into the terminal-sent fields and clears pending.
   * Returns false if there is no pending delivery or if already delivered.
   */
  markPendingTerminalDelivered(taskId: string, at?: string): boolean {
    const task = this.tasks.get(taskId);
    if (!task) return false;
    if (task.terminalSentAt) return false; // already delivered
    if (!task.pendingTerminalStatus) return false; // nothing pending
    task.terminalSentAt = at ?? new Date().toISOString();
    task.terminalStatus = task.pendingTerminalStatus;
    task.terminalBody = task.pendingTerminalBody;
    task.status = task.pendingTerminalStatus as DelegationTaskStatus;
    task.pendingTerminalStatus = null;
    task.pendingTerminalBody = null;
    task.pendingTerminalPreparedAt = null;
    task.pendingTerminalInflightAt = null;
    task.pendingTerminalDeliveryId = null;
    this.persist();
    return true;
  }

  /**
   * Mark that a send attempt is about to start for this pending terminal.
   * Must be called BEFORE sendTerminal() and persisted synchronously.
   * On restart, if this is set, we assume the send succeeded (crash-after-send
   * recovery) and mark delivered without resending.
   */
  markPendingTerminalInflight(taskId: string, at?: string): boolean {
    const task = this.tasks.get(taskId);
    if (!task) return false;
    if (task.terminalSentAt) return false;
    if (!task.pendingTerminalStatus) return false;
    task.pendingTerminalInflightAt = at ?? new Date().toISOString();
    this.persist();
    return true;
  }

  /**
   * Clear the inflight marker after a send failure so the next poll can retry.
   */
  clearPendingTerminalInflight(taskId: string): boolean {
    const task = this.tasks.get(taskId);
    if (!task) return false;
    task.pendingTerminalInflightAt = null;
    this.persist();
    return true;
  }

  /**
   * Check if a task has a pending terminal delivery awaiting transport send.
   */
  hasPendingTerminalDelivery(taskId: string): boolean {
    const task = this.tasks.get(taskId);
    return (task?.pendingTerminalStatus != null) && (task?.terminalSentAt == null);
  }

  /**
   * Get all tasks that have pending terminal deliveries (not yet transported).
   */
  getPendingTerminalDeliveries(): DelegationTask[] {
    return this.getAll().filter(
      (t) => t.pendingTerminalStatus != null && t.terminalSentAt == null,
    );
  }

  /**
   * Get all tracked tasks.
   */
  getAll(): DelegationTask[] {
    return Array.from(this.tasks.values());
  }

  /**
   * Get tasks that are still waiting for terminal status.
   */
  getPending(): DelegationTask[] {
    return this.getAll().filter((t) => t.terminalSentAt === null);
  }

  /**
   * Get the first active delegated task for a repo path, excluding an optional task id.
   */
  getPendingForRepo(repoPath: string, excludeTaskId?: string): DelegationTask | undefined {
    return this.getPending().find(
      (task) => task.repoPath === repoPath && task.taskId !== excludeTaskId,
    );
  }

  /**
   * Count of active (non-terminal) delegation tasks.
   */
  pendingCount(): number {
    return this.getPending().length;
  }

  /**
   * Total count of tracked delegation tasks.
   */
  count(): number {
    return this.tasks.size;
  }

  /**
   * Get a summary for health/debug output.
   */
  getSummary(): {
    total: number;
    pending: number;
    completed: number;
    pendingTerminalDeliveries: number;
    tasks: Array<{
      taskId: string;
      status: DelegationTaskStatus;
      sessionId: string;
      ackedAt: string;
      lastActivityAt: string;
      lastHeartbeatAt: string | null;
      terminalSentAt: string | null;
      pendingTerminalStatus: string | null;
      pendingTerminalPreparedAt: string | null;
      pendingTerminalInflightAt: string | null;
      pendingTerminalDeliveryId: string | null;
    }>;
  } {
    const all = this.getAll();
    const pending = all.filter((t) => t.terminalSentAt === null);
    const pendingTerminalDeliveries = this.getPendingTerminalDeliveries();
    return {
      total: all.length,
      pending: pending.length,
      completed: all.length - pending.length,
      pendingTerminalDeliveries: pendingTerminalDeliveries.length,
      tasks: all.map((t) => ({
        taskId: t.taskId,
        status: t.status,
        sessionId: t.sessionId,
        ackedAt: t.ackedAt,
        lastActivityAt: t.lastActivityAt ?? t.ackedAt,
        lastHeartbeatAt: t.lastHeartbeatAt ?? null,
        terminalSentAt: t.terminalSentAt,
        pendingTerminalStatus: t.pendingTerminalStatus ?? null,
        pendingTerminalPreparedAt: t.pendingTerminalPreparedAt ?? null,
        pendingTerminalInflightAt: t.pendingTerminalInflightAt ?? null,
        pendingTerminalDeliveryId: t.pendingTerminalDeliveryId ?? null,
      })),
    };
  }

  private loadFromDisk(): void {
    if (!this.stateFilePath) {
      return;
    }
    try {
      if (!fs.existsSync(this.stateFilePath)) {
        return;
      }
      const raw = fs.readFileSync(this.stateFilePath, "utf-8").trim();
      if (!raw) {
        return;
      }
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) {
        return;
      }
      const restored = new Map<string, DelegationTask>();
      for (const entry of parsed) {
        const task = normalizeTask(entry);
        if (!task) {
          continue;
        }
        restored.set(task.taskId, task);
      }
      this.tasks = restored;
    } catch {
      this.tasks = new Map<string, DelegationTask>();
    }
  }

  private persist(): void {
    if (!this.stateFilePath) {
      return;
    }
    try {
      fs.mkdirSync(path.dirname(this.stateFilePath), { recursive: true });
      const tmpPath = `${this.stateFilePath}.tmp`;
      fs.writeFileSync(
        tmpPath,
        JSON.stringify(this.getAll(), null, 2),
        "utf-8",
      );
      fs.renameSync(tmpPath, this.stateFilePath);
    } catch {
      // best effort persistence — runtime behavior should continue even if
      // the disk snapshot cannot be updated.
    }
  }
}

function normalizeTask(value: unknown): DelegationTask | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const entry = value as Record<string, unknown>;
  if (
    typeof entry.taskId !== "string" ||
    typeof entry.sessionId !== "string" ||
    typeof entry.repoPath !== "string" ||
    typeof entry.receiptPath !== "string" ||
    typeof entry.status !== "string" ||
    typeof entry.ackedAt !== "string"
  ) {
    return null;
  }
  return {
    taskId: entry.taskId,
    sessionId: entry.sessionId,
    repoPath: entry.repoPath,
    receiptPath: entry.receiptPath,
    taskBody:
      typeof entry.taskBody === "string" ? entry.taskBody : null,
    status: entry.status as DelegationTaskStatus,
    ackedAt: entry.ackedAt,
    lastActivityAt:
      typeof entry.lastActivityAt === "string" ? entry.lastActivityAt : entry.ackedAt,
    lastHeartbeatAt:
      typeof entry.lastHeartbeatAt === "string" ? entry.lastHeartbeatAt : null,
    terminalSentAt:
      typeof entry.terminalSentAt === "string" ? entry.terminalSentAt : null,
    terminalStatus:
      typeof entry.terminalStatus === "string" ? entry.terminalStatus : null,
    terminalBody:
      typeof entry.terminalBody === "string" ? entry.terminalBody : null,
    pendingTerminalStatus:
      typeof entry.pendingTerminalStatus === "string" ? entry.pendingTerminalStatus : null,
    pendingTerminalBody:
      typeof entry.pendingTerminalBody === "string" ? entry.pendingTerminalBody : null,
    pendingTerminalPreparedAt:
      typeof entry.pendingTerminalPreparedAt === "string" ? entry.pendingTerminalPreparedAt : null,
    pendingTerminalInflightAt:
      typeof entry.pendingTerminalInflightAt === "string" ? entry.pendingTerminalInflightAt : null,
    pendingTerminalDeliveryId:
      typeof entry.pendingTerminalDeliveryId === "string" ? entry.pendingTerminalDeliveryId : null,
  };
}
