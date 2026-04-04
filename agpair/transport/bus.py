from __future__ import annotations

from pathlib import Path
import json
import subprocess
import tempfile

from agpair.transport import messages


class BusPullError(RuntimeError):
    """Raised when an ``agent-bus pull`` invocation fails transiently.

    Wraps subprocess failures and JSON-decode errors so callers only need
    to catch a single, domain-specific exception type.
    """


class AgentBusClient:
    def __init__(self, executable: str = "agent-bus") -> None:
        self.executable = executable

    def send_task(self, *, task_id: str, body: str, repo_path: str) -> int:
        full_body = f"repo_path: {repo_path}\n\n{body}"
        return self._send(task_id=task_id, status=messages.TASK, body=full_body)

    def send_review(self, *, task_id: str, body: str) -> int:
        return self._send(task_id=task_id, status=messages.REVIEW, body=body)

    def send_approved(self, *, task_id: str, body: str) -> int:
        return self._send(task_id=task_id, status=messages.APPROVED, body=body)

    def pull_receipts(self, *, task_id: str | None = None, limit: int = 20) -> list[dict]:
        argv = [
            self.executable,
            "pull",
            "--sender",
            messages.CODE_SENDER,
            "--reader",
            messages.DESKTOP_READER,
            "--limit",
            str(limit),
            "--full",
        ]
        if task_id:
            argv.extend(["--task-id", task_id])
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, check=True)
            payload = json.loads(proc.stdout or "{}")
        except subprocess.CalledProcessError as exc:
            raise BusPullError(
                f"agent-bus pull failed (rc={exc.returncode}): {exc.stderr or exc.stdout}"
            ) from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise BusPullError(f"agent-bus pull returned invalid JSON: {exc}") from exc
        return list(payload.get("messages", []))

    def _send(self, *, task_id: str, status: str, body: str) -> int:
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tmp:
                tmp.write(body)
                tmp_path = Path(tmp.name)
            proc = subprocess.run(
                [
                    self.executable,
                    "send",
                    "--sender",
                    messages.DESKTOP_SENDER,
                    "--task-id",
                    task_id,
                    "--status",
                    status,
                    "--body-file",
                    str(tmp_path),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
        finally:
            if tmp_path:
                tmp_path.unlink(missing_ok=True)
        payload = json.loads(proc.stdout or "{}")
        return int(payload.get("id", 0))
