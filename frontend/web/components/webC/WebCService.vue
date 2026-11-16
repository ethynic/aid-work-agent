<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <AppHeader
      title="官网接待客户"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    />

    <!-- 顶部 Tab 切换 -->
    <div class="border-b border-default bg-surface px-6">
      <div class="flex gap-6">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          :class="[
            'py-3 text-sm font-medium border-b-2 transition-colors',
            activeTab === tab.key
              ? 'text-primary-600 border-primary-600'
              : 'text-muted border-transparent hover:text-default hover:border-hover'
          ]"
          @click="switchTab(tab.key)"
        >
          {{ tab.label }}
        </button>
      </div>
    </div>

    <!-- Tab1：对话记录 -->
    <div v-show="activeTab === 'chat'" class="flex-1 overflow-hidden flex">
      <!-- 左侧：客户列表 -->
      <div class="w-96 border-r border-default bg-surface flex flex-col">
        <div class="p-4 border-b border-default">
          <div class="flex gap-2">
            <BaseInput
              v-model="searchUsername"
              placeholder="搜索客户名称"
              size="sm"
              clearable
              class="flex-1"
              @keyup.enter="handleSearch"
            />
            <BaseButton size="sm" @click="handleSearch">搜索</BaseButton>
          </div>
        </div>

        <div class="flex-1 overflow-y-auto">
          <div v-if="loadingUsers" class="flex items-center justify-center h-32">
            <span class="text-muted">加载中...</span>
          </div>
          <div v-else-if="userList.length === 0" class="flex items-center justify-center h-32">
            <span class="text-muted">暂无数据</span>
          </div>
          <div v-else>
            <div
              v-for="user in userList"
              :key="user.user_id"
              class="p-4 border-b border-default cursor-pointer transition-all"
              :class="selectedUserId === user.user_id ? 'bg-primary-50 border-l-4 border-l-primary-500' : 'hover:bg-surface-hover'"
              @click="selectUser(user)"
            >
              <div class="flex items-center gap-3">
                <img
                  :src="user.avatar_url || defaultAvatar"
                  class="w-11 h-11 rounded-full object-cover bg-gray-100 ring-2 ring-gray-200"
                  alt="头像"
                />
                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-2">
                    <span class="font-medium text-default truncate">{{ user.display_name || user.nickname || user.username || '官网访客' }}</span>
                    <span
                      v-if="user.channel_user_id"
                      class="text-xs px-2 py-0.5 rounded-full shrink-0"
                      :class="String(user.channel_user_id).startsWith('ext_') ? 'bg-primary-50 text-primary-600' : 'bg-gray-100 text-muted'"
                      :title="user.channel_user_id"
                    >
                      {{ String(user.channel_user_id).startsWith('ext_') ? '官网用户' : '匿名访客' }}
                    </span>
                  </div>
                  <div class="text-xs text-muted mt-1">{{ formatSessionDateRange(user) }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="p-3 border-t border-default">
          <BasePagination
            :total="userTotal"
            v-model:current-page="userPage"
            :page-size="userPageSize"
            :show-size-changer="false"
          />
        </div>
      </div>

      <!-- 右侧：会话与消息 -->
      <div class="flex-1 flex flex-col overflow-hidden">
        <div v-if="selectedUser" class="p-4 border-b border-default bg-surface">
          <div class="flex items-center gap-3">
            <img
              :src="selectedUser.avatar_url || defaultAvatar"
              class="w-10 h-10 rounded-full object-cover bg-gray-100"
              alt="头像"
            />
            <div class="min-w-0">
              <div class="font-medium text-default truncate">{{ selectedUser.display_name || selectedUser.nickname || selectedUser.username || '官网访客' }}</div>
              <div class="text-xs text-muted font-mono break-all whitespace-normal">{{ selectedUser.channel_user_id || selectedUser.user_id }}</div>
            </div>
            <BaseSelect v-if="sessionList.length > 1" v-model="selectedSessionId" size="sm" class="ml-auto w-56" @change="loadSessionMessages">
              <option v-for="s in sessionList" :key="s.session_id" :value="s.session_id">
                会话 {{ formatTime(s.updated_at || s.created_at || '') }}
              </option>
            </BaseSelect>
          </div>
        </div>

        <div class="flex-1 overflow-y-auto p-4" ref="messageContainerRef">
          <div v-if="!selectedUserId" class="flex items-center justify-center h-full">
            <p class="text-muted">请选择左侧客户查看对话记录</p>
          </div>
          <div v-else-if="loadingMessages" class="flex items-center justify-center h-full">
            <span class="text-muted">加载中...</span>
          </div>
          <div v-else-if="messageList.length === 0" class="flex items-center justify-center h-full">
            <p class="text-muted">暂无对话记录</p>
          </div>
          <div v-else class="space-y-4">
            <div
              v-for="msg in visibleMessages"
              :key="String(msg.message_id)"
              class="flex flex-col"
              :class="msg.role === 'user' ? 'items-end' : 'items-start'"
            >
              <div class="max-w-[70%] rounded-2xl px-4 py-2 text-sm"
                :class="msg.role === 'user'
                  ? 'bg-primary-500 text-white rounded-br-sm'
                  : 'bg-white border border-default text-default rounded-bl-sm shadow-sm'">
                <!-- 用户上传附件（图片预览 / 文件下载） -->
                <template v-if="msg.role === 'user' && getUserAttachments(msg).length">
                  <template v-for="(att, i) in getUserAttachments(msg)" :key="i">
                    <img
                      v-if="att.type === 'image'"
                      :src="attachmentUrl(att)"
                      class="max-w-[240px] max-h-[240px] rounded-lg cursor-pointer mb-1"
                      loading="lazy"
                      @click="previewImage(attachmentUrl(att))"
                      alt="用户图片"
                    />
                    <div v-else class="mt-1">
                      <AttachmentCard :attachment="toAttachmentCard(att)" :download-url="attachmentUrl(att)" />
                    </div>
                  </template>
                </template>
                <div v-if="msg.content" class="whitespace-pre-wrap">{{ msg.content }}</div>
                <!-- AI 消息图片 -->
                <div v-if="msg.role === 'assistant' && getAssistantImages(msg).length > 0" class="mt-2 flex flex-wrap gap-2">
                  <img
                    v-for="img in getAssistantImages(msg)"
                    :key="img.file_id"
                    :src="inlineFileUrl(img.file_id)"
                    :alt="img.display_name || '图片'"
                    loading="lazy"
                    class="max-w-[240px] max-h-[240px] rounded-lg cursor-pointer border border-default"
                    @click="previewImage(inlineFileUrl(img.file_id))"
                  />
                </div>
                <!-- AI 产出可下载文件 -->
                <div v-if="msg.role === 'assistant' && getDownloadableFiles(msg).length > 0" class="mt-3 flex flex-wrap gap-2">
                  <DownloadFileCard
                    v-for="file in getDownloadableFiles(msg)"
                    :key="file.file_id"
                    :file="file"
                  />
                </div>
              </div>
              <div class="text-xs text-muted mt-1 px-1">{{ formatTime(msg.created_at) }}</div>
            </div>
          </div>
        </div>

    <!-- 图片预览遮罩 -->
    <div v-if="previewUrl" class="fixed inset-0 z-50 bg-black/70 flex items-center justify-center" @click="previewUrl = ''">
      <img :src="previewUrl" class="max-w-[90vw] max-h-[90vh] rounded-lg" alt="预览" />
    </div>

        <div v-if="selectedUserId" class="p-3 border-t border-default bg-surface">
          <BasePagination
            :total="messageTotal"
            v-model:current-page="messagePage"
            :page-size="messagePageSize"
            :show-size-changer="false"
          />
        </div>
      </div>
    </div>

    <!-- Tab2：统计 -->
    <div v-show="activeTab === 'stats'" class="flex-1 overflow-y-auto p-6 bg-canvas">
      <div v-if="statsLoading" class="text-center py-12 text-muted">加载中...</div>
      <template v-else>
        <div class="flex flex-wrap items-center gap-4 mb-6 bg-surface border border-default rounded-lg p-4">
          <div class="flex gap-2">
            <BaseButton
              v-for="p in rangePresets"
              :key="p.key"
              :intent="activeRange === p.key ? 'primary' : 'ghost'"
              size="sm"
              @click="selectRange(p.key)"
            >
              {{ p.label }}
            </BaseButton>
          </div>
          <div v-if="activeRange === 'custom'" class="flex items-center gap-2">
            <input type="date" v-model="customStartDate" class="block h-8 px-2 rounded-lg border border-default bg-surface text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500" />
            <span class="text-muted text-sm">至</span>
            <input type="date" v-model="customEndDate" class="block h-8 px-2 rounded-lg border border-default bg-surface text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500" />
            <BaseButton size="sm" @click="applyCustomRange">查询</BaseButton>
          </div>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
          <div class="bg-surface border border-default rounded-lg p-5">
            <div class="text-muted text-sm">会话数</div>
            <div class="text-3xl font-bold text-default mt-1">{{ stats.total_sessions ?? 0 }}</div>
          </div>
          <div class="bg-surface border border-default rounded-lg p-5">
            <div class="text-muted text-sm">消息数</div>
            <div class="text-3xl font-bold text-default mt-1">{{ stats.total_messages ?? 0 }}</div>
          </div>
          <div class="bg-surface border border-default rounded-lg p-5">
            <div class="text-muted text-sm">活跃客户数</div>
            <div class="text-3xl font-bold text-default mt-1">{{ stats.active_users ?? 0 }}</div>
          </div>
        </div>

        <div class="bg-surface border border-default rounded-lg">
          <div class="px-5 py-3 border-b border-default font-medium text-default">按绑定数字员工</div>
          <div class="table-scroll-wrapper">
            <BaseTable :columns="subagentColumns" :data="stats.by_subagent || []" row-key="subagent_id">
              <template #subagent_name="{ row }">{{ row.subagent_name || row.subagent_id || '未绑定' }}</template>
              <template #empty>暂无数据</template>
            </BaseTable>
          </div>
        </div>
      </template>
    </div>

    <!-- Tab3：留资线索 -->
    <div v-show="activeTab === 'leads'" class="flex-1 overflow-y-auto p-6 bg-canvas">
      <div v-if="leadsLoading" class="text-center py-12 text-muted">加载中...</div>
      <template v-else>
        <div class="flex flex-wrap items-center gap-4 mb-6 bg-surface border border-default rounded-lg p-4">
          <div class="flex gap-2">
            <BaseButton
              v-for="p in rangePresets"
              :key="p.key"
              :intent="leadActiveRange === p.key ? 'primary' : 'ghost'"
              size="sm"
              @click="selectLeadRange(p.key)"
            >
              {{ p.label }}
            </BaseButton>
          </div>
          <div v-if="leadActiveRange === 'custom'" class="flex items-center gap-2">
            <input type="date" v-model="leadCustomStartDate" class="block h-8 px-2 rounded-lg border border-default bg-surface text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500" />
            <span class="text-muted text-sm">至</span>
            <input type="date" v-model="leadCustomEndDate" class="block h-8 px-2 rounded-lg border border-default bg-surface text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500" />
            <BaseButton size="sm" @click="applyLeadCustomRange">查询</BaseButton>
          </div>
          <span class="ml-auto text-xs text-muted">过滤基准：留资时间</span>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
          <div class="bg-surface border border-default rounded-lg p-5">
            <div class="text-muted text-sm">总留资数</div>
            <div class="text-3xl font-bold text-default mt-1">{{ leadStats.total_leads ?? 0 }}</div>
          </div>
          <div
            v-for="m in leadStats.by_contact_method || []"
            :key="m.contact_method"
            class="bg-surface border border-default rounded-lg p-5"
          >
            <div class="text-muted text-sm">{{ getMethodLabel(m.contact_method) }}</div>
            <div class="text-3xl font-bold text-default mt-1">{{ m.count }}</div>
            <div class="text-xs text-muted mt-1">占比 {{ formatRatio(m.ratio) }}</div>
          </div>
        </div>

        <div class="bg-surface border border-default rounded-lg">
          <div class="px-5 py-3 border-b border-default flex flex-wrap items-center gap-3">
            <span class="font-medium text-default">留资线索</span>
            <div class="ml-auto flex items-center gap-2">
              <BaseSelect v-model="leadStageFilter" size="sm" class="w-32" @change="loadLeads">
                <option value="">全部阶段</option>
                <option v-for="(s, key) in leadStageOptions" :key="key" :value="key">{{ s.label }}</option>
              </BaseSelect>
            </div>
          </div>
          <div class="table-scroll-wrapper">
            <BaseTable :columns="leadColumns" :data="leadList" row-key="lead_id" table-fixed>
              <template #seq="{ index }">{{ (leadPage - 1) * leadPageSize + index + 1 }}</template>
              <template #contact_method="{ row }">
                <BaseBadge intent="neutral">{{ getMethodLabel(row.contact_method) }}</BaseBadge>
              </template>
              <template #phone="{ row }">
                <span class="block truncate" :class="formatLeadContact(row) === '—' ? 'text-muted' : ''">{{ formatLeadContact(row) }}</span>
              </template>
              <template #stage="{ row }">
                <BaseBadge :intent="getStageInfo(row.stage).intent">{{ getStageInfo(row.stage).label }}</BaseBadge>
              </template>
              <template #created_at="{ row }">{{ formatTime(row.created_at) }}</template>
              <template #actions="{ row }">
                <BaseButton intent="ghost" size="sm" class="whitespace-nowrap text-xs" @click="openLeadDetail(row)">详情</BaseButton>
              </template>
              <template #empty>暂无留资线索</template>
            </BaseTable>
          </div>
          <div class="p-3 border-t border-default flex justify-center">
            <BasePagination
              :total="leadTotal"
              v-model:current-page="leadPage"
              :page-size="leadPageSize"
              :show-size-changer="false"
            />
          </div>
        </div>
      </template>
    </div>

    <!-- 留资线索详情弹框 -->
    <BaseModal v-model="leadDetailOpen" title="留资线索详情" size="md">
      <div v-if="leadDetail" class="space-y-3 text-sm">
        <div class="grid grid-cols-2 gap-4">
          <div>
            <div class="text-muted text-xs mb-1">客户姓名</div>
            <div class="text-default">{{ leadDetail.contact_name || '-' }}</div>
          </div>
          <div>
            <div class="text-muted text-xs mb-1">联系方式</div>
            <div class="text-default break-all whitespace-normal">{{ formatLeadContact(leadDetail) }}</div>
          </div>
          <div>
            <div class="text-muted text-xs mb-1">留资方式</div>
            <div class="text-default">{{ getMethodLabel(leadDetail.contact_method) }}</div>
          </div>
          <div>
            <div class="text-muted text-xs mb-1">阶段</div>
            <div class="text-default">{{ getStageInfo(leadDetail.stage).label }}</div>
          </div>
          <div>
            <div class="text-muted text-xs mb-1">来源</div>
            <div class="text-default">官网客服</div>
          </div>
          <div>
            <div class="text-muted text-xs mb-1">留资时间</div>
            <div class="text-default">{{ formatTime(leadDetail.created_at) }}</div>
          </div>
          <div class="col-span-2">
            <div class="text-muted text-xs mb-1">需求摘要（留资时点）</div>
            <div class="text-default whitespace-pre-wrap">{{ leadDetail.demand_summary || '-' }}</div>
          </div>
          <div class="col-span-2 flex items-center gap-2 pt-3 border-t border-default">
            <span class="text-muted text-xs">更新阶段</span>
            <BaseSelect v-model="leadDetailStage" size="sm" class="w-32">
              <option v-for="(s, key) in leadStageOptions" :key="key" :value="key">{{ s.label }}</option>
            </BaseSelect>
            <BaseButton size="sm" @click="saveLeadStage">保存</BaseButton>
          </div>
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="leadDetailOpen = false">关闭</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch, inject } from 'vue'
import AppHeader from '@/components/AppHeader.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import DownloadFileCard from '@/components/DownloadFileCard.vue'
import AttachmentCard from '@/components/saas/AttachmentCard.vue'
import {
  listWebCCustomers, listWebCCustomerSessions, listWebCSessionMessages,
  getWebCStats, getWebCLeadStats, listWebCLeads, getWebCLeadDetail, updateWebCLeadStage,
  type WebCCustomer, type WebCSession, type WebCLead
} from '@/api/webCCustomers'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { inlineFileUrl } from '@/utils/file'
import type { DownloadableFile, ImageRef } from '@/types'

