<template>
  <div class="shell">
    <nav class="nav">
      <span class="brand">BOSS 简历筛选助手</span>
      <RouterLink to="/login" class="nav-link">登录</RouterLink>
      <RouterLink to="/jobs" class="nav-link">岗位配置</RouterLink>
      <RouterLink to="/run" class="nav-link">任务控制台</RouterLink>
      <RouterLink to="/review" class="nav-link">复核队列</RouterLink>
      <RouterLink to="/audit" class="nav-link">审计日志</RouterLink>
    </nav>
    <main class="content">
      <router-view />
    </main>
  </div>
</template>

<script setup lang="ts">
</script>

<style>
:root {
  color-scheme: light dark;
  --c-bg: #f5f6f8;
  --c-surface: #ffffff;
  --c-border: #e5e7eb;
  --c-text: #111827;
  --c-muted: #6b7280;
  --c-primary: #2563eb;
  --c-primary-hover: #1d4ed8;
  --c-danger: #dc2626;
  --c-ok-bg: #dcfce7;
  --c-ok-text: #166534;
  --c-warn-bg: #fef9c3;
  --c-warn-text: #854d0e;
  --c-fail-bg: #fee2e2;
  --c-fail-text: #991b1b;
}
@media (prefers-color-scheme: dark) {
  :root {
    --c-bg: #111827;
    --c-surface: #1f2937;
    --c-border: #374151;
    --c-text: #e5e7eb;
    --c-muted: #9ca3af;
    --c-ok-bg: #14532d;
    --c-ok-text: #86efac;
    --c-warn-bg: #422006;
    --c-warn-text: #fde047;
    --c-fail-bg: #450a0a;
    --c-fail-text: #fca5a5;
  }
}
* {
  box-sizing: border-box;
}
html,
body,
#app {
  margin: 0;
  padding: 0;
  height: 100%;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC',
    'Microsoft YaHei', 'Helvetica Neue', Arial, sans-serif;
  color: var(--c-text);
  background: var(--c-bg);
}

/* ===== 全局基础样式（五页共用，克制统一） ===== */
.card {
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: 10px;
  padding: 20px;
  margin-bottom: 16px;
}
.card h2 {
  margin: 0 0 14px;
  font-size: 15px;
  font-weight: 600;
}
.btn {
  height: 34px;
  padding: 0 14px;
  border-radius: 8px;
  border: 1px solid transparent;
  background: var(--c-primary);
  color: #fff;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
}
.btn:hover {
  background: var(--c-primary-hover);
}
.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.btn-secondary {
  background: transparent;
  color: var(--c-text);
  border-color: var(--c-border);
}
.btn-secondary:hover {
  background: var(--c-bg);
}
.btn-danger {
  background: var(--c-danger);
}
.btn-sm {
  height: 26px;
  padding: 0 10px;
  font-size: 12px;
}
.input,
.select,
.textarea {
  width: 100%;
  height: 34px;
  padding: 0 10px;
  border-radius: 8px;
  border: 1px solid var(--c-border);
  background: var(--c-surface);
  color: var(--c-text);
  font-size: 13px;
}
.textarea {
  height: auto;
  min-height: 72px;
  padding: 8px 10px;
  resize: vertical;
  font-family: inherit;
}
.input:focus,
.select:focus,
.textarea:focus {
  outline: 2px solid var(--c-primary);
  outline-offset: -1px;
}
.form-label {
  display: block;
  font-size: 12px;
  color: var(--c-muted);
  margin-bottom: 4px;
}
.form-field {
  margin-bottom: 12px;
}
.badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
}
.badge-ok {
  background: var(--c-ok-bg);
  color: var(--c-ok-text);
}
.badge-warn {
  background: var(--c-warn-bg);
  color: var(--c-warn-text);
}
.badge-fail {
  background: var(--c-fail-bg);
  color: var(--c-fail-text);
}
.badge-neutral {
  background: var(--c-bg);
  color: var(--c-muted);
}
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.data-table th {
  text-align: left;
  padding: 8px 10px;
  font-size: 12px;
  font-weight: 500;
  color: var(--c-muted);
  border-bottom: 1px solid var(--c-border);
  white-space: nowrap;
}
.data-table td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--c-border);
  vertical-align: top;
}
.data-table tr:hover td {
  background: var(--c-bg);
}
.mono {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 12px;
}
.muted {
  color: var(--c-muted);
}
</style>

<style scoped>
.shell {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.nav {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 16px;
  height: 48px;
  background: var(--c-surface);
  border-bottom: 1px solid var(--c-border);
  flex-shrink: 0;
}
.brand {
  font-size: 14px;
  font-weight: 600;
  margin-right: 20px;
}
.nav-link {
  padding: 6px 12px;
  border-radius: 8px;
  font-size: 13px;
  color: var(--c-muted);
  text-decoration: none;
}
.nav-link:hover {
  color: var(--c-text);
  background: var(--c-bg);
}
.nav-link.router-link-active {
  color: var(--c-primary);
  font-weight: 500;
  background: var(--c-bg);
}
.content {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}
</style>
