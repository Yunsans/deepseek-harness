/**
 * Extract a completed turn, call the base model twice, and format the office-eval report.
 */

import { mkdir, writeFile } from 'node:fs/promises'
import { isAbsolute, join } from 'node:path'
import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import {
  BlockAssembler,
  boundContextSummary,
  createUserMessage,
  deepFreeze,
  ReasoningEffortId,
} from '@deepseek-ai/dsh-llm'
import type { ContentBlock, FinishReason, GenerateOptions, Message } from '@deepseek-ai/dsh-llm'
import type { Session, SessionEvent } from '@deepseek-ai/dsh-session'
import {
  RUBRIC_SYSTEM_PROMPT,
  SCORE_SYSTEM_PROMPT,
  rubricUserPrompt,
  scoreUserPrompt,
} from './office-eval-prompts.ts'

/** Validated plugin configuration consumed by the evaluation pipeline. */
export interface OfficeEvalConfig {
  /** When true, every newly created agent starts with auto-eval armed. */
  readonly auto: boolean
  /** Directory name under the session cwd for markdown reports. */
  readonly outputDirName: string
  /** Output-token cap for each auxiliary model call. */
  readonly maxOutputTokens: number
  /** Maximum characters of the agent answer sent to the scoring call. */
  readonly maxAnswerChars: number
  /** Extra model attempts after a JSON parse failure (0 disables retries). */
  readonly jsonRetries: number
  /** Requested multiple-choice count for rubric generation. */
  readonly questionCount: number
  /** Requested fact-checkpoint count for rubric generation. */
  readonly checkpointCount: number
}

const PLUGIN = 'office-eval'
const LAYER_VALUES = new Set(['通过', '部分通过', '未通过', '不适用', '无法核验'])
const FACT_CHOICES = new Set(['A', 'B', 'C', 'D', 'E', 'F', 'G'])

/** One multiple-choice item frozen before the answer is scored. */
export interface RubricQuestion {
  readonly id: string
  readonly prompt: string
  readonly options: Readonly<Record<string, string>>
}

/** One fixed-position fact check frozen before the answer is scored. */
export interface RubricCheckpoint {
  readonly id: string
  readonly description: string
}

/** Rubric generated from the user task alone. */
export interface Rubric {
  readonly questions: readonly RubricQuestion[]
  readonly factCheckpoints: readonly RubricCheckpoint[]
}

/** One scored multiple-choice item. */
export interface ScoredQuestion {
  readonly id: string
  readonly choice: string
  readonly rationale: string
  readonly evidence: string
}

/** One scored fact checkpoint. */
export interface ScoredCheckpoint {
  readonly id: string
  readonly excerpt: string
  readonly choice: string
  readonly verification: string
  readonly rationale: string
}

/** Structured evaluation of one agent answer. */
export interface Scorecard {
  readonly resultCheck: string
  readonly fileCheck: string
  readonly visualCheck: string
  readonly needsHumanReview: boolean
  readonly oneLiner: string
  readonly questions: readonly ScoredQuestion[]
  readonly factCheckpoints: readonly ScoredCheckpoint[]
  readonly strengths: readonly string[]
  readonly issues: readonly string[]
  readonly unverifiable: readonly string[]
}

/** Filesystem path plus markdown body of one evaluation. */
export interface OfficeEvalReport {
  readonly reportMarkdown: string
  readonly reportPath: string
  readonly summaryLine: string
}

/** Task text and deliverable extracted from one completed turn. */
export interface TurnMaterial {
  readonly turn: number
  readonly task: string
  readonly answer: string
}

/**
 * Locate the latest completed turn that contains a human task.
 * @param session - the live session log.
 * @returns the turn number, or `undefined` when none is evaluable.
 */
export function lastEvaluableTurn(session: Session): number | undefined {
  for (const event of session.events.slice().reverse()) {
    if (event.type !== 'turn/end') continue
    if (!isEvaluableReason(event.data.reason.kind)) continue
    const material = extractTurn(session, event.data.turn)
    if (material !== undefined) return event.data.turn
  }
  return undefined
}

