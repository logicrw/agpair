/**
 * Task → Session in-memory store.
 *
 * Only holds extension-local truth, not global workflow state.
 */

export interface TaskSession {
  task_id: string;
  attempt_no: number;
  review_round: number;
  repo_path: string;
  branch: string | null;
  session_id: string;
  last_step_count: number;
  last_heartbeat_at: string;
  last_monitor_state: string | null;
  last_known_status: string;
}

export class TaskSessionStore {
  private sessions: Map<string, TaskSession> = new Map();

  private key(task_id: string, attempt_no: number): string {
    return `${task_id}::${attempt_no}`;
  }

  bind(task_id: string, attempt_no: number, session: TaskSession): void {
    this.sessions.set(this.key(task_id, attempt_no), session);
  }

  get(task_id: string, attempt_no: number): TaskSession | undefined {
    return this.sessions.get(this.key(task_id, attempt_no));
  }

  remove(task_id: string, attempt_no: number): void {
    this.sessions.delete(this.key(task_id, attempt_no));
  }

  count(): number {
    return this.sessions.size;
  }

  /** Find a session by its SDK session_id (cascadeId). */
  findBySessionId(sessionId: string): TaskSession | undefined {
    for (const session of this.sessions.values()) {
      if (session.session_id === sessionId) {
        return session;
      }
    }
    return undefined;
  }

  /** Get all active sessions. */
  getAll(): TaskSession[] {
    return Array.from(this.sessions.values());
  }
}
