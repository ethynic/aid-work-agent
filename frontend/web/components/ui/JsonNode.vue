<template>
  <div class="select-text">
    <!-- Object / Array -->
    <template v-if="isObject || isArray">
      <span class="cursor-pointer inline-flex items-center gap-1" @click="toggle">
        <span class="text-muted text-xs w-4 inline-block text-center">{{ expanded ? '▼' : '▶' }}</span>
        <span class="text-primary-700">{{ keyLabel }}</span>
        <span class="text-muted text-xs ml-1">{{ typeLabel }}</span>
        <JsonCopyButton :data="data" />
      </span>
      <div v-if="expanded" class="ml-4 border-l border-default pl-2">
        <template v-if="isArray">
          <JsonNode
            v-for="(item, idx) in data"
            :key="idx"
            :data="item"
            :key-name="String(idx)"
            :depth="depth + 1"
            :max-preview="maxPreview"
            :default-expand="defaultExpand"
          />
        </template>
        <template v-else>
          <JsonNode
            v-for="(val, k) in data"
            :key="k"
            :data="val"
            :key-name="String(k)"
            :depth="depth + 1"
            :max-preview="maxPreview"
            :default-expand="defaultExpand"
          />
        </template>
      </div>
    </template>

    <!-- String -->
    <template v-else-if="typeof data === 'string'">
      <span class="cursor-pointer inline-flex items-center gap-1" @click="toggleExpand">
        <span class="text-muted text-xs w-4 inline-block text-center">{{ expanded ? '▼' : '▶' }}</span>
        <span class="text-primary-700">{{ keyLabel }}:</span>
        <span class="text-success-700" :style="expanded && data.includes('\n') ? 'white-space: pre-wrap' : ''">"{{ expanded ? data : truncate(data) }}"</span>
        <span v-if="!expanded && data.length > (maxPreview || 80)" class="text-muted text-xs">...</span>
        <JsonCopyButton :data="data" />
      </span>
    </template>

    <!-- Number -->
    <template v-else-if="typeof data === 'number'">
      <span class="inline-flex items-center gap-1 ml-5">
        <span class="text-primary-700">{{ keyLabel }}:</span>
        <span class="text-info-700">{{ data }}</span>
      </span>
    </template>

    <!-- Boolean -->
    <template v-else-if="typeof data === 'boolean'">
      <span class="inline-flex items-center gap-1 ml-5">
        <span class="text-primary-700">{{ keyLabel }}:</span>
        <span class="text-warning-700">{{ data }}</span>
      </span>
    </template>

    <!-- null -->
    <template v-else-if="data === null">
      <span class="inline-flex items-center gap-1 ml-5">
        <span class="text-primary-700">{{ keyLabel }}:</span>
        <span class="text-muted">null</span>
      </span>
    </template>

    <!-- fallback -->
    <template v-else>
      <span class="inline-flex items-center gap-1 ml-5">
        <span class="text-primary-700">{{ keyLabel }}:</span>
        <span class="text-default">{{ String(data) }}</span>
      </span>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import JsonCopyButton from './JsonCopyButton.vue'

const props = withDefaults(defineProps<{
  data: any
  keyName?: string
  depth?: number
  maxPreview?: number
  defaultExpand?: number
}>(), {
  keyName: '',
  depth: 0,
  maxPreview: 80,
  defaultExpand: 0,
})

const expanded = ref(props.depth < (props.defaultExpand || 0))

const isObject = computed(() => props.data !== null && typeof props.data === 'object' && !Array.isArray(props.data))
const isArray = computed(() => Array.isArray(props.data))

const typeLabel = computed(() => {
  if (isArray.value) return 'Array[' + props.data.length + ']'
  if (isObject.value) return 'Object{' + Object.keys(props.data).length + '}'
  return ''
})

const keyLabel = computed(() => {
  if (!props.keyName) return ''
  // If key is a number (array index), show [0] format
  if (/^\d+$/.test(props.keyName)) {
    return `[${props.keyName}]`
  }
  return props.keyName
})

function toggle() {
  expanded.value = !expanded.value
}

function toggleExpand() {
  expanded.value = !expanded.value
}

function truncate(str: string): string {
  const max = props.maxPreview || 80
  if (str.length <= max) return str
  return str.substring(0, max)
}
</script>
