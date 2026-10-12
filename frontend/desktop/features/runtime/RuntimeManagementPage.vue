<template>
  <section class="workspace-page runtime-page" aria-labelledby="runtime-title">
    <div class="page-kicker">RUNTIME</div>
    <div class="home-heading">
      <div><h1 id="runtime-title">本机执行环境</h1><p>连接企业服务并管理本机第一方插件。</p></div>
      <button type="button" class="runtime-secondary" :disabled="view.connecting || view.busy || recovery.busy" @click="controller.connect()">{{ view.connecting ? '连接中…' : view.connected ? '重新连接管理服务' : '连接管理服务' }}</button>
    </div>

    <div v-if="view.error" class="inline-notice error" role="alert">{{ view.error }} <button v-if="view.retryAvailable" type="button" class="status-action" :disabled="view.busy || recovery.busy" @click="controller.retry()">查询原请求结果</button><button v-if="controller.supportsRecovery" type="button" class="status-action" :disabled="recovery.busy" @click="showRecovery">查看连接处理步骤</button></div>
    <div v-if="view.connected && (view.stateDirty || view.pluginsDirty)" class="inline-notice warning" role="status">状态正在刷新，旧信息暂不可用于操作。<button type="button" class="status-action" @click="controller.refresh()">重新刷新</button></div>

    <section class="runtime-panel" aria-labelledby="connection-title">
      <div class="runtime-section-heading"><h2 id="connection-title">设备连接</h2><button type="button" class="status-action" :disabled="!view.connected" @click="controller.refresh()">刷新状态</button></div>
      <p v-if="!view.state" class="runtime-muted">{{ view.connected ? '正在读取设备状态…' : '管理连接未建立。' }}</p>
      <template v-else>
        <dl class="runtime-facts">
          <div><dt>执行状态</dt><dd>{{ stateLabels[view.state.state] }}</dd></div>
          <div><dt>服务连接</dt><dd>{{ connectionLabels[view.state.connection] }}</dd></div>
          <div><dt>设备</dt><dd>{{ view.state.device?.name || view.state.device?.device_id || '尚未配对' }}</dd></div>
        </dl>
        <div v-if="view.state.state === 'draining'" class="inline-notice warning" role="status">正在停止领取并等待已接受的任务收尾。等待时间较长时可以刷新操作进度。</div>
        <div v-if="view.state.state === 'reconciling' || view.state.state === 'blocked'" class="inline-notice warning" role="status">{{ view.state.blocked_reason || '执行事实需要核对，请保留当前设备身份及执行记录。' }}<button v-if="controller.supportsRecovery" type="button" class="status-action" :disabled="recovery.busy" @click="showRecovery">查看连接处理步骤</button></div>
        <div class="runtime-actions">
          <button class="primary-button runtime-primary" type="button" :disabled="cannotAct || view.state.state !== 'stopped' || !view.state.device" @click="controller.mutate('start')">启动执行</button>
          <button class="runtime-secondary" type="button" :disabled="cannotStop || !['starting', 'running', 'reconciling'].includes(view.state.state)" @click="controller.mutate('stop')">停止执行</button>
        </div>
      </template>
      <form class="runtime-pair-form" @submit.prevent="pair">
        <div class="runtime-section-heading"><h3>{{ view.state?.device ? '更换设备配对' : '配对设备' }}</h3></div>
        <p class="runtime-muted">执行运行时，临时网络中断会自动重试；已停止需启动执行，待核对或已阻断需处理对应原因。配对会生成新的设备编号，名称可沿用；完成后需在网页中选定新设备。配对前需停止执行，未确认的旧任务仍保留在原设备。</p>
        <div class="field"><label for="runtime-server">服务地址</label><input id="runtime-server" v-model.trim="form.server" type="url" placeholder="https://企业服务地址" autocomplete="url" required :disabled="!canPair"></div>
        <div class="field"><label for="runtime-device-name">设备名称</label><input id="runtime-device-name" v-model.trim="form.device_name" maxlength="100" placeholder="例如 招聘工作电脑" autocomplete="off" required :disabled="!canPair"></div>
        <div class="field"><label for="runtime-pairing-code">一次性配对码</label><input id="runtime-pairing-code" v-model.trim="form.pairing_code" type="password" autocomplete="off" placeholder="输入管理员提供的配对码" required :disabled="!canPair"></div>
        <button type="submit" class="primary-button runtime-primary" :disabled="!canPair">{{ view.busy ? '处理中…' : '确认配对' }}</button>
      </form>
    </section>

    <section v-if="controller.supportsRecovery" ref="recoveryPanel" class="runtime-panel" aria-labelledby="recovery-title" aria-live="polite">
      <div class="runtime-section-heading"><h2 id="recovery-title">连接检查与恢复</h2><button type="button" class="runtime-secondary" :disabled="recovery.busy || view.busy" @click="controller.diagnoseConnection()">{{ recovery.busy ? '正在检查或恢复…' : '检查服务器连接' }}</button></div>
      <p class="runtime-muted">离线、配对失效或本机管理异常时，从这里检查。检查只验证连接，不领取任务。只关闭窗口会保留后台；托盘中的“退出”才会退出客户端。</p>
      <p v-if="recovery.error" class="field-error" role="alert">{{ recovery.error }}</p>
      <template v-if="recovery.diagnosis">
        <p :class="['inline-notice', recovery.diagnosis.service_verified ? 'success' : 'warning']">{{ recovery.diagnosis.summary }}（{{ recovery.diagnosis.category }}）</p>
        <p v-if="recovery.diagnosis.service_verified" class="runtime-muted">已收到服务器心跳响应。执行仍需单独启动；BOSS插件需安装、登录并就绪。</p>
        <ol class="runtime-recovery-steps"><li v-for="(step, index) in recovery.diagnosis.steps" :key="index">{{ step }}</li></ol>
        <div class="runtime-actions">
          <button v-if="recovery.diagnosis.can_preserve" type="button" class="runtime-secondary" :disabled="recovery.busy || view.busy" @click="recoveryMode = 'preserve'; recoveryConfirmed = false">保留原配对，修复管理连接</button>
          <button v-if="recovery.diagnosis.can_reconfigure" type="button" class="runtime-secondary" :disabled="recovery.busy || view.busy" @click="recoveryMode = 'new_device'; recoveryConfirmed = false">重新配置连接</button>
          <button v-if="recovery.diagnosis.requires_reboot || recovery.diagnosis.category === 'EXECUTION_ACTIVE'" type="button" class="runtime-secondary" :disabled="recovery.busy || view.busy" @click="recoveryMode = 'prepare_reboot'; recoveryConfirmed = false">登记重启恢复</button>
        </div>
      </template>
      <form v-if="recoveryMode" class="runtime-pair-form" @submit.prevent="recoverConnection">
        <h3>{{ recoveryMode === 'preserve' ? '修复原设备' : recoveryMode === 'new_device' ? '建立新设备连接' : '登记重启恢复' }}</h3>
        <p class="runtime-muted">{{ recoveryMode === 'preserve' ? '保留设备编号、配对和插件，仅归档异常管理记录。旧任务记录保留，未确认的旧任务仍需核对。' : recoveryMode === 'new_device' ? '旧资料完整归档，不会清空。使用新配对码建立新设备编号，名称可以沿用；随后在网页选定新设备，并重新安装ZIP中的BOSS插件。旧任务不会转移到新设备。' : '登记旧执行资源的重启核验信息，保留任务记录。完成登记后，请正常重启Windows；重新打开客户端并检查连接，再继续恢复。' }}</p>
        <template v-if="recoveryMode === 'new_device'">
          <div class="field"><label for="recovery-server">企业服务地址</label><input id="recovery-server" v-model.trim="recoveryForm.server" type="url" required autocomplete="url" placeholder="https://企业服务地址" :disabled="recovery.busy"></div>
          <div class="field"><label for="recovery-name">设备名称</label><input id="recovery-name" v-model.trim="recoveryForm.device_name" required maxlength="100" autocomplete="off" :disabled="recovery.busy"></div>
          <div class="field"><label for="recovery-code">新的配对码</label><input id="recovery-code" v-model.trim="recoveryForm.pairing_code" required type="password" maxlength="128" autocomplete="off" :disabled="recovery.busy"></div>
        </template>
        <label class="runtime-confirm"><input v-model="recoveryConfirmed" type="checkbox" :disabled="recovery.busy">我已了解上述处理方式，确认继续</label>
        <div class="runtime-actions"><button type="submit" class="primary-button runtime-primary" :disabled="!recoveryConfirmed || recovery.busy || view.busy">{{ recovery.busy ? '处理中…' : '确认处理' }}</button><button type="button" class="runtime-secondary" :disabled="recovery.busy" @click="recoveryMode = ''; recoveryForm.pairing_code = ''">取消</button></div>
      </form>
    </section>

    <section class="runtime-panel" aria-labelledby="plugins-title">
      <div class="runtime-section-heading"><h2 id="plugins-title">第一方插件</h2><button v-if="controller.supportsPlugins" type="button" class="primary-button runtime-primary" :disabled="cannotAct || view.pluginsDirty" @click="controller.importPackage()">安装 / 升级离线包</button></div>
      <p v-if="!controller.supportsPlugins" class="runtime-muted">当前 Runtime 尚未提供第一方插件管理能力。</p>
      <p v-else class="runtime-muted">选择已签名的 BOSS、微信或企业微信离线包。安装成功后默认启用，软件登录等条件决定是否就绪。</p>
      <p v-if="view.selectionLabel" class="runtime-muted">已选择：{{ view.selectionLabel }}</p>
      <p v-if="view.plugins && !view.plugins.plugins.length" class="runtime-empty">尚未安装插件。设备连接与插件安装可分别完成。</p>
      <p v-else-if="!view.plugins" class="runtime-muted">{{ view.connected ? '正在读取插件列表…' : '连接后查看已安装插件。' }}</p>
      <ul v-else class="runtime-plugin-list">
        <li v-for="plugin in view.plugins.plugins" :key="plugin.installation_id">
          <div class="runtime-plugin-heading"><h3>{{ plugin.display_name }}</h3><span class="runtime-muted">{{ plugin.version || '版本未知' }}</span></div>
          <p class="runtime-plugin-state"><span>{{ plugin.enabled ? '已启用' : '已停用' }}</span><span :class="plugin.ready ? 'ready' : 'pending'">{{ plugin.ready ? '已就绪' : '未就绪' }}</span></p>
          <p v-if="plugin.reason" class="runtime-muted">{{ plugin.reason }}</p>
          <div v-if="controller.supportsPlugins" class="runtime-actions">
            <button type="button" class="runtime-secondary" :disabled="cannotAct || view.pluginsDirty" @click="controller.mutate(plugin.enabled ? 'plugins.disable' : 'plugins.enable', { installation_id: plugin.installation_id })">{{ plugin.enabled ? '停用' : '启用' }}</button>
            <button type="button" class="runtime-secondary runtime-danger" :disabled="cannotAct || view.pluginsDirty" @click="confirmRemoval = plugin.installation_id">卸载</button>
          </div>
          <div v-if="confirmRemoval === plugin.installation_id" class="inline-notice warning" role="alert">
            确认卸载 {{ plugin.display_name }}？执行记录和未确认结果会保留。
            <div class="runtime-actions"><button class="runtime-secondary runtime-danger" type="button" :disabled="cannotAct || view.pluginsDirty" @click="uninstall(plugin.installation_id)">确认卸载</button><button class="runtime-secondary" type="button" @click="confirmRemoval = ''">取消</button></div>
          </div>
        </li>
      </ul>
    </section>
    <section v-if="view.operation" class="runtime-panel" aria-labelledby="operation-title" aria-live="polite">
      <div class="runtime-section-heading"><h2 id="operation-title">管理操作</h2><button class="status-action" type="button" :disabled="!view.connected" @click="controller.refreshOperation()">刷新进度</button></div>
      <p>{{ operationLabels[view.operation.operation] }} · {{ operationStatusLabels[view.operation.status] }}</p>
      <p v-if="view.operation.message" class="runtime-muted">{{ view.operation.message }}</p>
      <p v-if="view.operation.error" class="field-error" role="alert">{{ view.operation.error }}（代码 {{ view.operation.code }}）</p>
      <p class="runtime-operation-id">操作编号：{{ view.operation.operation_id }}</p>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { RuntimeController } from './controller'
