"""Typer CLI entrypoint for BigQuery Cleaner."""

import logging
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from . import __version__, logger
from .bq_client import TableMetadata, get_all_tables_for_location, get_client, list_datasets
from .config import CleanerConfig, resolve_config
from .core_operations import (
    delete_empty_datasets,
    delete_suffixed_tables,
    get_old_tables,
    rename_unused_tables,
    revert_renamed_tables,
)
from .utils import get_execution_context

app = typer.Typer(help="BigQuery Cleaner CLI", add_completion=False, no_args_is_help=True)
console = Console()


@app.callback()
def main(
    log_level: Annotated[str, typer.Option("--log-level", help="Set the logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)")] = "INFO",
) -> None:
    """BigQuery Cleaner CLI."""
    numeric_level = getattr(logging, log_level.upper(), None)
    if not isinstance(numeric_level, int):
        typer.echo(f"Invalid log level: {log_level}")
        raise typer.Exit(code=1)

    # Configure the package logger
    logger.setLevel(numeric_level)

    # Also configure a basic handler if none exists to ensure output
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)


def _apply_log_level(level_name: str) -> None:
    """Apply the given log level name to the package logger."""
    numeric_level = getattr(logging, level_name.upper(), None)
    if isinstance(numeric_level, int):
        logger.setLevel(numeric_level)


def _validate_datasets(cfg: CleanerConfig) -> None:
    """Ensure that either specific datasets or the all-datasets flag is True.

    Args:
        cfg: The cleaner configuration to validate.

    Raises:
        typer.Exit: If neither datasets nor all_datasets is set.
    """
    if not cfg.all_datasets and not cfg.datasets:
        console.print(
            "[red]Error: Provide --datasets or --all-datasets (or set them via --config)[/red]",
            style="bold",
        )
        raise typer.Exit(code=2)


def _print_unqueried_results(results: dict[str, list[TableMetadata]]) -> None:
    """Print grouped table results in a dataset-header format using rich Tables.

    Args:
        results: Dictionary mapping dataset IDs to lists of TableMetadata.
    """
    if not results:
        console.print("[yellow]No unused tables found.[/yellow]")
        return

    grand_total_bytes = 0
    grand_total_count = 0
    for ds_key, items in results.items():
        table = Table(title=f"Unused Tables in {ds_key}", show_header=True, header_style="bold magenta")
        table.add_column("Table ID", style="dim")
        table.add_column("Created", justify="right")
        table.add_column("Modified", justify="right")
        table.add_column("Size (GB)", justify="right")

        dataset_total_bytes = 0
        dataset_total_count = 0
        if not items:
            table.add_row("(none)", "-", "-", "-")
        else:
            dataset_total_count = len(items)
            for meta in items:
                created = meta.created.strftime("%Y-%m-%d %H:%M") if meta.created else "unknown"
                modified = meta.modified.strftime("%Y-%m-%d %H:%M") if meta.modified else "unknown"
                size_bytes = meta.size_bytes or 0
                dataset_total_bytes += size_bytes
                size_gb = f"{size_bytes / (1024**3):.2f}" if meta.size_bytes is not None else "unknown"
                table.add_row(meta.table_id, created, modified, size_gb)

        grand_total_bytes += dataset_total_bytes
        grand_total_count += dataset_total_count
        dataset_total_gb = dataset_total_bytes / (1024**3)
        table.add_section()
        table.add_row(f"Total ({dataset_total_count} tables)", "", "", f"[bold]{dataset_total_gb:.2f}[/bold]")

        console.print(table)
        console.print()

    grand_total_gb = grand_total_bytes / (1024**3)
    console.print(f"[bold green]Grand Total: {grand_total_count} tables, {grand_total_gb:.2f} GB[/bold green]")
    console.print()


@app.command()
def version() -> None:
    """Show version and exit."""
    typer.echo(__version__)


