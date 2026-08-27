/**
 * boss_send_to operation（设计 §10.6）：沟通页搜索找人并发送消息（写动作）。
 *
 * 流程：搜索姓名进入对话（ChatSearchExecutor.openContact）→ 输入并发送消息（ChatSendExecutor.sendMessage）。
 * 默认真发送；dry_run=true 只输入不点发送（测试链路）。不在沟通页时自动先跳转。
 * 前置校验：to/message 非空（在 connect Chrome 前完成）。
 */
import { ChatSearchExecutor } from '../boss/ChatSearchExecutor.js'
import { ChatSendExecutor } from '../boss/ChatSendExecutor.js'
import {
  defaultSessionFactory,
  ensureChatPage,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import type { BossOperation, OperationResult, OpContext } from './types.js'

export interface BossSendToArgs {
  /** 搜索的联系人姓名 */
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
          // 1. 搜索找人并进入对话（clearInput 注入：输入未落地时清空重试一次，2026-08-27 聚焦竞态修复）
          const searcher = new ChatSearchExecutor({
            snapshot: session.snapshot,
            click: session.click,
            clickAndType: session.clickAndType,
            clearInput: session.clearInput,
            pressEscape: session.pressEscape,
            signal: ctx.signal,
          })
          ctx.progress({ stage: 'execute', message: `搜索联系人「${to}」并进入对话` })
          await searcher.openContact({ name: to })
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
            data: { to, sent: r.sent, dry_run: dryRun },
            effect: r.sent ? ('applied' as const) : ('none' as const),
          }
        },
      )
    },
  }
}
