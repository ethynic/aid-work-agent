<template>
  <button
    type="button"
    class="json-copy-btn ml-1.5 p-0.5 text-muted hover:text-primary-600 transition-colors"
    :title="copied ? '已复制' : '复制该节点 JSON'"
    @click.stop="copyNode"
  >
    <svg v-if="!copied" class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <rect x="9" y="9" width="11" height="11" rx="2" stroke-width="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" stroke-width="2" />
    </svg>
    <svg v-else class="w-3 h-3 text-success-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path d="M5 13l4 4L19 7" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" />
    </svg>
  </button>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{ data: any }>()

const copied = ref(false)

/** String 复制原文，Object/Array 复制格式化 JSON */
function copyText(data: any): string {
  if (typeof data === 'string') return data
  if (data === null || data === undefined) return String(data)
  return JSON.stringify(data, null, 2)
}

async function copyNode() {
  const text = copyText(props.data)
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    // 降级：textarea 选中复制（非 https / 旧浏览器）
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try {
      document.execCommand('copy')
    } catch (e) {
      console.error('复制失败:', e)
      return
    } finally {
      document.body.removeChild(ta)
    }
  }
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
}
</script>
