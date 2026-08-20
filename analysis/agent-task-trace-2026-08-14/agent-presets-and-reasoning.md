# Agent 预设模式与推理等级实现追踪

分析日期：2026-08-15。分析版本：`master@47f943859bef60e4160492346772ded9b24f765a`。

本文解释 Web UI 中四种 Agent 预设（标准、PTC、极简、创造）与 DeepSeek 官方适配器提供的三个推理等级（Off、High、Max）。结论来自配置、运行时代码和测试的交叉核对，不把 UI 文案当作唯一依据。四预设 roster 是 Web/CLI profile 的交付事实；内置 headless bundle 不挂载该 roster，其模型能力留在宿主组装中。

给同事和领导的展示稿：[agent-modes-and-reasoning.md](agent-modes-and-reasoning.md)。讲解备忘：[agent-modes-and-reasoning-brief.md](agent-modes-and-reasoning-brief.md)。

## 1. 结论摘要

预设模式与推理等级是两条相互独立的选择轴：

| 选择轴 | 决定内容 | 主要所有者 | 持久化位置 |
|---|---|---|---|
| Agent 预设 | 该会话可见的工具、提示词段、Skills、压缩和委派能力，以及工具呈现方式 | `dsh-agent-presets` + 各 preset 的 `agent.cordis.yml` | 创建时写 `SessionHeader.agentPreset`；空白会话切换写 `agent-preset/selected` |
| 推理等级 | 精确 provider/model 路由使用的 adapter-owned `reasoningEffort`，以及最终 DeepSeek 请求中的 `thinking` / `reasoning_effort` | `dsh-llm` + `dsh-llm-deepseek` | 每次有效请求写 `request/header.header.config.reasoningEffort` |

因此，`标准 + Max`、`PTC + Off`、`极简 + High` 都是合法组合。预设不选择模型或推理等级；推理等级也不增删工具或提示词。

四个内置预设的核心差异：

| UI 名称 | 稳定 id | 工具呈现 | 主要用途 |
|---|---|---|---|
| 标准模式 | `standard` | Native：模型直接看到并调用每个工具 | 完整通用编码 Agent |
| PTC 模式 | `code` | Code：模型只直接调用 `run_code`，在 TypeScript 程序中组合底层工具 | 减少需要多轮模型往返的工具编排 |
| 极简模式 | `minimal` | Native，但只组装持久 Bash 与 `str_replace_editor` | 固定短提示词、双工具编码环境 |
| 创造模式 | `cordis` | Native，同标准模式 | 标准能力 + 检查和修改 Harness 运行时、创作自定义 preset |

三个 DeepSeek 推理等级的仓库内可证实差异：

| 等级 | Harness 选择值 | DeepSeek 请求字段 | 仓库能证明的语义 |
|---|---|---|---|
| Off | `off` | `thinking: { type: "disabled" }`；不发送 `reasoning_effort` | 显式关闭 thinking |
| High | `high` | `thinking: { type: "enabled" }`；`reasoning_effort: "high"` | 开启 thinking，并请求官方 High 档 |
| Max | `max` | `thinking: { type: "enabled" }`；`reasoning_effort: "max"` | 开启 thinking，并请求官方 Max 档 |

仓库没有为 High 与 Max 设置两个本地 token 上限，也没有为三档更换 system prompt 或工具表。它只验证并记录选择，然后把官方字段发给 DeepSeek。官方文档把 High 定为日常 Agent 默认档、Max 定为更复杂任务档，但没有公布两档的思考 token 上限、时延倍数或单独价目；费用按同一套输出 token 单价结算。

## 2. 证据层级与验证方法

本报告按以下优先级判断事实：

1. 实际运行代码和 `agent.cordis.yml`。
2. 对应单元、组合和请求重建测试。
3. package README 与已实施 Agent Note。
4. `preset.yml` 和 Web locale 文案，仅用于名称和面向用户的说明。

重点验证命令：

```sh
pnpm exec vitest run \
  packages/preset/agent-presets/tests/session.spec.ts \
  packages/host/apiproxy/tests/api-proxy-agent-preset.spec.ts \
  packages/core/agent-tool-presentation/tests/agent-tool-presentation.spec.ts \
  packages/core/tools/tests/code-mode.spec.ts \
  packages/llm/llm/tests/service.spec.ts \
  packages/llm/llm-deepseek/tests/serialize.spec.ts \
  packages/core/agent-loop/tests/request-reconstruction.spec.ts
```

结果：7 个测试文件、271 项测试全部通过。

## 3. 两条选择轴为什么互不包含

### 3.1 预设属于 Agent 组装

预设是一个目录，其中：

- `preset.yml` 提供名称、说明和排序；
- `agent.cordis.yml` 声明该 Agent 向宿主注册表贡献的插件行。

定义和发现逻辑见：

- [`packages/preset/agent-presets/src/preset.ts`](../../packages/preset/agent-presets/src/preset.ts)，L3-L61：preset id、信任级别、目录和配置字段；
- [`packages/preset/agent-presets/src/discovery.ts`](../../packages/preset/agent-presets/src/discovery.ts)，L124-L184：扫描目录、读取 `agent.cordis.yml`、按 `order` 排序、先出现的 root 覆盖同 id；
- [`packages/preset/agent-presets/src/index.ts`](../../packages/preset/agent-presets/src/index.ts)，L241-L287、L490-L533：standing mount 和 Agent scope 绑定。

