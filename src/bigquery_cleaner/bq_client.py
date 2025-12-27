"""BigQuery client helpers for dataset and table discovery."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from google.cloud import bigquery
from google.cloud.exceptions import NotFound

from . import logger
from .queries.sql import (
    list_all_tables_across_datasets_sql,
    list_old_tables_across_datasets_sql,
    recent_references_across_datasets_sql,
)


@dataclass
class TableMetadata:
    """Detailed information about a BigQuery table."""
    table_id: str
    created: datetime | None = None
    modified: datetime | None = None
    size_bytes: int | None = None


def get_client(project_id: str | None = None) -> bigquery.Client:
    """Return an authenticated BigQuery client.

    Uses Application Default Credentials (ADC). Optionally pin a project.

    Args:
        project_id: Optional GCP project ID to pin the client to.

    Returns:
        An authenticated bigquery.Client instance.
    """
    return bigquery.Client(project=project_id) if project_id else bigquery.Client()


def _split_dataset_ref(dataset: str, project_id: str | None) -> tuple[str, str]:
    """Split a dataset ref into (project, dataset), using fallback project if needed.

    Args:
        dataset: Dataset ID string (e.g., "dataset" or "project.dataset").
        project_id: Fallback project ID if the dataset string is not project-qualified.

    Returns:
        A tuple of (project_id, dataset_id).

    Raises:
        ValueError: If dataset is not qualified and no project_id is provided.
    """
    if "." in dataset:
        project_id, dataset_id = dataset.split(".", 1)
        return project_id, dataset_id
    if not project_id:
        raise ValueError(
            "Dataset must be 'project.dataset' or provide project via --project"
        )
    return project_id, dataset


def list_datasets(project_id: str | None) -> list[str]:
    """Return dataset IDs available in the given or default project.

    Args:
        project_id: Optional GCP project ID.

    Returns:
        A list of dataset IDs.
    """
    client = get_client(project_id)
    # Fetch and return all dataset IDs from the project.
    return [dataset.dataset_id for dataset in client.list_datasets()]  # type: ignore[attr-defined]


def list_tables(dataset: str, project_id: str | None) -> list[str]:
    """Return table IDs for the given dataset (project-qualified or not).

    Args:
        dataset: Dataset ID string (e.g., "dataset" or "project.dataset").
        project_id: Fallback project ID if the dataset string is not project-qualified.

    Returns:
        A list of table IDs in the dataset.
    """
    project_id, dataset_id = _split_dataset_ref(dataset, project_id)
    client = get_client(project_id)
    # Fetch and return all table IDs from the specified dataset.
    return [table.table_id for table in client.list_tables(dataset_id)]  # type: ignore[attr-defined]


def normalize_datasets(
    client: bigquery.Client,
    datasets: list[str] | None,
    default_project: str,
    exclude_datasets: list[str] | None = None,
) -> list[tuple[str, str]]:
    """Normalize dataset inputs to (project, dataset) project_dataset_pairs.

    If ``datasets`` is None/empty, list all datasets in the client's project.
    Filters out any datasets present in ``exclude_datasets``.

    Args:
        client: BigQuery client instance.
        datasets: Optional list of dataset strings to normalize.
        default_project: Fallback project ID for non-qualified datasets.
        exclude_datasets: Optional list of dataset strings to exclude from the result.

    Returns:
        A list of (project_id, dataset_id) tuples.
    """
    project_dataset_pairs: list[tuple[str, str]] = []
    if datasets and len(datasets) > 0:
        for dataset in datasets:
            project_id, dataset_id = _split_dataset_ref(dataset, default_project)
            project_dataset_pairs.append((project_id, dataset_id))
    else:
        # Generate a list of (project, dataset) tuples for all datasets in the project.
        project_dataset_pairs = [
            (client.project, dataset.dataset_id) for dataset in client.list_datasets()
        ]

    if exclude_datasets:
        # Normalize exclude list for comparison
        excluded = {
            _split_dataset_ref(excluded_ds, default_project) for excluded_ds in exclude_datasets
        }
        # Filter out the datasets that are marked for exclusion.
        project_dataset_pairs = [
            pair for pair in project_dataset_pairs if pair not in excluded
        ]

    return project_dataset_pairs


def group_datasets_by_location(
    client: bigquery.Client, project_dataset_pairs: Iterable[tuple[str, str]]
) -> defaultdict[str, list[tuple[str, str]]]:
    """Group (project, dataset) project_dataset_pairs by dataset location using metadata lookups.

    Args:
        client: BigQuery client instance.
        project_dataset_pairs: Iterable of (project_id, dataset_id) tuples.

    Returns:
        A dictionary mapping location strings to lists of (project_id, dataset_id) tuples.
    """
    groups: defaultdict[str, list[tuple[str, str]]] = defaultdict(list)
    for project_id, dataset_id in project_dataset_pairs:
        ds_obj = client.get_dataset(bigquery.DatasetReference(project_id, dataset_id))
        groups[ds_obj.location].append((project_id, dataset_id))
    return groups


def get_recent_referenced_tables_by_dataset(
    client: bigquery.Client,
    location: str,
    project_dataset_pairs: list[tuple[str, str]],
    days: int,
) -> defaultdict[str, set[str]]:
    """Return recent referenced tables grouped by dataset for a location.

    Falls back to empty sets on failure.

    Args:
        client: BigQuery client instance.
        location: Dataset location (e.g., "US").
        project_dataset_pairs: List of (project_id, dataset_id) tuples.
        days: Lookback window in days.

    Returns:
        A dictionary mapping dataset ID to a set of table IDs referenced in queries.
    """
    region_dataset = f"region-{location.lower()}"
    project = project_dataset_pairs[0][0]
    # Extract unique dataset IDs from the project-dataset pairs.
    dataset_ids = sorted({dataset_id for _, dataset_id in project_dataset_pairs})

    query = recent_references_across_datasets_sql(project, region_dataset)
    cfg = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("days", "INT64", days),
            bigquery.ScalarQueryParameter("project_id", "STRING", project),
            bigquery.ArrayQueryParameter("dataset_ids", "STRING", dataset_ids),
        ]
    )
    out: defaultdict[str, set[str]] = defaultdict(set)
    try:
        for row in client.query(query, job_config=cfg, location=location).result():
            out[row["dataset_id"]].add(row["table_id"])
    except Exception as err:
        logger.warning("Recent references query failed for location %s: %s", location, err)
        out = defaultdict(set)
        raise err
    return out


def get_all_tables_for_location(
    client: bigquery.Client,
    location: str,
    project_dataset_pairs: list[tuple[str, str]],
) -> defaultdict[str, dict[str, TableMetadata]]:
    """Return all tables grouped by dataset for a location.

    Falls back to listing per dataset via API on failure.

    Args:
        client: BigQuery client instance.
        location: Dataset location (e.g., "US").
        project_dataset_pairs: List of (project_id, dataset_id) tuples.

    Returns:
        A dictionary mapping dataset ID to a dict of table_id -> TableMetadata.
    """
    region_dataset = f"region-{location.lower()}"
    project = project_dataset_pairs[0][0]
    # Extract unique dataset IDs from the project-dataset pairs.
    dataset_ids = sorted({dataset_id for _, dataset_id in project_dataset_pairs})

    query = list_all_tables_across_datasets_sql(project, region_dataset)
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("dataset_ids", "STRING", dataset_ids)]
    )
    out: defaultdict[str, dict[str, TableMetadata]] = defaultdict(dict)
    try:
        for row in client.query(query, job_config=cfg, location=location).result():
            table_id = row["table_id"]
            ds_id = row["dataset_id"]
            out[ds_id][table_id] = TableMetadata(
                table_id=table_id,
                created=row.get("creation_time"),
                size_bytes=row.get("total_bytes"),
            )
    except Exception as err:
        logger.warning("Batched table list query failed for location %s: %s. Falling back to per-dataset API calls.", location, err)
        raise err

    return out


def get_old_modified_tables_for_location(
    client: bigquery.Client,
    location: str,
    project_dataset_pairs: list[tuple[str, str]],
    days: int,
) -> dict[str, list[TableMetadata]]:
    """Return tables modified before the lookback window for a location.

    Falls back to per-dataset queries on failure.

    Args:
        client: BigQuery client instance.
        location: Dataset location (e.g., "US").
        project_dataset_pairs: List of (project_id, dataset_id) tuples.
        days: Lookback window in days.

    Returns:
        Mapping: ``project.dataset`` -> [TableMetadata ...]
    """
    region_dataset = f"region-{location.lower()}"
    project = project_dataset_pairs[0][0]
    # Extract unique dataset IDs from the project-dataset pairs.
    dataset_ids = sorted({dataset_id for _, dataset_id in project_dataset_pairs})

    query = list_old_tables_across_datasets_sql(project, region_dataset)
    cfg = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("days", "INT64", days),
            bigquery.ArrayQueryParameter("dataset_ids", "STRING", dataset_ids),
        ]
    )
    all_by_ds: defaultdict[str, dict[str, TableMetadata]] = defaultdict(dict)
    try:
        for row in client.query(query, job_config=cfg, location=location).result():
            table_id = row["table_id"]
            ds_id = row["dataset_id"]
            all_by_ds[ds_id][table_id] = TableMetadata(
                table_id=table_id,
                modified=row.get("storage_last_modified_time"),
                size_bytes=row.get("total_bytes"),
            )
    except Exception as err:
        logger.warning(
            "Batched old-tables query failed for location %s: %s.",
            location,
            err,
        )
        raise err

    # Format results
    out: dict[str, list[TableMetadata]] = {}
    for project_id, dataset_id in project_dataset_pairs:
        key = f"{project_id}.{dataset_id}"
        ds_tables = all_by_ds.get(dataset_id, {})
        # Convert the dictionary of table metadata into a sorted list.
        out[key] = [
            ds_tables[table_id] for table_id in sorted(ds_tables.keys())
        ]
    return out


def rename_table(
    client: bigquery.Client,
    project_id: str,
    dataset_id: str,
    table_id: str,
    new_table_id: str,
    location: str,
) -> None:
    """Rename a BigQuery table using ALTER TABLE ... RENAME TO ...

    Args:
        client: BigQuery client instance.
        project_id: GCP project ID.
        dataset_id: Dataset ID.
        table_id: Original table name.
        new_table_id: New table name.
        location: Dataset location.
    """
    sql = f"ALTER TABLE `{project_id}.{dataset_id}.{table_id}` RENAME TO `{new_table_id}`"
    client.query(sql, location=location).result()


def rename_tables(
    client: bigquery.Client,
    statements: list[str],
    location: str,
) -> None:
    """Execute multiple SQL statements in a single query.

    Args:
        client: BigQuery client instance.
        statements: List of SQL statements to execute.
        location: Dataset location.
    """
    if not statements:
        return
    sql = ";\n".join(statements)
    client.query(sql, location=location).result()


def delete_tables(
    client: bigquery.Client,
    statements: list[str],
    location: str,
) -> None:
    """Execute multiple DROP TABLE statements in a single query.

    Args:
        client: BigQuery client instance.
        statements: List of SQL statements to execute.
        location: Dataset location.
    """
    if not statements:
        return
    sql = ";\n".join(statements)
    client.query(sql, location=location).result()


def delete_dataset(
    client: bigquery.Client, project_id: str, dataset_id: str, not_found_ok: bool = True
) -> None:
    """Delete a BigQuery dataset.

    Args:
        client: BigQuery client instance.
        project_id: GCP project ID.
        dataset_id: Dataset ID.
        not_found_ok: If True, do not raise error if dataset doesn't exist.
    """
    dataset_ref = bigquery.DatasetReference(project_id, dataset_id)
    client.delete_dataset(dataset_ref, delete_contents=False, not_found_ok=not_found_ok)


def table_exists(
    client: bigquery.Client, project_id: str, dataset_id: str, table_id: str
) -> bool:
    """Check if a table exists in BigQuery.

    Args:
        client: BigQuery client instance.
        project_id: GCP project ID.
        dataset_id: Dataset ID.
        table_id: Table ID to check.

    Returns:
        True if the table exists, False otherwise.
    """
    table_ref = bigquery.TableReference(
        bigquery.DatasetReference(project_id, dataset_id), table_id
    )
    try:
        client.get_table(table_ref)
        return True
    except NotFound:
        return False
