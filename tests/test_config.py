"""Tests for configuration loading and defaults."""

from __future__ import annotations

from pathlib import Path

from bigquery_cleaner.config import CleanerConfig, _parse_datasets_csv, load_config, resolve_config


def test_cleaner_config_uses_consistent_default_days() -> None:
    """Use 30 days as the default lookback window."""
    assert CleanerConfig().days == 30


def test_load_config_uses_same_default_days_when_missing_from_file(tmp_path: Path) -> None:
    """Use the dataclass default when the config file omits days."""
    config_path = tmp_path / "cleaner.toml"
    config_path.write_text(
        '[bigquery_cleaner]\nproject = "demo-project"\n',
        encoding="utf-8",
    )

    cfg = load_config(str(config_path))

    assert cfg.days == CleanerConfig().days


def test_parse_datasets_csv_trims_values_and_ignores_empty_entries() -> None:
    """Normalize comma-separated dataset input."""
    assert _parse_datasets_csv(" alpha, beta ,, gamma ") == ["alpha", "beta", "gamma"]


def test_resolve_config_prefers_cli_values_over_file_defaults(tmp_path: Path) -> None:
    """Let explicit CLI values override file configuration."""
    config_path = tmp_path / "cleaner.toml"
    config_path.write_text(
        (
            "[bigquery_cleaner]\n"
            'project = "file-project"\n'
            'datasets = ["alpha"]\n'
            'exclude_datasets = ["beta"]\n'
            "all_datasets = false\n"
            "days = 45\n"
            'rename_suffix = "_file"\n'
            "dry_run = false\n"
            'log_level = "INFO"\n'
        ),
        encoding="utf-8",
    )

    cfg = resolve_config(
        path=str(config_path),
        cli_project="cli-project",
        cli_datasets_csv="delta,epsilon",
        cli_exclude_datasets_csv="zeta",
        cli_all_datasets=True,
        cli_days=90,
        cli_rename_suffix="_cli",
        cli_dry_run=True,
        cli_log_level="DEBUG",
    )

    assert cfg.project == "cli-project"
    assert cfg.datasets == ["delta", "epsilon"]
    assert cfg.exclude_datasets == ["zeta"]
    assert cfg.all_datasets is True
    assert cfg.days == 90
    assert cfg.rename_suffix == "_cli"
    assert cfg.dry_run is True
    assert cfg.log_level == "DEBUG"
