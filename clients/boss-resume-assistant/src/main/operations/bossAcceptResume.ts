/**
 * boss_accept_resume operation（设计 §10.2）：沟通页批量「同意」接收附件简历（写动作）。
 *
 * 找到左列最后一条消息为「对方想发送（加密）附件简历给您，您是否同意」的会话，
 * 逐个打开并点击底部处理条的「同意」；preview 开启时同意后点开附件预览再关闭。
 * 不在沟通页时自动先跳转。operation 层 limit 上限 100；MCP schema 用 maximum=1 收紧（设计 §14）。
 */
import { ResumeConsentExecutor } from '../boss/ResumeConsentExecutor.js'
import {
  defaultSessionFactory,
  ensureChatPage,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import type { BossOperation, OperationResult, OpContext } from './types.js'

export interface BossAcceptResumeArgs {
  /** 上限，operation 默认 20，最大 100 */
  limit?: number
  /** 同意后接着点开附件简历预览再关闭（默认开启） */
  preview?: boolean
}

/** 左列会话列表区域中心滚动点（真机校准坐标，浏览类操作走 CDP） */
const CHAT_LIST_SCROLL_POINT = { x: 550, y: 900 }

export function createBossAcceptResumeOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<BossAcceptResumeArgs> {
  return {
    name: 'boss_accept_resume',
    execute(args: BossAcceptResumeArgs, ctx: OpContext): Promise<OperationResult> {
      const limit = args?.limit ?? 20
      const preview = args?.preview ?? true
      return runBossOperation(
        'write',
        ctx,
        sessionFactory,
        () => {
          if (!Number.isInteger(limit) || limit <= 0 || limit > 100) {
            return `limit 必须是 1-100 的整数：${String(args?.limit)}`
          }
          return null
        },
        async (session, tracker) => {
          await ensureChatPage(session, ctx)
          const executor = new ResumeConsentExecutor({
            snapshot: session.snapshot,
            click: session.click,
            scroll: (deltaY) => session.mouseWheel(CHAT_LIST_SCROLL_POINT.x, CHAT_LIST_SCROLL_POINT.y, deltaY),
            pressEscape: session.pressEscape,
            signal: ctx.signal,
            onProgress: (done) => {
              tracker.completed = done
              ctx.progress({ stage: 'execute', current: done, total: limit, message: `已同意接收 ${done}/${limit} 人` })
            },
          })
          ctx.progress({ stage: 'execute', message: '逐个打开会话并点击「同意」' })
          const result = await executor.acceptAll({ limit, preview })
          return {
            message:
              `完成：同意接收 ${result.accepted} 人的附件简历` +
              (preview ? `，已预览 ${result.previewed} 份` : '') +
              `（${result.reachedEnd ? '已滚到底' : '达到上限'}）`,
            data: { accepted: result.accepted, previewed: result.previewed, reached_end: result.reachedEnd },
          }
        },
      )
    },
  }
}
