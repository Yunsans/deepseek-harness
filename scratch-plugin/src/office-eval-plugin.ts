/**
 * Office-agent evaluation plugin: optional slash command plus post-turn auto eval.
 *
 * `/office-eval auto` arms the next completed human turns. After the agent
 * finishes, the plugin generates a frozen rubric from the user task, scores the
 * answer with the same base model, writes a markdown report, and appends a
 * notice. `/office-eval` scores the latest completed turn on demand.
 */

import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import type { CommandInvocation, CommandResult } from '@deepseek-ai/dsh-commands'
import type { Session } from '@deepseek-ai/dsh-session'
import z from '@deepseek-ai/schemastery'
import {
  extractTurn,
  publishEvalNotice,
  runOfficeEval,
  type OfficeEvalConfig,
} from './office-eval-pipeline.ts'

export const name = 'office-eval'
export const inject = ['commands', 'llm', 'agents']

export interface Config {
  auto?: boolean
  outputDirName?: string
  maxOutputTokens?: number
  maxAnswerChars?: number
  jsonRetries?: number
  questionCount?: number
  checkpointCount?: number
}

export const Config: z<Config> = z.object({
  auto: z.boolean().default(false),
  outputDirName: z.string().default('office-eval-results'),
  maxOutputTokens: z.number().step(1).min(256).default(8192),
  maxAnswerChars: z.number().step(1).min(1000).default(80000),
  jsonRetries: z.number().step(1).min(0).max(5).default(2),
  questionCount: z.number().step(1).min(4).max(20).default(10),
  checkpointCount: z.number().step(1).min(3).max(20).default(8),
})

const USAGE = '用法：/office-eval  |  /office-eval auto  |  /office-eval off  |  /office-eval status'

/** Fail loudly if a locally closed union gains an unhandled member. */
function assertNever(value: never, label: string): never {
  throw new TypeError(`unknown ${label}: ${String(value)}`)
}

type OfficeEvalCommand =
  | { readonly kind: 'run' }
  | { readonly kind: 'auto' }
  | { readonly kind: 'off' }
  | { readonly kind: 'status' }
  | { readonly kind: 'invalid' }

/** Parse the `/office-eval` grammar; any other input is invalid. */
function parseCommand(rawInput: string): OfficeEvalCommand {
  const input = rawInput.trim().toLowerCase()
  if (input.length === 0) return { kind: 'run' }
  if (input === 'auto') return { kind: 'auto' }
  if (input === 'off') return { kind: 'off' }
  if (input === 'status') return { kind: 'status' }
  return { kind: 'invalid' }
}

/**
 * Register the command and the post-turn auto-eval listeners.
 * @param ctx - Cordis context with commands, llm, and agents.
 * @param config - loader-validated configuration, defaults already applied.
 */
export function apply(ctx: Context, config: Config): void {
  const resolved: OfficeEvalConfig = {
    auto: config.auto === true,
    outputDirName: config.outputDirName ?? 'office-eval-results',
    maxOutputTokens: config.maxOutputTokens ?? 8192,
    maxAnswerChars: config.maxAnswerChars ?? 80000,
    jsonRetries: config.jsonRetries ?? 2,
    questionCount: config.questionCount ?? 10,
    checkpointCount: config.checkpointCount ?? 8,
  }
  const autoBySession = new Map<string, boolean>()
  const pendingTurns = new Map<string, number>()
  const evaluating = new Set<string>()

  const isAuto = (sessionId: string): boolean => autoBySession.get(sessionId) ?? resolved.auto

  ctx.commands.register({
    name: 'office-eval',
    description: '评测上一轮办公智能体回答，或在之后的任务中自动评测',
    input: { hint: '[auto|off|status]' },
    handler: invocation => executeCommand(ctx, resolved, autoBySession, invocation),
  })

  ctx.on('session/event', (session: Session, event) => {
    if (event.type !== 'turn/end') return
    if (!isAuto(String(session.id))) return
    if (event.data.reason.kind !== 'completed' && event.data.reason.kind !== 'max-tokens') return
    if (extractTurn(session, event.data.turn) === undefined) return
    pendingTurns.set(String(session.id), event.data.turn)
  })

  ctx.on('agent/status', ({ agent, status }: { agent: Agent; status: string }) => {
    if (status !== 'idle') return
    const turn = pendingTurns.get(String(agent.id))
    if (turn === undefined) return
    pendingTurns.delete(String(agent.id))
    void runAutoEval(ctx, resolved, evaluating, agent, turn)
  })

}

/** Execute one `/office-eval` invocation against the receiving agent. */
async function executeCommand(
  ctx: Context,
  config: OfficeEvalConfig,
  autoBySession: Map<string, boolean>,
  invocation: CommandInvocation,
): Promise<CommandResult> {
  const command = parseCommand(invocation.rawInput)
  const sessionKey = String(invocation.agent.id)
  switch (command.kind) {
    case 'invalid':
      return { kind: 'error', text: USAGE }
    case 'auto':
      autoBySession.set(sessionKey, true)
      return {
        kind: 'success',
        text: '已开启自动评测。请输入任务；智能体回答完成后将生成题目、事实检查点并给出逐项结果。',
      }
    case 'off':
      autoBySession.set(sessionKey, false)
      return { kind: 'success', text: '已关闭本会话的自动评测。仍可用 /office-eval 手动评测上一轮。' }
    case 'status': {
      const armed = autoBySession.get(sessionKey) ?? config.auto
      return {
        kind: 'success',
        text: armed
          ? '自动评测：开。下一轮用户任务在智能体完成后会自动评测。'
          : '自动评测：关。发送 /office-eval auto 开启，或 /office-eval 评测上一轮。',
      }
    }
    case 'run':
      try {
        await invocation.agent.whenIdle()
        const report = await runOfficeEval(ctx, invocation.agent, config, undefined, invocation.signal)
        publishEvalNotice(invocation.agent, report)
        return {
          kind: 'success',
          text: `${report.reportMarkdown}\n\n报告文件：${report.reportPath}`,
        }
      } catch (error) {
        if (invocation.signal.aborted) return { kind: 'error', text: '评测已取消。' }
        return { kind: 'error', text: error instanceof Error ? error.message : String(error) }
      }
    default:
      return assertNever(command, 'office-eval command')
  }
}

/** Score a completed turn after the agent returns to idle. */
async function runAutoEval(
  ctx: Context,
  config: OfficeEvalConfig,
  evaluating: Set<string>,
  agent: Agent,
  turn: number,
): Promise<void> {
  const key = String(agent.id)
  if (evaluating.has(key)) return
  evaluating.add(key)
  try {
    await agent.runMaintenance(async (signal) => {
      const report = await runOfficeEval(ctx, agent, config, turn, signal)
      publishEvalNotice(agent, report)
    })
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    console.error(`[office-eval] auto eval failed: ${message}`)
    try {
      publishEvalNotice(agent, {
        reportMarkdown: `办公智能体评测失败：${message}`,
        reportPath: '',
        summaryLine: boundFailureSummary(message),
      })
    } catch (noticeError) {
      const detail = noticeError instanceof Error ? noticeError.message : String(noticeError)
      console.error(`[office-eval] failed to publish eval notice: ${detail}`)
    }
  } finally {
    evaluating.delete(key)
  }
}

/** One-line notice summary that stays within the context-summary bound. */
function boundFailureSummary(message: string): string {
  const prefix = '办公智能体评测失败：'
  const budget = 120 - prefix.length
  if (message.length <= budget) return `${prefix}${message}`
  return `${prefix}${message.slice(0, Math.max(0, budget - 1))}…`
}
