from __future__ import annotations

from pathlib import Path
import json
import subprocess
import tempfile

from agpair.transport import messages


class BusPullError(RuntimeError):
    """Raised when a receipt-consumption invocation fails transiently.

    Covers both the deprecated ``pull`` compatibility path and the newer
    ``reserve``-based receipt flow so callers only need one exception type.
    """


class BusSendError(RuntimeError):
    """Raised when an ``agent-bus send`` invocation fails.

    Wraps subprocess failures and JSON-decode errors so callers only need
    to catch a single, domain-specific exception type.
    """


class BusSettleError(RuntimeError):
    """Raised when an ``agent-bus settle`` invocation fails."""


class AgentBusClient:
    def __init__(self, executable: str = "agent-bus") -> None:
        self.executable = executable

    def send_task(self, *, task_id: str, body: str, repo_path: str) -> int:
        full_body = f"repo_path: {repo_path}\n\n{body}"
        return self._send(task_id=task_id, status=messages.TASK, body=full_body)

    def pull_receipts(self, *, task_id: str | None = None, limit: int = 20) -> list[dict]:
        try:
            messages_reserved = self.reserve_receipts(task_id=task_id, limit=limit)
            claim_ids = [
                claim_id
                for claim_id in (msg.get("claim_id") for msg in messages_reserved)
                if isinstance(claim_id, str) and claim_id
            ]
            if claim_ids:
                self.settle_claims(reader=messages.DESKTOP_READER, claims=claim_ids)
            return messages_reserved
        except BusSettleError as exc:
            raise BusPullError(f"agent-bus pull compatibility settle failed: {exc}") from exc

    def reserve_receipts(
        self,
        *,
        task_id: str | None = None,
        limit: int = 20,
        lease_ms: int = 30000,
    ) -> list[dict]:
        argv = [
            self.executable,
            "reserve",
            "--sender",
            messages.CODE_SENDER,
            "--reader",
            messages.DESKTOP_READER,
            "--limit",
            str(limit),
            "--lease-ms",
            str(lease_ms),
            "--full",
        ]
        if task_id:
            argv.extend(["--task-id", task_id])
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, check=True)
            payload = json.loads(proc.stdout or "{}")
        except subprocess.CalledProcessError as exc:
            raise BusPullError(
                f"agent-bus reserve failed (rc={exc.returncode}): {exc.stderr or exc.stdout}"
            ) from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise BusPullError(f"agent-bus reserve returned invalid JSON: {exc}") from exc
        return list(payload.get("messages", []))

    def settle_claims(self, *, reader: str, claims: list[str]) -> int:
        try:
            proc = subprocess.run(
                [
                    self.executable,
                    "settle",
                    "--reader",
                    reader,
                    "--claims",
                    ",".join(claims),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            payload = json.loads(proc.stdout or "{}")
        except subprocess.CalledProcessError as exc:
            raise BusSettleError(
                f"agent-bus settle failed (rc={exc.returncode}): {exc.stderr or exc.stdout}"
            ) from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise BusSettleError(f"agent-bus settle returned invalid JSON: {exc}") from exc
        return int(payload.get("settled", 0))

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
            payload = json.loads(proc.stdout or "{}")
        except subprocess.CalledProcessError as exc:
            raise BusSendError(
                f"agent-bus send failed (rc={exc.returncode}): {exc.stderr or exc.stdout}"
            ) from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise BusSendError(f"agent-bus send returned invalid JSON: {exc}") from exc
        finally:
            if tmp_path:
                tmp_path.unlink(missing_ok=True)
        return int(payload.get("id", 0))
