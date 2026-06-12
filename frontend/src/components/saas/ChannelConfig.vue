<template>
  <div class="page-container bg-canvas">
    <!-- Header Bar -->
    <AppHeader
      title="渠道配置"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    >
    </AppHeader>

    <!-- Main Content -->
    <div class="page-content p-6">
      <div class="page-toolbar">
        <div></div>
        <div class="page-toolbar-right">
          <BaseButton @click="openAddChannel">添加渠道</BaseButton>
        </div>
      </div>

    <div v-if="loading" class="text-center py-12 text-muted">加载中...</div>

    <!-- 渠道列表 -->
    <div v-else-if="channels.length > 0" class="space-y-4">
      <div v-for="ch in channels" :key="ch.config_id" class="bg-surface rounded-lg border border-default p-5">
        <div class="flex items-center justify-between mb-3">
          <div class="flex items-center gap-3">
            <span class="text-sm text-default font-mono">{{ ch.id }}</span>
            <span class="text-sm text-default">{{ channelTypeLabel(ch.channel_type) }}</span>
            <BaseBadge :intent="ch.verified ? 'success' : 'warning'">
              {{ ch.verified ? '已验证' : '未验证' }}
            </BaseBadge>
            <BaseBadge v-if="ch.subagent_type" intent="info">
              🤖 {{ subagentTypeLabel(ch.subagent_type) }}
            </BaseBadge>
          </div>
          <div class="flex items-center gap-2">
            <BaseButton intent="ghost" size="sm" @click="showGuide(ch)">配置指南</BaseButton>
            <BaseButton intent="ghost" size="sm" @click="handleVerify(ch.config_id)">验证连接</BaseButton>
            <BaseButton intent="ghost" size="sm" @click="editChannel(ch)">编辑</BaseButton>
            <BaseButton intent="danger-ghost" size="sm" @click="handleDelete(ch.config_id)">删除</BaseButton>
          </div>
        </div>
        <!-- 回调地址展示 -->
        <div v-if="tenant" class="bg-canvas rounded-lg p-3 text-sm">
          <span class="text-muted">回调地址：</span>
          <code class="text-primary-600 select-all font-mono">{{ getCallbackUrl(ch.channel_type, ch.config_id) }}</code>
          <BaseButton intent="ghost" size="sm" @click="copyUrl(getCallbackUrl(ch.channel_type, ch.config_id), ch.config_id)">
            {{ copied[ch.config_id] ? '已复制' : '复制' }}
          </BaseButton>
        </div>
      </div>
    </div>

    <div v-else class="text-center py-16 bg-surface rounded-lg border border-default">
      <div class="text-4xl mb-4">🔗</div>
      <p class="text-lg font-medium text-default mb-2">尚未配置任何渠道</p>
      <p class="text-sm text-muted mb-6">配置企业微信、钉钉或飞书后，员工即可在 IM 中与智能体对话</p>
      <BaseButton @click="openAddChannel">添加第一个渠道</BaseButton>
    </div>
    </div>

    <!-- ==================== 添加/编辑弹窗 ==================== -->
    <BaseModal
      v-model="showForm"
      :title="editingId ? '渠道配置 - 编辑' : '渠道配置 - 新增'"
      size="xl"
      :close-on-overlay="false"
      :mode="editingId ? 'edit' : 'create'"
      :content-class="{ 'modal-fullscreen': isFullscreen }"
    >
      <template #header-extra>
        <BaseButton intent="ghost" size="sm" class="modal-fullscreen-btn" title="全屏" @click="toggleFullscreen">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>
          </svg>
        </BaseButton>
      </template>

      <div>
        <!-- 操作按钮区（第一行） -->
        <div class="flex items-center gap-2 mb-3">
          <BaseButton @click="handleSubmit">保存</BaseButton>
          <BaseButton intent="secondary" @click="handleSaveAndClose">保存并关闭</BaseButton>
        </div>

        <p v-if="!editingId" class="text-sm text-muted mb-3">选择 IM 平台，然后填写应用凭证</p>

        <!-- 渠道类型选择 -->
        <div v-if="!editingId" class="grid grid-cols-4 gap-2 mb-3">
          <button
            v-for="ct in channelTypes" :key="ct.value"
            @click="form.channel_type = ct.value"
            class="flex flex-col items-center gap-1.5 py-3 px-2 rounded-lg border-2 transition-all cursor-pointer"
            :class="form.channel_type === ct.value ? 'border-primary-400 bg-primary-50' : 'border-default hover:border-hover'"
          >
            <span class="text-lg">{{ ct.icon }}</span>
            <span class="text-xs font-medium" :class="form.channel_type === ct.value ? 'text-primary-700' : 'text-default'">{{ ct.label }}</span>
          </button>
        </div>

        <!-- 配置指引摘要 -->
        <div class="bg-warning-50 border border-warning-200 rounded-lg p-3 mb-3 text-sm text-warning-800">
          <p class="font-medium mb-1.5">{{ currentGuide.title }}</p>
          <ol class="list-decimal list-inside space-y-0.5 text-warning-700">
            <li v-for="(step, i) in currentGuide.steps" :key="i">{{ step }}</li>
          </ol>
          <a v-if="currentGuide.docUrl" :href="currentGuide.docUrl" target="_blank"
            class="inline-block mt-1.5 text-primary-600 hover:underline text-xs">
            前往 {{ channelTypeLabel(form.channel_type) }} 管理后台 &rarr;
          </a>
        </div>

        <!-- 表单字段（一行4个） -->
        <div class="grid grid-cols-4 gap-3">
          <div v-for="field in channelFields" :key="field.key">
            <label class="text-sm text-muted mb-1 block">{{ field.label }}</label>
            <BaseInput
              v-model="form.config[field.key]"
              :placeholder="field.placeholder"
            />
            <p v-if="field.hint" class="mt-1 text-xs text-muted">{{ field.hint }}</p>
          </div>

          <!-- 关联数字员工 -->
          <div>
            <label class="text-sm text-muted mb-1 block">关联数字员工</label>
            <BaseSelect v-model="form.subagent_type">
              <option value="">不绑定（默认）</option>
              <option v-for="sa in availableSubagents" :key="sa" :value="sa">{{ subagentTypeLabel(sa) }} ({{ sa }})</option>
            </BaseSelect>
            <p class="mt-1 text-xs text-muted">选择该渠道消息由哪个数字员工处理</p>
          </div>
        </div>

        <!-- 微信客服特有：客服账号配置 -->
        <div v-if="form.channel_type === 'wecom_kf'" class="mt-3 pt-3 border-t border-default">
          <div class="flex items-center justify-between mb-3">
            <label class="text-sm font-medium text-default">客服账号配置</label>
            <BaseButton intent="ghost" size="sm" @click="addKfAccount">+ 添加客服账号</BaseButton>
          </div>
          <div v-if="kfAccounts.length === 0" class="text-xs text-muted bg-canvas rounded-lg p-4 text-center">
            尚未配置客服账号，请先添加
          </div>
          <div v-for="(kf, idx) in kfAccounts" :key="idx" class="bg-canvas border border-default rounded-lg p-3 mb-3">
            <div class="flex items-center justify-between mb-3">
              <span class="text-sm font-medium text-default">客服账号 #{{ idx + 1 }}</span>
              <BaseButton intent="danger-ghost" size="sm" @click="removeKfAccount(idx)">删除</BaseButton>
            </div>
            <div class="grid grid-cols-4 gap-3">
              <div>
                <label class="block text-xs text-muted mb-1">客服账号名称</label>
                <BaseInput v-model="kf.name" placeholder="售前咨询" />
              </div>
              <div>
                <label class="block text-xs text-muted mb-1">open_kfid</label>
                <BaseInput v-model="kf.open_kfid" placeholder="首次接收消息时自动填入" />
              </div>
              <div>
                <label class="block text-xs text-muted mb-1">绑定子智能体</label>
                <BaseSelect v-model="kf.subagent_type">
                  <option value="">不绑定（使用渠道默认）</option>
                  <option v-for="sa in availableSubagents" :key="sa" :value="sa">{{ subagentTypeLabel(sa) }} ({{ sa }})</option>
                </BaseSelect>
              </div>
              <div>
                <label class="block text-xs text-muted mb-1">欢迎语</label>
                <BaseInput v-model="kf.welcome_message" placeholder="您好，请问有什么可以帮您？" />
              </div>
              <div class="col-span-2">
                <label class="block text-xs text-muted mb-1">人工接待人员（企微 userid）</label>
                <BaseInput v-model="kf.servicer_userid_list" placeholder="zhangsan, lisi" />
                <p class="mt-0.5 text-xs text-muted">多个用逗号分隔</p>
              </div>
              <div class="col-span-2">
                <label class="block text-xs text-muted mb-1">转人工关键词</label>
                <BaseInput v-model="kf.human_transfer_keywords" placeholder="人工服务, 转人工, 人工客服" />
                <p class="mt-0.5 text-xs text-muted">多个用逗号分隔，留空则关闭转人工功能</p>
              </div>
            </div>
          </div>
        </div>

        <!-- 回调地址提示 -->
        <div v-if="tenant" class="mt-3 bg-primary-50 border border-primary-200 rounded-lg p-3">
          <p class="text-sm font-medium text-primary-800 mb-1">回调地址</p>
          <p class="text-xs text-primary-600 mb-2">请将此地址填入 {{ channelTypeLabel(form.channel_type) }} 后台的「接收消息」配置中</p>
          <div class="flex items-center gap-2">
            <code class="flex-1 bg-surface px-3 py-2 rounded text-sm text-primary-700 font-mono select-all break-all">{{ getCallbackUrl(form.channel_type) }}</code>
            <BaseButton intent="ghost" size="sm" @click="copyUrl(getCallbackUrl(form.channel_type), 'form')">{{ copied['form'] ? '已复制' : '复制' }}</BaseButton>
          </div>
        </div>

        <div v-if="formError" class="mt-3 p-3 bg-danger-50 border border-danger-200 rounded-lg text-danger-600 text-sm">{{ formError }}</div>
      </div>
    </BaseModal>

    <!-- ==================== 配置指南弹窗 ==================== -->
    <BaseModal v-model="showGuideModal" :title="`${channelTypeLabel(guideChannel)} 接入指南`" size="xl" mode="view">
      <!-- 步骤指引 -->
      <div class="space-y-4">
        <div v-for="(step, i) in fullGuide.steps" :key="i" class="flex gap-4">
          <div class="flex-shrink-0 w-8 h-8 rounded-full bg-primary-100 text-primary-700 flex items-center justify-center text-sm font-bold">{{ i + 1 }}</div>
          <div class="flex-1 pt-1">
            <p class="font-medium text-default">{{ step.title }}</p>
            <p class="text-sm text-muted mt-0.5">{{ step.desc }}</p>
            <p v-if="step.location" class="text-xs text-primary-600 mt-1">位置：{{ step.location }}</p>
          </div>
        </div>
      </div>

      <!-- 回调地址 -->
      <div class="mt-6 bg-canvas border border-default rounded-lg p-4">
        <p class="font-medium text-default mb-2">回调地址</p>
        <p class="text-sm text-muted mb-3">在 {{ channelTypeLabel(guideChannel) }} 后台配置接收消息时，URL 填入：</p>
        <div class="flex items-center gap-2">
          <code class="flex-1 bg-surface px-3 py-2 rounded text-sm text-primary-700 font-mono select-all break-all border border-default">{{ getCallbackUrl(guideChannel) }}</code>
          <BaseButton intent="ghost" size="sm" @click="copyUrl(getCallbackUrl(guideChannel), 'guide')">{{ copied['guide'] ? '已复制' : '复制' }}</BaseButton>
        </div>
      </div>

      <!-- 凭证说明表 -->
      <div class="mt-6">
        <p class="font-medium text-default mb-3">凭证字段说明</p>
        <div class="table-scroll-wrapper">
          <BaseTable :columns="guideFieldColumns" :data="channelFieldMap[guideChannel] || []" row-key="key">
            <template #key="{ row }"><span class="font-mono text-xs">{{ row.key }}</span></template>
            <template #label="{ row }">{{ row.label }}</template>
            <template #location="{ row }"><span class="text-muted text-xs">{{ row.location || '-' }}</span></template>
          </BaseTable>
        </div>
      </div>

      <!-- 常见问题 -->
      <div v-if="fullGuide.faq" class="mt-6">
        <p class="font-medium text-default mb-3">常见问题</p>
        <div class="space-y-3">
          <div v-for="(item, i) in fullGuide.faq" :key="i">
            <p class="text-sm font-medium text-default">Q: {{ item.q }}</p>
            <p class="text-sm text-muted">A: {{ item.a }}</p>
          </div>
        </div>
      </div>

      <template #footer>
        <BaseButton intent="secondary" @click="showGuideModal = false">关闭</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, inject } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import { listChannels, createChannel, updateChannel, deleteChannel, verifyChannel, getAvailableSubagents } from '@/api/saasTenant'
