<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <!-- Header Bar -->
    <AppHeader
      title="本地工具"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    >
    </AppHeader>

    <!-- Main Content -->
    <div class="flex-1 overflow-y-auto p-6">
      <div class="max-w-4xl space-y-6">

        <!-- 使用引导卡 -->
        <BaseCard title="使用引导">
          <ol class="list-decimal list-inside space-y-2 text-sm text-default">
            <li>下载并在你的电脑上启动本地工具 Runtime（仅支持 Windows，需保持电脑未锁屏）。</li>
            <li>点击下方「生成配对码」按钮获取配对码。</li>
            <li>在 Runtime 中输入配对码完成绑定，然后在设备列表中「选定」该设备。</li>
          </ol>
          <div class="mt-4 space-y-2">
            <div class="flex items-center gap-2">
              <code class="flex-1 px-3 py-2 bg-gray-100 rounded-lg text-xs font-mono text-default truncate">agent-tool-runtime pair --code {{ pairCommandCode }}</code>
              <BaseButton intent="secondary" size="sm" @click="copyText(`agent-tool-runtime pair --code ${pairCommandCode}`)">复制</BaseButton>
            </div>
            <div class="flex items-center gap-2">
              <code class="flex-1 px-3 py-2 bg-gray-100 rounded-lg text-xs font-mono text-default truncate">agent-tool-runtime start</code>
              <BaseButton intent="secondary" size="sm" @click="copyText('agent-tool-runtime start')">复制</BaseButton>
            </div>
          </div>
        </BaseCard>

        <!-- 配对码区 -->
        <BaseCard title="设备配对">
          <div v-if="!ticket" class="flex items-center gap-4">
            <BaseButton :disabled="generatingTicket" @click="handleGenerateTicket">
              {{ generatingTicket ? '生成中...' : '生成配对码' }}
            </BaseButton>
            <span class="text-sm text-muted">配对码 5 分钟内有效，仅可配对一台设备</span>
          </div>
          <div v-else>
            <div class="flex items-center gap-4 flex-wrap">
              <span
                class="text-3xl font-mono font-semibold tracking-widest"
                :class="ticketExpired ? 'text-muted line-through' : 'text-primary-600'"
              >{{ ticket.code }}</span>
              <BaseButton intent="secondary" size="sm" :disabled="ticketExpired" @click="copyText(ticket!.code)">复制</BaseButton>
              <BaseButton intent="ghost" size="sm" :disabled="generatingTicket" @click="handleGenerateTicket">重新生成</BaseButton>
            </div>
            <div class="mt-3 text-sm">
              <span v-if="ticketExpired" class="text-danger-600">配对码已过期，请重新生成</span>
              <span v-else class="text-muted">剩余有效时间：<span class="font-mono text-default">{{ countdownText }}</span></span>
            </div>
            <p class="mt-2 text-xs text-muted">配对码只显示这一次，请立即在 Runtime 中输入完成配对；关闭或刷新页面后无法再次查看。</p>
          </div>
        </BaseCard>

        <!-- 设备列表 -->
        <BaseCard>
          <template #header>
            <div class="flex items-center justify-between">
              <h3 class="text-base font-semibold text-default">我的设备</h3>
              <BaseButton intent="ghost" size="sm" :disabled="loading" @click="loadDevices">刷新</BaseButton>
            </div>
          </template>

          <div v-if="loading && devices.length === 0" class="text-center py-12 text-muted">加载中...</div>

          <div v-else-if="devices.length === 0" class="text-center py-12 text-muted">
            <p class="text-lg mb-2">暂无已配对的设备</p>
            <p class="text-sm">按上方「使用引导」生成配对码，并在本机 Runtime 中完成配对</p>
          </div>

          <div v-else class="table-scroll-wrapper">
            <BaseTable :columns="columns" :data="devices" row-key="device_id">
              <template #index="{ index }">{{ index + 1 }}</template>
              <template #name="{ row }">{{ row.name || '-' }}</template>
              <template #platform="{ row }">{{ row.platform || '-' }}</template>
              <template #online="{ row }">
                <BaseBadge :intent="row.online ? 'success' : 'neutral'" size="sm">
                  {{ row.online ? '在线' : '离线' }}
                </BaseBadge>
              </template>
              <template #selected="{ row }">
                <BaseBadge v-if="row.selected" intent="primary" size="sm">使用中</BaseBadge>
                <span v-else class="text-muted">-</span>
              </template>
              <template #last_seen_at="{ row }">{{ formatTime(row.last_seen_at) }}</template>
              <template #runtime_version="{ row }">{{ row.runtime_version || '-' }}</template>
              <template #actions="{ row }">
                <div class="flex gap-2 justify-center">
                  <BaseButton
                    intent="ghost" size="sm" class="whitespace-nowrap text-xs"
                    :disabled="row.selected || !row.online"
                    @click="handleSelect(row)"
                  >选定</BaseButton>
                  <BaseButton
                    intent="danger-ghost" size="sm" class="whitespace-nowrap text-xs"
                    @click="handleRevoke(row)"
                  >解绑</BaseButton>
                </div>
              </template>
            </BaseTable>
          </div>
        </BaseCard>

      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, inject } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppHeader from '@/components/AppHeader.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import type { TableColumn } from '@/components/ui/BaseTable.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import {
  listDevices,
  createPairingTicket,
  selectDevice,
  revokeDevice,
  type LocalToolDevice,
  type PairingTicket,
} from '@/api/localTools'

