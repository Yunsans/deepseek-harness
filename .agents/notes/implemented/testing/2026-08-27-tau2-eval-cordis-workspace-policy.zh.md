# Agent Note：τ² eval cordis workspace policy

Status: implemented

[English](2026-08-27-tau2-eval-cordis-workspace-policy.md) | 中文

## 问题

阶段 2 的 τ² 评测使用 `examples/jsonrpc-agent/minimal.cordis.yml`：它关闭工作区指令，模型只看到 persistent bash 与 `str_replace_editor`。domain policy 被拼进第一轮 `harness.run` 的 prompt。这测不到 `dsh-agent-instructions`；若按默认 project-root 标记打开工作区加载，还会把仓库 `AGENTS.md` 或 `~/.dsh/AGENTS.md` 泄漏进请求，并且无法按 task 隔离政策与 API 端口。

## 决策

`eval/tau2/cordis.eval.yml` include `examples/jsonrpc-agent/cordis.yml`，关掉 subagent 与 todo，把 `dsh-sandbox-policy` 钉为 `danger-full-access`，JSONL 不压缩，并打开 `workspaceContext`：`maxBytes: 65536`、`projectRootMarkers: [AGENTS.md]`、`dshHome` 等于 `DSH_CWD`。`eval/tau2/prompts.py` 为每个 task 写入 `AGENTS.md`（角色、开场、领域 API 规则、整份 policy）以及 `TOOLS.md` / `ENV_API.txt`。`generate_next_message` 只转发用户原话。每个 task 在领域桥监听之后启动自己的 `DeepSeekHarness`，`cwd` 为 `eval/tau2/.work/stage4/workspaces/{task_id}-{trial}/`（[MCP 桥](2026-08-27-tau2-eval-mcp-bridge.zh.md)）。`run.py --check-hash` 还要求两个临时 workspace 的政策文本和端口互不泄漏。

## 曾考虑的替代方案

**继续把 policy 拼进第一轮用户 prompt。** 拒绝，因为阶段 3 的完成标准是第一轮模型请求通过工作区指令插件看到 policy。

**保留默认 `projectRootMarkers`（`.git`）。** 拒绝，因为评测 workspace 位于本仓库内，harness 自己的 `AGENTS.md` 会进入每一次请求。

**复用同一个 Harness `cwd`，原地覆盖 `AGENTS.md`。** 拒绝，因为阶段 3 要求每个 task 新的 workspace 目录（以及端口），与后续 per-task 重启运行时一致。

**挂上 `dsh-user-approval` 且 `policy: never`。** 本次组合拒绝：`never` 是自动拒绝询问，不是自动批准。无 UI 评测的立场是 `danger-full-access` sandbox policy，与 `minimal.cordis.yml` 相同。

## 后果

airline 量级的政策能放进 65536 字节的指令预算。compaction 仍来自 jsonrpc-agent 骨架。MCP 领域工具见[MCP 桥](2026-08-27-tau2-eval-mcp-bridge.zh.md)。`cordis.eval.yml` 仍关闭 skill、web、plan、Code Mode 和 ask-user；后续分层见[完整组与消融组](2026-08-27-tau2-eval-full-ablation-layers.zh.md)。若第一轮 JSONL 找不到 policy 针，说明 `dsh-agent-instructions` 没有加载 `AGENTS.md`，或 `dshHome` 仍指向 task workspace 之外。
