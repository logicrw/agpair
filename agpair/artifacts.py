from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from agpair.models import utcnow_iso

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_task_id(task_id: str) -> str:
    safe = _SAFE_NAME_RE.sub("_", task_id.strip())
    return safe or "task"


def task_artifact_root(root: Path, task_id: str, attempt_no: int) -> Path:
    return root / "tasks" / safe_task_id(task_id) / f"attempt-{attempt_no}"


def ensure_attempt_dir(root: Path, task_id: str, attempt_no: int) -> Path:
    path = task_artifact_root(root, task_id, attempt_no)
    path.mkdir(parents=True, exist_ok=True)
    return path


def copy_artifact(src: str | Path | None, dest: Path) -> str | None:
    if not src:
        return None
    source = Path(src)
    if not source.exists() or not source.is_file():
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    return str(dest)


def write_json(path: Path, payload: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return str(path)


def write_text(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def read_excerpt(path: str | Path | None, *, max_chars: int = 2000) -> str | None:
    if not path:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    if len(stripped) <= max_chars:
        return stripped
    marker = "\n[... truncated by agpair ...]\n"
    if max_chars <= len(marker) + 2:
        return stripped[:max_chars]
    content_budget = max_chars - len(marker)
    head_budget = max(1, content_budget // 2)
    tail_budget = max(1, content_budget - head_budget)
    return stripped[:head_budget] + marker + stripped[-tail_budget:]


def sha256_file(path: str | Path | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def artifact_metadata(path: str | Path | None, *, artifact_type: str) -> dict[str, Any] | None:
    if not path:
        return None
    p = Path(path)
    try:
        stat = p.stat()
    except OSError:
        return None
    return {
        "type": artifact_type,
        "path": str(p),
        "size_bytes": stat.st_size,
        "sha256": sha256_file(p),
        "captured_at": utcnow_iso(),
    }


def live_artifact_metadata(
    path: str | Path | None,
    *,
    artifact_type: str,
    max_excerpt_chars: int = 2000,
) -> dict[str, Any] | None:
    if not path:
        return None
    p = Path(path)
    try:
        stat = p.stat()
    except OSError:
        return None
    if not p.is_file():
        return None
    return {
        "type": artifact_type,
        "path": str(p),
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        "mtime_ns": stat.st_mtime_ns,
        "excerpt": read_excerpt(p, max_chars=max_excerpt_chars),
    }
