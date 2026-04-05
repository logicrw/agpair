import { describe, it } from "node:test";
import * as assert from "node:assert/strict";

import {
  canonicalizeReceiptV1,
  isTerminalReceiptStatus,
  parseDelegationReceipt,
} from "../protocols/receipt";

describe("receipt protocol helpers", () => {
  it("recognizes canonical terminal statuses only", () => {
    assert.equal(isTerminalReceiptStatus("EVIDENCE_PACK"), true);
    assert.equal(isTerminalReceiptStatus("BLOCKED"), true);
    assert.equal(isTerminalReceiptStatus("COMMITTED"), true);
    assert.equal(isTerminalReceiptStatus("FAILED"), false);
    assert.equal(isTerminalReceiptStatus("RUNNING"), false);
    assert.equal(isTerminalReceiptStatus(null), false);
  });

  it("canonicalizeReceiptV1 produces deterministic JSON", () => {
    const json = canonicalizeReceiptV1({
      schema_version: "1",
      task_id: "TASK-1",
      attempt_no: 2,
      review_round: 3,
      status: "BLOCKED",
      summary: "Need a credential",
      payload: { blocker_type: "auth", recoverable: true },
    });

    assert.equal(
      json,
      '{"schema_version":"1","task_id":"TASK-1","attempt_no":2,"review_round":3,"status":"BLOCKED","summary":"Need a credential","payload":{"blocker_type":"auth","recoverable":true}}',
    );
  });

  it("parses legacy receipt and preserves body text", () => {
    const parsed = parseDelegationReceipt(
      JSON.stringify({
        task_id: "TASK-LEGACY",
        status: "EVIDENCE_PACK",
        body: "plain text body",
      }),
      "TASK-LEGACY",
    );

    assert.ok(parsed);
    assert.equal(parsed.status, "EVIDENCE_PACK");
    assert.equal(parsed.body, "plain text body");
  });

  it("parses v1 receipt and canonicalizes transport body", () => {
    const parsed = parseDelegationReceipt(
      JSON.stringify({
        schema_version: "1",
        task_id: "TASK-V1",
        attempt_no: 1,
        review_round: 0,
        status: "COMMITTED",
        summary: "Committed cleanly",
        payload: { commit_sha: "abc1234", branch: "main" },
      }),
      "TASK-V1",
    );

    assert.ok(parsed);
    assert.equal(parsed.status, "COMMITTED");
    assert.deepEqual(JSON.parse(parsed.body), {
      schema_version: "1",
      task_id: "TASK-V1",
      attempt_no: 1,
      review_round: 0,
      status: "COMMITTED",
      summary: "Committed cleanly",
      payload: { commit_sha: "abc1234", branch: "main" },
    });
  });

  it("rejects malformed v1 receipt", () => {
    const parsed = parseDelegationReceipt(
      JSON.stringify({
        schema_version: "1",
        task_id: "TASK-BAD",
        status: "BLOCKED",
        summary: "bad",
      }),
      "TASK-BAD",
    );

    assert.equal(parsed, null);
  });

  it("rejects wrong task id and invalid status", () => {
    assert.equal(
      parseDelegationReceipt(
        JSON.stringify({ task_id: "OTHER", status: "BLOCKED", body: "x" }),
        "TASK-1",
      ),
      null,
    );
    assert.equal(
      parseDelegationReceipt(
        JSON.stringify({ task_id: "TASK-1", status: "RUNNING", body: "x" }),
        "TASK-1",
      ),
      null,
    );
  });

  it("remaps legacy FAILED to BLOCKED", () => {
    const parsed = parseDelegationReceipt(
      JSON.stringify({
        task_id: "TASK-1",
        status: "FAILED",
        body: "Legacy fail",
      }),
      "TASK-1",
    );
    assert.ok(parsed);
    assert.equal(parsed.status, "BLOCKED");
    assert.equal(parsed.body, "Legacy fail");
  });
});
