<template>
  <div>
    <div class="card">
      <h2>登录引导</h2>
      <ol class="steps">
        <li>点击下方「启动浏览器」，客户端会用一个全新的临时 Chrome 窗口打开 BOSS 登录页。</li>
        <li>在 Chrome 中<strong>用 BOSS 直聘 App 扫码登录</strong>（本工具不自动化登录过程）。</li>
        <li>登录后进入「推荐牛人」页面，选好岗位，<strong>关闭所有遮挡弹窗</strong>。</li>
        <li>确认页面就绪后，回到本窗口点击「登录完成，开始工作」。</li>
      </ol>
      <p class="muted hint">
        安全说明：确认之前客户端不会连接浏览器；确认后也只使用只读快照与模拟鼠标键盘，
        不会读取 Cookie、密码或页面脚本数据。
      </p>
    </div>

    <div class="card">
      <h2>当前状态</h2>
      <div class="status-row">
        <span class="muted">状态机</span>
        <span v-if="status" :class="stateBadgeClass(status.state)">{{ STATE_LABELS[status.state] }}</span>
        <span v-else class="muted">加载中…</span>
      </div>
      <div class="status-row">
        <span class="muted">CDP 连接</span>
        <span :class="status?.connected ? 'badge badge-ok' : 'badge badge-neutral'">
          {{ status?.connected ? '已连接' : '未连接' }}
        </span>
      </div>
      <div v-if="status?.pauseReason" class="status-row">
        <span class="muted">暂停原因</span>
        <span class="error-text">{{ status.pauseReason }}</span>
      </div>
      <div v-if="launchResult" class="status-row">
        <span class="muted">Chrome</span>
        <span class="mono">pid {{ launchResult.pid }} / 调试端口 {{ launchResult.port }}</span>
      </div>
      <div v-if="error" class="status-row">
        <span class="muted">错误</span>
        <span class="error-text">{{ error }}</span>
      </div>

      <div class="actions">
        <button
          class="btn"
          :disabled="busy || (status !== null && status.state !== 'IDLE' && !canRelaunch)"
          @click="onLaunch"
        >
          启动浏览器
        </button>
        <button
          class="btn"
          :disabled="busy || !canConfirm"
          @click="onConfirm"
        >
          登录完成，开始工作
        </button>
        <button
          v-if="canRelaunch"
          class="btn btn-secondary"
          :disabled="busy"
          @click="onReset"
        >
          复位重新登录
        </button>
      </div>
      <p v-if="status?.state === 'WAITING_MANUAL_LOGIN'" class="muted hint">
        Chrome 已启动。请完成扫码登录和页面准备，然后点击「登录完成，开始工作」。
      </p>
      <p v-if="status?.connected" class="muted hint">
        已连接。前往「任务控制台」选择岗位并开始任务。
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useSession } from '../composables/useSession'
import { STATE_LABELS, stateBadgeClass } from '../stateLabels'
import type { ChromeLaunchResult } from '@shared/ipc'

const { status, refresh } = useSession()
const busy = ref(false)
const error = ref('')
const launchResult = ref<ChromeLaunchResult | null>(null)

const canConfirm = computed(
  () =>
    status.value?.state === 'WAITING_MANUAL_LOGIN' ||
    (status.value?.state === 'PAUSED' && !status.value?.connected),
)
const canRelaunch = computed(
  () =>
    status.value?.state === 'STOPPED' ||
    status.value?.state === 'COMPLETED' ||
    status.value?.state === 'WAITING_MANUAL_LOGIN' ||
    (status.value?.state === 'PAUSED' && !status.value?.connected),
)

async function onLaunch() {
  busy.value = true
  error.value = ''
  try {
    const result = await window.bossResume.chrome.launch()
    if (!result.ok) {
      error.value = result.error ?? '启动失败'
      return
    }
    launchResult.value = result
    await refresh()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

async function onConfirm() {
  busy.value = true
  error.value = ''
  try {
    await window.bossResume.session.confirmLogin()
    await refresh()
    if (status.value && !status.value.connected) {
      error.value = status.value.pauseReason ?? '连接失败'
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

async function onReset() {
  busy.value = true
  error.value = ''
  try {
    await window.bossResume.session.reset()
    await refresh()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.steps {
  margin: 0;
  padding-left: 20px;
  font-size: 13px;
  line-height: 2;
}
.hint {
  font-size: 12px;
  margin: 12px 0 0;
  line-height: 1.6;
}
.status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 6px 0;
  font-size: 13px;
}
.error-text {
  color: var(--c-danger);
  font-size: 12px;
  text-align: right;
  word-break: break-all;
}
.actions {
  display: flex;
  gap: 10px;
  margin-top: 16px;
}
</style>
