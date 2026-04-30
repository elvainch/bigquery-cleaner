"""Tests for core dataset cleanup operations."""

from __future__ import annotations

from bigquery_cleaner.config import CleanerConfig
from bigquery_cleaner.core_operations import delete_empty_datasets


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
