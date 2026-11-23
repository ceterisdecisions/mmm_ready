# MCP server: readiness report

A small [MCP](https://modelcontextprotocol.io) server (official Python SDK, stdio transport) that lets an
agent such as Claude Code query `mmm_readiness_report` instead of reading SQL output.

It opens the DuckDB file **read-only** and never writes to it.

## Tools

| tool | returns |
|---|---|
| `readiness_summary` | channel count per status (`ready` / `review` / `blocked`), which channels are blocked and by what, which need review, and informational `warnings` |
| `channel_readiness(channel)` | each flagged issue for one channel: a plain business explanation using the channel's own numbers, a recommended action (`fix the data`, `review`, `validate with an experiment`) and a concrete next step; plus `warnings` |
| `list_channels` | every channel with its status, total spend and share of spend |

Recommended action per flag:

| flag | severity | action |
|---|---|---|
| `unmapped_spend`, `short_history`, `kpi_gaps` | blocking | fix the data |
| `platform_gaps`, `sparse`, `spike` | review | review |
| `low_variance`, `collinear` | review | validate with an experiment |

Explanations use the report's own numbers. For `short_history` that includes the minimum the report
was built with (`min_history_weeks`, from `mmm_ready_min_history_weeks`), so the text follows the var.

Low variance and collinearity are not data errors: the data simply cannot identify the effect, so the
fix is a lift or geo experiment used as a prior or for calibration.

Warnings (`severity: info`) never change a status, a count, `ready_for_modeling` or the issue
lists. Today there is one: `kpi_from_analytics`, when the report's `warn_kpi_from_analytics` is 1
because the KPI was built from GA4/Firebase (`mmm_ready_outcomes_source`) instead of the ERP or
backend; the next step is to reconcile analytics revenue against the backend. Reports built before
that column existed simply return no warnings.

For collinear channels, `channel_readiness` also reads `mmm_channel_correlations` and names the
partner channel(s) with their correlation (`collinear_with` field and in the explanation). If that
table is missing, the explanation falls back to "another channel".

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

`tests/test_evals.py` holds the evals (next section).

To try the tools by hand, outside Claude Code, use the MCP Inspector (requires Node):

```bash
npx @modelcontextprotocol/inspector .venv-mcp/bin/python mcp_server/server.py
```

## Evals

A catalog of questions a user or an agent would ask the server, each with its expected answer on
the synthetic data, lives in [`evals/catalog.json`](evals/catalog.json). `tests/test_evals.py`
starts `server.py` over stdio with the MCP client (as Claude Code does), makes the tool calls and
checks the answers. They are deterministic and call no model, so they need no API key.

Expected answers come from:

- **ground truth**: `integration_tests/ground_truth.json` (seeded issues, per-platform spend, ROI and
  contribution) mapped through `seeds/channel_taxonomy.csv`, computed by
  [`evals/truth.py`](evals/truth.py) at test time;
- **baseline**: statuses and flags of the current report that the generator produces without being
  a seeded issue (e.g. low variance on `search_brand`, TV's off-season flighting), pinned in the
  catalog.

Nothing that depends on the build is pinned (gap weeks, ratios, correlations): checks are on
channel, platform, flag, status and action. The number of TikTok gap weeks is computed from the
seeded gap dates and the week start the build used (3 with `monday`, 2 with `sunday`), so the evals
pass on any of the CI builds.

| id | question | tools | expected answer | source |
|---|---|---|---|---|
| Q1 | Is my data ready for an MMM? | `readiness_summary` | no: `ready_for_modeling` false; 7 channels, 1 ready / 5 review / 1 blocked | baseline |
| Q2 | What is blocking the model? | `readiness_summary`, `channel_readiness` | only `unmapped`, by `unmapped_spend`: *fix the data*, next step names `channel_taxonomy.csv`, quoted share = Pinterest spend / total spend | ground truth |
| Q3 | Which channels need review, and why? | `readiness_summary` | `search_brand` low_variance; `social_prospecting` platform_gaps + spike; `social_retargeting` low_variance; `tv` and `video_online` platform_gaps + collinear | baseline + ground truth |
| Q4 | What is wrong with TikTok? | `channel_readiness` | `social_prospecting` in review: platform_gaps naming `tiktok` with the complete weeks inside the seeded gap; spike (seeded Meta ×10 week) | ground truth |
| Q5 | Why are TV and YouTube flagged? | `channel_readiness` | `tv` and `video_online` collinear with each other only; *validate with an experiment* | ground truth |
| Q6 | Where is Pinterest? | `channel_readiness`, `list_channels` | `pinterest` is a tool error listing the valid channels; its spend is `unmapped`'s `total_spend` | ground truth |
| Q7 | Is the history long enough? | `readiness_summary`, `channel_readiness` | yes: no channel has `short_history`. **Limited check**: absence of the flag only, because the tools return no `expected_weeks` or `min_history_weeks` unless the flag is set | baseline |
| Q8 | Where does the KPI come from? | `readiness_summary`, `channel_readiness` | no `warnings` anywhere, so not GA4/Firebase. **Limited check**: absence of the warning only, because the tools return no `kpi_source` unless `warn_kpi_from_analytics` is set | baseline |
| Q9 | Which channels are ready as they are? | `list_channels` | only `search_nonbrand` | baseline |
| Q10 | How much does each channel spend? | `list_channels` | channel list = ground-truth platforms through the taxonomy; `total_spend` matches ground truth (relative tolerance 1e-6); shares sum to 1. **Known limitation**: `social_prospecting` is left out of the spend match, because `ground_truth.json` counts TikTok spend inside the seeded gap, whose rows the generator then drops | ground truth |
| Q11 | What is TV's ROI / contribution? | all | not answerable: no tool, field or text offers ROI or contribution, and no ground-truth ROI/contribution figure appears in any answer | ground truth |

The evals are skipped unless both `integration_tests/mmm_ready_dev.duckdb` and
`integration_tests/ground_truth.json` exist. In CI they run in the existing pytest step, against the
DuckDB left by the last `dbt build`. A test keeps this table's ids in sync with the catalog.

To run only the evals:

```bash
.venv-mcp/bin/python -m pytest mcp_server/tests/test_evals.py -v
```

## Layout

- `readiness.py`: query and explanation logic, with no MCP dependency.
- `server.py`: thin MCP layer that registers the three tools.
- `evals/`: eval catalog (`catalog.json`) and expected answers from the known truth (`truth.py`).
- `tests/`: pytest suite, including the evals (`test_evals.py`).
