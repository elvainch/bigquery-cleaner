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
    unqueried_meta = TableMetadata(
        table_id="keep_me",
        created=created,
        size_bytes=123,
        object_type="BASE TABLE",
    )
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


def test_get_old_tables_excludes_unsupported_object_types(monkeypatch) -> None:
    """Exclude materialized views from unused-table results and rename planning."""
    base_table = TableMetadata(table_id="base_table", object_type="BASE TABLE")
    materialized_view = TableMetadata(
        table_id="materialized_view",
        object_type="MATERIALIZED VIEW",
    )

    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_non_queried_tables",
        lambda cfg: {"demo.alpha": [base_table, materialized_view]},
    )
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_old_modified_tables",
        lambda cfg: {
            "demo.alpha": [
                TableMetadata(table_id="base_table"),
                TableMetadata(table_id="materialized_view"),
            ]
        },
    )

    results = get_old_tables(CleanerConfig(project="demo", datasets=["alpha"]))

    assert [metadata.table_id for metadata in results["demo.alpha"]] == ["base_table"]


def test_rename_unused_tables_batches_statements_by_location(monkeypatch) -> None:
    """Rename candidate tables and hand batches to the concurrent executor."""
    cfg = CleanerConfig(project="demo", datasets=["alpha"], rename_suffix="_old", dry_run=False)
    client = object()
    rename_calls: list[tuple[object, list[tuple[str, list[str]]], int, object]] = []

    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_old_tables",
        lambda cfg: {
            "demo.alpha": [
                TableMetadata(table_id=f"table_{index}", object_type="BASE TABLE")
                for index in range(25)
            ]
        },
    )
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_execution_context",
        lambda cfg: (client, {"US": [("demo", "alpha")]}),
    )
    monkeypatch.setattr("bigquery_cleaner.core_operations.get_ds_to_loc_map", lambda groups: {("demo", "alpha"): "US"})
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_all_tables_for_location",
        lambda *args, **kwargs: {"alpha": {}},
    )
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


def test_rename_unused_tables_skips_targets_that_already_exist(monkeypatch) -> None:
    """Skip rename statements whose target table name already exists."""
    cfg = CleanerConfig(project="demo", datasets=["alpha"], rename_suffix="_old", dry_run=False)
    client = object()
    executed: list[tuple[object, list[tuple[str, list[str]]], int, object, object]] = []

    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_old_tables",
        lambda cfg: {
            "demo.alpha": [
                TableMetadata(table_id="keep_me", object_type="BASE TABLE"),
                TableMetadata(table_id="rename_me", object_type="BASE TABLE"),
            ]
        },
    )
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_execution_context",
        lambda cfg: (client, {"US": [("demo", "alpha")]}),
    )
    monkeypatch.setattr("bigquery_cleaner.core_operations.get_ds_to_loc_map", lambda groups: {("demo", "alpha"): "US"})
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_all_tables_for_location",
        lambda *args, **kwargs: {
            "alpha": {
                "keep_me_old": TableMetadata(table_id="keep_me_old"),
            }
        },
    )
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.execute_statement_batches_concurrently",
        lambda client_arg, work_items, max_concurrent_jobs, operation_label, progress_callback=None: executed.append(
            (client_arg, work_items, max_concurrent_jobs, operation_label, progress_callback)
        ),
    )

    renamed = rename_unused_tables(cfg)

    assert renamed == {"demo.alpha": [("rename_me", "rename_me_old")]}
    assert executed == [
        (
            client,
            [("US", ["ALTER TABLE `demo.alpha.rename_me` RENAME TO `rename_me_old`"])],
            10,
            "Rename",
            None,
        )
    ]


