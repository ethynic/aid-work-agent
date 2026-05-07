<template>
  <div class="instance-lobby min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 p-6">
    <!-- Header -->
    <div class="max-w-7xl mx-auto mb-8">
      <h1 class="text-2xl font-bold text-slate-800 mb-2">我的数字员工</h1>
      <p class="text-slate-500">选择一位数字员工开始对话，或排队等待忙碌的员工</p>
    </div>

    <!-- Instance Grid -->
    <div class="max-w-7xl mx-auto">
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        <!-- Instance Card -->
        <div
          v-for="instance in instances"
          :key="instance.instance_id"
          class="instance-card bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden hover:shadow-md transition-all duration-300"
          :class="{
            'ring-2 ring-green-500': instance.status === 'idle',
            'ring-2 ring-amber-400': instance.status === 'busy',
          }"
        >
          <!-- Card Header -->
          <div class="p-6">
            <div class="flex items-start justify-between mb-4">
              <!-- Avatar -->
              <div
                class="w-16 h-16 rounded-2xl bg-gradient-to-br flex items-center justify-center text-3xl"
                :class="instance.status === 'idle' ? 'from-green-400 to-emerald-500' : 'from-amber-400 to-orange-500'"
              >
                {{ instance.avatar || '🤖' }}
              </div>

              <!-- Status Badge -->
              <div class="flex items-center gap-2">
                <span
                  class="status-dot inline-block w-3 h-3 rounded-full"
                  :class="getStatusColor(instance.status)"
                ></span>
                <span
                  class="text-sm font-medium"
                  :class="getStatusTextColor(instance.status)"
                >
                  {{ instance.status_text || getStatusText(instance.status) }}
                </span>
              </div>
            </div>

            <!-- Instance Name -->
            <h3 class="text-xl font-bold text-slate-800 mb-1">
              {{ instance.instance_name || instance.display_name }}
            </h3>

            <!-- Description -->
            <p class="text-slate-500 text-sm mb-4 line-clamp-2">
              {{ instance.description || '您的智能数字助理' }}
            </p>

            <!-- Personality Tags -->
            <div v-if="instance.personality_traits?.length" class="flex flex-wrap gap-2 mb-4">
              <span
                v-for="trait in instance.personality_traits"
                :key="trait"
                class="px-2 py-1 bg-slate-100 text-slate-600 text-xs rounded-full"
              >
                {{ trait }}
              </span>
            </div>

            <!-- Busy Info -->
            <div v-if="instance.status === 'busy' && instance.current_user" class="mb-4 p-3 bg-amber-50 rounded-lg">
              <div class="flex items-center gap-2 text-sm text-amber-700">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"
                  />
                </svg>
                <span>{{ instance.current_user.name }} 正在使用中</span>
              </div>
              <div class="text-xs text-amber-600 mt-1">
                已占用 {{ formatDuration(instance.current_user.held_seconds) }}
              </div>
            </div>

            <!-- Queue Info -->
            <div
              v-if="instance.queue_length && instance.queue_length > 0"
              class="mb-4 flex items-center gap-2 text-sm text-slate-500"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2M7 20v-2M7 20H2a3 3 0 005.356-1.857M7 20h10a3 3 0 005.356-1.857"
                />
              </svg>
              <span>{{ instance.queue_length }} 人正在排队</span>
            </div>

            <!-- Stats -->
            <div class="flex gap-4 mb-4 text-xs text-slate-400">
              <span>累计对话 {{ instance.total_chats || 0 }} 次</span>
              <span>创建于 {{ formatDate(instance.created_at) }}</span>
            </div>

            <!-- Action Buttons -->
            <div class="flex gap-3">
              <!-- Idle: Start Chat -->
              <button
                v-if="instance.status === 'idle'"
                @click="startChat(instance)"
                class="flex-1 py-2.5 px-4 bg-gradient-to-r from-green-500 to-emerald-600 text-white font-medium rounded-xl hover:from-green-600 hover:to-emerald-700 transition-all shadow-md shadow-green-200"
              >
                开始对话
              </button>

              <!-- Busy: Queue / Take Over -->
              <template v-else-if="instance.status === 'busy'">
                <button
                  v-if="instance.can_take_over"
                  @click="takeOver(instance)"
                  class="flex-1 py-2.5 px-4 bg-gradient-to-r from-blue-500 to-indigo-600 text-white font-medium rounded-xl hover:from-blue-600 hover:to-indigo-700 transition-all shadow-md shadow-blue-200"
                >
                  接管对话
                </button>
                <button
                  v-else
                  @click="queueForInstance(instance)"
                  class="flex-1 py-2.5 px-4 bg-gradient-to-r from-amber-500 to-orange-600 text-white font-medium rounded-xl hover:from-amber-600 hover:to-orange-700 transition-all shadow-md shadow-amber-200"
                >
                  排队等待
                </button>
              </template>

            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Queue Modal -->
    <InstanceQueueModal
      v-if="showQueueModal"
      v-model:visible="showQueueModal"
      :instance="queueingInstance!"
      :session-id="currentSessionId"
      @queued="handleQueued"
      @ready="handleQueueReady"
      @cancelled="showQueueModal = false"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useToast } from 'vue-toastification'