预设只承载 Agent 平面的贡献。工具、system prompt、session、LLM 等注册表，以及持久化、沙箱、审批和模型路由，仍由宿主平面持有。这个划分见 [`docs/architecture.md`](../../docs/architecture.md) 的 Profiles、Core packages、Capability seams，以及 [`packages/preset/agent-presets/README.zh.md`](../../packages/preset/agent-presets/README.zh.md)。

### 3.2 推理等级属于每个 Agent 的模型选择

模型选择类型是 `{ provider, model, reasoningEffort? }`，见 [`packages/core/agent/src/model-selection.ts`](../../packages/core/agent/src/model-selection.ts)，L9-L25。`reasoningEffort` 是 adapter-owned id，不是 Agent preset id，也不是 Harness 核心硬编码的通用枚举。

同一文件 L27-L74 把选择同时接到：

- `system-prompt/assemble`：快照 provider/model，用于 `{{model}}` 等变量；
- `agent/request`：覆盖本步请求的 provider/model/reasoning effort。

这两个 listener 在一次 prompt assembly 时快照同一份选择，避免并发切换导致提示词中的模型名与实际请求路由不一致。

## 4. 内置预设如何被发现和激活

### 4.1 交付目录和默认值

CLI 把 [`apps/cli/config/agent-presets/`](../../apps/cli/config/agent-presets) 作为内置 root。入口在 [`apps/cli/src/profile-boot.ts`](../../apps/cli/src/profile-boot.ts)：

- L34-L35：计算内置 preset 根目录；
- L155-L166：把该 root 以 `trust: system` 注入 `agent-presets` 行。

Web bundle 在 [`packages/bundle/web-app/cordis.patch.yml`](../../packages/bundle/web-app/cordis.patch.yml)，L410-L424 挂载 `@deepseek-ai/dsh-agent-presets`，并把默认值设为 `standard`。

用户 preset 根目录由 roster 追加为 `$DSH_HOME/.agent-presets`，信任级别是 `user`。`PresetTrust` 的注释明确指出用户 preset 与 shell access 同级信任，见 `preset.ts` L3-L8。

### 4.2 standing mount，而不是每会话重复挂载

当前实现按 preset id 保存 standing mount：

```text
AgentPresets.mount(agentCtx, presetId)
  resolve preset
  ensureStanding(preset)       # 同一 id/同一文件版本只挂一棵插件树
  bindScopeParent(agentKey, standing.key)
```

证据：

- [`packages/preset/agent-presets/src/index.ts`](../../packages/preset/agent-presets/src/index.ts)，L241-L252：`standing` 以 preset id 缓存并 single-flight；
- 同文件 L275-L287：Agent 的 scope key 绑定到 standing scope；
- 同文件 L490-L533：首次挂载、文件 stamp 检查和新 generation 建立。

同一 preset 的会话因此共享插件组装实例，但会话状态仍由插件按 Agent/Session key 隔离。修改 preset 文件后，新会话可进入新 generation；已经加入旧 generation 的会话不被迁移。

### 4.3 创建、切换、恢复

新建会话链路：

```text
session.create { agentPreset? }
  ApiProxy.composeAgent()
    agentPresets.resolve(requested ?? default)
    Agent setup:
      installModelSelection(agentCtx)
      agentPresets.mount(agentCtx, resolvedId)
  SessionHeader.agentPreset = resolvedId
```

源码：

- [`packages/host/apiproxy/src/api-proxy.ts`](../../packages/host/apiproxy/src/api-proxy.ts)，L1211-L1247：先解析 id，再在 Agent setup 中挂载；
- 同文件 L2167-L2239：`session.create` 接受 `agentPreset` 并返回实际组装值。

空白会话切换链路：

```text
agentPreset.select
  串行化同 session 的切换
  sessionBlank() 检查（日志中没有 turn/start）
  agentPresets.recompose()
    ensureStanding(newPreset)
    scope parent rebind
  append agent-preset/selected
```

源码：

- `api-proxy.ts` L3083-L3132：只允许空白会话，成功后才写日志；
- [`packages/preset/agent-presets/src/index.ts`](../../packages/preset/agent-presets/src/index.ts)，L437-L471：`recompose()` 通过 scope parent rebind 切换；
- [`packages/preset/agent-presets/src/session.ts`](../../packages/preset/agent-presets/src/session.ts)，L18-L53：`agent-preset/selected` 事件以及“最后一次选择胜出”的重建规则。

会话开始后拒绝切换，因为已有历史可能包含新 preset 不再提供的工具调用。这里的“开始”精确定义为日志中出现 `turn/start`；单独执行 `/plan`、`/goal` 等只写插件事件的命令仍保持 blank，见 `api-proxy.ts` L500-L509。切换测试见 [`packages/host/apiproxy/tests/api-proxy-agent-preset.spec.ts`](../../packages/host/apiproxy/tests/api-proxy-agent-preset.spec.ts)，L344-L468。

## 5. 标准模式（`standard`）

### 5.1 元数据和人设

名称和用途来自 [`apps/cli/config/agent-presets/standard/preset.yml`](../../apps/cli/config/agent-presets/standard/preset.yml)：

