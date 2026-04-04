from __future__ import annotations

import dataclasses
import logging
import pathlib
import subprocess
import tempfile
import typing

from agpair.executors.base import ExecutorAdapter
from agpair.models import ContinuationCapability

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class GeminiTaskRef:
    task_id: str
    process: subprocess.Popen[str] | None
    stdout_file: pathlib.Path
    stderr_file: pathlib.Path
    rc_file: pathlib.Path
    temp_dir: pathlib.Path


@dataclasses.dataclass
class GeminiTaskState:
    is_done: bool
    returncode: int | None
    events_count: int

    def synthesize_receipt(self, task_id: str, *, attempt_no: int = 1) -> dict[str, typing.Any]:
        """Synthesize a terminal receipt dict for this task state."""
        if not self.is_done:
            return {}

        if self.returncode == 0:
            status = "EVIDENCE_PACK"
            summary = "Task finished successfully via Gemini"
            payload = {
                "events_count": self.events_count,
                "returncode": self.returncode,
            }
        else:
            status = "BLOCKED"
            summary = f"Gemini executor failed with return code {self.returncode}"
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
            "attempt_no": attempt_no,
            "review_round": 0,
            "status": status,
            "summary": summary,
            "payload": payload,
        }


class GeminiExecutor(ExecutorAdapter):
    """
    Executor adapter that runs tasks using the local Gemini CLI.
    This is groundwork; not yet wired as the primary executor.
    """

    def __init__(self, gemini_bin: str = "gemini") -> None:
        self.gemini_bin = gemini_bin

    @property
    def backend_id(self) -> str:
        return "gemini_cli"

    @property
    def continuation_capability(self) -> ContinuationCapability:
        # Set explicitly to UNSUPPORTED for now until continuation semantics
        # are fully wired for Gemini.
        return ContinuationCapability.UNSUPPORTED

    def dispatch(self, *, task_id: str, body: str, repo_path: str) -> GeminiTaskRef:
        """
        Dispatch a task using gemini CLI.
        Returns a GeminiTaskRef tracking reference.
        """
        import shlex
        
        temp_dir = pathlib.Path(tempfile.mkdtemp(prefix=f"agpair_gemini_{task_id}_"))
        
        stdout_file = temp_dir / "stdout.log"
        stderr_file = temp_dir / "stderr.log"
        rc_file = temp_dir / "rc.txt"
        pid_file = temp_dir / "pid.txt"
        
        # -y / --yolo : auto approve actions
        # --output-format json : to parse events potentially
        # -p : prompt (headless mode)
        cmd = [
            self.gemini_bin,
            "-y",
            "--output-format", "json",
            "-p", str(body)
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

        # Close parent's copies
        stdout_fh.close()
        stderr_fh.close()

        return GeminiTaskRef(
            task_id=task_id,
            process=process,
            stdout_file=stdout_file,
            stderr_file=stderr_file,
            rc_file=rc_file,
            temp_dir=temp_dir,
        )

    def poll(self, task_ref: typing.Any) -> GeminiTaskState:
        """
        Poll the status of an ongoing Gemini task.
        """
        if not isinstance(task_ref, GeminiTaskRef):
            raise TypeError(f"Expected GeminiTaskRef, got {type(task_ref)}")

        retcode = None
        is_done = False

        if task_ref.process is not None:
            retcode = task_ref.process.poll()
            is_done = retcode is not None
        else:
            if task_ref.rc_file.exists():
                is_done = True
                try:
                    retcode = int(task_ref.rc_file.read_text(encoding="utf-8").strip())
                except ValueError:
                    retcode = 1

        if is_done and retcode == 0 and task_ref.rc_file.exists():
            try:
                retcode = int(task_ref.rc_file.read_text(encoding="utf-8").strip())
            except ValueError:
                pass

        events_count = 0
        if is_done and task_ref.stdout_file.exists():
            try:
                with task_ref.stdout_file.open("r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            events_count += 1
            except Exception:
                pass

        return GeminiTaskState(
            is_done=is_done,
            returncode=retcode,
            events_count=events_count,
        )

    def cancel(self, task_ref: typing.Any) -> None:
        """
        Cancel an ongoing Gemini task, best-effort.
        """
        import os
        import signal

        if not isinstance(task_ref, GeminiTaskRef):
            raise TypeError(f"Expected GeminiTaskRef, got {type(task_ref)}")

        if task_ref.process is not None and task_ref.process.poll() is None:
            task_ref.process.terminate()
            try:
                task_ref.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                task_ref.process.kill()
        else:
            pid_file = task_ref.temp_dir / "pid.txt"
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
        if not session_id:
            return
        temp_dir = pathlib.Path(session_id)
        if temp_dir.exists() and temp_dir.name.startswith("agpair_gemini_"):
            shutil.rmtree(temp_dir, ignore_errors=True)