/**
 * Run rubric generation, scoring, file write, and markdown formatting.
 * @param ctx - context exposing `ctx.llm`.
 * @param agent - the agent whose last (or named) turn is evaluated.
 * @param config - validated plugin configuration.
 * @param turn - turn to evaluate; the latest evaluable turn when omitted.
 * @param signal - cancellation for both model calls and the file write.
 * @returns the report body, path, and one-line summary.
 */
export async function runOfficeEval(
  ctx: Context,
  agent: Agent,
  config: OfficeEvalConfig,
  turn: number | undefined,
  signal: AbortSignal,
): Promise<OfficeEvalReport> {
  signal.throwIfAborted()
  const selectedTurn = turn ?? lastEvaluableTurn(agent.session)
  if (selectedTurn === undefined) {
    throw new Error('没有可评测的已完成轮次：需要用户任务和智能体回答。')
  }
  const material = extractTurn(agent.session, selectedTurn)
  if (material === undefined) {
    throw new Error(`第 ${String(selectedTurn)} 轮缺少用户任务或智能体回答。`)
  }
  const route = resolveRoute(agent)
  const rubric = await completeJsonParsed(ctx, {
    route,
    system: RUBRIC_SYSTEM_PROMPT,
    user: rubricUserPrompt(material.task, config.questionCount, config.checkpointCount),
    maxTokens: config.maxOutputTokens,
    sessionId: agent.session.id,
    signal,
  }, parseRubric, config.jsonRetries)
  const scorecard = await completeJsonParsed(ctx, {
    route,
    system: SCORE_SYSTEM_PROMPT,
    user: scoreUserPrompt(material.task, clip(material.answer, config.maxAnswerChars), JSON.stringify(rubric, null, 2)),
    maxTokens: config.maxOutputTokens,
    sessionId: agent.session.id,
    signal,
  }, text => parseScorecard(text, rubric), config.jsonRetries)
  const reportMarkdown = formatReport(selectedTurn, material.task, rubric, scorecard)
  const reportPath = await writeReport(agent, config.outputDirName, selectedTurn, reportMarkdown)
  return {
    reportMarkdown,
    reportPath,
    summaryLine: boundContextSummary(`办公智能体评测已完成：${scorecard.oneLiner}`),
  }
}

/**
 * Append a collapsed notice that carries the evaluation report.
 * @param agent - the idle agent that just finished the evaluated turn.
 * @param report - markdown report and optional file path.
 */
export function publishEvalNotice(agent: Agent, report: OfficeEvalReport): void {
  const pathLine = report.reportPath.length > 0 ? `\n\n报告文件：${report.reportPath}` : ''
  agent.session.append('user/message', createUserMessage({
    content: [{ type: 'text', text: `${report.reportMarkdown}${pathLine}` }],
    source: {
      kind: 'plugin',
      plugin: PLUGIN,
      form: 'notice',
      summary: report.summaryLine,
    },
  }), { surfaceOp: 'append' })
}

/** Whether a turn-end reason is a completed model turn rather than a cancel or error. */
function isEvaluableReason(kind: string): boolean {
  return kind === 'completed' || kind === 'max-tokens'
}

/**
 * Collect the human task and the agent deliverable for one turn.
 * @param session - the session log.
 * @param turn - the turn number.
 * @returns task and answer text, or `undefined` when the turn is not a human task turn.
 */
export function extractTurn(session: Session, turn: number): TurnMaterial | undefined {
  const slice = eventsOfTurn(session.events, turn)
  const tasks: string[] = []
  const answerParts: string[] = []
  for (const event of slice) {
    if (event.type === 'user/message' && event.data.source.kind === 'user') {
      const text = blocksText(event.data.content).trim()
      if (text.length > 0) tasks.push(text)
    }
    if (event.type === 'assistant/message') {
      const text = blocksText(event.data.message.content).trim()
      if (text.length > 0) answerParts.push(text)
    }
    if (event.type === 'tool/call') {
      answerParts.push(`[工具调用 ${event.data.name}]\n${event.data.arguments}`)
    }
    if (event.type === 'tool/result') {
      const text = blocksText(event.data.message.content).trim()
      const label = event.data.error === undefined ? '工具结果' : `工具错误 ${event.data.error.code}`
      answerParts.push(`[${label}]\n${text}`)
    }
  }
  if (tasks.length === 0 || answerParts.length === 0) return undefined
  return { turn, task: tasks.join('\n\n'), answer: answerParts.join('\n\n') }
}

