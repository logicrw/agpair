/**
 * Structured output parser for Antigravity agent responses.
 *
 * Parses terminal output text looking for structured headers:
 *   STATUS: EVIDENCE_PACK | BLOCKED | COMMITTED
 *   TASK_ID: ...
 *   ATTEMPT_NO: ...
 *   REVIEW_ROUND: ...
 *   SUMMARY: ...
 *
 * Canonical terminal vocabulary:
 *   COMMITTED     — successful terminal completion
 *   EVIDENCE_PACK — successful completion with evidence payload
 *   BLOCKED       — failure to complete
 *
 * Legacy compatibility: FAILED is accepted but remapped to BLOCKED
 * with a deprecation warning. This ensures historical receipts
 * still parse without leaving ambiguous dual vocabulary in operation.
 *
 * Spec reference: codex_antigravity_companion_extension_ts_spec.md §8
 */

/** Recognised terminal statuses from structured output. */
export type TerminalStatus = "EVIDENCE_PACK" | "BLOCKED" | "COMMITTED";

export interface ParsedOutput {
  status: TerminalStatus;
  task_id: string;
  attempt_no: number;
  review_round: number;
  summary: string;
  /** Full raw text that was parsed. */
  raw_text: string;
}

/**
 * Attempt to parse structured output from raw agent text.
 *
 * Returns `null` if input does not contain a parseable STATUS header,
 * or if required fields (TASK_ID, ATTEMPT_NO, REVIEW_ROUND) are missing.
 *
 * Legacy FAILED status is remapped to BLOCKED for backward compatibility.
 */
export function parseStructuredOutput(raw: string): ParsedOutput | null {
  if (!raw) return null;

  // Accept COMMITTED, EVIDENCE_PACK, BLOCKED as canonical; FAILED as legacy
  const statusMatch = raw.match(/^STATUS:\s*(EVIDENCE_PACK|BLOCKED|COMMITTED|FAILED)\s*$/m);
  if (!statusMatch) return null;

  let status = statusMatch[1] as TerminalStatus | "FAILED";

  // Legacy FAILED → BLOCKED remapping (backward compatibility)
  if (status === "FAILED") {
    console.warn(
      `[parser] Deprecated STATUS: FAILED received — remapping to BLOCKED. ` +
      `Update the agent to use COMMITTED (success) or BLOCKED (failure).`
    );
    status = "BLOCKED";
  }

  const taskIdMatch = raw.match(/^TASK_ID:\s*(.+)\s*$/m);
  if (!taskIdMatch) return null;

  const attemptMatch = raw.match(/^ATTEMPT_NO:\s*(\d+)\s*$/m);
  if (!attemptMatch) return null;

  const reviewMatch = raw.match(/^REVIEW_ROUND:\s*(\d+)\s*$/m);
  if (!reviewMatch) return null;

  const summaryMatch = raw.match(/^SUMMARY:\s*(.+)\s*$/m);
  const summary = summaryMatch ? summaryMatch[1].trim() : "";

  return {
    status: status as TerminalStatus,
    task_id: taskIdMatch[1].trim(),
    attempt_no: parseInt(attemptMatch[1], 10),
    review_round: parseInt(reviewMatch[1], 10),
    summary,
    raw_text: raw,
  };
}

/**
 * Extract the HUMAN_APPROVAL_REQUIRED structured block from raw text.
 *
 * Returns the approval reason or null if not found.
 */
export function parseApprovalRequired(raw: string): { reason: string } | null {
  if (!raw) return null;

  const match = raw.match(/^STATUS:\s*HUMAN_APPROVAL_REQUIRED\s*$/m);
  if (!match) return null;

  const reasonMatch = raw.match(/^REASON:\s*(.+)\s*$/m);
  return {
    reason: reasonMatch ? reasonMatch[1].trim() : "Agent requested human approval",
  };
}
