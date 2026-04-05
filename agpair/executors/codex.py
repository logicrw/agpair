from __future__ import annotations

import dataclasses
import json
import logging
import pathlib
import subprocess
import tempfile
import typing

from agpair.executors.base import DispatchResult, ExecutorAdapter, TaskState
from agpair.models import ContinuationCapability, ExecutorSafetyMetadata

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class CodexTaskRef:
    task_id: str
    process: subprocess.Popen[str] | None
    stdout_file: pathlib.Path
    stderr_file: pathlib.Path
    last_msg_file: pathlib.Path
    temp_dir: pathlib.Path


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

    @property
    def safety_metadata(self) -> ExecutorSafetyMetadata:
        return ExecutorSafetyMetadata(
            is_mutating=True,
            is_concurrency_safe=False,
            requires_human_interaction=False,
        )

    def dispatch(self, *, task_id: str, body: str, repo_path: str) -> DispatchResult:
        """
        Dispatch a task using codex exec.
        Returns a CodexTaskRef tracking reference.
        """
        import shlex
        
        temp_dir = pathlib.Path(tempfile.mkdtemp(prefix=f"agpair_codex_{task_id}_"))
        
        stdout_file = temp_dir / "stdout.jsonl"
        stderr_file = temp_dir / "stderr.log"
        last_msg_file = temp_dir / "last_msg.txt"
        rc_file = temp_dir / "rc.txt"
        pid_file = temp_dir / "pid.txt"
        
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

        cmd_str = " ".join(shlex.quote(str(x)) for x in cmd)
        wrapper_cmd = ["sh", "-c", f"echo $$ > {shlex.quote(str(pid_file))} ; {cmd_str} ; RC=$? ; echo $RC > {shlex.quote(str(rc_file))} ; exit $RC"]

        stdout_fh = stdout_file.open("w", encoding="utf-8")
        stderr_fh = stderr_file.open("w", encoding="utf-8")

        try:
            process = subprocess.Popen(
                wrapper_cmd,
                stdout=stdout_fh,
                stderr=stderr_fh,
                cwd=repo_path,
                text=True,
                start_new_session=True,
            )
        except Exception:
            stdout_fh.close()
            stderr_fh.close()
            raise

        # Close parent's copies – the child has inherited them.
        stdout_fh.close()
        stderr_fh.close()

        return DispatchResult(session_id=str(temp_dir))

    def poll(self, task_id: str, session_id: str, attempt_no: int = 1) -> TaskState | None:
        """
        Poll the status of an ongoing Codex task.
        """
        temp_dir = pathlib.Path(session_id)
        if not temp_dir.exists():
            return TaskState(
                is_done=True,
                receipt={
                    "schema_version": "1",
                    "task_id": task_id,
                    "attempt_no": attempt_no,
                    "review_round": 0,
                    "status": "BLOCKED",
                    "summary": "Executor temp directory missing, task is lost.",
                    "payload": {
                        "blocker_type": "execution_error",
                        "message": "Executor temp directory missing, task is lost.",
                        "recoverable": False,
                        "suggested_action": "Retry",
                        "last_error_excerpt": "",
                    }
                }
            )

        task_ref = CodexTaskRef(
            task_id=task_id,
            process=None,
            stdout_file=temp_dir / "stdout.jsonl",
            stderr_file=temp_dir / "stderr.log",
            last_msg_file=temp_dir / "last_msg.txt",
            temp_dir=temp_dir,
        )

        rc_file = task_ref.temp_dir / "rc.txt"
        retcode = None
        is_done = False

        if rc_file.exists():
            is_done = True
            try:
                retcode = int(rc_file.read_text(encoding="utf-8").strip())
            except ValueError:
                retcode = 1

        events_count = 0
        if is_done and task_ref.stdout_file.exists():
            try:
                with task_ref.stdout_file.open("r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            events_count += 1
            except Exception:
                pass

        last_message = None
        if is_done and task_ref.last_msg_file.exists():
            try:
                last_message = task_ref.last_msg_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass

        receipt = None
        if is_done:
            if retcode == 0:
                status = "COMMITTED"
                summary = last_message or "Task finished successfully"
                payload = {
                    "events_count": events_count,
                    "returncode": retcode,
                }
            else:
                status = "BLOCKED"
                summary = last_message or "Task failed"
                payload = {
                    "blocker_type": "execution_error",
                    "message": summary,
                    "recoverable": False,
                    "suggested_action": "Inspect stderr logs",
                    "last_error_excerpt": summary[:200] if summary else "",
                    "returncode": retcode,
                }
            receipt = {
                "schema_version": "1",
                "task_id": task_id,
                "attempt_no": attempt_no,
                "review_round": 0,
                "status": status,
                "summary": summary,
                "payload": payload,
            }

        return TaskState(is_done=is_done, receipt=receipt)

    def cancel(self, task_id: str, session_id: str) -> None:
        """
        Cancel an ongoing Codex task, best-effort.
        """
        import os
        import signal

        temp_dir = pathlib.Path(session_id)
        if not temp_dir.exists():
            return
            
        pid_file = temp_dir / "pid.txt"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
                os.kill(-pid, signal.SIGTERM)
            except Exception:
                pass

    def cleanup(self, session_id: str) -> None:
        """
        Clean up the task's temporary directory.
        """
        import shutil
        import pathlib
        if not session_id:
            return
        temp_dir = pathlib.Path(session_id)
        if temp_dir.exists() and temp_dir.name.startswith("agpair_codex_"):
            shutil.rmtree(temp_dir, ignore_errors=True)
