# DeepSeek Harness 四任务运行链路追踪报告

生成时间：2026-08-14。数据来自本机 Web UI 实际跑完的 Session 日志（`~/.dsh/sessions`），并对照仓库源码还原「用户输入 → Prompt 组装 → 基座模型 API → 工具执行 → 结果写回」的完整路径。

工作区：`/Users/duyuntao.1/Desktop`（不是本仓库根目录）。Agent preset：`standard`。模型路由：`deepseek-official` / `deepseek-v4-flash`，`reasoningEffort: max`。

本报告不包含 API 密钥。日志里的密钥字段从未进入 Session 事件。

---

## 1. 四个任务总览

磁盘上共 5 条 Session。其中 4 条完成了一轮 `turn/end { kind: 'completed' }`，第 5 条只写了权限基线、没有用户消息，不计入任务。

| # | 本地时间 (UTC+8) | Session ID | 用户任务 | 步数 | 工具调用 | 墙钟 | 产出 |
|---|---|---|---|---|---|---|---|
| 1 | 11:51–12:04 | `session-fc8efec4-774b-4748-9b54-1f779b857b21` | 6 人新加坡商务行程（10/19–22） | 41 | 59 | 12.6 min | Canvas + 正文方案 |
| 2 | 13:35–13:45 | `session-4ca60d00-ef1a-4cc6-a0d9-539ac8b81167` | 90 天 AI 办公试点计划 + 甘特图 | 12 | 14 | 10.6 min | Canvas |
| 3 | 13:47–13:54 | `session-c5150194-7d53-488f-88f2-2980faecd094` | 董事会 PPT（AI 办公 ROI） | 20 | 20 | 6.3 min | `build_deck.py` + PPT |
| 4 | 13:59–14:06 | `session-17558424-3916-436f-974f-7e3bae6e6cc1` | 日活折线图 Dashboard HTML | 7 | 8 | 6.9 min | `dau-dashboard.html` |

空 Session：`session-80f8574f-0cf8-459c-b10b-dcf4fe8d4518`（14:32 创建，仅 `permission/preset`、`sandbox/mode`、`approval/policy` 三条事件）。

### Token 会计（仅主对话循环的 `assistant/message.usage`）

| 任务 | input | output | cacheRead | reasoning |
|---|---:|---:|---:|---:|
| 1 新加坡 | 56,098 | 50,764 | 2,267,008 | 26,904 |
| 2 试点计划 | 53,248 | 36,836 | 427,136 | 23,451 |
| 3 董事会 PPT | 28,915 | 47,886 | 968,960 | 28,097 |
| 4 Dashboard | 3,865 | 29,389 | 208,640 | 21,231 |

另外还有两类**不计入上表**的模型请求：

- 每条完成会话都有 `session/title-llm-request`：用首条用户消息生成侧边栏标题。
- 任务 1 有 21 次 `web/deepseek-search-llm-request`：`web_search` 走 DeepSeek Search Messages 端点，不是 chat-completions。

日志物理文件：

```text
~/.dsh/sessions/--Users-duyuntao.1-Desktop--/<session-id>/session.jsonl.zstd
```

编码：独立 checksummed Zstandard frame 拼接；默认还把连续 `assistant/chunk` 打成 `text-chunks` / `reasoning-chunks` / `tool-call-chunks` 行。直接 `cat` 看不到明文。解码入口：`packages/session/session-persistence-jsonl/src/zstd.ts` + `packages/core/session/src/chunk-rows.ts` 的 `decodeStorageRecord()`。

---

## 2. 端到端激活路径

下面是这四次任务共用的运行时路径。括号里是被激活的源文件。

### 2.1 进程启动（一次，四次任务共享）

```text
pnpm dsh web
  apps/cli/src/bin.ts                    # 解析 argv，dsh web ≡ --profile web
  apps/cli/src/profile-boot.ts           # 加载 profile
  packages/boot/app-boot/src/index.ts    # 组合空根 + bundle patch + 用户 overlay
    packages/bundle/base/cordis.patch.yml      # 能力层：LLM / tools / persistence / sandbox
    packages/bundle/web-app/cordis.patch.yml   # Web 层：persona、HTTP、浏览器 roster
  packages/host/webserver/src/index.ts   # 绑定 http://127.0.0.1:3080
  packages/host/frontend-static/         # 提供已构建的 Web 前端
  packages/host/apiproxy/src/api-proxy.ts # Host JSON-RPC / 会话 RPC
```

