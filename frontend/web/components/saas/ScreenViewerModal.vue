<template>
  <BaseModal
    :model-value="modelValue"
    size="xl"
    :accessible="true"
    :show-fullscreen="false"
    @update:model-value="handleModalUpdate"
  >
    <template #title>
      <!-- 观看中：顶部绿点、「实时画面」、设备名、线路徽标、「仅查看」（设计 10） -->
      <span v-if="viewer.state.phase === 'watching'" class="flex items-center gap-2 flex-wrap">
        <span class="w-2.5 h-2.5 rounded-full bg-success-500" aria-hidden="true"></span>
        <span>实时画面</span>
        <span class="text-sm font-normal text-muted">{{ viewer.state.deviceName }}</span>
        <BaseBadge v-if="viewer.state.path" intent="info" size="sm">{{ pathLabel }}</BaseBadge>
        <BaseBadge intent="neutral" size="sm">仅查看</BaseBadge>
      </span>
      <span v-else>实时画面</span>
    </template>

    <!-- 中部：三步进度 / 画面 / 结束态 -->
    <div class="flex-1 min-h-0 flex flex-col">
      <!-- 连接中：三步进度，当前步骤转圈与一句说明，可取消（设计 10） -->
      <div v-if="viewer.state.phase === 'connecting'" class="flex-1 flex flex-col items-center justify-center gap-6 py-10">
        <ol class="w-full max-w-md space-y-4">
          <li v-for="(step, index) in SCREEN_VIEWER_STEPS" :key="step.label" class="flex items-start gap-3">
            <!-- 已完成 -->
            <template v-if="viewer.state.step > index + 1">
              <span class="mt-0.5 w-5 h-5 flex items-center justify-center rounded-full bg-success-100 text-success-700 flex-shrink-0">
                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"/></svg>
              </span>
              <div class="min-w-0">
                <p class="text-sm text-default">{{ step.label }}</p>
              </div>
            </template>
            <!-- 当前：转圈 + 说明 -->
            <template v-else-if="viewer.state.step === index + 1">
              <svg class="w-5 h-5 text-primary-600 animate-spin flex-shrink-0" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
              </svg>
              <div class="min-w-0">
                <p class="text-sm font-medium text-default">{{ step.label }}</p>
                <p class="text-xs text-muted mt-0.5">{{ step.hint }}</p>
              </div>
            </template>
            <!-- 未开始 -->
            <template v-else>
              <span class="mt-0.5 w-5 h-5 flex items-center justify-center rounded-full border border-default text-muted text-xs flex-shrink-0">{{ index + 1 }}</span>
              <div class="min-w-0">
                <p class="text-sm text-muted">{{ step.label }}</p>
              </div>
            </template>
          </li>
        </ol>
        <!-- 建连前「本地网络访问」权限处于提示状态：先说明用途，不让弹窗凭空出现（设计 6.3） -->
        <p v-if="viewer.state.localNetworkPrompt" class="text-xs text-muted max-w-md text-center">
          即将连接对方电脑的内网地址：浏览器可能弹出「本地网络访问」权限提示，请选择允许。
        </p>
      </div>

      <!-- 观看中：画面 + 统计 + 锁屏覆盖 + 降画质提示 -->
      <div v-else-if="viewer.state.phase === 'watching'" class="flex-1 min-h-0 relative bg-gray-950 rounded-lg overflow-hidden">
        <video ref="videoEl" class="w-full h-full object-contain" autoplay muted playsinline></video>
        <!-- 右上角：时长、分辨率与帧率、码率（getStats 每 2 秒刷新） -->
        <div class="absolute top-2 right-2 flex items-center gap-3 rounded bg-black/60 px-2.5 py-1 text-xs text-white font-mono">
          <span>{{ durationText }}</span>
          <span>{{ resolutionText }}</span>
          <span>{{ bitrateText }}</span>
        </div>
        <!-- 任务运行中资源不足、已降低画质：画面左上角提示，restored 或结束时移除（设计 4.5） -->
        <div v-if="viewer.state.degraded && !viewer.state.locked" class="absolute top-2 left-2 rounded bg-warning-600 px-2.5 py-1 text-xs text-white">
          对方电脑正在执行任务，已降低画质
        </div>
        <!-- 目标电脑锁屏：画面暂停，覆盖提示，解锁后自动恢复 -->
        <div v-if="viewer.state.locked" class="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/70">
          <p class="text-base font-medium text-white">目标电脑已锁屏</p>
          <p class="text-sm text-white/80">解锁后自动恢复</p>
        </div>
      </div>

      <!-- 已结束：标题、说明与附加提示 -->
      <div v-else class="flex-1 flex flex-col items-center justify-center gap-3 py-10 text-center px-4">
        <p class="text-lg font-semibold text-default">{{ viewer.state.end?.title }}</p>
        <p class="text-sm text-muted max-w-md">{{ viewer.state.end?.description }}</p>
        <p v-if="viewer.state.end?.hint" class="text-xs text-muted max-w-md">{{ viewer.state.end.hint }}</p>
      </div>
    </div>

    <template #footer>
      <div class="flex items-center justify-between w-full">
        <p class="text-xs text-muted">画面经 WebRTC 点对点加密直连，不经过服务器，仅查看、不能控制对方电脑。</p>
        <div class="flex items-center gap-2 flex-shrink-0">
          <template v-if="viewer.state.phase === 'connecting'">
            <BaseButton intent="secondary" size="md" :disabled="stopping" @click="handleStop">取消</BaseButton>
          </template>
          <template v-else-if="viewer.state.phase === 'watching'">
            <BaseButton intent="danger" size="md" :disabled="stopping" @click="handleStop">结束查看</BaseButton>
          </template>
          <template v-else>
            <BaseButton v-if="viewer.state.end?.retryable" intent="primary" size="md" @click="handleRetry">重新查看</BaseButton>
            <BaseButton intent="secondary" size="md" @click="handleClose">关闭</BaseButton>
          </template>
        </div>
      </div>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