- 名称：标准模式；
- 说明：完整编码 Agent；
- 排序：1。

组装入口是 [`apps/cli/config/agent-presets/standard/agent.cordis.yml`](../../apps/cli/config/agent-presets/standard/agent.cordis.yml)。

L24-L33 挂载：

- `@deepseek-ai/dsh-persona`：`You are a coding agent powered by the {{model}} model. Your working directory is {{cwd}}.`；
- `@deepseek-ai/dsh-agent-instructions`：最多读取 65536 字节的工作区指令。

`persona` 通过同名 section 在当前 scope 遮蔽宿主人设，机制见 [`packages/preset/persona/src/index.ts`](../../packages/preset/persona/src/index.ts)，L1-L12、L54-L68。

### 5.2 工具和能力插件

以下是标准模式 `agent.cordis.yml` 的有效行：

| 配置行 | 插件 | 模型侧能力 |
|---|---|---|
| `tool-bash` / `tool-pwsh` | `dsh-tool-bash` / `dsh-tool-pwsh` | 按平台二选一的 shell |
| `tool-fs` | `dsh-tool-fs` | read、write、edit、read_image |
| `tool-fs-search` | `dsh-tool-fs-search` | glob、grep |
| `tool-jobs` | `dsh-tool-jobs` | 后台任务查询、输出和停止 |
| `skill-filesystem` + `tool-skill` | `dsh-skill-filesystem` + `dsh-tool-skill` | Skill 发现、目录注入和正文加载 |
| `tool-goal` | `dsh-tool-goal` | create/get/update goal |
| `plan-mode` | `dsh-plan-mode` | plan 状态及 `exit_plan_mode` |
| `compaction-basic` | `dsh-compaction-basic` | 上下文压缩 |
| `command-compact` | `dsh-command-compact` | 人类 `/compact` 命令 |
| `tool-result-pruner` | `dsh-compaction-tool-result-pruner` | 大工具结果裁剪 |
| `tool-subagent-control` | `dsh-tool-subagent-control` | interrupt/send/list 等子 Agent 控制 |
| `tool-subagent` / `tool-subagent-fork` | `dsh-tool-subagent` | spawn / fork 委派 |
| `workflow-worker-thread` + `tool-workflow` | workflow 包 | worker-thread workflow |
| `tool-ralph` | `dsh-tool-ralph` | 多轮 fresh-agent Ralph |
| `tool-ask-user` | `dsh-tool-ask-user` | 结构化向用户提问 |
| `tool-todo` | `dsh-tool-todo` | todo_write |
| `tool-web` | `dsh-tool-web` | web_search；配置中 `fetch: false` |

精确行号见标准组装文件 L44-L250。Codex 和 Claude Code 两个外部 subagent 工具行存在，但 L203-L219 显式 `disabled: true`；默认可用的是 `spawn` 与 `fork`。

### 5.3 Native 工具呈现

标准组装没有 `tool-presentation` 行，因此继承宿主 `ToolRuntime` 的默认 `native`。默认值定义在 [`packages/core/tools/src/index.ts`](../../packages/core/tools/src/index.ts)，L650-L673、L790-L831。

Native 模式中，当前 scope 可见的每个工具都以独立 JSON Schema 进入模型请求；模型直接输出对应工具名。工具仍统一经过 `tools/pre-execute → execute → tools/post-execute`，预设不会绕过宿主沙箱和审批。

## 6. PTC 模式（`code`）

### 6.1 与标准模式的静态差异

名称来自 [`apps/cli/config/agent-presets/code/preset.yml`](../../apps/cli/config/agent-presets/code/preset.yml)，排序为 2。英文 UI 把它称为 Code mode，中文 UI 称为 PTC 模式，映射见 [`packages/client/ui-agent-preset/src/client/locales.ts`](../../packages/client/ui-agent-preset/src/client/locales.ts)，L35-L49、L100-L108、L169-L174。

[`apps/cli/config/agent-presets/code/agent.cordis.yml`](../../apps/cli/config/agent-presets/code/agent.cordis.yml) 与标准组装的有效插件行相同，末尾额外增加：

```yaml
- id: tool-presentation
  name: '@deepseek-ai/dsh-agent-tool-presentation'
  config:
    mode: code
```

对应 L254-L262。逐行 diff 只显示注释变化和这一个有效配置差异。

### 6.2 呈现切换

`@deepseek-ai/dsh-agent-tool-presentation` 的实现在 [`packages/core/agent-tool-presentation/src/index.ts`](../../packages/core/agent-tool-presentation/src/index.ts)：

- L38-L52：允许 `native | code | both`，且 `mode` 必填；
- L59-L71：Native 直接 `presentAs('native')`；Code/Both 等待宿主 `codeRuntime` 后按当前 scope 注册呈现方式；
- Code runtime 缺失时该行保持 pending，preset mount 会把它作为不可用组装拒绝，而不是等第一次请求才失败。

`ToolRuntime.presentAs()` 在 [`packages/core/tools/src/index.ts`](../../packages/core/tools/src/index.ts) L935-L973 把呈现方式写入 scope layer。同一 scope 只能声明一次；nearest scope 胜出，因此一个 PTC 会话可以与 Native 会话共存。

### 6.3 Prompt 和 schema 如何变化