Profile 目录：`~/.dsh/profiles/web/`。会话根：`dshHomePath('sessions')` → `~/.dsh/sessions`（`packages/bundle/base/cordis.patch.yml` 中 `session-persistence-jsonl` 行）。

### 2.2 用户在 UI 里发一条消息

```text
浏览器 Composer
  packages/client/ui-conversation/       # 会话主界面 / 输入框
  packages/client/runtime/               # Session 对象层，发 RPC
  packages/host/apiproxy/src/api-proxy.ts
      durablePromptContent()             # 把附件变成可持久化 content
      agent.followup(message)            # ~L2470–2499
  packages/core/agent/src/index.ts       # ctx.agents 注册表 + Inbox
  packages/core/agent-loop/src/agent.ts  # ReactLoopAgent 被 inbox 唤醒
```

用户消息在日志里的 `source` 形如：

```json
{ "kind": "user", "rpcId": "<uuid>", "clientTimeZone": "Asia/Shanghai" }
```

证明这四次都是 Web GUI 经 ApiProxy 写入，而不是 headless / Python SDK。

### 2.3 一个 Turn / Step（循环内核）

权威实现：`packages/core/agent-loop/src/agent.ts`。与 `docs/architecture.md`、`docs/agent-lifecycle.md` 一致。

```text
turn/start
  inbox.claim()                          # 取出 queued user message
  ctx.systemPrompt.assemble()            # packages/core/system-prompt/src/index.ts
  agent/pre-step waterfall               # 注入 runtime-context、skill catalog
  step/start
  user/message *                         # 本步进入模型的全部 user-role 消息
  buildRequest()                         # 组装 GenerateOptions
    agent/request waterfall
    ctx.llm.prepareCall()                # packages/llm/llm/src/index.ts
    request/header + request/context     # 只在 initial / 变化时写日志
  ctx.llm.stream(request)                # llm/stream waterfall
    DeepSeekAdapter.fetch POST           # 见 §4
  assistant/chunk *  →  assistant/message
  executeToolCalls()                     # packages/core/agent-loop/src/tool-calls.ts
    tool/call → tools/pre-execute → tools/execute → tools/post-execute → tool/result
  step/end
  （若还有 tool 结果要喂回模型 → 下一 step）
agent/turn-stopping
turn/end
```

四次任务都是 **1 个 turn、N 个 step**：模型每要一次工具，就多一轮「请求模型 → 跑工具 → 再请求」。没有 compaction、没有 subagent、没有 plan-mode 退出。

### 2.4 每步进入模型的三类 user 消息

日志里每个完成会话的 step 1 都有 3 条 `user/message`：

| source.kind | 谁写入 | 内容 |
|---|---|---|
| `user` | ApiProxy `followup` | 你在 UI 里打的任务原文 |
| `plugin` / `@deepseek-ai/dsh-system-prompt` / form=`snapshot` | `packages/core/agent-loop/src/runtime-context.ts` | sandbox + approval 当前策略快照 |
| `skill-catalog` | `packages/skill/tool-skill/src/index.ts` | `<available_skills>` 目录（本机 Cursor/Codex skills） |

之后各 step 只追加 `tool/result` 投影出来的历史，不再重复这三条，除非策略变化。

---

## 3. Prompt 如何设计（实际渲染）

### 3.1 组装器

文件：`packages/core/system-prompt/src/index.ts`

- `SystemPrompt.section()` 按 `order` 拼接静态/动态段落。
- `SystemPrompt.tools()` 收集本 scope 可见工具的 JSON Schema。
- `system-prompt/assemble` waterfall 可改 assembly；完整 `complete` section 会覆盖其它段落。
- `renderPrompt(assembly)` 得到最终 system 字符串，写入 `request/header.header.system`。

四次任务的 `request/header` **完全相同**：`reason: initial`，`systemChars: 6019`，25 个 tool schema。说明 preset=`standard` 下 prompt 是部署级的，不随任务变化。

### 3.2 实际 system 文本的来源（按 order）