import { useTenantAuth } from '@/composables/useTenantAuth'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const tenantId = computed(() => route.params.tenant_id as string)

const { tenant, admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()

// 统一的登录状态检查
const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)

// 统一的用户信息
const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone
  } : null
})

// 从 PortalLayout 注入侧边栏状态
const sidebarCollapsed = inject<{ value: boolean }>('sidebarCollapsed')
const toggleSidebarFn = inject<() => void>('toggleSidebar')

// 侧边栏折叠状态
const localSidebarCollapsed = ref(false)
const isSidebarCollapsed = computed({
  get: () => sidebarCollapsed?.value ?? localSidebarCollapsed.value,
  set: (val: boolean) => {
    if (sidebarCollapsed) {
      sidebarCollapsed.value = val
    } else {
      localSidebarCollapsed.value = val
    }
  }
})

function handleToggleSidebar() {
  if (toggleSidebarFn) {
    toggleSidebarFn()
  } else {
    isSidebarCollapsed.value = !isSidebarCollapsed.value
  }
}

async function handleLogout() {
  await tenantLogout()
  router.push(`/t/${tenantId.value}/login`)
}
const loading = ref(true)
const channels = ref<any[]>([])
const availableSubagents = ref<string[]>([])
const showForm = ref(false)
const submitting = ref(false)
const formError = ref('')
const editingId = ref<string | null>(null)
const copied = ref<Record<string, boolean>>({})
const showGuideModal = ref(false)
const guideChannel = ref('wecom')