Code 模式并未删除底层工具定义，而是投影出另一种模型接口：

1. `wireSchemas()` 先解析当前 scope 的工具视图；
2. Code 模式只保留 `run_code` schema；
3. `tools:sdk` section 从同一份可见工具生成 TypeScript SDK；
4. `tools:code-only` section 明确只有 `run_code` 可以由模型直接调用。

源码见 `packages/core/tools/src/index.ts`：

- L839-L890：collapse 指令与 SDK prompt section；
- L976-L1000：Native 返回全部 schema，Code 只返回 `run_code`，Both 两者都返回；
- [`packages/core/tools/src/ts-types.ts`](../../packages/core/tools/src/ts-types.ts)，L250 起：面向模型的 `run_code` TypeScript 使用说明。

因此 PTC 的直接模型接口通常从多个 tool schema 收敛为：

```text
system: 标准提示词 + code-only 规则 + 生成的 TypeScript SDK
tools:  [run_code]
```

底层工具仍出现在 SDK 的 `tools.<name>()` 方法中，不是从运行时移除。

### 6.4 执行层也强制收敛

PTC 不是只靠 prompt 要求模型“请勿直接调用工具”。执行入口也执行同一规则：

- `ToolRuntime.resolveExecution()`：模型直接调用时，Code 模式只接受 `run_code`；
- `collapses()`：`!nested && mode === 'code' && name !== 'run_code'` 时拒绝；
- 被拒绝的工具按 `UNKNOWN_TOOL` 物化；
- `run_code` 内部产生的 sub-dispatch 带 `parent` token，因此可调用当前 Agent scope 中的底层工具。

证据见 [`packages/core/tools/src/index.ts`](../../packages/core/tools/src/index.ts)，L1208-L1225、L1308-L1326。

### 6.5 `run_code` 的执行链

[`packages/core/tools/src/code-mode.ts`](../../packages/core/tools/src/code-mode.ts)，L283-L654 创建 `run_code`：

```text
模型调用 run_code { code, description }
  获取宿主 codeRuntime
  为当前 Agent 可见工具建立 tools.<name> binding
  worker-thread 执行 TypeScript 函数体
  每次 tools.<name>(args)
    生成 <parent>:code:<n> 子调用 id
    进入同一 ToolRuntime scheduler
    经过 pre-execute / guard / tool body / post-execute
    写 tool/code-dispatch-start
    写 tool/code-dispatch
  汇总 console logs + return value
  返回 run_code tool/result
```

子调用继续使用 Native 调度语义：只有标记 concurrency-safe 的连续调用并行，exclusive 工具形成屏障；默认最多 10 个并行 sub-call。相关实现见 `code-mode.ts` L342-L457、L464-L628。

宿主 TypeScript 执行后端是 [`packages/code-runtime/code-runtime-worker-thread/src/index.ts`](../../packages/code-runtime/code-runtime-worker-thread/src/index.ts)。L1-L6 明确说明 worker thread 是资源约束，不是安全边界；模型代码仍视为 bash 等级信任。它提供 busy-time、wall-time、输出字节和 heap 上限。

### 6.6 PTC 的准确收益与边界

仓库能证明：

- 多个工具操作可以写进一次 `run_code`，减少“模型响应 → 一个工具 → 再请求模型”的往返；
- SDK、schema 和实际可执行工具来自同一 scope view；
- 底层调用仍经过权限、审批、guard 和 durable sub-dispatch 日志；
- `run_code` 本身不会暴露给程序，避免递归调用。

仓库不能保证每个任务一定更快或 token 更少。PTC 增加了一段生成 SDK 的 system prompt；如果任务只需一次工具调用，Code Mode 未必更省。

## 7. 极简模式（`minimal`）

### 7.1 它不是“标准模式少几个工具”

名称来自 [`apps/cli/config/agent-presets/minimal/preset.yml`](../../apps/cli/config/agent-presets/minimal/preset.yml)，排序为 3。完整组装只有 [`apps/cli/config/agent-presets/minimal/agent.cordis.yml`](../../apps/cli/config/agent-presets/minimal/agent.cordis.yml) 中的三组行：

1. 固定 persona；
2. persistent shell realm；
3. local filesystem realm。

它没有标准模式的 Agent instructions、文件搜索、Skills、Goal、Plan、Compaction、Subagent、Workflow、Todo 或 Web tool。

### 7.2 固定完整提示词

L8-L14：

```yaml
text: You are a helpful software engineer assistant.
complete: true
includeRuntimeContext: false
```

`complete: true` 使该 persona 成为唯一 system prompt section；`includeRuntimeContext: false` 调用 scope 级 `suppressRuntimeContext()`。实现见 [`packages/preset/persona/src/index.ts`](../../packages/preset/persona/src/index.ts)，L33-L51、L60-L68，以及 [`packages/core/system-prompt/src/index.ts`](../../packages/core/system-prompt/src/index.ts)，L409-L420、L457-L464。

这意味着宿主 identity、Web orientation、普通工具指导和动态沙箱/审批快照不会进入该 Agent 的 system prompt。服务和权限机制本身没有因此被关闭；这里只改变模型可见提示词。

### 7.3 两个模型工具

Persistent shell 组（L18-L44）：

