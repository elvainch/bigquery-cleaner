"""BigQuery client helpers for dataset and table discovery."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
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

RENAME_BATCH_SIZE = 20
MAX_RENAME_CONCURRENT_JOBS = 10


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
        Example: ``bigquery.Client(project="demo-project")``

    """
    if not project_id:
        raise ValueError("Project must be provided explicitly via --project or config.")
    return bigquery.Client(project=project_id)


def validate_dataset_name(dataset: str) -> str:
    """Validate that a dataset input is an unqualified dataset name.

    Args:
        dataset: Dataset ID string (dataset name only, without project qualification).

    Returns:
        The validated dataset name.
        Example: ``"analytics"``

    Raises:
        ValueError: If the dataset is project-qualified or empty after trimming.

    """
    dataset_name = dataset.strip()
    if not dataset_name:
        raise ValueError("Dataset name cannot be empty.")
    if "." in dataset:
        raise ValueError(
            "Dataset must be an unqualified dataset name. Set the project separately via --project or config."
        )
    return dataset_name


def list_datasets(project_id: str | None) -> list[str]:
    """Return dataset IDs available in the given or default project.

    Args:
        project_id: Optional GCP project ID.

    Returns:
        A list of dataset IDs.
        Example: ``["analytics", "staging", "archive"]``

    """
    if not project_id:
        raise ValueError("Project must be provided explicitly via --project or config.")
    client = get_client(project_id)
    # Fetch and return all dataset IDs from the project.
    return [dataset.dataset_id for dataset in client.list_datasets()]  # type: ignore[attr-defined]


def normalize_datasets(
    client: bigquery.Client,
    datasets: list[str] | None,
    default_project: str,
    exclude_datasets: list[str] | None = None,
) -> list[tuple[str, str]]:
    """Normalize dataset inputs to (project, dataset) pairs for one project.

    If ``datasets`` is None/empty, list all datasets in the client's project.
    Filters out any datasets present in ``exclude_dataserts``.

    Args:
        client: BigQuery client instance.
        datasets: Optional list of dataset names to normalize.
        default_project: Project ID used for all dataset names.
        exclude_datasets: Optional list of dataset names to exclude from the result.

    Returns:
        A list of (project_id, dataset_id) tuples.
        Example: ``[("demo-project", "analytics"), ("demo-project", "staging")]``

    """
    project_dataset_pairs: list[tuple[str, str]] = []
    if datasets and len(datasets) > 0:
        for dataset in datasets:
            dataset_id = validate_dataset_name(dataset)
            project_dataset_pairs.append((default_project, dataset_id))
    else:
        # Generate a list of (project, dataset) tuples for all datasets in the project.
        project_dataset_pairs = [
            (client.project, dataset.dataset_id) for dataset in client.list_datasets()
        ]

    if exclude_datasets:
        # Normalize exclude list for comparison
        excluded = {
            (default_project, validate_dataset_name(excluded_ds)) for excluded_ds in exclude_datasets
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
        Example: ``{"US": [("demo-project", "analytics")], "EU": [("demo-project", "reporting")]}``

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
    jobs_projects: list[str],
) -> defaultdict[str, set[str]]:
    """Return recent referenced tables grouped by dataset for a location.

    Falls back to empty sets on failure.

    Args:
        client: BigQuery client instance.
        location: Dataset location (e.g., "US").
        project_dataset_pairs: List of (project_id, dataset_id) tuples.
        days: Lookback window in days.
        jobs_projects: Projects whose INFORMATION_SCHEMA.JOBS views should be scanned.

    Returns:
        A dictionary mapping dataset ID to a set of table IDs referenced in queries.
        Example: ``{"analytics": {"events", "sessions"}, "staging": {"raw_imports"}}``

    """
    region_dataset = f"region-{location.lower()}"
    dataset_project = project_dataset_pairs[0][0]
    # Extract unique dataset IDs from the project-dataset pairs.
    dataset_ids = sorted({dataset_id for _, dataset_id in project_dataset_pairs})

    out: defaultdict[str, set[str]] = defaultdict(set)
    for jobs_project in jobs_projects:
        query = recent_references_across_datasets_sql(jobs_project, region_dataset)
        cfg = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("days", "INT64", days),
                bigquery.ScalarQueryParameter("dataset_project_id", "STRING", dataset_project),
                bigquery.ArrayQueryParameter("dataset_ids", "STRING", dataset_ids),
            ]
        )
        try:
            for row in client.query(query, job_config=cfg, location=location).result():
                out[row["dataset_id"]].add(row["table_id"])
        except Exception as err:
            logger.warning(
                "Recent references query failed for jobs project %s in location %s: %s",
                jobs_project,
                location,
                err,
            )
            raise RuntimeError(
                f"Failed to query INFORMATION_SCHEMA.JOBS for project '{jobs_project}' in location '{location}'."
            ) from err
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
        Example: ``{"analytics": {"events": TableMetadata(table_id="events", size_bytes=1024)}}``

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
        Example: ``{"demo-project.analytics": [TableMetadata(table_id="events", modified=datetime(...))]}``

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


