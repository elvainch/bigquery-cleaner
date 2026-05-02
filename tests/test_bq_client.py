"""Tests for BigQuery client helper functions."""

from __future__ import annotations

from datetime import datetime
from threading import Event, Lock
from time import sleep

import pytest
from google.cloud import bigquery
from google.cloud.exceptions import NotFound

from bigquery_cleaner.bq_client import (
    TableMetadata,
    chunk_statements,
    delete_dataset,
    execute_statement_batches_concurrently,
    get_all_tables_for_location,
    get_client,
    get_old_modified_tables_for_location,
    get_recent_referenced_tables_by_dataset,
    group_datasets_by_location,
    list_datasets,
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
        self.query_handler = None

    def query(self, query: str, job_config=None, location: str | None = None) -> DummyQueryJob:
        """Record the query and return the configured rows."""
        self.queries.append((query, job_config, location))
        if self.query_handler is not None:
            return self.query_handler(query, job_config, location)
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
    """Aggregate referenced table IDs across multiple jobs projects."""
    client = RecordingClient()

    def query_handler(query: str, job_config, location: str | None) -> DummyQueryJob:
        """Return rows based on which jobs project is being queried."""
        del job_config
        assert location == "US"
        if "`audit-project`.`region-us`.INFORMATION_SCHEMA.JOBS" in query:
            return DummyQueryJob(
                [
                    {"dataset_id": "alpha", "table_id": "table_one"},
                    {"dataset_id": "beta", "table_id": "table_three"},
                ]
            )
        if "`bi-project`.`region-us`.INFORMATION_SCHEMA.JOBS" in query:
            return DummyQueryJob(
                [
                    {"dataset_id": "alpha", "table_id": "table_two"},
                    {"dataset_id": "beta", "table_id": "table_three"},
                ]
            )
        raise AssertionError(f"Unexpected query: {query}")

    client.query_handler = query_handler

    result = get_recent_referenced_tables_by_dataset(
        client=client,
        location="US",
        project_dataset_pairs=[("demo", "alpha"), ("demo", "beta")],
        days=30,
        jobs_projects=["audit-project", "bi-project"],
    )

    assert result == {
        "alpha": {"table_one", "table_two"},
        "beta": {"table_three"},
    }
    assert len(client.queries) == 2
    for query, job_config, location in client.queries:
        assert "INFORMATION_SCHEMA.JOBS" in query
        assert location == "US"
        assert isinstance(job_config, bigquery.QueryJobConfig)


def test_get_recent_referenced_tables_by_dataset_raises_when_one_jobs_project_fails() -> None:
    """Abort the scan when one jobs project cannot be queried."""
    client = RecordingClient()

    def query_handler(query: str, job_config, location: str | None) -> DummyQueryJob:
        """Raise only for the second jobs project to simulate missing access."""
        del job_config, location
        if "`audit-project`.`region-us`.INFORMATION_SCHEMA.JOBS" in query:
            return DummyQueryJob([{"dataset_id": "alpha", "table_id": "table_one"}])
        raise RuntimeError("permission denied")

    client.query_handler = query_handler

    with pytest.raises(RuntimeError, match=r"Failed to query INFORMATION_SCHEMA\.JOBS for project 'bi-project'"):
        get_recent_referenced_tables_by_dataset(
            client=client,
            location="US",
            project_dataset_pairs=[("demo", "alpha")],
            days=30,
            jobs_projects=["audit-project", "bi-project"],
        )


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


def test_execute_statement_batches_concurrently_joins_sql_and_runs_query() -> None:
    """Join one statement batch into a single submitted query."""
    client = RecordingClient()

    execute_statement_batches_concurrently(
        client,
        [("US", ["ALTER TABLE foo RENAME TO bar", "SELECT 1"])],
        max_concurrent_jobs=1,
        operation_label="Rename",
    )

    assert client.queries == [("ALTER TABLE foo RENAME TO bar;\nSELECT 1", None, "US")]


def test_execute_statement_batches_concurrently_skips_empty_batches() -> None:
    """Avoid query execution when no batch work items are provided."""
    client = RecordingClient()

    execute_statement_batches_concurrently(
        client,
        [],
        max_concurrent_jobs=1,
        operation_label="Rename",
    )

    assert client.queries == []


@pytest.mark.parametrize(
    ("statement_count", "expected_batch_lengths"),
    [
        (0, []),
        (1, [1]),
        (20, [20]),
        (21, [20, 1]),
        (41, [20, 20, 1]),
    ],
)
def test_chunk_statements_splits_batches(statement_count: int, expected_batch_lengths: list[int]) -> None:
    """Split rename statements into deterministic fixed-size batches."""
    statements = [f"ALTER TABLE table_{index}" for index in range(statement_count)]

    batches = chunk_statements(statements, 20)

    assert [len(batch) for batch in batches] == expected_batch_lengths


def test_execute_statement_batches_concurrently_joins_batches_and_preserves_locations() -> None:
    """Submit each statement batch as one query with its own location."""
    client = RecordingClient()

    execute_statement_batches_concurrently(
        client,
        [
            ("US", ["ALTER TABLE one", "ALTER TABLE two"]),
            ("EU", ["ALTER TABLE three"]),
        ],
        max_concurrent_jobs=2,
        operation_label="Rename",
    )

    assert client.queries == [
        ("ALTER TABLE one;\nALTER TABLE two", None, "US"),
        ("ALTER TABLE three", None, "EU"),
    ]


def test_execute_statement_batches_concurrently_limits_in_flight_jobs() -> None:
    """Keep the number of concurrent jobs under the configured cap."""
    client = RecordingClient()
    active_jobs = 0
    max_seen = 0
    lock = Lock()

    def query_handler(query: str, job_config, location: str | None) -> DummyQueryJob:
        """Track how many query jobs are running at once."""
        del query, job_config, location

        class BlockingQueryJob(DummyQueryJob):
            """Sleep briefly so concurrent jobs overlap."""

            def result(self) -> list[dict[str, object]]:
                """Record overlap, sleep briefly, and then finish."""
                nonlocal active_jobs, max_seen
                with lock:
                    active_jobs += 1
                    max_seen = max(max_seen, active_jobs)
                sleep(0.05)
                with lock:
                    active_jobs -= 1
                return []

        return BlockingQueryJob([])

    client.query_handler = query_handler

    statements = [("US", [f"ALTER TABLE table_{index}"]) for index in range(4)]
    execute_statement_batches_concurrently(
        client,
        statements,
        max_concurrent_jobs=2,
        operation_label="Rename",
    )

    assert max_seen == 2


def test_execute_statement_batches_concurrently_reports_batch_progress() -> None:
    """Report cumulative table progress after each finished batch."""
    client = RecordingClient()
    progress_updates: list[tuple[int, int]] = []

    execute_statement_batches_concurrently(
        client,
        [
            ("US", ["ALTER TABLE one", "ALTER TABLE two"]),
            ("US", ["ALTER TABLE three"]),
        ],
        max_concurrent_jobs=2,
        operation_label="Rename",
        progress_callback=lambda completed, total: progress_updates.append((completed, total)),
    )

    assert progress_updates[0] == (0, 3)
    assert progress_updates[-1] == (3, 3)
    assert progress_updates[1:] in [[(2, 3), (3, 3)], [(1, 3), (3, 3)]]


def test_execute_statement_batches_concurrently_stops_scheduling_after_failure() -> None:
    """Do not submit new statement batches after the first batch failure."""
    client = RecordingClient()
    release_event = Event()

    def query_handler(query: str, job_config, location: str | None) -> DummyQueryJob:
        """Fail the first batch and block the second so no third batch starts."""
        del job_config
        if "fail_batch" in query:
            class FailingQueryJob(DummyQueryJob):
                """Raise when waiting on the failed batch."""

                def result(self) -> list[dict[str, object]]:
                    raise RuntimeError("boom")

            return FailingQueryJob([])

        class BlockingQueryJob(DummyQueryJob):
            """Simulate an in-flight job that eventually completes."""

            def result(self) -> list[dict[str, object]]:
                release_event.wait(timeout=2)
                return []

        return BlockingQueryJob([])

    client.query_handler = query_handler

    from threading import Thread

    releaser = Thread(target=lambda: (sleep(0.05), release_event.set()))
    releaser.start()

    with pytest.raises(RuntimeError, match="Rename batch failed in location 'US'"):
        execute_statement_batches_concurrently(
            client,
            [
                ("US", ["ALTER TABLE fail_batch"]),
                ("US", ["ALTER TABLE second_batch"]),
                ("US", ["ALTER TABLE third_batch"]),
            ],
            max_concurrent_jobs=2,
            operation_label="Rename",
        )

    releaser.join(timeout=2)

    assert len(client.queries) == 2


def test_execute_statement_batches_concurrently_uses_operation_label_in_failures() -> None:
    """Include the operation label in the batch failure message."""
    client = RecordingClient()

    def query_handler(query: str, job_config, location: str | None) -> DummyQueryJob:
        """Fail every submitted batch."""
        del query, job_config, location

        class FailingQueryJob(DummyQueryJob):
            """Raise when the job result is awaited."""

            def result(self) -> list[dict[str, object]]:
                raise RuntimeError("boom")

        return FailingQueryJob([])

    client.query_handler = query_handler

    with pytest.raises(RuntimeError, match="Delete batch failed in location 'US'"):
        execute_statement_batches_concurrently(
            client,
            [("US", ["DROP TABLE one"])],
            max_concurrent_jobs=1,
            operation_label="Delete",
        )


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
