# Agent Note: τ² trajectory domain-call projection

Status: implemented

English | [中文](2026-08-27-tau2-trajectory-domain-call-projection.zh.md)

## Problem

τ² `EnvironmentEvaluator` scores `DB` by replaying `AssistantMessage.tool_calls` from the simulation trajectory onto a fresh environment and comparing that hash to gold actions. The DeepSeek Harness adapter must return user-facing text only: if `generate_next_message` includes `tool_calls`, the Orchestrator executes those tools again and feeds `ToolMessage` back into the agent, which changes the conversation and `COMMUNICATE`. HTTP or MCP writes to the live toolkit therefore do not appear in the trajectory, so official `DB` stays 0 even when the shared database is correct.

## Decision

`eval/tau2/env_bridge.py` records each successful `toolkit.use_tool` as a `BridgeCall` whose `content` is `Environment.to_json_str` of the live return value. `DshHalfDuplexAgent` still returns text only, and groups those records per `harness.run()`. `eval/tau2/project_trajectory.py` replaces τ `run_simulation` so that after `orchestrator.run()` and before `evaluate_simulation()` it inserts one native-style `AssistantMessage(tool_calls=…)` plus `ToolMessage` pair per record, immediately before the corresponding user-facing assistant text. The orchestrator-injected opening greeting is not a harness turn. Failed HTTP requests and MCP `isError` results are omitted because they never reached the toolkit. Source of truth is the bridge audit log, not parsed bash from the dsh JSONL. MCP `call_tool` on the same `EnvBridge` appends that same record list ([MCP bridge](2026-08-27-tau2-eval-mcp-bridge.md)).

`run.py --check-hash` requires a live hash change and that an unprojected mock `create_task_1` trajectory scores `DB=0` while the projected one scores `DB=1`.

## Alternatives considered

**Return `tool_calls` from `generate_next_message`.** Rejected because the Orchestrator would re-execute domain tools and treat the next input as `ToolMessage`, which changes user simulation, termination, and `COMMUNICATE` relative to a black-box harness turn.

**Hash `orchestrator.environment` instead of using `EnvironmentEvaluator`.** Rejected because that is not the published τ scoring path. `ACTION` still reads trajectory `tool_calls`, and `tau2 view` would not show domain calls.

**Parse curl lines from the dsh session JSONL.** Rejected because retries, malformed commands, and requests that never hit the toolkit would diverge from the mutations the live toolkit actually applied.

**Fork τ to skip Orchestrator execution of agent `tool_calls`.** Rejected because glue stays in `eval/tau2/` and must not patch τ core.

## Consequences

Official `DB × COMMUNICATE` (and `ACTION` when present in `reward_basis`) apply to the same domain calls the harness made, without changing the live half-duplex protocol. `set_state(strict=True)` will raise if recorded `ToolMessage.content` does not match a fresh replay; the bridge therefore stores `Environment.to_json_str`, not the HTTP JSON encoder. Projection that drops or reorders writes produces a wrong official score rather than a live-hash override. `agent.stop()` tears down the HTTP server before evaluation, so `turn_calls` must remain on the agent instance.
