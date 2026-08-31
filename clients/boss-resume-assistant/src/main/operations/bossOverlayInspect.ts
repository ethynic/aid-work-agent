/**
 * boss_overlay_inspect operation（弹层自愈原语，2026-08-31）：只读导出主文档文本节点清单。
 *
 * 供云端自愈编排（本地工具失败 → 弹层识别 → 关闭 → 重试）使用，也可人工排障调用：
 * 工具失败往往不是页面改版而是不可预见的弹层（广告/功能引导）遮挡，本原语把
 * 「页面长什么样」结构化导出，判断在云端做（启发式优先，LLM 兜底）。
 * 纯只读单次快照：不点击、不输入、不滚屏；无 URL 前置（弹层可出现在任意页面）。
 */
import { collectOverlayCandidates } from '../boss/OverlayInspector.js'
import {
  defaultSessionFactory,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import type { BossOperation, OperationResult, OpContext } from './types.js'

export interface BossOverlayInspectArgs {
  /** 预留参数位，当前无入参 */
  _unused?: never
}

export function createBossOverlayInspectOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<BossOverlayInspectArgs> {
  return {
    name: 'boss_overlay_inspect',
    execute(_args: BossOverlayInspectArgs, ctx: OpContext): Promise<OperationResult> {
      return runBossOperation(
        'readonly',
        ctx,
        sessionFactory,
        () => null,
        async (session) => {
          const snapshot = await session.snapshot()
          const inspection = collectOverlayCandidates(snapshot)
          ctx.progress({
            stage: 'execute',
            message: `已导出页面文本节点 ${inspection.candidates.length} 条（视口 ${inspection.viewport.width}x${inspection.viewport.height}）`,
          })
          return {
            message: `已导出 ${inspection.candidates.length} 条文本节点 + ${inspection.icon_candidates.length} 个 icon 关闭控件候选`,
            data: {
              viewport: inspection.viewport,
              candidates: inspection.candidates,
              icon_candidates: inspection.icon_candidates,
              dismiss_whitelist_hint: true, // 提示上层：关闭控件必须命中白名单语义
            },
            effect: 'none',
          }
        },
      )
    },
  }
}
