"""Tests for configuration loading and defaults."""

from __future__ import annotations

from pathlib import Path

from bigquery_cleaner.config import CleanerConfig, load_config


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
