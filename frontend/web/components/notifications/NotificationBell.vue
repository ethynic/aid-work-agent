<template>
  <div class="relative">
    <button
      @click="togglePanel"
      class="relative p-2 min-w-[40px] min-h-[40px] flex items-center justify-center text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors flex-shrink-0"
      title="站内通知"
    >
      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M15 17h5l-1.4-1.4A2 2 0 0118 14.2V11a6 6 0 00-4-5.7V5a2 2 0 10-4 0v.3A6 6 0 006 11v3.2a2 2 0 01-.6 1.4L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
      </svg>
      <span
        v-if="unreadCount > 0"
        class="absolute top-0.5 right-0.5 min-w-[16px] h-4 px-1 rounded-full bg-danger-500 text-white text-[10px] leading-4 text-center font-medium"
      >{{ unreadCount > 99 ? '99+' : unreadCount }}</span>
    </button>

    <div
      v-if="panelOpen"
      class="absolute right-0 top-full mt-2 w-[21rem] max-w-[calc(100vw-2rem)] bg-white border border-gray-200 rounded-lg shadow-lg z-50 flex flex-col max-h-[70vh]"
    >
      <div class="flex items-center justify-between px-4 py-2.5 border-b border-gray-100 flex-shrink-0">
        <span class="text-sm font-semibold text-gray-800">站内通知</span>
        <button
          @click="loadInbox"
          class="text-xs text-gray-400 hover:text-primary-600 transition-colors"
        >刷新</button>
      </div>
      <div class="flex-1 overflow-y-auto">
        <div v-if="loading" class="px-4 py-8 text-center text-xs text-gray-400">加载中...</div>
        <div v-else-if="items.length === 0" class="px-4 py-8 text-center text-xs text-gray-400">暂无通知</div>
        <button
          v-for="item in items"
          :key="item.notification_ref"
          @click="handleRead(item)"
          class="w-full text-left px-4 py-3 border-b border-gray-50 hover:bg-gray-50 transition-colors"
        >
          <div class="flex items-center gap-2">
            <span
              v-if="!item.read_at"
              class="w-1.5 h-1.5 rounded-full bg-danger-500 flex-shrink-0"
            ></span>
            <span class="text-sm font-medium text-gray-800 truncate flex-1" :class="{ 'text-gray-500': item.read_at }">
              {{ item.title }}
            </span>
            <span class="text-[10px] text-gray-400 flex-shrink-0">{{ formatTime(item.created_at) }}</span>
          </div>
          <p class="mt-1 text-xs text-gray-500 leading-relaxed line-clamp-3">{{ item.body }}</p>
        </button>
      </div>
    </div>

    <div v-if="panelOpen" class="fixed inset-0 z-40" @click="panelOpen = false"></div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { notificationsAPI, type InboxItem } from '@/api/notifications'

const router = useRouter()
const panelOpen = ref(false)
const loading = ref(false)
const items = ref<InboxItem[]>([])
const unreadCount = ref(0)
let pollTimer: number | null = null
let pollingDisabled = false

async function loadUnreadCount() {
  if (pollingDisabled) return
  try {
    const res = await notificationsAPI.inbox({ limit: 1 })
    unreadCount.value = res.unread_count
  } catch (e: any) {
    if (e?.code === 'NOTIFICATION_DISABLED') {
      // 通知核心未开启的环境停止轮询，避免无意义请求
      pollingDisabled = true
      return
    }
    console.log('临时调试：未读数获取失败', e)
  }
}

async function loadInbox() {
  loading.value = true
  try {
    const res = await notificationsAPI.inbox({ limit: 20 })
    items.value = res.items
    unreadCount.value = res.unread_count
  } catch (e) {
    console.error('加载站内通知失败:', e)
  } finally {
    loading.value = false
  }
}

function togglePanel() {
  panelOpen.value = !panelOpen.value
  if (panelOpen.value) {
    loadInbox()
  }
}

async function handleRead(item: InboxItem) {
  if (!item.read_at) {
    try {
      await notificationsAPI.markRead(item.notification_ref)
      item.read_at = new Date().toISOString()
      unreadCount.value = Math.max(0, unreadCount.value - 1)
    } catch (e) {
      console.error('标记已读失败:', e)
    }
  }
  if (item.link) {
    panelOpen.value = false
    router.push(item.link)
  }
}

function formatTime(value: string): string {
  const date = new Date(value)
  if (isNaN(date.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

onMounted(() => {
  loadUnreadCount()
  pollTimer = window.setInterval(loadUnreadCount, 60_000)
})

onUnmounted(() => {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
})
</script>
