# Release Notes

## 0.1.7

### Added

- Added `jobs_projects` config support and `--jobs-projects` CLI support for scanning `INFORMATION_SCHEMA.JOBS` in additional projects when determining whether tables were recently queried.
- Added strict failure behavior for cross-project jobs scans so the command aborts if one configured jobs project cannot be queried.
- Added return-value examples to the docstrings in `bq_client.py` and `core_operations.py` to make the internal APIs easier to understand.

### Changed

- Updated the recent-reference query flow to union table references across the main project and any configured extra jobs projects.
- Updated the sample config and README to document cross-project jobs-history scanning.
- Expanded test coverage for config parsing, CLI passthrough, query fan-out, failure handling, and jobs-project deduplication.

