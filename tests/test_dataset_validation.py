import pytest

from bigquery_cleaner.bq_client import _split_dataset_ref, normalize_datasets


class DummyDataset:
    def __init__(self, dataset_id: str) -> None:
        self.dataset_id = dataset_id


class DummyClient:
    def __init__(self, project: str) -> None:
        self.project = project

    def list_datasets(self) -> list[DummyDataset]:
        return [DummyDataset("alpha"), DummyDataset("beta")]


def test_split_dataset_ref_rejects_fully_qualified_datasets() -> None:
    with pytest.raises(ValueError, match="unqualified dataset name"):
        _split_dataset_ref("other_project.analytics", "test-project")


def test_split_dataset_ref_requires_project_for_explicit_dataset_inputs() -> None:
    with pytest.raises(ValueError, match="Project must be provided"):
        _split_dataset_ref("analytics", None)


def test_normalize_datasets_uses_single_project_for_dataset_names() -> None:
    client = DummyClient(project="test-project")

    pairs = normalize_datasets(
        client=client,
        datasets=["alpha", "beta"],
        default_project="test-project",
        exclude_datasets=["beta"],
    )

    assert pairs == [("test-project", "alpha")]


def test_normalize_datasets_lists_all_datasets_for_client_project() -> None:
    client = DummyClient(project="test-project")

    pairs = normalize_datasets(
        client=client,
        datasets=None,
        default_project="test-project",
        exclude_datasets=["beta"],
    )

    assert pairs == [("test-project", "alpha")]
