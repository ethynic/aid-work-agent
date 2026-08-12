<template>
  <div class="flex items-start gap-4">
    <!-- 预览区 -->
    <div
      class="w-20 h-20 rounded-lg border border-default bg-canvas flex items-center justify-center flex-shrink-0 overflow-hidden"
    >
      <img
        v-if="downloadUrl"
        :src="downloadUrl"
        alt="Logo"
        class="w-full h-full object-contain"
        @error="handleImageError"
      />
      <span v-else class="text-xs text-muted">Logo</span>
    </div>

    <!-- 操作区 -->
    <div class="flex flex-col gap-2 flex-1">
      <div class="flex gap-2">
        <BaseButton
          intent="secondary"
          size="sm"
          :disabled="uploading"
          @click="triggerFileInput"
        >
          {{ uploading ? '上传中...' : (modelValue ? '更换' : '上传') }}
        </BaseButton>
        <BaseButton
          v-if="modelValue"
          intent="danger-ghost"
          size="sm"
          :disabled="uploading"
          @click="handleDelete"
        >
          删除
        </BaseButton>
      </div>
      <p class="text-xs text-muted">建议尺寸 200x200，支持 JPG/PNG/WebP/SVG，≤2MB</p>
    </div>

    <!-- 隐藏的文件输入 -->
    <input
      ref="fileInputRef"
      type="file"
      accept=".jpg,.jpeg,.png,.webp,.svg,image/*"
      class="hidden"
      @change="handleFileChange"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { uploadTenantLogo } from '@/api/saasTenant'

const props = defineProps<{
  /** Logo 文件 ID（无 Logo 时为 null） */
  modelValue: string | null | undefined
}>()

const emit = defineEmits<{
  'update:modelValue': [fileId: string | null]
}>()

const fileInputRef = ref<HTMLInputElement | null>(null)
const downloadUrl = ref<string | null>(null)
const uploading = ref(false)

// 允许的扩展名（与后端 _LOGO_ALLOWED_EXTS 一致）
const ALLOWED_EXTS = ['.jpg', '.jpeg', '.png', '.webp', '.svg']
const MAX_SIZE = 2 * 1024 * 1024  // 2MB

// 监听 modelValue 变化，更新预览 URL
watch(
  () => props.modelValue,
  (newVal) => {
    if (newVal) {
      downloadUrl.value = `/api/files/${newVal}/download`
    } else {
      downloadUrl.value = null
    }
  },
  { immediate: true }
)

function triggerFileInput() {
  fileInputRef.value?.click()
}

async function handleFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

  // 重置 input value 允许重复选择同一文件
  input.value = ''

  // 校验扩展名
  const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase()
  if (!ALLOWED_EXTS.includes(ext)) {
    alert(`不支持的图片格式（仅支持 ${ALLOWED_EXTS.join('/')}）`)
    return
  }

  // 校验大小
  if (file.size > MAX_SIZE) {
    alert(`文件过大（上限 ${MAX_SIZE / 1024 / 1024}MB）`)
    return
  }

  uploading.value = true
  try {
    const res = await uploadTenantLogo(file)
    if (res.success && res.file_id) {
      emit('update:modelValue', res.file_id)
    } else {
      alert(res.error || '上传失败')
    }
  } catch (err: any) {
    alert(err.message || '上传失败')
  } finally {
    uploading.value = false
  }
}

function handleDelete() {
  emit('update:modelValue', null)
}

function handleImageError() {
  // 图片加载失败时清空预览，避免显示 broken icon
  downloadUrl.value = null
}
</script>
