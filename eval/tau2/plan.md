# 用 τ²-bench 评测 DeepSeek Harness

本文是把本仓库智能体接到 [τ²-bench](https://github.com/sierra-research/tau2-bench) 上的实施计划。目标是：**共享 Environment + Python SDK 适配器**，让 dsh 整机作为 τ 的 system-under-test；领域写操作打到 Orchestrator 持有的同一份 toolkit，并在评测前投影进轨迹，使官方 `DB` 回放生效。阶段 0–4 的脚本已在 `eval/tau2/`。

本地 τ 仓库由环境变量 `TAU2_ROOT` 指定，默认 `$HOME/Desktop/projects/tau2-bench`。下文用 `TAU2_ROOT` 指代它。DeepSeek Harness 仓库根为 `DSH_ROOT`（本文件所在仓库）。

## 1. 先对齐的事实

τ²-bench 测的是客服智能体：模拟用户、领域 API、政策遵守。默认分数是 `DB` 终态哈希与 `COMMUNICATE` 关键字的乘积，见 τ 的 `docs/evaluation.md`。airline / retail / telecom **不**把 gold `evaluation_criteria.actions` 当作唯一合法轨迹。

DeepSeek Harness 是编码智能体：bash、fs、skill、subagent、web、plan、MCP、compaction 等。`DeepSeekHarness.run()` 会跑到 agent idle，中间可以多步调工具；τ 的 `HalfDuplexAgent.generate_next_message()` 则是一轮要么对用户说话，要么交出 `tool_calls` 给 Orchestrator 执行。

因此不能把 dsh 当成 τ 自带 `LLMAgent` 的 LiteLLM 后端——那样只测模型，不测 harness。也不能用 `dsh --profile headless`：它一次性退出，接不住 τ 的多轮用户模拟。

本方案把每个用户话轮交给 dsh 整段跑完，领域工具在 dsh 内部调用；HTTP/MCP 打到 **Orchestrator 持有的同一份 toolkit**。评测走 τ 官方回放：把这些调用投影进轨迹，而不是改打分公式，也不是在 `generate_next_message` 里交 `tool_calls`。

## 2. 架构

```text
τ Orchestrator
  UserSimulator  ←→  DshHalfDuplexAgent  ←→  dsh JSON-RPC runtime
                         │                         │
                         │                         ├── bash / fs / skill / …
                         │                         └── MCP 或 HTTP 客户端
                         │                                    │
                         └── 同一份 Environment.tools ─────────┘
                              （本机 FastAPI / MCP 暴露领域工具）
```

`EnvironmentEvaluator` **不会**哈希 Orchestrator 手里那份现场 `Environment`。它新建 predicted / gold 两份 env：predicted 回放轨迹里的 `AssistantMessage.tool_calls`，gold 回放 `evaluation_criteria.actions`，再比 DB hash。HTTP/MCP 必须打到 Orchestrator 的同一份 toolkit（现场写对是投影的事实来源），但分数生效还要把这些调用投影进 `simulation.messages`。不要另起 `tau2 domain airline`：那是另一份 env，投影回放也对不上。

`build_agent`（`TAU2_ROOT/src/tau2/runner/build.py`）的 factory 签名是 `factory(tools, domain_policy, **kwargs)`，**拿不到** `Environment`。airline 的 DB 在 `AirlineTools(db)` 上；`tools[0]._func.__self__` 就是这份 toolkit，与 `orchestrator.environment.tools` 是同一对象。桥里调用 `toolkit.use_tool` 就是现场写入；官方 `DB` 分来自把这些调用投影进轨迹后的回放。

代码全部放在本仓库 `eval/tau2/`，不改 τ 核心。在 `run.py` 里 `registry.register_agent_factory(...)` 即可被 `tau2 run --agent dsh_agent` 语义使用。

建议文件：

```text
eval/tau2/
  plan.md                  # 本文
  probe.py                 # 阶段 0：双轮 SDK 探测
  dsh_agent.py             # HalfDuplexAgent + factory
  run.py                   # 注册 agent 并调用 τ runner
  env_bridge.py            # 把 toolkit 挂到本机 HTTP 与 MCP，并记录成功调用
  project_trajectory.py    # 评测前把记录投影成 τ tool_calls
  cordis.eval.yml          # 评测用 dsh 组合（阶段 3–4：workspace policy + MCP）
  cordis.eval.5a.yml       # 阶段 5a：skill
  cordis.eval.5b.yml       # 阶段 5b：todo + subagent
  cordis.eval.full.yml     # 阶段 5 完整组（5c：再加 web_search）
  cordis.eval.ablation.yml # 阶段 5 消融：只留 MCP + 对话
  cordis.eval.5e.yml       # 阶段 5e：Code Mode
  classify.py              # 阶段 6：失败分类与 JSONL 扫描
  run-comparable.zsh       # 可对比实验：ablation + base + 4 trial + gpt-4.1 用户模拟器
  prompts.py               # 生成每 task 的 AGENTS.md / TOOLS.md
  requirements.txt         # 评测 venv 额外依赖（fastapi、uvicorn、mcp）
```

## 3. 贯穿全程的约束

**会话与隔离。** 每个 task、每个 trial 使用新的 dsh `session_id`、新的 workspace 目录、新的 Environment。τ 换 DB 时，dsh 的 bash 状态和会话日志也必须是新的。

**开场白。** 非 solo 模式下 Orchestrator 会注入固定的 `Hi! How can I help you today?`，**不会**为此调用 `generate_next_message`。dsh 的第一次 `run()` 发生在第一条真实用户消息到达时。系统说明里写明对话已经开过场，避免重复打招呼。

**运行时只返回对用户的文本；评测前再投影 tool_calls。** `generate_next_message` 只处理 `UserMessage`，返回 `AssistantMessage(content=final_response)`，不要带 `tool_calls`。一旦带上，Orchestrator 会再执行一遍领域工具，并在下一拍把 `ToolMessage` 喂回 agent，对话与 `COMMUNICATE` 都被扭曲。桥把每次成功的 `use_tool` 记进审计日志；`run.py` 替换 `run_simulation`，在 `orchestrator.run()` 之后、`evaluate_simulation()` 之前把记录插进轨迹。`ToolMessage.content` 必须是 `Environment.to_json_str`，否则 `set_state(strict=True)` 会因回放结果对不上而抛错。若收到 `ToolMessage` / `MultiToolMessage`，直接失败——说明有人把 τ 原生 tool_call 路径接回来了。

**并发与锁。** 黑盒设计下，Orchestrator 卡在 `harness.run()` 期间不会同时碰 env；用户工具要等 agent 返回文本之后才由 UserSimulator 调用。v1 可以不加锁。`max_concurrency > 1` 时每个 worker 自己的 toolkit、端口、workspace、Harness 进程，不要多线程共用一个 stdio runtime。

**批准策略。** 无 UI 评测必须 `danger-full-access`（`cordis.eval.yml` 钉死 sandbox-policy；`run.py` 再设 `DSH_PERMISSION_MODE`）。`ask` 会把评测卡死。

**政策注入。** 不要用进程级 `DSH_SYSTEM_PROMPT` 承载 domain policy。每个 task 的 workspace 写 `AGENTS.md`，由 `dsh-agent-instructions` 在第一轮请求注入。

**测当前检出，不要测旧 wheel。** Python SDK 默认拉捆绑 exe。源码 launch 见 [python/development.md](../../python/development.md)：`launch_args_override=("./node_modules/.bin/tsx", "packages/examples/jsonrpc-demo/src/bin.ts")`，`runtime_cwd` 必须是本仓库根；`cwd` / `DSH_CWD` 才是 bash/fs 工作区。

**官方可比性。** 完整工具集 + 自有用户 LLM 的分数不能直接交 leaderboard。与论文数字对比时固定 `--user-llm`，并另跑「只留领域工具」的消融组。

## 4. 分阶段实施

每阶段都有完成标准。未通过不要进入下一阶段。

### 阶段 0：环境与 SDK 多轮

确认三件事：当前检出的 dsh 能被 Python SDK 拉起；同一 `session_id` 能连续两轮；τ 的 mock 域能独立跑通。

τ 侧：

```bash
cd "$TAU2_ROOT"
uv sync
uv run tau2 run --domain mock --agent-llm <模型> --user-llm <模型> --num-tasks 1
```

用户模拟器也要 LLM。这一步失败先别接 dsh。

dsh 侧脚本是 [probe.py](probe.py)，放在 `eval/tau2/`，**从本仓库根目录运行**（不要 `cd eval/tau2`）。它用源码 launch 拉起 `packages/examples/jsonrpc-demo/src/bin.ts`，组合是 `examples/jsonrpc-agent/minimal.cordis.yml`，同一 `session_id=probe-1` 连跑两轮。

```bash
cd "$DSH_ROOT"
pnpm install   # 若尚未装过，提供 node_modules 里的 tsx
export UV_PROJECT_ENVIRONMENT="$PWD/tmp/py-sdk-venv"
uv sync --project python/sdk
set -a && source .env && set +a   # 或自行 export DEEPSEEK_API_KEY
uv run --project python/sdk python eval/tau2/probe.py
```

`runtime_cwd` 是仓库根（Node 解析插件）；`cwd` 是 `eval/tau2/.work/stage0/workspace`（bash/fs 工作区）。会话写在 `eval/tau2/.work/stage0/sessions`。脚本不读 `.env`，密钥必须已经在进程环境里。

完成标准：τ mock 能出 `reward`；`probe.py` 第二轮能引用第一轮的 `ready`；`eval/tau2/.work/stage0/sessions` 下有 JSONL。

### 阶段 1：最小适配器（先不接领域工具）

实现已落在 [dsh_agent.py](dsh_agent.py) 与 [run.py](run.py)。对照 τ 的 `examples/agents/minimal_text_agent.py`：factory 签名是 `create_dsh_agent(tools, domain_policy, **kwargs)`，由 `src/tau2/runner/build.py` 的 `build_agent` 调用；`kwargs["task"]` 来自当前 `Task`。

同一解释器必须同时 `import tau2` 和 `import deepseek_harness`。τ 要求 Python `>=3.12,<3.14`。阶段 0 的 `uv run --project python/sdk` 用的是 `tmp/py-sdk-venv`；`uv pip install -e` 在已 `source .venv/bin/activate` 时会装进 **`.venv`**，两套环境各有一半包。把 τ 装进阶段 0 那个解释器：

```bash
cd "$DSH_ROOT"
deactivate   # 若提示符带 (deepseek-harness)，先退出 .venv
uv pip install --python tmp/py-sdk-venv/bin/python -e "$TAU2_ROOT"
export DEEPSEEK_API_KEY=...          # 与阶段 0 相同
export TAU2_USER_LLM=deepseek/deepseek-chat   # 用户模拟器走 LiteLLM；若你有 OpenAI 可改 gpt-4.1-2025-04-14
uv run --project python/sdk python eval/tau2/run.py
```

`run.py` 默认 `--tau2-root` 为 `$TAU2_ROOT`（未设置时 `$HOME/Desktop/projects/tau2-bench`）、`--domain mock`、`--task-id create_task_1`（定义在 τ 的 `data/tau2/domains/mock/tasks.json`）。它会设置 `TAU2_DATA_DIR`、注册 `dsh_agent`、用与 [probe.py](probe.py) 相同的源码 launch 起一个 `DeepSeekHarness`，再调 `run_single_task`。

行为要点（已写在 `dsh_agent.py`）：

- `get_init_state` 只分配 `session_id={task.id}-{8 hex}`，不调用 `harness.run`。τ Orchestrator 会自己注入开场 `Hi! How can I help you today?`（`src/tau2/orchestrator/orchestrator.py` 的 `DEFAULT_FIRST_AGENT_MESSAGE`）。
- 第一轮用户话把 `domain_policy` 和「不要再打招呼」拼进 prompt；`minimal.cordis.yml` 关了 workspace 指令，不能靠 `AGENTS.md`。
- `generate_next_message` 只接受 `UserMessage`，返回 `AssistantMessage.text(...)`，不带 `tool_calls`。若收到 `ToolMessage` 直接失败。
- `TextRunConfig.llm_agent` 不会驱动 dsh（dsh 用 `--model` / `DSH_MODEL`）。`llm_user` 才是用户模拟器的 LiteLLM 模型名。

完成标准：轨迹为固定开场 → 用户 → dsh 回复 → … → `USER_STOP`；`eval/tau2/.work/stage1/sessions` 下有 JSONL；轨迹里 agent 侧没有 `tool_calls`。reward 可以为 0（领域工具尚未接到同一份 DB）。

### 阶段 2：共享 Environment 的 HTTP 桥

实现已落在 [env_bridge.py](env_bridge.py) 与 [project_trajectory.py](project_trajectory.py)，并由 [dsh_agent.py](dsh_agent.py) 在 `get_init_state` 启动桥、`stop` 拆除桥。`tools[0]._func.__self__` 就是 Orchestrator 的 toolkit。只暴露 assistant tools：`GET /health`、`GET /tools`、`POST /tools/{name}`。成功的 POST 记入 `BridgeCall`；失败的 HTTP 不记（没碰到 toolkit，与未写入一致）。

先不启动 dsh，确认 HTTP 能改 toolkit，且投影后官方 `EnvironmentEvaluator` 的 `DB` 为 1：

```bash
cd "$DSH_ROOT"
uv run --project python/sdk python eval/tau2/run.py --check-hash
```

通过后再跑 mock 的 `create_task_1`。`minimal.cordis.yml` 的 bash 说明写了「没有互联网」；第一轮 prompt 和 `TOOLS.md` 明确 localhost curl 是允许且必须的。

```bash
uv run --project python/sdk python eval/tau2/run.py
```

会话写在 `eval/tau2/.work/stage2/`。`run.py` 在评测前把桥记录插进 `simulation.messages`，所以打印出的轨迹里会出现投影后的 `tool_calls`；`generate_next_message` 仍然只回文本。`reward_breakdown` 里的 `DB` 分量 > 0 表示投影与 gold 终态一致。

完成标准：`--check-hash` 打印 hash 变化以及 `unprojected DB=0, projected DB=1`；至少 1 条会写 DB 的 task（默认 mock/`create_task_1`）其 `DB` 分量非 0；`stop()` 后下一 task 使用新端口，且 `turn_calls` 在 `stop()` 之后仍可供投影。

### 阶段 3：评测专用 cordis 与政策注入

不要用 `examples/jsonrpc-agent/minimal.cordis.yml`（只有 persistent bash 与 `str_replace_editor`）。[cordis.eval.yml](cordis.eval.yml) include `examples/jsonrpc-agent/cordis.yml`，关掉 subagent / todo，加上 `danger-full-access` 与 `workspaceContext`。stdout 只能走 JSON-RPC，不要加 console logger。

v1 打开：JSON-RPC server、DeepSeek 适配器、bash + fs + editor、`danger-full-access`、未压缩 JSONL、compaction、MCP 领域工具。v1 先关掉：skill、subagent、web、plan、Code Mode、ask-user。先拿到可复现分数，再在阶段 5 往上加。

每个 task 的 workspace：

```text
workspaces/{run_id}/{task_id}-{trial}/
  AGENTS.md       # 角色 + 整份 domain_policy + 不要再打招呼
  TOOLS.md        # 基址、调用约定、从 openai_schema 生成的工具清单
```

若 composition 挂了 `dsh-agent-instructions`（`cordis.eval.yml` 里 `workspaceContext`），它会读该 task workspace 的 `AGENTS.md`，第一轮模型请求里应出现 policy 原文。第一轮 `harness.run` 只传用户话，不再把 policy 拼进 prompt。`projectRootMarkers: [AGENTS.md]` 且 `dshHome` 为 `DSH_CWD`，避免读到仓库根 `AGENTS.md` 或 `~/.dsh/AGENTS.md`。

`AGENTS.md` 必须写明：遵守 policy；改预约/订单/账户必须经领域 API，本地文件不算成功；对用户只要说话，不要把 curl/JSON 原文念出来；信息不够就问用户；已经开过场。

每个 task 使用 `eval/tau2/.work/stage4/workspaces/{task_id}-{trial}/` 作为 `DSH_CWD`。`get_init_state` 写入该目录的 `AGENTS.md` / `TOOLS.md` / `ENV_API.txt` 后启动 Harness（阶段 4 起须先有 MCP URL）。

完成标准：`--check-hash` 含 per-task AGENTS.md 隔离；完整跑 mock/`create_task_1` 后 JSONL 含 policy 原文（`policy_in_session_jsonl=True`）；第一轮 `request/header` 含 `bash` 与 `read`/`write`/`edit`；无审批阻塞；换 task 后 workspace 目录、policy 文本、ENV_API 端口都是新的。

### 阶段 4：MCP 桥

实现已落在 [env_bridge.py](env_bridge.py) 的 Streamable HTTP `/mcp`、[cordis.eval.yml](cordis.eval.yml) 的 `dsh-mcp-client`（`serverName: tau2`），以及 [dsh_agent.py](dsh_agent.py) 在桥监听之后再 `DeepSeekHarness.start()`。决策见 [Agent Note](../../.agents/notes/implemented/testing/2026-08-27-tau2-eval-mcp-bridge.md)。

HTTP 保留作完整能力组与 `--check-hash` 备用。MCP `call_tool` 写入同一套 `BridgeCall`。评测 venv 需要 `mcp>=1.12,<2`（见 [requirements.txt](requirements.txt)）。

```bash
cd "$DSH_ROOT"
export UV_PROJECT_ENVIRONMENT="$PWD/tmp/py-sdk-venv"
uv run --project python/sdk python eval/tau2/run.py --check-hash
uv run --project python/sdk python eval/tau2/run.py
```

会话写在 `eval/tau2/.work/stage4/`。`--check-hash` 含 HTTP 哈希、仅 MCP `call_tool` 哈希、MCP 记录投影后 DB=1、AGENTS.md 隔离。

完成标准：第一轮 prompt 的工具表含 `mcp__tau2__*`；JSONL 里领域写入优先是 MCP 工具名而不是 bash curl；DB reward 与阶段 2 同量级（允许随机差）。

### 阶段 5：打开完整能力工具集

实现已落在具名组合与 `run.py --layer` / `--suite`。决策见 [Agent Note](../../.agents/notes/implemented/testing/2026-08-27-tau2-eval-full-ablation-layers.md)。`--suite` 固定 mock `create_task_1` + airline test-split `2,6,8,13,16`、seed 42。`--stop-on-collapse` 在 mock reward 不是 1.0 时停该层。

| 层 | 组合 | 打开 |
|---|---|---|
| 5a | `cordis.eval.5a.yml` | skill（agent-instructions 已在阶段 3） |
| 5b | `cordis.eval.5b.yml` | todo、进程内 spawn 子代理（同进程，能调 MCP） |
| 5c / full | `cordis.eval.full.yml` | web_search（无 fetch） |
| 消融 | `cordis.eval.ablation.yml` | 只留 MCP + 对话 |
| 5e | `cordis.eval.5e.yml` | Code Mode（`run_code`） |
| 5d / 5f | 不挂 | plan 的 `exit_plan_mode` 无审阅 UI 会卡住；客服 workspace 无 LSP server |

```bash
uv run --project python/sdk python eval/tau2/run.py --layer full --suite
uv run --project python/sdk python eval/tau2/run.py --layer ablation --suite
```

完成标准：`cordis.eval.yml` 与 `cordis.eval.full.yml` 可切换；同一 `task_ids` + seed 能复跑；`run.py` 打印分层 pass@1 与 `fail_reason`。完整组低于消融组是预期的工具表噪声，不是实现失败。

### 阶段 6：规模化

实现已落在 `run.py --split`（走 τ 的 `run_domain`）与 [classify.py](classify.py)。决策见 [Agent Note](../../.agents/notes/implemented/testing/2026-08-27-tau2-eval-scale-out.md)。每个 task 的 workspace 在 factory 里建在 `work/workspaces/{task_id}-{hex}`，所以 `run_domain` 不必在循环外 `set_workspace`。串行 `num_trials=1`、`max_concurrency=1`、`workers=0`（factory / 投影 hook 是进程内全局量）。`auto_resume=True`，结果写到 `$TAU2_DATA_DIR/simulations/<save-to>/results.json`。

```bash
export UV_PROJECT_ENVIRONMENT="$PWD/tmp/py-sdk-venv"
# 与论文数字对比时用消融组；完整组是工具表噪声对照。
uv run --project python/sdk python eval/tau2/run.py \
  --layer ablation --domain airline --split base \
  --save-to dsh-ablation-airline-base
# 同一 --save-to 可续跑。看结果：
#   cd "$TAU2_ROOT" && uv run tau2 view
# 要对齐 Sierra 核实过的 Pass^k：用户模拟器改为 gpt-4.1-2025-04-14（需 OPENAI_API_KEY），4 trial，三域 base。任意 zsh：
#   zsh eval/tau2/run-comparable.zsh
# 只有 DEEPSEEK_API_KEY 时，用户模拟器也走 DeepSeek（数字不能当榜单 Pass^k；结果写 *-t4-dsuser）：
#   TAU2_USER_LLM=deepseek/deepseek-v4-flash zsh eval/tau2/run-comparable.zsh airline
# retail / telecom 同样可加 --domain retail|telecom --split base。
# telecom 的 user_tools 不会交给 dsh（非 solo 的 build_agent 本来就不拼；factory 再按 toolkit 类名过滤）。
```

`--timeout` 默认 900 秒（同时设 SDK `request_timeout_seconds`）。dsh 内部工具步数不计 τ 的 `max_steps`（默认 200，只计用户↔agent 话轮）。失败分类见打印的 `fail_reason`：`user_sim_early_stop`、`model_refused_write`、`tool_arg_error`、`COMMUNICATE`，以及 `DB`。JSONL 扫描打印 MCP / bash / compaction 次数。

`banking_knowledge` 拒绝混跑。

完成标准：airline `base` 至少 1 trial 跑完，结果在 τ 的 `data/simulations/`；失败能区分用户模拟器提前停、模型拒改、工具参数错、COMMUNICATE 漏说。

### 阶段 7（可选）：`tau2 view` 与 MCP 共用同一审计日志

投影已经让官方打分和 `tau2 view` 能看到领域调用。MCP `call_tool` 写入同一套 `BridgeCall` 记录，不要为 MCP 再写一套评测器。不要从 dsh JSONL 解析 curl 字符串来投影：那会漏、会重、会把未打到 toolkit 的失败请求算进去。

## 5. 明确不做

- 不改 `agent-loop` 去单步暂停（SDK 没有 pause-on-tool；在 `run()` 里等工具结果会和 Orchestrator 死锁）
- 不把 dsh 当 `LLMAgent` 的模型后端
- 不用 headless profile
- 不 fork τ 的 `registry.py`
- 第一版不做 voice / full-duplex
- 第一版不提交 leaderboard

## 6. 验收总表

| 阶段 | 产出 | 未通过时停在 |
|---|---|---|
| 0 | SDK 双轮 + τ mock 官方 agent | 运行时或密钥 |
| 1 | `dsh_agent` 能聊完 mock | 会话或开场白时序 |
| 2 | HTTP 改 toolkit；投影后 mock/`create_task_1` 的 DB>0 | 接到了错误的 env，或投影未接入评测 |
| 3 | policy 出现在模型上下文；无审批卡死 | 组合或隔离 |
| 4 | MCP 原生工具；对照 curl | 工具表或进程级 URL |
| 5 | 具名组合 + `--suite` 分层 pass@1 | 工具噪声 |
| 6 | 全量 airline `base` → `$TAU2_DATA_DIR/simulations/` | 超时、并发或费用 |
| 7 | （可选）`tau2 view` 与投影共用审计日志 | — |

建议下一步：airline `base` 1 trial 跑完后，用同一 CLI 加 `--domain retail` / `--domain telecom`。阶段 7 的 `tau2 view` 读的就是 `data/simulations/` 里这份结果。

## 7. 相关路径

| 主题 | 路径 |
|---|---|
| 本仓库 Python SDK | [python/sdk/README.md](../../python/sdk/README.md)、[docs/user/guide/python-sdk.md](../../docs/user/guide/python-sdk.md) |
| 源码 launch | [python/development.md](../../python/development.md) |
| JSON-RPC 组合 | [examples/jsonrpc-agent/README.md](../../examples/jsonrpc-agent/README.md) |
| dsh-base 工具行 | [packages/bundle/base/cordis.patch.yml](../../packages/bundle/base/cordis.patch.yml) |
| MCP 客户端 | [packages/mcp/mcp-client/README.md](../../packages/mcp/mcp-client/README.md) |
| 现有 benchmark 入口 | [BENCHMARK.md](../../BENCHMARK.md) |
| τ Agent 开发指南 | `TAU2_ROOT/src/tau2/agent/README.md` |
| τ 最小 agent 示例 | `TAU2_ROOT/examples/agents/minimal_text_agent.py` |
| τ 打分 | `TAU2_ROOT/docs/evaluation.md` |
| 本仓库投影决策 | [Agent Note](../../.agents/notes/implemented/testing/2026-08-27-tau2-trajectory-domain-call-projection.md) |
| 评测 cordis 与 AGENTS.md | [Agent Note](../../.agents/notes/implemented/testing/2026-08-27-tau2-eval-cordis-workspace-policy.md) |
| 评测 MCP 桥 | [Agent Note](../../.agents/notes/implemented/testing/2026-08-27-tau2-eval-mcp-bridge.md) |
| 完整组 vs 消融组 | [Agent Note](../../.agents/notes/implemented/testing/2026-08-27-tau2-eval-full-ablation-layers.md) |
| 规模化与官方 split | [Agent Note](../../.agents/notes/implemented/testing/2026-08-27-tau2-eval-scale-out.md) |
| τ Environment HTTP | `TAU2_ROOT/src/tau2/environment/server.py` |
| τ 组装 agent/env | `TAU2_ROOT/src/tau2/runner/build.py` |