const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()
const toggleSidebarFn = inject<() => void>('toggleSidebar')
const messageContainerRef = ref<HTMLElement | null>(null)
const defaultAvatar = 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0iI2JkYmRiZCI+PHBhdGggZD0iTTEyIDEyYTIyIDIyIDAgMSAwIDAtNCAyIDIgMCAwIDAgMCA0em0tNyA3YTcgNyAwIDAgMSAxNCAwIi8+PC9zdmc+'

const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)
const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone
  } : null
})

function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}
function handleLogout() {
  void tenantLogout()
}

// ============== Tab ==============
const tabs = [
  { key: 'chat', label: '对话记录' },
  { key: 'stats', label: '统计' },
  { key: 'leads', label: '留资线索' },
] as const
const activeTab = ref<'chat' | 'stats' | 'leads'>('chat')

function switchTab(key: 'chat' | 'stats' | 'leads') {
  activeTab.value = key
  if (key === 'stats' && !statsLoading.value && !statsLoaded.value) void loadStats()
  if (key === 'leads' && !leadsLoaded.value) void loadLeadStats()
  if (key === 'leads' && !leadsListLoaded.value) void loadLeads()
}

// ============== Tab1 对话记录 ==============
const searchUsername = ref('')
const userList = ref<WebCCustomer[]>([])
const userTotal = ref(0)
const userPage = ref(1)
const userPageSize = ref(20)
const loadingUsers = ref(false)
const selectedUser = ref<WebCCustomer | null>(null)
const selectedUserId = computed(() => selectedUser.value?.user_id || '')
const sessionList = ref<WebCSession[]>([])
const selectedSessionId = ref('')
const messageList = ref<Record<string, unknown>[]>([])
const messageTotal = ref(0)
const messagePage = ref(1)
const messagePageSize = ref(50)
const loadingMessages = ref(false)