// 微信客服特有：客服账号配置
const kfAccounts = ref<Array<{
  name: string
  open_kfid: string
  subagent_type: string
  welcome_message: string
  servicer_userid_list: string
  human_transfer_keywords: string
}>>([])

function addKfAccount() {
  kfAccounts.value.push({
    name: '',
    open_kfid: '',
    subagent_type: '',
    welcome_message: '',
    servicer_userid_list: '',
    human_transfer_keywords: '',
  })
}

function removeKfAccount(idx: number) {
  kfAccounts.value.splice(idx, 1)
}

const form = ref<{ channel_type: string; config: Record<string, string>; subagent_type: string }>({
  channel_type: 'wecom',
  config: {},
  subagent_type: ''
})

const isFullscreen = ref(false)
const formInitialSnapshot = ref<Record<string, any>>({})

function toggleFullscreen() {
  isFullscreen.value = !isFullscreen.value
}

function closeModal() {
  showForm.value = false
  isFullscreen.value = false
  formInitialSnapshot.value = {}
}

// ==================== 渠道类型定义 ====================

const channelTypes = [
  { value: 'wecom', label: '企业微信', icon: '' },
  { value: 'wecom_kf', label: '企业微信客服', icon: '' },
  { value: 'dingtalk', label: '钉钉', icon: '' },
  { value: 'feishu', label: '飞书', icon: '' },
]

