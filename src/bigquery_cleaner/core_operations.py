"""Compute unqueried and old-table sets for BigQuery datasets."""

from __future__ import annotations

from collections import defaultdict

from .bq_client import (
    TableMetadata,
    delete_dataset,
    delete_tables,
    get_all_tables_for_location,
    get_old_modified_tables_for_location,
    rename_tables,
    table_exists,
)
from .config import CleanerConfig
from .utils import (
    compute_unqueried_for_location,
    get_ds_to_loc_map,
    get_execution_context,
)


def get_non_queried_tables(
    cfg: CleanerConfig,
) -> dict[str, list[TableMetadata]]:
    """List unqueried tables for many datasets or all project datasets.

    Optimized to batch BigQuery queries per location instead of per dataset.

    Args:
        cfg: The cleaner configuration.

    Returns:
        Mapping of fully-qualified dataset (``project.dataset``) to a sorted list of unqueried
        table metadata.
    """
    client, loc_groups = get_execution_context(cfg)

    results: dict[str, list[TableMetadata]] = {}

    # For each location, run two queries against region-<loc> INFORMATION_SCHEMA to gather:
    # - recently referenced tables
    # - all tables
    for location, project_dataset_pairs in loc_groups.items():
        batched = compute_unqueried_for_location(client, location, project_dataset_pairs, cfg)
        results.update(batched)

    return results


def get_old_modified_tables(
    cfg: CleanerConfig,
) -> dict[str, list[TableMetadata]]:
    """Return tables modified before the last ``days`` across selected or all datasets.

    Args:
        cfg: The cleaner configuration.

    Returns:
        Mapping: ``project.dataset`` -> [TableMetadata ...]
    """
    client, loc_groups = get_execution_context(cfg)

    results: dict[str, list[TableMetadata]] = {}
    for location, project_dataset_pairs in loc_groups.items():
        batched = get_old_modified_tables_for_location(client, location, project_dataset_pairs, cfg.days)
        results.update(batched)

    return results


def get_old_tables(
        cfg: CleanerConfig,
) -> dict[str, list[TableMetadata]]:
    """List tables that are both old and unqueried within the lookback window.

    Args:
        cfg: The cleaner configuration.

    Returns:
        Mapping of ``project.dataset`` -> [TableMetadata ...] for tables that:
        - were modified more than ``days`` ago
        - have not been referenced in the last ``days``
    """
    unqueried_by_dataset = get_non_queried_tables(cfg)
    old_modified_by_dataset = get_old_modified_tables(cfg)

    results: dict[str, list[TableMetadata]] = {}

    # Iterate through all datasets found in either scan
    all_datasets = set(unqueried_by_dataset) | set(old_modified_by_dataset)

    for ds_key in sorted(all_datasets):
        # Create lookups by table_id for both sets of metadata
        # Map table IDs to their metadata for unqueried tables.
        unq_map = {
            meta.table_id: meta for meta in unqueried_by_dataset.get(ds_key, [])
        }
        # Map table IDs to their metadata for old modified tables.
        old_map = {
            meta.table_id: meta
            for meta in old_modified_by_dataset.get(ds_key, [])
        }

        # Unused tables are the intersection of 'unqueried' and 'old modified'
        unused_ids = sorted(set(unq_map.keys()) & set(old_map.keys()))

        merged_metadata = []
        for table_id in unused_ids:
            # Take the metadata from unqueried (which has creation_time)
            # and enrich it with modified_time from the old_modified scan
            meta = unq_map[table_id]
            meta.modified = old_map[table_id].modified
            merged_metadata.append(meta)

        if merged_metadata:
            results[ds_key] = merged_metadata

    return results

def rename_unused_tables(
    cfg: CleanerConfig,
) -> dict[str, list[tuple[str, str]]]:
    """Rename tables that are both old and unqueried.

    Args:
        cfg: The cleaner configuration.

    Returns:
        Mapping: ``project.dataset`` -> [(``old_table_id``, ``new_table_id``) ...]
    """
    candidates_meta = get_old_tables(cfg)
    # Extract table IDs from the metadata for each dataset.
    candidates = {
        ds_key: [meta.table_id for meta in metadata_list]
        for ds_key, metadata_list in candidates_meta.items()
    }

    client, loc_groups = get_execution_context(cfg)

    # Create a reverse lookup for dataset location
    ds_to_loc = get_ds_to_loc_map(loc_groups)

    renamed: dict[str, list[tuple[str, str]]] = {}
    statements_by_loc: defaultdict[str, list[str]] = defaultdict(list)

    for ds_key, tables in candidates.items():
        if not tables:
            continue

        project_id_split, dataset_id_split = ds_key.split(".", 1)
        location = ds_to_loc.get((project_id_split, dataset_id_split))
        if not location:
            # Should not happen as we just resolved them
            continue

        renamed[ds_key] = []
        for table_ref in tables:
            # table_ref is "dataset.table" or just table_id
            table_id = table_ref.split(".", 1)[1] if "." in table_ref else table_ref
            new_table_id = f"{table_id}{cfg.rename_suffix}"

            # Check if target table already exists
            if table_exists(client, project_id_split, dataset_id_split, new_table_id):
                # We record it but skip the actual call
                continue

            if not cfg.dry_run:
                sql = f"ALTER TABLE `{project_id_split}.{dataset_id_split}.{table_id}` RENAME TO `{new_table_id}`"
                statements_by_loc[location].append(sql)

            renamed[ds_key].append((table_id, new_table_id))

    if not cfg.dry_run:
        for location, statements in statements_by_loc.items():
            rename_tables(client, statements, location)

    return renamed


