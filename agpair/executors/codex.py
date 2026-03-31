from __future__ import annotations

import dataclasses
import json
import logging
import pathlib
import subprocess
import tempfile
import typing

from agpair.executors.base import ExecutorAdapter
from agpair.models import ContinuationCapability

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class CodexTaskRef:
    task_id: str
    process: subprocess.Popen[str]
    stdout_file: pathlib.Path
    stderr_file: pathlib.Path
    last_msg_file: pathlib.Path
    temp_dir: pathlib.Path


@dataclasses.dataclass
class CodexTaskState:
    is_done: bool
    returncode: int | None
    last_message: str | None
    events: list[dict[str, typing.Any]]

    def synthesize_receipt(self, task_id: str) -> dict[str, typing.Any]:
        """Synthesize a terminal receipt dict for this task state."""
        if not self.is_done:
            return {}

        if self.returncode == 0:
            status = "EVIDENCE_PACK"
            summary = self.last_message or "Task finished successfully"
            payload = {
                "events_count": len(self.events),
                "returncode": self.returncode,
            }
        else:
            status = "BLOCKED"
            summary = self.last_message or "Task failed"
            payload = {
                "blocker_type": "execution_error",
                "message": summary,
                "recoverable": False,
                "suggested_action": "Inspect stderr logs",
                "last_error_excerpt": summary[:200] if summary else "",
                "returncode": self.returncode,
            }

        return {
            "schema_version": "1",
            "task_id": task_id,
            "attempt_no": 1,
            "review_round": 0,
            "status": status,
            "summary": summary,
            "payload": payload,
        }


class CodexExecutor(ExecutorAdapter):
    """
    Executor adapter that runs tasks using the local Codex CLI.
    """

    def __init__(self, codex_bin: str = "codex") -> None:
        self.codex_bin = codex_bin

    @property
    def backend_id(self) -> str:
        return "codex_cli"

    @property
    def continuation_capability(self) -> ContinuationCapability:
        return ContinuationCapability.FRESH_RESUME_FIRST

    def dispatch(self, *, task_id: str, body: str, repo_path: str) -> CodexTaskRef:
        """
        Dispatch a task using codex exec.
        Returns a CodexTaskRef tracking reference.
        """
        temp_dir = pathlib.Path(tempfile.mkdtemp(prefix=f"agpair_codex_{task_id}_"))
        
        stdout_file = temp_dir / "stdout.jsonl"
        stderr_file = temp_dir / "stderr.log"
        last_msg_file = temp_dir / "last_msg.txt"
        
        cmd = [
            self.codex_bin,
            "exec",
            "--ephemeral",
            "--json",
            "--skip-git-repo-check",
            "-C", str(repo_path),
            "-o", str(last_msg_file),
            str(body)
        ]

        stdout_fh = stdout_file.open("w", encoding="utf-8")
        stderr_fh = stderr_file.open("w", encoding="utf-8")

        process = subprocess.Popen(
            cmd,
            stdout=stdout_fh,
            stderr=stderr_fh,
            cwd=repo_path,
            text=True,
        )

        return CodexTaskRef(
            task_id=task_id,
            process=process,
            stdout_file=stdout_file,
            stderr_file=stderr_file,
            last_msg_file=last_msg_file,
            temp_dir=temp_dir,
        )

    def poll(self, task_ref: typing.Any) -> CodexTaskState:
        """
        Poll the status of an ongoing Codex task.
        """
        if not isinstance(task_ref, CodexTaskRef):
            raise TypeError(f"Expected CodexTaskRef, got {type(task_ref)}")

        retcode = task_ref.process.poll()
        is_done = retcode is not None

        events = []
        if task_ref.stdout_file.exists():
            try:
                with task_ref.stdout_file.open("r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            try:
                                events.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
            except Exception:
                pass

        last_message = None
        if is_done and task_ref.last_msg_file.exists():
            try:
                last_message = task_ref.last_msg_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass

        return CodexTaskState(
            is_done=is_done,
            returncode=retcode,
            last_message=last_message,
            events=events,
        )

    def cancel(self, task_ref: typing.Any) -> None:
        """
        Cancel an ongoing Codex task, best-effort.
        """
        if not isinstance(task_ref, CodexTaskRef):
            raise TypeError(f"Expected CodexTaskRef, got {type(task_ref)}")

        if task_ref.process.poll() is None:
            task_ref.process.terminate()
            try:
                task_ref.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                task_ref.process.kill()
