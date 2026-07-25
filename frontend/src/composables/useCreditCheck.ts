/**
 * 积分余额检查 composable（#37 Phase 4 余额报警）
 *
 * 提供 `checkCreditBeforeAction(action)` 在关键动作前检查余额：
 * - 余额 ≤ 0：toast.error 阻断动作（sendMessage / newSession 均阻断）
 * - 0 < 余额 ≤ LOW_CREDIT_THRESHOLD：toast.warning 提醒，当天只提醒一次（localStorage 去重）
 * - 余额 > 阈值：放行
 *
 * 三入口调用：
 * 1. 登录成功后（useTenantAuth.setLogin）：action='login'，仅提醒不阻断
 * 2. 新会话创建（useSession.createNewSession）：action='newSession'，余额 ≤ 0 阻断创建
 * 3. 发送消息前（useAgent.sendMessage）：action='sendMessage'，余额 ≤ 0 阻断
 */

import { useToast } from 'vue-toastification'
import { getTenantBalance, type BalanceInfo } from '@/api/billing'
import { useTenantAuth } from './useTenantAuth'

// 低余额提醒阈值
const LOW_CREDIT_THRESHOLD = 100

// 当天提醒去重 key 前缀，完整 key: `credit_low_warn_{userId}_{YYYY-MM-DD}`
const LOW_WARN_KEY_PREFIX = 'credit_low_warn_'

export type CreditCheckAction = 'login' | 'newSession' | 'sendMessage'

export interface CreditCheckResult {
  /** 是否允许动作继续执行（true=放行，false=阻断） */
  allowed: boolean
  /** 余额信息（获取失败时为 null） */
  balance: BalanceInfo | null
  /** 阻断/提醒的原因，便于调试 */
  reason?: string
}

function getTodayKey(userId: string): string {
  const now = new Date()
  const ymd = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
  return `${LOW_WARN_KEY_PREFIX}${userId}_${ymd}`
}

function hasWarnedToday(userId: string): boolean {
  try {
    return localStorage.getItem(getTodayKey(userId)) === '1'
  } catch {
    return false
  }
}

function markWarnedToday(userId: string): void {
  try {
    localStorage.setItem(getTodayKey(userId), '1')
  } catch {
    // localStorage 不可用时静默失败，不影响主流程
  }
}

export function useCreditCheck() {
  const toast = useToast()
  const { admin } = useTenantAuth()

  /**
   * 在关键动作前检查积分余额
   *
   * 行为约定：
   * - 余额 ≤ 0：
   *   - sendMessage / newSession：toast.error 阻断，返回 { allowed: false }
   *   - login：toast.error 提醒，返回 { allowed: true }（不阻断登录本身）
   * - 0 < 余额 ≤ 100：toast.warning 提醒（当天仅一次），返回 { allowed: true }
   * - 余额 > 100：静默放行
   * - 余额获取失败：静默放行（不阻塞用户），返回 { allowed: true, balance: null }
   *
   * @param action 触发场景，决定余额 ≤ 0 时是否真正阻断
   */
  async function checkCreditBeforeAction(action: CreditCheckAction): Promise<CreditCheckResult> {
    // 平台管理员也参与余额检查（在 /t/{tenant_id} 路径下代管理租户时需要报警）
    // 若 platform_admin 未带 X-Tenant-Id（在 /portal 路径下），后端返回 success=false，前端静默放行

    let balance: BalanceInfo | null = null
    try {
      const res = await getTenantBalance()
      if (res.success && res.balance) {
        balance = res.balance
      } else {
        // 余额接口失败：不阻塞用户主流程
        return { allowed: true, balance: null, reason: 'balance_api_failed' }
      }
    } catch (e: any) {
      console.warn('[useCreditCheck] 获取余额失败:', e)
      return { allowed: true, balance: null, reason: 'balance_api_error' }
    }

    const creditBalance = balance.credit_balance ?? 0
    const userId = admin.value?.user_id || 'anonymous'

    // 余额耗尽：sendMessage / newSession 阻断，login 仅提醒
    if (creditBalance <= 0) {
      const blockMsg = '积分余额已耗尽，无法继续对话，请联系管理员充值'
      if (action === 'sendMessage' || action === 'newSession') {
        toast.error(blockMsg)
        return { allowed: false, balance, reason: 'no_credit_blocked' }
      }
      // login 仅提醒，不阻断（避免用户连登录都进不去）
      toast.error(blockMsg)
      return { allowed: true, balance, reason: 'no_credit_warned' }
    }

    // 低余额提醒：当天去重
    if (creditBalance <= LOW_CREDIT_THRESHOLD) {
      if (!hasWarnedToday(userId)) {
        toast.success(`积分余额即将耗尽（剩余 ${creditBalance} 积分），请尽快联系管理员充值`)
        markWarnedToday(userId)
      }
      return { allowed: true, balance, reason: 'low_credit_warned' }
    }

    return { allowed: true, balance, reason: 'ok' }
  }

  return {
    checkCreditBeforeAction,
    LOW_CREDIT_THRESHOLD,
  }
}
