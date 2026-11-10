<template>
  <section class="runtime-logs" aria-labelledby="runtime-logs-title">
    <div class="logs-heading">
      <div><h2 id="runtime-logs-title">客户端日志</h2><p>查看连接和运行记录，或导出后交给技术支持。敏感内容不会包含在导出文件中。</p></div>
      <button type="button" class="logs-button" :disabled="!logs || exporting" @click="exportLogs">{{ exporting ? '正在导出…' : '导出日志' }}</button>
    </div>
    <p v-if="!logs">当前客户端尚未提供日志功能，请使用包含该功能的版本。</p>
    <template v-else>
      <div class="logs-toolbar">
        <label for="runtime-log-range">时间范围</label>
        <select id="runtime-log-range" v-model.number="hours" :disabled="loading" @change="readLogs"><option :value="1">最近 1 小时</option><option :value="24">最近 24 小时</option><option :value="168">最近 7 天</option></select>
        <button type="button" class="logs-button" :disabled="loading" @click="readLogs">{{ loading ? '正在读取…' : '刷新日志' }}</button>
      </div>
      <p v-if="error" class="logs-error" role="alert">{{ error }}</p>
      <p v-if="exportMessage" role="status">{{ exportMessage }}</p>
      <template v-if="snapshot">
        <p class="logs-coverage">当前安装版本：客户端 {{ snapshot.versions.app || '未知' }} · Host {{ snapshot.versions.host || '未知' }} · Core {{ snapshot.versions.core || '未知' }}</p>
        <p class="logs-coverage">{{ snapshot.entries.length }} 条记录 · {{ snapshot.range.observedFrom ? `已有记录：${time(snapshot.range.observedFrom)} 至 ${time(snapshot.range.observedTo)}` : '所选时间内暂无记录' }}</p>
        <p v-if="partial" class="logs-warning" role="status">部分历史日志不可用或无法安全展示，以下为本机已有的可读记录。</p>
        <p v-if="snapshot.truncated" class="logs-warning">日志较多，仅展示本次读取范围内的记录，导出文件会注明截断情况。</p>
        <p v-if="snapshot.health?.fileWriteFailures" class="logs-warning">日志写入曾失败 {{ snapshot.health.fileWriteFailures }} 次，请检查磁盘空间和目录权限。</p>
        <ol v-if="snapshot.entries.length" class="logs-entries" aria-label="运行日志">
          <li v-for="(entry, index) in entries" :key="`${entry.at}-${index}`">
            <time :datetime="entry.at">{{ time(entry.at) }}</time><span :class="{ 'logs-error': entry.level === 'error' }">{{ entry.summary }}</span>
            <span v-if="entry.location || entry.tool || entry.durationMs !== undefined" class="logs-meta">{{ [entry.location && locationLabels[entry.location], entry.protocol?.toUpperCase(), entry.httpStatus, entry.tool, entry.durationMs !== undefined && `${entry.durationMs} ms`].filter(Boolean).join(' · ') }}</span>
          </li>
        </ol>
        <details class="logs-sources"><summary>采集详情</summary><ul><li v-for="source in snapshot.sources" :key="`${source.location ?? ''}-${source.name}`"><template v-if="source.location">{{ locationLabels[source.location] }} · </template>{{ source.name }}：{{ sourceLabels[source.status] }}<template v-if="source.observedFrom">，可识别记录 {{ time(source.observedFrom) }} 至 {{ time(source.observedTo) }}</template><template v-if="source.stale">，早于所选范围</template><template v-if="source.skippedLines">，{{ source.skippedLines }} 行未展示</template><template v-if="source.partialLines">，{{ source.partialLines }} 行尚未写完</template><template v-if="source.truncated">，读取范围已截断</template></li></ul></details>
      </template>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { RuntimeLogsPort, RuntimeLogSnapshot } from './logContracts'
const props = defineProps<{ logs?: RuntimeLogsPort }>()
const hours = ref<1 | 24 | 168>(24)
const snapshot = ref<RuntimeLogSnapshot | null>(null)
const loading = ref(false)
const exporting = ref(false)
const error = ref('')
const exportMessage = ref('')
let disposed = false
const entries = computed(() => [...(snapshot.value?.entries ?? [])].reverse())
const partial = computed(() => snapshot.value?.sources.some(source => ['unreadable', 'linked'].includes(source.status) || source.skippedLines > 0 || source.partialLines > 0))
const sourceLabels = { read: '已读取', missing: '暂无记录', unreadable: '暂不可读取', linked: '来源不可读取' }
const locationLabels = { active: '当前连接', prior: '上一连接', desktop: '客户端' }
function time(value?: string | null) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }
async function readLogs() {
  if (!props.logs || loading.value) return
  loading.value = true; error.value = ''
  try { const result = await props.logs.read({ hours: hours.value }); if (!disposed) snapshot.value = result }
  catch { if (!disposed) error.value = '日志暂时无法读取，可以重试或直接导出。连接和任务执行不受影响。' }
  finally { if (!disposed) loading.value = false }
}
async function exportLogs() {
  if (!props.logs || exporting.value) return
  exporting.value = true; error.value = ''; exportMessage.value = ''
  try { const result = await props.logs.export({ hours: hours.value }); if (!disposed && !result.canceled) exportMessage.value = `已导出${result.fileName ? `：${result.fileName}` : '日志'}` }
  catch { if (!disposed) error.value = '导出未完成，请检查保存位置权限和磁盘空间后重试。' }
  finally { if (!disposed) exporting.value = false }
}
onMounted(() => { void readLogs() })
onUnmounted(() => { disposed = true })
</script>

<style scoped>
.runtime-logs { margin-top: 24px; padding: 24px; border: 1px solid var(--d-line); background: var(--d-surface); }
.logs-heading, .logs-toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.logs-heading { justify-content: space-between; }
.logs-heading h2 { margin: 0; font: 500 20px var(--d-font-display); }
.logs-heading p, .logs-coverage, .logs-meta, .logs-sources { color: var(--d-text-muted); }
.logs-button, select { min-height: 36px; padding: 6px 12px; border: 1px solid var(--d-line-strong); border-radius: var(--d-radius); background: var(--d-surface-raised); color: var(--d-text); font: inherit; }
.logs-button { cursor: pointer; }
.logs-button:disabled { opacity: .5; cursor: default; }
.logs-toolbar { margin-top: 16px; }
.logs-entries { max-height: 420px; overflow: auto; list-style: none; margin: 16px 0; padding: 0; }
.logs-entries li { display: grid; grid-template-columns: 155px minmax(0, 1fr); gap: 4px 12px; padding: 10px 0; border-bottom: 1px solid var(--d-line); overflow-wrap: anywhere; }
.logs-entries time, .logs-meta { font-size: 12px; }
.logs-meta { grid-column: 2; }
.logs-error { color: var(--d-danger); }.logs-warning { color: var(--d-warning); }
.logs-sources summary { cursor: pointer; width: fit-content; }
select:focus-visible, summary:focus-visible { outline: 2px solid var(--d-focus); outline-offset: 2px; }
@media(max-width: 560px) { .runtime-logs { padding: 16px; }.logs-entries li { grid-template-columns: 1fr; }.logs-meta { grid-column: 1; } }
</style>
