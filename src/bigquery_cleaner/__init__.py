"""Package metadata for BigQuery Cleaner."""

import logging

__all__ = ["__version__", "logger"]

__version__ = "0.1.9"

# Configure logging
logger = logging.getLogger("bigquery_cleaner")
