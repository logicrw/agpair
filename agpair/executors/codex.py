from __future__ import annotations

import dataclasses
import json
import logging
import pathlib
import subprocess
import tempfile
import time
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

        repo_path_file = temp_dir / "repo_path.txt"
        repo_path_file.write_text(repo_path, encoding="utf-8")
        start_head_file = temp_dir / "start_head.txt"
        try:
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_path, text=True, stderr=subprocess.DEVNULL).strip()
            start_head_file.write_text(head, encoding="utf-8")
        except Exception:
            pass

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
                        if not detected_file.exists():
                            detected_file.write_text(str(time.time()), encoding="utf-8")
                        else:
                            try:
                                detected_at = float(detected_file.read_text(encoding="utf-8").strip())
                            except ValueError:
                                detected_at = time.time()
                            if time.time() - detected_at > 30:
                                diff_stat = subprocess.check_output(
                                    ["git", "diff", "--stat", f"{start_head}..{curr_head}"],
                                    cwd=repo_path_str, text=True, stderr=subprocess.DEVNULL,
                                ).strip()
                                has_real_commit = bool(diff_stat)
                                self.cancel(task_id, session_id)
                                if has_real_commit:
                                    logger.warning(
                                        "Codex process hung after commit for %s, force killed. "
                                        "Commit has real changes (%d lines in diff), treating as success (RC=0).",
                                        task_id, diff_stat.count("\n") + 1,
                                    )
                                    rc_file.write_text("0", encoding="utf-8")
                                    retcode = 0
                                else:
                                    logger.warning(
                                        "Codex process hung after empty commit for %s, force killed. "
                                        "Treating as timeout/failure (RC=124).",
                                        task_id,
                                    )
                                    rc_file.write_text("124", encoding="utf-8")
                                    retcode = 124
                                is_done = True
                except Exception as e:
                    logger.debug("Failed to check git head for codex watchdog: %s", e)

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
