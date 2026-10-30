/** Host-only pure observation; no claim that a readable page proves an old action stopped. */
import type { ProcessSupervision } from './processSupervision.js'
import type { ProviderSet } from './providerManager.js'

export function recoveryObservation(providers: ProviderSet, supervision?: ProcessSupervision) {
  return {
    async callTool(name: string, args: Record<string, unknown>, options: Parameters<ReturnType<ProviderSet['get']>['callTool']>[2]) {
      if (name !== 'runtime_recovery_observe' || ![2, 3].includes(Number(args.version))) throw new Error('LOCAL_VERIFICATION_VERSION_UNSUPPORTED')
      const provider = args.provider_key ?? 'boss-recruiting'
      if (!['boss-recruiting', 'wecom'].includes(String(provider))) throw new Error('LOCAL_VERIFICATION_PROVIDER_UNSUPPORTED')
      // Legacy v1 calls have no durable invocation -> Host generation binding.
      // Keep stop unknown, but do not prevent an independent CDP read.
      let stop: Record<string, unknown> = { resource_stopped: false, stop_error_code: 'LOCAL_RECOVERY_STOP_UNPROVED' }
      if (args.version === 3 && args.stop_resources === true) {
        try {
          if (!supervision) throw new Error('LOCAL_RECOVERY_SUPERVISION_UNAVAILABLE')
          const proof = await providers.recoverIdlePool(() => supervision.stopManagedResources())
          stop = { resource_stopped: true, ...proof }
        } catch (error) {
          const code = error instanceof Error && /^LOCAL_RECOVERY_[A-Z_]+$/.test(error.message) ? error.message : 'LOCAL_RECOVERY_STOP_UNPROVED'
          stop = { resource_stopped: false, stop_error_code: code }
        }
      }
      const observation = { version: args.version, scope_id: args.scope_id, device_id: args.device_id,
        original_invocation_ids: args.original_invocation_ids, ...stop }
      if (provider === 'wecom') {
        const stopped = stop.resource_stopped === true
        return { success: stopped, effect: 'none' as const,
          code: stopped ? 'OK' : String(stop.stop_error_code ?? 'LOCAL_RECOVERY_STOP_UNPROVED'),
          message: stopped ? '原受管执行资源已停止；原消息发送结果仍以对账为准' : '原受管执行资源尚未确认停止',
          data: { recovery_observation: { ...observation, provider_scope: 'wecom', target_relation: 'not_applicable',
            browser_target_ref: null, page_summary: { observed: false, page_kind: 'not_applicable', result: 'unknown' } } } }
      }
      if (args.version === 3 && args.page_target_unavailable === true) return pageFailed('WRONG_PAGE')
      try {
        const page = await providers.get('boss-recruiting').callTool('boss_recovery_probe', {
          ...(typeof args.browser_target_ref === 'string' ? { _boss_target_ref: args.browser_target_ref } : {}),
          ...(args.expectation ? { expectation: args.expectation } : {}),
        }, options)
        if (page.success === true && page.effect === 'none' && page.data && typeof page.data === 'object') {
          return { ...page, success: true, effect: 'none' as const, code: 'OK', data: { recovery_observation: { ...observation,
            ...Object.fromEntries(Object.entries(page.data).filter(([key]) => ['target_relation', 'browser_target_ref', 'page_summary'].includes(key))),
          } } }
        }
        const code = ['WRONG_PAGE', 'CHROME_UNAVAILABLE', 'UI_CHANGED'].includes(String(page.code)) ? String(page.code) : 'LOCAL_PAGE_OBSERVATION_FAILED'
        return pageFailed(code)
      } catch { return pageFailed('LOCAL_PAGE_OBSERVATION_FAILED') }
      function pageFailed(code: string) {
        return { success: false, code, message: code, effect: 'none' as const, retryable: true,
          data: { recovery_observation: { ...observation, target_relation: args.page_target_unavailable ? 'unavailable' : args.browser_target_ref ? 'original' : 'current_unbound',
            browser_target_ref: args.browser_target_ref ?? null, page_summary: { observed: false, page_kind: 'unavailable', result: 'unknown' }, page_error_code: code } } }
      }
    },
  }
}