async function loadUsers() {
  loadingUsers.value = true
  try {
    const result = await listWebCCustomers({ username: searchUsername.value || undefined, page: userPage.value, page_size: userPageSize.value })
    userList.value = result.users || []
    userTotal.value = result.total || 0
  } catch (e) {
    userList.value = []
    userTotal.value = 0
    console.error('前端日志：官网接待客户列表加载失败', e)
  } finally {
    loadingUsers.value = false
  }
}

function handleSearch() {
  userPage.value = 1
  void loadUsers()
}

watch(userPage, () => void loadUsers())

async function selectUser(user: WebCCustomer) {
  if (selectedUserId.value === user.user_id) return
  selectedUser.value = user
  messageList.value = []
  messageTotal.value = 0
  messagePage.value = 1
  try {
    const result = await listWebCCustomerSessions({ user_id: user.user_id, page: 1, page_size: 50 })
    sessionList.value = result.sessions || []
    if (sessionList.value.length) {
      selectedSessionId.value = sessionList.value[0].session_id
      await loadSessionMessages()
    } else {
      selectedSessionId.value = ''
    }
  } catch (e) {
    console.error('前端日志：官网客户会话列表加载失败', e)
    sessionList.value = []
  }
}

async function loadSessionMessages() {
  if (!selectedSessionId.value) return
  loadingMessages.value = true
  try {
    const result = await listWebCSessionMessages({
      session_id: selectedSessionId.value,
      page: messagePage.value,
      page_size: messagePageSize.value
    })
    // 倒序分页 -> 正序渲染
    const messages = (result.messages || []).slice().reverse()
    messageList.value = messages
    messageTotal.value = result.total || 0
  } catch (e) {
    console.error('前端日志：官网会话消息加载失败', e)
    messageList.value = []
  } finally {
    loadingMessages.value = false
  }
}

