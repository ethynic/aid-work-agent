<template>
  <!-- 视频生成参数弹框（Phase 5.1.5）
       含 5 个参数：创作模式 / 时长 / 比例 / 分辨率 / 生成条数
       联动：选「精修模式」时生成条数禁用并固定为 1 -->
  <BaseModal
    :model-value="open"
    @update:model-value="(v) => !v && emit('close')"
    title="视频生成参数"
    size="md"
  >
    <div class="space-y-4">
      <!-- 创作模式 -->
      <div>
        <label class="text-sm text-muted mb-1 block">创作模式</label>
        <div class="flex gap-2">
          <button
            type="button"
            :class="[
              'flex-1 px-3 py-2 rounded-lg border text-sm transition-colors',
              draft.mode === 'refine'
                ? 'border-primary-500 bg-primary-50 text-primary-700'
                : 'border-default text-muted hover:border-hover',
            ]"
            @click="emit('update', 'mode', 'refine')"
          >
            精修模式
          </button>
          <button
            type="button"
            :class="[
              'flex-1 px-3 py-2 rounded-lg border text-sm transition-colors',
              draft.mode === 'agile'
                ? 'border-primary-500 bg-primary-50 text-primary-700'
                : 'border-default text-muted hover:border-hover',
            ]"
            @click="emit('update', 'mode', 'agile')"
          >
            敏捷模式
          </button>
        </div>
        <p class="text-xs text-muted mt-1">
          {{ draft.mode === 'refine'
            ? '精修：生成 1 段提示词，需在聊天中确认后生成视频'
            : '敏捷：一次生成多段差异化提示词，直接生成视频' }}
        </p>
      </div>

      <!-- 视频时长 -->
      <div>
        <label class="text-sm text-muted mb-1 block">视频时长</label>
        <BaseSelect
          :model-value="String(draft.duration_sec)"
          @update:model-value="(v: string) => emit('update', 'duration_sec', Number(v))"
          size="md"
        >
          <option value="5">5 秒</option>
          <option value="10">10 秒</option>
          <option value="15">15 秒</option>
          <option value="30">30 秒</option>
        </BaseSelect>
      </div>

      <!-- 视频比例 -->
      <div>
        <label class="text-sm text-muted mb-1 block">视频比例</label>
        <BaseSelect
          :model-value="draft.ratio"
          @update:model-value="(v: string) => emit('update', 'ratio', v)"
          size="md"
        >
          <option value="9:16">9:16（竖屏）</option>
          <option value="16:9">16:9（横屏）</option>
          <option value="1:1">1:1（方形）</option>
          <option value="4:3">4:3</option>
          <option value="3:4">3:4</option>
        </BaseSelect>
      </div>

      <!-- 分辨率（按 .env 中 VIDEO_GEN_PROVIDER 动态拉取；wanx=720P/1080P，minimax=768P/2K） -->
      <div>
        <label class="text-sm text-muted mb-1 block">分辨率</label>
        <BaseSelect
          :model-value="draft.resolution"
          @update:model-value="(v: string) => emit('update', 'resolution', v)"
          size="md"
        >
          <option v-for="r in resolutionOptions" :key="r.value" :value="r.value">{{ r.label }}</option>
        </BaseSelect>
        <p class="text-xs text-muted mt-1">
          支持的分辨率取决于当前配置的视频模型
        </p>
      </div>

      <!-- 提示词模型（生成提示词所用的 LLM；视觉模型可看图，文本模型仅文字） -->
      <div>
        <label class="text-sm text-muted mb-1 block">提示词模型</label>
        <BaseSelect
          :model-value="draft.prompt_model"
          @update:model-value="(v: string) => emit('update', 'prompt_model', v)"
          size="md"
        >
          <option v-for="m in promptModelOptions" :key="m.value" :value="m.value">{{ m.label }}</option>
        </BaseSelect>
        <p class="text-xs text-muted mt-1">
          视觉模型（Qwen-VL-*）可根据上传的参考图片生成提示词；文本模型（Qwen3.7-Plus）仅支持文字需求
        </p>
      </div>

      <!-- 生成条数 -->
      <div>
        <label class="text-sm text-muted mb-1 block">生成条数</label>
        <BaseSelect
          :model-value="String(draft.card_count)"
          @update:model-value="(v: string) => emit('update', 'card_count', Number(v))"
          size="md"
          :disabled="draft.mode === 'refine'"
        >
          <option value="1">1 条</option>
          <option value="2">2 条</option>
          <option value="3">3 条</option>
        </BaseSelect>
        <p v-if="draft.mode === 'refine'" class="text-xs text-muted mt-1">
          精修模式固定生成 1 条
        </p>
      </div>
    </div>

    <template #footer>
      <BaseButton intent="secondary" @click="emit('close')">取消</BaseButton>
      <BaseButton @click="emit('commit')">确认</BaseButton>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import { videoGenAPI, type ProviderOptions, type OptionItem } from '@/api/videoGen'
import type { VideoGenParams } from '@/composables/useVideoGenParams'

interface Props {
  open: boolean
  draft: VideoGenParams
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'commit'): void
  (e: 'update', field: keyof VideoGenParams, value: any): void
}>()

// 拉取失败时的兜底（与 .env 默认 provider=wanx 对齐）
const FALLBACK_RESOLUTIONS: OptionItem[] = [
  { value: '720P', label: '720P' },
  { value: '1080P', label: '1080P' },
]
// 提示词模型兜底（与后端 /api/video-gen/options 默认列表一致）
const FALLBACK_PROMPT_MODELS: OptionItem[] = [
  { value: 'qwen-vl-plus', label: 'Qwen-VL-Plus（视觉模型，默认）' },
  { value: 'qwen-vl-max', label: 'Qwen-VL-Max（视觉模型，更强）' },
  { value: 'qwen3-vl-flash', label: 'Qwen3-VL-Flash（视觉模型，最快）' },
  { value: 'qwen3.7-plus', label: 'Qwen3.7-Plus（文本模型，不支持看图）' },
]

const options = ref<ProviderOptions | null>(null)
const resolutionOptions = computed<OptionItem[]>(
  () => options.value?.resolutions ?? FALLBACK_RESOLUTIONS,
)
const promptModelOptions = computed<OptionItem[]>(
  () => options.value?.prompt_models ?? FALLBACK_PROMPT_MODELS,
)

async function loadOptions() {
  const res = await videoGenAPI.getOptions()
  if (!res.success || !res.data) return
  options.value = res.data
  // draft.resolution 不在 provider 支持列表中（如默认 720P 但 provider=minimax）时，
  // 自动纠正为 provider 默认分辨率，避免下拉框无匹配项
  const validValues = res.data.resolutions.map(r => r.value)
  if (!validValues.includes(props.draft.resolution) && res.data.default_resolution) {
    emit('update', 'resolution', res.data.default_resolution)
  }
}

// 弹框首次打开时拉一次 options（同一会话内 provider 不变，无需每次拉）
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen && !options.value) loadOptions()
  },
)
</script>