function channelTypeLabel(type: string) {
  const map: Record<string, string> = { wecom: '企业微信', wecom_kf: '企业微信客服', dingtalk: '钉钉', feishu: '飞书' }
  return map[type] || type
}

// ==================== 字段定义（含获取位置说明） ====================

const channelFieldMap: Record<string, { key: string; label: string; placeholder: string; hint?: string; location: string }[]> = {
  wecom: [
    { key: 'corp_id', label: '企业 ID (CorpID)', placeholder: 'ww...', hint: '以 ww 开头的字符串', location: '「我的企业」→「企业信息」' },
    { key: 'agent_id', label: '应用 AgentId', placeholder: '1000002', location: '「应用管理」→ 应用详情页' },
    { key: 'secret', label: '应用 Secret', placeholder: '', hint: '点击「查看」获取', location: '「应用管理」→ 应用详情页' },
    { key: 'token', label: '回调 Token', placeholder: '', hint: '设置 API 接收时自行设定或随机生成', location: '「接收消息」→「设置 API 接收」' },
    { key: 'encoding_aes_key', label: 'EncodingAESKey', placeholder: '43 字符', hint: '点击「随机获取」，43 字符 Base64', location: '「接收消息」→「设置 API 接收」' },
  ],
  wecom_kf: [
    { key: 'corp_id', label: '企业 ID (CorpID)', placeholder: 'ww...', hint: '以 ww 开头的字符串', location: '「我的企业」→「企业信息」' },
    { key: 'secret', label: '应用 Secret', placeholder: '', hint: '自建应用的 Secret（微信客服无独立 Secret）', location: '「应用管理」→ 自建应用详情页' },
    { key: 'token', label: '回调 Token', placeholder: '', hint: '设置 API 接收时自行设定或随机生成', location: '「微信客服」→「API」→ 回调配置' },
    { key: 'encoding_aes_key', label: 'EncodingAESKey', placeholder: '43 字符', hint: '点击「随机获取」，43 字符 Base64', location: '「微信客服」→「API」→ 回调配置' },
  ],
  dingtalk: [
    { key: 'app_key', label: 'App Key', placeholder: '', location: '「基础信息」页面' },
    { key: 'app_secret', label: 'App Secret', placeholder: '', location: '「基础信息」页面' },
    { key: 'token', label: '回调 Token', placeholder: '', hint: '设置回调时自行设定', location: '「事件与回调」' },
    { key: 'encoding_aes_key', label: 'EncodingAESKey', placeholder: '43 字符', hint: '点击「随机获取」', location: '「事件与回调」' },
  ],
  feishu: [
    { key: 'app_id', label: 'App ID', placeholder: 'cli_...', location: '「凭证与基础信息」页面' },
    { key: 'app_secret', label: 'App Secret', placeholder: '', location: '「凭证与基础信息」页面' },
    { key: 'verification_token', label: 'Verification Token', placeholder: '', location: '「事件与回调」' },
    { key: 'encrypt_key', label: 'Encrypt Key', placeholder: '32 字符', location: '「事件与回调」' },
  ],
}

