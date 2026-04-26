from __future__ import annotations

from pathlib import Path
import json
import os
import stat
import textwrap


def write_fake_agent_bus(tmp_path: Path) -> tuple[str, Path, Path]:
    script_path = tmp_path / "agent-bus"
    calls_path = tmp_path / "calls.jsonl"
    pull_path = tmp_path / "pull.json"
    script_path.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import os
            import sys
            from pathlib import Path

            calls_path = Path(os.environ["FAKE_AGENT_BUS_CALLS"])
            pull_path = Path(os.environ["FAKE_AGENT_BUS_PULL"])

            argv = sys.argv[1:]
            command = argv[0] if argv else ""

            if command == "send":
                body = ""
                if "--body-file" in argv:
                    body = Path(argv[argv.index("--body-file") + 1]).read_text(encoding="utf-8")
                with calls_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"argv": [Path(sys.argv[0]).name, *argv], "body": body}) + "\\n")
                print(json.dumps({"ok": True, "id": 101}))
            elif command in {"pull", "reserve"}:
                with calls_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"argv": [Path(sys.argv[0]).name, *argv], "body": ""}) + "\\n")
                if pull_path.exists():
                    print(pull_path.read_text(encoding="utf-8"))
                else:
                    key = "reserved" if command == "reserve" else "claimed"
                    print(json.dumps({"ok": True, "reader": "desktop", key: 0, "messages": []}))
            elif command == "settle":
                with calls_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"argv": [Path(sys.argv[0]).name, *argv], "body": ""}) + "\\n")
                print(json.dumps({"ok": True, "reader": "desktop", "settled": 1}))
            else:
                print(json.dumps({"ok": False, "error": f"unsupported command: {command}"}))
                raise SystemExit(2)
            """
        ),
        encoding="utf-8",
    )
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
    return str(script_path), calls_path, pull_path


def read_calls(calls_path: Path) -> list[dict]:
    if not calls_path.exists():
        return []
    return [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines() if line.strip()]
