/**
 * ChannelConfig「通知用途」回归：存量应用配置改选「仅通知」
 *
 * - 仅通知下弹窗不显示回调字段与回调地址，接入指引也不再引导配置回调地址；
 *   保存请求带 notify_usage=notify_only 且不含回调字段（库内旧值由后端保留）
 * - 仅通知保存后重新打开编辑、改回「智能体接入 + 通知」：表单带出库内保留的
 *   回调字段，无需重填
 *
 * API 模块按边界 mock（同 WecomScheduledSendForm.test.ts 手法），渲染与保存请求体
 * 走真实组件。
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import ChannelConfig from '@/components/saas/ChannelConfig.vue'

const api = vi.hoisted(() => ({
  listChannels: vi.fn(), createChannel: vi.fn(), updateChannel: vi.fn(), deleteChannel: vi.fn(),
  verifyChannel: vi.fn(), getAvailableSubagents: vi.fn(), rotateWechatMpToken: vi.fn(),
  rotateWebCSecret: vi.fn(), listTenantUsers: vi.fn(), listKfAccounts: vi.fn(),
  createKfAccount: vi.fn(), updateKfAccount: vi.fn(), deleteKfAccount: vi.fn(),
}))
vi.mock('@/api/saasTenant', () => api)
vi.mock('vue-toastification', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}))
vi.mock('@/composables/useTenantAuth', async () => {
  const { ref } = await import('vue')
  return {
    useTenantAuth: () => ({
      tenant: ref({ tenant_id: 't1' }), admin: ref({ role: 'tenant_admin', user_id: 'u1' }),
      saasToken: ref(''), privateCloud: ref(false), hiddenMenus: ref([]), isLoggedIn: ref(true),
      isLoading: ref(false), isInitialized: ref(true), init: vi.fn(), setLogin: vi.fn(),
      logout: vi.fn(), getCurrentTenantId: () => 't1', getAuthHeader: () => ({}),
    }),
  }
})

// BaseModal 真实实现 Teleport 到 body，这里用同 props 的内联桩承接默认插槽
const Modal = {
  props: ['modelValue', 'title'],
  template: '<section v-if="modelValue" :aria-label="title"><slot /></section>',
}

const WECOM_SEND_FIELDS = { corp_id: 'wwFAKE', agent_id: '1000002', secret: 'FAKE_SECRET' }
const WECOM_CALLBACK_FIELDS = { token: 'FAKE_CALLBACK_TOKEN', encoding_aes_key: 'FAKE_AES_KEY' }
const MEDIA_SWITCHES = { asr_enabled: true, image_recognition_enabled: false }
const NAME = '企业微信通知任务'

// 「原来配置」：企微应用五项齐全、用途缺省（仅智能体接入）
const WECOM_LEGACY = {
  config_id: 'cfg_wecom', channel_type: 'wecom', name: NAME, verified: 1,
  subagent_type: 'recruiting-operator',
  config: { ...WECOM_SEND_FIELDS, ...WECOM_CALLBACK_FIELDS, ...MEDIA_SWITCHES },
}
// 仅通知保存后的落库形态：用途为仅通知，回调字段由后端保留
const WECOM_NOTIFY_ONLY_STORED = {
  ...WECOM_LEGACY,
  config: { ...WECOM_SEND_FIELDS, ...WECOM_CALLBACK_FIELDS, notify_usage: 'notify_only', ...MEDIA_SWITCHES },
}

const FEISHU_LEGACY = {
  config_id: 'cfg_feishu', channel_type: 'feishu', name: '飞书应用', verified: 1,
  config: { app_id: 'cli_fake', app_secret: 'FAKE_SECRET', verification_token: 'FAKE_TOKEN', encrypt_key: 'FAKE_KEY' },
}
const DINGTALK_LEGACY = {
  config_id: 'cfg_dingtalk', channel_type: 'dingtalk', name: '钉钉应用', verified: 1,
  config: { app_key: 'ding_fake', app_secret: 'FAKE_SECRET', token: 'FAKE_TOKEN', encoding_aes_key: 'FAKE_KEY' },
}

// 对话接入用途下的指引步骤（仅通知不应再出现）
const CALLBACK_GUIDE_STEPS = ['设置 API 接收', '将下方回调地址填入', '完成验证']
const CALLBACK_LABELS = ['回调 Token', 'EncodingAESKey', '关联数字员工']
// 仅取表单字段 <label> 文本，避免与指引里的同名文字混淆
const fieldLabels = (root: any) => root.findAll('label').map((label: any) => label.text())
const inputValues = (root: any) => root.findAll('input').map((input: any) => input.element.value)

async function mountPage(channel: any) {
  api.listChannels.mockResolvedValue({ channels: [channel] })
  api.getAvailableSubagents.mockResolvedValue({ subagents: [] })
  api.listTenantUsers.mockResolvedValue({ users: [] })
  api.listKfAccounts.mockResolvedValue({ accounts: [] })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/t/:tenant_id/channels', component: { template: '<div />' } }],
  })
  await router.push('/t/t1/channels')
  await router.isReady()
  const wrapper = mount(ChannelConfig, {
    attachTo: document.body,
    global: {
      plugins: [router],
      provide: { sidebarCollapsed: ref(false), toggleSidebar: () => {} },
      stubs: { BaseModal: Modal, AppHeader: true, AbusePolicyEditor: true },
    },
  })
  await flushPromises()
  return wrapper
}

const modal = (wrapper: any) => wrapper.get('section[aria-label="渠道配置 - 编辑"]')
const selectUsage = async (root: any, value: string) => {
  await root.get(`input[type="radio"][value="${value}"]`).setValue(true)
  await flushPromises()
}
async function clickByText(root: any, text: string) {
  await root.findAll('button').find((button: any) => button.text() === text)!.trigger('click')
  await flushPromises()
}

beforeEach(() => {
  vi.clearAllMocks()
})

it('存量企微应用改选仅通知：隐藏回调字段与回调地址，指引不再引导配置回调，保存请求不带回调字段', async () => {
  api.updateChannel.mockResolvedValue({ success: true, channel: WECOM_NOTIFY_ONLY_STORED })
  const wrapper = await mountPage(WECOM_LEGACY)
  await clickByText(wrapper, '编辑')
  let form = modal(wrapper)
  // 改选前（仅智能体接入）：回调字段、回调地址与回调指引均显示
  for (const label of CALLBACK_LABELS) expect(fieldLabels(form)).toContain(label)
  expect(form.text()).toContain('/t/t1/wecom/callback/cfg_wecom')
  for (const step of CALLBACK_GUIDE_STEPS) expect(form.text()).toContain(step)

  await selectUsage(form, 'notify_only')
  form = modal(wrapper)
  expect(fieldLabels(form)).toEqual(expect.arrayContaining(['企业 ID (CorpID)', '应用 AgentId', '应用 Secret']))
  for (const label of CALLBACK_LABELS) expect(fieldLabels(form)).not.toContain(label)
  expect(form.text()).not.toContain('/wecom/callback/')
  expect(form.text()).toContain('无需配置回调地址')
  for (const step of CALLBACK_GUIDE_STEPS) expect(form.text()).not.toContain(step)

  await clickByText(form, '保存')
  // 隐藏的回调字段不随保存提交，库内旧值由后端保留
  expect(api.updateChannel).toHaveBeenCalledExactlyOnceWith('cfg_wecom', {
    name: NAME,
    config: { ...WECOM_SEND_FIELDS, ...MEDIA_SWITCHES, notify_usage: 'notify_only' },
    subagent_type: 'recruiting-operator',
  })
  wrapper.unmount()
})

it('同一弹窗内仅通知保存后直接改回「智能体接入 + 通知」：表单仍留有回调字段，保存请求照常带上', async () => {
  api.updateChannel.mockResolvedValue({ success: true, channel: WECOM_NOTIFY_ONLY_STORED })
  const wrapper = await mountPage(WECOM_LEGACY)
  await clickByText(wrapper, '编辑')
  await selectUsage(modal(wrapper), 'notify_only')
  await clickByText(modal(wrapper), '保存')
  await selectUsage(modal(wrapper), 'both')
  await clickByText(modal(wrapper), '保存')
  expect(api.updateChannel).toHaveBeenLastCalledWith('cfg_wecom', {
    name: NAME,
    config: { ...WECOM_SEND_FIELDS, ...WECOM_CALLBACK_FIELDS, ...MEDIA_SWITCHES, notify_usage: 'both' },
    subagent_type: 'recruiting-operator',
  })
  wrapper.unmount()
})

it('仅通知保存后重新打开编辑、改回「智能体接入 + 通知」：带出库内保留的回调字段，无需重填', async () => {
  api.updateChannel.mockResolvedValue({ success: true, channel: WECOM_LEGACY })
  const wrapper = await mountPage(WECOM_NOTIFY_ONLY_STORED)
  expect(wrapper.text()).not.toContain('/wecom/callback/')
  await clickByText(wrapper, '编辑')
  let form = modal(wrapper)
  expect(form.text()).toContain('无需配置回调地址')
  expect(form.text()).not.toContain('/wecom/callback/')

  await selectUsage(form, 'both')
  form = modal(wrapper)
  // 回调地址、回调字段与回调指引随用途恢复，字段值为库内保留的旧值
  expect(form.text()).toContain('/t/t1/wecom/callback/cfg_wecom')
  for (const step of CALLBACK_GUIDE_STEPS) expect(form.text()).toContain(step)
  for (const value of Object.values(WECOM_CALLBACK_FIELDS)) expect(inputValues(form)).toContain(value)

  await clickByText(form, '保存')
  expect(api.updateChannel).toHaveBeenCalledExactlyOnceWith('cfg_wecom', {
    name: NAME,
    config: { ...WECOM_SEND_FIELDS, ...WECOM_CALLBACK_FIELDS, ...MEDIA_SWITCHES, notify_usage: 'both' },
    subagent_type: 'recruiting-operator',
  })
  wrapper.unmount()
})

it.each([
  ['飞书', FEISHU_LEGACY, '机器人'],
  ['钉钉', DINGTALK_LEGACY, '工作通知'],
])('%s应用改选仅通知：指引不再引导配置回调，保留发送前提', async (_label, legacy, sendPrerequisite) => {
  const wrapper = await mountPage(legacy)
  await clickByText(wrapper, '编辑')
  const form = modal(wrapper)
  for (const step of ['事件与回调', '将下方回调地址填入']) expect(form.text()).toContain(step)

  await selectUsage(form, 'notify_only')
  const after = modal(wrapper)
  expect(after.text()).toContain('无需配置回调地址')
  // 发送前提不随回调步骤一起消失（飞书机器人能力 / 钉钉工作通知权限）
  expect(after.text()).toContain(sendPrerequisite)
  for (const step of ['事件与回调', '将下方回调地址填入']) expect(after.text()).not.toContain(step)
  expect(after.text()).not.toContain('/callback/')
  wrapper.unmount()
})
