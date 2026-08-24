/**
 * useCreditCheck composable 单元测试
 *
 * 覆盖场景：
 * - newSession 入口：余额 ≤ 0 阻断（allowed: false）
 * - sendMessage 入口：余额 ≤ 0 阻断（allowed: false）
 * - login 入口：余额 ≤ 0 仅提醒不阻断（allowed: true）
 * - 余额充足且非待续费：放行
 * - 待续费（renewal_pending=true）：提示续费、当天去重
 * - 待续费且预估天数 < 0：文案显示"暂无数据"
 * - 余额获取失败：放行
 * - 平台管理员：跳过检查
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// 使用 vi.hoisted 确保 mock 引用在 vi.mock 工厂内可用（vi.mock 调用会被提升到文件顶部）
// 注意：vi.hoisted 内不能引用外部 import（如 ref），故用普通对象模拟 ref 的 .value 接口
const { mockGetTenantBalance, mockToast, mockAdmin, mockTenant, mockGetCurrentTenantId } = vi.hoisted(() => ({
  mockGetTenantBalance: vi.fn(),
  mockToast: {
    error: vi.fn(),
    warning: vi.fn(),
    success: vi.fn(),
    info: vi.fn(),
  },
  // 模拟 ref<{user_id, role} | null> 的最小接口
  mockAdmin: { value: null as { user_id: string; role: string } | null },
  // 模拟 ref<{tenant_id} | null> 的最小接口
  mockTenant: { value: null as { tenant_id: string } | null },
  // 模拟当前路由提取的租户 ID（平台管理员代管场景有值）
  mockGetCurrentTenantId: vi.fn(),
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
    tenant: mockTenant,
    getCurrentTenantId: mockGetCurrentTenantId,
  }),
}))

import { useCreditCheck } from '@/composables/useCreditCheck'

describe('useCreditCheck', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockAdmin.value = null
    mockTenant.value = null
    // 默认无租户上下文（/portal 等场景）
    mockGetCurrentTenantId.mockReturnValue(undefined)
    // 默认清除 localStorage（低余额去重）
    localStorage.clear()
  })

  it('newSession 入口：余额 ≤ 0 应阻断（allowed: false）', async () => {
    mockAdmin.value = { user_id: 'u1', role: 'tenant_admin' }
    mockGetTenantBalance.mockResolvedValue({
      success: true,
      balance: { credit_balance: 0, daily_avg_cost: 0, daily_avg_cost_7d: 0, estimated_days_left: 0, renewal_pending: true },
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
      balance: { credit_balance: -5, daily_avg_cost: 0, daily_avg_cost_7d: 0, estimated_days_left: 0, renewal_pending: true },
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
      balance: { credit_balance: 0, daily_avg_cost: 0, daily_avg_cost_7d: 0, estimated_days_left: 0, renewal_pending: true },
    })

    const { checkCreditBeforeAction } = useCreditCheck()
    const result = await checkCreditBeforeAction('login')

    expect(result.allowed).toBe(true)
    expect(result.reason).toBe('no_credit_warned')
    expect(mockToast.error).toHaveBeenCalled()
  })

  it('余额充足且非待续费：放行且不提示', async () => {
    mockAdmin.value = { user_id: 'u1', role: 'tenant_admin' }
    mockGetTenantBalance.mockResolvedValue({
      success: true,
      balance: { credit_balance: 500, daily_avg_cost: 10, daily_avg_cost_7d: 10, estimated_days_left: 50, renewal_pending: false },
    })

    const { checkCreditBeforeAction } = useCreditCheck()
    const result = await checkCreditBeforeAction('newSession')

    expect(result.allowed).toBe(true)
    expect(result.reason).toBe('ok')
    expect(mockToast.error).not.toHaveBeenCalled()
    expect(mockToast.success).not.toHaveBeenCalled()
  })

  it('login 入口：renewal_pending=true 应提示续费且当天去重', async () => {
    mockAdmin.value = { user_id: 'u1', role: 'tenant_admin' }
    mockGetTenantBalance.mockResolvedValue({
      success: true,
      balance: { credit_balance: 100, daily_avg_cost: 10, daily_avg_cost_7d: 10, estimated_days_left: 10, renewal_pending: true },
    })

    const { checkCreditBeforeAction } = useCreditCheck()

    // 第一次调用：提示续费
    const result1 = await checkCreditBeforeAction('login')
    expect(result1.allowed).toBe(true)
    expect(result1.reason).toBe('renewal_pending_warned')
    expect(mockToast.success).toHaveBeenCalledTimes(1)
    expect(mockToast.success).toHaveBeenCalledWith('积分余额预计可用 10 天，即将耗尽，请及时续费')

    // 同一天第二次调用：不再重复提示
    const result2 = await checkCreditBeforeAction('login')
    expect(result2.allowed).toBe(true)
    expect(mockToast.success).toHaveBeenCalledTimes(1)
  })

  it('renewal_pending=true 且预估天数 < 0：文案显示"暂无数据"', async () => {
    mockAdmin.value = { user_id: 'u1', role: 'tenant_admin' }
    mockGetTenantBalance.mockResolvedValue({
      success: true,
      balance: { credit_balance: 100, daily_avg_cost: 0, daily_avg_cost_7d: 0, estimated_days_left: -1, renewal_pending: true },
    })

    const { checkCreditBeforeAction } = useCreditCheck()
    const result = await checkCreditBeforeAction('login')

    expect(result.allowed).toBe(true)
    expect(mockToast.success).toHaveBeenCalledWith('积分余额预计可用 暂无数据，即将耗尽，请及时续费')
  })

  it('平台管理员：无租户上下文时跳过检查', async () => {
    mockAdmin.value = { user_id: 'admin1', role: 'platform_admin' }
    mockGetCurrentTenantId.mockReturnValue(undefined)

    const { checkCreditBeforeAction } = useCreditCheck()
    const result = await checkCreditBeforeAction('newSession')

    expect(result.allowed).toBe(true)
    expect(result.reason).toBe('platform_admin_skipped')
    // 不应调用余额接口
    expect(mockGetTenantBalance).not.toHaveBeenCalled()
  })

  it('平台管理员在租户前台代管：按 X-Tenant-Id 检查对应租户余额并提醒', async () => {
    mockAdmin.value = { user_id: 'admin1', role: 'platform_admin' }
    mockTenant.value = { tenant_id: 'tenant_49a0f3d1706a' }
    mockGetCurrentTenantId.mockReturnValue('tenant_49a0f3d1706a')
    mockGetTenantBalance.mockResolvedValue({
      success: true,
      balance: { credit_balance: 3.7, daily_avg_cost: 1.2, daily_avg_cost_7d: 1.2, estimated_days_left: 3, renewal_pending: true },
    })

    const { checkCreditBeforeAction } = useCreditCheck()
    const result = await checkCreditBeforeAction('login')

    expect(result.allowed).toBe(true)
    expect(result.reason).toBe('renewal_pending_warned')
    // 应调用余额接口（不再被平台管理员分支拦截）
    expect(mockGetTenantBalance).toHaveBeenCalledTimes(1)
    expect(mockToast.success).toHaveBeenCalledWith('积分余额预计可用 3 天，即将耗尽，请及时续费')
  })

  it('跨租户去重：同一用户不同租户当天各自提醒', async () => {
    mockAdmin.value = { user_id: 'u1', role: 'tenant_admin' }
    mockGetTenantBalance.mockResolvedValue({
      success: true,
      balance: { credit_balance: 100, daily_avg_cost: 10, daily_avg_cost_7d: 10, estimated_days_left: 10, renewal_pending: true },
    })

    const { checkCreditBeforeAction } = useCreditCheck()

    // 租户 t1 提醒后
    mockTenant.value = { tenant_id: 't1' }
    await checkCreditBeforeAction('login')
    expect(mockToast.success).toHaveBeenCalledTimes(1)

    // 同一天切换租户 t2，应再次提醒（去重 key 含 tenant_id）
    mockTenant.value = { tenant_id: 't2' }
    await checkCreditBeforeAction('login')
    expect(mockToast.success).toHaveBeenCalledTimes(2)

    // 回到 t1，同一天不再提醒
    mockTenant.value = { tenant_id: 't1' }
    await checkCreditBeforeAction('login')
    expect(mockToast.success).toHaveBeenCalledTimes(2)
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