/** Events from `turn/start` through matching `turn/end`, inclusive. */
function eventsOfTurn(events: readonly SessionEvent[], turn: number): SessionEvent[] {
  const slice: SessionEvent[] = []
  let inTurn = false
  for (const event of events) {
    if (event.type === 'turn/start' && event.data.turn === turn) inTurn = true
    if (inTurn) slice.push(event)
    if (event.type === 'turn/end' && event.data.turn === turn) break
  }
  return slice
}

/** Flatten text-bearing content blocks; skip images and reasoning. */
function blocksText(content: readonly ContentBlock[]): string {
  const parts: string[] = []
  for (const block of content) {
    switch (block.type) {
      case 'text':
        parts.push(block.text)
        break
      case 'tool-result':
        parts.push(blocksText(block.content))
        break
      default:
        break
    }
  }
  return parts.join('\n')
}

/** Provider and model used by the conversation, required for the auxiliary calls. */
function resolveRoute(agent: Agent): { provider: string; model: string } {
  const header = agent.session.requestHeader()
  const provider = header?.config.provider ?? agent.options.provider
  const model = header?.config.model ?? agent.options.model
  if (provider === undefined || model === undefined) {
    throw new Error('无法确定评测所用模型：会话还没有 request/header，且 agent.options 缺少 provider/model。')
  }
  return { provider, model }
}

interface CompletionRequest {
  readonly route: { readonly provider: string; readonly model: string }
  readonly system: string
  readonly user: string
  /** Corrective user messages appended after earlier attempts failed to parse. */
  readonly followUps?: readonly string[]
  readonly maxTokens: number
  readonly sessionId: Agent['session']['id']
  readonly signal: AbortSignal
}

/** Stream one auxiliary JSON completion and return concatenated text. */
async function completeJson(ctx: Context, request: CompletionRequest): Promise<string> {
  request.signal.throwIfAborted()
  const messages: Message[] = [createUserMessage({
    content: [{ type: 'text', text: request.user }],
    source: { kind: 'plugin', plugin: PLUGIN },
  })]
  for (const followUp of request.followUps ?? []) {
    messages.push(createUserMessage({
      content: [{ type: 'text', text: followUp }],
      source: { kind: 'plugin', plugin: PLUGIN },
    }))
  }
  const options: GenerateOptions = deepFreeze({
    provider: request.route.provider,
    model: request.route.model,
    // JSON scoring must land in visible text. Omitting this inherits the
    // conversation default (high/max thinking), which often finishes with
    // reasoning blocks only — the failure seen in session-42e12e2e.
    reasoningEffort: ReasoningEffortId('off'),
    messages,
    system: request.system,
    maxTokens: request.maxTokens,
    sessionId: request.sessionId,
    signal: request.signal,
  })
  const assembler = new BlockAssembler()
  for await (const chunk of ctx.llm.stream(options)) {
    request.signal.throwIfAborted()
    assembler.push(chunk)
  }
  request.signal.throwIfAborted()
  const terminal = finishError(assembler.finish)
  if (terminal !== undefined) throw terminal
  const blocks = assembler.blocks()
  const text = blocks
    .filter((block): block is Extract<ContentBlock, { type: 'text' }> => block.type === 'text')
    .map(block => block.text)
    .join('')
    .trim()
  if (text.length > 0) return text
  const reasoning = blocks
    .filter((block): block is Extract<ContentBlock, { type: 'reasoning' }> => block.type === 'reasoning')
    .map(block => block.text)
    .join('')
    .trim()
  if (reasoning.length > 0) return reasoning
  const kinds = [...new Set(blocks.map(block => block.type))].join(', ') || 'none'
  throw new Error(`评测模型没有返回文本（finish=${assembler.finish.kind}, blocks=${kinds}）。`)
}

