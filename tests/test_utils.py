"""Tests for orchestration helpers."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from bigquery_cleaner.bq_client import TableMetadata
from bigquery_cleaner.config import CleanerConfig
from bigquery_cleaner.utils import (
    compute_unqueried_for_location,
    get_ds_to_loc_map,
    get_execution_context,
    get_jobs_projects,
)


class DummyClient:
    """Provide a client object with a project attribute for tests."""

    def __init__(self, project: str) -> None:
        """Store the effective project."""
        self.project = project


def test_get_execution_context_normalizes_datasets_and_groups_locations(monkeypatch) -> None:
    """Build the execution context from the effective client project."""
    cfg = CleanerConfig(project="configured-project", datasets=["alpha"], exclude_datasets=["beta"])
    client = DummyClient(project="effective-project")
    expected_pairs = [("effective-project", "alpha")]
    expected_groups = defaultdict(list, {"US": expected_pairs})

    def fake_get_client(project: str | None) -> DummyClient:
        """Return the prepared client and validate the configured project."""
        assert project == "configured-project"
        return client

    def fake_normalize_datasets(
        client_arg: DummyClient,
        datasets: list[str] | None,
        default_project: str,
        exclude_datasets: list[str] | None,
    ) -> list[tuple[str, str]]:
        """Return normalized dataset pairs for the effective project."""
        assert client_arg is client
        assert datasets == ["alpha"]
        assert default_project == "effective-project"
        assert exclude_datasets == ["beta"]
        return expected_pairs

    def fake_group_datasets_by_location(
        client_arg: DummyClient,
        pairs: list[tuple[str, str]],
    ) -> defaultdict[str, list[tuple[str, str]]]:
        """Return the prepared location grouping."""
        assert client_arg is client
        assert pairs == expected_pairs
        return expected_groups

    monkeypatch.setattr("bigquery_cleaner.utils.get_client", fake_get_client)
    monkeypatch.setattr("bigquery_cleaner.utils.normalize_datasets", fake_normalize_datasets)
    monkeypatch.setattr("bigquery_cleaner.utils.group_datasets_by_location", fake_group_datasets_by_location)

    resolved_client, groups = get_execution_context(cfg)

    assert resolved_client is client
    assert groups == expected_groups


def test_get_ds_to_loc_map_flattens_location_groups() -> None:
    """Create a reverse lookup keyed by project and dataset."""
    loc_groups = defaultdict(
        list,
        {
            "US": [("demo", "alpha")],
            "EU": [("demo", "beta"), ("demo", "gamma")],
        },
    )

    mapping = get_ds_to_loc_map(loc_groups)

    assert mapping == {
        ("demo", "alpha"): "US",
        ("demo", "beta"): "EU",
        ("demo", "gamma"): "EU",
    }


def test_get_jobs_projects_includes_main_project_and_deduplicates() -> None:
    """Always scan the main project first when building jobs-project inputs."""
    cfg = CleanerConfig(project="demo", jobs_projects=["audit-project", "demo", "bi-project"])

    jobs_projects = get_jobs_projects(cfg, "demo")

    assert jobs_projects == ["demo", "audit-project", "bi-project"]


def test_compute_unqueried_for_location_excludes_recently_referenced_tables(monkeypatch) -> None:
    """Return only tables absent from the recent-reference set."""
    created = datetime(2026, 4, 1, 10, 0)
    alpha_old = TableMetadata(table_id="old_table", created=created, size_bytes=10)
    alpha_recent = TableMetadata(table_id="recent_table", created=created, size_bytes=20)
    beta_only = TableMetadata(table_id="beta_table", created=created, size_bytes=30)

    def fake_get_recent_referenced_tables_by_dataset(**kwargs) -> dict[str, set[str]]:
        """Return deterministic recent-reference data."""
        assert kwargs["location"] == "US"
        assert kwargs["days"] == 30
        assert kwargs["jobs_projects"] == ["demo", "audit-project"]
        return {"alpha": {"recent_table"}, "beta": set()}

    def fake_get_all_tables_for_location(**kwargs) -> dict[str, dict[str, TableMetadata]]:
        """Return deterministic table metadata per dataset."""
        assert kwargs["location"] == "US"
        return {
            "alpha": {"old_table": alpha_old, "recent_table": alpha_recent},
            "beta": {"beta_table": beta_only},
        }

    monkeypatch.setattr(
        "bigquery_cleaner.utils.get_recent_referenced_tables_by_dataset",
        fake_get_recent_referenced_tables_by_dataset,
    )
    monkeypatch.setattr(
        "bigquery_cleaner.utils.get_all_tables_for_location",
        fake_get_all_tables_for_location,
    )

    results = compute_unqueried_for_location(
        client=DummyClient("demo"),
        location="US",
        project_dataset_pairs=[("demo", "alpha"), ("demo", "beta")],
        cfg=CleanerConfig(project="demo", datasets=["alpha", "beta"], days=30, jobs_projects=["audit-project"]),
    )

    assert results == {
        "demo.alpha": [alpha_old],
        "demo.beta": [beta_only],
    }
