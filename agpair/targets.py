from __future__ import annotations

import json
import re
from pathlib import Path

import typer

from agpair.config import AppPaths

app = typer.Typer(no_args_is_help=True)


class TargetAliasError(Exception):
    pass


class TargetManager:
    def __init__(self, targets_path: Path):
        self.path = targets_path

    @staticmethod
    def _normalize_repo_path(repo_path: str) -> Path:
        path = Path(repo_path).expanduser()
        if not path.is_absolute():
            raise TargetAliasError(f"repo-path '{repo_path}' is not an absolute path")
        if not path.exists():
            raise TargetAliasError(f"repo-path '{path}' does not exist")
        return path.resolve()

    def _read(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:
            raise TargetAliasError(f"Failed to read targets file: {e}")

    def _write(self, data: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    @staticmethod
    def _normalize_default_executor(default_executor: str | None) -> str | None:
        if default_executor is None:
            return None
        normalized = default_executor.strip().lower()
        if normalized not in {"antigravity", "codex", "gemini"}:
            raise TargetAliasError(
                "default-executor must be one of: antigravity, codex, gemini"
            )
        return normalized

    def add(
        self,
        name: str,
        repo_path: str,
        default_executor: str | None = None,
    ) -> None:
        if not re.match(r"^[a-zA-Z0-9.\-_]+$", name):
            raise TargetAliasError(f"Invalid alias name '{name}', must match [a-zA-Z0-9._-]+")

        data = self._read()
        payload = {"repo_path": str(self._normalize_repo_path(repo_path))}
        normalized_executor = self._normalize_default_executor(default_executor)
        if normalized_executor is not None:
            payload["default_executor"] = normalized_executor
        data[name] = payload
        self._write(data)

    def remove(self, name: str) -> None:
        data = self._read()
        if name not in data:
            raise TargetAliasError(f"Target alias '{name}' not found")
        del data[name]
        self._write(data)

    def resolve(self, name: str) -> str:
        return self.get(name)["repo_path"]

    def get(self, name: str) -> dict:
        data = self._read()
        if name not in data:
            raise TargetAliasError(f"Target '{name}' not found")
        entry = data[name]
        repo_path = entry.get("repo_path")
        if not isinstance(repo_path, str) or not repo_path.strip():
            raise TargetAliasError(f"Target '{name}' has no repo_path configured")
        payload = {"repo_path": str(self._normalize_repo_path(repo_path))}
        default_executor = entry.get("default_executor")
        if default_executor is not None:
            payload["default_executor"] = self._normalize_default_executor(default_executor)
        return payload

    def list_all(self) -> dict[str, dict]:
        return self._read()


def resolve_repo_path(repo_path: str | None, target: str | None, paths: AppPaths | None = None) -> str | None:
    if repo_path and target:
        raise typer.BadParameter("Cannot specify both --repo-path and --target")
    if target:
        if paths is None:
            paths = AppPaths.default()
        try:
            mgr = TargetManager(paths.targets_path)
            return mgr.resolve(target)
        except TargetAliasError as e:
            raise typer.BadParameter(str(e))
    return repo_path


@app.command("add")
def add_target(
    name: str = typer.Option(..., "--name", help="Alias name for the target."),
    repo_path: str = typer.Option(..., "--repo-path", help="Absolute path to the repository."),
    default_executor: str | None = typer.Option(
        None,
        "--default-executor",
        help="Optional default executor for this target (antigravity, codex, gemini).",
    ),
) -> None:
    paths = AppPaths.default()
    mgr = TargetManager(paths.targets_path)
    try:
        mgr.add(name, repo_path, default_executor=default_executor)
        typer.echo(f"Added target alias '{name}' -> '{mgr.resolve(name)}'")
    except TargetAliasError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)


@app.command("remove")
def remove_target(
    name: str = typer.Option(..., "--name", help="Alias name for the target to remove."),
) -> None:
    paths = AppPaths.default()
    mgr = TargetManager(paths.targets_path)
    try:
        mgr.remove(name)
        typer.echo(f"Removed target alias '{name}'")
    except TargetAliasError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)


@app.command("list")
def list_targets(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    paths = AppPaths.default()
    mgr = TargetManager(paths.targets_path)
    try:
        data = mgr.list_all()
    except TargetAliasError as e:
        if json_output:
            typer.echo(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        else:
            typer.echo(str(e), err=True)
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(json.dumps({"ok": True, "targets": data}, ensure_ascii=False, indent=2))
        return

    if not data:
        typer.echo("No target aliases found.")
        return

    for k, v in data.items():
        typer.echo(f"{k}: {v['repo_path']}")


@app.command("resolve")
def resolve_target(
    name: str = typer.Argument(..., help="Alias name to resolve."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    paths = AppPaths.default()
    mgr = TargetManager(paths.targets_path)
    try:
        payload = mgr.get(name)
        path = payload["repo_path"]
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "ok": True,
                        "target": name,
                        "repo_path": path,
                        "default_executor": payload.get("default_executor"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            typer.echo(path)
    except TargetAliasError as e:
        if json_output:
            typer.echo(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            raise typer.Exit(code=1)
        else:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=1)
