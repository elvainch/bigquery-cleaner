# BigQuery Cleaner — Agent Guide

This repository is a uv‑managed Python package exposing a Typer CLI to help identify (and later, rename/delete) unused BigQuery tables. Use this document to quickly align with project decisions and conventions before making changes.

## Project Shape
- Packaging: `src/` layout
- Manager: `uv` (Python 3.10+)
- CLI: Typer entrypoint `bigquery-cleaner`
- GCP: `google-cloud-bigquery` client with ADC auth
- Console script: defined in `pyproject.toml` as `bigquery-cleaner = "bigquery_cleaner.cli:app"`

## Core Commands
- `list-unused-tables`
  - Purpose: list tables not referenced by queries in the past N days AND modified more than N days ago.
  - Inputs: provided via TOML config and/or CLI flags (flags override config).
  - No `--dataset` flag. Prefer `--datasets` (comma‑separated) or `--all-datasets`.
  - Output: displays table ID, creation/modification dates, and size in GB. Includes per-dataset totals and a grand total (table count and size).
- `rename-old-tables`
  - Purpose: rename tables not referenced by queries in the past N days AND modified more than N days ago.
  - Suffix: uses `--suffix` or `rename_suffix` from config.
- `revert-renamed-tables`
  - Purpose: revert renamed tables by removing the specified suffix.
  - Suffix: uses `--suffix` or `rename_suffix` from config.

## Config Schema (TOML)
Section: `[bigquery_cleaner]`
- `project` (str): GCP project id
- `datasets` (list[str], optional): dataset ids; may be fully‑qualified (`proj.ds`) or just `ds`
- `exclude_datasets` (list[str], optional): dataset ids to skip
- `all_datasets` (bool, optional): scan every dataset in `project`
- `days` (int, default 30): lookback window
- `location` (str, optional): generally auto‑detected per dataset; not required
- `rename_suffix` (str, default "_renamed_YYYYMMDD"): suffix for `rename-old-tables`
- `dry_run` (bool, default false): if true, do not perform actual modifications
- `log_level` (str, default "INFO"): logging level

Example: see `cleaner.example.toml`.

## Implementation Notes
- Detection functions live in `src/bigquery_cleaner/core_operations.py`.
- Orchestration and utility helpers live in `src/bigquery_cleaner/utils.py`.
- API and client-related functions live in `src/bigquery_cleaner/bq_client.py`.
- CLI commands use `Annotated` types for parameters to avoid Ruff B008 errors (no function calls in argument defaults).
- Core detection functions:
  - `get_non_queried_tables(cfg: CleanerConfig) -> dict[str, list[TableMetadata]]`
  - `get_old_tables(cfg: CleanerConfig) -> dict[str, list[TableMetadata]]`
  - `get_old_modified_tables(cfg: CleanerConfig) -> dict[str, list[TableMetadata]]`
- Strategy:
  - Query `{project}.region-<location>.INFORMATION_SCHEMA.JOBS` and use `referenced_tables` to find recently referenced tables (per dataset, per location).
  - Query `INFORMATION_SCHEMA.TABLE_STORAGE` for table size and metadata.
  - List all tables in the dataset via the BigQuery client and subtract the referenced set.
  - Intersection: used to find tables that are both unqueried AND haven't been modified in N days.
  - Queries run in the dataset’s location; location is auto‑resolved from dataset metadata when not provided.
- CLI wiring in `src/bigquery_cleaner/cli.py` respects precedence: CLI flags > TOML config.
- Do not add repeatable flags (Click `multiple=True`); use comma‑separated `--datasets` instead.

## Constraints & Conventions
- Keep the `src/` layout. Align with existing style and minimal changes.
- Use `Annotated` for all Typer options and arguments.
- The file `bigquery_maintenance.py` is a legacy reference; do not modify it. It will be replaced as CLI matures.
- Prefer config‑first UX; only add flags that add real value. Avoid introducing `--dataset` (single) again.
- Windows usage: use `uv run bigquery-cleaner ...` or install via `uv tool install .` to get `bigquery-cleaner` on PATH.
- Linting: Use Ruff for linting and formatting. Configuration is in `ruff.toml`.
  - Run check: `uv run ruff check .`
  - Run format: `uv run ruff format .`

## Auth & Prereqs
- ADC expected (e.g., `gcloud auth application-default login`) or `GOOGLE_APPLICATION_CREDENTIALS` set.

## Roadmap (future work)
- Add `delete-old` command with `--dry-run`.
- Dependency graph checks: ensure no views/materialized views/procedures reference candidate tables (e.g., `INFORMATION_SCHEMA.OBJECT_REFERENCES`).
- Audit logs usage: consider Cloud Logging signals (dataRead/jobCompleted) for non-SQL consumers (extracts, copies, ML, BI tools).
- Scheduled jobs: detect Scheduled Queries/Dataform/Composer/Dataflow that read tables (via jobs metadata or config sources).
- Table-type handling: treat views/materialized views/external tables/snapshots carefully; avoid deleting sources.
- Streaming/loads: skip tables with active streaming buffers or very recent loads.
- Partitions: consider per-partition recency; avoid deleting tables with recent partitions.
- Labels/tags/policies: honor governance flags (e.g., `do-not-delete`, policy tags, constraints).
- Expiration: skip tables with upcoming expiry and leverage TTL where possible.
- Safety workflow: dry-run report, owner approval, optional snapshot/copy backup before DROP.
- Improve logging vs print, and structured output options (e.g., `--json`).
