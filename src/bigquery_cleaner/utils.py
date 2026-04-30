"""Utility functions for BigQuery Cleaner orchestration."""

from __future__ import annotations

from collections import defaultdict

from google.cloud import bigquery

from .bq_client import (
    TableMetadata,
    get_all_tables_for_location,
    get_client,
    get_recent_referenced_tables_by_dataset,
    group_datasets_by_location,
    normalize_datasets,
)
from .config import CleanerConfig


def get_execution_context(
    cfg: CleanerConfig,
) -> tuple[bigquery.Client, defaultdict[str, list[tuple[str, str]]]]:
    """Initialize client, normalize datasets, and group them by location.

    Args:
        cfg: The cleaner configuration.

    Returns:
        A tuple containing (BigQuery client, location groups).

    """
    client = get_client(cfg.project)
    effective_project = client.project
    project_dataset_pairs = normalize_datasets(client, cfg.datasets, effective_project, cfg.exclude_datasets)
    loc_groups = group_datasets_by_location(client, project_dataset_pairs)
    return client, loc_groups


def get_ds_to_loc_map(
    loc_groups: defaultdict[str, list[tuple[str, str]]]
) -> dict[tuple[str, str], str]:
    """Create a reverse lookup for dataset location.

    Args:
        loc_groups: Mapping of location to lists of (project_id, dataset_id) tuples.

    Returns:
        Mapping of (project_id, dataset_id) to its location.

    """
    ds_to_loc: dict[tuple[str, str], str] = {}
    for location, project_dataset_pairs in loc_groups.items():
        for project_id, dataset_id in project_dataset_pairs:
            ds_to_loc[(project_id, dataset_id)] = location
    return ds_to_loc


def get_jobs_projects(cfg: CleanerConfig, effective_project: str) -> list[str]:
    """Return the effective jobs-scan project list with deduplication.

    Args:
        cfg: The cleaner configuration.
        effective_project: The resolved main project used for dataset ownership.

    Returns:
        A deduplicated list of projects to scan for INFORMATION_SCHEMA.JOBS, always starting
        with the effective main project.

    """
    jobs_projects = [effective_project]
    for project_id in cfg.jobs_projects or []:
        if project_id and project_id not in jobs_projects:
            jobs_projects.append(project_id)
    return jobs_projects


def compute_unqueried_for_location(
    client: bigquery.Client, location: str, project_dataset_pairs: list[tuple[str, str]], cfg: CleanerConfig
) -> dict[str, list[TableMetadata]]:
    """Compute unqueried tables for a location using batched region queries.

    Args:
        client: BigQuery client instance.
        location: Dataset location (e.g., "US").
        project_dataset_pairs: List of (project_id, dataset_id) tuples.
        cfg: The cleaner configuration.

    Returns:
        Mapping of ``project.dataset`` -> [TableMetadata ...]

    """
    jobs_projects = get_jobs_projects(cfg, client.project)
    recent_by_ds = get_recent_referenced_tables_by_dataset(
        client=client,
        location=location,
        project_dataset_pairs=project_dataset_pairs,
        days=cfg.days,
        jobs_projects=jobs_projects,
    )
    all_by_ds = get_all_tables_for_location(
        client=client,
        location=location,
        project_dataset_pairs=project_dataset_pairs,
    )

    out: dict[str, list[TableMetadata]] = {}
    for project_id, dataset_id in project_dataset_pairs:
        key = f"{project_id}.{dataset_id}"
        all_tables = all_by_ds.get(dataset_id, {})
        recent_ids = recent_by_ds.get(dataset_id, set())

        unq_ids = sorted(set(all_tables.keys()) - recent_ids)
        # Retrieve metadata for all table IDs that have not been queried.
        out[key] = [all_tables[table_id] for table_id in unq_ids]
    return out
