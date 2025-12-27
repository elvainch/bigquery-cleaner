"""SQL templates used by the BigQuery Cleaner scans."""

from __future__ import annotations


def recent_references_across_datasets_sql(project: str, region_dataset: str) -> str:
    """SQL to fetch recently referenced tables across many datasets.

    Uses INFORMATION_SCHEMA.JOBS.referenced_tables. Expects parameters:
      - @days (INT64)
      - @project_id (STRING)
      - @dataset_ids (ARRAY<STRING>)
    """
    return f"""
        SELECT DISTINCT t.dataset_id AS dataset_id, t.table_id AS table_id
        FROM `{project}`.`{region_dataset}`.INFORMATION_SCHEMA.JOBS AS j,
             UNNEST(j.referenced_tables) AS t
        WHERE j.job_type = 'QUERY'
          AND j.creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
          AND t.project_id = @project_id
          AND t.dataset_id IN UNNEST(@dataset_ids)
    """


def list_all_tables_across_datasets_sql(project: str, region_dataset: str) -> str:
    """SQL to list all tables across many datasets in a location.

    Uses INFORMATION_SCHEMA.TABLE_STORAGE to get table size.

    Expects parameter:
      - @dataset_ids (ARRAY<STRING>)
    """
    return f"""
        SELECT
            table_schema AS dataset_id,
            table_name AS table_id,
            creation_time,
            total_rows,
            total_logical_bytes AS total_bytes
        FROM `{project}`.`{region_dataset}`.INFORMATION_SCHEMA.TABLE_STORAGE
        WHERE table_schema IN UNNEST(@dataset_ids)
          AND deleted = false
    """


def list_old_tables_across_datasets_sql(project: str, region_dataset: str) -> str:
    """SQL to list tables across many datasets modified before the lookback window.

    Uses INFORMATION_SCHEMA.TABLE_STORAGE which is available at the region level.

    Expects parameters:
      - @days (INT64)
      - @dataset_ids (ARRAY<STRING>)
    """
    return f"""
        SELECT
            table_schema AS dataset_id,
            table_name AS table_id,
            storage_last_modified_time,
            total_rows,
            total_logical_bytes AS total_bytes
        FROM `{project}`.`{region_dataset}`.INFORMATION_SCHEMA.TABLE_STORAGE
        WHERE table_schema IN UNNEST(@dataset_ids)
          AND storage_last_modified_time < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
          AND deleted = false
    """