/** Map a terminal finish reason to a thrown error, or accept `stop`. */
function finishError(finish: FinishReason): Error | undefined {
  switch (finish.kind) {
    case 'stop':
      return undefined
    case 'error':
    case 'aborted':
      return new Error(finish.failure.message)
    case 'max-tokens':
      return new Error('评测模型输出达到 maxTokens，JSON 可能不完整。请提高 maxOutputTokens 后重试。')
    case 'tool-calls':
      return new Error('评测模型不应请求工具。')
    default:
      return new Error(`评测模型结束原因不受支持：${String((finish as { kind?: unknown }).kind)}`)
  }
}

/**
 * Stream a JSON completion and parse it, retrying the model with the parse
 * error when the output is invalid. Each retry appends a corrective user
 * message so the model can fix its previous output in a fresh completion.
 */
async function completeJsonParsed<T>(
  ctx: Context,
  request: Omit<CompletionRequest, 'followUps'>,
  parse: (text: string) => T,
  retries: number,
): Promise<T> {
  const followUps: string[] = []
  for (let attempt = 0; ; attempt++) {
    request.signal.throwIfAborted()
    const text = await completeJson(ctx, { ...request, followUps })
    try {
      return parse(text)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      if (attempt >= retries) throw error
      followUps.push(jsonRetryFollowUp(message))
      console.warn(`[office-eval] 评测模型 JSON 无效（第 ${attempt + 1} 次失败），重试：${message}`)
    }
  }
}

/** Corrective instruction appended to the model call after a JSON parse failure. */
function jsonRetryFollowUp(detail: string): string {
  const verificationHint = detail.includes('factCheckpoints[') && detail.includes('.verification')
    ? `
额外修正要求（本次错误必须修复）：
- 你有至少一个 factCheckpoints 项缺少 verification 或该字段为空。
- 为每个 factCheckpoints 项补全 verification，必须是非空字符串。
- verification 必须写“核验来源或核验操作”；若无法核验，请写“未提供来源，无法核验：<原因>”。`
    : ''
  return `你上一次的输出不是合法 JSON，解析失败：

${detail}

请只重新输出一个能通过 JSON.parse 直接解析的 JSON 对象，并严格遵守：
1. 只输出 JSON 本身，不要任何解释文字，不要 Markdown 代码块围栏；
2. 数组元素之间、对象属性之间必须用半角逗号 , 分隔，不得遗漏；
3. 字符串一律用半角双引号包裹；字符串内部的半角双引号要写成 \\" 转义，字符串内不要包含原始换行；
4. 不要尾随逗号，不要注释，不要省略号，不要用单引号代替双引号。
5. 必填字段不得为空字符串，尤其是每个 factCheckpoints 项必须包含非空 verification。
${verificationHint}
重新输出：`
}

/**
 * Extract the JSON object body from model text that may include a fenced block.
 * @throws when the text contains no `{ ... }` object at all.
 */
function extractJsonText(text: string): string {
  const fenced = /```(?:json)?\s*([\s\S]*?)```/u.exec(text)
  const raw = (fenced?.[1] ?? text).trim()
  const start = raw.indexOf('{')
  const end = raw.lastIndexOf('}')
  if (start < 0 || end <= start) throw new Error('评测模型输出中没有 JSON 对象。')
  return raw.slice(start, end + 1)
}

/** Short slice of the output around the first JSON.parse failure, for diagnostics. */
function failureExcerpt(raw: string, detail: string): string {
  const match = /position\s+(\d+)/u.exec(detail)
  const parsed = match === null ? NaN : Number(match[1])
  const position = Number.isFinite(parsed) ? Math.max(0, Math.min(raw.length, parsed)) : 0
  const start = Math.max(0, position - 80)
  const end = Math.min(raw.length, position + 160)
  return `${start === 0 ? '' : '…'}${raw.slice(start, end)}${end >= raw.length ? '' : '…'}`
}