watch(messagePage, () => void loadSessionMessages())

const visibleMessages = computed(() =>
  (messageList.value as Record<string, unknown>[]).filter(msg => {
    if (msg.role === 'tool') return false
    if (msg.role === 'assistant' && !msg.content && !getDownloadableFiles(msg).length && !getAssistantImages(msg).length) return false
    if (msg.role === 'user' && !msg.content && !getUserAttachments(msg).length) return false
    return true
  }),
)

function getUserAttachments(msg: Record<string, unknown>): Record<string, unknown>[] {
  const atts = msg.attachments
  if (!Array.isArray(atts)) return []
  return atts.filter(a => (a as Record<string, unknown>).type !== 'image') as Record<string, unknown>[]
}

function getAssistantImages(msg: any): ImageRef[] {
  const atts = msg?.attachments
  if (!Array.isArray(atts)) return []
  return atts.filter(a => (a as Record<string, unknown>).type === 'image') as unknown as ImageRef[]
}

function getDownloadableFiles(msg: any): DownloadableFile[] {
  const atts = msg?.attachments
  if (!Array.isArray(atts)) return []
  return (atts as Record<string, unknown>[])
    .filter(a => (a as Record<string, unknown>).type !== 'image' && (a as Record<string, unknown>).file_id)
    .map(a => {
      const att = a as Record<string, unknown>
      return {
        file_id: String(att.file_id),
        file_name: String(att.name || att.file_name || att.file_id),
        file_size: Number(att.size || att.file_size || 0),
        download_url: String(att.download_url || inlineFileUrl(String(att.file_id))),
        mime_type: att.mime_type ? String(att.mime_type) : undefined
      }
    })
}

