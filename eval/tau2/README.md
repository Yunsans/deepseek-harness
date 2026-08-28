# τ²-bench eval

English | [中文](README.zh.md)

This checkout can run as the system-under-test on [τ²-bench](https://github.com/sierra-research/tau2-bench). Glue lives in this directory: each user turn goes through `DeepSeekHarness.run()`, domain writes hit the Orchestrator's toolkit, and successful calls are projected into the trajectory before official `DB` × `COMMUNICATE` scoring. This is not τ's `LLMAgent`.

Run every command from the **DeepSeek Harness repository root**. Do not `cd eval/tau2`.

## Prerequisites

| Requirement | Notes |
|---|---|
| Node.js | `^22.19 \|\| >=24` |
| pnpm | workspace install |
| Python | `>=3.12,<3.14` |
| [uv](https://docs.astral.sh/uv/) | Python env for the SDK |
| [τ²-bench](https://github.com/sierra-research/tau2-bench) | separate clone; set `TAU2_ROOT` |
| `DEEPSEEK_API_KEY` | pays the dsh agent, and the DeepSeek user simulator |

| Variable | Role |
|---|---|
| `TAU2_ROOT` | τ²-bench checkout. Default if unset: `$HOME/Desktop/projects/tau2-bench` |
| `TAU2_DATA_DIR` | simulation output; default `$TAU2_ROOT/data` |
| `UV_PROJECT_ENVIRONMENT` | use `$PWD/tmp/py-sdk-venv` only |
| `DSH_MODEL` | agent model; default `deepseek-v4-flash` |
| `TAU2_USER_LLM` | LiteLLM id for the user simulator. DeepSeek must be `deepseek/<model>` |
| `OPENAI_API_KEY` | only when the user simulator is `gpt-4.1-2025-04-14` |

Do not copy `DEEPSEEK_API_KEY` into `OPENAI_API_KEY`. An unprefixed model name is routed to OpenAI.

## Clone and install

```bash
git clone https://github.com/Yunsans/deepseek-harness.git
cd deepseek-harness
git clone https://github.com/sierra-research/tau2-bench.git "$HOME/tau2-bench"

export TAU2_ROOT="$HOME/tau2-bench"
export TAU2_DATA_DIR="$TAU2_ROOT/data"
export UV_PROJECT_ENVIRONMENT="$PWD/tmp/py-sdk-venv"
export DEEPSEEK_API_KEY=...   # or put it in a root .env (never commit)

pnpm install
uv sync --project python/sdk
uv pip install --python tmp/py-sdk-venv/bin/python -e "$TAU2_ROOT"
uv pip install --python tmp/py-sdk-venv/bin/python -r eval/tau2/requirements.txt
```

If a `.venv` is active, `deactivate` first. `uv pip install -e` must target `tmp/py-sdk-venv` so `import tau2` and `import deepseek_harness` share one interpreter.

Keyless self-check:

```bash
uv run --project python/sdk python eval/tau2/run.py --check-hash
```

Keep `--max-concurrency 1` and `workers=0`. The factory and projection hook are in-process globals.

## Run

### Smoke (mock, one task)

```bash
uv run --project python/sdk python eval/tau2/run.py --domain mock --task-id create_task_1
```

### Airline `base`, 1 trial (DeepSeek user simulator)

`--layer ablation` keeps MCP and conversation only. Needs only `DEEPSEEK_API_KEY`.

```bash
uv run --project python/sdk python eval/tau2/run.py \
  --layer ablation --domain airline --split base \
  --save-to dsh-ablation-airline-base
```

The same `--save-to` resumes (`auto_resume=True`). Swap `--domain retail` or `--domain telecom` for those splits. Do not pass `banking_knowledge`.

### Four trials, DeepSeek user simulator

```bash
TAU2_USER_LLM=deepseek/deepseek-v4-flash \
  zsh eval/tau2/run-comparable.zsh airline
```

Writes `$TAU2_DATA_DIR/simulations/dsh-ablation-airline-base-t4-dsuser/`. Omit `airline` to run airline, retail, and telecom.

### Sierra-comparable Pass^k (OpenAI user simulator)

Needs a real `OPENAI_API_KEY`. User model is `gpt-4.1-2025-04-14`, 4 trials, seed 300. Without that key the script exits and prints the DeepSeek command above.

```bash
zsh eval/tau2/run-comparable.zsh
zsh eval/tau2/run-comparable.zsh airline
```

Writes `dsh-ablation-<domain>-base-t4`. Leaderboard submission is out of scope: this harness is not τ's `LLMAgent`.

## View results

Official scores are in τ's simulation directory, not under `eval/tau2/.work/`:

```text
$TAU2_DATA_DIR/simulations/<save-to>/results.json
```

```bash
export TAU2_DATA_DIR="$TAU2_ROOT/data"
cd "$TAU2_ROOT"
uv run tau2 view --file data/simulations/dsh-ablation-airline-base/results.json
```

`--only-show-failed` lists failed tasks. The Average Reward / Pass^k / DB Match table printed at the end of `run.py` is τ `compute_metrics`.

## Files in this directory

| File | Role |
|---|---|
| `run.py` | Register `dsh_agent`; single task or `--split` |
| `run-comparable.zsh` | 4-trial launcher; DeepSeek vs OpenAI user sim |
| `dsh_agent.py` | τ `HalfDuplexAgent`; text-only replies |
| `env_bridge.py` | HTTP + MCP on the Orchestrator toolkit |
| `project_trajectory.py` | Project successful calls onto the trajectory |
| `prompts.py` | Per-task `AGENTS.md` / `TOOLS.md` / `ENV_API.txt` |
| `classify.py` | Fail labels and JSONL MCP/bash/compaction counts |
| `probe.py` | SDK two-turn smoke without the Orchestrator |
| `requirements.txt` | fastapi, uvicorn, mcp |
| `cordis.eval.yml` | Baseline composition |
| `cordis.eval.5a.yml` | + skill |
| `cordis.eval.5b.yml` | + todo + subagent |
| `cordis.eval.full.yml` | + web_search |
| `cordis.eval.ablation.yml` | Comparison layer: MCP + conversation |
| `cordis.eval.5e.yml` | Code Mode |
| `plan.md` | Architecture and staged design |

Runtime workspaces and session JSONL under `.work/` are generated and gitignored.

Design decisions: `.agents/notes/implemented/testing/2026-08-27-tau2-*.md`.

## Do not regress

- Domain writes must hit the Orchestrator toolkit (`tools[0]._func.__self__`) and then be projected. A separate `tau2 domain` process will not match DB hashes.
- The adapter returns user-facing text only. Failed HTTP / MCP `isError` calls are not projected.
- Projected names are raw τ names (`create_task`), not `mcp__tau2__create_task`.
- Non-solo greeting is injected by the Orchestrator; dsh must not greet again.
- Telecom `user_tools` are not passed to dsh.
- Source launch: `node --import tsx packages/examples/jsonrpc-demo/src/bin.ts`. `runtime_cwd` is the harness root; `DSH_CWD` is the per-task workspace.
