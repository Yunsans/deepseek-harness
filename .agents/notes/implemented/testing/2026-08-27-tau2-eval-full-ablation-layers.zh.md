# Agent Note：τ² eval 完整组与消融组工具分层

Status: implemented

[English](2026-08-27-tau2-eval-full-ablation-layers.md) | 中文

## 问题

阶段 4 的 τ² 评测在 bash、fs 旁边用 MCP 领域工具给 mock `create_task_1` 打分，skill、subagent、todo、web 关闭。这回答不了 harness 其余工具表会把官方 `DB`×`COMMUNICATE` 改多少，而且 `cordis.eval.yml` 无法在同一组 `task_ids` 和 seed 上切换完整组或「只留 MCP」的消融组。

## 决策

`eval/tau2/run.py --layer` / `--layers` 选择具名组合；`--suite` 跑 mock `create_task_1` 加上 airline test-split 的 `2,6,8,13,16`，seed 为 42。`--stop-on-collapse`（默认开）在该 mock 的 reward 不是 1.0 时跳过本层剩余 task 以及后续 `--layers`。会话写在 `eval/tau2/.work/stage5/{layer}/`。`cordis.eval.yml` 仍是阶段 4 基线。

| 层 | 文件 | 相对基线多出来的模型可见工具 |
|---|---|---|
| `5a` | `cordis.eval.5a.yml` | `skills.enabled: true`；`dshHome` 为 `DSH_CWD`，不读 `~/.dsh` 的 skill |
| `5b` | `cordis.eval.5b.yml` | 5a 加上 todo 与进程内 spawn 子代理（同一进程组合，子级能看到 `mcp__tau2__*`） |
| `full`（`5c`） | `cordis.eval.full.yml` | 5b 加上仅搜索的 `web_search`（`fetch: false`） |
| `ablation` | `cordis.eval.ablation.yml` | 只留 MCP + 对话（`toolBash: false`、关掉 `tool-fs`，skill/subagent/todo/web 关） |
| `5e` | `cordis.eval.5e.yml` | full 加上 `tools.mode: code` 与 `@deepseek-ai/dsh-code-runtime-worker-thread` |

plan mode 保持关闭：`exit_plan_mode` 经 `ctx.userQuestions` 做审阅，JSON-RPC 评测组合没有审阅 UI。`danger-full-access` 不会自动批准这些审阅。LSP 保持关闭：客服 workspace 没有 language server，该工具只会成为死的工具表噪声。

完整组分数低于消融组是预期的工具表噪声信号，不是桥坏了。

## 测试

2026-08-27、seed 42、`deepseek-v4-flash` 的 mock `create_task_1`：上表每一层 reward 均为 1.0，`DB=1`，`COMMUNICATE=1`，`policy_in_session_jsonl=True`。第一轮工具表：`5a` 增加 `skill`；`5b` 增加 `subagent` 与 `todo_write`；`full` 增加 `web_search`；`ablation` 只有 `mcp__tau2__*`；`5e` 只有 `run_code`，领域写入仍能投影。领域调用仍是 `mcp__tau2__get_users` 再 `mcp__tau2__create_task`，`5e` 除外（`run_code`）。

同一 seed 的 airline test-split `2,6,8,13,16`：`full` 为 5/5；`ablation` 在对两次 τ `UserMessage` 构造失败（用户模拟器空话轮）各重试一次后为 5/5。第一次跑里已经打出分的消融 trial 也是 1.0，缺口不是 DB 对不上。`run.py` 对该构造错误重试一次，并写入 `eval/tau2/.work/stage5/summary.json`。到达 `generate_next_message` 的空用户文本会请模拟器再说一遍，并仍追加一个空的投影槽。

复跑：

```bash
export UV_PROJECT_ENVIRONMENT="$PWD/tmp/py-sdk-venv"
uv run --project python/sdk python eval/tau2/run.py --layer full --suite
uv run --project python/sdk python eval/tau2/run.py --layer ablation --suite
```

## 曾考虑的替代方案

**一份打开全部 dsh-base 行的 `cordis.eval.full.yml`，包括 plan 与 LSP。** 拒绝，因为无人值守的 `exit_plan_mode` 会卡住 trial，且这些 workspace 没有 LSP server。Code Mode 单独放在 `5e`，以便原生工具崩分时可以隔离。

**对 `cordis.eval.yml` 再套一层 include 来打后续层的 patch。** 拒绝，因为 include 的 patch 只打到被 include 文件自己的行，而 `cordis.eval.yml` 本身已是 include，agent-spine 配置到不了，除非改写嵌套 include。

**AGENTS.md 不写 HTTP，但仍挂着 `tool-bash`，以此关掉 bash。** 拒绝，因为这回答不了消融问题：模型仍可以 curl。`toolBash: false` 再禁用 `tool-fs` 才是「只留 MCP + 对话」的工具表。

**把完整组低于消融组当成 harness 缺陷。** 拒绝：阶段 5 问的是多余工具表会让 τ 分数掉多少。打分路径仍是投影和共享 toolkit。

**用户模拟器发出空 `UserMessage` 时直接判该 trial 失败。** 拒绝：`run.py` 对此重试一次，因为这是 LiteLLM 空话轮，不是领域工具没打中。第二次仍失败则记录 `fail_reason`，不会卡住。

## 后果

切换组合不改变[轨迹投影](2026-08-27-tau2-trajectory-domain-call-projection.zh.md)或 [MCP 桥](2026-08-27-tau2-eval-mcp-bridge.zh.md)：成功的 `use_tool` 记录仍在 `run()` 之后变成 τ 的 `tool_calls`。阶段 4 的 `cordis.eval.yml` 仍关闭 skill、subagent、todo 和 web（[workspace 政策](2026-08-27-tau2-eval-cordis-workspace-policy.zh.md)）。完整组的分数仍不能直接对比 τ 论文里的 agent。这五条 airline 样本在 `full` 和 `ablation` 上都是 5/5；若完整组以后掉分，仍是工具表噪声，不是桥坏了。