def chunk_statements(statements: list[str], batch_size: int) -> list[list[str]]:
    """Split statements into fixed-size batches.

    Args:
        statements: SQL statements to batch.
        batch_size: Maximum number of statements per batch.

    Returns:
        A list of statement batches.
        Example: ``[["ALTER TABLE ...", "ALTER TABLE ..."], ["ALTER TABLE ..."]]``

    """
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero.")
    return [statements[index: index + batch_size] for index in range(0, len(statements), batch_size)]


def execute_statement_batches_concurrently(
    client: bigquery.Client,
    batch_work_items: list[tuple[str, list[str]]],
    max_concurrent_jobs: int,
    operation_label: str,
    progress_callback: Callable[[int, int], None] | None = None,
) -> None:
    """Execute batched DDL/DML statements concurrently with a global in-flight cap.

    Args:
        client: BigQuery client instance.
        batch_work_items: A list of ``(location, statements_batch)`` work items.
        max_concurrent_jobs: Maximum number of BigQuery jobs allowed in flight at once.
        operation_label: Human-readable operation name used in error messages.
        progress_callback: Optional callback receiving ``(completed_statements, total_statements)``
            after each finished batch.

    """
    if not batch_work_items:
        return
    if max_concurrent_jobs <= 0:
        raise ValueError("max_concurrent_jobs must be greater than zero.")

    def submit_batch(location: str, statements: list[str]):
        """Submit one batch query and wait for its result."""
        sql = ";\n".join(statements)
        client.query(sql, location=location).result()

    next_index = 0
    completed_statements = 0
    total_statements = sum(len(statements) for _, statements in batch_work_items)
    first_error: Exception | None = None
    in_flight: dict[Future[None], tuple[str, list[str]]] = {}

    if progress_callback is not None:
        progress_callback(0, total_statements)

    with ThreadPoolExecutor(max_workers=max_concurrent_jobs) as executor:
        while next_index < len(batch_work_items) or in_flight:
            while first_error is None and next_index < len(batch_work_items) and len(in_flight) < max_concurrent_jobs:
                location, statements = batch_work_items[next_index]
                future = executor.submit(submit_batch, location, statements)
                in_flight[future] = (location, statements)
                next_index += 1

            if not in_flight:
                break

            done, _ = wait(in_flight.keys(), return_when=FIRST_COMPLETED)
            for future in done:
                location, statements = in_flight.pop(future)
                try:
                    future.result()
                    completed_statements += len(statements)
                    if progress_callback is not None:
                        progress_callback(completed_statements, total_statements)
                except Exception as err:
                    if first_error is None:
                        first_error = RuntimeError(
                            f"{operation_label} batch failed in location '{location}' with {len(statements)} statements."
                        )
                        first_error.__cause__ = err

        if first_error is not None:
            raise first_error

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
        Example: ``True``

    """
    table_ref = bigquery.TableReference(
        bigquery.DatasetReference(project_id, dataset_id), table_id
    )
    try:
        client.get_table(table_ref)
        return True
    except NotFound:
        return False
