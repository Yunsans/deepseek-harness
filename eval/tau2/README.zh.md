# τ²-bench 评测

[English](README.md) | 中文

本仓库检出可以作为 [τ²-bench](https://github.com/sierra-research/tau2-bench) 的 system-under-test。胶水在本目录：每个用户话轮交给 `DeepSeekHarness.run()`，领域写打到 Orchestrator 的 toolkit，成功调用在官方 `DB` × `COMMUNICATE` 打分前投影进轨迹。这不是 τ 的 `LLMAgent`。

所有命令都在 **DeepSeek Harness 仓库根**执行。不要 `cd eval/tau2`。

## 前置条件

| 要求 | 说明 |
|---|---|
| Node.js | `^22.19 \|\| >=24` |
| pnpm | workspace 安装 |
| Python | `>=3.12,<3.14` |
| [uv](https://docs.astral.sh/uv/) | SDK 用的 Python 环境 |
| [τ²-bench](https://github.com/sierra-research/tau2-bench) | 另克隆一份；设置 `TAU2_ROOT` |
| `DEEPSEEK_API_KEY` | 支付 dsh agent，以及 DeepSeek 用户模拟器 |

| 变量 | 作用 |
|---|---|
| `TAU2_ROOT` | τ²-bench 检出。未设置时默认 `$HOME/Desktop/projects/tau2-bench` |
| `TAU2_DATA_DIR` | 仿真输出；默认 `$TAU2_ROOT/data` |
| `UV_PROJECT_ENVIRONMENT` | 只用 `$PWD/tmp/py-sdk-venv` |
| `DSH_MODEL` | agent 模型；默认 `deepseek-v4-flash` |
| `TAU2_USER_LLM` | 用户模拟器的 LiteLLM 名。DeepSeek 必须写成 `deepseek/<model>` |
| `OPENAI_API_KEY` | 仅当用户模拟器是 `gpt-4.1-2025-04-14` 时需要 |

不要把 `DEEPSEEK_API_KEY` 复制进 `OPENAI_API_KEY`。不带前缀的模型名会送到 OpenAI。

## 克隆与安装

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

若已激活 `.venv`，先 `deactivate`。`uv pip install -e` 必须打到 `tmp/py-sdk-venv`，这样 `import tau2` 和 `import deepseek_harness` 共用一个解释器。

不调 LLM 的自检：

```bash
uv run --project python/sdk python eval/tau2/run.py --check-hash
```

保持 `--max-concurrency 1` 和 `workers=0`。factory 和投影 hook 是进程内全局量。

## 运行

### 冒烟（mock，一题）

```bash
uv run --project python/sdk python eval/tau2/run.py --domain mock --task-id create_task_1
```

### airline `base`，1 trial（DeepSeek 用户模拟器）

`--layer ablation` 只留 MCP 和对话。只需 `DEEPSEEK_API_KEY`。

```bash
uv run --project python/sdk python eval/tau2/run.py \
  --layer ablation --domain airline --split base \
  --save-to dsh-ablation-airline-base
```

同一 `--save-to` 可续跑（`auto_resume=True`）。retail / telecom 换 `--domain`。不要传 `banking_knowledge`。

### 四 trial，DeepSeek 用户模拟器

```bash
TAU2_USER_LLM=deepseek/deepseek-v4-flash \
  zsh eval/tau2/run-comparable.zsh airline
```

写到 `$TAU2_DATA_DIR/simulations/dsh-ablation-airline-base-t4-dsuser/`。去掉 `airline` 则跑 airline、retail、telecom。

### Sierra 可比 Pass^k（OpenAI 用户模拟器）

需要真正的 `OPENAI_API_KEY`。用户模型为 `gpt-4.1-2025-04-14`，4 trial，seed 300。没有该 key 时脚本退出并打印上面的 DeepSeek 命令。

```bash
zsh eval/tau2/run-comparable.zsh
zsh eval/tau2/run-comparable.zsh airline
```

写到 `dsh-ablation-<domain>-base-t4`。交 leaderboard 超出范围：本 harness 不是 τ 的 `LLMAgent`。

## 查看结果

官方分数在 τ 的仿真目录，不在 `eval/tau2/.work/`：

```text
$TAU2_DATA_DIR/simulations/<save-to>/results.json
```

```bash
export TAU2_DATA_DIR="$TAU2_ROOT/data"
cd "$TAU2_ROOT"
uv run tau2 view --file data/simulations/dsh-ablation-airline-base/results.json
```

`--only-show-failed` 只列出失败题。`run.py` 结束时打印的 Average Reward / Pass^k / DB Match 就是 τ 的 `compute_metrics`。

## 本目录文件

| 文件 | 作用 |
|---|---|
| `run.py` | 注册 `dsh_agent`；单题或 `--split` |
| `run-comparable.zsh` | 4 trial 启动器；DeepSeek 与 OpenAI 用户模拟器 |
| `dsh_agent.py` | τ `HalfDuplexAgent`；只回文本 |
| `env_bridge.py` | 把 Orchestrator toolkit 挂到 HTTP + MCP |
| `project_trajectory.py` | 把成功调用投影进轨迹 |
| `prompts.py` | 每题 `AGENTS.md` / `TOOLS.md` / `ENV_API.txt` |
| `classify.py` | 失败标签与 JSONL 的 MCP/bash/compaction 计数 |
| `probe.py` | 不经 Orchestrator 的 SDK 双轮冒烟 |
| `requirements.txt` | fastapi、uvicorn、mcp |
| `cordis.eval.yml` | 基线组合 |
| `cordis.eval.5a.yml` | + skill |
| `cordis.eval.5b.yml` | + todo + subagent |
| `cordis.eval.full.yml` | + web_search |
| `cordis.eval.ablation.yml` | 对比层：MCP + 对话 |
| `cordis.eval.5e.yml` | Code Mode |
| `plan.md` | 架构与分阶段设计 |

`.work/` 下的运行时 workspace 和 session JSONL 是生成物，已被 gitignore。

设计决策：`.agents/notes/implemented/testing/2026-08-27-tau2-*.md`。

## 不要回退

- 领域写必须打到 Orchestrator 的 toolkit（`tools[0]._func.__self__`），再投影。另起一份 `tau2 domain` 进程会对不上 DB hash。
- 适配器只返回对用户的文本。失败的 HTTP / MCP `isError` 不投影。
- 投影后的名称是 τ 原名（`create_task`），不是 `mcp__tau2__create_task`。
- 非 solo 开场白由 Orchestrator 注入；dsh 不要再打招呼。
- telecom 的 `user_tools` 不交给 dsh。
- 源码 launch：`node --import tsx packages/examples/jsonrpc-demo/src/bin.ts`。`runtime_cwd` 是 harness 根；`DSH_CWD` 是每题 workspace。