日志里的 system 开头三段，可以一一对上注册点：

| 日志原文（节选） | 注册点 | 文件 |
|---|---|---|
| `You are an AI agent powered by DeepSeek Harness.` | `harness:identity` order -100 | `packages/core/system-prompt/src/index.ts` constructor |
| `The DeepSeek Harness implementation checkout is at /Users/duyuntao.1/Desktop/deepseek-harness/.` | `harness:source` order -99 | `packages/boot/app-boot/src/index.ts` `addHarnessSourceSection()` |
| `You are interacting with the user through the DeepSeek Harness Web GUI at http://127.0.0.1:3080.` | Web GUI 段 | `packages/bundle/web-app/src/index.ts` |
| `You are a coding agent powered by the deepseek-v4-flash model. Your working directory is /Users/duyuntao.1/Desktop.` | `deployment:persona` order 0 | `packages/bundle/web-app/cordis.patch.yml` 的 `persona:`，`{{model}}` / `{{cwd}}` 在 assemble 时插值 |
| `Use the read tool — not shell commands like cat — ...` | `tool:read` 等 100–199 | `packages/fs/tool-fs/src/read.ts`、`write.ts`、`edit.ts` |
| glob / grep 段 | `tool:glob` / `tool:grep` | `packages/fs/tool-fs-search/src/glob.ts` 等 |
| bash exit code 段 | `tool:bash` | `packages/shell/tool-bash/src/index.ts` |
| job_* 段 | `tool:jobs` | `packages/todo` 旁的 jobs 工具包 `packages/` 下 `tool-jobs` |
| web_search 段 | `tool:web_search` | `packages/web/tool-web/src/search.ts` |
| skill 段 | `tool:skill` | `packages/skill/tool-skill/src/index.ts` |
| plan-mode 段（工具仍挂着 `exit_plan_mode`） | `plan-mode` | `packages/plan/plan-mode/src/index.ts` + `packages/bundle/base/cordis.patch.yml` |
| AGENTS.md 工作区指令 | `agent-instructions` | `packages/context/agent-instructions/src/index.ts`（本次 cwd=Desktop，没有仓库 AGENTS.md，所以这段很短或空） |

`{{model}}` 来自当时 `AgentOptions.model` = `deepseek-v4-flash`（`packages/core/agent-default-model`，base bundle 默认；也可被 Settings 覆盖）。

### 3.3 交给模型的 tools 列表（25 个，四次相同）

```text
ask_user_question  bash  create_goal  edit  exit_plan_mode  get_goal
glob  grep  interrupt_agent  job_kill  job_list  job_output
list_agents  ralph  read  read_image  send_message  skill
subagent  subagent_fork  todo_write  update_goal  web_search  workflow  write
```

Schema 由 `packages/core/tools/src/index.ts` 的 `wireSchemas()` 在 assemble 时从 `ctx.tools` 导出。Web overlay 里 `tools.mode` 未设，走默认 **native**（不是 Code Mode），所以模型直接发这些 tool name，而不是只发 `run_code`。

### 3.4 `request/header` 如何保证「模型可见 ⟺ 已记录」

`packages/core/agent-loop/src/agent.ts` 的 `buildRequest()`：

1. 用 `renderPrompt(assembly)` 得到 system。
2. 用 `session.deriveMessages()` 从事件日志投影对话历史。
3. `ctx.llm.prepareCall(config)` 填 adapter 默认（本次 `reasoningEffort: max`）。
4. 把 `{ config, system, tools }` 写成 `request/header`。
5. 再 `ctx.llm.stream(request)`。

因此这四次任务的每一次模型请求，都可以从同一条 JSONL 无损重建。

---

## 4. 基座模型 API 如何激活

### 4.1 主对话：Chat Completions

