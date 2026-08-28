# Agent Note: τ² eval cordis workspace policy

Status: implemented

English | [中文](2026-08-27-tau2-eval-cordis-workspace-policy.zh.md)

## Problem

Stage-2 τ² eval used `examples/jsonrpc-agent/minimal.cordis.yml`, which disables workspace instructions and exposes only persistent bash plus `str_replace_editor`. Domain policy was concatenated into the first `harness.run` prompt. That does not exercise `dsh-agent-instructions`, can leak the repository `AGENTS.md` or `~/.dsh/AGENTS.md` if workspace loading is turned on with default project-root markers, and cannot isolate policy and API port per task.

## Decision

`eval/tau2/cordis.eval.yml` includes `examples/jsonrpc-agent/cordis.yml`, disables subagent and todo rows, pins `dsh-sandbox-policy` to `danger-full-access`, writes uncompressed JSONL, and enables `workspaceContext` with `maxBytes: 65536`, `projectRootMarkers: [AGENTS.md]`, and `dshHome` equal to `DSH_CWD`. `eval/tau2/prompts.py` writes each task's `AGENTS.md` (role, opening, domain-API rules, full policy) plus `TOOLS.md` / `ENV_API.txt`. `generate_next_message` forwards only the customer utterance. Each task starts its own `DeepSeekHarness` after the domain bridge listens, with `cwd` at `eval/tau2/.work/stage4/workspaces/{task_id}-{trial}/` ([MCP bridge](2026-08-27-tau2-eval-mcp-bridge.md)). `run.py --check-hash` also requires two temporary workspaces to keep distinct policy text and ports.

## Alternatives considered

**Keep concatenating policy into the first user prompt.** Rejected because stage 3's completion criterion is that the first model request sees policy through the workspace-instruction plugin.

**Leave `projectRootMarkers` at the default `.git`.** Rejected because the eval workspace sits inside this repository, so the harness `AGENTS.md` would enter every request.

**Reuse one Harness `cwd` and overwrite `AGENTS.md` in place.** Rejected because stage 3 requires a new workspace directory (and port) per task, matching later per-task runtime restarts.

**Mount `dsh-user-approval` with `policy: never`.** Rejected for this composition: `never` auto-rejects asks rather than auto-approving them. `danger-full-access` sandbox policy is the unattended stance, as in `minimal.cordis.yml`.

## Consequences

Airline-scale policies fit the 65536-byte instruction budget. Compaction remains mounted from the jsonrpc-agent skeleton. MCP domain tools are mounted in [the MCP bridge note](2026-08-27-tau2-eval-mcp-bridge.md). `cordis.eval.yml` keeps skill, web, plan, Code Mode, and ask-user off; later layers are [full vs ablation](2026-08-27-tau2-eval-full-ablation-layers.md). A first-request JSONL that lacks the policy needle means `dsh-agent-instructions` did not load `AGENTS.md` or `dshHome` still pointed outside the task workspace.