/** JSON escape for a raw control character found inside a string literal. */
function escapeControl(ch: string): string {
  switch (ch) {
    case '\n': return '\\n'
    case '\r': return '\\r'
    case '\t': return '\\t'
    default: return `\\u${ch.charCodeAt(0).toString(16).padStart(4, '0')}`
  }
}

/** Characters that may continue a JSON number literal. */
const JSON_NUMBER_CHARS = new Set(['0','1','2','3','4','5','6','7','8','9','.','e','E','+','-'])
const JSON_IDENT_START = /[A-Za-z]/u

/**
 * Best-effort repair of the most common LLM JSON mistakes, applied only after
 * plain JSON.parse fails. Handles missing commas between array elements or
 * object properties, trailing commas, raw control characters inside strings,
 * and single-quoted strings. Returns the repaired text only when it now
 * parses; otherwise `null` so the caller can fall back to a model retry.
 */
export function repairJsonText(raw: string): string | null {
  let out = ''
  let inString = false
  let quote = ''
  let escaped = false
  let inNumber = false
  let inIdent = false
  let depth = 0
  let prevValueEnd = false
  let commaIndex: number | null = null
  let changed = false
  const singleBuf: string[] = []

  const appendComma = (): void => {
    out = out.replace(/\s+$/u, '')
    out += ','
    commaIndex = out.length - 1
    changed = true
    prevValueEnd = false
  }

  const appendSignificant = (ch: string): void => {
    out += ch
    commaIndex = null
  }

  const dropTrailingComma = (): void => {
    if (commaIndex !== null && out[commaIndex] === ',') {
      out = out.slice(0, commaIndex) + out.slice(commaIndex + 1)
      commaIndex = null
      changed = true
    }
  }

  for (const ch of raw) {
    if (inString) {
      if (escaped) {
        if (quote === '"') {
          out += '\\' + ch
          commaIndex = null
        } else {
          singleBuf.push('\\', ch)
        }
        escaped = false
        continue
      }
      if (ch === '\\') {
        escaped = true
        if (quote === '"') {
          out += '\\'
          commaIndex = null
        } else {
          singleBuf.push('\\')
        }
        continue
      }
      if (ch === quote) {
        if (quote === '"') {
          out += '"'
          commaIndex = null
          prevValueEnd = true
        } else {
          const content = singleBuf.join('')
          const converted = content.replaceAll('\\', '\\\\').replaceAll('"', '\\"')
          if (converted !== content) changed = true
          // The opening quote was already emitted as `"` when the literal
          // started, so only the closing quote is appended here.
          out += converted + '"'
          commaIndex = null
          changed = true
          prevValueEnd = true
          singleBuf.length = 0
        }
        inString = false
        quote = ''
        continue
      }
      if (quote === '"') {
        if (ch.charCodeAt(0) < 0x20) {
          out += escapeControl(ch)
          changed = true
        } else {
          out += ch
        }
        commaIndex = null
      } else {
        if (ch.charCodeAt(0) < 0x20) {
          singleBuf.push(escapeControl(ch))
          changed = true
        } else {
          singleBuf.push(ch)
        }
      }
      continue
    }
    if (inNumber) {
      if (JSON_NUMBER_CHARS.has(ch)) {
        out += ch
        continue
      }
      inNumber = false
      prevValueEnd = true
    }
    if (inIdent) {
      if (JSON_IDENT_START.test(ch)) {
        out += ch
        continue
      }
      inIdent = false
      prevValueEnd = true
    }
    if (ch === ' ' || ch === '\t' || ch === '\n' || ch === '\r') {
      out += ch
      continue
    }
    switch (ch) {
      case '"':
      case "'":
        if (prevValueEnd && depth > 0) appendComma()
        inString = true
        quote = ch
        if (quote === "'") singleBuf.length = 0
        appendSignificant('"')
        if (quote === "'") changed = true
        prevValueEnd = false
        break
      case '{':
      case '[':
        if (prevValueEnd && depth > 0) appendComma()
        depth++
        appendSignificant(ch)
        prevValueEnd = false
        break
      case '}':
      case ']':
        dropTrailingComma()
        depth = Math.max(0, depth - 1)
        appendSignificant(ch)
        prevValueEnd = true
        break
      case ',':
        out += ','
        commaIndex = out.length - 1
        prevValueEnd = false
        break
      case ':':
        appendSignificant(ch)
        prevValueEnd = false
        break
      default:
        if ((ch >= '0' && ch <= '9') || ch === '-') {
          if (prevValueEnd && depth > 0) appendComma()
          inNumber = true
          appendSignificant(ch)
          prevValueEnd = false
        } else if (JSON_IDENT_START.test(ch)) {
          if (prevValueEnd && depth > 0) appendComma()
          inIdent = true
          appendSignificant(ch)
          prevValueEnd = false
        } else {
          out += ch
        }
    }
  }
  dropTrailingComma()
  if (!changed) return null
  try {
    JSON.parse(out)
    return out
  } catch {
    return null
  }
}