function attachmentUrl(att: Record<string, unknown>): string {
  const fileId = String(att.file_id || '')
  return fileId ? inlineFileUrl(fileId) : ''
}

// 图片预览（简单遮罩放大）
const previewUrl = ref('')
function previewImage(url: string) {
  previewUrl.value = url
}

function toAttachmentCard(att: Record<string, unknown>) {
  return {
    type: String(att.type || 'file'),
    media_id: String(att.file_id || ''),
    file_name: String(att.name || att.file_name || att.file_id || '附件'),
    mime_type: String(att.mime_type || ''),
    file_size: Number(att.size || att.file_size || 0),
    local_path: '',
    saved_at: ''
  }
}

function formatSessionDateRange(user: WebCCustomer): string {
  if (!user.first_session_at && !user.last_session_at) return '暂无会话'
  if (user.first_session_at === user.last_session_at) return formatDate(user.first_session_at)
  return `${formatDate(user.first_session_at)} ~ ${formatDate(user.last_session_at)}`
}

function formatDate(value?: string | null): string {
  if (!value) return '-'
  return String(value).slice(0, 10)
}

function formatTime(value?: unknown): string {
  if (!value) return '-'
  const raw = String(value)
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) return raw
  return `${formatDate(raw)} ${raw.slice(11, 16)}`
}

