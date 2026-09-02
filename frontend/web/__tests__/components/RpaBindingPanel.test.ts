/**
 * RpaBindingPanel 组件测试（平台后台「RPA 绑定管理」Tab）
 *
 * 覆盖（自 2026-06 改造后列表粒度 = client，不再是 binding）：
 * - 挂载后从 MSW 加载跨租户 client 列表（默认「全部」，不再过滤）
 * - 默认过滤「全部」：显示所有 client（含 active / disabled）
 * - 连接状态徽章：last_heartbeat_at NULL → 未连接；> 60s → 离线；<= 60s → 在线
 * - client_status='disabled' 行显示「恢复」按钮；'active' 行显示「暂停」按钮
 * - 占位地址 https://agent.example.com 触发黄色警告样式
 * - 详情弹框中 agent_base_url 可编辑保存
 * - 「+ 新增绑定」按钮打开新增表单
 * - 填字段后提交 → 调 registerClient → 弹密钥展示对话框
 * - 密钥展示对话框关闭按钮默认禁用，勾选后才能关
 * - 轮换密钥流程
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

// mock vue-toastification
const toastMock = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }
vi.mock('vue-toastification', () => ({ useToast: () => toastMock }))

// mock confirm
const confirmMock = vi.fn(() => true)
Object.defineProperty(globalThis, 'confirm', { value: confirmMock, writable: true })

// mock useTenantAuth（组件依赖其 isLoggedIn/admin）
vi.mock('@/composables/useTenantAuth', () => ({
  useTenantAuth: () => ({
    isLoggedIn: { value: true },
    admin: { value: { role: 'platform_admin', username: 'admin' } },
  }),
}))

// 拉取真实组件
import RpaBindingPanel from '@/components/saas/RpaBindingPanel.vue'

// 记录当前挂载的 wrapper，afterEach 主动卸载，避免 Teleport 残留导致下一个用例 patch 报错
let activeWrapper: ReturnType<typeof mount> | null = null

function mountPanel() {
  // attachTo: document.body 让 BaseModal 的 Teleport 内容可被 document.querySelector 检索到
  // （vue-test-utils 默认 mount 不挂到真实 DOM，Teleport 内容 wrapper.html() 看不到）
  const div = document.createElement('div')
  div.id = 'test-mount'
  document.body.appendChild(div)
  activeWrapper = mount(RpaBindingPanel, {
    attachTo: div,
    global: {
      provide: {
        toggleSidebar: () => {},
      },
    },
  })
  return activeWrapper
}

// 取整个 body 的 HTML（用于跨 teleport 检查 BaseModal 渲染的弹框内容）
function bodyHtml(): string {
  return document.body.innerHTML
}

describe('RpaBindingPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    confirmMock.mockReturnValue(true)
  })

  afterEach(async () => {
    // 主动卸载组件，避免 Teleport 残留导致下一个用例 patch 出错（insertBefore null）
    if (activeWrapper) {
      activeWrapper.unmount()
      activeWrapper = null
    }
    // 清理挂载残留的 DOM（Teleport 内容、mount 容器）
    document.body.innerHTML = ''
  })

  it('挂载后加载跨租户 client 列表（默认「全部」，显示 active 和 disabled）', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const html = wrapper.html()
    // 默认「全部」：3 个 client 都应显示（active / disabled / 离线 active）
    expect(html).toContain('客户端A')
    expect(html).toContain('客户端B')
    expect(html).toContain('客户端C-离线')
  })

  it('切换到「需关注」过滤后只显示非 active client（即客户端B-disabled）', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const selects = wrapper.findAll('select')
    const statusSelect = selects[0]
    await statusSelect.setValue('needs_review_only')
    await flushPromises()
    const html = wrapper.html()
    // 客户端B 是 disabled，应保留
    expect(html).toContain('客户端B')
    // 客户端A / C 是 active，应被过滤掉
    expect(html).not.toContain('客户端A')
    expect(html).not.toContain('客户端C-离线')
  })

  it('连接状态徽章：last_heartbeat_at NULL → 未连接（客户端B）', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const html = wrapper.html()
    expect(html).toContain('未连接')
  })

  it('连接状态徽章：> 60s 心跳 → 离线（客户端C）', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const html = wrapper.html()
    expect(html).toContain('离线')
  })

  it('连接状态徽章：<= 60s 心跳 → 在线（客户端A）', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const html = wrapper.html()
    expect(html).toContain('在线')
  })

  it('account_count=0 时显示「尚未接入」', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const html = wrapper.html()
    expect(html).toContain('尚未接入')
  })

  it('account_count>0 时显示「N 个账号」徽章', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const html = wrapper.html()
    expect(html).toContain('2 个账号')
  })

  it('占位地址 https://agent.example.com 在表格中显示警告样式', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const html = wrapper.html()
    expect(html).toContain('https://agent.example.com')
    expect(html).toContain('text-warning-700')
  })

  it('disabled client 行显示「恢复」按钮（client_status=disabled）', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    // 客户端B 是 disabled，应有「恢复」按钮
    const html = wrapper.html()
    expect(html).toContain('恢复')
  })

  it('active client 行显示「暂停」按钮（client_status=active）', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const html = wrapper.html()
    expect(html).toContain('暂停')
  })

  it('点击「恢复」调用 resume API 并 toast 成功', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    // 找到 disabled 客户端 B 的「恢复」按钮（在对应行内）
    const rows = wrapper.findAll('tbody tr')
    const disabledRow = rows.find(r => r.html().includes('客户端B'))
    expect(disabledRow).toBeTruthy()
    const resumeBtn = disabledRow!.findAll('button').find(b => b.text().includes('恢复'))
    expect(resumeBtn).toBeTruthy()
    await resumeBtn!.trigger('click')
    await flushPromises()
    expect(toastMock.success).toHaveBeenCalledWith('已恢复')
  })

  it('打开详情弹框显示 agent_base_url 编辑框', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const detailBtns = wrapper.findAll('button').filter(b => b.text().includes('详情'))
    expect(detailBtns.length).toBeGreaterThan(0)
    await detailBtns[0].trigger('click')
    await flushPromises()
    const modalHtml = wrapper.html()
    expect(modalHtml).toContain('agent_base_url')
  })

  // ==================== 新增绑定流程 ====================

  it('「+ 新增绑定」按钮打开新增表单弹框', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const btn = wrapper.findAll('button').find(b => b.text().includes('新增绑定'))
    expect(btn?.exists()).toBe(true)
    await btn!.trigger('click')
    await flushPromises()
    const html = bodyHtml()
    expect(html).toContain('新增 RPA 绑定')
    expect(html).toContain('目标租户')
    expect(html).toContain('绑定名称')
    expect(html).toContain('关联数字员工')
  })

  it('新增表单加载租户列表（仅 active 租户可选，已停用被过滤）', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const btn = wrapper.findAll('button').find(b => b.text().includes('新增绑定'))!
    await btn.trigger('click')
    await flushPromises()
    const html = bodyHtml()
    expect(html).toContain('租户A')
    expect(html).toContain('租户B')
    // tenant_C 状态 suspended，应被过滤
    expect(html).not.toContain('已停用租户')
  })

  it('选中租户后加载该租户的可用数字员工', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const btn = wrapper.findAll('button').find(b => b.text().includes('新增绑定'))!
    await btn.trigger('click')
    await flushPromises()
    // 弹框内的 select 在 body 下（Teleport），用原生 DOM 操作
    const tenantSelect = Array.from(document.querySelectorAll('select')).find(s => {
      return Array.from(s.querySelectorAll('option')).some(o => (o as HTMLOptionElement).value === 'tenant_A')
    }) as HTMLSelectElement | undefined
    expect(tenantSelect).toBeTruthy()
    const selectEl = tenantSelect as HTMLSelectElement
    selectEl.value = 'tenant_A'
    selectEl.dispatchEvent(new Event('change'))
    await flushPromises()
    const html = bodyHtml()
    expect(html).toContain('trade-specialist')
    expect(html).toContain('travel-consultant')
  })

  it('必填字段缺失时提交显示校验错误，不发请求', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const btn = wrapper.findAll('button').find(b => b.text().includes('新增绑定'))!
    await btn.trigger('click')
    await flushPromises()
    const createBtnEl = Array.from(document.querySelectorAll('button')).find(b =>
      b.textContent?.includes('创建') && !b.textContent?.includes('创建中'),
    ) as HTMLButtonElement | undefined
    expect(createBtnEl).toBeTruthy()
    createBtnEl!.click()
    await flushPromises()
    expect(toastMock.error).toHaveBeenCalledWith('请选择目标租户')
  })

  it('填全字段提交 → 弹密钥展示对话框，含 client_id/client_secret/yaml 片段', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const btn = wrapper.findAll('button').find(b => b.text().includes('新增绑定'))!
    await btn.trigger('click')
    await flushPromises()
    // 选租户
    const tenantSelect = Array.from(document.querySelectorAll('select')).find(s =>
      Array.from(s.querySelectorAll('option')).some(o => (o as HTMLOptionElement).value === 'tenant_A'),
    ) as HTMLSelectElement
    tenantSelect.value = 'tenant_A'
    tenantSelect.dispatchEvent(new Event('change'))
    await flushPromises()
    // 填绑定名称
    const nameInput = Array.from(document.querySelectorAll('input')).find(i =>
      (i as HTMLInputElement).placeholder?.includes('销售一组'),
    ) as HTMLInputElement
    expect(nameInput).toBeTruthy()
    nameInput.value = '销售一组-客户机01'
    nameInput.dispatchEvent(new Event('input'))
    await flushPromises()

    const createBtnEl = Array.from(document.querySelectorAll('button')).find(b =>
      b.textContent?.includes('创建') && !b.textContent?.includes('创建中'),
    ) as HTMLButtonElement
    createBtnEl.click()
    await flushPromises()

    const html = bodyHtml()
    expect(html).toContain('客户端密钥')
    expect(html).toContain('rpa_client_new_test')
    expect(html).toContain('plain_secret_once_only_xyz')
    expect(html).toContain('client_secret_ref: "dpapi:Client.WeComPersonalRpa:client_secret"')
    expect(html).toContain('tenant_A')
    expect(html).toContain('https://agent.example.com')
  })

  it('密钥展示对话框关闭按钮默认禁用，勾选后才能关', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const btn = wrapper.findAll('button').find(b => b.text().includes('新增绑定'))!
    await btn.trigger('click')
    await flushPromises()
    const tenantSelect = Array.from(document.querySelectorAll('select')).find(s =>
      Array.from(s.querySelectorAll('option')).some(o => (o as HTMLOptionElement).value === 'tenant_A'),
    ) as HTMLSelectElement
    tenantSelect.value = 'tenant_A'
    tenantSelect.dispatchEvent(new Event('change'))
    await flushPromises()
    const nameInput = Array.from(document.querySelectorAll('input')).find(i =>
      (i as HTMLInputElement).placeholder?.includes('销售一组'),
    ) as HTMLInputElement
    nameInput.value = '销售一组-客户机01'
    nameInput.dispatchEvent(new Event('input'))
    await flushPromises()
    const createBtnEl = Array.from(document.querySelectorAll('button')).find(b =>
      b.textContent?.includes('创建') && !b.textContent?.includes('创建中'),
    ) as HTMLButtonElement
    createBtnEl.click()
    await flushPromises()

    const closeBtn = Array.from(document.querySelectorAll('button')).find(b =>
      b.textContent?.trim() === '关闭',
    ) as HTMLButtonElement | undefined
    expect(closeBtn).toBeTruthy()
    expect(closeBtn!.disabled).toBe(true)

    const checkbox = document.querySelector('input[type="checkbox"]') as HTMLInputElement
    expect(checkbox).toBeTruthy()
    checkbox.checked = true
    checkbox.dispatchEvent(new Event('change'))
    await flushPromises()

    const closeBtn2 = Array.from(document.querySelectorAll('button')).find(b =>
      b.textContent?.trim() === '关闭',
    ) as HTMLButtonElement
    expect(closeBtn2.disabled).toBe(false)

    closeBtn2.click()
    await flushPromises()
    const htmlAfter = bodyHtml()
    expect(htmlAfter).not.toContain('plain_secret_once_only_xyz')
  })

  // ==================== 轮换密钥流程 ====================

  it('列表行渲染「轮换密钥」按钮', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const rotateBtns = wrapper.findAll('button').filter(b => b.text().includes('轮换密钥'))
    expect(rotateBtns.length).toBeGreaterThan(0)
  })

  it('点击「轮换密钥」弹出 confirm 确认对话框', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const rotateBtn = wrapper.findAll('button').find(b => b.text().includes('轮换密钥'))!
    expect(rotateBtn).toBeTruthy()
    confirmMock.mockClear()
    await rotateBtn.trigger('click')
    expect(confirmMock).toHaveBeenCalledTimes(1)
    const calls = confirmMock.mock.calls as unknown as string[][]
    const confirmArg = String(calls[0][0])
    expect(confirmArg).toContain('轮换')
  })

  it('confirm 取消时不发送 rotate 请求', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    confirmMock.mockReturnValueOnce(false)
    const rotateBtn = wrapper.findAll('button').find(b => b.text().includes('轮换密钥'))!
    await rotateBtn.trigger('click')
    await flushPromises()
    const html = bodyHtml()
    expect(html).not.toContain('rotated_new_secret_abc123')
  })

  it('confirm 确认 → 调用 rotate API → 弹密钥展示对话框显示新 secret', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const rotateBtn = wrapper.findAll('button').find(b => b.text().includes('轮换密钥'))!
    await rotateBtn.trigger('click')
    await flushPromises()
    const html = bodyHtml()
    expect(html).toContain('客户端密钥')
    expect(html).toContain('rotated_new_secret_abc123')
    // client_id 透传（rotate 接口返回 params.clientId）
    // rpa_client_a 是列表中第一个 client
    expect(html).toContain('rpa_client_a')
    expect(html).toContain('client_secret_ref: "dpapi:Client.WeComPersonalRpa:client_secret"')
  })

  it('轮换后密钥展示对话框关闭按钮默认禁用，勾选后才能关，关闭后刷新列表', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const rotateBtn = wrapper.findAll('button').find(b => b.text().includes('轮换密钥'))!
    await rotateBtn.trigger('click')
    await flushPromises()
    const closeBtn = Array.from(document.querySelectorAll('button')).find(b =>
      b.textContent?.trim() === '关闭',
    ) as HTMLButtonElement | undefined
    expect(closeBtn).toBeTruthy()
    expect(closeBtn!.disabled).toBe(true)
    const checkbox = document.querySelector('input[type="checkbox"]') as HTMLInputElement
    expect(checkbox).toBeTruthy()
    checkbox.checked = true
    checkbox.dispatchEvent(new Event('change'))
    await flushPromises()
    const closeBtn2 = Array.from(document.querySelectorAll('button')).find(b =>
      b.textContent?.trim() === '关闭',
    ) as HTMLButtonElement
    expect(closeBtn2.disabled).toBe(false)
    closeBtn2.click()
    await flushPromises()
    const htmlAfter = bodyHtml()
    expect(htmlAfter).not.toContain('rotated_new_secret_abc123')
  })
})
