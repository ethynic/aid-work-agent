<template>
  <div class="flex flex-col h-full">
    <div class="flex items-center justify-between mb-2 flex-shrink-0">
      <span class="text-sm font-medium text-gray-500">版本历史</span>
      <button v-if="loading" disabled class="text-xs text-gray-400">加载中...</button>
    </div>
    <div v-if="!versions.length" class="flex-1 flex items-center justify-center text-sm text-gray-400">
      暂无版本
    </div>
    <div v-else class="flex-1 overflow-y-auto space-y-2">
      <div v-for="v in versions" :key="v.version"
        @click="$emit('select', v)"
        :class="['p-2 rounded-lg cursor-pointer border transition-colors',
          isProduction(v.version) ? 'border-success-200 bg-success-50' : 'border-gray-100 hover:border-gray-200 hover:bg-gray-50',
          selectedVersion === v.version ? 'ring-1 ring-info-300' : '']">
        <div class="flex items-center gap-2">
          <span class="text-sm font-medium" :class="isProduction(v.version) ? 'text-success-700' : 'text-gray-700'">
            V{{ v.version }}
          </span>
          <span v-if="isProduction(v.version)"
            class="px-1.5 py-0.5 text-xs bg-success-100 text-success-700 rounded">production</span>
          <span v-if="isStaging(v.version)"
            class="px-1.5 py-0.5 text-xs bg-warning-100 text-warning-700 rounded">staging</span>
        </div>
        <div class="text-xs text-gray-400 mt-1">
          {{ formatTime(v.created_at) }}
          <span v-if="v.created_by"> · {{ v.created_by }}</span>
        </div>
        <div v-if="v.commit_message" class="text-xs text-gray-500 mt-0.5 truncate">
          {{ v.commit_message }}
        </div>
      </div>
    </div>
    <!-- Actions -->
    <div v-if="versions.length >= 2" class="mt-2 pt-2 border-t border-gray-100 flex-shrink-0 flex gap-2">
      <button @click="showDiffDialog = true"
        class="flex-1 px-2 py-1.5 text-xs text-info-600 bg-info-50 hover:bg-info-100 rounded-lg">
        版本对比
      </button>
    </div>

    <!-- Diff Dialog -->
    <div v-if="showDiffDialog" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div class="bg-white rounded-xl shadow-xl w-full max-w-4xl max-h-[80vh] flex flex-col p-6">
        <div class="flex items-center justify-between mb-4 flex-shrink-0">
          <h3 class="text-base font-semibold">版本对比</h3>
          <button @click="showDiffDialog = false" class="p-1 text-gray-400 hover:text-gray-600">&times;</button>
        </div>
        <div class="flex gap-3 mb-4 flex-shrink-0">
          <div class="flex items-center gap-2">
            <label class="text-sm text-gray-500">从</label>
            <select v-model="diffFrom" class="px-2 py-1 text-sm border border-gray-300 rounded-lg">
              <option v-for="v in versions" :key="v.version" :value="v.version">V{{ v.version }}</option>
            </select>
          </div>
          <div class="flex items-center gap-2">
            <label class="text-sm text-gray-500">到</label>
            <select v-model="diffTo" class="px-2 py-1 text-sm border border-gray-300 rounded-lg">
              <option v-for="v in versions" :key="v.version" :value="v.version">V{{ v.version }}</option>
            </select>
          </div>
          <button @click="loadDiff" :disabled="diffLoading"
            class="px-3 py-1 text-sm text-white bg-blue-600 hover:bg-blue-700 rounded-lg disabled:opacity-50">
            {{ diffLoading ? '对比中...' : '对比' }}
          </button>
        </div>
        <div v-if="diffData" class="flex-1 flex gap-4 overflow-hidden min-h-0">
          <div class="flex-1 flex flex-col min-w-0">
            <div class="text-sm font-medium text-gray-500 mb-1">V{{ diffData.from.version }} ({{ formatTime(diffData.from.created_at) }})</div>
            <pre class="flex-1 bg-gray-50 rounded-lg p-3 text-xs text-gray-700 overflow-auto whitespace-pre-wrap font-mono">{{ diffData.from.content }}</pre>
          </div>
          <div class="flex-1 flex flex-col min-w-0">
            <div class="text-sm font-medium text-gray-500 mb-1">V{{ diffData.to.version }} ({{ formatTime(diffData.to.created_at) }})</div>
            <pre class="flex-1 bg-info-50 rounded-lg p-3 text-xs text-gray-700 overflow-auto whitespace-pre-wrap font-mono">{{ diffData.to.content }}</pre>
          </div>
        </div>
        <div v-if="!diffData && !diffLoading" class="flex-1 flex items-center justify-center text-sm text-gray-400">
          选择两个版本后点击"对比"
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import {
  listVersions, listLabels, diffVersions, setLabel,
  type PromptVersion, type PromptLabel, type DiffResult,
} from '@/api/prompts'

const props = defineProps<{
  promptId: string | null
  selectedVersion?: number
}>()

defineEmits<{
  select: [version: PromptVersion]
}>()

const versions = ref<PromptVersion[]>([])
const labels = ref<PromptLabel[]>([])
const loading = ref(false)

// Diff state
const showDiffDialog = ref(false)
const diffFrom = ref(1)
const diffTo = ref(2)
const diffData = ref<DiffResult | null>(null)
const diffLoading = ref(false)

function isProduction(version: number): boolean {
  const label = labels.value.find(l => l.label === 'production')
  return label?.version === version
}

function isStaging(version: number): boolean {
  const label = labels.value.find(l => l.label === 'staging')
  return label?.version === version
}

function formatTime(ts: string): string {
  if (!ts) return ''
  const d = new Date(ts)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

async function loadData() {
  if (!props.promptId) {
    versions.value = []
    labels.value = []
    return
  }
  loading.value = true
  try {
    const [vRes, lRes] = await Promise.all([
      listVersions(props.promptId, 1, 50),
      listLabels(props.promptId),
    ])
    if (vRes.success) versions.value = vRes.data.items
    if (lRes.success) labels.value = lRes.data
    // Set diff defaults to last two versions
    if (versions.value.length >= 2) {
      diffFrom.value = versions.value[1].version
      diffTo.value = versions.value[0].version
    }
  } catch (e) {
    console.error('Failed to load versions', e)
  } finally {
    loading.value = false
  }
}

async function loadDiff() {
  if (!props.promptId) return
  diffLoading.value = true
  diffData.value = null
  try {
    const res = await diffVersions(props.promptId, diffFrom.value, diffTo.value)
    if (res.success) diffData.value = res.data
  } catch (e) {
    console.error('Diff failed', e)
  } finally {
    diffLoading.value = false
  }
}

async function rollback(version: number) {
  if (!props.promptId) return
  if (!confirm(`确定要回滚到 V${version} 吗？`)) return
  try {
    await setLabel(props.promptId, 'production', version)
    await loadData()
  } catch (e) {
    console.error('Rollback failed', e)
  }
}

watch(() => props.promptId, loadData)
onMounted(loadData)

defineExpose({ reload: loadData, rollback, isProduction })
</script>
