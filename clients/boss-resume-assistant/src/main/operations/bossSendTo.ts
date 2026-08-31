/**
 * boss_send_to operation（设计 §10.6）：向指定联系人发送消息（写动作）。
 *
 * 流程：统一会话切换（ChatOpenExecutor.open：already 零点击 / 搜索找人 / 会话列表兜底，
 * 头部身份校验防串会话——2026-08-31 起 send-to 与 open-chat 共用同一条「找人会话」链路，
 * ChatSearchExecutor 只作为 ChatOpenExecutor 的内部搜索原语，不再被 operation 直接驱动）
 * → 输入并发送消息（ChatSendExecutor.sendMessage）。
 * 默认真发送；dry_run=true 只输入不点发送（测试链路）。不在沟通页时自动先跳转。
 * 前置校验：to/message 非空（在 connect Chrome 前完成）。
 */
import { ChatOpenExecutor } from '../boss/ChatOpenExecutor.js'
import { ChatSendExecutor } from '../boss/ChatSendExecutor.js'
import {
  defaultSessionFactory,
  ensureChatPage,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import type { BossOperation, OperationResult, OpContext } from './types.js'

export interface BossSendToArgs {
  /** 目标联系人姓名 */
  to: string
  /** 发送的消息内容 */
  message: string
  /** 只输入不点发送（测试链路），默认 false 真发送 */
  dry_run?: boolean
}

export function createBossSendToOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<BossSendToArgs> {
  return {
    name: 'boss_send_to',
    execute(args: BossSendToArgs, ctx: OpContext): Promise<OperationResult> {
      const to = args?.to ?? ''
      const message = args?.message ?? ''
      const dryRun = args?.dry_run === true
      return runBossOperation(
        'write',
        ctx,
        sessionFactory,
        () => {
          if (!to.trim()) return 'to（联系人姓名）不能为空'
          if (!message.trim()) return 'message（消息内容）不能为空'
          return null
        },
        async (session, tracker) => {
          await ensureChatPage(session, ctx)
          // 1. 统一会话切换（already 零点击 / 搜索找人 / 列表兜底 + 头部身份校验；
          //    clearInput/pressEscape 注入透传：输入未落地清空重试、浮层清场）
          const opener = new ChatOpenExecutor({
            snapshot: session.snapshot,
            click: session.click,
            clickAndType: session.clickAndType,
            mouseWheel: session.mouseWheel,
            pressEscape: session.pressEscape,
            clearInput: session.clearInput,
            signal: ctx.signal,
          })
          ctx.progress({ stage: 'execute', message: `打开「${to}」的会话（已在目标会话则零点击）` })
          const opened = await opener.open({ contact: to })
          // 2. 输入并发送消息
          const sender = new ChatSendExecutor({
            snapshot: session.snapshot,
            click: session.click,
            clickAndType: session.clickAndType,
            signal: ctx.signal,
          })
          ctx.progress({
            stage: 'execute',
            message: dryRun ? '输入消息（dry-run 不发送）' : '输入并发送消息',
          })
          const r = await sender.sendMessage({ message, dryRun })
          tracker.completed = r.sent ? 1 : 0
          return {
            message: dryRun ? `完成：已向「${to}」输入消息（dry-run 未发送）` : `完成：已向「${to}」发送消息`,
            data: { to, via: opened.via, sent: r.sent, dry_run: dryRun },
            effect: r.sent ? ('applied' as const) : ('none' as const),
          }
        },
      )
    },
  }
}