@app.command()
def ping(
    project: Annotated[str | None, typer.Option(help="GCP project ID to use")] = None,
    ctx: Annotated[typer.Context, typer.Option(hidden=True)] = None,
) -> None:
    """Run a simple SELECT 1 query to validate connectivity.

    Args:
        project: Optional GCP project ID to use.
    """
    # Extract log_level from parent context (main callback)
    log_level = ctx.parent.params.get("log_level") if ctx.parent else "INFO"
    _apply_log_level(log_level)

    client = get_client(project)
    query = "SELECT 1 AS one"
    _ = next(client.query(query).result())
    typer.echo(f"Successfully pinged BigQuery: {client.project}")


@app.command()
def datasets(
    project: Annotated[str | None, typer.Option(help="GCP project ID; defaults to ADC project")] = None,
    ctx: Annotated[typer.Context, typer.Option(hidden=True)] = None,
) -> None:
    """List datasets in the project.

    Args:
        project: Optional GCP project ID to list datasets from.
    """
    # Extract log_level from parent context (main callback)
    log_level = ctx.parent.params.get("log_level") if ctx.parent else "INFO"
    _apply_log_level(log_level)

    ids = list_datasets(project)
    if not ids:
        typer.echo("No datasets found.")
        raise typer.Exit(code=0)
    for dataset_id in ids:
        typer.echo(dataset_id)


@app.command()
def tables(
    datasets: Annotated[str | None, typer.Option("--datasets", help="Comma-separated list of datasets (each may be 'project.dataset' or just 'dataset')")] = None,
    exclude_datasets: Annotated[str | None, typer.Option("--exclude-datasets", help="Comma-separated list of datasets to exclude")] = None,
    project: Annotated[str | None, typer.Option("--project", help="Project ID used when datasets are not fully-qualified")] = None,
    all_datasets: Annotated[bool, typer.Option("--all-datasets", help="Scan all datasets in the project")] = False,
    config: Annotated[str | None, typer.Option("--config", help="Path to TOML config file")] = None,
    ctx: Annotated[typer.Context, typer.Option(hidden=True)] = None,
) -> None:
    """List tables in datasets with creation time.

    Args:
        datasets: Comma-separated list of datasets to inspect.
        exclude_datasets: Comma-separated list of datasets to exclude.
        project: GCP project ID.
        all_datasets: Flag to scan all datasets in the project.
        config: Path to TOML config file.
    """
    # Extract log_level from parent context (main callback)
    log_level = ctx.parent.params.get("log_level") if ctx.parent else None

    cfg: CleanerConfig = resolve_config(
        path=config,
        cli_project=project,
        cli_datasets_csv=datasets,
        cli_exclude_datasets_csv=exclude_datasets,
        cli_all_datasets=all_datasets,
        cli_days=None,
        cli_log_level=log_level,
    )
    _apply_log_level(cfg.log_level)

    _validate_datasets(cfg)

    client, loc_groups = get_execution_context(cfg)

    found_any = False
    grand_total_bytes = 0
    grand_total_count = 0
    for location, project_dataset_pairs in loc_groups.items():
        all_by_ds = get_all_tables_for_location(client, location, project_dataset_pairs)
        for ds_id, tables_dict in all_by_ds.items():
            found_any = True
            table = Table(title=f"Tables in {ds_id}", show_header=True, header_style="bold cyan")
            table.add_column("Table ID", style="dim")
            table.add_column("Created", justify="right")
            table.add_column("Size (GB)", justify="right")

            dataset_total_bytes = 0
            dataset_total_count = len(tables_dict)
            for table_id in sorted(tables_dict.keys()):
                meta = tables_dict[table_id]
                created = meta.created.strftime("%Y-%m-%d %H:%M") if meta.created else "unknown"
                size_bytes = meta.size_bytes or 0
                dataset_total_bytes += size_bytes
                size_gb = f"{size_bytes / (1024**3):.2f}" if meta.size_bytes is not None else "unknown"
                table.add_row(table_id, created, size_gb)

            grand_total_bytes += dataset_total_bytes
            grand_total_count += dataset_total_count
            dataset_total_gb = dataset_total_bytes / (1024**3)
            table.add_section()
            table.add_row(f"Total ({dataset_total_count} tables)", "", f"[bold]{dataset_total_gb:.2f}[/bold]")

            console.print(table)
            console.print()

    if not found_any:
        console.print("[yellow]No tables found.[/yellow]")
    else:
        grand_total_gb = grand_total_bytes / (1024**3)
        console.print(f"[bold green]Grand Total: {grand_total_count} tables, {grand_total_gb:.2f} GB[/bold green]")
        console.print()