- `@deepseek-ai/dsh-terminal`：Agent-owned terminal registry；
- `@deepseek-ai/dsh-terminal-bash`：Bash terminal backend；
- `@deepseek-ai/dsh-tool-bash-persistent`：模型工具，`timeoutMs: 300000`。

它与标准的一次性 `tool-bash` 不同：PTY 状态跨调用保留。工具 description 声称无互联网、有常见 apt/pip mirror；这是模型可见说明，实际网络限制仍应由宿主 sandbox policy 验证，不能只靠 description。

Filesystem 组（L48-L62）：

- `@deepseek-ai/dsh-fs-local`，cwd 为 `DSH_CWD ?? process.cwd()`；
- `@deepseek-ai/dsh-tool-str-replace-editor`，要求绝对路径，最大输出 16000 字符。

该 scope-local `fs` service 遮蔽宿主 sandboxed fs provider，所以编辑器通过 local fs 工作；shell backend 仍消费宿主 sandbox policy 和 subprocess 实现。组内使用 entry-local `isolate`，避免向进程 root 发布同名 service。

### 7.4 极简模式没有上下文压缩

组装中没有 `compaction-basic` 或 tool result pruner。长会话不会因为选择极简模式自动获得标准模式的压缩策略。这是能力差异，不只是 UI 工具数量差异。

## 8. 创造模式（`cordis`）

### 8.1 标准能力 + 自修改能力

名称来自 [`apps/cli/config/agent-presets/cordis/preset.yml`](../../apps/cli/config/agent-presets/cordis/preset.yml)，排序为 4。

[`apps/cli/config/agent-presets/cordis/agent.cordis.yml`](../../apps/cli/config/agent-presets/cordis/agent.cordis.yml) 保留标准模式的 shell、fs、search、jobs、skills、goal、plan、compaction、delegation、workflow、ask-user、todo 和 web 能力，并增加：

- `@deepseek-ai/dsh-tool-cordis`（L245-L246）；
- 指向 preset 自带 `skills/` 的 `customSkillDirs`（L255-L262）；
- 更长的 Harness/Cordis persona（L17-L29）。

标准模式原有的 `skill-filesystem` / `tool-skill` 被移动到文件末尾并增加自带目录配置，不是删除 Skill 能力。

### 8.2 专用 persona

创造模式 persona 告诉模型：

- 当前运行在 DeepSeek Harness 上；
- HOST plane 与 AGENT PRESET plane 的所有权区别；
- 自定义 preset 位于 `${DSH_HOME:-$HOME/.dsh}/.agent-presets/<id>/`；
- 不得直接修改交付的内置 preset；
- 编辑 composition 前加载 `editing-cordis-compositions` Skill。

自带 Skills：

- [`apps/cli/config/agent-presets/cordis/skills/editing-cordis-compositions/SKILL.md`](../../apps/cli/config/agent-presets/cordis/skills/editing-cordis-compositions/SKILL.md)；
- [`apps/cli/config/agent-presets/cordis/skills/cordis-plugin-development/SKILL.md`](../../apps/cli/config/agent-presets/cordis/skills/cordis-plugin-development/SKILL.md)。

### 8.3 信任边界

`dsh-tool-cordis` 的源码位于 [`packages/extensions/tool-cordis/`](../../packages/extensions/tool-cordis)。当前源码实际注册七个工具：`cordis_inspect_list`、`cordis_inspect_query`、`cordis_inspect_self`、`cordis_define`、`cordis_run`、`cordis_stop` 和 `cordis_undefine`，见 [`packages/extensions/tool-cordis/src/index.ts`](../../packages/extensions/tool-cordis/src/index.ts)，L42、L61、L97、L149、L241、L330、L352。`cordis_run` 会在 vm runner 中求值模型提交的 plain JavaScript host half；动态包只存于当前进程内存，不会自动写入 preset 或 `cordis.yml`。创建持久 preset 仍依赖创造模式的普通文件工具与专用 Skills。

该 vm 隔离全局变量但不是安全边界；README 明确要求按 bash access 对待这套工具。创造模式 persona 还允许通过文件工具写入用户 preset，生成的 composition 可被后续会话挂载。

所以创造模式适合受信任操作者开发 preset，不应仅凭“它也是内置模式”就向不受信任用户开放。

## 9. 四个预设的结构化对比

| 维度 | 标准 | PTC | 极简 | 创造 |
|---|---|---|---|---|
| `persona.complete` | false | false | true | false |
| Runtime context prompt | 有 | 有 | 抑制 | 有 |
| Agent instructions | 有 | 有 | 无 | 有 |
| Shell | 一次性 Bash/Pwsh | 同标准，通过 SDK 调用 | 持久 Bash PTY | 同标准 |
| 文件工具 | read/write/edit/read_image | 同标准，通过 SDK 调用 | `str_replace_editor` | 同标准 |
| glob/grep | 有 | 有 | 无 | 有 |
| web_search | 有 | 有 | 无 | 有 |
| Skills | 用户/系统 Skills | 同标准 | 无 | 同标准 + 两个 preset 创作 Skills |
| Plan/Goal/Todo | 有 | 有 | 无 | 有 |
| Compaction/pruner | 有 | 有 | 无 | 有 |
| Subagent/workflow/Ralph | 有 | 有 | 无 | 有 |
| Tool schema | 每个原生工具 | 只有 `run_code` | 两个原生工具 | 每个原生工具 |
| 底层工具是否存在 | 是 | 是，由 SDK 间接到达 | 只组装两个 | 是 |
| 修改运行时 | 普通 shell/fs 能力 | 普通 shell/fs 能力 | 无专用能力 | 专用 `tool-cordis` 动态包 + 普通文件工具写 preset |

