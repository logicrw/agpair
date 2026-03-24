/**
 * Tests for the structured output parser.
 *
 * Covers:
 *   - parseStructuredOutput with valid EVIDENCE_PACK
 *   - parseStructuredOutput with valid BLOCKED
 *   - parseStructuredOutput with valid FAILED
 *   - parseStructuredOutput returns null on missing STATUS
 *   - parseStructuredOutput returns null on missing TASK_ID
 *   - parseStructuredOutput returns null on missing ATTEMPT_NO
 *   - parseStructuredOutput returns null on missing REVIEW_ROUND
 *   - parseStructuredOutput returns null on empty input
 *   - parseApprovalRequired with valid input
 *   - parseApprovalRequired returns null on missing STATUS
 */

import { describe, it } from "node:test";
import * as assert from "node:assert/strict";
import { parseStructuredOutput, parseApprovalRequired } from "../protocols/parser";

describe("parseStructuredOutput", () => {
  it("parses EVIDENCE_PACK", () => {
    const raw = [
      "STATUS: EVIDENCE_PACK",
      "TASK_ID: task-001",
      "ATTEMPT_NO: 1",
      "REVIEW_ROUND: 0",
      "SUMMARY: All tests pass",
    ].join("\n");

    const result = parseStructuredOutput(raw);
    assert.ok(result);
    assert.equal(result.status, "EVIDENCE_PACK");
    assert.equal(result.task_id, "task-001");
    assert.equal(result.attempt_no, 1);
    assert.equal(result.review_round, 0);
    assert.equal(result.summary, "All tests pass");
    assert.equal(result.raw_text, raw);
  });

  it("parses BLOCKED", () => {
    const raw = [
      "STATUS: BLOCKED",
      "TASK_ID: task-002",
      "ATTEMPT_NO: 2",
      "REVIEW_ROUND: 1",
      "SUMMARY: Missing API key",
    ].join("\n");

    const result = parseStructuredOutput(raw);
    assert.ok(result);
    assert.equal(result.status, "BLOCKED");
    assert.equal(result.task_id, "task-002");
  });

  it("parses FAILED", () => {
    const raw = [
      "STATUS: FAILED",
      "TASK_ID: task-003",
      "ATTEMPT_NO: 1",
      "REVIEW_ROUND: 0",
      "SUMMARY: Compilation error",
    ].join("\n");

    const result = parseStructuredOutput(raw);
    assert.ok(result);
    assert.equal(result.status, "FAILED");
  });

  it("returns null on missing STATUS", () => {
    const raw = [
      "TASK_ID: task-001",
      "ATTEMPT_NO: 1",
      "REVIEW_ROUND: 0",
    ].join("\n");

    assert.equal(parseStructuredOutput(raw), null);
  });

  it("returns null on unrecognized STATUS", () => {
    const raw = [
      "STATUS: RUNNING",
      "TASK_ID: task-001",
      "ATTEMPT_NO: 1",
      "REVIEW_ROUND: 0",
    ].join("\n");

    assert.equal(parseStructuredOutput(raw), null);
  });

  it("returns null on missing TASK_ID", () => {
    const raw = [
      "STATUS: EVIDENCE_PACK",
      "ATTEMPT_NO: 1",
      "REVIEW_ROUND: 0",
    ].join("\n");

    assert.equal(parseStructuredOutput(raw), null);
  });

  it("returns null on missing ATTEMPT_NO", () => {
    const raw = [
      "STATUS: EVIDENCE_PACK",
      "TASK_ID: task-001",
      "REVIEW_ROUND: 0",
    ].join("\n");

    assert.equal(parseStructuredOutput(raw), null);
  });

  it("returns null on missing REVIEW_ROUND", () => {
    const raw = [
      "STATUS: EVIDENCE_PACK",
      "TASK_ID: task-001",
      "ATTEMPT_NO: 1",
    ].join("\n");

    assert.equal(parseStructuredOutput(raw), null);
  });

  it("returns null on empty input", () => {
    assert.equal(parseStructuredOutput(""), null);
  });

  it("returns null on null-ish input", () => {
    assert.equal(parseStructuredOutput(null as any), null);
  });

  it("parses embedded in larger text", () => {
    const raw = [
      "Here is the result of the task:",
      "",
      "STATUS: EVIDENCE_PACK",
      "TASK_ID: task-embedded",
      "ATTEMPT_NO: 1",
      "REVIEW_ROUND: 0",
      "SUMMARY: Done",
      "",
      "Some trailing text",
    ].join("\n");

    const result = parseStructuredOutput(raw);
    assert.ok(result);
    assert.equal(result.status, "EVIDENCE_PACK");
    assert.equal(result.task_id, "task-embedded");
  });

  it("handles missing SUMMARY gracefully", () => {
    const raw = [
      "STATUS: EVIDENCE_PACK",
      "TASK_ID: task-nosummary",
      "ATTEMPT_NO: 1",
      "REVIEW_ROUND: 0",
    ].join("\n");

    const result = parseStructuredOutput(raw);
    assert.ok(result);
    assert.equal(result.summary, "");
  });
});

describe("parseApprovalRequired", () => {
  it("parses HUMAN_APPROVAL_REQUIRED with reason", () => {
    const raw = [
      "STATUS: HUMAN_APPROVAL_REQUIRED",
      "REASON: Destructive operation detected",
    ].join("\n");

    const result = parseApprovalRequired(raw);
    assert.ok(result);
    assert.equal(result.reason, "Destructive operation detected");
  });

  it("returns default reason when REASON is missing", () => {
    const raw = "STATUS: HUMAN_APPROVAL_REQUIRED";

    const result = parseApprovalRequired(raw);
    assert.ok(result);
    assert.equal(result.reason, "Agent requested human approval");
  });

  it("returns null when STATUS is not HUMAN_APPROVAL_REQUIRED", () => {
    assert.equal(parseApprovalRequired("STATUS: EVIDENCE_PACK"), null);
    assert.equal(parseApprovalRequired(""), null);
    assert.equal(parseApprovalRequired(null as any), null);
  });
});
