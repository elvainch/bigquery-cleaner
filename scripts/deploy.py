#!/usr/bin/env python3
"""
Deployment helper for bigquery-cleaner.

Responsibilities:
- Prompt for version bump (major/minor/patch) and update version in:
  - pyproject.toml [project].version
  - src/bigquery_cleaner/__init__.py __version__
- Ensure dev dependencies are synced (for pytest).
- Run tests.
- Build the package with uv.

All heavy lifting is done here; the shell wrapper only invokes this file.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
INIT_FILE = ROOT / "src" / "bigquery_cleaner" / "__init__.py"


class DeployError(RuntimeError):
    """Deployment-related error."""
    pass


def run(cmd: Iterable[str], cwd: Path | None = None) -> None:
    """Run a command, raising DeployError on failure."""
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=str(cwd) if cwd else None,
            check=False,
            text=True,
        )
    except FileNotFoundError as exc:
        raise DeployError(f"Command not found: {cmd[0]}") from exc
    if proc.returncode != 0:
        raise DeployError(f"Command failed ({proc.returncode}): {' '.join(cmd)}")


def ensure_tools() -> None:
    """Ensure required tools are available in PATH."""
    if shutil.which("uv") is None:
        raise DeployError(
            "'uv' is required. Install from https://docs.astral.sh/uv/ and ensure it is on PATH."
        )


def prompt_bump() -> str:
    """Prompt the user for bump type: major, minor, or patch."""
    valid = {"major", "minor", "patch"}
    while True:
        choice = input("Version bump (major/minor/patch): ").strip().lower()
        if choice in valid:
            return choice
        print("Please enter 'major', 'minor', or 'patch'.")


def confirm_bump(current: str, new_version: str, kind: str) -> bool:
    """Ask user to confirm the proposed version bump."""
    print(f"Current version: {current}")
    print(f"Proposed version: {new_version} ({kind})")
    while True:
        ans = input("Proceed with version bump? [y/N]: ").strip().lower()
        if ans in {"y", "yes"}:
            return True
        if ans in {"n", "no", ""}:
            return False
        print("Please answer 'y' or 'n'.")


def parse_version(s: str) -> tuple[int, int, int]:
    """Parse version string s into (major, minor, patch)."""
    m = re.fullmatch(r"\s*(\d+)\.(\d+)\.(\d+)\s*", s)
    if not m:
        raise DeployError(f"Unsupported version format: {s!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def bump_version(v: str, kind: str) -> str:
    """Bump version string v according to kind."""
    major, minor, patch = parse_version(v)
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise DeployError(f"Unknown bump kind: {kind}")


def read_current_version_pyproject(path: Path) -> str:
    """Read current version from pyproject.toml."""
    text = path.read_text(encoding="utf-8")
    # Find version under [project]
    project_block = re.search(r"(?ms)^\[project\](.*?)(^\[|\Z)", text)
    if not project_block:
        raise DeployError("[project] section not found in pyproject.toml")
    block = project_block.group(1)
    # Accept versions wrapped in proper quotes or mistakenly escaped quotes (\")
    m = re.search(r"(?m)^\s*version\s*=\s*(?:\\)?([\"'])([^\"']+)\1\s*$", block)
    if not m:
        raise DeployError("project.version not found in pyproject.toml")
    return m.group(2)


def write_version_pyproject(path: Path, new_version: str) -> None:
    """Write new version to pyproject.toml."""
    text = path.read_text(encoding="utf-8")

    def _repl(match: re.Match[str]) -> str:
        head, block, tail = match.group(1), match.group(2), match.group(3)
        # Replace version line inside the [project] block without introducing escape sequences
        block_new = re.sub(
            r'(?m)^(\s*version\s*=\s*)"[^"]+"(\s*)$',
            lambda m: f'{m.group(1)}"{new_version}"{m.group(2)}',
            block,
            count=1,
        )
        if block == block_new:
            raise DeployError("Failed to update version in pyproject.toml")
        return f"{head}{block_new}{tail}"

    new_text, n = re.subn(
        r"(?ms)^(\[project\]\s*)(.*?)(^\[|\Z)",
        _repl,
        text,
        count=1,
    )
    if n == 0:
        raise DeployError("[project] section not found while writing pyproject.toml")
    path.write_text(new_text, encoding="utf-8")


def read_current_version_init(path: Path) -> str:
    """Read __version__ from __init__.py."""
    text = path.read_text(encoding="utf-8")
    # Accept proper or mistakenly escaped quotes
    m = re.search(r"(?m)^\s*__version__\s*=\s*(?:\\)?([\"'])([^\"']+)\1\s*$", text)
    if not m:
        raise DeployError("__version__ not found in __init__.py")
    return m.group(2)


def write_version_init(path: Path, new_version: str) -> None:
    """Write new version to __init__.py."""
    text = path.read_text(encoding="utf-8")
    new_text, n = re.subn(
        r'(?m)^(\s*__version__\s*=\s*)"[^"]+"(\s*)$',
        lambda m: f'{m.group(1)}"{new_version}"{m.group(2)}',
        text,
        count=1,
    )
    if n == 0:
        raise DeployError("Failed to update __version__ in __init__.py")
    path.write_text(new_text, encoding="utf-8")


def sync_dev_deps() -> None:
    """Sync dev dependencies via uv."""
    # Ensure dev tools (pytest/ruff) are available
    run(["uv", "sync", "--group", "dev"])


def run_ruff() -> None:
    """Run ruff checks via uv."""
    run(["uv", "run", "ruff", "check", "."])


def run_tests() -> None:
    """Run tests with pytest via uv."""
    # Use pytest per repo convention
    run(["uv", "run", "pytest", "-q"])


def build_package() -> None:
    """Build the package with uv."""
    run(["uv", "build"])


def main() -> int:
    """Main deployment flow."""
    try:
        ensure_tools()
        if not PYPROJECT.exists():
            raise DeployError(f"pyproject.toml not found at {PYPROJECT}")
        if not INIT_FILE.exists():
            raise DeployError(f"__init__.py not found at {INIT_FILE}")

        current = read_current_version_pyproject(PYPROJECT)
        init_ver = read_current_version_init(INIT_FILE)
        if current != init_ver:
            print(
                f"Warning: version mismatch pyproject={current} vs __init__={init_ver}; proceeding.",
                file=sys.stderr,
            )

        bump_kind = prompt_bump()
        new_version = bump_version(current, bump_kind)
        # Confirm before writing any files
        if not confirm_bump(current, new_version, bump_kind):
            print("Canceled.")
            return 0

        print(f"Bumping version: {current} -> {new_version} ({bump_kind})")
        write_version_pyproject(PYPROJECT, new_version)
        write_version_init(INIT_FILE, new_version)

        print("Syncing dev dependencies (ruff/pytest)...")
        sync_dev_deps()

        print("Running ruff checks...")
        run_ruff()

        print("Running tests...")
        run_tests()

        print("Building package with uv...")
        build_package()

        dist = ROOT / "dist"
        if dist.exists():
            artifacts = sorted(p.name for p in dist.iterdir())
            if artifacts:
                print("Build artifacts in dist/:")
                for name in artifacts:
                    print(f" - {name}")
        print("Done.")
        return 0
    except DeployError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
