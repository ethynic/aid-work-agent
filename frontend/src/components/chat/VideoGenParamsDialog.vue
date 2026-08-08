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

      <!-- 分辨率 -->
      <div>
        <label class="text-sm text-muted mb-1 block">分辨率</label>
        <BaseSelect
          :model-value="draft.resolution"
          @update:model-value="(v: string) => emit('update', 'resolution', v)"
          size="md"
        >
          <option value="720P">720P</option>
          <option value="1080P">1080P</option>
          <option value="768P">768P</option>
          <option value="2K">2K</option>
        </BaseSelect>
        <p class="text-xs text-muted mt-1">
          支持的分辨率取决于当前会话视频模型（wan2.7-r2v: 720P/1080P；MiniMax-H3: 768P/2K）
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
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import type { VideoGenParams } from '@/composables/useVideoGenParams'

interface Props {
  open: boolean
  draft: VideoGenParams
}

defineProps<Props>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'commit'): void
  (e: 'update', field: keyof VideoGenParams, value: any): void
}>()
</script>
