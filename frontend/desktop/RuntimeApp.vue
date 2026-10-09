<template>
  <main class="runtime-app">
    <header class="runtime-product"><strong>{{ acceptance ? 'AID Work Runtime 验收版' : 'AID Work Runtime' }}</strong><span>执行节点</span></header>
    <div v-if="acceptance" class="inline-notice warning runtime-profile" role="status">人工验收版 · 使用独立设备数据与测试插件。请安装正式版本后用于正式任务。</div>
    <RuntimeManagementPage v-if="port" :port="port" />
    <p v-else class="inline-notice error" role="alert">本机 Runtime 管理入口不可用，请核对安装包。</p>
  </main>
</template>
<script setup lang="ts">
import { RuntimeManagementPage } from './features/runtime'
const port = window.agentDesktop?.runtimeHost
const acceptance = window.agentDesktop?.runtime.productProfile === 'acceptance'
</script>
<style scoped>
.runtime-app { height: 100%; overflow: auto; background: var(--d-bg); }
.runtime-product { display: flex; align-items: center; gap: 16px; padding: 16px 32px; border-bottom: 1px solid var(--d-line); }
.runtime-product strong { font-family: var(--d-font-display); }
.runtime-product span { color: var(--d-text-muted); }
.runtime-profile { margin: 20px 32px 0; }
</style>
