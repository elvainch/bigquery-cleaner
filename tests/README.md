# Tests

This directory contains the automated test suite for `bigquery-cleaner`.

## How the tests work

The project uses `pytest` for test execution.

Most tests are unit-style tests:

- they isolate one module or function at a time
- they replace BigQuery or filesystem interactions with small fakes
- they use `monkeypatch` to override helpers and external calls
- they use `tmp_path` when a test needs temporary files or directories
- they prefer named local helper functions over inline patch lambdas when behavior needs explanation

The goal is to test behavior without requiring:

- a real BigQuery project
- Google credentials
- network access

## Test structure

- `test_cli.py`
  Covers CLI command behavior with `typer.testing.CliRunner`.
- `test_bq_client.py`
  Covers BigQuery client helper functions and SQL/query orchestration behavior.
- `test_config.py`
  Covers config loading, defaults, and CLI override resolution.
- `test_core_operations.py`
  Covers the higher-level cleanup and rename/delete workflows.
- `test_utils.py`
  Covers orchestration helpers such as dataset normalization and location grouping flow.
- `test_deploy.py`
  Covers the release/deploy helper script behavior.
- `test_publish.py`
  Covers the publish helper script behavior.
- `test_dataset_validation.py`
  Covers dataset input validation and normalization rules.
- `test_cli_imports.py`
  Keeps a simple import smoke test for the CLI module.

## Common pytest features used here

### `monkeypatch`

`monkeypatch` replaces functions, objects, or environment-dependent behavior during a test.

Example uses in this repo:

- replace `get_client()` with a fake client
- replace `delete_empty_datasets()` with a deterministic stub
- replace deploy/publish helper functions to avoid real git or publish operations

Preferred style:

- use a named local helper when the replacement has behavior worth explaining
- keep inline lambdas only for trivial no-op replacements

Example:

```python
def test_get_client_passes_project_when_provided(monkeypatch) -> None:
    captured: list[str | None] = []

    def fake_client(project=None):
        captured.append(project)
        return {"project": project}

    monkeypatch.setattr("bigquery_cleaner.bq_client.bigquery.Client", fake_client)

    client = get_client("demo-project")

    assert client == {"project": "demo-project"}
    assert captured == ["demo-project"]
```

How it works:

- the test replaces `bigquery.Client` with `fake_client` only for this test run
- when `get_client("demo-project")` executes, it calls the fake instead of the real BigQuery constructor
- the fake records the received `project` value in `captured`
- the fake returns a small predictable object, so the test can assert the result directly

This lets the test verify that `get_client()` forwards the project correctly without creating a real BigQuery client.

### `tmp_path`

`tmp_path` is a built-in pytest fixture.

Pytest creates a temporary directory and passes it into the test automatically as a `pathlib.Path`.

It is used when a test needs to create files such as:

- temporary TOML config files
- temporary `.env` files
- temporary `dist/` artifacts

### Parametrized tests

Some tests use `@pytest.mark.parametrize(...)` to run the same scenario with multiple inputs.

This helps avoid duplicated test code while still covering multiple cases.

Example:

```python
@pytest.mark.parametrize("raises_not_found", [False, True])
def test_table_exists_reports_presence(raises_not_found: bool) -> None:
    class TableClient:
        def get_table(self, table_ref):
            if raises_not_found:
                raise NotFound("missing")
            return table_ref

    assert table_exists(TableClient(), "demo", "alpha", "table_one") is (not raises_not_found)
```

How it works:

- pytest runs this test once with `raises_not_found=False`
- pytest runs it again with `raises_not_found=True`
- in the first run, `get_table()` returns normally, so `table_exists(...)` should be `True`
- in the second run, `get_table()` raises `NotFound`, so `table_exists(...)` should be `False`

This is useful when the test logic stays the same but the input or expected result changes. It keeps related scenarios together and avoids copy-pasting nearly identical tests.

## Running the tests

Run the full suite:

```bash
uv run pytest -q
```

Run Ruff checks:

```bash
uv run ruff check .
```

Run one test file:

```bash
uv run pytest -q tests/test_core_operations.py
```

Run one test function:

```bash
uv run pytest -q tests/test_config.py -k default_days
```

## Test design expectations

When adding new tests, prefer:

- behavior-focused assertions
- small fake objects over complex mocks
- one clear scenario per test
- parametrization for repeated cases
- no dependency on real cloud resources

Avoid:

- tests that depend on local credentials
- tests that require a live BigQuery environment
- brittle assertions tied to incidental formatting unless formatting is the thing being tested
