"""Smoke tests for CLI imports."""

def test_import_cli():
    """Import the CLI module successfully."""
    import bigquery_cleaner.cli as cli  # noqa: F401