| 层 | 文件 | 作用 |
|---|---|---|
| 路由注册 | `packages/llm/llm-deepseek/src/index.ts` | `registerAdapter(['deepseek-official'], DeepSeekAdapter)` |
| 连接解析 | 同上 `resolveAdapterOptions()` | settings `llm-deepseek:` + `$DEEPSEEK_API_KEY` / `~/.dsh/.credentials.yaml` |
| 序列化 | `packages/llm/llm-deepseek/src/serialize.ts` | Message → OpenAI 风格 `messages` / `tools` / `thinking` |
| HTTP | `packages/llm/llm-deepseek/src/adapter.ts` ~L301 | `POST {baseURL}/chat/completions`，`Accept: text/event-stream` |
| SSE | `packages/llm/llm-deepseek/src/sse.ts` | 拆 event-stream |
| 翻译 | `packages/llm/llm-deepseek/src/translate.ts` | wire chunk → harness `StreamChunk` |
| 服务 | `packages/llm/llm/src/index.ts` | `llm/stream` waterfall；`llm-retry` 可在失败时重试 |
| 组装 | `packages/llm/llm/src/assembler.ts` `BlockAssembler` | chunk → `assistant/message` |

默认 `baseURL`：`https://api.deepseek.com`（可被 `DEEPSEEK_BASE_URL` 或 settings 覆盖）。请求头还包括：

- `Authorization: Bearer <key>`（密钥来自 credentials，不进 session 日志）
- `x-deepseek-harness-user-id` ← `~/.dsh/.anonymous-user-id`
- `x-deepseek-harness-session-id` ← 当前 SessionId

`thinking` / `reasoningEffort`：serialize 把 `max` 编成 thinking enabled + effort max。日志里四次都是 `reasoningEffort: max`，且每步 `usage.reasoningTokens` 非零，说明走了思考模式。

### 4.2 标题：另一次短请求

`packages/session/session-title-first-prompt-llm/src/index.ts` 在首条用户消息后发 `purpose: 'session-title'` 的 LLM 调用（serialize 会强制 `thinking: disabled`）。日志事件：`session/title-llm-request`、`session/title`。

### 4.3 搜索：Messages 端点（仅任务 1）

`web_search` 不走 chat-completions。

```text
packages/web/tool-web/src/search.ts          # 模型工具
packages/web/web/src/                        # ctx.web 服务
packages/web/web-search-deepseek/src/provider.ts
    发出 web/deepseek-search-llm-request
```

任务 1 记录了 **21** 次该事件，与 21 次 `web_search` 工具调用一一对应。

### 4.4 凭证

`packages/credentials/credentials-local/` + `packages/settings/settings-file/`。解析顺序（CLI 参考文档）：继承环境 → `~/.dsh/.credentials.yaml` → 调用目录 `.env` → `~/.dsh/.env`。Web 的「设置 → 模型」写入 credentials 文档，不重启进程。

---

## 5. 工具如何调用

### 5.1 调度

`packages/core/agent-loop/src/tool-calls.ts`：

1. 从 `assistant/message` 里抽出 `tool-call` block（原始 `arguments` 字符串）。
2. `JSON.parse` 成对象。
3. 先写 `tool/call`（执行前就落盘）。
4. `tools/pre-execute`：hooks、permission、approval。
5. monotonic guards。
6. `tools/execute`：真正的 `ToolDefinition.execute()`，可被 timeout 包一层。
7. `tools/post-execute`。
8. 写 `tool/result`，投影进下一轮 `deriveMessages()`。

并行：同一 assistant 消息里的多个 tool-call 可并行（任务 1 step 1 同时 `skill` + `bash` + 两次 `web_search`）。`exclusive` 工具会形成屏障。

### 5.2 本次实际用到的工具 → 实现文件

| 工具 | 调用次数（四任务合计） | 实现 |
|---|---:|---|
| `bash` | 很多 | `packages/shell/tool-bash/src/index.ts` → `ctx.shell` → `packages/subprocess/subprocess-local`；macOS 上再套 `packages/shell/bash-sandbox` |
| `web_search` | 21（仅任务 1） | `packages/web/tool-web/src/search.ts` |
| `write` | 若干 | `packages/fs/tool-fs/src/write.ts` → `packages/fs/fs-sandbox/src/index.ts` |
| `edit` | 若干 | `packages/fs/tool-fs/src/edit.ts` |
| `read` | 若干 | `packages/fs/tool-fs/src/read.ts` |
| `skill` | 4（每任务 1 次，都是 `canvas`） | `packages/skill/tool-skill/src/index.ts` → `packages/skill/skill-filesystem` |
| `todo_write` | 2（仅任务 1） | `packages/todo/tool-todo` |