import type { RuntimeManagementPort, ConnectionRecoveryInput } from './contracts'

const props = defineProps<{ port: RuntimeManagementPort }>()
const controller = new RuntimeController(props.port)
const view = controller.view
const recovery = controller.recovery
const recoveryPanel = ref<HTMLElement | null>(null)
const recoveryMode = ref<ConnectionRecoveryInput['mode'] | ''>('')
const recoveryConfirmed = ref(false)
const recoveryForm = reactive({ server: '', device_name: '', pairing_code: '' })
const form = reactive({ server: '', device_name: '', pairing_code: '' })
const confirmRemoval = ref('')
const cannotAct = computed(() => !view.connected || view.busy || recovery.busy || view.retryAvailable || view.operation?.status === 'running' || view.stateDirty)
const cannotStop = computed(() => !view.connected || view.busy || recovery.busy || view.retryAvailable || view.operation?.status === 'running' || view.stateDirty)
const canPair = computed(() => !cannotAct.value && view.state?.state === 'stopped')
const stateLabels = { stopped: '已停止', starting: '启动中', running: '运行中', draining: '收尾中', reconciling: '待核对', blocked: '已阻断' }
const connectionLabels = { unpaired: '未配对', connecting: '连接中', online: '在线', offline: '离线', revoked: '授权已撤回' }
const operationLabels = { start: '启动', stop: '停止', pair: '配对', import: '安装 / 升级', enable: '启用', disable: '停用', uninstall: '卸载' }
const operationStatusLabels = { running: '处理中', succeeded: '已完成', failed: '失败', reconciling: '待核对' }
async function pair() {
  if (!canPair.value) return
  const params = { ...form }
  form.pairing_code = ''
  await controller.mutate('pair', params)
}
async function uninstall(installationId: string) {
  confirmRemoval.value = ''
  await controller.mutate('plugins.uninstall', { installation_id: installationId })
}
async function recoverConnection() {
  if (!recoveryMode.value || !recoveryConfirmed.value || recovery.busy || view.busy) return
  const mode = recoveryMode.value
  const input: ConnectionRecoveryInput = mode === 'new_device' ? { mode, confirmed: true, ...recoveryForm } : { mode, confirmed: true }
  recoveryForm.pairing_code = ''; recoveryMode.value = ''; recoveryConfirmed.value = false
  await controller.reconfigureConnection(input)
}
function showRecovery() { recoveryPanel.value?.scrollIntoView({ block: 'start' }); if (!recovery.diagnosis) void controller.diagnoseConnection() }
onMounted(() => { void (async () => { await controller.connect(); if (!view.state || ['blocked', 'reconciling'].includes(view.state.state) || view.state.connection === 'revoked') await controller.diagnoseConnection() })() })
onUnmounted(() => { form.pairing_code = ''; recoveryForm.pairing_code = ''; controller.dispose() })
</script>

