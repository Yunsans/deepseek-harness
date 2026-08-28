# Agent Note: τ² eval 在官方 split 上规模化

Status: implemented

[English](2026-08-27-tau2-eval-scale-out.md) | 中文

## 问题

阶段 5 用 `run_single_task` 给 5 条 airline 样本打分，结果只写在 `eval/tau2/.work/stage5/`。这给不出官方 split 的 `DB`×`COMMUNICATE` 表，不能把 50 条 airline `base` trial 续跑进 τ 的 `data/simulations/` 供 `tau2 view` 使用，也不能把失败分成用户模拟器提前停、拒写、工具参数错和 COMMUNICATE 漏说。

## 决策

`eval/tau2/run.py --split` 调用 τ 的 `run_domain`：`num_trials=1`、`max_concurrency=1`、`workers=0`、`auto_resume=True`，`timeout` 默认 900 秒（同时作为 SDK 的 `request_timeout_seconds`）。结果落在 `$TAU2_DATA_DIR/simulations/<save-to>/results.json`。默认 `--save-to` 为 `dsh-{layer}-{domain}-{split}`。`create_dsh_agent` 把 `work/workspaces/{task_id}-{hex}` 当作 `DSH_CWD`，因此 `run_domain` 不会跨 task 共用一个 workspace。`workers` 保持为 0：factory、work root 和投影 hook 都是进程内全局量。

与 τ 论文 agent 比数字时用 `--layer ablation`（只留 MCP + 对话）。`--layer full` 是工具表噪声对照。CLI 拒绝 `banking_knowledge`。telecom 的 `user_tools` 在非 solo 的 `build_agent` 里本来就不会传给 dsh；factory 还会丢掉类名含 `User` 的 toolkit。

`eval/tau2/classify.py` 给每次 trial 打标签：`user_sim_early_stop`（USER_STOP 且未调工具）、`model_refused_write`（只有查询工具且 DB=0）、`tool_arg_error`（JSONL 工具出错且 DB=0）、`COMMUNICATE`、以及 `DB`。同一扫描统计 `mcp__tau2__*` 调用、`bash` 和 `compaction/*` 事件。

与 Sierra 核实过的榜单列对齐的 Pass^k 使用 `--layer ablation`、`--split base`、`--num-trials 4`、`--seed 300`、以及 `--user-llm gpt-4.1-2025-04-14`。该用户模型走 LiteLLM 的 OpenAI 路由：需要真正的 `OPENAI_API_KEY`（代理可选 `OPENAI_API_BASE` / `OPENAI_BASE_URL`）。`DEEPSEEK_API_KEY` 不是 OpenAI key，不能复制进 `OPENAI_API_KEY`。只有 DeepSeek key 的实验室把 `TAU2_USER_LLM=deepseek/deepseek-v4-flash`（LiteLLM 必须带 `deepseek/` 前缀）；这类 run 写到 `$TAU2_DATA_DIR/simulations/dsh-ablation-<domain>-base-t4-dsuser/`，不会和之后的 gpt-4.1 checkpoint 混在一起。`eval/tau2/run-comparable.zsh` 可从任意 cwd 跑 airline、retail 和 telecom。

```bash
export UV_PROJECT_ENVIRONMENT="$PWD/tmp/py-sdk-venv"
uv run --project python/sdk python eval/tau2/run.py \
  --layer ablation --domain airline --split base \
  --save-to dsh-ablation-airline-base
```

```bash
zsh eval/tau2/run-comparable.zsh
```

## 测试

`run.py --check-hash` 含 `classify.self_test`，覆盖通过以及四类失败标签（仅 COMMUNICATE、拒写、用户模拟器提前停、工具参数错）。airline `base` 是 50 个 task id（τ `split_tasks.json` 的 train∪test）。`--num-tasks 1` 的冒烟写入同一 `--save-to` 目录；其余 49 条靠 `auto_resume=True` 续跑。会话写在 `eval/tau2/.work/stage6/{layer}/{domain}/`。`tau2 view` 读 `$TAU2_DATA_DIR/simulations/`。

## 考虑过的替代方案

**继续循环 `run_single_task`，事后把 JSON 拷进 `data/simulations/`。** 否决：`run_domain` 已经负责 checkpoint、续跑、指标，以及 `tau2 view` 读取的路径。在 `run.py` 里再实现一遍会漂移。

**50 条任务用 `workers>0` 或 `max_concurrency>1`。** v1 否决：注册的 factory、`set_workspace` 和 `run_simulation` 投影 hook 都是进程内的。并行 worker 会错过 hook，或共用一个 work root。

**规模化默认 `--layer full`。** 否决：额外工具表是阶段 5 的噪声问题。消融组才接近论文 agent；完整组保持为显式对照。

**把 telecom 的 `user_tools` 暴露给 dsh 去操作设备。** 否决：那些工具属于用户模拟器。半双工打分假定用户侧自己改它们。

## 后果

airline `base` 的 1 trial 就是该域的标准文本评测集；retail 和 telecom 用同一套 CLI。这个数字仍不能交 leaderboard：`run.py` 默认用户模拟器是 `deepseek/deepseek-v4-flash`，完整 harness 也不是 τ 的 `LLMAgent`。`eval/tau2/run-comparable.zsh` 会钉死 `--user-llm gpt-4.1-2025-04-14` 和 `--layer ablation` 以对齐 Pass^k 列；`TAU2_USER_LLM=deepseek/deepseek-v4-flash` 是只有 DeepSeek key 的实验室路径，结果写 `*-t4-dsuser`。阶段 7 是在同一份投影日志上跑 `tau2 view`，不是第二套打分器。