没有使用：`grep`/`glob`/`subagent`/`workflow`/`ralph`/`ask_user_question`/`exit_plan_mode`。

### 5.3 沙箱与审批（任务 1、2 踩过）

默认权限：`workspace-write` + `approval: ask`（`packages/bundle/base/cordis.patch.yml` 的 `sandbox-policy` / `permission` / `approval`）。

工作区是 Desktop，所以写 `~/Desktop/*.html` 直接成功。写到 `~/.cursor/projects/.../canvases/*.canvas.tsx` **越出工作区**，`fs-sandbox` 抛 `FS_SANDBOX_DENIED`（`packages/fs/fs-sandbox/src/index.ts`）。工具层带 `sandbox_permissions: danger-full-access` 重试，触发 `approval/asked` → UI 询问 → `approval/decided`（`packages/interaction/user-approval/src/index.ts`）。

日志证据：

- 任务 1：`approval/asked` ×4、`approval/decided` ×4；`write`/`edit` 各有一次 `FS_SANDBOX_DENIED` 后带 justification 重试。
- 任务 2：`approval/asked` ×3、`approval/decided` ×3；同样先 deny 再 escalation。
- 任务 3、4：产出在 Desktop 内，无审批事件。

`bash` 出站 `curl` 不受 fs-sandbox 管；Seatbelt 策略在 `packages/shell/bash-sandbox`。任务 1/4 大量 curl CDN 与航班页，说明当前 macOS sandbox 允许这些网络请求。

---

## 6. 分任务逐步追踪

### 任务 1 — 新加坡商务行程

- 日志：`~/.dsh/sessions/--Users-duyuntao.1-Desktop--/session-fc8efec4-774b-4748-9b54-1f779b857b21/session.jsonl.zstd`
- 用户原文要点：6 人、沪 4 京 2、10/19 17:00 前抵达、禁红眼、10/22 17:00 后返程、四星、距 MBFC ≤25 分钟、预算 9 万、主方案+异常备选、要来源、不要下单。

策略：先 `skill(canvas)` 加载可视化技能，再 `web_search` + `bash curl` 采时刻/房价/汇率/免签规则，最后把方案写成 Canvas。

| Step | 模型之后的工具 | 在做什么 |
|---|---|---|
| 1 | skill, bash, web_search×2 | 加载 canvas 技能；看当天日期；搜 PVG/PEK→SIN 航班 |
| 2 | todo_write, web_search×2 | 立 6 项待办；补班次 |
| 3–13 | bash（curl kvikr / flightera / directflights 等） | 抓时刻表 HTML 并用 python 抽航班号 |
| 14 | web_search×4 | 票价、酒店、免签 |
| 15–22 | bash + web_search 交错 | 文章、flightprices、PBC 汇率、Trip.com、HopeGoo |
| 23–25 | web_search | 再核对票价与 SGAC |
| 26–29 | bash, read×4 | 定位 Canvas SDK `.d.ts` |
| 30–31 | write | 第一次写 canvas 被沙箱拒绝，第二次 `danger-full-access` 成功 |
| 32–39 | bash tsc, edit×4 | 类型检查并修 import / DaySection / Link |
| 40 | todo_write | 全部标 completed |
| 41 | （无工具） | 把完整方案写进 assistant 正文 |

产出：`~/.cursor/projects/Users-duyuntao-1-Desktop/canvases/singapore-biz-trip-oct2026.canvas.tsx`。

额外模型 API：21 次 DeepSeek Search。主循环 41 次 chat-completions。

### 任务 2 — 90 天 AI 办公试点计划

- 日志：`.../session-4ca60d00-ef1a-4cc6-a0d9-539ac8b81167/session.jsonl.zstd`
- 用户原文：五部门、120 人、80 万、前 30 天禁生产数据、财务全程脱敏、D30/D60 评审、1 PM + 2 技术 + 5 代表，要排期/分工/预算/培训/指标/风险 + 甘特图。

