/** Strict CDP read; no Chrome launch, focus, visual recovery, clicks or scrolling. */
import { randomUUID } from 'node:crypto'
import { CdpGateway } from '../cdp/CdpGateway.js'
import { resolveBrowserTarget, rememberBrowserTarget } from '../boss/browserTargetBinding.js'
import { FilterSetter, FILTER_SNAPSHOT_OPTIONS } from '../boss/FilterSetter.js'
import { isBossPage } from '../boss/PageNavigator.js'
import type { DomSnapshot } from '../boss/domSnapshot.js'
import { failResult, okResult, type BossOperation } from './types.js'

export type RecoveryExpectation = { kind: 'navigation'; target: 'recommend' | 'chat' }
  | { kind: 'clear_filter' } | { kind: 'filter'; count?: number }

function validExpectation(value: unknown): value is RecoveryExpectation {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const v = value as Record<string, unknown>
  if (v.kind === 'navigation') return Object.keys(v).length === 2 && ['recommend', 'chat'].includes(String(v.target))
  if (v.kind === 'clear_filter') return Object.keys(v).length === 1
  return v.kind === 'filter' && Object.keys(v).every(key => ['kind', 'count'].includes(key))
    && (v.count === undefined || Number.isInteger(v.count) && Number(v.count) >= 0)
}

export function createBossRecoveryProbeOperation(gatewayFactory = () => new CdpGateway({ timeoutMs: 10000 })): BossOperation<{ expectation?: RecoveryExpectation }> {
  return {
    name: 'boss_recovery_probe',
    async execute(args, ctx) {
      const runId = randomUUID()
      if (!args || Object.keys(args).some(key => key !== 'expectation') || args.expectation !== undefined && !validExpectation(args.expectation) || ctx.signal.aborted) return failResult(runId, 'INVALID_ARGUMENT', '核验请求无效', 'none')
      const gateway = gatewayFactory()
      try {
        const port = ctx.cdpPort ?? 9222
        // A supplied stale ref must fail; it cannot fall back to another tab.
        const original = ctx.browserTargetRef ? await resolveBrowserTarget(ctx.browserTargetRef, port) : undefined
        await gateway.connect(`http://127.0.0.1:${port}`)
        const targets = (await gateway.getTargets()).filter(target => {
          try {
            const url = new URL(target.url)
            return target.type === 'page' && url.protocol === 'https:' && !url.port && !url.username && !url.password
              && ['www.zhipin.com', 'zhipin.com'].includes(url.hostname)
          } catch { return false }
        })
        const target = original ? targets.find(item => item.targetId === original.target_id) : targets.length === 1 ? targets[0] : undefined
        if (!target) return failResult(runId, 'WRONG_PAGE', '原标签不可核对，或当前存在多个BOSS标签；未切换标签', 'none')
        await gateway.attachToBoundBossTarget(target.targetId)
        const snapshot = await gateway.captureDomSnapshot(FILTER_SNAPSHOT_OPTIONS) as DomSnapshot
        // Read the fresh target URL again: URL may change during attachment/snapshot.
        const current = (await gateway.getTargets()).find(item => item.targetId === target.targetId)
        if (!current) return failResult(runId, 'WRONG_PAGE', '原标签已关闭，未切换标签', 'none')
        const pageKind = isBossPage(current.url, 'recommend') ? 'recommend' : isBossPage(current.url, 'chat') ? 'chat' : 'other'
        const summary: Record<string, unknown> = { observed: true, page_kind: pageKind, result: 'unknown' }
        const expectation = args.expectation
        if (expectation?.kind === 'navigation') summary.result = isBossPage(current.url, expectation.target) ? 'match' : 'mismatch'
        if (pageKind === 'recommend') {
          const setter = new FilterSetter({ snapshot: async () => snapshot, click: async () => { throw new Error('pure probe cannot click') } })
          const count = expectation?.kind === 'clear_filter' ? 0 : expectation?.kind === 'filter' ? expectation.count ?? null : null
          const filter = setter.observePostcondition(snapshot, count)
          summary.filter_count = filter.observedCount
          summary.panel_open = filter.panelOpen
          if (expectation?.kind === 'clear_filter') summary.result = filter.status === 'met' ? 'match' : filter.status === 'unmet' ? 'mismatch' : 'unknown'
          if (expectation?.kind === 'filter') {
            // Equal counts do not establish equal selected options. Keep the full
            // operation unknown and expose the count check independently.
            summary.count_result = filter.status === 'met' ? 'match' : filter.status === 'unmet' ? 'mismatch' : 'unknown'
            if (filter.status === 'unmet') summary.result = 'mismatch'
          }
        } else if (expectation?.kind === 'clear_filter' || expectation?.kind === 'filter') summary.result = 'mismatch'
        if (ctx.signal.aborted) return failResult(runId, 'CANCELLED', '核验已取消', 'none')
        return okResult(runId, '已只读观察页面；未点击或重新执行原操作', 'none', {
          target_relation: original ? 'original' : 'current_unbound',
          browser_target_ref: ctx.browserTargetRef ?? await rememberBrowserTarget(target.targetId, port),
          page_summary: summary,
        })
      } catch {
        return failResult(runId, ctx.browserTargetRef ? 'WRONG_PAGE' : 'CHROME_UNAVAILABLE', '页面只读核验失败；请检查原Chrome与原标签', 'none')
      } finally { await gateway.close().catch(() => {}) }
    },
  }
}
