"""Tests for the release deployment helper."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

DEPLOY_PATH = Path(__file__).resolve().parents[1] / "scripts" / "deploy.py"


def load_deploy_module():
    """Load the deploy script as an importable module."""
    spec = importlib.util.spec_from_file_location("deploy_script", DEPLOY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load deploy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bump_version_patch() -> None:
    """Increment the patch segment when requested."""
    deploy = load_deploy_module()

    assert deploy.bump_version("0.1.4", "patch") == "0.1.5"


def test_validate_release_state_requires_main_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject release runs outside the main branch."""
    deploy = load_deploy_module()

    monkeypatch.setattr(deploy, "local_branch_name", lambda: "feature/test")

    with pytest.raises(deploy.DeployError, match="run from 'main'"):
        deploy.validate_release_state()


@pytest.mark.parametrize(
    ("relation", "message"),
    [
        ("1 0", "ahead of origin/main"),
        ("0 1", "behind origin/main"),
        ("2 3", "diverged from origin/main"),
    ],
)
def test_ensure_synced_with_origin_main_detects_invalid_states(
    monkeypatch: pytest.MonkeyPatch,
    relation: str,
    message: str,
) -> None:
    """Reject ahead, behind, or diverged local main states."""
    deploy = load_deploy_module()

    monkeypatch.setattr(deploy, "has_origin_remote", lambda: True)
    monkeypatch.setattr(deploy, "remote_ref_exists", lambda ref: ref == "refs/remotes/origin/main")
    monkeypatch.setattr(deploy, "git_output", lambda *args: relation)

    with pytest.raises(deploy.DeployError, match=message):
        deploy.ensure_synced_with_origin_main()


def test_main_rolls_back_version_files_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Restore version files when deployment fails before the release commit."""
    deploy = load_deploy_module()

    pyproject = tmp_path / "pyproject.toml"
    init_file = tmp_path / "__init__.py"
    pyproject.write_text('[project]\nversion = "0.1.4"\n', encoding="utf-8")
    init_file.write_text('__version__ = "0.1.4"\n', encoding="utf-8")

    monkeypatch.setattr(deploy, "PYPROJECT", pyproject)
    monkeypatch.setattr(deploy, "INIT_FILE", init_file)
    monkeypatch.setattr(deploy, "ensure_tools", lambda: None)
    monkeypatch.setattr(deploy, "validate_release_state", lambda: None)
    monkeypatch.setattr(deploy, "prompt_bump", lambda: "patch")
    monkeypatch.setattr(deploy, "confirm_bump", lambda current, new_version, kind: True)
    monkeypatch.setattr(deploy, "prompt_release_message", lambda: "Ship it")
    monkeypatch.setattr(
        deploy,
        "sync_dev_deps",
        lambda: (_ for _ in ()).throw(deploy.DeployError("sync failed")),
    )

    result = deploy.main()

    assert result == 1
    assert pyproject.read_text(encoding="utf-8") == '[project]\nversion = "0.1.4"\n'
    assert init_file.read_text(encoding="utf-8") == '__version__ = "0.1.4"\n'


def test_main_success_commits_pushes_and_tags_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Run the happy path through commit, push, and tag creation."""
    deploy = load_deploy_module()
    calls: list[str] = []

    pyproject = tmp_path / "pyproject.toml"
    init_file = tmp_path / "__init__.py"
    pyproject.write_text('[project]\nversion = "0.1.4"\n', encoding="utf-8")
    init_file.write_text('__version__ = "0.1.4"\n', encoding="utf-8")

    monkeypatch.setattr(deploy, "PYPROJECT", pyproject)
    monkeypatch.setattr(deploy, "INIT_FILE", init_file)
    monkeypatch.setattr(deploy, "ensure_tools", lambda: calls.append("ensure_tools"))
    monkeypatch.setattr(deploy, "validate_release_state", lambda: calls.append("validate_release_state"))
    monkeypatch.setattr(deploy, "prompt_bump", lambda: "patch")
    monkeypatch.setattr(deploy, "confirm_bump", lambda current, new_version, kind: True)
    monkeypatch.setattr(deploy, "prompt_release_message", lambda: "Ship it")
    monkeypatch.setattr(deploy, "sync_dev_deps", lambda: calls.append("sync_dev_deps"))
    monkeypatch.setattr(deploy, "run_ruff", lambda: calls.append("run_ruff"))
    monkeypatch.setattr(deploy, "run_tests", lambda: calls.append("run_tests"))
    monkeypatch.setattr(deploy, "build_package", lambda: calls.append("build_package"))
    monkeypatch.setattr(deploy, "ensure_tag_absent", lambda tag_name: calls.append(f"ensure_tag_absent:{tag_name}"))
    monkeypatch.setattr(deploy, "commit_release", lambda message: calls.append(f"commit_release:{message}"))
    monkeypatch.setattr(deploy, "push_main", lambda: calls.append("push_main"))
    monkeypatch.setattr(deploy, "create_and_push_tag", lambda tag_name, version: calls.append(f"tag:{tag_name}:{version}"))
    monkeypatch.setattr(deploy, "print_build_artifacts", lambda: calls.append("print_build_artifacts"))

    result = deploy.main()

    assert result == 0
    assert calls == [
        "ensure_tools",
        "validate_release_state",
        "sync_dev_deps",
        "run_ruff",
        "run_tests",
        "build_package",
        "ensure_tag_absent:v0.1.5",
        "commit_release:v0.1.5: Ship it",
        "push_main",
        "tag:v0.1.5:0.1.5",
        "print_build_artifacts",
    ]
    assert pyproject.read_text(encoding="utf-8") == '[project]\nversion = "0.1.5"\n'
    assert init_file.read_text(encoding="utf-8") == '__version__ = "0.1.5"\n'


def test_main_does_not_restore_files_after_commit_failure_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep bumped versions once the release has crossed the commit boundary."""
    deploy = load_deploy_module()

    pyproject = tmp_path / "pyproject.toml"
    init_file = tmp_path / "__init__.py"
    pyproject.write_text('[project]\nversion = "0.1.4"\n', encoding="utf-8")
    init_file.write_text('__version__ = "0.1.4"\n', encoding="utf-8")

    monkeypatch.setattr(deploy, "PYPROJECT", pyproject)
    monkeypatch.setattr(deploy, "INIT_FILE", init_file)
    monkeypatch.setattr(deploy, "ensure_tools", lambda: None)
    monkeypatch.setattr(deploy, "validate_release_state", lambda: None)
    monkeypatch.setattr(deploy, "prompt_bump", lambda: "patch")
    monkeypatch.setattr(deploy, "confirm_bump", lambda current, new_version, kind: True)
    monkeypatch.setattr(deploy, "prompt_release_message", lambda: "Ship it")
    monkeypatch.setattr(deploy, "sync_dev_deps", lambda: None)
    monkeypatch.setattr(deploy, "run_ruff", lambda: None)
    monkeypatch.setattr(deploy, "run_tests", lambda: None)
    monkeypatch.setattr(deploy, "build_package", lambda: None)
    monkeypatch.setattr(deploy, "ensure_tag_absent", lambda tag_name: None)
    monkeypatch.setattr(deploy, "commit_release", lambda message: None)
    monkeypatch.setattr(
        deploy,
        "push_main",
        lambda: (_ for _ in ()).throw(deploy.DeployError("push failed")),
    )

    result = deploy.main()

    assert result == 1
    assert pyproject.read_text(encoding="utf-8") == '[project]\nversion = "0.1.5"\n'
    assert init_file.read_text(encoding="utf-8") == '__version__ = "0.1.5"\n'
