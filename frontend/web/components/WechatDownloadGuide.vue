<template>
  <BaseModal v-model="modelValue" title="在微信中下载文件" size="sm" mode="view">
    <p class="text-sm text-default leading-relaxed">
      微信内置浏览器不支持直接下载文件。请点击右上角「···」菜单，选择「在浏览器打开」后，在新页面中重新下载。
    </p>
    <template #footer>
      <BaseButton @click="modelValue = false">知道了</BaseButton>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'

const modelValue = defineModel<boolean>({ default: false })
</script>
