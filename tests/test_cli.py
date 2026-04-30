"""Tests for CLI command behavior."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from bigquery_cleaner import __version__
from bigquery_cleaner.cli import app
from bigquery_cleaner.config import CleanerConfig

runner = CliRunner()


class DummyQueryResult:
    """Wrap query results for the ping command."""

    def result(self):
        """Yield a single result row."""
        yield {"one": 1}


class DummyPingClient:
    """Provide the minimal query interface used by the ping command."""

    def __init__(self, project: str) -> None:
        """Store the effective project."""
        self.project = project

    def query(self, query: str) -> DummyQueryResult:
        """Return a successful ping query result."""
        assert query == "SELECT 1 AS one"
        return DummyQueryResult()


def build_config(**overrides) -> CleanerConfig:
    """Build a config object with readable test defaults."""
    cfg = CleanerConfig(project="demo-project", datasets=["alpha"])
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def test_version_command_prints_package_version() -> None:
    """Expose the package version through the CLI."""
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_ping_command_uses_client_and_reports_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the ping command successfully."""
    def fake_get_client(project: str | None) -> DummyPingClient:
        """Return a fake client for the requested project."""
        assert project == "demo-project"
        return DummyPingClient("demo-project")

    monkeypatch.setattr("bigquery_cleaner.cli.get_client", fake_get_client)

    result = runner.invoke(app, ["ping", "--project", "demo-project"])

    assert result.exit_code == 0
    assert "Successfully pinged BigQuery: demo-project" in result.stdout


def test_datasets_command_prints_dataset_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """Print each dataset on its own line."""
    def fake_list_datasets(project: str | None) -> list[str]:
        """Return a fixed list of datasets."""
        assert project == "demo-project"
        return ["alpha", "beta"]

    monkeypatch.setattr("bigquery_cleaner.cli.list_datasets", fake_list_datasets)

    result = runner.invoke(app, ["datasets", "--project", "demo-project"])

    assert result.exit_code == 0
    assert "alpha" in result.stdout
    assert "beta" in result.stdout


def test_tables_command_requires_dataset_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject table listing when no datasets are selected."""
    def fake_resolve_config(**kwargs) -> CleanerConfig:
        """Return a config that omits dataset selection."""
        del kwargs
        return CleanerConfig(project="demo-project", datasets=None, all_datasets=False)

    monkeypatch.setattr("bigquery_cleaner.cli.resolve_config", fake_resolve_config)

    result = runner.invoke(app, ["tables", "--project", "demo-project"])

    assert result.exit_code == 2
    assert "Provide --datasets or --all-datasets" in result.stdout


def test_delete_tables_command_requires_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject table deletion when no suffix is configured."""
    cfg = build_config()
    cfg.rename_suffix = ""

    def fake_resolve_config(**kwargs) -> CleanerConfig:
        """Return a config with an empty suffix."""
        del kwargs
        return cfg

    monkeypatch.setattr("bigquery_cleaner.cli.resolve_config", fake_resolve_config)

    result = runner.invoke(app, ["delete-tables", "--datasets", "alpha", "--project", "demo-project"])

    assert result.exit_code == 2
    assert "Provide --suffix" in result.stdout


def test_delete_empty_datasets_command_prints_deleted_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render deleted empty datasets in the command output."""
    def fake_resolve_config(**kwargs) -> CleanerConfig:
        """Return a dry-run config for the delete-empty-datasets command."""
        del kwargs
        return build_config(dry_run=True)

    def fake_delete_empty_datasets(cfg: CleanerConfig) -> list[str]:
        """Return a deterministic deleted dataset list."""
        assert cfg.dry_run is True
        return ["demo-project.alpha"]

    monkeypatch.setattr("bigquery_cleaner.cli.resolve_config", fake_resolve_config)
    monkeypatch.setattr("bigquery_cleaner.cli.delete_empty_datasets", fake_delete_empty_datasets)

    result = runner.invoke(
        app,
        ["delete-empty-datasets", "--datasets", "alpha", "--project", "demo-project", "--dry-run"],
    )

    assert result.exit_code == 0
    assert "Would delete" in result.stdout
    assert "Datasets" in result.stdout
    assert "demo-project.alpha" in result.stdout
