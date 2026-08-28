# Agent Note：τ² 轨迹领域调用投影

Status: implemented

[English](2026-08-27-tau2-trajectory-domain-call-projection.md) | 中文

## 问题

τ² 的 `EnvironmentEvaluator` 计算 `DB` 时，会把轨迹里的 `AssistantMessage.tool_calls` 回放到一份新建的 environment 上，再与 gold actions 的哈希比较。DeepSeek Harness 适配器必须只返回对用户的文本：若 `generate_next_message` 带上 `tool_calls`，Orchestrator 会再执行一遍领域工具，并把 `ToolMessage` 作为下一拍输入，从而改变对话和 `COMMUNICATE`。因此 HTTP 或 MCP 对现场 toolkit 的写入不会出现在轨迹里，即使共享数据库已经写对，官方 `DB` 仍为 0。

## 决策

`eval/tau2/env_bridge.py` 把每次成功的 `toolkit.use_tool` 记为 `BridgeCall`，其 `content` 是现场返回值的 `Environment.to_json_str`。`DshHalfDuplexAgent` 仍只返回文本，并按每次 `harness.run()` 分组这些记录。`eval/tau2/project_trajectory.py` 替换 τ 的 `run_simulation`：在 `orchestrator.run()` 之后、`evaluate_simulation()` 之前，在对应的对用户 assistant 文本前插入原生形态的 `AssistantMessage(tool_calls=…)` 与 `ToolMessage` 对。Orchestrator 注入的开场白不是一次 harness 话轮。失败的 HTTP 与 MCP `isError` 结果不记录，因为它们从未碰到 toolkit。事实来源是桥的审计日志，不是从 dsh JSONL 解析 curl。同一 `EnvBridge` 上的 MCP `call_tool` 写入同一份记录列表（[MCP 桥](2026-08-27-tau2-eval-mcp-bridge.zh.md)）。

`run.py --check-hash` 要求现场哈希变化，并且未投影的 mock `create_task_1` 轨迹 `DB=0`、投影后 `DB=1`。

## 曾考虑的替代方案

**由 `generate_next_message` 返回 `tool_calls`。** 拒绝，因为 Orchestrator 会再执行领域工具，并把下一拍当作 `ToolMessage`，相对于黑盒 harness 话轮会改变用户模拟、终止原因和 `COMMUNICATE`。

**哈希 `orchestrator.environment`，不再使用 `EnvironmentEvaluator`。** 拒绝，因为那不是对外的 τ 打分路径。`ACTION` 仍从轨迹读取 `tool_calls`，`tau2 view` 也看不到领域调用。

**从 dsh 会话 JSONL 解析 curl 行。** 拒绝，因为重试、畸形命令、以及从未打到 toolkit 的请求会与现场 toolkit 实际发生的写入不一致。

**Fork τ，让 Orchestrator 跳过执行 agent 的 `tool_calls`。** 拒绝，因为胶水留在 `eval/tau2/`，不得改 τ 核心。

## 后果

官方 `DB × COMMUNICATE`（以及 `reward_basis` 含 `ACTION` 时的该项）作用于 harness 实际发出的领域调用，且不改变现场半双工协议。若记录的 `ToolMessage.content` 与干净回放不一致，`set_state(strict=True)` 会抛错；因此桥存储 `Environment.to_json_str`，而不是 HTTP JSON 编码器的输出。投影漏写或写乱顺序会得到错误的官方分数，而不会退回现场哈希覆盖。`agent.stop()` 在评测前拆除 HTTP 服务，因此 `turn_calls` 必须留在 agent 实例上。