// ============== 日期段预设（Tab2/Tab3 共用） ==============
const rangePresets = [
  { key: 'today', label: '今天' },
  { key: 'yesterday', label: '昨天' },
  { key: '7d', label: '近7天' },
  { key: '30d', label: '近30天' },
  { key: 'custom', label: '自定义' },
] as const

function presetRange(key: string): { start_date: string; end_date: string } {
  const fmt = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  const today = new Date()
  if (key === 'today') return { start_date: fmt(today), end_date: fmt(today) }
  if (key === 'yesterday') {
    const y = new Date(today.getTime() - 86400_000)
    return { start_date: fmt(y), end_date: fmt(y) }
  }
  if (key === '7d') return { start_date: fmt(new Date(today.getTime() - 7 * 86400_000)), end_date: fmt(today) }
  if (key === '30d') return { start_date: fmt(new Date(today.getTime() - 30 * 86400_000)), end_date: fmt(today) }
  return { start_date: '', end_date: '' }
}

// ============== Tab2 统计 ==============
const stats = ref<{ total_sessions: number; total_messages: number; active_users: number; by_subagent: { subagent_id: string; subagent_name?: string; sessions: number }[] }>({
  total_sessions: 0, total_messages: 0, active_users: 0, by_subagent: []
})
const statsLoading = ref(false)
const statsLoaded = ref(false)
const activeRange = ref<'today' | 'yesterday' | '7d' | '30d' | 'custom'>('30d')
const customStartDate = ref('')
const customEndDate = ref('')
const subagentColumns = [
  { key: 'subagent_name', label: '数字员工' },
  { key: 'sessions', label: '会话数' },
]

async function loadStats() {
  statsLoading.value = true
  try {
    const range = activeRange.value === 'custom'
      ? { start_date: customStartDate.value, end_date: customEndDate.value }
      : presetRange(activeRange.value)
    stats.value = await getWebCStats(range)
    statsLoaded.value = true
  } catch (e) {
    console.error('前端日志：官网接待统计加载失败', e)
  } finally {
    statsLoading.value = false
  }
}

function selectRange(key: typeof activeRange.value) {
  activeRange.value = key
  if (key !== 'custom') void loadStats()
}

function applyCustomRange() {
  void loadStats()
}

// ============== Tab3 留资线索 ==============
const leadStats = ref<{ total_leads: number; by_contact_method: { contact_method: string; count: number; ratio: number }[] }>({ total_leads: 0, by_contact_method: [] })
const leadList = ref<WebCLead[]>([])
const leadTotal = ref(0)
const leadPage = ref(1)
const leadPageSize = ref(20)
const leadsLoading = ref(false)
const leadsLoaded = ref(false)
const leadsListLoaded = ref(false)
const leadActiveRange = ref<'today' | 'yesterday' | '7d' | '30d' | 'custom'>('30d')
const leadCustomStartDate = ref('')
const leadCustomEndDate = ref('')
const leadStageFilter = ref('')
const leadDetailOpen = ref(false)
const leadDetail = ref<Record<string, unknown> | null>(null)
const leadDetailStage = ref('')

