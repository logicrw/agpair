import json
import pathlib

from agpair.executors.antigravity_project_state import sweep_antigravity_transient_projects


def _write_project_config(config_path: pathlib.Path, path: pathlib.Path, project_id: str) -> None:
    config_path.write_text(
        json.dumps(
            {
                "id": project_id,
                "projectResources": {"resources": [{"folderUri": path.as_uri()}]},
            },
        ),
        encoding="utf-8",
    )


def _read_cache(cache_path: pathlib.Path) -> dict[str, str]:
    return json.loads(cache_path.read_text(encoding="utf-8"))


def test_sweep_removes_only_orphaned_agpair_transient_projects(monkeypatch, tmp_path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    cache_file = tmp_path / "cache" / "projects.json"
    cache_file.parent.mkdir()
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_PROJECTS_DIR", str(projects_dir))
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_PROJECTS_CACHE", str(cache_file))
    orphan_path = (tmp_path / "repo" / ".agpair" / "worktrees" / "TASK-ORPHAN").resolve()
    active_path = (tmp_path / "repo" / ".agpair" / "worktrees" / "TASK-ACTIVE").resolve()
    real_path = (tmp_path / "real-project").resolve()
    orphan_config = projects_dir / "orphan.json"
    active_config = projects_dir / "active.json"
    real_config = projects_dir / "real.json"
    _write_project_config(orphan_config, orphan_path, "orphan")
    _write_project_config(active_config, active_path, "active")
    _write_project_config(real_config, real_path, "real")
    cache_file.write_text(
        json.dumps(
            {
                str(orphan_path): "orphan",
                str(active_path): "active",
                str(real_path): "real",
            },
        ),
        encoding="utf-8",
    )

    result = sweep_antigravity_transient_projects(active_paths={active_path})

    assert result.config_files == 1
    assert result.cache_entries == 1
    assert not orphan_config.exists()
    assert active_config.exists()
    assert real_config.exists()
    cache = _read_cache(cache_file)
    assert str(orphan_path) not in cache
    assert cache[str(active_path)] == "active"
    assert cache[str(real_path)] == "real"


def test_sweep_dry_run_reports_without_removing(monkeypatch, tmp_path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    cache_file = tmp_path / "cache" / "projects.json"
    cache_file.parent.mkdir()
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_PROJECTS_DIR", str(projects_dir))
    monkeypatch.setenv("AGPAIR_ANTIGRAVITY_PROJECTS_CACHE", str(cache_file))
    orphan_path = (tmp_path / "repo" / ".agpair" / "worktrees" / "TASK-ORPHAN").resolve()
    orphan_config = projects_dir / "orphan.json"
    _write_project_config(orphan_config, orphan_path, "orphan")
    cache_file.write_text(json.dumps({str(orphan_path): "orphan"}), encoding="utf-8")

    result = sweep_antigravity_transient_projects(dry_run=True)

    assert result.config_files == 1
    assert result.cache_entries == 1
    assert orphan_config.exists()
    assert _read_cache(cache_file)[str(orphan_path)] == "orphan"