/**
 * 实时画面观看弹框（设计 6.3、10）。
 *
 * 组合函数 useScreenViewer 负责连接状态机；本组件只做渲染与用户动作转发：
 * 打开时开始会话，关闭（X / Esc / 取消 / 结束查看）时立即补发 end 再关，不弹确认。
 */
import { computed, nextTick, onUnmounted, ref, watch, watchEffect } from 'vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import {
  SCREEN_VIEWER_STEPS,
  useScreenViewer,
} from '@/composables/useScreenViewer'
import { formatBitrate, formatDuration, formatFps } from '@/composables/screenViewerStats'
import type { LocalToolDevice } from '@/api/localTools'

const props = defineProps<{
  modelValue: boolean
  device: LocalToolDevice | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const viewer = useScreenViewer()

const pathLabel = computed(() => viewer.state.path === 'lan' ? '局域网直连' : viewer.state.path === 'p2p' ? '点对点直连' : '')
const durationText = computed(() => formatDuration(viewer.state.stats.durationMs))
const resolutionText = computed(() => {
  const { width, height, fps } = viewer.state.stats
  if (width && height) return `${width}×${height} · ${formatFps(fps)} 帧`
  if (fps !== null) return `${formatFps(fps)} 帧`
  return '-'
})
const bitrateText = computed(() => formatBitrate(viewer.state.stats.bitrateBps))

// 结束按钮防重复提交守卫：连点只触发一次 end（组合函数内部同样有同步护栏）
const stopping = ref(false)

// 轨道 → video：优先用组合函数包装好的 MediaStream，包装不可用时从轨道兜底
const videoEl = ref<HTMLVideoElement | null>(null)
watchEffect(() => {
  const el = videoEl.value
  if (!el) return
  const media = viewer.stream.value
  const incoming = viewer.track.value
  if (media) {
    el.srcObject = media
  } else if (incoming) {
    try {
      el.srcObject = new MediaStream([incoming])
    } catch {
      el.srcObject = null
    }
  } else {
    el.srcObject = null
  }
})

// 出画面后主动播放（部分浏览器对自动播放有条件限制）
watch(() => viewer.state.phase, async (phase) => {
  if (phase !== 'watching') return
  await nextTick()
  try {
    await videoEl.value?.play()
  } catch {
    // 自动播放被浏览器拦截：用户点击画面区域后浏览器通常恢复，不额外处理
  }
})

// 打开即开始会话；进行中重复 start 由组合函数护栏忽略
watch(() => props.modelValue, (open) => {
  if (!open) return
  stopping.value = false
  if (props.device) viewer.start(props.device)
})

/** 用户主动结束：立即补发 end（幂等）再关弹框，不弹确认（结束不造成损失） */
function handleStop(): void {
  if (stopping.value) return
  stopping.value = true
  viewer.endByUser()
  emit('update:modelValue', false)
}

function handleRetry(): void {
  stopping.value = false
  viewer.restart()
}

function handleClose(): void {
  emit('update:modelValue', false)
}

/** X 按钮 / Esc：BaseModal 触发关闭时同样立即补发 end（已是结束态时为空操作） */
function handleModalUpdate(value: boolean): void {
  if (!value && props.modelValue) {
    viewer.endByUser()
  }
  emit('update:modelValue', value)
}

onUnmounted(() => {
  viewer.dispose()
})
</script>
