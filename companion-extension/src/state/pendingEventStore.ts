/**
 * Pending event store — events awaiting Supervisor consumption.
 */

export interface PendingEvent {
  source_event_id: string;
  task_id: string;
  attempt_no: number;
  review_round: number;
  session_id: string;
  source_seq: number;
  status: string;
  payload: Record<string, unknown>;
  emitted_at: string;
  delivered_at: string | null;
}

export class PendingEventStore {
  private events: Map<string, PendingEvent[]> = new Map();

  push(task_id: string, event: PendingEvent): void {
    const list = this.events.get(task_id) || [];
    if (list.some((existing) => existing.source_event_id === event.source_event_id)) {
      return;
    }
    list.push(event);
    this.events.set(task_id, list);
  }

  /** Return undelivered events for `task_id`. */
  getPending(task_id: string): PendingEvent[] {
    const list = this.events.get(task_id) || [];
    return list.filter((e) => e.delivered_at === null);
  }

  /** Mark all pending events as delivered. */
  markDelivered(task_id: string): void {
    const list = this.events.get(task_id) || [];
    const now = new Date().toISOString();
    for (const e of list) {
      if (e.delivered_at === null) {
        e.delivered_at = now;
      }
    }
  }

  /**
   * Mark specific events as delivered by source_event_id.
   * Returns the number of events actually marked.
   */
  markDeliveredByIds(task_id: string, source_event_ids: string[]): number {
    const list = this.events.get(task_id) || [];
    const idSet = new Set(source_event_ids);
    const now = new Date().toISOString();
    let count = 0;
    for (const e of list) {
      if (e.delivered_at === null && idSet.has(e.source_event_id)) {
        e.delivered_at = now;
        count++;
      }
    }
    return count;
  }
}