const channelFields = computed(() => channelFieldMap[form.value.channel_type] || [])

// ==================== 配置指引（简短版，弹窗内） ====================

const quickGuideMap: Record<string, { title: string; steps: string[]; docUrl: string }> = {
  wecom: {
    title: '企业微信接入步骤',
    steps: [
      '前往企业微信管理后台 →「应用管理」→「创建应用」',
      '记录 CorpID、AgentId、Secret',
      '在应用详情页找到「接收消息」→ 点击「设置 API 接收」',
      '将下方回调地址填入 URL 栏，生成 Token 和 EncodingAESKey',
      '先在此页面保存凭证，再到企业微信后台点击保存完成验证',
    ],
    docUrl: 'https://work.weixin.qq.com/wework_admin/frame',
  },
  wecom_kf: {
    title: '企业微信客服接入步骤',
    steps: [
      '前往企业微信管理后台 →「应用管理」→「微信客服」→ 确认已开启',
      '创建自建应用，记录 CorpID 和 Secret（不需要 AgentId）',
      '在「微信客服」→「通过 API 管理」中开启并授权自建应用',
      '创建客服账号，记录 open_kfid',
      '将下方回调地址填入「微信客服」→「API」→ 回调配置',
      '先在此页面保存凭证，再到企业微信后台点击保存完成验证',
    ],
    docUrl: 'https://work.weixin.qq.com/wework_admin/frame',
  },
  dingtalk: {
    title: '钉钉接入步骤',
    steps: [
      '前往钉钉开放平台 →「开发者后台」→ 创建应用',
      '启用「机器人」能力',
      '在「事件与回调」中添加 im.message.receive_v1 事件',
      '将下方回调地址填入 HTTP 回调配置',
      '记录 AppKey、AppSecret、Token、EncodingAESKey',
    ],
    docUrl: 'https://open.dingtalk.com/',
  },
  feishu: {
    title: '飞书接入步骤',
    steps: [
      '前往飞书开放平台 →「开发者后台」→ 创建企业自建应用',
      '启用「机器人」能力',
      '在「事件与回调」中添加 im.message.receive_v1 事件',
      '将下方回调地址填入请求地址',
      '记录 App ID、App Secret、Verification Token、Encrypt Key',
    ],
    docUrl: 'https://open.feishu.cn/',
  },
}

