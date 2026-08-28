# Agent Note: τ² eval scale-out on official splits

Status: implemented

English | [中文](2026-08-27-tau2-eval-scale-out.zh.md)

## Problem

Stage 5 scores a five-task airline sample through `run_single_task` and writes only under `eval/tau2/.work/stage5/`. That cannot produce an official-split `DB`×`COMMUNICATE` table, cannot resume a 50-task airline `base` trial into τ's `data/simulations/` for `tau2 view`, and does not classify failures into user-sim early stop, refused writes, tool-arg errors, and COMMUNICATE misses.

## Decision

`eval/tau2/run.py --split` calls τ `run_domain` with `num_trials=1`, `max_concurrency=1`, `workers=0`, `auto_resume=True`, and `timeout` default 900s (also the SDK `request_timeout_seconds`). Results land at `$TAU2_DATA_DIR/simulations/<save-to>/results.json`. Default `--save-to` is `dsh-{layer}-{domain}-{split}`. `create_dsh_agent` allocates `work/workspaces/{task_id}-{hex}` as `DSH_CWD` so `run_domain` does not share one workspace across tasks. `workers` stays 0: the factory, work root, and projection hook are in-process globals.

`--layer ablation` is the composition for numbers compared to τ's paper agent (MCP + conversation). `--layer full` is the catalog-noise contrast. `banking_knowledge` is rejected at CLI. Telecom `user_tools` are not passed in non-solo `build_agent`; the factory also drops any toolkit whose class name contains `User`.

`eval/tau2/classify.py` labels each trial: `user_sim_early_stop` (USER_STOP and no tools), `model_refused_write` (DB=0 after lookup tools only), `tool_arg_error` (JSONL tool errors plus DB=0), `COMMUNICATE`, and `DB`. The same scan counts `mcp__tau2__*` calls, `bash`, and `compaction/*` events.

Pass^k numbers that match Sierra-verified leaderboard columns use `--layer ablation`, `--split base`, `--num-trials 4`, `--seed 300`, and `--user-llm gpt-4.1-2025-04-14`. That user model is LiteLLM's OpenAI route: it needs a real `OPENAI_API_KEY` (optional `OPENAI_API_BASE` / `OPENAI_BASE_URL` for a proxy). `DEEPSEEK_API_KEY` is not an OpenAI key and must not be copied into `OPENAI_API_KEY`. A DeepSeek-only lab (user simulator and dsh agent on the same key) sets `TAU2_USER_LLM=deepseek/deepseek-v4-flash`; LiteLLM requires the `deepseek/` prefix, and those runs write `$TAU2_DATA_DIR/simulations/dsh-ablation-<domain>-base-t4-dsuser/` so they never mix with a later gpt-4.1 checkpoint. `eval/tau2/run-comparable.zsh` runs airline, retail, and telecom from any cwd.

```bash
export UV_PROJECT_ENVIRONMENT="$PWD/tmp/py-sdk-venv"
uv run --project python/sdk python eval/tau2/run.py \
  --layer ablation --domain airline --split base \
  --save-to dsh-ablation-airline-base
```

```bash
zsh eval/tau2/run-comparable.zsh
```

## Testing

`run.py --check-hash` includes `classify.self_test` for pass and the four failure labels (COMMUNICATE-only, model refused write, user-sim early stop, tool-arg error). Airline `base` is 50 task ids (train∪test in τ `split_tasks.json`). A `--num-tasks 1` smoke writes the same `--save-to` directory; the remaining 49 resume with `auto_resume=True`. Sessions write under `eval/tau2/.work/stage6/{layer}/{domain}/`. `tau2 view` reads `$TAU2_DATA_DIR/simulations/`.

## Alternatives considered

**Keep looping `run_single_task` and copy JSON into `data/simulations/` afterward.** Rejected because `run_domain` already owns checkpoint, resume, metrics, and the path `tau2 view` reads. Reimplementing that in `run.py` would drift.

**`workers>0` or `max_concurrency>1` for the 50-task run.** Rejected for v1: the registered factory, `set_workspace`, and the `run_simulation` projection hook are process-local. Parallel workers would miss the hook or share one work root.

**Default `--layer full` for scale-out.** Rejected for comparability: extra catalog is the stage-5 noise question. Ablation is the paper-like agent; full remains an explicit contrast.

**Expose telecom `user_tools` so dsh can drive the device.** Rejected: those tools belong to the user simulator. Half-duplex scoring assumes the user side mutates them.

## Consequences

Airline `base` 1 trial is τ's standard text eval set for that domain; retail and telecom use the same CLI. The number is still not a leaderboard submission: `run.py` defaults the user simulator to `deepseek/deepseek-v4-flash`, and the full harness is not τ's `LLMAgent`. `eval/tau2/run-comparable.zsh` pins `--user-llm gpt-4.1-2025-04-14` and `--layer ablation` for Pass^k columns; `TAU2_USER_LLM=deepseek/deepseek-v4-flash` is a DeepSeek-only lab path and writes `*-t4-dsuser`. Stage 7 is `tau2 view` on the same projected log, not a second scorer.
