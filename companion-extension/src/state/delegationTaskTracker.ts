/**
 * Delegation Task Tracker — in-memory state for delegation tasks.
 *
 * Tracks delegation tasks from TASK → ACK → terminal (EVIDENCE_PACK / BLOCKED / COMMITTED).
 * Provides deduplication guard so the extension never sends duplicate terminal
 * status messages for the same delegated task.
 *
 * This is separate from the bridge-first TaskSessionStore because delegation
 * tasks use agent-bus transport, not bridge /run_task.
 */

import * as fs from "fs";
import * as path from "path";

export type DelegationTaskStatus =
  | "ACKED"
  | "RUNNING"
  | "EVIDENCE_PACK"
  | "BLOCKED"
  | "COMMITTED"
  | "FAILED";

export interface DelegationTask {
  taskId: string;
  sessionId: string;
  repoPath: string;
  receiptPath: string;
  status: DelegationTaskStatus;
  ackedAt: string;
  lastActivityAt?: string | null;
  /** Timestamp of the most recent RUNNING heartbeat sent for this task. */
  lastHeartbeatAt?: string | null;
  terminalSentAt: string | null;
  terminalStatus: string | null;
  terminalBody: string | null;
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
      lastActivityAt: task.lastActivityAt ?? task.ackedAt,
      lastHeartbeatAt: task.lastHeartbeatAt ?? null,
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
   * Check if terminal has already been sent for a task (dedup query).
   */
  isTerminalSent(taskId: string): boolean {
    const task = this.tasks.get(taskId);
    return task?.terminalSentAt != null;
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
    tasks: Array<{
      taskId: string;
      status: DelegationTaskStatus;
      sessionId: string;
      ackedAt: string;
      lastActivityAt: string;
      lastHeartbeatAt: string | null;
      terminalSentAt: string | null;
    }>;
  } {
    const all = this.getAll();
    const pending = all.filter((t) => t.terminalSentAt === null);
    return {
      total: all.length,
      pending: pending.length,
      completed: all.length - pending.length,
      tasks: all.map((t) => ({
        taskId: t.taskId,
        status: t.status,
        sessionId: t.sessionId,
        ackedAt: t.ackedAt,
        lastActivityAt: t.lastActivityAt ?? t.ackedAt,
        lastHeartbeatAt: t.lastHeartbeatAt ?? null,
        terminalSentAt: t.terminalSentAt,
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
  };
}