const route = useRoute()
const router = useRouter()
const tenantId = computed(() => route.params.tenant_id as string)
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()

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

// 从 PortalLayout 注入侧边栏控制
const sidebarCollapsed = inject<{ value: boolean }>('sidebarCollapsed')
const toggleSidebarFn = inject<() => void>('toggleSidebar')

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

// ==================== 设备列表 ====================

const devices = ref<LocalToolDevice[]>([])
const loading = ref(false)

const columns: TableColumn[] = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'name', label: '设备名称' },
  { key: 'platform', label: '平台', width: '120px' },
  { key: 'online', label: '在线状态', width: '90px' },
  { key: 'selected', label: '当前设备', width: '90px' },
  { key: 'last_seen_at', label: '最近在线时间', width: '170px', tooltip: (row) => formatTime(row.last_seen_at) },
  { key: 'runtime_version', label: '版本', width: '100px' },
  { key: 'actions', label: '操作', width: '130px', thAlign: 'center', tooltip: () => undefined },
]

async function loadDevices() {
  loading.value = true
  try {
    devices.value = await listDevices()
  } catch (e: any) {
    alert(e?.message || '查询设备列表失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

// BaseTable slot 行类型为 Record<string, any>，此处按 LocalToolDevice 处理
async function handleSelect(row: Record<string, any>) {
  const device = row as LocalToolDevice
  try {
    await selectDevice(device.device_id)
    await loadDevices()
  } catch (e: any) {
    alert(e?.message || '选定设备失败，请稍后重试')
  }
}

async function handleRevoke(row: Record<string, any>) {
  const device = row as LocalToolDevice
  if (!confirm(`确定解绑设备「${device.name || device.device_id}」？解绑后该设备的 Runtime 将无法继续使用。`)) return
  try {
    await revokeDevice(device.device_id)
    await loadDevices()
  } catch (e: any) {
    alert(e?.message || '解绑设备失败，请稍后重试')
  }
}

// 后端返回本地时间字符串（无 Z 后缀），直接按本地时间解析，禁止拼接 Z
function formatTime(value: string | null): string {
  if (!value) return '-'
  const d = new Date(value)
  if (isNaN(d.getTime())) return '-'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

// ==================== 配对码 ====================

const ticket = ref<PairingTicket | null>(null)
const generatingTicket = ref(false)
const nowTs = ref(Date.now())
let countdownTimer: ReturnType<typeof setInterval> | null = null

// 引导命令中的配对码占位：已生成则带真实码，否则用占位符
const pairCommandCode = computed(() => ticket.value && !ticketExpired.value ? ticket.value.code : '<配对码>')

const ticketExpireTs = computed(() => {
  if (!ticket.value?.expires_at) return 0
  const ts = new Date(ticket.value.expires_at).getTime()
  return isNaN(ts) ? 0 : ts
})

const ticketExpired = computed(() => ticketExpireTs.value > 0 && nowTs.value >= ticketExpireTs.value)

const countdownText = computed(() => {
  const remain = Math.max(0, Math.floor((ticketExpireTs.value - nowTs.value) / 1000))
  const m = Math.floor(remain / 60)
  const s = remain % 60
  return `${m}:${String(s).padStart(2, '0')}`
})

async function handleGenerateTicket() {
  generatingTicket.value = true
  try {
    ticket.value = await createPairingTicket()
    nowTs.value = Date.now()
  } catch (e: any) {
    alert(e?.message || '创建配对码失败，请稍后重试')
  } finally {
    generatingTicket.value = false
  }
}

// ==================== 复制 ====================

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    alert('复制失败，请手动选择文本复制')
  }
}

// ==================== 生命周期 ====================

onMounted(() => {
  loadDevices()
  countdownTimer = setInterval(() => {
    nowTs.value = Date.now()
  }, 1000)
})

onUnmounted(() => {
  if (countdownTimer) {
    clearInterval(countdownTimer)
    countdownTimer = null
  }
})
</script>
