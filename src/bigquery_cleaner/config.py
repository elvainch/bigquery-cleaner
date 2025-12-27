"""Configuration loading and CLI override resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import logger

try:  # Python 3.11+
    import tomllib as tomli  # type: ignore[assignment]
except Exception:  # Python <3.11
    import tomli  # type: ignore[no-redef]


def get_default_suffix() -> str:
    """Return the default suffix with the current date: _renamed_YYYYMMDD."""
    return f"_renamed_{datetime.now().strftime('%Y%m%d')}"


@dataclass
class CleanerConfig:
    project: str | None = None
    datasets: list[str] | None = None
    exclude_datasets: list[str] | None = None
    all_datasets: bool = False
    location: str | None = None
    days: int = 120
    rename_suffix: str = ""  # Initialized in __post_init__ or resolve_config
    dry_run: bool = False
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if not self.rename_suffix:
            self.rename_suffix = get_default_suffix()


def load_config(path: str | None) -> CleanerConfig:
    """Load a TOML config file and return defaults when missing.

    Args:
        path: Optional path to the TOML configuration file.

    Returns:
        A CleanerConfig instance populated with values from the file or defaults.
    """
    if not path:
        return CleanerConfig()
    config_path = Path(path)
    if not config_path.exists():
        return CleanerConfig()
    try:
        data = tomli.loads(config_path.read_text(encoding="utf-8"))
    except Exception as err:
        logger.error("Failed to load config from %s: %s", path, err)
        return CleanerConfig()

    cfg = data.get("bigquery_cleaner", {}) if isinstance(data, dict) else {}
    return CleanerConfig(
        project=cfg.get("project"),
        datasets=cfg.get("datasets"),
        exclude_datasets=cfg.get("exclude_datasets"),
        all_datasets=bool(cfg.get("all_datasets", False)),
        location=cfg.get("location"),
        days=int(cfg.get("days", 30)) if cfg.get("days") is not None else 30,
        rename_suffix=str(cfg.get("rename_suffix", "")),
        dry_run=bool(cfg.get("dry_run", False)),
        log_level=str(cfg.get("log_level", "INFO")),
    )


def _parse_datasets_csv(val: str | None) -> list[str] | None:
    """Parse a comma-separated datasets string into a list of non-empty values.

    Args:
        val: Comma-separated string of dataset IDs.

    Returns:
        A list of dataset IDs or None if input is empty.
    """
    if not val:
        return None
    # Strip whitespace from each dataset ID in the comma-separated string.
    items = [item.strip() for item in val.split(",")]
    # Filter out any empty strings from the list of dataset IDs.
    return [item for item in items if item]


def resolve_config(
    *,
    path: str | None,
    cli_project: str | None,
    cli_datasets_csv: str | None,
    cli_exclude_datasets_csv: str | None = None,
    cli_all_datasets: bool,
    cli_days: int | None,
    cli_rename_suffix: str | None = None,
    cli_dry_run: bool = False,
    cli_log_level: str | None = None,
) -> CleanerConfig:
    """Merge TOML config with CLI flags and return the effective configuration.

    Precedence: CLI flags override TOML values. Datasets can be provided as a comma-separated list
    via CLI or an array in TOML. If both CLI datasets and `all_datasets` are provided, `all_datasets`
    takes precedence in downstream logic but both are returned as set here without validation to
    keep behavior consistent with the CLI.

    Args:
        path: Optional path to the TOML configuration file.
        cli_project: GCP project ID from CLI.
        cli_datasets_csv: Comma-separated list of datasets from CLI.
        cli_exclude_datasets_csv: Comma-separated list of datasets to exclude from CLI.
        cli_all_datasets: Flag to scan all datasets from CLI.
        cli_days: Lookback window in days from CLI.

    Returns:
        The resolved CleanerConfig instance.
    """
    base = load_config(path)

    merged = CleanerConfig()
    merged.project = cli_project or base.project
    merged.days = int(cli_days) if cli_days is not None else base.days
    merged.all_datasets = bool(cli_all_datasets) or bool(base.all_datasets)
    merged.rename_suffix = cli_rename_suffix if cli_rename_suffix is not None else base.rename_suffix
    merged.dry_run = bool(cli_dry_run) or bool(base.dry_run)
    merged.log_level = cli_log_level or base.log_level

    cli_datasets = _parse_datasets_csv(cli_datasets_csv)
    merged.datasets = cli_datasets if cli_datasets is not None else base.datasets

    cli_exclude = _parse_datasets_csv(cli_exclude_datasets_csv)
    merged.exclude_datasets = cli_exclude if cli_exclude is not None else base.exclude_datasets

    # Location remains a file-only option for now (multi-dataset mode auto-detects per dataset)
    merged.location = base.location

    return merged
