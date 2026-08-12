/**
 * useRpaPauseResume — 企业微信个人账号 RPA 暂停 / 恢复共享逻辑
 *
 * 封装 pause/resume API 调用 + 浏览器原生 confirm 确认 + loading 状态 + toast。
 * 供 RpaBindingPanel（平台后台）与 WecomPersonalRpaManager（租户后台）复用，
 * 避免在两个组件里重复实现相同的确认 / 错误处理逻辑。
 *
 * 使用方式：
 *   const { pausing, resuming, pauseAccount, resumeAccount, pauseConversation, resumeConversation }
 *     = useRpaPauseResume({ onSuccess: () => refresh() })
 */
import { ref } from 'vue'
import { useToast } from 'vue-toastification'
import { pause as pauseApi, resume as resumeApi } from '@/api/wecomPersonalRpa'

interface Options {
  /** 操作成功后的回调（通常是刷新列表） */
  onSuccess?: () => void | Promise<void>
  /** 自定义暂停确认文案 */
  pauseConfirmMessage?: (kind: 'account' | 'conversation', id: string) => string
}

export function useRpaPauseResume(options: Options = {}) {
  const toast = useToast()
  const pausing = ref(false)
  const resuming = ref(false)

  const defaultPauseConfirm = (_kind: 'account' | 'conversation', _id: string) =>
    '确定暂停此账号？暂停后客户端无法收发消息（可通过「恢复」回到正常）'

  async function pauseAccount(accountId: string, reason?: string): Promise<boolean> {
    if (pausing.value || resuming.value) return false
    const msg = (options.pauseConfirmMessage || defaultPauseConfirm)('account', accountId)
    if (!confirm(msg)) return false
    pausing.value = true
    try {
      await pauseApi({ scope: 'account', account_id: accountId, reason })
      toast.success('已暂停')
      if (options.onSuccess) await options.onSuccess()
      return true
    } catch (e: any) {
      toast.error(e?.message || '暂停失败')
      return false
    } finally {
      pausing.value = false
    }
  }

  async function resumeAccount(accountId: string): Promise<boolean> {
    if (pausing.value || resuming.value) return false
    resuming.value = true
    try {
      await resumeApi({ scope: 'account', account_id: accountId })
      toast.success('已恢复')
      if (options.onSuccess) await options.onSuccess()
      return true
    } catch (e: any) {
      toast.error(e?.message || '恢复失败')
      return false
    } finally {
      resuming.value = false
    }
  }

  async function pauseConversation(bindingId: string, reason?: string): Promise<boolean> {
    if (pausing.value || resuming.value) return false
    if (!confirm('确定暂停此会话？暂停后该会话不会再被自动处理')) return false
    pausing.value = true
    try {
      await pauseApi({ scope: 'conversation', conversation_id: bindingId, reason })
      toast.success('已暂停')
      if (options.onSuccess) await options.onSuccess()
      return true
    } catch (e: any) {
      toast.error(e?.message || '暂停失败')
      return false
    } finally {
      pausing.value = false
    }
  }

  async function resumeConversation(bindingId: string): Promise<boolean> {
    if (pausing.value || resuming.value) return false
    resuming.value = true
    try {
      await resumeApi({ scope: 'conversation', conversation_id: bindingId })
      toast.success('已恢复')
      if (options.onSuccess) await options.onSuccess()
      return true
    } catch (e: any) {
      toast.error(e?.message || '恢复失败')
      return false
    } finally {
      resuming.value = false
    }
  }

  return {
    pausing,
    resuming,
    pauseAccount,
    resumeAccount,
    pauseConversation,
    resumeConversation,
  }
}
