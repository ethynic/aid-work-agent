<template>
  <div class="placeholder">
    <div class="card">
      <h1>BOSS 简历筛选助手</h1>
      <p class="subtitle">Phase 1 · 工程骨架就绪</p>

      <div class="status-section">
        <div class="status-row">
          <span class="label">数据库状态</span>
          <span :class="['badge', dbHealth?.ok ? 'badge-ok' : 'badge-fail']">
            {{ dbHealth?.ok ? '正常' : '异常' }}
          </span>
        </div>
        <div v-if="dbHealth" class="status-row">
          <span class="label">Schema 版本</span>
          <span class="value">{{ dbHealth.schemaVersion }}</span>
        </div>
        <div v-if="dbHealth?.dbPath" class="status-row">
          <span class="label">数据库路径</span>
          <span class="value mono">{{ dbHealth.dbPath }}</span>
        </div>
        <div v-if="dbHealth?.error" class="status-row">
          <span class="label">错误</span>
          <span class="value error">{{ dbHealth.error }}</span>
        </div>
        <div v-if="runtime" class="status-row">
          <span class="label">运行时</span>
          <span class="value mono">
            Electron {{ runtime.electronVersion }} / Chrome {{ runtime.chromeVersion }} /
            Node {{ runtime.nodeVersion }} / {{ runtime.platform }}
          </span>
        </div>
      </div>

      <p class="hint">后续 Phase 将在此界面接入登录门禁、岗位配置、复核队列与审计日志。</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import type { DbHealth, AppRuntime } from '@shared/ipc'

const dbHealth = ref<DbHealth | null>(null)
const runtime = ref<AppRuntime | null>(null)

async function refresh() {
  dbHealth.value = await window.bossResume.db.health()
  runtime.value = await window.bossResume.app.runtime()
}

onMounted(() => {
  void refresh()
})
</script>

<style scoped>
.placeholder {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f5f6f8;
  padding: 24px;
}
@media (prefers-color-scheme: dark) {
  .placeholder {
    background: #111827;
  }
}
.card {
  width: 100%;
  max-width: 640px;
  background: #ffffff;
  border-radius: 12px;
  padding: 32px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08), 0 8px 24px rgba(0, 0, 0, 0.04);
}
@media (prefers-color-scheme: dark) {
  .card {
    background: #1f2937;
    color: #e5e7eb;
  }
}
h1 {
  margin: 0 0 4px;
  font-size: 22px;
  font-weight: 600;
}
.subtitle {
  margin: 0 0 24px;
  font-size: 13px;
  color: #6b7280;
}
.status-section {
  border-top: 1px solid #e5e7eb;
  padding-top: 16px;
}
@media (prefers-color-scheme: dark) {
  .status-section {
    border-top-color: #374151;
  }
}
.status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 0;
  font-size: 14px;
}
.label {
  color: #6b7280;
  flex-shrink: 0;
}
.value {
  color: #111827;
  text-align: right;
  word-break: break-all;
}
@media (prefers-color-scheme: dark) {
  .value {
    color: #e5e7eb;
  }
}
.mono {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 12px;
}
.error {
  color: #dc2626;
}
.badge {
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
}
.badge-ok {
  background: #dcfce7;
  color: #166534;
}
.badge-fail {
  background: #fee2e2;
  color: #991b1b;
}
.hint {
  margin: 24px 0 0;
  font-size: 12px;
  color: #9ca3af;
  line-height: 1.6;
}
</style>