def revert_renamed_tables(
    cfg: CleanerConfig,
) -> dict[str, list[tuple[str, str]]]:
    """Revert renamed tables by removing the specified suffix.

    Args:
        cfg: The cleaner configuration.

    Returns:
        Mapping: ``project.dataset`` -> [(``current_table_id``, ``reverted_table_id``) ...]
    """
    client, loc_groups = get_execution_context(cfg)

    reverted: dict[str, list[tuple[str, str]]] = {}
    statements_by_loc: defaultdict[str, list[str]] = defaultdict(list)

    suffix = cfg.rename_suffix

    for location, project_dataset_pairs in loc_groups.items():
        # Get all tables in these datasets at this location
        all_by_ds = get_all_tables_for_location(client, location, project_dataset_pairs)

        for project_id, dataset_id in project_dataset_pairs:
            ds_key = f"{project_id}.{dataset_id}"
            tables_dict = all_by_ds.get(dataset_id, {})
            if not tables_dict:
                continue

            for table_id in sorted(tables_dict.keys()):
                if table_id.endswith(suffix):
                    orig_table_id = table_id[: -len(suffix)]
                    if not orig_table_id:
                        # Safety: don't rename if suffix IS the table name
                        continue

                    # Check if original table name already exists
                    if table_exists(client, project_id, dataset_id, orig_table_id):
                        # Skip if we would overwrite an existing table
                        continue

                    if ds_key not in reverted:
                        reverted[ds_key] = []

                    if not cfg.dry_run:
                        sql = f"ALTER TABLE `{project_id}.{dataset_id}.{table_id}` RENAME TO `{orig_table_id}`"
                        statements_by_loc[location].append(sql)

                    reverted[ds_key].append((table_id, orig_table_id))

    if not cfg.dry_run:
        for location, statements in statements_by_loc.items():
            rename_tables(client, statements, location)

    return reverted


def delete_suffixed_tables(
    cfg: CleanerConfig,
) -> dict[str, list[str]]:
    """Delete tables that have the specified suffix.

    Args:
        cfg: The cleaner configuration.

    Returns:
        Mapping: ``project.dataset`` -> [``deleted_table_id`` ...]
    """
    client, loc_groups = get_execution_context(cfg)

    deleted: dict[str, list[str]] = {}
    statements_by_loc: defaultdict[str, list[str]] = defaultdict(list)

    suffix = cfg.rename_suffix

    for location, project_dataset_pairs in loc_groups.items():
        # Get all tables in these datasets at this location
        all_by_ds = get_all_tables_for_location(client, location, project_dataset_pairs)

        for project_id, dataset_id in project_dataset_pairs:
            ds_key = f"{project_id}.{dataset_id}"
            tables_dict = all_by_ds.get(dataset_id, {})
            if not tables_dict:
                continue

            for table_id in sorted(tables_dict.keys()):
                if table_id.endswith(suffix):
                    if ds_key not in deleted:
                        deleted[ds_key] = []

                    if not cfg.dry_run:
                        sql = f"DROP TABLE `{project_id}.{dataset_id}.{table_id}`"
                        statements_by_loc[location].append(sql)

                    deleted[ds_key].append(table_id)

    if not cfg.dry_run:
        for location, statements in statements_by_loc.items():
            delete_tables(client, statements, location)

    return deleted


def delete_empty_datasets(
    cfg: CleanerConfig,
) -> list[str]:
    """Identify and delete datasets that contain no tables or views.

    Args:
        cfg: The cleaner configuration.

    Returns:
        List of fully-qualified dataset IDs (project.dataset) that were deleted (or would be).
    """
    client, loc_groups = get_execution_context(cfg)
    deleted_datasets: list[str] = []

    for location, project_dataset_pairs in loc_groups.items():
        # fetch_all_tables_for_location returns a dict of dataset_id -> {table_id -> TableMetadata}
        # It includes views as well because the underlying SQL/API lists them.
        all_by_ds = get_all_tables_for_location(client, location, project_dataset_pairs)

        for project_id, dataset_id in project_dataset_pairs:
            ds_key = f"{project_id}.{dataset_id}"

            # If the dataset is not in all_by_ds or has an empty dict of tables, it's empty
            tables_dict = all_by_ds.get(dataset_id, {})

            if not tables_dict:
                if not cfg.dry_run:
                    delete_dataset(client, project_id, dataset_id)

                deleted_datasets.append(ds_key)

    return sorted(deleted_datasets)
