# Agent Note: τ² eval full vs ablation tool layers

Status: implemented

English | [中文](2026-08-27-tau2-eval-full-ablation-layers.zh.md)

## Problem

Stage-4 τ² eval scores mock `create_task_1` with MCP domain tools beside bash and fs, with skill, subagent, todo, and web off. That cannot answer how much the rest of the harness catalog changes official `DB`×`COMMUNICATE` reward, and `cordis.eval.yml` is not switchable against a complete group or an MCP-only ablation on the same `task_ids` and seed.

## Decision

`eval/tau2/run.py --layer` / `--layers` selects a named composition; `--suite` runs mock `create_task_1` plus airline test-split ids `2,6,8,13,16` at seed 42. `--stop-on-collapse` (default on) skips the rest of a layer, and later `--layers`, when that mock reward is not 1.0. Sessions write under `eval/tau2/.work/stage5/{layer}/`. `cordis.eval.yml` remains the stage-4 baseline.

| Layer | File | Model-facing extras vs baseline |
|---|---|---|
| `5a` | `cordis.eval.5a.yml` | `skills.enabled: true`; `dshHome` is `DSH_CWD` so `~/.dsh` skills stay out |
| `5b` | `cordis.eval.5b.yml` | 5a plus todo and in-process spawn subagent (same process composition, so children see `mcp__tau2__*`) |
| `full` (`5c`) | `cordis.eval.full.yml` | 5b plus search-only `web_search` (`fetch: false`) |
| `ablation` | `cordis.eval.ablation.yml` | MCP + conversation only (`toolBash: false`, `tool-fs` disabled, skill/subagent/todo/web off) |
| `5e` | `cordis.eval.5e.yml` | full plus `tools.mode: code` and `@deepseek-ai/dsh-code-runtime-worker-thread` |

Plan mode stays off: `exit_plan_mode` reviews through `ctx.userQuestions`, and the JSON-RPC eval composition has no review UI. `danger-full-access` does not auto-approve those reviews. LSP stays off: the customer-service workspace has no language server, so the tool would be dead catalog noise.

A lower complete-group score than ablation is the expected catalog-noise signal, not a broken bridge.

## Testing

Mock `create_task_1` at seed 42, `deepseek-v4-flash`, 2026-08-27: every listed layer reward 1.0, `DB=1`, `COMMUNICATE=1`, `policy_in_session_jsonl=True`. First-request extras: `5a` adds `skill`; `5b` adds `subagent` and `todo_write`; `full` adds `web_search`; `ablation` lists only `mcp__tau2__*`; `5e` lists only `run_code` and still projects domain writes. Called domain tools remain `mcp__tau2__get_users` then `mcp__tau2__create_task` except `5e` (`run_code`).

Airline test-split ids `2,6,8,13,16` at the same seed: `full` 5/5; `ablation` 5/5 after one retry of two τ `UserMessage` construction failures (empty user-simulator turns). Completed ablation trials that scored were 1.0 on the first pass as well; the gap was not a DB miss. `run.py` retries that construction error once and writes `eval/tau2/.work/stage5/summary.json`. Empty user text that does reach `generate_next_message` asks the simulator to repeat and still appends an empty projection slot.

Rerun:

```bash
export UV_PROJECT_ENVIRONMENT="$PWD/tmp/py-sdk-venv"
uv run --project python/sdk python eval/tau2/run.py --layer full --suite
uv run --project python/sdk python eval/tau2/run.py --layer ablation --suite
```

## Alternatives considered

**One `cordis.eval.full.yml` that turns every dsh-base row on, including plan and LSP.** Rejected because unattended `exit_plan_mode` can hang the trial, and LSP has no server in these workspaces. Code Mode is a separate `5e` file so a native-tool collapse is isolable.

**Nested include of `cordis.eval.yml` with patches for later layers.** Rejected because include patches target the included file's rows, and `cordis.eval.yml` is itself an include; agent-spine config would not be reachable without rewriting the nested include.

**Disable bash by omitting HTTP from AGENTS.md while leaving `tool-bash` mounted.** Rejected for the ablation question: the model could still curl. `toolBash: false` plus disabled `tool-fs` is the catalog that answers "MCP + conversation only."

**Treat a complete-group score below ablation as a harness defect.** Rejected: the stage-5 question is how much extra catalog costs τ reward. Projection and the shared toolkit stay the scoring path.

**Fail the trial when the user simulator emits an empty `UserMessage`.** Rejected for a single retry in `run.py`: that error is a LiteLLM empty turn, not a domain-tool miss. A second failure still records `fail_reason` instead of hanging.

## Consequences

Switching compositions does not change [trajectory projection](2026-08-27-tau2-trajectory-domain-call-projection.md) or the [MCP bridge](2026-08-27-tau2-eval-mcp-bridge.md): successful `use_tool` records still become τ `tool_calls` after `run()`. Stage-4 `cordis.eval.yml` still disables skill, subagent, todo, and web ([workspace policy](2026-08-27-tau2-eval-cordis-workspace-policy.md)). Full-group leaderboard numbers remain incomparable to τ's paper agent. This five-task airline sample scored 5/5 on both `full` and `ablation`; a complete-group drop would still be catalog noise, not a broken bridge.