type BadgeIntent = 'success' | 'primary' | 'danger' | 'info' | 'neutral' | 'warning'
const leadStageOptions: Record<string, { label: string; intent: BadgeIntent }> = {
  new: { label: '待跟进', intent: 'info' },
  contacting: { label: '跟进中', intent: 'warning' },
  converted: { label: '已转化', intent: 'success' },
  abandoned: { label: '已放弃', intent: 'neutral' },
}
const methodLabels: Record<string, string> = {
  phone: '手机号',
  landline: '固话',
  email: '邮箱',
  qr: '二维码',
}
const leadColumns = [
  { key: 'seq', label: '序号', width: '60px' },
  { key: 'contact_name', label: '客户姓名' },
  { key: 'phone', label: '联系方式' },
  { key: 'contact_method', label: '联系方式类型', width: '110px' },
  { key: 'stage', label: '阶段', width: '90px' },
  { key: 'created_at', label: '留资时间', width: '160px' },
  { key: 'actions', label: '操作', width: '90px' },
]

function getMethodLabel(method: any): string {
  return methodLabels[String(method)] || String(method)
}
function getStageInfo(stage: any): { label: string; intent: BadgeIntent } {
  return leadStageOptions[String(stage)] || { label: String(stage || '-'), intent: 'neutral' }
}
function formatRatio(ratio?: number): string {
  return typeof ratio === 'number' ? `${ratio}%` : '-'
}
function formatLeadContact(lead: any): string {
  if (lead?.phone) return String(lead.phone)
  const other = lead?.other_contact as Record<string, unknown> | null
  if (other) {
    if (other.landline) return String(other.landline)
    if (other.email) return String(other.email)
  }
  return '—'
}

async function loadLeadStats() {
  try {
    const range = leadActiveRange.value === 'custom'
      ? { start_date: leadCustomStartDate.value, end_date: leadCustomEndDate.value }
      : presetRange(leadActiveRange.value)
    leadStats.value = await getWebCLeadStats(range)
    leadsLoaded.value = true
  } catch (e) {
    console.error('前端日志：官网留资统计加载失败', e)
  }
}

async function loadLeads() {
  leadsLoading.value = true
  try {
    const range = leadActiveRange.value === 'custom'
      ? { start_date: leadCustomStartDate.value, end_date: leadCustomEndDate.value }
      : presetRange(leadActiveRange.value)
    const result = await listWebCLeads({ ...range, stage: leadStageFilter.value || undefined, page: leadPage.value, page_size: leadPageSize.value })
    leadList.value = result.leads || []
    leadTotal.value = result.total || 0
    leadsListLoaded.value = true
  } catch (e) {
    console.error('前端日志：官网留资线索加载失败', e)
    leadList.value = []
  } finally {
    leadsLoading.value = false
  }
}

function selectLeadRange(key: typeof leadActiveRange.value) {
  leadActiveRange.value = key
  if (key !== 'custom') {
    void loadLeadStats()
    void loadLeads()
  }
}

function applyLeadCustomRange() {
  void loadLeadStats()
  void loadLeads()
}

watch(leadPage, () => void loadLeads())

async function openLeadDetail(row: any) {
  try {
    const result = await getWebCLeadDetail(String(row.lead_id))
    leadDetail.value = result.lead as unknown as Record<string, unknown>
    leadDetailStage.value = String((result.lead as unknown as Record<string, unknown>).stage || 'new')
    leadDetailOpen.value = true
  } catch (e) {
    console.error('前端日志：官网留资详情加载失败', e)
  }
}

async function saveLeadStage() {
  if (!leadDetail.value) return
  try {
    const result = await updateWebCLeadStage(String((leadDetail.value as Record<string, unknown>).lead_id), leadDetailStage.value)
    leadDetail.value = result.lead as unknown as Record<string, unknown>
    await loadLeads()
    await loadLeadStats()
  } catch (e) {
    console.error('前端日志：官网留资阶段更新失败', e)
  }
}

onMounted(() => {
  void loadUsers()
})
</script>
