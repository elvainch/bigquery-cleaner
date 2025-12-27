# 🧹 BigQuery Cleaner

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**BigQuery Cleaner** is a powerful CLI tool designed to help you declutter your Google BigQuery environment. It identifies tables that haven't been queried recently and provides safe mechanisms to rename or prepare them for deletion.

---

## 🚀 Quick Start

Get up and running in seconds:

```bash
# 1. Install via uv
uv tool install .

# 2. Find unused tables (older than 30 days and not queried)
bigquery-cleaner list-unused-tables --project your-gcp-project --all-datasets --days 30
```

---

## ✨ Features

- 🔍 **Unused Table Detection**: Scans `INFORMATION_SCHEMA.JOBS` to find tables that aren't being used.
- 📊 **Storage Insight**: Displays table sizes in GB and provides per-dataset and grand total summaries.
- 📂 **Multi-Dataset Support**: Target specific datasets, exclude others, or scan your entire project.
- 🏷️ **Safe Renaming**: Dry-run mode allows you to see what *would* happen before making changes.
- 🔄 **Easy Revert**: Renamed a table by mistake? Revert it easily with the `revert-renamed-tables` command.
- 🗑️ **Permanent Cleanup**: Use `delete-tables` to remove suffixed tables once you've confirmed they are no longer needed.
- 🧹 **Dataset Cleanup**: Remove empty datasets that no longer contain any tables or views using `delete-empty-datasets`.
- ⚙️ **Configurable**: Use a `cleaner.toml` file to save your project defaults and lookback windows.
- ⚡ **Built with Speed**: Powered by `uv`, `Typer`, and `Rich` for a beautiful, fast terminal experience.

---

## 📋 Prerequisites

- **Python 3.10+**
- **[uv](https://docs.astral.sh/uv/)** package manager installed.
- **Google Cloud Credentials**: Configured via Application Default Credentials (ADC).
  ```bash
  gcloud auth application-default login
  ```

---

## 🛠️ Installation

```bash
# Clone the repository
git clone https://github.com/your-repo/bigquery-cleaner.git
cd bigquery-cleaner

# Sync dependencies and install the tool
uv sync
uv tool install .
```

---

## 📖 Usage Guide

### Help Command
> Every command and sub-command supports the `--help` flag for detailed information on available options.
> 
>Example: `bigquery-cleaner list-unused-tables --help`
> 
> Run bigquery-cleaner --help to see all available commands.

### Connectivity Check
Ensure your credentials and project access are working:
```bash
bigquery-cleaner ping --project YOUR_PROJECT
```

### Exploration
List available datasets and tables:
```bash
# List all datasets
bigquery-cleaner datasets --project YOUR_PROJECT

# List tables in specific datasets
bigquery-cleaner tables --datasets dataset1,dataset2 --project YOUR_PROJECT
```

### Identifying Waste
The core functionality to find old, unreferenced tables:
```bash
# List unused tables across all datasets
bigquery-cleaner list-unused-tables --all-datasets --days 90
```

### Cleanup Operations
Safely rename unused tables with a suffix:
```bash
# Dry run first!
bigquery-cleaner rename-old-tables --all-datasets --days 90 --dry-run

# Perform the rename
bigquery-cleaner rename-old-tables --all-datasets --days 90

# Delete renamed tables after verification
# Dry run first!
bigquery-cleaner delete-tables --all-datasets --suffix "_renamed_20241225" --dry-run

# Perform the deletion
bigquery-cleaner delete-tables --all-datasets --suffix "_renamed_20241225"

# Remove empty datasets
bigquery-cleaner delete-empty-datasets --all-datasets
```

---

## ⚙️ Configuration

Tired of typing the same flags? Create a `cleaner.toml` file in your project root. All CLI options can be persisted here:

```toml
[bigquery_cleaner]
# GCP Project ID (defaults to ADC project if omitted)
project = "your-gcp-project"

# List of datasets to scan (e.g. ["ds1", "project2.ds2"])
datasets = ["dataset1", "dataset2"]

# List of datasets to ignore
exclude_datasets = ["logs_dataset", "temp_staging"]

# If true, scans all datasets in the project (overrides 'datasets' list)
all_datasets = true

# Lookback window in days for identifying unused tables (default: 30)
days = 60

# Suffix used for renaming and identifying tables for deletion (default: _renamed_YYYYMMDD)
rename_suffix = "_old_backup"

# Default behavior for commands (true = dry run by default)
dry_run = false

# Logging level (DEBUG, INFO, WARNING, ERROR)
log_level = "INFO"

# BigQuery Location (e.g. "US", "EU"). 
# Note: Multi-dataset mode usually auto-detects this.
location = "US"
```

Then run with:
```bash
bigquery-cleaner list-unused-tables --config cleaner.toml
```

---

## 📝 Notes

- **Detection Logic**: The `list-unused-tables` command identifies tables created more than `N` days ago that do not appear in `INFORMATION_SCHEMA.JOBS.referenced_tables` within that same window.
- **Rich Output**: All results are displayed in beautiful, sortable tables thanks to the `Rich` library. Includes total table counts and storage size summaries.
- **Linting & Quality**: The project uses **Ruff** for fast linting and formatting.

---
Developed by Alan Vainsencher.
