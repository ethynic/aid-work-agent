/**
 * useCreditCheck composable 单元测试
 *
 * 覆盖场景：
 * - newSession 入口：余额 ≤ 0 阻断（allowed: false）
 * - sendMessage 入口：余额 ≤ 0 阻断（allowed: false）
 * - login 入口：余额 ≤ 0 仅提醒不阻断（allowed: true）
 * - 余额 > 100：放行
 * - 余额获取失败：放行
 * - 平台管理员：跳过检查
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// 使用 vi.hoisted 确保 mock 引用在 vi.mock 工厂内可用（vi.mock 调用会被提升到文件顶部）
// 注意：vi.hoisted 内不能引用外部 import（如 ref），故用普通对象模拟 ref 的 .value 接口
const { mockGetTenantBalance, mockToast, mockAdmin } = vi.hoisted(() => ({
  mockGetTenantBalance: vi.fn(),
  mockToast: {
    error: vi.fn(),
    warning: vi.fn(),
    success: vi.fn(),
    info: vi.fn(),
  },
  // 模拟 ref<{user_id, role} | null> 的最小接口
  mockAdmin: { value: null as { user_id: string; role: string } | null },
}))

// ---- mock getTenantBalance ----
vi.mock('@/api/billing', () => ({
  getTenantBalance: mockGetTenantBalance,
}))

// ---- mock useToast ----
vi.mock('vue-toastification', () => ({
  useToast: () => mockToast,
}))

// ---- mock useTenantAuth ----
vi.mock('@/composables/useTenantAuth', () => ({
  useTenantAuth: () => ({
    admin: mockAdmin,
  }),
}))

import { useCreditCheck } from '@/composables/useCreditCheck'

describe('useCreditCheck', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockAdmin.value = null
    // 默认清除 localStorage（低余额去重）
    localStorage.clear()
  })

  it('newSession 入口：余额 ≤ 0 应阻断（allowed: false）', async () => {
    mockAdmin.value = { user_id: 'u1', role: 'tenant_admin' }
    mockGetTenantBalance.mockResolvedValue({
      success: true,
      balance: { credit_balance: 0, daily_avg_cost_7d: 0, estimated_days_left: 0 },
    })

    const { checkCreditBeforeAction } = useCreditCheck()
    const result = await checkCreditBeforeAction('newSession')

    expect(result.allowed).toBe(false)
    expect(result.reason).toBe('no_credit_blocked')
    expect(mockToast.error).toHaveBeenCalledWith(
      '积分余额已耗尽，无法继续对话，请联系管理员充值'
    )
  })

  it('sendMessage 入口：余额 ≤ 0 应阻断', async () => {
    mockAdmin.value = { user_id: 'u1', role: 'tenant_admin' }
    mockGetTenantBalance.mockResolvedValue({
      success: true,
      balance: { credit_balance: -5, daily_avg_cost_7d: 0, estimated_days_left: 0 },
    })

    const { checkCreditBeforeAction } = useCreditCheck()
    const result = await checkCreditBeforeAction('sendMessage')

    expect(result.allowed).toBe(false)
    expect(result.reason).toBe('no_credit_blocked')
  })

  it('login 入口：余额 ≤ 0 仅提醒不阻断（避免用户进不去）', async () => {
    mockAdmin.value = { user_id: 'u1', role: 'tenant_admin' }
    mockGetTenantBalance.mockResolvedValue({
      success: true,
      balance: { credit_balance: 0, daily_avg_cost_7d: 0, estimated_days_left: 0 },
    })

    const { checkCreditBeforeAction } = useCreditCheck()
    const result = await checkCreditBeforeAction('login')

    expect(result.allowed).toBe(true)
    expect(result.reason).toBe('no_credit_warned')
    expect(mockToast.error).toHaveBeenCalled()
  })

  it('newSession 入口：余额 > 100 放行', async () => {
    mockAdmin.value = { user_id: 'u1', role: 'tenant_admin' }
    mockGetTenantBalance.mockResolvedValue({
      success: true,
      balance: { credit_balance: 500, daily_avg_cost_7d: 10, estimated_days_left: 50 },
    })

    const { checkCreditBeforeAction } = useCreditCheck()
    const result = await checkCreditBeforeAction('newSession')

    expect(result.allowed).toBe(true)
    expect(result.reason).toBe('ok')
    expect(mockToast.error).not.toHaveBeenCalled()
  })

  it('平台管理员：跳过检查', async () => {
    mockAdmin.value = { user_id: 'admin1', role: 'platform_admin' }

    const { checkCreditBeforeAction } = useCreditCheck()
    const result = await checkCreditBeforeAction('newSession')

    expect(result.allowed).toBe(true)
    expect(result.reason).toBe('platform_admin_skipped')
    // 不应调用余额接口
    expect(mockGetTenantBalance).not.toHaveBeenCalled()
  })

  it('余额接口失败：放行（不阻塞主流程）', async () => {
    mockAdmin.value = { user_id: 'u1', role: 'tenant_admin' }
    mockGetTenantBalance.mockRejectedValue(new Error('network error'))

    const { checkCreditBeforeAction } = useCreditCheck()
    const result = await checkCreditBeforeAction('newSession')

    expect(result.allowed).toBe(true)
    expect(result.reason).toBe('balance_api_error')
    expect(result.balance).toBeNull()
  })
})
