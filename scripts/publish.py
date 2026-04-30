#!/usr/bin/env python3
"""Publishing helper for bigquery-cleaner.

Responsibilities:
- Prompt for a publish target (TestPyPI or PyPI).
- Load publish tokens from .private/.env.
- Validate that build artifacts exist in dist/.
- Upload distributions with uv publish.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".private" / ".env"
DIST_DIR = ROOT / "dist"

PUBLISH_TARGETS = {
    "testpypi": {
        "token_key": "PYPY_TEST_TOKEN",
        "publish_url": "https://test.pypi.org/legacy/",
        "check_url": "https://test.pypi.org/simple/",
    },
    "pypi": {
        "token_key": "PYPY_TOKEN",
        "publish_url": "https://upload.pypi.org/legacy/",
        "check_url": "https://pypi.org/simple/",
    },
}


class PublishError(RuntimeError):
    """Publishing-related error."""


def run(
    cmd: Iterable[str],
    cwd: Path | None = None,
    *,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command, raising PublishError on failure."""
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=str(cwd) if cwd else None,
            check=False,
            text=True,
            env=env,
            capture_output=capture_output,
        )
    except FileNotFoundError as exc:
        raise PublishError(f"Command not found: {cmd[0]}") from exc
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip() if capture_output else ""
        stdout = (proc.stdout or "").strip() if capture_output else ""
        detail = stderr or stdout
        if detail:
            raise PublishError(
                f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{detail}"
            )
        raise PublishError(f"Command failed ({proc.returncode}): {' '.join(cmd)}")
    return proc


def ensure_tools() -> None:
    """Ensure uv is available in PATH."""
    if shutil.which("uv") is None:
        raise PublishError(
            "'uv' is required. Install from https://docs.astral.sh/uv/ and ensure it is on PATH."
        )


def load_env_file(path: Path) -> dict[str, str]:
    """Load key-value pairs from a simple .env file."""
    if not path.exists():
        raise PublishError(f"Env file not found at {path}")

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise PublishError(f"Invalid env line in {path}: {line!r}")
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def list_dist_files(dist_dir: Path) -> list[Path]:
    """Return distribution files from dist/."""
    if not dist_dir.exists():
        raise PublishError(f"Distribution directory not found at {dist_dir}")
    files = sorted(path for path in dist_dir.iterdir() if path.is_file())
    if not files:
        raise PublishError(f"No distribution files found in {dist_dir}")
    return files


def prompt_publish_target() -> str:
    """Prompt for a publish target."""
    while True:
        choice = input("Publish to (testpypi/pypi): ").strip().lower()
        if choice in PUBLISH_TARGETS:
            return choice
        print("Please enter 'testpypi' or 'pypi'.")


def confirm_publish(target: str, files: list[Path]) -> bool:
    """Ask for confirmation before publishing."""
    print(f"Target: {target}")
    print("Files:")
    for path in files:
        print(f" - {path.name}")
    while True:
        ans = input("Proceed with publish? [y/N]: ").strip().lower()
        if ans in {"y", "yes"}:
            return True
        if ans in {"n", "no", ""}:
            return False
        print("Please answer 'y' or 'n'.")


def publish_distributions(target: str, token: str, files: list[Path]) -> None:
    """Publish the given distributions to the selected target."""
    cfg = PUBLISH_TARGETS[target]
    env = os.environ.copy()
    env["UV_PUBLISH_TOKEN"] = token
    run(
        [
            "uv",
            "publish",
            "--publish-url",
            cfg["publish_url"],
            "--check-url",
            cfg["check_url"],
            *[str(path) for path in files],
        ],
        cwd=ROOT,
        env=env,
    )


def main() -> int:
    """Run the publishing flow."""
    try:
        ensure_tools()
        env_values = load_env_file(ENV_FILE)
        files = list_dist_files(DIST_DIR)

        target = prompt_publish_target()
        token_key = PUBLISH_TARGETS[target]["token_key"]
        token = env_values.get(token_key)
        if not token:
            raise PublishError(f"Missing token '{token_key}' in {ENV_FILE}")

        if not confirm_publish(target, files):
            print("Canceled.")
            return 0

        print(f"Publishing {len(files)} file(s) to {target}...")
        publish_distributions(target, token, files)
        print("Done.")
        return 0
    except PublishError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
