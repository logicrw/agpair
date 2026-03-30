/**
 * Structured terminal receipt definitions.
 * Spec reference for v1 envelope: codex_antigravity_companion_extension_ts_spec.md
 */

export type TerminalReceiptStatus = "EVIDENCE_PACK" | "BLOCKED" | "COMMITTED";

export interface TerminalReceiptV1 {
  schema_version: "1";
  task_id: string;
  attempt_no: number;
  review_round: number;
  status: TerminalReceiptStatus;
  summary: string;
  payload: Record<string, unknown>;
}

export interface DelegationReceipt {
  task_id: string;
  status: TerminalReceiptStatus;
  body: string;
}

export function isTerminalReceiptStatus(value: unknown): value is TerminalReceiptStatus {
  return value === "EVIDENCE_PACK" || value === "BLOCKED" || value === "COMMITTED";
}

export function canonicalizeReceiptV1(receipt: TerminalReceiptV1): string {
  return JSON.stringify({
    schema_version: "1",
    task_id: receipt.task_id,
    attempt_no: receipt.attempt_no,
    review_round: receipt.review_round,
    status: receipt.status,
    summary: receipt.summary,
    payload: receipt.payload,
  });
}

export function parseDelegationReceipt(raw: string, expectedTaskId: string): DelegationReceipt | null {
  try {
    const parsed = JSON.parse(raw);
    const taskId = parsed?.task_id;
    const status = parsed?.status;

    if (typeof taskId !== "string" || taskId !== expectedTaskId) {
      return null;
    }
    if (!isTerminalReceiptStatus(status)) {
      return null;
    }

    if (parsed?.schema_version === "1") {
      if (
        typeof parsed.attempt_no !== "number" ||
        typeof parsed.review_round !== "number" ||
        typeof parsed.summary !== "string" ||
        typeof parsed.payload !== "object" ||
        parsed.payload === null
      ) {
        return null;
      }

      return {
        task_id: taskId,
        status,
        body: canonicalizeReceiptV1({
          schema_version: "1",
          task_id: taskId,
          attempt_no: parsed.attempt_no,
          review_round: parsed.review_round,
          status,
          summary: parsed.summary,
          payload: parsed.payload as Record<string, unknown>,
        }),
      };
    }

    const body = typeof parsed?.body === "string" ? parsed.body : "";
    return { task_id: taskId, status, body };
  } catch {
    return null;
  }
}