const currentGuide = computed(() => quickGuideMap[form.value.channel_type] || { title: '', steps: [], docUrl: '' })

// ==================== 完整配置指南（独立弹窗） ====================

const fullGuideMap: Record<string, { steps: { title: string; desc: string; location?: string }[]; faq?: { q: string; a: string }[] }> = {
  wecom: {
    steps: [
      { title: '创建自建应用', desc: '登录企业微信管理后台，进入「应用管理」→「自建」→ 点击「创建应用」。填写应用名称、描述、可见范围。', location: '应用管理 → 自建 → 创建应用' },
      { title: '获取企业 ID', desc: '进入「我的企业」→「企业信息」，复制 CorpID（格式 ww 开头）。', location: '我的企业 → 企业信息' },
      { title: '获取应用凭证', desc: '在应用详情页记录 AgentId 和 Secret（点击「查看」显示）。', location: '应用管理 → 应用详情' },
      { title: '配置 API 接收消息', desc: '在应用详情页找到「接收消息」→ 点击「设置 API 接收」。将回调地址填入 URL，点击「随机获取」生成 Token 和 EncodingAESKey。', location: '应用详情 → 接收消息 → 设置 API 接收' },
      { title: '配置可信 IP', desc: '在应用详情页找到「企业可信IP」，添加服务器公网 IP。', location: '应用详情 → 企业可信IP' },
      { title: '申请通讯录权限', desc: '在应用权限中申请「获取成员详情」权限，管理员审批后生效。', location: '应用详情 → 权限' },
      { title: '保存并验证', desc: '先在本页面保存凭证配置，然后回到企业微信后台点击「保存」。系统会自动验证回调地址。' },
    ],
    faq: [
      { q: '回调 URL 验证失败？', a: '确保服务器已启动、凭证已在本页面保存、SSL 证书有效。先在本页面保存，再到企业微信后台点保存。' },
      { q: '用户发消息没有回复？', a: '检查可信 IP 是否已配置、应用可见范围是否包含该用户、Agent 实例是否已启动并绑定企业微信渠道。' },
    ],
  },
  wecom_kf: {
    steps: [
      { title: '开启微信客服功能', desc: '登录企业微信管理后台，进入「应用管理」→「微信客服」，确认微信客服功能已开启。', location: '应用管理 → 微信客服' },
      { title: '创建自建应用', desc: '进入「应用管理」→「自建」→ 创建应用。记录 CorpID（「我的企业」→「企业信息」）和 Secret（应用详情页）。微信客服场景不需要 AgentId。', location: '应用管理 → 自建' },
      { title: '设置微信客服 API 管理', desc: '进入「微信客服」→「通过 API 管理」，开启「通过 API 管理微信客服账号」，将步骤 2 的自建应用设为「可调用接口的应用」。', location: '微信客服 → 通过 API 管理' },
      { title: '创建客服账号', desc: '进入「微信客服」→「客服账号」→ 添加客服账号。创建后通过 API 获取 open_kfid（格式如 wkAAAA）。可创建多个客服账号绑定不同子智能体。', location: '微信客服 → 客服账号' },
      { title: '配置回调 URL', desc: '在「微信客服」→「API」中找到回调配置，填写回调 URL、Token、EncodingAESKey。注意：需先在本页面保存凭证后再到企微后台点保存。', location: '微信客服 → API → 回调配置' },
      { title: '配置可信 IP', desc: '在应用详情页找到「企业可信IP」，添加服务器公网 IP。', location: '应用详情 → 企业可信IP' },
      { title: '设置接待方式', desc: '进入「微信客服」→「客服账号」→ 选择客服账号 → 设置「接待方式」为「机器人+人工接待」。设置为「仅人工接待」时消息不会通过 API 推送。', location: '微信客服 → 客服账号 → 接待方式' },
      { title: '保存并验证', desc: '先在本页面保存凭证配置，再回到企业微信后台点击「保存」完成验证。' },
    ],
    faq: [
      { q: '回调 URL 验证失败？', a: '确保服务器已启动、凭证已在本页面保存、SSL 证书有效。先在本页面保存，再到企业微信后台点保存。' },
      { q: '收不到客户消息？', a: '确认「通过 API 管理微信客服账号」已开启、自建应用在「可调用接口的应用」列表中、客服账号接待方式设为「机器人+人工接待」。' },
      { q: 'Agent 回复发送失败？', a: '确认使用的是自建应用的 Secret（不是微信客服的 Secret，微信客服没有独立 Secret）、token 未过期、未超过 5 条消息限制。' },
    ],
  },
  dingtalk: {
    steps: [
      { title: '创建钉钉应用', desc: '登录钉钉开放平台，进入「开发者后台」→ 创建「企业内部开发」应用。', location: '开发者后台 → 创建应用' },
      { title: '启用机器人能力', desc: '在应用详情页点击「添加应用能力」→ 启用「机器人」。', location: '应用详情 → 添加应用能力' },
      { title: '获取应用凭证', desc: '在「基础信息」页面记录 AppKey 和 AppSecret。', location: '基础信息页面' },
      { title: '配置消息回调', desc: '在「事件与回调」中配置回调 URL，生成 Token 和 EncodingAESKey。添加 im.message.receive_v1 事件。', location: '事件与回调' },
      { title: '保存并验证', desc: '先在本页面保存凭证配置，再到钉钉后台完成回调验证。' },
    ],
    faq: [
      { q: '收不到消息？', a: '确认已启用机器人能力、已添加消息接收事件、回调 URL 配置正确。' },
    ],
  },
  feishu: {
    steps: [
      { title: '创建飞书应用', desc: '登录飞书开放平台，进入「开发者后台」→ 创建「企业自建应用」。', location: '开发者后台 → 创建应用' },
      { title: '启用机器人能力', desc: '在应用详情页点击「添加应用能力」→ 启用「机器人」。', location: '应用详情 → 添加应用能力' },
      { title: '获取应用凭证', desc: '在「凭证与基础信息」页面记录 App ID 和 App Secret。', location: '凭证与基础信息' },
      { title: '配置消息回调', desc: '在「事件与回调」中添加 im.message.receive_v1 事件，将回调地址填入请求地址，记录 Verification Token 和 Encrypt Key。', location: '事件与回调' },
      { title: '配置权限', desc: '在「权限管理」中申请「获取用户信息」和「发送消息」权限。', location: '权限管理' },
      { title: '保存并验证', desc: '先在本页面保存凭证配置，再到飞书后台完成回调验证。' },
    ],
    faq: [
      { q: '回调验证不通过？', a: '飞书使用 challenge 验证，系统会自动处理。确保 Encrypt Key 和 Verification Token 填写正确。' },
    ],
  },
}

