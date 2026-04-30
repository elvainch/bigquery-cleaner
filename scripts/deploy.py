#!/usr/bin/env python3
"""
Deployment helper for bigquery-cleaner.

Responsibilities:
- Validate release preconditions on git state.
- Prompt for version bump (major/minor/patch) and update version in:
  - pyproject.toml [project].version
  - src/bigquery_cleaner/__init__.py __version__
- Prompt for a release message.
- Ensure dev dependencies are synced (for pytest).
- Run lint and tests.
- Build the package with uv.
- Create the release commit, push main, and create/push the release tag.
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


def run(
    cmd: Iterable[str],
    cwd: Path | None = None,
    *,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command, raising DeployError on failure."""
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=str(cwd) if cwd else None,
            check=False,
            text=True,
            capture_output=capture_output,
        )
    except FileNotFoundError as exc:
        raise DeployError(f"Command not found: {cmd[0]}") from exc
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip() if capture_output else ""
        stdout = (proc.stdout or "").strip() if capture_output else ""
        detail = stderr or stdout
        if detail:
            raise DeployError(
                f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{detail}"
            )
        raise DeployError(f"Command failed ({proc.returncode}): {' '.join(cmd)}")
    return proc


def ensure_tools() -> None:
    """Ensure required tools are available in PATH."""
    if shutil.which("uv") is None:
        raise DeployError(
            "'uv' is required. Install from https://docs.astral.sh/uv/ and ensure it is on PATH."
        )
    if shutil.which("git") is None:
        raise DeployError(
            "'git' is required. Install Git and ensure it is on PATH."
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


def prompt_release_message() -> str:
    """Prompt for the release commit message suffix."""
    while True:
        message = input("Release message: ").strip()
        if message:
            return message
        print("Release message cannot be empty.")


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
    project_block = re.search(r"(?ms)^\[project\](.*?)(^\[|\Z)", text)
    if not project_block:
        raise DeployError("[project] section not found in pyproject.toml")
    block = project_block.group(1)
    m = re.search(r"(?m)^\s*version\s*=\s*(?:\\)?([\"'])([^\"']+)\1\s*$", block)
    if not m:
        raise DeployError("project.version not found in pyproject.toml")
    return m.group(2)


def write_version_pyproject(path: Path, new_version: str) -> None:
    """Write new version to pyproject.toml."""
    text = path.read_text(encoding="utf-8")

    def _repl(match: re.Match[str]) -> str:
        head, block, tail = match.group(1), match.group(2), match.group(3)
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
    run(["uv", "sync", "--group", "dev"])


def run_ruff() -> None:
    """Run ruff checks via uv."""
    run(["uv", "run", "ruff", "check", "."])


def run_tests() -> None:
    """Run tests with pytest via uv."""
    run(["uv", "run", "pytest", "-q"])


def build_package() -> None:
    """Build the package with uv."""
    run(["uv", "build"])


def git_output(*args: str) -> str:
    """Run a git command and return trimmed stdout."""
    proc = run(["git", *args], cwd=ROOT, capture_output=True)
    return (proc.stdout or "").strip()


def local_branch_name() -> str:
    """Return the current local branch name."""
    return git_output("branch", "--show-current")


def ensure_main_branch() -> None:
    """Ensure deploy is being run from main."""
    branch = local_branch_name()
    if branch != "main":
        raise DeployError(f"Deploy must be run from 'main', got '{branch or '<detached>'}'.")


def has_origin_remote() -> bool:
    """Return True if the repo has an origin remote."""
    remotes = git_output("remote")
    return "origin" in remotes.splitlines()


def remote_ref_exists(ref: str) -> bool:
    """Return True if the given git ref exists locally."""
    try:
        git_output("rev-parse", "--verify", "--quiet", ref)
    except DeployError:
        return False
    return True


def ensure_synced_with_origin_main() -> None:
    """Ensure local main is in sync with origin/main when available."""
    if not has_origin_remote():
        print("No origin remote configured; skipping origin/main sync check.")
        return

    if not remote_ref_exists("refs/remotes/origin/main"):
        print("No local origin/main tracking ref found; skipping origin/main sync check.")
        return

    relation = git_output("rev-list", "--left-right", "--count", "main...origin/main")
    try:
        ahead_count_str, behind_count_str = relation.split()
    except ValueError as exc:
        raise DeployError(f"Unexpected git rev-list output: {relation!r}") from exc

    ahead_count = int(ahead_count_str)
    behind_count = int(behind_count_str)
    if ahead_count == 0 and behind_count == 0:
        return
    if ahead_count > 0 and behind_count > 0:
        raise DeployError("Local main has diverged from origin/main.")
    if ahead_count > 0:
        raise DeployError("Local main is ahead of origin/main.")
    raise DeployError("Local main is behind origin/main.")


def snapshot_files(paths: Iterable[Path]) -> dict[Path, str]:
    """Snapshot file contents for later restoration."""
    return {path: path.read_text(encoding="utf-8") for path in paths}


def restore_files(snapshot: dict[Path, str]) -> None:
    """Restore file contents from a snapshot."""
    for path, content in snapshot.items():
        path.write_text(content, encoding="utf-8")


def validate_release_state() -> None:
    """Validate preconditions for a release run."""
    ensure_main_branch()
    ensure_synced_with_origin_main()


def ensure_tag_absent(tag_name: str) -> None:
    """Ensure the release tag does not already exist locally or remotely."""
    if remote_ref_exists(f"refs/tags/{tag_name}"):
        raise DeployError(f"Tag '{tag_name}' already exists locally.")

    if not has_origin_remote():
        return

    try:
        git_output("ls-remote", "--exit-code", "--tags", "origin", f"refs/tags/{tag_name}")
    except DeployError as err:
        if "Command failed (2)" in str(err):
            return
        raise
    raise DeployError(f"Tag '{tag_name}' already exists on origin.")


def commit_release(commit_message: str) -> None:
    """Stage repo changes and create the release commit."""
    run(["git", "add", "-A"], cwd=ROOT)
    run(["git", "commit", "-m", commit_message], cwd=ROOT)


def push_main() -> None:
    """Push the local main branch to origin."""
    run(["git", "push", "origin", "main"], cwd=ROOT)


def create_and_push_tag(tag_name: str, version: str) -> None:
    """Create and push an annotated release tag."""
    run(["git", "tag", "-a", tag_name, "-m", f"Release {version}"], cwd=ROOT)
    try:
        run(["git", "push", "origin", tag_name], cwd=ROOT)
    except DeployError:
        if remote_ref_exists(f"refs/tags/{tag_name}"):
            run(["git", "tag", "-d", tag_name], cwd=ROOT)
        raise


def print_build_artifacts() -> None:
    """Print build artifacts in dist/ if present."""
    dist = ROOT / "dist"
    if not dist.exists():
        return
    artifacts = sorted(p.name for p in dist.iterdir())
    if not artifacts:
        return
    print("Build artifacts in dist/:")
    for name in artifacts:
        print(f" - {name}")


def main() -> int:
    """Main deployment flow."""
    version_snapshot: dict[Path, str] | None = None
    release_committed = False
    try:
        ensure_tools()
        if not PYPROJECT.exists():
            raise DeployError(f"pyproject.toml not found at {PYPROJECT}")
        if not INIT_FILE.exists():
            raise DeployError(f"__init__.py not found at {INIT_FILE}")

        validate_release_state()

        current = read_current_version_pyproject(PYPROJECT)
        init_ver = read_current_version_init(INIT_FILE)
        if current != init_ver:
            print(
                f"Warning: version mismatch pyproject={current} vs __init__={init_ver}; proceeding.",
                file=sys.stderr,
            )

        bump_kind = prompt_bump()
        new_version = bump_version(current, bump_kind)
        if not confirm_bump(current, new_version, bump_kind):
            print("Canceled.")
            return 0

        release_message = prompt_release_message()
        version_snapshot = snapshot_files([PYPROJECT, INIT_FILE])

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

        tag_name = f"v{new_version}"
        commit_message = f"{tag_name}: {release_message}"
        ensure_tag_absent(tag_name)

        print(f"Creating release commit: {commit_message}")
        commit_release(commit_message)
        release_committed = True

        print("Pushing main...")
        push_main()

        print(f"Creating and pushing tag {tag_name}...")
        create_and_push_tag(tag_name, new_version)

        print_build_artifacts()
        print("Done.")
        return 0
    except DeployError as err:
        if version_snapshot is not None and not release_committed:
            restore_files(version_snapshot)
            print("Restored version files after failed deploy.", file=sys.stderr)
        print(f"ERROR: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
