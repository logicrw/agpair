from __future__ import annotations

import dataclasses
import logging
import pathlib
import subprocess
import tempfile
import typing

from agpair.executors.base import DispatchResult, ExecutorAdapter, TaskState
from agpair.models import ContinuationCapability, ExecutorSafetyMetadata

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class GeminiTaskRef:
    task_id: str
    process: subprocess.Popen[str] | None
    stdout_file: pathlib.Path
    stderr_file: pathlib.Path
    rc_file: pathlib.Path
    temp_dir: pathlib.Path


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

    @property
    def safety_metadata(self) -> ExecutorSafetyMetadata:
        return ExecutorSafetyMetadata(
            is_mutating=True,
            is_concurrency_safe=False,
            requires_human_interaction=False,
        )

    def dispatch(self, *, task_id: str, body: str, repo_path: str) -> DispatchResult:
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
        
        repo_path_file = temp_dir / "repo_path.txt"
        repo_path_file.write_text(repo_path, encoding="utf-8")
        start_head_file = temp_dir / "start_head.txt"
        try:
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_path, text=True, stderr=subprocess.DEVNULL).strip()
            start_head_file.write_text(head, encoding="utf-8")
        except Exception:
            pass

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
        wrapper_cmd = ["sh", "-c", f"echo $$ > {shlex.quote(str(pid_file))} ; {cmd_str} < /dev/null ; RC=$? ; echo $RC > {shlex.quote(str(rc_file))} ; exit $RC"]

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

        return DispatchResult(session_id=str(temp_dir))

    def poll(self, task_id: str, session_id: str, attempt_no: int = 1) -> TaskState | None:
        """
        Poll the status of an ongoing Gemini task.
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

        task_ref = GeminiTaskRef(
            task_id=task_id,
            process=None,
            stdout_file=temp_dir / "stdout.log",
            stderr_file=temp_dir / "stderr.log",
            rc_file=temp_dir / "rc.txt",
            temp_dir=temp_dir,
        )

        retcode = None
        is_done = False

        if task_ref.rc_file.exists():
            is_done = True
            try:
                retcode = int(task_ref.rc_file.read_text(encoding="utf-8").strip())
            except ValueError:
                retcode = 1
        else:
            repo_path_file = temp_dir / "repo_path.txt"
            start_head_file = temp_dir / "start_head.txt"
            detected_file = temp_dir / "commit_detected.txt"
            
            if repo_path_file.exists() and start_head_file.exists():
                repo_path_str = repo_path_file.read_text(encoding="utf-8").strip()
                start_head = start_head_file.read_text(encoding="utf-8").strip()
                try:
                    curr_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_path_str, text=True, stderr=subprocess.DEVNULL).strip()
                    if curr_head and curr_head != start_head:
                        import time
                        if not detected_file.exists():
                            detected_file.write_text(str(time.time()), encoding="utf-8")
                        else:
                            try:
                                detected_at = float(detected_file.read_text(encoding="utf-8").strip())
                            except ValueError:
                                detected_at = time.time()
                            if time.time() - detected_at > 30:
                                logger.info(f"Gemini hang timeout reached for {task_id}, force killing.")
                                self.cancel(task_id, session_id)
                                task_ref.rc_file.write_text("0", encoding="utf-8")
                                is_done = True
                                retcode = 0
                except Exception as e:
                    logger.debug("Failed to check git head for gemini watchdog: %s", e)

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

        receipt = None
        if is_done:
            if retcode == 0:
                status = "COMMITTED"
                summary = "Task finished successfully via Gemini"
                payload = {
                    "events_count": events_count,
                    "returncode": retcode,
                }
            else:
                status = "BLOCKED"
                summary = f"Gemini executor failed with return code {retcode}"
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
        Cancel an ongoing Gemini task, best-effort.
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
        if not session_id:
            return
        temp_dir = pathlib.Path(session_id)
        if temp_dir.exists() and temp_dir.name.startswith("agpair_gemini_"):
            shutil.rmtree(temp_dir, ignore_errors=True)