const fullGuide = computed(() => fullGuideMap[guideChannel.value] || { steps: [] })

const guideFieldColumns = [
  { key: 'key', label: '字段', width: '160px' },
  { key: 'label', label: '说明' },
  { key: 'location', label: '获取位置', width: '200px' },
]

// ==================== 回调地址 ====================

function getCallbackUrl(channelType: string, configId?: string): string {
  const base = window.location.origin
  if (tenant.value) {
    if (configId) {
      return `${base}/t/${tenant.value.tenant_id}/${channelType}/callback/${configId}`
    }
    return `${base}/t/${tenant.value.tenant_id}/${channelType}/callback/{config_id}`
  }
  return `${base}/${channelType}/callback`
}

function subagentTypeLabel(type: string): string {
  // 将目录名转为人可读标签
  const map: Record<string, string> = {
    'travel-consultant': '旅游咨询顾问',
    'trade-specialist': '外贸获客智能体',
    'contract-archive-review': '合同档案审查',
  }
  return map[type] || type
}

function copyUrl(url: string, id?: string) {
  navigator.clipboard.writeText(url).then(() => {
    if (id) {
      copied.value[id] = true
      setTimeout(() => { copied.value[id] = false }, 2000)
    }
  })
}

// ==================== 操作 ====================

function openAddChannel() {
  editingId.value = null
  form.value = { channel_type: 'wecom', config: {}, subagent_type: '' }
  kfAccounts.value = []
  formError.value = ''
  formInitialSnapshot.value = JSON.parse(JSON.stringify(form.value))
  isFullscreen.value = false
  showForm.value = true
}

