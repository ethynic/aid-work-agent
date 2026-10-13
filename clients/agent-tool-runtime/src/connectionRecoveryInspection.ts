import { type RuntimeHost } from '@aid/local-tool-host-core'
import { diagnosis, diagnoseConnection, identityDigest, type ConnectionDiagnosis } from './connectionDiagnosis.js'
import { checkConnectionRecoveryResources, registerConnectionRecoveryReboot } from './connectionRecoveryResources.js'

export type RecoveryAction = 'diagnose' | 'prepare_preserve' | 'prepare_new' | 'prepare_reboot'
export interface RecoveryInspection { diagnosis: ConnectionDiagnosis; resources_safe: boolean; identity_digest: string | null }
export async function inspectConnectionRecovery(host: RuntimeHost, home: string, node: string, action: RecoveryAction): Promise<RecoveryInspection> {
  return host.inspectConnectionRecovery(async owns => {
    if (owns) {
      if (action === 'prepare_reboot') {
        const resource = await registerConnectionRecoveryReboot(home, node)
        return { diagnosis: diagnosis(resource.category, resource.needs_reboot ? '重启恢复已登记，当前任务记录保留' : '重启恢复无法登记',
          resource.needs_reboot ? ['正常重启Windows，重新打开Runtime。', '点击检查连接后继续修复或重新配置；不需要提交日志。'] : ['使用原Windows用户，修复系统凭据保护和目录权限后重新登记。'], { requires_reboot: resource.needs_reboot }), resources_safe: false, identity_digest: null }
      }
      return { diagnosis: diagnosis('EXECUTION_ACTIVE', '当前实例仍拥有执行资源', ['先点击停止执行，等待任务收尾后检查连接。', '停止仍失败时选择登记重启恢复，正常重启Windows后继续。'], { can_reconfigure: true }), resources_safe: false, identity_digest: null }
    }
    let result = await diagnoseConnection(home)
    const state = host.getState()
    if (state.state === 'blocked' && result.service_verified) {
      const reason = typeof state.blocked_reason === 'string' ? state.blocked_reason : ''
      const managementFailure = reason.includes('管理') || reason.includes('旧执行')
      if (!managementFailure) result = diagnosis('LOCAL_RUNTIME_STATE', '服务器验证通过，但本机插件或恢复资料仍异常', ['选择重新配置，原资料归档保留。', '连接恢复后从交付ZIP安装BOSS插件；旧任务仍归属原设备。'], { service_verified: true, can_reconfigure: true })
    }
    // Looking up connection instructions must not scan/decrypt historical Job
    // ledgers or hold the management process while probing execution resources.
    // The explicit prepare action and Main's second check still prove stop.
    if (action === 'diagnose' || !result.can_reconfigure) return { diagnosis: result, resources_safe: false, identity_digest: result.can_preserve ? identityDigest(home) : null }
    const resource = action === 'prepare_reboot' ? await registerConnectionRecoveryReboot(home, node)
      : await checkConnectionRecoveryResources(home, node, { prepareReboot: true })
    if (!resource.safe) {
      result = { ...result, requires_reboot: resource.needs_reboot,
        steps: [...result.steps, resource.needs_reboot ? '需要确认旧执行资源停止：登记重启恢复后正常重启Windows，再继续。' : '请先正常退出旧Runtime实例；系统或目录异常请管理员修复后再检查。'] }
      result = diagnosis(resource.category, resource.needs_reboot ? (resource.category === 'CONNECTION_RESOURCE_REBOOT_PREPARED' ? '恢复已登记，需要一次正常Windows重启' : '旧执行资源尚需登记重启恢复') : '当前执行资源尚不能安全恢复',
        resource.needs_reboot ? [resource.category === 'CONNECTION_RESOURCE_REBOOT_PREPARED' ? '正常重启Windows后重新打开本客户端。' : '点击登记重启恢复，登记成功后正常重启Windows；资料变化后需要重新登记。', '点击检查连接后继续原设备修复或重新配置。'] : ['正常停止其他Runtime/旧CLI实例。', '使用原Windows用户，请管理员修复系统PowerShell、凭据保护、安装文件及配置目录权限后重新检查。'], { requires_reboot: resource.needs_reboot, can_reconfigure: true })
    }
    if (action === 'prepare_preserve' && !result.can_preserve && resource.safe) result = { ...result, steps: [...result.steps, '原身份尚不能保留，请选择重新配置连接。'] }
    return { diagnosis: result, resources_safe: resource.safe, identity_digest: result.can_preserve ? identityDigest(home) : null }
  }, action !== 'diagnose', action === 'prepare_reboot')
}
