/**
 * boss_overlay_dismiss operation（弹层自愈原语，2026-08-31）：按白名单关闭文案点击关闭弹层。
 *
 * 安全铁律：text 必须命中关闭语义白名单（关闭/知道了/以后再说/取消/跳过/× 等），
 * executor 内再做一次校验——就算上层决策失误传了「立即领取」也会被拒绝执行，
 * 绝不产生领券/跳转/开通等非关闭副作用。点击后校验弹层消失（文本计数减少）。
 * 借用真实鼠标约 1-2 秒，期间勿动鼠标（与筛选/发送同通道防风控）。
 */
import { OverlayDismissExecutor } from '../boss/OverlayDismissExecutor.js'
import {
  defaultSessionFactory,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import type { BossOperation, OperationResult, OpContext } from './types.js'

export interface BossOverlayDismissArgs {
  /** 关闭控件的精确文本（白名单内，与快照文本 trim 全等） */
  text: string
}

export function createBossOverlayDismissOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<BossOverlayDismissArgs> {
  return {
    name: 'boss_overlay_dismiss',
    execute(args: BossOverlayDismissArgs, ctx: OpContext): Promise<OperationResult> {
      const text = args?.text ?? ''
      return runBossOperation(
        'readonly',
        ctx,
        sessionFactory,
        () => {
          if (typeof text !== 'string' || !text.trim()) {
            return 'text（关闭控件文本）不能为空'
          }
          return null
        },
        async (session) => {
          const executor = new OverlayDismissExecutor({
            snapshot: session.snapshot,
            clickBrowse: session.clickBrowse,
            click: session.click,
            signal: ctx.signal,
          })
          ctx.progress({ stage: 'execute', message: `点击关闭「${text}」弹层` })
          const r = await executor.dismiss({ text })
          return {
            message: `完成：已点击「${r.text}」关闭弹层`,
            data: { text: r.text, dismissed: r.dismissed },
            effect: 'none',
          }
        },
      )
    },
  }
}
