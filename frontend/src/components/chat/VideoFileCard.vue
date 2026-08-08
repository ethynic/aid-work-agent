<template>
  <!-- 视频文件卡片（Phase 5.2.1）
       显示视频预览 + 操作按钮（预览/下载/查看提示词/留用/不喜欢） -->
  <div class="rounded-xl border border-default bg-surface overflow-hidden max-w-md">
    <!-- 视频预览区 + 已消耗积分 -->
    <div class="relative bg-gray-900 aspect-video">
      <video
        v-if="videoUrl"
        :src="videoUrl"
        controls
        preload="metadata"
        class="w-full h-full object-contain"
      />
      <div v-else class="absolute inset-0 flex items-center justify-center text-gray-400 text-sm">
        视频加载中...
      </div>
      <!-- 已消耗积分（右上角） -->
      <div
        v-if="card.credit_cost !== undefined && card.credit_cost !== null"
        class="absolute top-2 right-2 px-2 py-0.5 rounded bg-black/60 text-white text-xs"
      >
        已消耗 {{ card.credit_cost }} 积分
      </div>
    </div>

    <!-- 卡片信息 + 操作 -->
    <div class="p-3 space-y-2">
      <!-- 视频标题 -->
      <div class="text-sm text-default font-medium truncate" :title="displayName">
        {{ displayName }}
      </div>

      <!-- 第一行操作按钮 -->
      <div class="flex items-center gap-2">
        <BaseButton size="sm" intent="ghost" @click="handlePreview">预览</BaseButton>
        <BaseButton size="sm" intent="ghost" @click="handleDownload">下载</BaseButton>
        <BaseButton size="sm" intent="ghost" @click="showPrompt = !showPrompt">查看提示词</BaseButton>
      </div>

      <!-- 第二行操作按钮 -->
      <div class="flex items-center justify-between">
        <!-- 留用 + 收入提示词库勾选 -->
        <div class="flex items-center gap-2">
          <BaseButton size="sm" @click="handleKeep">留用</BaseButton>
          <label class="flex items-center gap-1 text-xs text-muted cursor-pointer">
            <input
              type="checkbox"
              v-model="savePromptToLibrary"
              class="w-3.5 h-3.5 rounded border-primary-200 text-primary-600 focus:ring-primary-500"
            />
            收入提示词库
          </label>
        </div>
        <!-- 不喜欢 -->
        <BaseButton size="sm" intent="danger-ghost" @click="handleDislike">不喜欢</BaseButton>
      </div>

      <!-- 提示词预览（展开式） -->
      <div v-if="showPrompt" class="mt-2 p-2 rounded-lg bg-gray-50 border border-default text-xs space-y-1">
        <div>
          <span class="text-muted">业务层：</span>
          <span class="text-default">{{ card.business_prompt || '-' }}</span>
        </div>
        <div>
          <span class="text-muted">工艺层：</span>
          <span class="text-default whitespace-pre-wrap">{{ card.craft_prompt || '-' }}</span>
        </div>
      </div>

      <!-- 不喜欢原因快选（展开式） -->
      <div v-if="showDislikeReason" class="mt-2 p-2 rounded-lg bg-danger-50 border border-danger-200 space-y-2">
        <div class="text-xs text-danger-700">请选择不喜欢的原因（可选）：</div>
        <div class="flex flex-wrap gap-1">
          <button
            v-for="reason in dislikeReasons"
            :key="reason"
            type="button"
            @click="toggleReason(reason)"
            :class="[
              'px-2 py-0.5 rounded-full text-xs border transition-colors',
              selectedReasons.includes(reason)
                ? 'border-danger-400 bg-danger-100 text-danger-700'
                : 'border-default text-muted hover:border-danger-300',
            ]"
          >
            {{ reason }}
          </button>
        </div>
        <input
          v-model="customReason"
          type="text"
          placeholder="其他原因（选填）"
          class="w-full px-2 py-1 text-xs rounded border border-default focus:border-primary-500 outline-none"
        />
        <div class="flex justify-end gap-1">
          <BaseButton size="sm" intent="ghost" @click="cancelDislike">取消</BaseButton>
          <BaseButton size="sm" intent="danger" @click="confirmDislike">确认</BaseButton>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import BaseButton from '@/components/ui/BaseButton.vue'

export interface VideoCardData {
  /** 卡片 id（前端生成） */
  card_id: string
  /** 业务层提示词 */
  business_prompt?: string
  /** 工艺层提示词 */
  craft_prompt?: string
  /** 模型参数 */
  model_params?: Record<string, any>
  /** 视频文件 id */
  video_file_id?: string
  /** 视频可访问 URL */
  video_url?: string
  /** 视频显示名 */
  video_display_name?: string
  /** 已消耗积分 */
  credit_cost?: number
  /** 错误信息 */
  error?: string
}

interface Props {
  card: VideoCardData
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'keep', payload: { card: VideoCardData; save_prompt_to_library: boolean }): void
  (e: 'dislike', payload: { card: VideoCardData; reason: string }): void
  (e: 'preview', card: VideoCardData): void
}>()

const showPrompt = ref(false)
const showDislikeReason = ref(false)
const savePromptToLibrary = ref(true)
const selectedReasons = ref<string[]>([])
const customReason = ref('')

const dislikeReasons = [
  '主体不符',
  '运镜生硬',
  '光影不协调',
  '色调偏冷',
  '画面模糊',
  '时长不符',
]

const videoUrl = computed(() => props.card.video_url || '')
const displayName = computed(() => props.card.video_display_name || '生成的视频.mp4')

function toggleReason(reason: string) {
  const idx = selectedReasons.value.indexOf(reason)
  if (idx >= 0) {
    selectedReasons.value.splice(idx, 1)
  } else {
    selectedReasons.value.push(reason)
  }
}

function handlePreview() {
  emit('preview', props.card)
}

function handleDownload() {
  if (props.card.video_file_id) {
    window.open(`/api/files/${props.card.video_file_id}/download`, '_blank')
  }
}

function handleKeep() {
  emit('keep', {
    card: props.card,
    save_prompt_to_library: savePromptToLibrary.value,
  })
}

function handleDislike() {
  showDislikeReason.value = true
}

function cancelDislike() {
  showDislikeReason.value = false
  selectedReasons.value = []
  customReason.value = ''
}

function confirmDislike() {
  const reasons = [...selectedReasons.value]
  if (customReason.value.trim()) {
    reasons.push(customReason.value.trim())
  }
  emit('dislike', {
    card: props.card,
    reason: reasons.join('；'),
  })
  showDislikeReason.value = false
  selectedReasons.value = []
  customReason.value = ''
}
</script>