@app.command("list-unused-tables")
def unused_tables(
    datasets: Annotated[str | None, typer.Option("--datasets", help="Comma-separated list of datasets (each may be 'project.dataset' or find dataset')")] = None,
    exclude_datasets: Annotated[str | None, typer.Option("--exclude-datasets", help="Comma-separated list of datasets to exclude")] = None,
    project: Annotated[str | None, typer.Option("--project", help="Project ID used when datasets are not fully-qualified")] = None,
    days: Annotated[int | None, typer.Option("--days", help="Lookback window in days")] = None,
    all_datasets: Annotated[bool, typer.Option("--all-datasets", help="Scan all datasets in the project")] = False,
    config: Annotated[str | None, typer.Option("--config", help="Path to TOML config file")] = None,
    ctx: Annotated[typer.Context, typer.Option(hidden=True)] = None,
) -> None:
    """List tables modified before N days ago and not referenced in the past N days.

    Parameter precedence: CLI flags override TOML config values. Config supports 'datasets' (list)
    and 'all_datasets'. Locations are auto-detected per dataset.

    Args:
        datasets: Comma-separated list of datasets to inspect.
        exclude_datasets: Comma-separated list of datasets to exclude.
        project: GCP project ID.
        days: Lookback window in days.
        all_datasets: Flag to scan all datasets in the project.
        config: Path to TOML config file.
    """
    # Extract log_level from parent context (main callback)
    log_level = ctx.parent.params.get("log_level") if ctx.parent else None

    cfg: CleanerConfig = resolve_config(
        path=config,
        cli_project=project,
        cli_datasets_csv=datasets,
        cli_exclude_datasets_csv=exclude_datasets,
        cli_all_datasets=all_datasets,
        cli_days=days,
        cli_log_level=log_level,
    )
    _apply_log_level(cfg.log_level)

    _validate_datasets(cfg)

    results = get_old_tables(cfg)
    _print_unqueried_results(results)
    raise typer.Exit(code=0)


@app.command("rename-old-tables")
def rename_old_tables_cmd(
    datasets: Annotated[str | None, typer.Option("--datasets", help="Comma-separated list of datasets (each may be 'project.dataset' or find dataset')")] = None,
    exclude_datasets: Annotated[str | None, typer.Option("--exclude-datasets", help="Comma-separated list of datasets to exclude")] = None,
    project: Annotated[str | None, typer.Option("--project", help="Project ID used when datasets are not fully-qualified")] = None,
    days: Annotated[int | None, typer.Option("--days", help="Lookback window in days")] = None,
    suffix: Annotated[str | None, typer.Option("--suffix", help="Suffix to append to renamed tables")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="If set, only print what would be renamed without executing")] = False,
    all_datasets: Annotated[bool, typer.Option("--all-datasets", help="Scan all datasets in the project")] = False,
    config: Annotated[str | None, typer.Option("--config", help="Path to TOML config file")] = None,
    ctx: Annotated[typer.Context, typer.Option(hidden=True)] = None,
) -> None:
    """Rename tables modified before N days ago and not referenced in the past N days.

    Args:
        datasets: Comma-separated list of datasets to inspect.
        exclude_datasets: Comma-separated list of datasets to exclude.
        project: GCP project ID.
        days: Lookback window in days.
        suffix: Suffix to append to renamed tables.
        dry_run: If True, do not perform actual renaming.
        all_datasets: Flag to scan all datasets in the project.
        config: Path to TOML config file.
    """
    # Extract log_level from parent context (main callback)
    log_level = ctx.parent.params.get("log_level") if ctx.parent else None

    cfg: CleanerConfig = resolve_config(
        path=config,
        cli_project=project,
        cli_datasets_csv=datasets,
        cli_exclude_datasets_csv=exclude_datasets,
        cli_all_datasets=all_datasets,
        cli_days=days,
        cli_rename_suffix=suffix,
        cli_dry_run=dry_run,
        cli_log_level=log_level,
    )
    _apply_log_level(cfg.log_level)

    _validate_datasets(cfg)

    results = rename_unused_tables(cfg)

    if not results:
        console.print("[yellow]No tables found to rename.[/yellow]")
        raise typer.Exit(code=0)

    action = "Would rename" if cfg.dry_run else "Renamed"
    for ds_key, renamed_project_dataset_pairs in results.items():
        table = Table(title=f"{action} in {ds_key}", show_header=True, header_style="bold green")
        table.add_column("From", style="red")
        table.add_column("To", style="green")

        for old_table_id, new_table_id in renamed_project_dataset_pairs:
            table.add_row(old_table_id, new_table_id)

        console.print(table)
        console.print()


