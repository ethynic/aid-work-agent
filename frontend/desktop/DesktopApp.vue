<template>
  <StartupBlocker v-if="state.phase === 'booting' || state.phase === 'secure-store-ready'" code="BOOT" title="正在建立安全工作区" message="正在检查系统凭证存储与服务连接。" tone="neutral" />
  <StartupBlocker v-else-if="state.phase === 'fatal-local'" code="LOCAL / FATAL" title="桌面端无法安全启动" :message="state.message || '本地产物或安全凭证存储不可用。'" tone="fatal"><p class="blocker-help">退出应用，确认系统密钥存储已解锁后重试。若问题持续，请联系管理员并提供应用版本。</p></StartupBlocker>
  <StartupBlocker v-else-if="state.phase === 'update-required'" code="UPDATE / REQUIRED" title="必须更新后才能继续" :message="`当前版本 ${state.currentVersion} 低于最低支持版本 ${state.minimumVersion}。`" tone="warning">
    <p v-if="updateState?.status === 'disabled'" class="blocker-help">当前安装包不支持自动更新，请从企业受控渠道安装新版本。</p>
    <p v-else-if="updateState?.status === 'error'" class="blocker-help" role="alert">更新检查失败，请稍后重试。</p>
    <p v-else-if="updateState?.status === 'downloading'" class="blocker-help" role="status">正在下载更新{{ updateState.percent === undefined ? '' : ` · ${Math.round(updateState.percent)}%` }}</p>
    <button class="primary-button compact" type="button" :disabled="updateBusy || updateState?.status === 'disabled'" @click="runUpdateAction">{{ updateActionLabel }}</button>
  </StartupBlocker>
  <DesktopLoginPage v-else-if="state.phase === 'auth-required'" :offline="state.connectivity === 'offline'" />
  <DesktopShell v-else-if="state.phase === 'authenticated' && state.session" :username="state.session.user.username" :tenant-code="state.session.tenantCode" :connectivity="state.connectivity" :platform-label="platformLabel" @retry="controller.retryConnectivity" @sign-out="controller.signOut" />
</template>

<script setup lang="ts">
import { computed, inject, onMounted, onUnmounted, ref } from 'vue'
import { desktopControllerKey } from '@desktop/app/context'
import DesktopShell from '@desktop/components/DesktopShell.vue'
import StartupBlocker from '@desktop/components/StartupBlocker.vue'
import DesktopLoginPage from '@desktop/pages/DesktopLoginPage.vue'
const controller = inject(desktopControllerKey)
if (!controller) throw new Error('Desktop controller is unavailable')
const state = controller.state
const platformLabel = computed(() => window.agentDesktop?.runtime.platform === 'darwin' ? 'macOS' : 'Windows')
const updateState = ref<DesktopUpdateState | null>(null)
const updateBusy = ref(false)
let unsubscribeUpdates: (() => void) | undefined
let updateRevision = 0
const updateActionLabel = computed(() => {
  if (updateState.value?.status === 'available') return '下载更新'
  if (updateState.value?.status === 'downloaded') return '重启并安装'
  if (updateState.value?.status === 'checking') return '正在检查…'
  if (updateState.value?.status === 'downloading') return '正在下载…'
  if (updateState.value?.status === 'disabled') return '自动更新不可用'
  return '检查更新'
})
async function runUpdateAction() {
  const updates = window.agentDesktop?.updates
  if (!updates || updateBusy.value) return
  updateBusy.value = true
  try {
    if (updateState.value?.status === 'available') await updates.download()
    else if (updateState.value?.status === 'downloaded') await updates.restartAndInstall()
    else await updates.check()
  } catch {
    updateState.value = { status: 'error', currentVersion: state.currentVersion }
  } finally {
    updateBusy.value = false
  }
}
onMounted(async () => {
  const updates = window.agentDesktop?.updates
  if (!updates) return
  unsubscribeUpdates = updates.onState((nextState) => { updateRevision += 1; updateState.value = nextState })
  const initialRevision = updateRevision
  try {
    const initialState = await updates.getState()
    if (initialRevision === updateRevision) updateState.value = initialState
  } catch {
    if (initialRevision === updateRevision) updateState.value = { status: 'error', currentVersion: state.currentVersion }
  }
})
onUnmounted(() => { updateRevision += 1; unsubscribeUpdates?.() })
</script>