function editChannel(ch: any) {
  editingId.value = ch.config_id
  form.value = { channel_type: ch.channel_type, config: { ...ch.config }, subagent_type: ch.subagent_type || '' }
  formError.value = ''
  // 解析已有的客服账号配置（仅企业微信客服渠道）
  if (ch.channel_type === 'wecom_kf' && ch.config.kf_account && Array.isArray(ch.config.kf_account)) {
    kfAccounts.value = ch.config.kf_account.map((kf: any) => ({
      name: kf.name || '',
      open_kfid: kf.open_kfid || '',
      subagent_type: kf.subagent_type || '',
      welcome_message: kf.welcome_message || '',
      servicer_userid_list: Array.isArray(kf.servicer_userid_list) ? kf.servicer_userid_list.join(', ') : (kf.servicer_userid_list || ''),
      human_transfer_keywords: Array.isArray(kf.human_transfer_keywords) ? kf.human_transfer_keywords.join(', ') : (kf.human_transfer_keywords || ''),
    }))
  } else {
    kfAccounts.value = []
  }
  formInitialSnapshot.value = JSON.parse(JSON.stringify(form.value))
  isFullscreen.value = false
  showForm.value = true
}

function showGuide(ch: any) {
  guideChannel.value = ch.channel_type
  showGuideModal.value = true
}

async function loadChannels() {
  loading.value = true
  try {
    const res = await listChannels()
    channels.value = res.channels || []
  } catch (e) {
    console.error('加载渠道列表失败:', e)
  } finally {
    loading.value = false
  }
}

async function loadAvailableSubagents() {
  try {
    const res = await getAvailableSubagents()
    availableSubagents.value = res.subagents || []
  } catch (e) {
    console.error('加载数字员工列表失败:', e)
  }
}

async function handleSubmit() {
  submitting.value = true
  formError.value = ''
  try {
    const payload: Record<string, any> = {
      config: { ...form.value.config },
      subagent_type: form.value.subagent_type || undefined
    }
    // 微信客服特有：序列化客服账号配置
    if (form.value.channel_type === 'wecom_kf' && kfAccounts.value.length > 0) {
      payload.config.kf_account = kfAccounts.value.map(kf => {
        const obj: Record<string, any> = {
          name: kf.name,
          open_kfid: kf.open_kfid,
          subagent_type: kf.subagent_type || undefined,
        }
        if (kf.welcome_message) obj.welcome_message = kf.welcome_message
        if (kf.servicer_userid_list) {
          obj.servicer_userid_list = kf.servicer_userid_list.split(/[,，]/).map((s: string) => s.trim()).filter(Boolean)
        }
        if ('human_transfer_keywords' in kf) {
          obj.human_transfer_keywords = (kf.human_transfer_keywords || '').split(/[,，]/).map((s: string) => s.trim()).filter(Boolean)
        }
        return obj
      })
    }
    if (editingId.value) {
      await updateChannel(editingId.value, payload as any)
    } else {
      await createChannel({ channel_type: form.value.channel_type, config: payload.config, subagent_type: payload.subagent_type } as any)
    }
    closeModal()
    await loadChannels()
  } catch (e: any) {
    formError.value = e.message || '保存失败'
  } finally {
    submitting.value = false
  }
}

async function handleSaveAndClose() {
  await handleSubmit()
}

async function handleVerify(configId: string) {
  try {
    const res = await verifyChannel(configId)
    if (res.verified) {
      toast.success('验证通过！渠道凭证有效。')
    } else {
      toast.error('验证失败: ' + (res.message || '请检查凭证配置是否正确'))
    }
    await loadChannels()
  } catch (e: any) {
    toast.error(e.message || '验证失败')
  }
}

async function handleDelete(configId: string) {
  if (!confirm('确定要删除此渠道配置吗？删除后对应渠道将无法接收消息。')) return
  try {
    await deleteChannel(configId)
    toast.success('删除成功')
    await loadChannels()
  } catch (e: any) {
    toast.error(e.message || '删除失败')
  }
}

onMounted(() => {
  loadChannels()
  loadAvailableSubagents()
})
</script>
