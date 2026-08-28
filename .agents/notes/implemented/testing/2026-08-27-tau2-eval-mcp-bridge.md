# Agent Note: τ² eval MCP domain-tool bridge

Status: implemented

English | [中文](2026-08-27-tau2-eval-mcp-bridge.zh.md)

## Problem

Stage-3 τ² eval mutated the Orchestrator's toolkit only through bash curl to a localhost HTTP bridge. That does not exercise `@deepseek-ai/dsh-mcp-client`. The MCP URL is per-task, and `dsh-mcp-client` reads `url` once at process start, so a harness started before the bridge listens cannot discover `mcp__tau2__*` on the first `initialize`. Scoring still requires successful `toolkit.use_tool` calls on the same object the Orchestrator holds, recorded as `BridgeCall` and projected before `evaluate_simulation()`.

## Decision

`eval/tau2/env_bridge.py` serves assistant tools on one 127.0.0.1 port as HTTP (`GET /health`, `GET /tools`, `POST /tools/{name}`) and as Streamable HTTP MCP at `/mcp`. MCP `list_tools` copies each τ `tool.openai_schema`; `call_tool` calls `toolkit.use_tool`. Successful calls from either transport append the same `BridgeCall` list (`content` is `Environment.to_json_str`). Failed HTTP and MCP `isError` results are not recorded.

`DshHalfDuplexAgent.get_init_state` starts that bridge, writes AGENTS.md / TOOLS.md / ENV_API.txt (MCP names first, HTTP curl as backup), then starts `DeepSeekHarness` with `DSH_TAU2_MCP_URL` set. `eval/tau2/cordis.eval.yml` mounts `@deepseek-ai/dsh-mcp-client` with `serverName: tau2`, `transport: streamable-http`, `failOnStartupError: true`, and `disabled` when the env var is unset. `agent.stop()` closes the harness, then the bridge; `turn_calls` remain for projection.

`run.py --check-hash` requires an HTTP hash change, an MCP-only `call_tool` hash change (no HTTP POST), unprojected mock `create_task_1` `DB=0` and projected `DB=1` from MCP-recorded calls, and per-task AGENTS.md isolation. Sessions write under `eval/tau2/.work/stage4/`.

## Alternatives considered

**Put the MCP URL only in workspace files and keep one long-lived harness.** Rejected because `dsh-mcp-client` interpolates `url` at process start; a first `initialize` would miss tools unless the URL is in the subprocess env before boot.

**A second audit list or parser for MCP distinct from HTTP `BridgeCall`.** Rejected because [trajectory projection](2026-08-27-tau2-trajectory-domain-call-projection.md) already scores from one successful-`use_tool` log. Parsing dsh JSONL curl or MCP names would miss, double-count, or include calls that never hit the toolkit.

**Start MCP after `DeepSeekHarness` and rely on reconnect.** Rejected for v1: Streamable HTTP reconnect is per-request, not a supervisor respawn, and `failOnStartupError: true` is the loud path when discovery must succeed before the first prompt.

**Disable bash so the model cannot curl.** The ablation composition is `cordis.eval.ablation.yml` ([full vs ablation](2026-08-27-tau2-eval-full-ablation-layers.md)). HTTP remains as backup and for `--check-hash`; AGENTS.md tells the model to prefer `mcp__tau2__*`.

## Consequences

The first `request/header` after a successful initialize must list `mcp__tau2__*` next to bash and fs tools. Official `DB` still comes from projected τ tool names (`create_task`), not from `mcp__tau2__create_task`. A missing `DSH_TAU2_MCP_URL` skips the mcp-client row; a set URL that fails discovery aborts initialize. Eval Python needs `mcp>=1.12,<2` in `tmp/py-sdk-venv` ([eval/tau2/requirements.txt](../../../../eval/tau2/requirements.txt)).