## 10. 推理等级的定义和公布

### 10.1 Harness 核心不定义固定三档

[`packages/llm/llm/src/types.ts`](../../packages/llm/llm/src/types.ts)，L252-L270 定义 adapter-owned reasoning metadata：

- `efforts[]`：有序 id/name/description；
- `defaultEffort?`：该精确 provider/model 的默认值。

[`packages/llm/llm/src/brand.ts`](../../packages/llm/llm/src/brand.ts)，L54-L63 把 id 品牌化为 `ReasoningEffortId`。核心层不把 `off/high/max` 写成跨 provider 枚举，因此其他 adapter 可以公布不同名称和数量的档位。

### 10.2 DeepSeek 官方适配器公布 Off/High/Max

[`packages/llm/llm-deepseek/src/adapter.ts`](../../packages/llm/llm-deepseek/src/adapter.ts)：

- L95-L105：定义 `off`、`high`、`max` 和 UI 名称；
- L175-L211：`resolveModel()` 为每个模型返回 reasoning metadata；
- 如果 deployment 配置 `thinking: disabled`，只公布 Off，默认 Off；
- 否则公布三档，配置默认 Off/Max 时使用对应值，其他情况默认 High。

配置声明和验证在 [`packages/llm/llm-deepseek/src/index.ts`](../../packages/llm/llm-deepseek/src/index.ts)：

- L54-L76：省略 `reasoningEffort` 时默认 High；
- L91-L100：配置只允许 `off | high | max`；
- L161-L166：`thinking: disabled` 与 High/Max 同时配置会在加载时失败；
- L183-L197：解析成每次请求读取的 connection defaults。

### 10.3 Host 校验 metadata 和选择

[`packages/llm/llm/src/index.ts`](../../packages/llm/llm/src/index.ts)：

- L675-L717：拒绝空 id、空名称、重复 id、未知默认值；
- L720-L768：`resolveCallConfig()` 对精确模型解析默认值；显式不支持的 effort 在网络 I/O 前报 `UNSUPPORTED_REASONING_EFFORT`；
- 不做 clamping 或 aliasing。

这保证 UI 看到的档位、Host 接受的档位和 adapter 最终发送的档位使用同一份 metadata。

## 11. 推理等级从 UI 到 DeepSeek API 的完整链路

### 11.1 模型目录

```text
DeepSeekAdapter.resolveModel()
  -> LlmRuntime.resolveModelInfo()
  -> ApiProxy.buildModelCatalog()
  -> session.models RPC
  -> ModelDirectory
  -> ModelSelect
```

[`packages/host/apiproxy/src/api-proxy.ts`](../../packages/host/apiproxy/src/api-proxy.ts)，L320-L374 把 adapter metadata 投影为 Web 的 provider/model/reasoning 目录。某一个 provider 目录加载失败不会使其他 provider 目录一起失败。

### 11.2 UI 选择

[`packages/client/ui-model-selection/src/client/ModelSelect.tsx`](../../packages/client/ui-model-selection/src/client/ModelSelect.tsx)：

- L67-L89：解析当前模型和有效 effort；
- L90-L102：档位完全来自 Host metadata，不维护客户端自有枚举；
- L190-L202：切换档位时保留 provider/model，只替换 `reasoningEffort`。

[`packages/client/ui-model-selection/src/client/directory.ts`](../../packages/client/ui-model-selection/src/client/directory.ts)，L88-L121 通过 `session.selectModel` RPC 提交完整选择并以 Host 返回的已验证值更新本地状态。

### 11.3 Host 接受并保存

[`packages/host/apiproxy/src/api-proxy.ts`](../../packages/host/apiproxy/src/api-proxy.ts)，L2282-L2330：

1. 把 wire string 品牌化为 `ReasoningEffortId`；
2. 调用 `ctx.llm.resolveCallConfig()` 验证并补齐 adapter 默认；
3. 更新该 live Agent 的 `ModelSelectionRef.current`；
4. 尝试保存为以后 Agent 的默认选择；
5. 返回 Host 实际接受的完整 selection。

默认选择由 [`packages/core/agent-default-model/src/index.ts`](../../packages/core/agent-default-model/src/index.ts) 管理。L23-L38 声明 settings 字段；L84-L103 读取和保存完整 selection。Preset 配置不含这个字段。

### 11.4 每个 step 快照并写请求头

`installModelSelection()` 在 prompt assembly 时把 `current` 快照到 `assembled`，随后 `agent/request` 使用该快照。选择在 assembly 期间发生变化，只影响后续 step。

Agent loop 的 [`packages/core/agent-loop/src/agent.ts`](../../packages/core/agent-loop/src/agent.ts)，L403-L494：

1. 读取历史 `request/header`，只在 provider/model 完全相同时考虑恢复显式 effort；
2. 执行 `agent/request` waterfall；
3. `ctx.llm.prepareCall()` 验证并填充 adapter default；
4. 把 resolved config 和 `adapterDefaults` 写入 `request/header`；
5. 使用同一 prepared adapter registration 发起 stream。

