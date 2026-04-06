from __future__ import annotations

import json
import logging
import os
import pathlib
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Callable

from agpair.executors.base import DispatchResult, ExecutorAdapter, TaskState
from agpair.models import ExecutorSafetyMetadata

logger = logging.getLogger(__name__)


def _git_head(repo_path: str) -> str | None:
    """获取当前 HEAD commit hash。"""
    try:
        res = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path, text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return res or None
    except Exception:
        return None


def _git_diff_stat(repo_path: str, start: str, end: str) -> str:
    """获取两个 commit 之间的 diff --stat。"""
    try:
        return subprocess.check_output(
            ["git", "diff", "--stat", f"{start}..{end}"],
            cwd=repo_path, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def _git_log_grep_task_id(repo_path: str, start: str, end: str, task_id: str) -> bool:
    """检查 start..end 之间是否存在 commit message 包含 task_id 的提交。

    用于多任务同 repo 场景的 commit 归属验证，防止 Task B 的提交被 Task A 误认。

    Uses record separator (\x01) to delimit commits instead of blank lines,
    because commit bodies themselves can contain blank lines.
    """
    import re as _re
    try:
        # %x01 as record separator — safe because it never appears in commit messages
        result = subprocess.check_output(
            ["git", "log", "--format=%H%x00%B%x01", f"--grep={task_id}", f"{start}..{end}"],
            cwd=repo_path, text=True, stderr=subprocess.DEVNULL,
        ).strip()
        if not result:
            return False
        # 严格验证：commit message 中必须包含完整 task_id（word boundary）
        for entry in result.split("\x01"):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split("\x00", 1)
            if len(parts) == 2 and _re.search(rf"\b{_re.escape(task_id)}\b", parts[1]):
                return True
        return False
    except Exception:
        return False


def _git_status_porcelain(repo_path: str) -> str:
    """获取工作区未提交修改（git status --porcelain）。"""
    try:
        return subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=repo_path, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def _get_process_start_time(pid: int) -> float | None:
    """获取进程的启动时间（epoch seconds），用于防止 PID 回收误判。"""
    try:
        output = subprocess.check_output(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if not output:
            return None
        from datetime import datetime
        # macOS/Linux ps lstart format: "Mon Apr  6 13:49:00 2026"
        dt = datetime.strptime(output, "%a %b %d %H:%M:%S %Y")
        return dt.timestamp()
    except Exception:
        return None


def _is_process_alive(pid: int | None, *, expected_start_time: float | None = None) -> bool:
    """检查对应进程组里是否还有非-zombie 进程存活。

    Uses ``os.killpg(pgid, 0)`` for cross-platform group liveness check,
    then falls back to ``ps`` to filter out zombie-only groups.
    macOS ``ps -g`` filters by *group leader* (not process group), so we
    use ``pgrep -g <pgid>`` which works consistently on both platforms.

    If *expected_start_time* is provided, validates the PID hasn't been
    recycled by comparing actual process start time (guards against stale
    PIDs after daemon restarts).
    """
    if not pid:
        return False
    # Fast path: check if anything in the process group can receive signals.
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process group exists but we lack permission — conservatively treat as alive.
        return True
    # Guard against PID recycling: verify the process started at the expected time.
    if expected_start_time is not None:
        actual_start = _get_process_start_time(pid)
        if actual_start is not None and abs(actual_start - expected_start_time) > 3:
            logger.info("PID %d start time mismatch (expected=%.0f, actual=%.0f) — PID was recycled.",
                        pid, expected_start_time, actual_start)
            return False
    # Slow path: exclude zombie-only groups via pgrep (cross-platform).
    try:
        status_output = subprocess.check_output(
            ["ps", "-o", "stat=", "-p", str(pid)],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        # ps failed but killpg succeeded — conservatively assume alive.
        return True
    statuses = [line.strip() for line in status_output.splitlines() if line.strip()]
    if not statuses:
        # Leader gone but group still has signal-reachable members.
        return True
    if all(s.startswith("Z") for s in statuses):
        # Leader is zombie; check if any non-zombie child exists in the group.
        try:
            children_output = subprocess.check_output(
                ["pgrep", "-g", str(pid)],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            child_pids = [p.strip() for p in children_output.splitlines() if p.strip()]
            if len(child_pids) <= 1:
                return False  # only the zombie leader itself
            child_status_output = subprocess.check_output(
                ["ps", "-o", "stat=", "-p", ",".join(child_pids)],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            child_statuses = [line.strip() for line in child_status_output.splitlines() if line.strip()]
            return any(not status.startswith("Z") for status in child_statuses)
        except Exception:
            return False
    return True


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _seconds_since(iso_str: str) -> float:
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except ValueError:
        return 0.0


def _strip_ansi(text: str) -> str:
    """移除 ANSI escape codes。"""
    import re
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


def _reap_child_process(pid: int | None) -> None:
    """Best-effort reap for finished wrapper processes to avoid zombie buildup."""
    if not pid:
        return
    try:
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass


def _atomic_write_state(state_path: pathlib.Path, state: dict) -> None:
    """Write state.json atomically via tmp + rename."""
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(state_path)


def _read_state(temp_dir: pathlib.Path) -> dict:
    """读 state.json。如果不存在，从散落文件重建（兼容旧格式）。"""
    state_file = temp_dir / "state.json"
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    # 兼容旧格式：从 pid.txt/rc.txt/repo_path.txt/start_head.txt 重建
    state = {"version": 1}
    for name, key, parser in [
        ("pid.txt", "pid", int),
        ("rc.txt", "exit_code", int),
        ("repo_path.txt", "repo_path", str),
        ("start_head.txt", "start_head", str),
    ]:
        f = temp_dir / name
        if f.exists():
            try:
                state[key] = parser(f.read_text(encoding="utf-8").strip())
            except (ValueError, TypeError):
                pass
    return state


class LocalCLIExecutor(ExecutorAdapter):
    """Base class for local CLI executors (Codex, Gemini, etc.)."""

    post_commit_grace_seconds: int = 30

    def __init__(self, bin_path: str, backend_id: str, build_cmd: Callable[[str, str, pathlib.Path], list[str]]) -> None:
        self.bin_path = bin_path
        self._backend_id = backend_id
        self._build_cmd = build_cmd

    @property
    def backend_id(self) -> str:
        return self._backend_id

    @property
    def safety_metadata(self) -> ExecutorSafetyMetadata:
        return ExecutorSafetyMetadata(
            is_mutating=True,
            is_concurrency_safe=False,
            requires_human_interaction=False,
        )

    def dispatch(self, *, task_id: str, body: str, repo_path: str) -> DispatchResult:
        temp_dir = pathlib.Path(tempfile.mkdtemp(prefix=f"agpair_{self._backend_id}_{task_id}_"))

        start_head = _git_head(repo_path)
        cli_cmd = self._build_cmd(body, repo_path, temp_dir)

        wrapper_script = temp_dir / "wrapper.sh"
        cmd_str = " ".join(shlex.quote(str(x)) for x in cli_cmd)
        
        wrapper_script.write_text(f"""#!/bin/sh
echo $$ > "{temp_dir}/pid.txt"
{cmd_str} < /dev/null
RC=$?
echo $RC > "{temp_dir}/rc.txt"
exit $RC
""", encoding="utf-8")
        wrapper_script.chmod(0o755)

        state = {
            "version": 1,
            "pid": None,
            "pgid": None,
            "started_at": _now_iso(),
            "repo_path": repo_path,
            "start_head": start_head,
            "current_head": None,
            "exit_code": None,
            "arbitration_rc": None,
            "is_process_alive": True,
            "has_committed": False,
            "commit_detected_at": None,
            "is_worktree_dirty": False,
            "final_summary": None,
            "error_summary": None,
            "termination_requested_at": None,
            "termination_signal": None,
            "updated_at": _now_iso(),
        }
        _atomic_write_state(temp_dir / "state.json", state)

        stdout_fh = (temp_dir / "stdout.log").open("w", encoding="utf-8")
        stderr_fh = (temp_dir / "stderr.log").open("w", encoding="utf-8")
        
        try:
            process = subprocess.Popen(
                [str(wrapper_script)],
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
            
        stdout_fh.close()
        stderr_fh.close()

        state["pid"] = process.pid
        state["pgid"] = process.pid
        state["process_start_time"] = time.time()
        _atomic_write_state(temp_dir / "state.json", state)

        return DispatchResult(session_id=str(temp_dir))

    def poll(self, task_id: str, session_id: str, attempt_no: int = 1) -> TaskState | None:
        temp_dir = pathlib.Path(session_id)
        if not temp_dir.exists():
            return TaskState(
                is_done=True,
                receipt=self._make_receipt(task_id, attempt_no, "BLOCKED", "Executor temp directory missing, task is lost.", {"blocker_type": "execution_error"})
            )

        state = _read_state(temp_dir)

        # ========= 第一层：进程级 liveness =========
        pid = state.get("pid")
        expected_start_time = state.get("process_start_time")
        process_alive = _is_process_alive(pid, expected_start_time=expected_start_time) if pid else False
        state["is_process_alive"] = process_alive

        # ========= 第二层：退出码检测 =========
        rc_file = temp_dir / "rc.txt"
        exit_code = state.get("exit_code")
        if exit_code is None and rc_file.exists():
            try:
                exit_code = int(rc_file.read_text(encoding="utf-8").strip())
            except ValueError:
                exit_code = 1
            state["exit_code"] = exit_code

        # ========= 第三层：Git 语义仲裁 =========
        repo_path = state.get("repo_path")
        start_head = state.get("start_head")

        if repo_path and start_head:
            current_head = _git_head(repo_path)
            state["current_head"] = current_head
            head_changed = current_head and current_head != start_head

            if head_changed and not state.get("has_committed"):
                # 检查是否有实质性提交
                diff_stat = _git_diff_stat(repo_path, start_head, current_head)
                has_real_commit = bool(diff_stat)
                # 多任务同 repo 保护：验证 commit 归属
                if has_real_commit and task_id:
                    owns_commit = _git_log_grep_task_id(repo_path, start_head, current_head, task_id)
                    if not owns_commit:
                        logger.info("Commit detected for %s but no matching task_id in commit messages; ignoring.", task_id)
                        has_real_commit = False
                state["has_committed"] = has_real_commit

                if has_real_commit and not state.get("commit_detected_at"):
                    state["commit_detected_at"] = _now_iso()

            if not state.get("has_committed") and (exit_code is not None or not process_alive):
                # 检查工作区是否有未提交修改
                porcelain = _git_status_porcelain(repo_path)
                state["is_worktree_dirty"] = bool(porcelain)

        # ========= 仲裁决策（使用缓存避免重复计算）=========
        cached_receipt = state.get("cached_receipt")
        if cached_receipt and state.get("cached_is_done"):
            # 上一轮已经仲裁完成，但进程还没死，现在重新检查进程状态
            is_done = True
            receipt = cached_receipt
        else:
            is_done, receipt = self._arbitrate(state, task_id, attempt_no, temp_dir)

        # ========= 终态自动清理 =========
        if is_done and state.get("is_process_alive"):
            still_alive, arbitration_rc = self._ensure_process_dead(state, temp_dir)
            if arbitration_rc is not None and state.get("arbitration_rc") is None:
                state["arbitration_rc"] = arbitration_rc
            state["is_process_alive"] = still_alive
            state["updated_at"] = _now_iso()
            if still_alive:
                # 缓存仲裁结果，下次 poll 无需重新计算
                state["cached_receipt"] = receipt
                state["cached_is_done"] = True
                _atomic_write_state(temp_dir / "state.json", state)
                return TaskState(is_done=False, receipt=None)
            _atomic_write_state(temp_dir / "state.json", state)
        if is_done:
            state["is_process_alive"] = False
            state.pop("cached_receipt", None)
            state.pop("cached_is_done", None)
        if not state.get("is_process_alive"):
            _reap_child_process(pid)

        state["updated_at"] = _now_iso()
        _atomic_write_state(temp_dir / "state.json", state)

        return TaskState(is_done=is_done, receipt=receipt)

    def _make_receipt(self, task_id: str, attempt_no: int, status: str, summary: str, extra_payload: dict) -> dict:
        payload = extra_payload.copy()
        if "exit_code" in payload and "returncode" not in payload:
            payload["returncode"] = payload["exit_code"]
        if "message" not in payload and status == "BLOCKED":
            payload["message"] = summary
        if "recoverable" not in payload and status == "BLOCKED":
            payload["recoverable"] = False
            
        return {
            "schema_version": "1",
            "task_id": task_id,
            "attempt_no": attempt_no,
            "review_round": 0,
            "status": status,
            "summary": summary,
            "payload": payload,
        }

    def _extract_error_summary(self, temp_dir: pathlib.Path, max_chars: int = 500) -> str:
        """从 stderr 和 stdout 中提取有用的错误/完成摘要。"""
        summary_parts = []
        
        # 1. Codex 特有的 -o 输出
        last_msg = temp_dir / "last_msg.txt"
        if last_msg.exists():
            text = last_msg.read_text(encoding="utf-8").strip()
            if text:
                return text[:max_chars]

        # 2. stderr
        stderr_file = temp_dir / "stderr.log"
        if stderr_file.exists():
            lines = stderr_file.read_text(encoding="utf-8").strip().splitlines()
            clean_lines = [_strip_ansi(l) for l in lines[-20:] if l.strip()]
            if clean_lines:
                summary_parts.append("stderr: " + "\n".join(clean_lines[-5:]))

        # 3. stdout
        stdout_file = temp_dir / "stdout.log"
        if stdout_file.exists():
            lines = stdout_file.read_text(encoding="utf-8").strip().splitlines()
            clean_lines = [_strip_ansi(l) for l in lines[-5:] if l.strip()]
            if clean_lines:
                summary_parts.append("stdout: " + "\n".join(clean_lines[-3:]))

        return "\n".join(summary_parts)[:max_chars] or "No output captured"

    def _extract_final_summary(self, temp_dir: pathlib.Path, max_chars: int = 500) -> str | None:
        """提取成功完成时的人类可读摘要。"""
        last_msg = temp_dir / "last_msg.txt"
        if not last_msg.exists():
            return None
        text = last_msg.read_text(encoding="utf-8").strip()
        return text[:max_chars] if text else None

    def _arbitrate(self, state: dict, task_id: str, attempt_no: int, temp_dir: pathlib.Path) -> tuple[bool, dict | None]:
        """
        根据进程状态 + Git 状态做出终态判定。
        返回 (is_done: bool, receipt: dict | None)
        """
        exit_code = state.get("exit_code")
        process_alive = state.get("is_process_alive", False)
        has_committed = state.get("has_committed", False)
        commit_detected_at = state.get("commit_detected_at")

        # ---- 情况 1：进程正常退出 ----
        if exit_code is not None:
            if exit_code == 0:
                events_count = self._count_events(temp_dir)
                summary = self._extract_final_summary(temp_dir) or "Task finished successfully"
                if has_committed:
                    state["final_summary"] = summary
                    state["error_summary"] = None
                    return True, self._make_receipt(task_id, attempt_no, "COMMITTED", summary, {"exit_code": 0, "events_count": events_count})
                if state.get("repo_path") and state.get("start_head"):
                    blocked_summary = "Process exited successfully without committing"
                    state["final_summary"] = None
                    state["error_summary"] = blocked_summary
                    return True, self._make_receipt(
                        task_id,
                        attempt_no,
                        "BLOCKED",
                        blocked_summary,
                        {"exit_code": 0, "blocker_type": "missing_commit", "events_count": events_count},
                    )
                state["final_summary"] = summary
                state["error_summary"] = None
                return True, self._make_receipt(task_id, attempt_no, "COMMITTED", summary, {"exit_code": 0, "events_count": events_count, "verification": "unverified"})
            else:
                summary = self._extract_error_summary(temp_dir)
                state["final_summary"] = None
                state["error_summary"] = summary or f"Exited with code {exit_code}"
                return True, self._make_receipt(task_id, attempt_no, "BLOCKED", summary or f"Exited with code {exit_code}", {"exit_code": exit_code, "blocker_type": "execution_error"})

        # ---- 情况 2：进程挂死但已有提交 ----
        if has_committed and commit_detected_at:
            seconds_since_commit = _seconds_since(commit_detected_at)
            if seconds_since_commit > self.post_commit_grace_seconds:
                logger.warning("Process hung %ds after commit for %s, force killing.", seconds_since_commit, task_id)
                summary = self._extract_final_summary(temp_dir) or "Task committed (process hung post-commit, force killed)"
                state["final_summary"] = summary
                state["error_summary"] = None
                return True, self._make_receipt(task_id, attempt_no, "COMMITTED", summary, {"exit_code": 0, "arbitration": "post_commit_hang"})

        # ---- 情况 3：进程已死但没有 exit_code（异常崩溃）----
        if not process_alive and exit_code is None:
            if has_committed:
                summary = self._extract_final_summary(temp_dir) or "Process died after committing"
                state["final_summary"] = summary
                state["error_summary"] = None
                return True, self._make_receipt(task_id, attempt_no, "COMMITTED", summary, {"exit_code": 0, "arbitration": "process_died_with_commit"})
            else:
                summary = "Process died without committing"
                state["final_summary"] = None
                state["error_summary"] = summary
                return True, self._make_receipt(task_id, attempt_no, "BLOCKED", summary, {"exit_code": -1, "blocker_type": "process_crash"})

        # ---- 情况 4：进程还在跑，没有提交 ----
        return False, None

    def _count_events(self, temp_dir: pathlib.Path) -> int:
        stdout_file = temp_dir / "stdout.jsonl"
        if not stdout_file.exists():
            stdout_file = temp_dir / "stdout.log"
        if not stdout_file.exists():
            return 0
        try:
            with stdout_file.open("r", encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        except Exception:
            return 0

    def _ensure_process_dead(self, state: dict, temp_dir: pathlib.Path) -> tuple[bool, int | None]:
        """请求终止进程组，不在主循环里阻塞等待。返回 (still_alive, arbitration_rc)。"""
        pgid = state.get("pgid") or state.get("pid")
        if not pgid:
            return False, None

        if not _is_process_alive(pgid):
            _reap_child_process(state.get("pid"))
            return False, state.get("arbitration_rc")

        termination_requested_at = state.get("termination_requested_at")
        termination_signal = state.get("termination_signal")

        if not termination_requested_at:
            try:
                os.killpg(pgid, signal.SIGTERM)
                logger.info("Sent SIGTERM to process group %d", pgid)
            except ProcessLookupError:
                # Process died between _is_process_alive check and killpg.
                return False, 128 + signal.SIGTERM
            except PermissionError:
                logger.warning("Permission denied sending SIGTERM to pgid %d", pgid)
                return False, None
            state["termination_requested_at"] = _now_iso()
            state["termination_signal"] = "SIGTERM"
            return True, 128 + signal.SIGTERM

        if termination_signal == "SIGKILL":
            # Give kernel 2s to reap after SIGKILL; if still visible, it's a zombie — force-treat as dead.
            if _seconds_since(termination_requested_at) > 2:
                _reap_child_process(state.get("pid"))
                logger.warning("SIGKILL sent but pgid %d still visible after 2s — treating as dead (likely zombie).", pgid)
                return False, 128 + signal.SIGKILL
            return True, 128 + signal.SIGKILL
        if _seconds_since(termination_requested_at) < 5:
            return True, 128 + signal.SIGTERM

        try:
            os.killpg(pgid, signal.SIGKILL)
            logger.warning("Sent SIGKILL to process group %d (SIGTERM timed out)", pgid)
        except ProcessLookupError:
            # Process died between _is_process_alive check and killpg — that's fine.
            return False, 128 + signal.SIGTERM
        except PermissionError:
            logger.warning("Permission denied sending SIGKILL to pgid %d", pgid)
            return False, None
        state["termination_requested_at"] = _now_iso()
        state["termination_signal"] = "SIGKILL"
        return True, 128 + signal.SIGKILL

    def _clean_git_locks(self, repo_path: str | None, *, started_at: str | None = None) -> None:
        """仅在确认锁文件无人持有且更可能由本任务遗留时清理 git 锁。"""
        if not repo_path:
            return
        git_dir = pathlib.Path(repo_path) / ".git"
        if not git_dir.exists():
            return
        started_at_epoch: float | None = None
        if started_at:
            from datetime import datetime, timezone

            try:
                started_at_epoch = (
                    datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                    .astimezone(timezone.utc)
                    .timestamp()
                )
            except ValueError:
                started_at_epoch = None
        for lock in git_dir.rglob("*.lock"):
            # 跳过 worktrees 子目录中的锁文件（属于其他 worktree）
            try:
                relative = lock.relative_to(git_dir)
                if str(relative).startswith("worktrees"):
                    continue
            except ValueError:
                continue
            try:
                if started_at_epoch is not None and lock.stat().st_mtime + 1 < started_at_epoch:
                    continue
                try:
                    result = subprocess.run(
                        ["lsof", str(lock)],
                        capture_output=True,
                        text=True,
                        stderr=subprocess.DEVNULL,
                        timeout=2,
                    )
                except FileNotFoundError:
                    continue
                except (subprocess.SubprocessError, OSError):
                    continue
                if result.returncode == 0 and result.stdout.strip():
                    continue
                if result.returncode not in {0, 1}:
                    continue
                lock.unlink()
                logger.info("Removed stale git lock: %s", lock)
            except OSError:
                pass

    def cancel(self, task_id: str, session_id: str) -> None:
        """取消任务：请求终止进程组，并持久化终止状态。"""
        temp_dir = pathlib.Path(session_id)
        if not temp_dir.exists():
            return

        state = _read_state(temp_dir)
        still_alive, arbitration_rc = self._ensure_process_dead(state, temp_dir)

        if arbitration_rc is not None:
            state["arbitration_rc"] = arbitration_rc
        elif state.get("arbitration_rc") is None:
            state["arbitration_rc"] = 128 + signal.SIGTERM
        state["is_process_alive"] = still_alive
        state["updated_at"] = _now_iso()
        _atomic_write_state(temp_dir / "state.json", state)

    def cleanup(self, session_id: str) -> None:
        """终态清理：单步推进终止流程；仅在进程确认退出后删除临时目录。"""
        if not session_id:
            return
        temp_dir = pathlib.Path(session_id)
        if not temp_dir.exists():
            return
        if not temp_dir.name.startswith("agpair_"):
            return

        state = _read_state(temp_dir)
        pgid = state.get("pgid") or state.get("pid")
        if _is_process_alive(pgid):
            still_alive, arbitration_rc = self._ensure_process_dead(state, temp_dir)
            if arbitration_rc is not None:
                state["arbitration_rc"] = arbitration_rc
            state["is_process_alive"] = still_alive
            state["updated_at"] = _now_iso()
            _atomic_write_state(temp_dir / "state.json", state)
            if still_alive:
                return

        _reap_child_process(state.get("pid"))
        if state.get("termination_signal") == "SIGKILL":
            self._clean_git_locks(state.get("repo_path"), started_at=state.get("started_at"))
        shutil.rmtree(temp_dir, ignore_errors=True)
