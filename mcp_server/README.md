# MCP server: readiness report

A small [MCP](https://modelcontextprotocol.io) server (official Python SDK, stdio transport) that lets an
agent such as Claude Code query `mmm_readiness_report` instead of reading SQL output.

It opens the DuckDB file **read-only** and never writes to it.

## Tools

| tool | returns |
|---|---|
| `readiness_summary` | channel count per status (`ready` / `review` / `blocked`), which channels are blocked and by what, and which need review |
| `channel_readiness(channel)` | each flagged issue for one channel: a plain business explanation using the channel's own numbers, a recommended action (`fix the data`, `review`, `validate with an experiment`) and a concrete next step |
| `list_channels` | every channel with its status, total spend and share of spend |

Recommended action per flag:

| flag | severity | action |
|---|---|---|
| `unmapped_spend`, `short_history`, `kpi_gaps` | blocking | fix the data |
| `platform_gaps`, `sparse`, `spike` | review | review |
| `low_variance`, `collinear` | review | validate with an experiment |

Low variance and collinearity are not data errors: the data simply cannot identify the effect, so the
fix is a lift or geo experiment used as a prior or for calibration.

## Setup

The server has its own virtualenv: the MCP SDK brings web dependencies (starlette, uvicorn, httpx...)
that we keep out of the dbt environment. From the repo root:

```bash
python3.12 -m venv .venv-mcp
.venv-mcp/bin/pip install -r mcp_server/requirements.txt
```

The report must exist first (see the main [README](../README.md#local-quickstart-duckdb-zero-cost)):
`dbt build` in `integration_tests/`.

## Configuration

| env var | default |
|---|---|
| `MMM_READY_DUCKDB_PATH` | `integration_tests/mmm_ready_dev.duckdb` (resolved from the repo, not the working directory) |
| `MMM_READY_SCHEMA` | `main` |

Errors come back to the agent as tool errors with a clear message: DuckDB file not found, table not
found ("Run `dbt build` in integration_tests/ first"), file locked by a running `dbt build`, or an
unknown channel (with the list of valid ones).

## Using it from Claude Code

The server is registered at project level in [`.mcp.json`](../.mcp.json), so it is available to anyone
who clones the repo and creates `.venv-mcp`. Start `claude` from the repo root; the first time it asks
you to approve the project server.

1. Run `/mcp`: `mmm-readiness` should show as connected with 3 tools.
2. Ask something like *"Is the data ready for MMM? What should I do about TV?"*. Claude should call
   `readiness_summary` and then `channel_readiness("tv")`.

To point it at another database, export `MMM_READY_DUCKDB_PATH` before starting `claude`.

## Testing

```bash
.venv-mcp/bin/python -m pytest mcp_server/tests -v
```

The tests call the functions in `readiness.py` directly, with no server running: unit tests on a
fixture DuckDB, plus a test that the four seeded issues from the synthetic data come through the tools.
That last one is skipped if `integration_tests/mmm_ready_dev.duckdb` does not exist. CI runs all of
them after `dbt build`.

To try the tools by hand, outside Claude Code, use the MCP Inspector (requires Node):

```bash
npx @modelcontextprotocol/inspector .venv-mcp/bin/python mcp_server/server.py
```

## Layout

- `readiness.py`: query and explanation logic, with no MCP dependency.
- `server.py`: thin MCP layer that registers the three tools.
- `tests/`: pytest suite.
