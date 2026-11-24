<template>
  <BaseModal v-model="modelValue" title="在微信中下载文件" size="sm" mode="view">
    <!-- 有下载直链时引导复制链接（uid/匿名聊天页的文件链接自带身份参数，
         系统浏览器粘贴打开可直接下载，无需会话上下文）；无直链时保留旧引导 -->
    <p class="text-sm text-default leading-relaxed">
      {{ downloadUrl
        ? '微信内置浏览器不支持直接下载文件。请点击「复制下载链接」，切换到系统浏览器后粘贴打开，即可直接下载。'
        : '微信内置浏览器不支持直接下载文件。请点击右上角「···」菜单，选择「在浏览器打开」后，在新页面中重新下载。' }}
    </p>
    <template #footer>
      <BaseButton v-if="downloadUrl" intent="secondary" @click="copyLink">{{ copied ? '已复制' : '复制下载链接' }}</BaseButton>
      <BaseButton @click="modelValue = false">知道了</BaseButton>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'

const props = defineProps<{ downloadUrl?: string }>()
const modelValue = defineModel<boolean>({ default: false })
const copied = ref(false)

async function copyLink() {
  if (!props.downloadUrl) return
  const ok = await writeClipboard(props.downloadUrl)
  if (ok) {
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  }
}

async function writeClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    /* 降级路径：旧内核/非 HTTPS 下 clipboard API 不可用 */
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}
</script>
