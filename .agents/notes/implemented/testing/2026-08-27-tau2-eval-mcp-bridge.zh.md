# Agent Note：τ² eval MCP 领域工具桥

Status: implemented

[English](2026-08-27-tau2-eval-mcp-bridge.md) | 中文

## 问题

阶段 3 的 τ² 评测只通过 bash curl 打本机 HTTP 桥来改 Orchestrator 的 toolkit。这测不到 `@deepseek-ai/dsh-mcp-client`。MCP URL 按 task 变化，而 `dsh-mcp-client` 在进程启动时读一次 `url`，因此若在桥监听之前就启动 harness，第一次 `initialize` 发现不了 `mcp__tau2__*`。打分仍然要求成功的 `toolkit.use_tool` 打在 Orchestrator 持有的同一对象上，记为 `BridgeCall`，并在 `evaluate_simulation()` 之前投影。

## 决策

`eval/tau2/env_bridge.py` 在同一个 127.0.0.1 端口上同时提供 HTTP（`GET /health`、`GET /tools`、`POST /tools/{name}`）和 `/mcp` 上的 Streamable HTTP MCP。MCP 的 `list_tools` 来自每条 τ `tool.openai_schema`；`call_tool` 转 `toolkit.use_tool`。两种传输上成功的调用写入同一份 `BridgeCall` 列表（`content` 为 `Environment.to_json_str`）。失败的 HTTP 与 MCP `isError` 结果不记录。

`DshHalfDuplexAgent.get_init_state` 先启动该桥，写入 AGENTS.md / TOOLS.md / ENV_API.txt（MCP 名称优先，HTTP curl 作备用），再带着 `DSH_TAU2_MCP_URL` 启动 `DeepSeekHarness`。`eval/tau2/cordis.eval.yml` 挂上 `@deepseek-ai/dsh-mcp-client`：`serverName: tau2`，`transport: streamable-http`，`failOnStartupError: true`，环境变量未设时 `disabled`。`agent.stop()` 先关 harness 再拆桥；`turn_calls` 留给投影。

`run.py --check-hash` 要求 HTTP 哈希变化、仅 MCP `call_tool`（无 HTTP POST）的哈希变化、未投影的 mock `create_task_1` 为 `DB=0` 且由 MCP 记录投影后为 `DB=1`，以及 per-task AGENTS.md 隔离。会话写在 `eval/tau2/.work/stage4/`。

## 曾考虑的替代方案

**MCP URL 只写在 workspace 文件里，并复用长寿命 harness。** 拒绝，因为 `dsh-mcp-client` 在进程启动时插值 `url`；第一次 `initialize` 会看不到工具，除非 URL 在启动前就进入子进程环境。

**为 MCP 另建一套审计列表或解析器，与 HTTP 的 `BridgeCall` 分开。** 拒绝，因为[轨迹投影](2026-08-27-tau2-trajectory-domain-call-projection.zh.md)已经从一份成功的 `use_tool` 日志打分。解析 dsh JSONL 里的 curl 或 MCP 名称会漏记、重记，或把从未打到 toolkit 的调用算进去。

**在 `DeepSeekHarness` 之后再开 MCP，靠重连发现工具。** v1 拒绝：Streamable HTTP 的重连是按请求的，不是监督进程拉起，且发现必须在第一轮 prompt 前成功时，`failOnStartupError: true` 才是显式失败路径。

**关掉 bash，让模型无法 curl。** 消融组合是 `cordis.eval.ablation.yml`（[完整组与消融组](2026-08-27-tau2-eval-full-ablation-layers.zh.md)）。HTTP 仍作备用和 `--check-hash`；AGENTS.md 要求优先使用 `mcp__tau2__*`。

## 后果

成功 initialize 后的第一轮 `request/header` 必须在 bash 与 fs 工具旁列出 `mcp__tau2__*`。官方 `DB` 仍来自投影后的 τ 工具名（`create_task`），不是 `mcp__tau2__create_task`。缺少 `DSH_TAU2_MCP_URL` 会跳过 mcp-client 行；URL 已设但发现失败会中止 initialize。评测 Python 需要 `tmp/py-sdk-venv` 中的 `mcp>=1.12,<2`（[eval/tau2/requirements.txt](../../../../eval/tau2/requirements.txt)）。
