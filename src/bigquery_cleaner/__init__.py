"""Package metadata for BigQuery Cleaner."""

import logging

__all__ = ["__version__", "logger"]

__version__ = "0.1.3"

# Configure logging
logger = logging.getLogger("bigquery_cleaner")