如果本次 effort 来自 adapter default，`adapterDefaults.reasoningEffort: true` 会同时记录。恢复时会重新解析标记过的默认值，而不是把旧 provider 默认永久冻结；显式选择则可以从日志恢复。

测试 [`packages/core/agent-loop/tests/request-reconstruction.spec.ts`](../../packages/core/agent-loop/tests/request-reconstruction.spec.ts)，L114-L169 验证：

- 第一轮默认 High 被记录；
- 第二轮显式改 Max 产生 `request/header` reason=`change`；
- resume 恢复有效值；
- 切换 model 时不会错误继承旧模型的 effort。

### 11.5 DeepSeek wire 序列化

[`packages/llm/llm-deepseek/src/serialize.ts`](../../packages/llm/llm-deepseek/src/serialize.ts)，L25-L52：

```text
off  -> thinking.disabled，省略 reasoning_effort
high -> thinking.enabled + reasoning_effort=high
max  -> thinking.enabled + reasoning_effort=max
```

最终字段写入 `WireRequest` 的位置在同文件 L169-L184。Wire 类型在 [`packages/llm/llm-deepseek/src/types.ts`](../../packages/llm/llm-deepseek/src/types.ts)，L12-L24；协议只允许发送 `high | max`，所以 Off 必须通过 `thinking.type: disabled` 表示。

## 12. 三个等级的准确差异

### 12.1 Off

- 模型请求显式关闭 thinking；
- 不发送非法的 `reasoning_effort: off`；
- UI 仍把它作为 adapter 公布的合法档位；
- deployment 若全局锁定 `thinking: disabled`，只允许 Off。

仓库没有承诺 Off 一定产生零个所有供应商定义下的内部推理 token；它只保证协议字段关闭 DeepSeek thinking。

### 12.2 High 与 Max：调用上真正不同的只有一个字段

二者都开启 thinking，都发到同一官方接口 `POST {baseURL}/chat/completions`，默认 `https://api.deepseek.com/chat/completions`。请求体其余部分（`model`、`messages`、`tools`、流式选项）不因档位而改写。唯一调用差异是顶层 `reasoning_effort`：

| | High | Max |
|---|---|---|
| `thinking` | `{ type: "enabled" }` | `{ type: "enabled" }` |
| `reasoning_effort` | `"high"` | `"max"` |
| 官方映射（Flash / Pro 相同） | `high` → `high` | `max` → `max` |

官方思考模式文档给出的完整映射是：`low`→`low`，`medium`→`high`，`high`→`high`，`xhigh`→`high`，`max`→`max`。Harness 只发送 `high` 和 `max`，不发送 `low` / `medium` / `xhigh`。

官方对两档的使用说明（不是本仓库的定量结论）：

