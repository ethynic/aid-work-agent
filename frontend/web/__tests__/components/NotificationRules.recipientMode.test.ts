/**
 * NotificationRules 组件测试（定时任务通知接入通知规则回归）
 *
 * 覆盖缺陷场景：新增规则时手动把事件类型从默认「客户留资」切换为
 * 「定时任务通知」，recipient_mode 必须联动为 task_owner——否则后端
 * 校验 NOTIFICATION_RULE_RECIPIENT_INVALID（接收人模式不合法）。
 *
 * Mock 范式参考 TaskPlanView.test.ts（reactive routeState + vi.mock 模块级替换）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { reactive, ref } from 'vue'

const routerPush = vi.hoisted(() => vi.fn())

const routeState = reactive({
  path: '/t/tenant-1/notification-rules',
  params: { tenant_id: 'tenant-1' } as Record<string, any>,
  query: {} as Record<string, any>,
})

const createRuleMock = vi.hoisted(() => vi.fn())

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPush }),
  useRoute: () => routeState,
}))

vi.mock('vue-toastification', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}))

vi.mock('@/api/notifications', () => ({
  notificationsAPI: {
    listRules: vi.fn(async () => ({ rules: [] })),
    listTenantTargets: vi.fn(async () => ({ targets: [] })),
    createRule: createRuleMock,
    updateRule: vi.fn(async () => ({ rule: {} })),
    deleteRule: vi.fn(async () => ({ rule_id: 'rule_x', revoked_targets: 0 })),
    registerTenantTarget: vi.fn(async () => ({ target_id: 't1', binding_version: 1 })),
    revokeTenantTarget: vi.fn(async () => ({})),
    tenantTest: vi.fn(async () => ({ success: true })),
  },
}))

vi.mock('@/api/saasTenant', () => ({
  listChannels: vi.fn(async () => ({ channels: [] })),
  listKfAccounts: vi.fn(async () => ({ accounts: [] })),
}))

vi.mock('@/composables/useTenantAuth', () => ({
  useTenantAuth: () => ({
    admin: ref({ user_id: 'u1', username: 'tester', role: 'tenant_admin' }),
    isLoggedIn: ref(true),
    getAuthHeader: () => ({ Authorization: 'Bearer test-token', 'X-Tenant-Id': 'tenant-1' }),
    logout: vi.fn(),
  }),
}))

import NotificationRules from '@/components/saas/NotificationRules.vue'

async function openCreateModal() {
  const wrapper = mount(NotificationRules, {
    global: {
      stubs: { AppHeader: true, BaseModal: false, Teleport: true },
    },
  })
  await flushPromises()
  const createBtn = wrapper.findAll('button').find((b) => b.text().includes('新增规则'))
  expect(createBtn).toBeTruthy()
  await createBtn!.trigger('click')
  await flushPromises()
  return wrapper
}

describe('NotificationRules 新增规则 recipient_mode 联动', () => {
  beforeEach(() => {
    createRuleMock.mockReset()
    createRuleMock.mockImplementation(async (body: any) => ({
      rule: { rule_id: 'rule_new', is_system: false, ...body },
    }))
    routeState.query = {}
  })

  it('事件类型从客户留资切换为定时任务通知时，提交 recipient_mode=task_owner', async () => {
    const wrapper = await openCreateModal()

    const nameInput = wrapper.find('input[placeholder^="如："]')
    await nameInput.setValue('渠道销售日报')

    // 页面里还有分页器条数 select，事件类型 select 按其 option 值域定位；
    // 用原生 setValue 触发 change，经 BaseSelect emit 更新 v-model
    const eventSelect = wrapper
      .findAll('select')
      .find((s) => s.findAll('option').some((o) => o.element.value === 'scheduled.task'))
    expect(eventSelect).toBeTruthy()
    await eventSelect!.setValue('scheduled.task')
    await flushPromises()

    const saveBtn = wrapper.findAll('button').find((b) => b.text().trim() === '保存')
    expect(saveBtn).toBeTruthy()
    await saveBtn!.trigger('click')
    await flushPromises()

    expect(createRuleMock).toHaveBeenCalledTimes(1)
    const body = createRuleMock.mock.calls[0][0]
    expect(body.event_type).toBe('scheduled.task')
    expect(body.recipient_mode).toBe('task_owner')
  })

  it('切回其他事件类型时 recipient_mode 回落 tenant_admins', async () => {
    const wrapper = await openCreateModal()

    const nameInput = wrapper.find('input[placeholder^="如："]')
    await nameInput.setValue('积分提醒规则')

    const findEventSelect = () =>
      wrapper
        .findAll('select')
        .find((s) => s.findAll('option').some((o) => o.element.value === 'scheduled.task'))
    await findEventSelect()!.setValue('scheduled.task')
    await flushPromises()
    // scheduled.task 分支重渲染后重新查询，避免拿到已替换的旧节点
    await findEventSelect()!.setValue('billing.credit_low')
    await flushPromises()

    const saveBtn = wrapper.findAll('button').find((b) => b.text().trim() === '保存')
    await saveBtn!.trigger('click')
    await flushPromises()

    const body = createRuleMock.mock.calls[0][0]
    expect(body.event_type).toBe('billing.credit_low')
    expect(body.recipient_mode).toBe('tenant_admins')
  })
})
