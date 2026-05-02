"""Tests for core dataset cleanup operations."""

from __future__ import annotations

from datetime import datetime

from bigquery_cleaner.bq_client import TableMetadata
from bigquery_cleaner.config import CleanerConfig
from bigquery_cleaner.core_operations import (
    delete_empty_datasets,
    delete_suffixed_tables,
    get_old_tables,
    rename_unused_tables,
    revert_renamed_tables,
)


class DummyClient:
    """Provide the minimal table-listing interface used by cleanup tests."""

    def __init__(self, objects_by_dataset: dict[tuple[str, str], list[str]]) -> None:
        """Store object names keyed by fully qualified dataset."""
        self.objects_by_dataset = objects_by_dataset

    def list_tables(self, dataset_ref, max_results: int | None = None):
        """Yield table-like placeholders for the requested dataset."""
        del max_results
        yield from self.objects_by_dataset.get(
            (dataset_ref.project, dataset_ref.dataset_id),
            [],
        )


def test_get_old_tables_intersects_old_and_unqueried_metadata(monkeypatch) -> None:
    """Keep only tables that are both old and unqueried."""
    created = datetime(2026, 4, 1, 10, 0)
    modified = datetime(2026, 3, 1, 9, 0)
    unqueried_meta = TableMetadata(table_id="keep_me", created=created, size_bytes=123)
    old_meta = TableMetadata(table_id="keep_me", modified=modified, size_bytes=123)
    other_meta = TableMetadata(table_id="other_table", created=created)

    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_non_queried_tables",
        lambda cfg: {"demo.alpha": [unqueried_meta, other_meta]},
    )
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_old_modified_tables",
        lambda cfg: {"demo.alpha": [old_meta]},
    )

    results = get_old_tables(CleanerConfig(project="demo", datasets=["alpha"]))

    assert list(results) == ["demo.alpha"]
    assert results["demo.alpha"][0].table_id == "keep_me"
    assert results["demo.alpha"][0].created == created
    assert results["demo.alpha"][0].modified == modified


def test_rename_unused_tables_batches_statements_by_location(monkeypatch) -> None:
    """Rename candidate tables and hand batches to the concurrent executor."""
    cfg = CleanerConfig(project="demo", datasets=["alpha"], rename_suffix="_old", dry_run=False)
    client = object()
    rename_calls: list[tuple[object, list[tuple[str, list[str]]], int, object]] = []

    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_old_tables",
        lambda cfg: {
            "demo.alpha": [TableMetadata(table_id=f"table_{index}") for index in range(25)]
        },
    )
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_execution_context",
        lambda cfg: (client, {"US": [("demo", "alpha")]}),
    )
    monkeypatch.setattr("bigquery_cleaner.core_operations.get_ds_to_loc_map", lambda groups: {("demo", "alpha"): "US"})
    monkeypatch.setattr("bigquery_cleaner.core_operations.table_exists", lambda *args: False)
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.execute_statement_batches_concurrently",
        lambda client_arg, work_items, max_concurrent_jobs, operation_label, progress_callback=None: rename_calls.append(
            (client_arg, work_items, max_concurrent_jobs, operation_label, progress_callback)
        ),
    )

    renamed = rename_unused_tables(cfg)

    assert len(renamed["demo.alpha"]) == 25
    assert rename_calls == [
        (
            client,
            [
                (
                    "US",
                    [
                        f"ALTER TABLE `demo.alpha.table_{index}` RENAME TO `table_{index}_old`"
                        for index in range(20)
                    ],
                ),
                (
                    "US",
                    [
                        f"ALTER TABLE `demo.alpha.table_{index}` RENAME TO `table_{index}_old`"
                        for index in range(20, 25)
                    ],
                ),
            ],
            10,
            "Rename",
            None,
        )
    ]


def test_revert_renamed_tables_skips_existing_original_names(monkeypatch) -> None:
    """Skip revert operations that would overwrite an existing table."""
    cfg = CleanerConfig(project="demo", datasets=["alpha"], rename_suffix="_old", dry_run=False)
    client = object()

    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_execution_context",
        lambda cfg: (client, {"US": [("demo", "alpha")]}),
    )
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_all_tables_for_location",
        lambda *args, **kwargs: {
            "alpha": {
                "rename_me_old": TableMetadata(table_id="rename_me_old"),
                "keep_me_old": TableMetadata(table_id="keep_me_old"),
            }
        },
    )
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.table_exists",
        lambda client, project_id, dataset_id, table_id: table_id == "keep_me",
    )
    executed: list[tuple[object, list[tuple[str, list[str]]], int, str, object]] = []
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.execute_statement_batches_concurrently",
        lambda client, batch_work_items, max_concurrent_jobs, operation_label, progress_callback=None: executed.append(
            (client, batch_work_items, max_concurrent_jobs, operation_label, progress_callback)
        ),
    )

    reverted = revert_renamed_tables(cfg)

    assert reverted == {"demo.alpha": [("rename_me_old", "rename_me")]}
    assert executed == [
        (
            client,
            [("US", ["ALTER TABLE `demo.alpha.rename_me_old` RENAME TO `rename_me`"])],
            10,
            "Revert",
            None,
        )
    ]


def test_delete_suffixed_tables_collects_matching_tables(monkeypatch) -> None:
    """Delete only tables matching the configured suffix."""
    cfg = CleanerConfig(project="demo", datasets=["alpha"], rename_suffix="_old", dry_run=False)
    executed: list[tuple[object, list[tuple[str, list[str]]], int, str, object]] = []
    client = object()

    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_execution_context",
        lambda cfg: (client, {"US": [("demo", "alpha")]}),
    )
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_all_tables_for_location",
        lambda *args, **kwargs: {
            "alpha": {
                "keep_me": TableMetadata(table_id="keep_me"),
                "drop_me_old": TableMetadata(table_id="drop_me_old"),
            }
        },
    )
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.execute_statement_batches_concurrently",
        lambda client_arg, batch_work_items, max_concurrent_jobs, operation_label, progress_callback=None: executed.append(
            (client_arg, batch_work_items, max_concurrent_jobs, operation_label, progress_callback)
        ),
    )

    deleted = delete_suffixed_tables(cfg)

    assert deleted == {"demo.alpha": ["drop_me_old"]}
    assert executed == [
        (
            client,
            [("US", ["DROP TABLE `demo.alpha.drop_me_old`"])],
            10,
            "Delete",
            None,
        )
    ]


def test_delete_empty_datasets_keeps_view_only_datasets(monkeypatch) -> None:
    """Treat datasets containing only views as non-empty."""
    client = DummyClient({("demo-project", "views_only"): ["sample_view"]})
    cfg = CleanerConfig(project="demo-project", datasets=["views_only"], dry_run=True)

    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_execution_context",
        lambda cfg: (client, {"US": [("demo-project", "views_only")]}),
    )

    deleted = delete_empty_datasets(cfg)

    assert deleted == []


def test_delete_empty_datasets_marks_empty_datasets_for_deletion(monkeypatch) -> None:
    """Return fully qualified dataset IDs for empty datasets."""
    client = DummyClient({})
    cfg = CleanerConfig(project="demo-project", datasets=["empty_ds"], dry_run=True)

    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_execution_context",
        lambda cfg: (client, {"US": [("demo-project", "empty_ds")]}),
    )

    deleted = delete_empty_datasets(cfg)

    assert deleted == ["demo-project.empty_ds"]
