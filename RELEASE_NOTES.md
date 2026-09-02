# Release Notes

## 0.1.9

### Added

- Added repeated analysis-status feedback for `list-unused-tables`, `rename-old-tables`, `revert-renamed-tables`, and `delete-tables` so long-running planning phases print a visible status message every 15 seconds before execution begins.

### Changed

- Removed the per-table `get_table()` existence-check bottleneck from `rename-old-tables` by reusing batched table metadata that was already fetched per location.
- Removed the same per-table existence-check bottleneck from `revert-renamed-tables` by checking revert collisions against the prefetched in-memory table set.
- Expanded CLI and core-operation tests to cover the new analysis-status feedback and the optimized rename/revert collision checks.

## 0.1.8

### Added

- Added concurrent batch execution for `rename-old-tables`, `revert-renamed-tables`, and `delete-tables`, using batches of 20 statements with up to 10 BigQuery jobs running at once.
- Added live Rich progress reporting for rename, revert, and delete execution so batch completion is shown as tables are processed.
- Added explicit-project validation coverage across CLI and execution-context paths.

### Changed

- Changed project handling so the tool no longer falls back to the ADC default project; a project must now be provided explicitly via CLI flag or config.
- Standardized mutation execution on a shared concurrent statement-batch executor for rename, revert, and delete workflows.
- Renamed `_split_dataset_ref()` to `validate_dataset_name()` and kept project pairing inside dataset normalization to match the single-project dataset model.
- Removed the unused `location` config field and the related sample configuration and README documentation.
- Expanded tests to cover concurrent batch execution, progress reporting, explicit project enforcement, and the updated dataset-validation behavior.

## 0.1.7

### Added

- Added `jobs_projects` config support and `--jobs-projects` CLI support for scanning `INFORMATION_SCHEMA.JOBS` in additional projects when determining whether tables were recently queried.
- Added strict failure behavior for cross-project jobs scans so the command aborts if one configured jobs project cannot be queried.
- Added return-value examples to the docstrings in `bq_client.py` and `core_operations.py` to make the internal APIs easier to understand.

### Changed

- Updated the recent-reference query flow to union table references across the main project and any configured extra jobs projects.
- Updated the sample config and README to document cross-project jobs-history scanning.
- Expanded test coverage for config parsing, CLI passthrough, query fan-out, failure handling, and jobs-project deduplication.