| Step | 工具 | 在做什么 |
|---|---|---|
| 1 | skill(canvas) | 加载技能正文 |
| 2 | bash | `ls ~/.cursor/projects` 与 canvas SDK 目录 |
| 3–5 | read×6 | 读 `hooks.d.ts` / `ui-primitives.d.ts` / `usage-bar.d.ts` 等，确认组件 API |
| 6–7 | write | 先被 `FS_SANDBOX_DENIED`，审批后写入 `ai-office-pilot-plan.canvas.tsx` |
| 8–10 | edit | 甘特图网格/里程碑标签 |
| 11 | read | 通读成品 |
| 12 | （无工具） | 向用户说明画布链接与结构 |

没有 `web_search`：任务是规划不是事实核查。12 步里大量 output/reasoning 花在生成那份 500+ 行 TSX。

### 任务 3 — 董事会 PPT

- 日志：`.../session-c5150194-7d53-488f-88f2-2980faecd094/session.jsonl.zstd`
- 用户原文：500 人、人均年成本 30 万、60% 岗位可用、1.2h 重复工作、AI 省 30%、采用率 65%、可用率 75%、350 元/席/月；要 ≤12 页董事会 PPT。

| Step | 工具 | 在做什么 |
|---|---|---|
| 1 | skill(canvas), bash | 仍先加载 canvas；同时探测 `python-pptx` / matplotlib / node |
| 2 | bash | `mkdir Desktop/AI办公工具董事会汇报` |
| 3 | bash | 用 python 验算 ROI 公式 |
| 4 | write | `build_deck.py`（约 29 KB） |
| 5 | bash | `python3 build_deck.py` |
| 6–18 | edit / read / bash | 修字体、图表、备注、页结构，多次重建 |
| 19 | bash | 最终校验 |
| 20 | （无工具） | 交付说明 |

产出在 Desktop 内，无沙箱审批。`skill(canvas)` 被调用但最终交付是 PPTX 而不是 canvas——模型先按技能目录习惯加载 canvas，发现 `python-pptx` 可用后改道。

### 任务 4 — 日活 Dashboard HTML

- 日志：`.../session-17558424-3916-436f-974f-7e3bae6e6cc1/session.jsonl.zstd`
- 用户原文：日期范围筛选、日活折线、Ant Design、响应式、模拟数据、单文件 HTML。

| Step | 工具 | 在做什么 |
|---|---|---|
| 1 | skill(canvas), bash | 又先加载 canvas；同时 curl 探测 moment/react/antd CDN |
| 2–3 | bash | 下载 antd UMD、检查 babel presets |
| 4 | write | `/Users/duyuntao.1/Desktop/dau-dashboard.html`（17,000 字符） |
| 5–6 | bash | 检查 script 标签；用本机 babel 编译内嵌 JSX |
| 7 | （无工具） | 报告功能表 |

主循环 input 只有 3,865，但 reasoning 21,231、output 29,389：几乎整份 HTML 是模型在 step 4 一次生成的。Canvas 技能再次被加载但未使用。

---

## 7. 激活文件清单（按链路阶段）

下列是这四次运行**确定走到**的源文件（不含测试）。Cordis 还会加载 base/web-app patch 里的其它插件（subagent、plan-mode、workflow…），它们注册了工具和 prompt 段，但本次模型没有调用对应工具。

### 启动与 Host

- `apps/cli/src/bin.ts`
- `apps/cli/src/args.ts`
- `apps/cli/src/profile-boot.ts`
- `packages/boot/app-boot/src/index.ts`
- `packages/bundle/base/cordis.patch.yml`
- `packages/bundle/web-app/cordis.patch.yml`
- `packages/bundle/web-app/src/index.ts`（GUI prompt 段）
- `packages/host/webserver/src/index.ts`
- `packages/host/frontend-static/`
- `packages/host/apiproxy/src/api-proxy.ts`
- `packages/host/apiproxy/src/api-proxy.ts` 中 `followup` 分支

### 会话 / 循环 / Prompt

- `packages/core/session/src/index.ts`、`types.ts`、`chunk-rows.ts`、`request-header.ts`
- `packages/session/session-persistence-jsonl/src/index.ts`、`zstd.ts`、`format.ts`
- `packages/core/agent/src/index.ts`、`inbox.ts`、`dispatch.ts`
- `packages/core/agent-loop/src/agent.ts`、`tool-calls.ts`、`runtime-context.ts`
- `packages/core/system-prompt/src/index.ts`
- `packages/core/agent-default-model/`
- `packages/core/scope/`
- `packages/context/agent-instructions/src/index.ts`
- `packages/session/session-title/src/index.ts`
- `packages/session/session-title-first-prompt-llm/src/index.ts`
- `packages/session/session-title-llm/src/index.ts`

