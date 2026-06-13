from importlib import import_module
import json
from pathlib import Path


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "executor_outputs"


def test_thought_only_stdout_fixture_is_not_completed_report() -> None:
    terminal_arbitration = import_module("agpair.terminal_arbitration")
    text = (FIXTURES / "grok_max_turns_thought_only_stdout.json").read_text(encoding="utf-8")

    assert terminal_arbitration.looks_like_completed_report(text) is False


def test_nonzero_exit_with_real_report_fixture_is_completed_report() -> None:
    terminal_arbitration = import_module("agpair.terminal_arbitration")
    text = (FIXTURES / "report_after_nonzero_exit_stdout.txt").read_text(encoding="utf-8")

    assert terminal_arbitration.looks_like_completed_report(text) is True


def test_claude_code_result_json_is_completed_report() -> None:
    terminal_arbitration = import_module("agpair.terminal_arbitration")
    text = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "结论：Claude Code worker 可用。\n\n- 能启动\n- 能返回中文报告",
        }
    )

    assert terminal_arbitration.looks_like_completed_report(text) is True
    assert terminal_arbitration.completed_report_text(text).startswith("结论：Claude Code worker 可用")