def test_rename_unused_tables_skips_unsupported_object_types(monkeypatch) -> None:
    """Exclude views and external tables from ALTER TABLE rename statements."""
    cfg = CleanerConfig(project="demo", datasets=["alpha"], rename_suffix="_old", dry_run=False)
    client = object()
    executed: list[tuple[object, list[tuple[str, list[str]]], int, object, object]] = []

    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_old_tables",
        lambda cfg: {
            "demo.alpha": [
                TableMetadata(table_id="regular_table", object_type="BASE TABLE"),
                TableMetadata(table_id="snapshot", object_type="SNAPSHOT"),
                TableMetadata(table_id="materialized", object_type="MATERIALIZED VIEW"),
                TableMetadata(table_id="view", object_type="VIEW"),
                TableMetadata(table_id="external", object_type="EXTERNAL"),
            ]
        },
    )
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_execution_context",
        lambda cfg: (client, {"US": [("demo", "alpha")]}),
    )
    monkeypatch.setattr("bigquery_cleaner.core_operations.get_ds_to_loc_map", lambda groups: {("demo", "alpha"): "US"})
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_all_tables_for_location",
        lambda *args, **kwargs: {"alpha": {}},
    )
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.execute_statement_batches_concurrently",
        lambda client_arg, work_items, max_concurrent_jobs, operation_label, progress_callback=None: executed.append(
            (client_arg, work_items, max_concurrent_jobs, operation_label, progress_callback)
        ),
    )

    renamed = rename_unused_tables(cfg)

    assert renamed == {
        "demo.alpha": [
            ("regular_table", "regular_table_old"),
            ("snapshot", "snapshot_old"),
        ]
    }
    assert executed[0][1] == [
        (
            "US",
            [
                "ALTER TABLE `demo.alpha.regular_table` RENAME TO `regular_table_old`",
                "ALTER TABLE `demo.alpha.snapshot` RENAME TO `snapshot_old`",
            ],
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
                "keep_me": TableMetadata(table_id="keep_me", object_type="BASE TABLE"),
                "rename_me_old": TableMetadata(
                    table_id="rename_me_old",
                    object_type="BASE TABLE",
                ),
                "keep_me_old": TableMetadata(
                    table_id="keep_me_old",
                    object_type="BASE TABLE",
                ),
            }
        },
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
                "keep_me": TableMetadata(table_id="keep_me", object_type="BASE TABLE"),
                "drop_me_old": TableMetadata(
                    table_id="drop_me_old",
                    object_type="BASE TABLE",
                ),
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


def test_revert_renamed_tables_skips_unsupported_object_types(monkeypatch) -> None:
    """Do not submit rename statements for suffixed views or external tables."""
    cfg = CleanerConfig(project="demo", datasets=["alpha"], rename_suffix="_old", dry_run=False)
    client = object()
    executed: list[tuple[object, list[tuple[str, list[str]]], int, str, object]] = []

    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_execution_context",
        lambda cfg: (client, {"US": [("demo", "alpha")]}),
    )
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_all_tables_for_location",
        lambda *args, **kwargs: {
            "alpha": {
                "base_old": TableMetadata(table_id="base_old", object_type="BASE TABLE"),
                "view_old": TableMetadata(table_id="view_old", object_type="VIEW"),
                "external_old": TableMetadata(table_id="external_old", object_type="EXTERNAL"),
            }
        },
    )
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.execute_statement_batches_concurrently",
        lambda client_arg, work_items, max_concurrent_jobs, operation_label, progress_callback=None: executed.append(
            (client_arg, work_items, max_concurrent_jobs, operation_label, progress_callback)
        ),
    )

    reverted = revert_renamed_tables(cfg)

    assert reverted == {"demo.alpha": [("base_old", "base")]}
    assert executed[0][1] == [("US", ["ALTER TABLE `demo.alpha.base_old` RENAME TO `base`"])]


def test_delete_suffixed_tables_uses_type_specific_statements(monkeypatch) -> None:
    """Delete only managed object types with their required DDL syntax."""
    cfg = CleanerConfig(project="demo", datasets=["alpha"], rename_suffix="_old", dry_run=False)
    client = object()
    executed: list[tuple[object, list[tuple[str, list[str]]], int, str, object]] = []

    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_execution_context",
        lambda cfg: (client, {"US": [("demo", "alpha")]}),
    )
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.get_all_tables_for_location",
        lambda *args, **kwargs: {
            "alpha": {
                "base_old": TableMetadata(table_id="base_old", object_type="BASE TABLE"),
                "snapshot_old": TableMetadata(table_id="snapshot_old", object_type="SNAPSHOT"),
                "materialized_old": TableMetadata(
                    table_id="materialized_old",
                    object_type="MATERIALIZED VIEW",
                ),
                "view_old": TableMetadata(table_id="view_old", object_type="VIEW"),
            }
        },
    )
    monkeypatch.setattr(
        "bigquery_cleaner.core_operations.execute_statement_batches_concurrently",
        lambda client_arg, work_items, max_concurrent_jobs, operation_label, progress_callback=None: executed.append(
            (client_arg, work_items, max_concurrent_jobs, operation_label, progress_callback)
        ),
    )

    deleted = delete_suffixed_tables(cfg)

    assert deleted == {"demo.alpha": ["base_old", "snapshot_old"]}
    assert executed[0][1] == [
        (
            "US",
            [
                "DROP TABLE `demo.alpha.base_old`",
                "DROP SNAPSHOT TABLE `demo.alpha.snapshot_old`",
            ],
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