### LLM API

- `packages/llm/llm/src/index.ts`、`assembler.ts`、`call-config.ts`
- `packages/llm/llm-deepseek/src/index.ts`、`adapter.ts`、`serialize.ts`、`sse.ts`、`translate.ts`
- `packages/llm/llm-retry/src/index.ts`（注册了；这四次主循环未见 retry 事件）
- `packages/credentials/`、`packages/settings/settings-file/`
- `packages/identity/anonymous-user-id/`

### 工具与策略

- `packages/core/tools/src/index.ts`
- `packages/shell/tool-bash/src/index.ts`
- `packages/shell/bash-sandbox/`
- `packages/subprocess/subprocess-local/`
- `packages/fs/tool-fs/src/write.ts`、`edit.ts`、`read.ts`
- `packages/fs/fs-sandbox/src/index.ts`
- `packages/fs/fs-observation-policy/`
- `packages/sandbox/sandbox-policy/`
- `packages/interaction/user-approval/src/index.ts`
- `packages/interaction/permission-presets/`
- `packages/web/tool-web/src/search.ts`
- `packages/web/web-search-deepseek/src/index.ts`、`provider.ts`
- `packages/skill/tool-skill/src/index.ts`
- `packages/skill/skill-filesystem/`
- `packages/todo/tool-todo/`
- `packages/plan/plan-mode/src/index.ts`（prompt + `exit_plan_mode` schema，未调用）

### 前端（查看结果时）

- `packages/client/ui-conversation/`
- `packages/client/ui-workspace/`（侧边栏历史）
- `packages/client/ui-trajectory/`（把 session 事件画成时间线）
- `packages/client/runtime/`

### 仍加载但本次未走业务路径的例子

`tool-subagent`、`tool-workflow`、`compaction-basic`、`tool-fs-search` 的 grep/glob、`code-runtime`。它们出现在 25 个 schema 和 prompt 里，所以**参与了每次模型请求的 token**，只是没有 `tool/call`。

---

## 8. 跨任务行为模式

1. **每个任务第一步都 `skill({ name: "canvas" })`**。技能目录来自本机 `~/.cursor` / `~/.agents` 的 Codex/Cursor skills，被 `skill-filesystem` 挂进 DSH。模型把「结构化交付」默认映射到 Canvas，即使任务 3/4 最终用了 PPT/HTML。
2. **越出 Desktop 的写文件必须审批**。Canvas 路径在 `~/.cursor/projects/...`，触发 `FS_SANDBOX_DENIED` → `danger-full-access` + `approval/asked`。Desktop 内的 HTML/PPT 无此步骤。
3. **没有 subagent、没有并行多 agent**。复杂调研（任务 1）全在父会话里用 bash/web_search 串行+同 step 并行完成。
4. **Prompt 与工具表四次字节级相同**。差异全在 user 消息和随后的 tool 历史。
5. **cacheRead 远大于 input**。长会话（任务 1 的 41 step）前缀命中 KV cache；这是 `request/header` 稳定（同一 system + tools）的直接后果。
6. **模型可见输入全部在日志里**：用户句、runtime snapshot、skill catalog、tool 结果、完整 system/tools 快照。

---

## 9. 如何自己复现这次解码

```sh
# 日志位置
ls ~/.dsh/sessions/--Users-duyuntao.1-Desktop--/

# 不要用 zstd -d 一次解整个文件后当普通 JSONL：
# 文件是多 frame 拼接，且含 packed chunk 行。
# 应使用仓库的 scanZstdFrames + decodeStorageRecord。
```

关注的事件类型：`user/message`、`request/header`、`assistant/message`、`tool/call`、`tool/result`、`approval/asked`、`web/deepseek-search-llm-request`、`session/title-llm-request`。

侧边栏打开历史：Web UI 对同一工作区列出 Session，点击即 `ctx.agents.resume()`（`packages/core/agent-loop` + `session-persistence-jsonl` load），把上述事件重放成对话。