@app.command("revert-renamed-tables")
def revert_renamed_tables_cmd(
    datasets: Annotated[str | None, typer.Option("--datasets", help="Comma-separated list of datasets (each may be 'project.dataset' or find dataset')")] = None,
    exclude_datasets: Annotated[str | None, typer.Option("--exclude-datasets", help="Comma-separated list of datasets to exclude")] = None,
    project: Annotated[str | None, typer.Option("--project", help="Project ID used when datasets are not fully-qualified")] = None,
    suffix: Annotated[str | None, typer.Option("--suffix", help="Suffix to remove from table names")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="If set, only print what would be reverted without executing")] = False,
    all_datasets: Annotated[bool, typer.Option("--all-datasets", help="Scan all datasets in the project")] = False,
    config: Annotated[str | None, typer.Option("--config", help="Path to TOML config file")] = None,
    ctx: Annotated[typer.Context, typer.Option(hidden=True)] = None,
) -> None:
    """Revert renamed tables by removing the specified suffix.

    Args:
        datasets: Comma-separated list of datasets to inspect.
        exclude_datasets: Comma-separated list of datasets to exclude.
        project: GCP project ID.
        suffix: Suffix to remove from table names.
        dry_run: If True, do not perform actual renaming.
        all_datasets: Flag to scan all datasets in the project.
        config: Path to TOML config file.
    """
    # Extract log_level from parent context (main callback)
    log_level = ctx.parent.params.get("log_level") if ctx.parent else None

    cfg: CleanerConfig = resolve_config(
        path=config,
        cli_project=project,
        cli_datasets_csv=datasets,
        cli_exclude_datasets_csv=exclude_datasets,
        cli_all_datasets=all_datasets,
        cli_days=None,  # Not used for reverting
        cli_rename_suffix=suffix,
        cli_dry_run=dry_run,
        cli_log_level=log_level,
    )
    _apply_log_level(cfg.log_level)

    _validate_datasets(cfg)

    results = revert_renamed_tables(cfg)

    if not results:
        console.print("[yellow]No tables found to revert.[/yellow]")
        raise typer.Exit(code=0)

    action = "Would revert" if cfg.dry_run else "Reverted"
    for ds_key, reverted_project_dataset_pairs in results.items():
        table = Table(title=f"{action} in {ds_key}", show_header=True, header_style="bold blue")
        table.add_column("From", style="red")
        table.add_column("To", style="green")

        for current_table_id, original_table_id in reverted_project_dataset_pairs:
            table.add_row(current_table_id, original_table_id)

        console.print(table)
        console.print()


