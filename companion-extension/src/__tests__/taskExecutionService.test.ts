import { describe, it } from "node:test";
import * as assert from "node:assert/strict";

import { TaskExecutionService } from "../services/taskExecutionService";
import { TaskSessionStore } from "../state/taskSessionStore";
import { PendingEventStore } from "../state/pendingEventStore";

describe("TaskExecutionService automation fallback policy", () => {
  it("runTask allows the stable interactive session creation fallback path", async () => {
    const sessionStore = new TaskSessionStore();
    const eventStore = new PendingEventStore();
    let capturedOptions: unknown = null;
    let capturedPrompt = "";

    const service = new TaskExecutionService(
      {
        async createBackgroundSession(prompt: string, options?: unknown) {
          capturedPrompt = prompt;
          capturedOptions = options ?? null;
          return { ok: true, session_id: "sess-run-1" };
        },
      } as any,
      sessionStore,
      eventStore,
    );

    const result = await service.runTask({
      task_id: "TASK-RUN-STRICT",
      attempt_no: 1,
      review_round: 0,
      repo_path: "/tmp/agpair-run-strict",
      prompt: "Implement the change.",
    });

    assert.equal(result.ok, true);
    assert.deepEqual(capturedOptions, {
      allowInteractiveFallback: true,
      contextLabel: "task TASK-RUN-STRICT",
    });
    assert.match(capturedPrompt, /"schema_version": "1"/);
    assert.match(capturedPrompt, /"changed_files": \["\.\.\."\]/);
    assert.match(capturedPrompt, /"validation": \["\.\.\."\]/);
    assert.match(capturedPrompt, /"residual_risks": \["\.\.\."\]/);
  });

  it("continueTask forbids prompt-panel fallback", async () => {
    const sessionStore = new TaskSessionStore();
    const eventStore = new PendingEventStore();
    let capturedOptions: unknown = null;

    sessionStore.bind("TASK-CONT-STRICT", 2, {
      task_id: "TASK-CONT-STRICT",
      attempt_no: 2,
      review_round: 0,
      repo_path: "/tmp/agpair-cont-strict",
      branch: null,
      session_id: "sess-cont-1",
      last_step_count: 3,
      last_heartbeat_at: "2026-03-30T00:00:00Z",
      last_monitor_state: null,
      last_known_status: "ACK",
    });

    const service = new TaskExecutionService(
      {
        async sendPrompt(_sessionId: string, _prompt: string, options?: unknown) {
          capturedOptions = options ?? null;
          return { ok: true };
        },
      } as any,
      sessionStore,
      eventStore,
    );

    const result = await service.continueTask({
      task_id: "TASK-CONT-STRICT",
      attempt_no: 2,
      review_round: 1,
      prompt: "Address review feedback.",
    });

    assert.equal(result.ok, true);
    assert.deepEqual(capturedOptions, {
      allowPanelFallback: false,
      contextLabel: "task TASK-CONT-STRICT",
    });
  });
});
