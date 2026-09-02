# BigQuery Cleaner - Agent Guide

This repository is a `uv`-managed Python package exposing a Typer CLI for finding old and unused BigQuery tables, renaming them safely, reverting those renames, deleting suffixed tables, and removing empty datasets.

## Project Shape

- Packaging: `src/` layout
- Manager: `uv` (Python 3.10+)
- CLI entrypoint: `bigquery-cleaner`
- Console script: `bigquery_cleaner.cli:app`
- BigQuery access: `google-cloud-bigquery`

## Current Behavior

- The CLI requires an explicit `project` from `--project` or `cleaner.toml`.
- ADC is still used for authentication, but ADC default-project fallback is intentionally disabled.
- Dataset inputs must be unqualified dataset names only, such as `analytics`.
- Fully-qualified dataset inputs such as `my-project.analytics` are rejected.
- If both `all_datasets` and `datasets` are set, the explicit `datasets` list wins.
- `jobs_projects` expands only jobs-history scanning. Dataset ownership still comes from the main configured project.

## Commands

- `ping`
  - Verifies connectivity with `SELECT 1`.
- `datasets`
  - Lists datasets in the explicit project.
- `tables`
  - Lists tables, creation time, and size for selected datasets.
- `list-unused-tables`
  - Finds tables that were not referenced in recent jobs history and were also not modified recently.
- `rename-old-tables`
  - Renames unused tables by appending a suffix.
- `revert-renamed-tables`
  - Removes the configured suffix from renamed tables.
- `delete-tables`
  - Deletes tables matching the configured suffix.
- `delete-empty-datasets`
  - Deletes datasets with no tables or views.

## Config Schema

Section: `[bigquery_cleaner]`

- `project` (`str`)
- `datasets` (`list[str] | None`)
- `exclude_datasets` (`list[str] | None`)
- `jobs_projects` (`list[str] | None`)
- `all_datasets` (`bool`, default `false`)
- `days` (`int`, default `30`)
- `rename_suffix` (`str`, default `_renamed_YYYYMMDD`)
- `dry_run` (`bool`, default `false`)
- `log_level` (`str`, default `INFO`)

Notes:

- `datasets` and `exclude_datasets` are dataset names only, not `project.dataset`.
- `jobs_projects` adds extra projects whose `INFORMATION_SCHEMA.JOBS` history should count as table usage.
- `all_datasets` is used only when `datasets` is omitted or empty.

## Implementation Map

- `src/bigquery_cleaner/cli.py`
  - Typer commands, validation, Rich output, analysis heartbeat, mutation progress UI
- `src/bigquery_cleaner/config.py`
  - `CleanerConfig`, TOML loading, CLI-over-config resolution
- `src/bigquery_cleaner/utils.py`
  - execution context, dataset normalization, location grouping, jobs-project resolution
- `src/bigquery_cleaner/bq_client.py`
  - BigQuery client helpers, `INFORMATION_SCHEMA` queries, statement batching, concurrent mutation executor
- `src/bigquery_cleaner/core_operations.py`
  - unused-table detection and mutation orchestration

## Detection Logic

- Unused-table detection is the intersection of:
  - tables not referenced in `INFORMATION_SCHEMA.JOBS.referenced_tables` within `days`
  - tables whose `storage_last_modified_time` is older than `days`
- Jobs-history usage can be scanned across the main project plus extra `jobs_projects`.
- Table metadata is fetched through batched region-level `INFORMATION_SCHEMA.TABLE_STORAGE` queries.
- Dataset locations are auto-resolved from dataset metadata.

## Mutation Behavior

- `rename-old-tables`, `revert-renamed-tables`, and `delete-tables` use batched execution.
- Batch size is `20` statements.
- Maximum concurrent BigQuery jobs is `10`.
- If a batch fails, no new batches are submitted, but already-started jobs are still awaited.
- Rename and revert avoid per-table `get_table()` bottlenecks by using prefetched table metadata for collision checks.

## User Feedback

- `list-unused-tables`, `rename-old-tables`, `revert-renamed-tables`, and `delete-tables` print:
  - `Working on it... analyzing datasets and building the work plan.`
- That message is emitted immediately and then repeated every 15 seconds during analysis.
- Mutation commands switch to a Rich progress bar once execution begins.

## Constraints and Conventions

- Keep the `src/` layout.
- Use `Annotated` for Typer options and arguments.
- Do not add repeatable CLI flags; use comma-separated strings instead.
- Preserve the config-first UX.
- Do not modify `bigquery_maintenance.py`; it is legacy reference code.
- Prefer updating tests alongside behavior changes.

## Auth and Prereqs

- User login: `gcloud auth login`
- ADC setup: `gcloud auth application-default login`
- Alternative: `GOOGLE_APPLICATION_CREDENTIALS`

## Quality Commands

- `uv run pytest -q`
- `uv run ruff check .`

## Known Limitations

- Usage detection is still based on `INFORMATION_SCHEMA.JOBS`; non-query consumers are not fully covered yet.
- The tool does not currently analyze downstream dependencies such as views, routines, or scheduled pipelines before rename/delete.

## Roadmap

- Dependency checks via `INFORMATION_SCHEMA.OBJECT_REFERENCES`
- Audit-log and non-SQL usage signals
- Scheduled pipeline awareness
- Table-type and partition-aware cleanup rules
- Governance-driven exclusions
- Snapshot/backup workflow before destructive deletes
- Structured output such as `--json`
