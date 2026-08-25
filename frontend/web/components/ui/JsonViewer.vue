<template>
  <div class="json-viewer font-mono text-sm">
    <JsonNode :data="data" :depth="0" :max-preview="maxPreview" :default-expand="defaultExpand" />
  </div>
</template>

<script setup lang="ts">
import JsonNode from './JsonNode.vue'

defineProps<{
  data: any
  maxPreview?: number
  defaultExpand?: number
}>()
</script>