<style scoped>
.runtime-page { max-width: 1100px; }
.runtime-panel { margin-top: 24px; padding: 24px; border: 1px solid var(--d-line); background: var(--d-surface); }
.runtime-section-heading, .runtime-plugin-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.runtime-section-heading h2 { font: 500 20px var(--d-font-display); margin: 0; }
.runtime-section-heading h3, .runtime-plugin-heading h3 { margin: 0; font: 500 16px var(--d-font-display); }
.runtime-muted { color: var(--d-text-muted); overflow-wrap: anywhere; }
.runtime-section-heading + .runtime-muted { margin-top: 12px; }
.runtime-facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin: 20px 0; }
.runtime-facts dt { color: var(--d-text-muted); font-size: 12px; }.runtime-facts dd { margin: 4px 0 0; overflow-wrap: anywhere; }
.runtime-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 12px; }
.runtime-primary { padding: 8px 18px; min-height: 38px; width: fit-content; }
.runtime-secondary { padding: 8px 14px; min-height: 38px; border: 1px solid var(--d-line-strong); border-radius: var(--d-radius); background: var(--d-surface-raised); color: var(--d-text); cursor: pointer; }
.runtime-secondary:disabled { color: var(--d-text-muted); opacity: .6; cursor: not-allowed; }
.runtime-danger { color: var(--d-danger); }
.runtime-pair-form { display: grid; gap: 16px; margin-top: 24px; padding-top: 24px; border-top: 1px solid var(--d-line); }.runtime-pair-form .runtime-muted { margin: 0; }
.runtime-plugin-list { padding: 0; margin: 16px 0 0; list-style: none; }.runtime-plugin-list > li { padding: 20px 0; border-top: 1px solid var(--d-line); }.runtime-plugin-list > li:last-child { padding-bottom: 0; }
.runtime-plugin-state { display: flex; gap: 18px; margin: 10px 0; }.runtime-empty { padding: 24px 0 0; color: var(--d-text-muted); }
.runtime-page > .inline-notice { margin-top: 20px; }.runtime-plugin-list .inline-notice { margin-top: 14px; }
.runtime-operation-id { color: var(--d-text-muted); font: 11px var(--d-font-mono); overflow-wrap: anywhere; }
.runtime-recovery-steps { padding-left: 24px; line-height: 1.8; overflow-wrap: anywhere; }.runtime-confirm { display: flex; align-items: center; gap: 8px; }
@media (max-width: 620px) { .runtime-panel { padding: 16px; }.runtime-section-heading { align-items: flex-start; flex-direction: column; }.runtime-facts { grid-template-columns: 1fr; }.runtime-page { padding: 20px; } }
</style>