import {
  listInstances,
  lockInstance,
  takeOverInstance,
  type ChatInstance,
} from '@/api/chatInstances'
import InstanceQueueModal from './InstanceQueueModal.vue'

function generateSessionId(): string {
  return 'session_' + Date.now() + '_' + Math.random().toString(36).substring(2, 9)
}

const router = useRouter()
const route = useRoute()
const toast = useToast()

const instances = ref<ChatInstance[]>([])
const loading = ref(false)
const showQueueModal = ref(false)
const queueingInstance = ref<ChatInstance | null>(null)
const currentSessionId = ref('')

// 检测是否为租户模式
const isTenantMode = computed(() => route.path.startsWith('/t/'))

// 获取当前租户ID（如果是租户模式）
const tenantId = computed(() => {
  const match = route.path.match(/^\/t\/([^\/]+)/)
  return match ? match[1] : null
})

// 构建对话页面路径（兼容租户模式和演示模式）
function buildChatPath(subagentType?: string | null): string {
  if (isTenantMode.value && tenantId.value) {
    return subagentType
      ? `/t/${tenantId.value}/chat/${subagentType}`
      : `/t/${tenantId.value}/chat`
  }
  return subagentType ? `/chat/${subagentType}` : '/'
}

/**
 * 加载实例列表
 */
async function loadInstances() {
  try {
    loading.value = true
    const res = await listInstances()
    if (res.success) {
      instances.value = res.instances
    }
  } catch (e) {
    toast.error('加载实例列表失败')
    console.error(e)
  } finally {
    loading.value = false
  }
}

/**
 * 开始对话 - 空闲实例
 */
async function startChat(instance: ChatInstance) {
  const sessionId = generateSessionId()
  currentSessionId.value = sessionId

  try {
    const result = await lockInstance(instance.instance_id, sessionId)
    if (result.success) {
      toast.success('已锁定实例，正在进入对话')
      router.push({
        path: buildChatPath(instance.subagent_type),
        query: {
          instance_id: instance.instance_id,
          _sid: sessionId,
        },
      })
    } else {
      toast.error(result.error || '锁定实例失败')
    }
  } catch (e) {
    toast.error('锁定实例失败')
    console.error(e)
  }
}

/**
 * 排队等待 - 忙碌实例
 */
async function queueForInstance(instance: ChatInstance) {
  const sessionId = generateSessionId()
  queueingInstance.value = instance
  currentSessionId.value = sessionId

  try {
    // 调用 lockInstance，对于忙碌实例会自动进入排队
    const result = await lockInstance(instance.instance_id, sessionId)
    if (result.is_queued) {
      handleQueued(result.queue_position || 0)
      showQueueModal.value = true
    } else if (result.success) {
      // 意外地直接获得了锁（可能实例刚好被释放），直接进入对话
      toast.success('已锁定实例，正在进入对话')
      router.push({
        path: buildChatPath(instance.subagent_type),
        query: {
          instance_id: instance.instance_id,
          _sid: sessionId,
        },
      })
    } else {
      toast.error(result.error || '加入队列失败')
    }
  } catch (e) {
    toast.error('加入队列失败')
    console.error(e)
  }
}

/**
 * 接管实例（跨设备）
 */
async function takeOver(instance: ChatInstance) {
  const sessionId = generateSessionId()
  currentSessionId.value = sessionId

  try {
    const result = await takeOverInstance(instance.instance_id, sessionId)
    if (result.success) {
      toast.success('已接管对话')
      router.push({
        path: buildChatPath(instance.subagent_type),
        query: {
          instance_id: instance.instance_id,
          _sid: sessionId,
        },
      })
    } else {
      toast.error(result.error || '接管失败')
    }
  } catch (e) {
    toast.error('接管失败')
    console.error(e)
  }
}

/**
 * 排队成功回调
 */
function handleQueued(position: number) {
  toast.info(`已加入队列，当前排名第 ${position + 1} 位`)
}

/**
 * 排到队首，准备开始对话
 */
function handleQueueReady() {
  showQueueModal.value = false
  if (queueingInstance.value) {
    toast.success('轮到您了，正在进入对话')
    router.push({
      path: buildChatPath(queueingInstance.value.subagent_type),
      query: {
        instance_id: queueingInstance.value.instance_id,
        _sid: currentSessionId.value,
      },
    })
  }
}

/**
 * 获取状态颜色
 */
function getStatusColor(status: string): string {
  if (status === 'idle') return 'bg-green-400 animate-pulse'
  if (status === 'busy') return 'bg-amber-400'
  return 'bg-slate-400'
}

function getStatusTextColor(status: string): string {
  if (status === 'idle') return 'text-green-600'
  if (status === 'busy') return 'text-amber-600'
  return 'text-slate-500'
}

function getStatusText(status: string) {
  return {
    idle: '空闲可用',
    busy: '忙碌中',
  }[status] || status
}

function formatDuration(seconds: number) {
  if (seconds < 60) return `${seconds} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`
  return `${Math.floor(seconds / 3600)} 小时`
}

function formatDate(dateStr: string) {
  const d = new Date(dateStr)
  return d.toLocaleDateString('zh-CN', {
    month: 'numeric',
    day: 'numeric',
  })
}

onMounted(() => {
  loadInstances()
})
</script>

<style scoped>
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