@app.command("delete-tables")
def delete_tables_cmd(
    datasets: Annotated[str | None, typer.Option("--datasets", help="Comma-separated list of datasets (each may be 'project.dataset' or find dataset')")] = None,
    exclude_datasets: Annotated[str | None, typer.Option("--exclude-datasets", help="Comma-separated list of datasets to exclude")] = None,
    project: Annotated[str | None, typer.Option("--project", help="Project ID used when datasets are not fully-qualified")] = None,
    suffix: Annotated[str | None, typer.Option("--suffix", help="Suffix to filter tables for deletion")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="If set, only print what would be deleted without executing")] = False,
    all_datasets: Annotated[bool, typer.Option("--all-datasets", help="Scan all datasets in the project")] = False,
    config: Annotated[str | None, typer.Option("--config", help="Path to TOML config file")] = None,
    ctx: Annotated[typer.Context, typer.Option(hidden=True)] = None,
) -> None:
    """Delete tables that have the specified suffix.

    Args:
        datasets: Comma-separated list of datasets to inspect.
        exclude_datasets: Comma-separated list of datasets to exclude.
        project: GCP project ID.
        suffix: Suffix to filter tables for deletion.
        dry_run: If True, do not perform actual deletion.
        all_datasets: Flag to scan all datasets in the project.
        config: Path to TOML config file.
    """
    # Extract log_level from parent context (main callback)
    log_level = ctx.parent.params.get("log_level") if ctx.parent else None

    cfg: CleanerConfig = resolve_config(
        path=config,
        cli_project=project,
        cli_datasets_csv=datasets,
        cli_exclude_datasets_csv=exclude_datasets,
        cli_all_datasets=all_datasets,
        cli_days=None,  # Not used for deleting by suffix
        cli_rename_suffix=suffix,
        cli_dry_run=dry_run,
        cli_log_level=log_level,
    )
    _apply_log_level(cfg.log_level)

    _validate_datasets(cfg)

    if not cfg.rename_suffix:
        console.print("[red]Error: Provide --suffix (or set rename_suffix via --config) to specify which tables to delete[/red]", style="bold")
        raise typer.Exit(code=2)

    results = delete_suffixed_tables(cfg)

    if not results:
        console.print(f"[yellow]No tables found with suffix '{cfg.rename_suffix}' to delete.[/yellow]")
        raise typer.Exit(code=0)

    action = "Would delete" if cfg.dry_run else "Deleted"
    for ds_key, deleted_tables in results.items():
        table = Table(title=f"{action} in {ds_key}", show_header=True, header_style="bold red")
        table.add_column("Table ID", style="red")

        for table_id in deleted_tables:
            table.add_row(table_id)

        console.print(table)
        console.print()


@app.command("delete-empty-datasets")
def delete_empty_datasets_cmd(
    datasets: Annotated[str | None, typer.Option("--datasets", help="Comma-separated list of datasets to check")] = None,
    exclude_datasets: Annotated[str | None, typer.Option("--exclude-datasets", help="Comma-separated list of datasets to exclude")] = None,
    project: Annotated[str | None, typer.Option("--project", help="Project ID used when datasets are not fully-qualified")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="If set, only print what would be deleted without executing")] = False,
    all_datasets: Annotated[bool, typer.Option("--all-datasets", help="Scan all datasets in the project")] = False,
    config: Annotated[str | None, typer.Option("--config", help="Path to TOML config file")] = None,
    ctx: Annotated[typer.Context, typer.Option(hidden=True)] = None,
) -> None:
    """Delete datasets that do not contain any tables or views.

    Args:
        datasets: Comma-separated list of datasets to inspect.
        exclude_datasets: Comma-separated list of datasets to exclude.
        project: GCP project ID.
        dry_run: If True, do not perform actual deletion.
        all_datasets: Flag to scan all datasets in the project.
        config: Path to TOML config file.
    """
    # Extract log_level from parent context (main callback)
    log_level = ctx.parent.params.get("log_level") if ctx.parent else None

    cfg: CleanerConfig = resolve_config(
        path=config,
        cli_project=project,
        cli_datasets_csv=datasets,
        cli_exclude_datasets_csv=exclude_datasets,
        cli_all_datasets=all_datasets,
        cli_days=None,
        cli_log_level=log_level,
        cli_dry_run=dry_run,
    )
    _apply_log_level(cfg.log_level)

    _validate_datasets(cfg)

    deleted = delete_empty_datasets(cfg)

    if not deleted:
        console.print("[yellow]No empty datasets found to delete.[/yellow]")
        raise typer.Exit(code=0)

    action = "Would delete" if cfg.dry_run else "Deleted"
    table = Table(title=f"{action} Empty Datasets", show_header=True, header_style="bold red")
    table.add_column("Dataset ID", style="red")

    for ds_id in deleted:
        table.add_row(ds_id)

    console.print(table)
    console.print()


if __name__ == "__main__":  # pragma: no cover
    # Allow `python -m bigquery_cleaner` during development
    app(prog_name="bigquery-cleaner")
