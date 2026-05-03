<template>
  <Transition name="modal">
    <div v-if="visible" class="fixed inset-0 z-50 flex items-center justify-center p-4">
      <!-- Backdrop -->
      <div class="absolute inset-0 bg-black/50" @click="handleCancel"></div>

      <!-- Modal Content -->
      <div class="relative bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden">
        <!-- Header -->
        <div class="p-6 border-b border-slate-100">
          <div class="flex items-center justify-between">
            <h3 class="text-xl font-bold text-slate-800">
              {{ isReady ? '轮到您了' : '排队等待中' }}
            </h3>
            <button
              @click="handleCancel"
              class="w-8 h-8 flex items-center justify-center rounded-full hover:bg-slate-100 text-slate-400 hover:text-slate-600 transition-colors"
            >
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        <!-- Body -->
        <div class="p-6">
          <!-- Instance Info -->
          <div v-if="instance" class="flex items-center gap-4 mb-6 p-4 bg-slate-50 rounded-xl">
            <div
              class="w-12 h-12 rounded-xl bg-gradient-to-br from-amber-400 to-orange-500 flex items-center justify-center text-xl"
            >
              {{ instance.avatar || '🤖' }}
            </div>
            <div>
              <div class="font-semibold text-slate-800">
                {{ instance.instance_name || instance.display_name }}
              </div>
              <div class="text-sm text-slate-500">数字员工</div>
            </div>
          </div>

          <!-- Ready State -->
          <div v-if="isReady" class="text-center py-4">
            <div class="w-20 h-20 mx-auto mb-4 rounded-full bg-green-100 flex items-center justify-center">
              <svg class="w-10 h-10 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <p class="text-lg font-semibold text-green-600 mb-2">轮到您对话了！</p>
            <p class="text-sm text-slate-500 mb-6">正在为您准备对话，请稍候...</p>
            <div class="animate-pulse flex justify-center">
              <div class="w-2 h-2 bg-green-400 rounded-full mx-1"></div>
              <div class="w-2 h-2 bg-green-400 rounded-full mx-1"></div>
              <div class="w-2 h-2 bg-green-400 rounded-full mx-1"></div>
            </div>
          </div>

          <!-- Waiting State -->
          <div v-else class="text-center py-4">
            <!-- Position Badge -->
            <div class="relative inline-block mb-4">
              <div class="w-24 h-24 rounded-full bg-gradient-to-br from-amber-100 to-orange-100 flex items-center justify-center">
                <span class="text-3xl font-bold text-amber-600">{{ displayPosition }}</span>
              </div>
              <div class="absolute -bottom-1 left-1/2 -translate-x-1/2 bg-amber-500 text-white text-xs px-3 py-1 rounded-full">
                第 {{ displayPosition }} 位
              </div>
            </div>

            <p class="text-lg font-semibold text-slate-800 mb-1">您正在排队中</p>
            <p class="text-sm text-slate-500 mb-6">前面还有 {{ displayPosition - 1 }} 人在等待</p>

            <!-- Progress Bar -->
            <div class="mb-6">
              <div class="flex justify-between text-xs text-slate-400 mb-2">
                <span>等待中</span>
                <span>您的位置</span>
              </div>
              <div class="h-2 bg-slate-100 rounded-full overflow-hidden">
                <div
                  class="h-full bg-gradient-to-r from-amber-400 to-orange-500 transition-all duration-500"
                  :style="{ width: progressWidth }"
                ></div>
              </div>
            </div>

            <!-- Estimated Wait Time -->
            <div v-if="estimatedWaitSeconds > 0" class="flex items-center justify-center gap-2 text-sm text-slate-500 mb-6">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <span>预计等待约 {{ formatWaitTime(estimatedWaitSeconds) }}</span>
            </div>

            <!-- Queue Status Indicator -->
            <div class="flex items-center justify-center gap-2 text-xs text-slate-400">
              <div class="w-2 h-2 rounded-full bg-green-400 animate-pulse"></div>
              <span>实时更新中</span>
            </div>
          </div>
        </div>

        <!-- Footer -->
        <div class="p-6 border-t border-slate-100">
          <button
            v-if="!isReady"
            @click="handleCancel"
            class="w-full py-3 px-4 bg-slate-100 hover:bg-slate-200 text-slate-700 font-medium rounded-xl transition-colors"
          >
            取消排队
          </button>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { ref, computed, watch, onUnmounted } from 'vue'
import { checkQueueStatus, cancelQueue } from '@/api/chatInstances'

// 兼容两种实例类型的公共接口
type QueueModalInstance = {
  instance_id?: string
  instance_name?: string
  display_name?: string
  avatar?: string
}

const props = defineProps<{
  visible: boolean
  instance: QueueModalInstance | null
  sessionId: string
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  queued: [position: number]
  ready: []
  cancelled: []
}>()

// State
const queuePosition = ref(0)
const queueLength = ref(0)
const estimatedWaitSeconds = ref(0)
const isReady = ref(false)
let pollingTimer: number | null = null

// Computed
const displayPosition = computed(() => queuePosition.value + 1)

const progressWidth = computed(() => {
  if (queueLength.value <= 1) return '100%'
  const progress = ((queueLength.value - queuePosition.value) / queueLength.value) * 100
  return `${Math.max(10, progress)}%`
})

// Methods
function formatWaitTime(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`
  return `${Math.floor(seconds / 3600)} 小时`
}

async function pollQueueStatus() {
  if (!props.instance || !props.sessionId || !props.instance.instance_id) return

  try {
    const status = await checkQueueStatus(props.instance.instance_id, props.sessionId)

    if (status.status === 'ready') {
      isReady.value = true
      stopPolling()
      // Short delay to show ready state before transitioning
      setTimeout(() => {
        emit('ready')
      }, 1500)
    } else if (status.status === 'waiting') {
      queuePosition.value = status.position || 0
      queueLength.value = status.queue_length || 0
      estimatedWaitSeconds.value = status.estimated_wait_seconds || 0
    } else if (status.status === 'expired' || status.status === 'not_in_queue') {
      // Queue expired or not found, close modal
      stopPolling()
      emit('cancelled')
    }
  } catch (e) {
    console.error('Poll queue status failed:', e)
  }
}

function startPolling() {
  // Poll immediately first
  pollQueueStatus()
  // Then poll every 10 seconds
  pollingTimer = window.setInterval(pollQueueStatus, 10000)
}

function stopPolling() {
  if (pollingTimer) {
    clearInterval(pollingTimer)
    pollingTimer = null
  }
}

async function handleCancel() {
  stopPolling()

  if (props.instance && props.sessionId && props.instance.instance_id) {
    try {
      await cancelQueue(props.instance.instance_id, props.sessionId)
    } catch (e) {
      console.error('Cancel queue failed:', e)
    }
  }

  emit('cancelled')
}

// Watch visibility
watch(
  () => props.visible,
  (newVal) => {
    if (newVal) {
      isReady.value = false
      queuePosition.value = 0
      queueLength.value = 0
      startPolling()
    } else {
      stopPolling()
    }
  }
)

onUnmounted(() => {
  stopPolling()
})
</script>

<style scoped>
.modal-enter-active,
.modal-leave-active {
  transition: all 0.3s ease;
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

.modal-enter-from > div:last-child,
.modal-leave-to > div:last-child {
  transform: scale(0.95) translateY(20px);
}
</style>
