"""Tests for BigQuery client helper functions."""

from __future__ import annotations

from datetime import datetime

import pytest
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

from bigquery_cleaner.bq_client import (
    TableMetadata,
    delete_dataset,
    delete_tables,
    get_all_tables_for_location,
    get_client,
    get_old_modified_tables_for_location,
    get_recent_referenced_tables_by_dataset,
    group_datasets_by_location,
    list_datasets,
    rename_tables,
    table_exists,
)


class DummyDataset:
    """Represent a BigQuery dataset in tests."""

    def __init__(self, dataset_id: str, location: str = "US") -> None:
        """Store the dataset metadata."""
        self.dataset_id = dataset_id
        self.location = location


class DummyQueryJob:
    """Provide a query result wrapper with a ``result`` method."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        """Store rows to be returned by ``result``."""
        self.rows = rows

    def result(self) -> list[dict[str, object]]:
        """Return the stored rows."""
        return self.rows


class RecordingClient:
    """Capture query and dataset operations for tests."""

    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        """Store default rows and initialize call history."""
        self.rows = rows or []
        self.queries: list[tuple[str, object, str | None]] = []
        self.deleted_datasets: list[tuple[object, bool, bool]] = []

    def query(self, query: str, job_config=None, location: str | None = None) -> DummyQueryJob:
        """Record the query and return the configured rows."""
        self.queries.append((query, job_config, location))
        return DummyQueryJob(self.rows)

    def delete_dataset(self, dataset_ref, delete_contents: bool, not_found_ok: bool) -> None:
        """Record dataset deletion requests."""
        self.deleted_datasets.append((dataset_ref, delete_contents, not_found_ok))


def test_get_client_passes_project_when_provided(monkeypatch) -> None:
    """Construct the BigQuery client with the configured project."""
    captured: list[str | None] = []

    def fake_client(project=None):
        """Record the configured project and return a fake client object."""
        captured.append(project)
        return {"project": project}

    monkeypatch.setattr("bigquery_cleaner.bq_client.bigquery.Client", fake_client)

    client = get_client("demo-project")

    assert client == {"project": "demo-project"}
    assert captured == ["demo-project"]


def test_list_datasets_returns_dataset_ids(monkeypatch) -> None:
    """Return only dataset identifiers from the API response."""
    class ListClient:
        """Provide a static dataset listing."""

        def list_datasets(self) -> list[DummyDataset]:
            """Return fixed test datasets."""
            return [DummyDataset("alpha"), DummyDataset("beta")]

    client = ListClient()

    def fake_get_client(project_id: str | None) -> ListClient:
        """Return the prepared list client regardless of project."""
        del project_id
        return client

    monkeypatch.setattr("bigquery_cleaner.bq_client.get_client", fake_get_client)

    assert list_datasets("demo-project") == ["alpha", "beta"]


def test_group_datasets_by_location_uses_dataset_metadata() -> None:
    """Group datasets under their reported BigQuery locations."""

    class DatasetClient:
        """Return dataset metadata with per-dataset locations."""

        def get_dataset(self, dataset_ref):
            """Return a dataset object with a deterministic location."""
            location = "EU" if dataset_ref.dataset_id == "beta" else "US"
            return DummyDataset(dataset_ref.dataset_id, location=location)

    groups = group_datasets_by_location(
        DatasetClient(),
        [("demo", "alpha"), ("demo", "beta"), ("demo", "gamma")],
    )

    assert dict(groups) == {
        "US": [("demo", "alpha"), ("demo", "gamma")],
        "EU": [("demo", "beta")],
    }


def test_get_recent_referenced_tables_by_dataset_returns_table_sets() -> None:
    """Aggregate referenced table IDs by dataset."""
    rows = [
        {"dataset_id": "alpha", "table_id": "table_one"},
        {"dataset_id": "alpha", "table_id": "table_two"},
        {"dataset_id": "beta", "table_id": "table_three"},
    ]
    client = RecordingClient(rows)

    result = get_recent_referenced_tables_by_dataset(
        client=client,
        location="US",
        project_dataset_pairs=[("demo", "alpha"), ("demo", "beta")],
        days=30,
    )

    assert result == {
        "alpha": {"table_one", "table_two"},
        "beta": {"table_three"},
    }
    query, job_config, location = client.queries[0]
    assert "INFORMATION_SCHEMA.JOBS" in query
    assert location == "US"
    assert isinstance(job_config, bigquery.QueryJobConfig)


def test_get_all_tables_for_location_returns_metadata_by_dataset() -> None:
    """Build per-dataset metadata maps from query rows."""
    created = datetime(2026, 4, 1, 12, 0)
    rows = [
        {"dataset_id": "alpha", "table_id": "table_one", "creation_time": created, "total_bytes": 128},
        {"dataset_id": "beta", "table_id": "table_two", "creation_time": created, "total_bytes": 256},
    ]
    client = RecordingClient(rows)

    result = get_all_tables_for_location(
        client=client,
        location="US",
        project_dataset_pairs=[("demo", "alpha"), ("demo", "beta")],
    )

    assert result["alpha"]["table_one"] == TableMetadata(
        table_id="table_one",
        created=created,
        size_bytes=128,
    )
    assert result["beta"]["table_two"] == TableMetadata(
        table_id="table_two",
        created=created,
        size_bytes=256,
    )


def test_get_old_modified_tables_for_location_formats_fully_qualified_keys() -> None:
    """Return old-table metadata keyed by fully qualified dataset name."""
    modified = datetime(2026, 3, 1, 8, 30)
    rows = [
        {
            "dataset_id": "alpha",
            "table_id": "stale_table",
            "storage_last_modified_time": modified,
            "total_bytes": 64,
        }
    ]
    client = RecordingClient(rows)

    result = get_old_modified_tables_for_location(
        client=client,
        location="US",
        project_dataset_pairs=[("demo", "alpha"), ("demo", "beta")],
        days=30,
    )

    assert result == {
        "demo.alpha": [TableMetadata(table_id="stale_table", modified=modified, size_bytes=64)],
        "demo.beta": [],
    }


@pytest.mark.parametrize(("executor", "statement"), [(rename_tables, "ALTER TABLE foo RENAME TO bar"), (delete_tables, "DROP TABLE foo")])
def test_statement_executors_join_sql_and_run_query(executor, statement: str) -> None:
    """Join batched statements into a single query."""
    client = RecordingClient()

    executor(client, [statement, "SELECT 1"], "US")

    assert client.queries == [(f"{statement};\nSELECT 1", None, "US")]


@pytest.mark.parametrize("executor", [rename_tables, delete_tables])
def test_statement_executors_skip_empty_batches(executor) -> None:
    """Avoid query execution when no statements are provided."""
    client = RecordingClient()

    executor(client, [], "US")

    assert client.queries == []


def test_delete_dataset_passes_expected_flags() -> None:
    """Delete datasets without deleting their contents implicitly."""
    client = RecordingClient()

    delete_dataset(client, "demo", "alpha", not_found_ok=False)

    dataset_ref, delete_contents, not_found_ok = client.deleted_datasets[0]
    assert dataset_ref.project == "demo"
    assert dataset_ref.dataset_id == "alpha"
    assert delete_contents is False
    assert not_found_ok is False


@pytest.mark.parametrize("raises_not_found", [False, True])
def test_table_exists_reports_presence(raises_not_found: bool) -> None:
    """Return table presence based on the BigQuery API result."""

    class TableClient:
        """Return or fail on table lookup based on the parametrized scenario."""

        def get_table(self, table_ref):
            """Return the table when present or raise NotFound otherwise."""
            if raises_not_found:
                raise NotFound("missing")
            return table_ref

    assert table_exists(TableClient(), "demo", "alpha", "table_one") is (not raises_not_found)