/** Parse a JSON object from model text that may include a fenced block. */
export function parseJsonObject(text: string): unknown {
  const raw = extractJsonText(text)
  try {
    return JSON.parse(raw) as unknown
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error)
    const repaired = repairJsonText(raw)
    if (repaired !== null) {
      try {
        const value = JSON.parse(repaired) as unknown
        console.warn('[office-eval] 评测模型 JSON 有语法错误，已自动修复后继续。')
        return value
      } catch {
        // fall through to the diagnostic error below
      }
    }
    throw jsonParseError(detail, failureExcerpt(raw, detail))
  }
}

function jsonParseError(detail: string, excerpt: string): Error {
  const snippet = excerpt.length > 0 ? `\n失败位置附近输出：\n${excerpt}` : ''
  return new Error(`评测模型输出不是合法 JSON：${detail}${snippet}`)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function asString(value: unknown, label: string): string {
  if (typeof value !== 'string' || value.trim().length === 0) {
    throw new Error(`评测 JSON 缺少非空字符串字段 ${label}。`)
  }
  return value.trim()
}

function asStringArray(value: unknown, label: string): string[] {
  if (!Array.isArray(value)) throw new Error(`评测 JSON 字段 ${label} 必须是字符串数组。`)
  return value.map((item, index) => asString(item, `${label}[${String(index)}]`))
}

/** Validate and freeze the generated rubric. */
function parseRubric(text: string): Rubric {
  const value = parseJsonObject(text)
  if (!isRecord(value)) throw new Error('评测蓝图根值必须是对象。')
  if (!Array.isArray(value.questions) || value.questions.length === 0) {
    throw new Error('评测蓝图必须包含至少一道选择题。')
  }
  if (!Array.isArray(value.factCheckpoints) || value.factCheckpoints.length === 0) {
    throw new Error('评测蓝图必须包含至少一个事实检查点。')
  }
  const questions = value.questions.map((item, index) => {
    if (!isRecord(item)) throw new Error(`questions[${String(index)}] 必须是对象。`)
    if (!isRecord(item.options) || Object.keys(item.options).length < 2) {
      throw new Error(`questions[${String(index)}].options 至少需要两个选项。`)
    }
    const options: Record<string, string> = {}
    for (const [key, option] of Object.entries(item.options)) {
      options[key] = asString(option, `questions[${String(index)}].options.${key}`)
    }
    return {
      id: asString(item.id, `questions[${String(index)}].id`),
      prompt: asString(item.prompt, `questions[${String(index)}].prompt`),
      options,
    }
  })
  const factCheckpoints = value.factCheckpoints.map((item, index) => {
    if (!isRecord(item)) throw new Error(`factCheckpoints[${String(index)}] 必须是对象。`)
    return {
      id: asString(item.id, `factCheckpoints[${String(index)}].id`),
      description: asString(item.description, `factCheckpoints[${String(index)}].description`),
    }
  })
  return { questions, factCheckpoints }
}

/** Validate a scorecard against the frozen rubric ids. */
function parseScorecard(text: string, rubric: Rubric): Scorecard {
  const value = parseJsonObject(text)
  if (!isRecord(value)) throw new Error('评测结果根值必须是对象。')
  const questionIds = new Set(rubric.questions.map(question => question.id))
  const checkpointIds = new Set(rubric.factCheckpoints.map(checkpoint => checkpoint.id))
  if (!Array.isArray(value.questions) || value.questions.length !== rubric.questions.length) {
    throw new Error('评测结果的选择题数量必须与蓝图一致。')
  }
  if (!Array.isArray(value.factCheckpoints) || value.factCheckpoints.length !== rubric.factCheckpoints.length) {
    throw new Error('评测结果的事实检查点数量必须与蓝图一致。')
  }
  const questions = value.questions.map((item, index) => {
    if (!isRecord(item)) throw new Error(`questions[${String(index)}] 必须是对象。`)
    const id = asString(item.id, `questions[${String(index)}].id`)
    if (!questionIds.has(id)) throw new Error(`评测结果出现未知题号 ${id}。`)
    const spec = rubric.questions.find(question => question.id === id)
    const choice = asString(item.choice, `questions[${String(index)}].choice`)
    if (spec !== undefined && spec.options[choice] === undefined) {
      throw new Error(`题 ${id} 的选项 ${choice} 不在蓝图中。`)
    }
    return {
      id,
      choice,
      rationale: asString(item.rationale, `questions[${String(index)}].rationale`),
      evidence: asString(item.evidence, `questions[${String(index)}].evidence`),
    }
  })
  const factCheckpoints = value.factCheckpoints.map((item, index) => {
    if (!isRecord(item)) throw new Error(`factCheckpoints[${String(index)}] 必须是对象。`)
    const id = asString(item.id, `factCheckpoints[${String(index)}].id`)
    if (!checkpointIds.has(id)) throw new Error(`评测结果出现未知抽查编号 ${id}。`)
    const choice = asString(item.choice, `factCheckpoints[${String(index)}].choice`).toUpperCase()
    if (!FACT_CHOICES.has(choice)) {
      throw new Error(`抽查 ${id} 的选项必须是 A–G，收到 ${choice}。`)
    }
    return {
      id,
      excerpt: asString(item.excerpt, `factCheckpoints[${String(index)}].excerpt`),
      choice,
      verification: asString(item.verification, `factCheckpoints[${String(index)}].verification`),
      rationale: asString(item.rationale, `factCheckpoints[${String(index)}].rationale`),
    }
  })
  return {
    resultCheck: layerValue(value.resultCheck, 'resultCheck'),
    fileCheck: layerValue(value.fileCheck, 'fileCheck'),
    visualCheck: layerValue(value.visualCheck, 'visualCheck'),
    needsHumanReview: value.needsHumanReview === true,
    oneLiner: asString(value.oneLiner, 'oneLiner'),
    questions,
    factCheckpoints,
    strengths: asStringArray(value.strengths, 'strengths'),
    issues: asStringArray(value.issues, 'issues'),
    unverifiable: Array.isArray(value.unverifiable) ? asStringArray(value.unverifiable, 'unverifiable') : [],
  }
}

function layerValue(value: unknown, label: string): string {
  const text = asString(value, label)
  if (!LAYER_VALUES.has(text)) throw new Error(`${label} 必须是分层核验枚举值，收到 ${text}。`)
  return text
}

function clip(text: string, maxChars: number): string {
  if (text.length <= maxChars) return text
  return `${text.slice(0, maxChars)}\n\n[回答已截断，共 ${String(text.length)} 字符]`
}

/** Render the evaluation markdown that the user sees. */
function formatReport(turn: number, task: string, rubric: Rubric, score: Scorecard): string {
  const questionRows = score.questions.map((item) => {
    const spec = rubric.questions.find(question => question.id === item.id)
    const option = spec?.options[item.choice] ?? ''
    return `| ${item.id} | ${item.choice} | ${escapeCell(item.rationale)} | ${escapeCell(item.evidence)} | ${escapeCell(option)} |`
  })
  const factRows = score.factCheckpoints.map((item) => {
    const spec = rubric.factCheckpoints.find(checkpoint => checkpoint.id === item.id)
    return `| ${item.id} | ${escapeCell(spec?.description ?? '')} | ${escapeCell(item.excerpt)} | ${item.choice} | ${escapeCell(item.verification)} | ${escapeCell(item.rationale)} |`
  })
  const portrait = factPortrait(score.factCheckpoints)
  const questionBank = rubric.questions.map((question) => {
    const options = Object.entries(question.options)
      .map(([key, text]) => `  - **${key}**：${text}`)
      .join('\n')
    return `### ${question.id}. ${question.prompt}\n${options}`
  }).join('\n\n')
  const checkpointBank = rubric.factCheckpoints
    .map(checkpoint => `- **${checkpoint.id}**：${checkpoint.description}`)
    .join('\n')
  return [
    `# 办公智能体评测结果（第 ${String(turn)} 轮）`,
    '',
    '本评价不给出总分、最终等级、淘汰结论或候选排名。',
    '',
    '## 评价摘要',
    `- 隔离方式：同会话顺序评价（仅一份回答）`,
    `- 做题式结果核验：${score.resultCheck}`,
    `- 文件与功能核验：${score.fileCheck}`,
    `- 视觉可用性核验：${score.visualCheck}`,
    `- 需要人工复核：${score.needsHumanReview ? '是' : '否'}`,
    `- 一句话概括：${score.oneLiner}`,
    `- 固定抽查画像：${portrait}`,
    '',
    '## 用户任务',
    task,
    '',
    '## 选择题结果',
    '| 编号 | 选择 | 简要依据 | 来源或实际操作证据 | 选项口径 |',
    '|---|---|---|---|---|',
    ...questionRows,
    '',
    '## 固定抽查',
    '| 编号 | 抽查点 | 候选原文或文件位置 | 选择 | 核验来源或操作 | 理由 |',
    '|---|---|---|---|---|---|',
    ...factRows,
    '',
    '## 三个主要优点',
    ...numbered(score.strengths),
    '',
    '## 三个主要问题',
    ...numbered(score.issues),
    '',
    '## 无法核验及人工复核事项',
    ...(score.unverifiable.length > 0 ? score.unverifiable.map(item => `- ${item}`) : ['- 无']),
    '',
    '## 本题蓝图（答题前固定）',
    questionBank,
    '',
    '### 事实检查点',
    checkpointBank,
    '',
  ].join('\n')
}

function factPortrait(checkpoints: readonly ScoredCheckpoint[]): string {
  const counts = new Map<string, number>()
  for (const item of checkpoints) {
    counts.set(item.choice, (counts.get(item.choice) ?? 0) + 1)
  }
  return [...FACT_CHOICES]
    .filter(choice => (counts.get(choice) ?? 0) > 0)
    .map(choice => `${choice}×${String(counts.get(choice) ?? 0)}`)
    .join('、')
}

function numbered(items: readonly string[]): string[] {
  if (items.length === 0) return ['1. （无）']
  return items.map((item, index) => `${String(index + 1)}. ${item}`)
}

function escapeCell(text: string): string {
  return text.replaceAll('|', '\\|').replaceAll('\n', ' ')
}

/** Write the markdown report under the session cwd. */
async function writeReport(
  agent: Agent,
  outputDirName: string,
  turn: number,
  markdown: string,
): Promise<string> {
  const cwd = agent.session.header.cwd
  const root = cwd !== undefined && isAbsolute(cwd) ? cwd : process.cwd()
  const directory = join(root, outputDirName)
  await mkdir(directory, { recursive: true })
  const safeId = String(agent.session.id).replaceAll(/[^a-zA-Z0-9._-]+/gu, '_')
  const path = join(directory, `${safeId}-turn${String(turn)}.md`)
  await writeFile(path, markdown, 'utf8')
  return path
}
