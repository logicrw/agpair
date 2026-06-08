from __future__ import annotations

import json

from agpair.task_terminal import _report_text_from_receipt_or_output


def test_report_text_requires_explicit_report_or_stdout_body() -> None:
    receipt = {
        "schema_version": "1",
        "task_id": "TASK-REPORT",
        "attempt_no": 1,
        "review_round": 0,
        "status": "EVIDENCE_PACK",
        "summary": "Finished",
        "payload": {},
    }

    assert _report_text_from_receipt_or_output(receipt, None) is None
    assert _report_text_from_receipt_or_output(receipt, json.dumps(receipt)) is None


def test_report_text_uses_payload_report() -> None:
    receipt = {
        "summary": "Finished",
        "payload": {"report": "中文审查结论：可采纳。"},
    }

    report = _report_text_from_receipt_or_output(receipt, None)

    assert report is not None
    assert "Finished" in report
    assert "中文审查结论" in report
    assert "schema_version" not in report


def test_report_text_preserves_stdout_report_body() -> None:
    receipt = {
        "schema_version": "1",
        "task_id": "TASK-REPORT",
        "attempt_no": 1,
        "review_round": 0,
        "status": "EVIDENCE_PACK",
        "summary": "Finished",
        "payload": {},
    }
    stdout = "中文审查结论：整体合理。\n" + json.dumps(receipt, ensure_ascii=False)

    report = _report_text_from_receipt_or_output(receipt, stdout)

    assert report is not None
    assert "Finished" in report
    assert "中文审查结论" in report
