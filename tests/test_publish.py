"""Tests for the package publishing helper."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

PUBLISH_PATH = Path(__file__).resolve().parents[1] / "scripts" / "publish.py"


def load_publish_module():
    """Load the publish script as an importable module."""
    spec = importlib.util.spec_from_file_location("publish_script", PUBLISH_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load publish.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_env_file_reads_tokens(tmp_path: Path) -> None:
    """Read publish tokens from the env file."""
    publish = load_publish_module()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nPYPY_TOKEN=prod-token\nPYPY_TEST_TOKEN=test-token\n",
        encoding="utf-8",
    )

    values = publish.load_env_file(env_file)

    assert values == {
        "PYPY_TOKEN": "prod-token",
        "PYPY_TEST_TOKEN": "test-token",
    }


def test_list_dist_files_requires_files(tmp_path: Path) -> None:
    """Fail when the dist directory contains no artifacts."""
    publish = load_publish_module()
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()

    with pytest.raises(publish.PublishError, match="No distribution files found"):
        publish.list_dist_files(dist_dir)


def test_publish_distributions_sets_uv_publish_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pass the selected token to uv publish through the environment."""
    publish = load_publish_module()
    captured: dict[str, object] = {}

    def fake_run(cmd, cwd=None, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env"] = kwargs["env"]
        return None

    monkeypatch.setattr(publish, "run", fake_run)

    files = [publish.ROOT / "dist" / "pkg.whl"]
    publish.publish_distributions("testpypi", "secret-token", files)

    assert captured["cmd"] == [
        "uv",
        "publish",
        "--publish-url",
        "https://test.pypi.org/legacy/",
        "--check-url",
        "https://test.pypi.org/simple/",
        str(files[0]),
    ]
    assert captured["cwd"] == publish.ROOT
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["UV_PUBLISH_TOKEN"] == "secret-token"
    assert env["PATH"] == os.environ["PATH"]


def test_main_requires_selected_target_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Fail when the chosen publish target is missing its token."""
    publish = load_publish_module()

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    dist_file = dist_dir / "artifact.whl"
    dist_file.write_text("artifact", encoding="utf-8")

    env_file = tmp_path / ".env"
    env_file.write_text("PYPY_TEST_TOKEN=test-token\n", encoding="utf-8")

    def fake_prompt_publish_target() -> str:
        """Choose the target that is missing its token."""
        return "pypi"

    def fake_confirm_publish(target: str, files: list[Path]) -> bool:
        """Confirm publish after validating the prepared inputs."""
        assert target == "pypi"
        assert len(files) == 1
        return True

    monkeypatch.setattr(publish, "ENV_FILE", env_file)
    monkeypatch.setattr(publish, "DIST_DIR", dist_dir)
    monkeypatch.setattr(publish, "ensure_tools", lambda: None)
    monkeypatch.setattr(publish, "prompt_publish_target", fake_prompt_publish_target)
    monkeypatch.setattr(publish, "confirm_publish", fake_confirm_publish)

    result = publish.main()

    assert result == 1


def test_main_success_publishes_selected_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Publish artifacts to the selected target on the happy path."""
    publish = load_publish_module()
    calls: list[str] = []

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    dist_file = dist_dir / "artifact.whl"
    dist_file.write_text("artifact", encoding="utf-8")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "PYPY_TOKEN=prod-token\nPYPY_TEST_TOKEN=test-token\n",
        encoding="utf-8",
    )

    def fake_ensure_tools() -> None:
        """Record tool validation."""
        calls.append("ensure_tools")

    def fake_prompt_publish_target() -> str:
        """Choose the TestPyPI target."""
        return "testpypi"

    def fake_confirm_publish(target: str, files: list[Path]) -> bool:
        """Confirm publish after checking the resolved inputs."""
        assert target == "testpypi"
        assert len(files) == 1
        return True

    def fake_publish_distributions(target: str, token: str, files: list[Path]) -> None:
        """Record the target, token, and artifact count used for publishing."""
        calls.append(f"publish:{target}:{token}:{len(files)}")

    monkeypatch.setattr(publish, "ENV_FILE", env_file)
    monkeypatch.setattr(publish, "DIST_DIR", dist_dir)
    monkeypatch.setattr(publish, "ensure_tools", fake_ensure_tools)
    monkeypatch.setattr(publish, "prompt_publish_target", fake_prompt_publish_target)
    monkeypatch.setattr(publish, "confirm_publish", fake_confirm_publish)
    monkeypatch.setattr(publish, "publish_distributions", fake_publish_distributions)

    result = publish.main()

    assert result == 0
    assert calls == [
        "ensure_tools",
        "publish:testpypi:test-token:1",
    ]