- [Chat Completions](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion)：`reasoning_effort` 控制推理强度，默认 `high`。
- [更新日志 2026-08-13](https://api-docs.deepseek.com/zh-cn/updates) 与 [V4-Pro 正式版说明](https://api-docs.deepseek.com/zh-cn/news/news260813/)：简单任务用 `low`，日常 Agent 任务用 `high`，更复杂 / 高度复杂的任务用 `max`。
- [V4 预览说明](https://api-docs.deepseek.com/zh-cn/news/news260424)：复杂 Agent 场景建议思考模式并把强度设为 `max`。
- 官方 Code Agent 基准（V4-Flash / V4-Pro）用 DeepSeek Harness 极简模式 + `max` 档评测。

官方文档同时写明，下面这些对 High 和 Max **相同**：

- 响应都带 `reasoning_content`，用量里都有 `reasoning_tokens`；
- 思考模式下 `temperature` / `top_p` 等采样参数不生效；
- 带 `tools` 时必须回传 `reasoning_content`，否则 400；
- [模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing) 不按档位另开单价：上下文 1M，输出最长 384K，按百万 tokens 计费。

因此：Max 若更贵或更慢，只可能来自服务端生成了更多思考 token，不是另一条计费或另一套 Harness 路径。官方没有公布两档的思考预算数值。

Mock HTTP 测试 [`packages/llm/llm-deepseek/tests/adapter.spec.ts`](../../packages/llm/llm-deepseek/tests/adapter.spec.ts)，L59-L82 验证默认请求带 `reasoning_effort: high`。

你在四任务运行追踪中观察到的是 `standard + max`，所以每个主循环请求的 `request/header` 都带 Max；这不是标准模式自带的默认值。

### 12.4 标题请求例外

`purpose: session-title` 无条件返回 `{ thinking: 'disabled' }`，即使当前会话选择 Max，见 `serialize.ts` L36-L52。测试 [`packages/llm/llm-deepseek/tests/serialize.spec.ts`](../../packages/llm/llm-deepseek/tests/serialize.spec.ts)，L240-L250 验证标题请求不发送 `reasoning_effort`。

这个例外只作用于生成会话标题的辅助请求，不会修改会话对话请求的默认或当前选择。

## 13. 组合后的实际请求差异

以同一个 DeepSeek 模型为例：

### 标准 + Max

```text
system = 标准 persona + 各工具指导 + runtime/skill context
tools  = 当前 scope 的各原生工具 schema
wire   = thinking.enabled + reasoning_effort=max
```

模型可直接发 `bash`、`read`、`web_search` 等调用。

### PTC + High

```text
system = 标准 persona + 各工具指导 + code-only 规则 + TypeScript SDK
tools  = [run_code]
wire   = thinking.enabled + reasoning_effort=high
```

模型直接调用 `run_code`，程序内通过 `tools.bash()`、`tools.read()` 等触发底层调用。

### 极简 + Off

```text
system = "You are a helpful software engineer assistant."
tools  = [persistent bash, str_replace_editor]
wire   = thinking.disabled
```

没有 runtime snapshot、Skill catalog、compaction 或其他标准能力。

### 创造 + Max

```text
system = Harness/Cordis 创作 persona + 标准工具指导 + preset 专用 Skills
tools  = 标准原生工具 + Cordis inspect_list/inspect_query/inspect_self/define/run/stop/undefine 工具
wire   = thinking.enabled + reasoning_effort=max
```

该组合同时具有最高的自修改权限和 Max 推理请求；二者仍来自不同配置轴。

## 14. 验证结论与易混淆点

### 已确认

1. 内置 roster 的确是 `standard`、`code`、`minimal`、`cordis` 四个目录。
2. Web 默认 preset 是 `standard`。
3. PTC 的有效组装等于标准能力加 `mode: code` 呈现行。
4. PTC 的执行器会拒绝模型绕过 `run_code` 直接调用原生工具。
5. 极简模式的 system prompt 是 complete，且抑制 runtime context。
6. 创造模式保留标准能力，增加 `tool-cordis`、专用 persona 和两个 preset Skills。
7. DeepSeek 官方适配器公布 Off/High/Max，默认 High。
8. Off 使用 `thinking.disabled`，High/Max 使用官方 `reasoning_effort`。
9. 每步 resolved effort 写入 `request/header`，可重建请求。
10. 预设与推理等级互相独立。

### 对先前概述的校正

1. **Preset 当前不是每会话重新 mount。** 当前实现为每个 preset id/file generation 建 standing mount，再把各 Agent scope 绑定到它。
2. **PTC 不是只改变提示词和 schema。** 工具执行入口使用同一个 collapse predicate，直接调用原生工具会得到 `UNKNOWN_TOOL`。
3. **“Max 更慢、更贵”不是本仓库可单独证明的定量事实。** 仓库只发 `reasoning_effort: max`；实际预算和计费由 DeepSeek 服务端决定。
4. **极简模式中的“无互联网”首先是一段工具说明。** 是否真正断网取决于宿主 sandbox policy，不能把 prompt description 当成强制网络隔离。
5. **创造模式工具实现位于 `packages/extensions/tool-cordis`。** 当前检查接口已经拆成 inspect_list/inspect_query/inspect_self，另有 define/run/stop/undefine；`cordis_run` 运行进程内动态包，但不会自动把实验写成 preset。

## 15. 关键文件索引

### Preset 定义和生命周期

- [`apps/cli/config/agent-presets/`](../../apps/cli/config/agent-presets)
- [`packages/preset/agent-presets/src/discovery.ts`](../../packages/preset/agent-presets/src/discovery.ts)
- [`packages/preset/agent-presets/src/index.ts`](../../packages/preset/agent-presets/src/index.ts)
- [`packages/preset/agent-presets/src/session.ts`](../../packages/preset/agent-presets/src/session.ts)
- [`packages/host/apiproxy/src/api-proxy.ts`](../../packages/host/apiproxy/src/api-proxy.ts)
- [`packages/client/ui-agent-preset/`](../../packages/client/ui-agent-preset)

### Tool presentation / PTC

- [`packages/core/agent-tool-presentation/src/index.ts`](../../packages/core/agent-tool-presentation/src/index.ts)
- [`packages/core/tools/src/index.ts`](../../packages/core/tools/src/index.ts)
- [`packages/core/tools/src/code-mode.ts`](../../packages/core/tools/src/code-mode.ts)
- [`packages/core/tools/src/ts-types.ts`](../../packages/core/tools/src/ts-types.ts)
- [`packages/code-runtime/code-runtime-worker-thread/src/index.ts`](../../packages/code-runtime/code-runtime-worker-thread/src/index.ts)

### Model selection / reasoning

- [`packages/core/agent/src/model-selection.ts`](../../packages/core/agent/src/model-selection.ts)
- [`packages/core/agent-default-model/src/index.ts`](../../packages/core/agent-default-model/src/index.ts)
- [`packages/client/ui-model-selection/src/client/ModelSelect.tsx`](../../packages/client/ui-model-selection/src/client/ModelSelect.tsx)
- [`packages/llm/llm/src/types.ts`](../../packages/llm/llm/src/types.ts)
- [`packages/llm/llm/src/index.ts`](../../packages/llm/llm/src/index.ts)
- [`packages/llm/llm-deepseek/src/adapter.ts`](../../packages/llm/llm-deepseek/src/adapter.ts)
- [`packages/llm/llm-deepseek/src/serialize.ts`](../../packages/llm/llm-deepseek/src/serialize.ts)
- [`packages/core/agent-loop/src/agent.ts`](../../packages/core/agent-loop/src/agent.ts)
