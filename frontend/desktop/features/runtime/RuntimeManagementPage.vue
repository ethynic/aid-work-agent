<template>
  <section class="workspace-page runtime-page" aria-labelledby="runtime-title">
    <div class="page-kicker">RUNTIME</div>
    <div class="home-heading">
      <div><h1 id="runtime-title">本机执行环境</h1><p>连接企业服务并管理本机第一方插件。</p></div>
      <button type="button" class="runtime-secondary" :disabled="view.connecting || view.busy || recovery.busy" @click="controller.connect()">{{ view.connecting ? '连接中…' : view.connected ? '重新连接管理服务' : '连接管理服务' }}</button>
    </div>

    <div v-if="view.error" class="inline-notice error" role="alert">{{ view.error }} <button v-if="view.retryAvailable" type="button" class="status-action" :disabled="view.busy || recovery.busy" @click="controller.retry()">查询原请求结果</button><button v-if="controller.supportsRecovery" type="button" class="status-action" @click="showRecovery">查看连接处理步骤</button></div>
    <div v-else-if="view.state?.state === 'blocked'" class="inline-notice error" role="alert">{{ view.state.blocked_reason || '本机管理状态异常，请按下方步骤处理。' }}<button v-if="controller.supportsRecovery" type="button" class="status-action" @click="showRecovery">查看连接处理步骤</button></div>
    <div v-if="view.connected && view.refreshing" class="inline-notice warning" role="status">正在读取设备状态，请稍候。<button type="button" class="status-action" :disabled="recovery.busy || view.busy" @click="controller.refresh()">重新刷新</button></div>

    <div v-if="view.connected && view.stateDirty && !view.refreshing" class="inline-notice warning" role="status">设备状态未能完整读取，请按连接处理步骤检查。<button v-if="controller.supportsRecovery" type="button" class="status-action" @click="showRecovery">查看连接处理步骤</button></div>

    <section v-if="controller.supportsRecovery" ref="recoveryPanel" class="runtime-panel" aria-labelledby="recovery-title" aria-live="polite" tabindex="-1">
      <div class="runtime-section-heading"><h2 id="recovery-title">连接检查与恢复</h2><button v-if="!recoveryDetailsVisible" type="button" class="status-action" @click="showRecovery">查看处理步骤</button><button type="button" class="runtime-secondary" :disabled="recovery.busy || view.busy" @click="controller.diagnoseConnection()">{{ recovery.busy ? '正在检查或恢复…' : '检查连接并给出处理方式' }}</button></div>
      <div v-if="recoveryDetailsVisible">
      <p class="runtime-muted">先检查本机配置与服务器连接；确认修复时再核对旧执行资源。检查不领取任务。只关闭窗口会保留后台；托盘中的“退出”才会退出客户端。</p>
      <div v-if="!recovery.diagnosis" class="inline-notice warning" role="status">
        <p>{{ recovery.busy ? '正在检查本机配置、凭据与服务器连接。处理步骤仍可查看，完成后会显示可用的修复方式。' : '先点击上方“检查连接并给出处理方式”。客户端会判断能否保留原配对修复，或需要使用新配对码建立连接。' }}</p>
        <ol class="runtime-recovery-steps"><li>原设备身份和历史记录会保留。故障状态下显示不出设备信息，不代表原配对已经失效。</li><li>检查完成后，优先选择“保留原配对，修复管理连接”；只有原配置或凭据无法恢复时才重新配置。</li><li>如提示需要重启，先点击“登记重启恢复”，登记成功后正常重启 Windows，再继续处理。</li></ol>
      </div>
      <p v-else-if="recovery.busy" class="runtime-muted" role="status">正在检查或恢复，请等待当前结果。下方保留上次检查的步骤，恢复按钮暂时不可重复提交。</p>
      <p v-if="recovery.error" class="field-error" role="alert">{{ recovery.error }}</p>
      <template v-if="recovery.diagnosis">
        <p :class="['inline-notice', recovery.diagnosis.service_verified ? 'success' : 'warning']">{{ recovery.diagnosis.summary }}（{{ recovery.diagnosis.category }}）</p>
        <p v-if="recovery.diagnosis.service_verified" class="runtime-muted">已收到服务器心跳响应。执行仍需单独启动；BOSS插件需安装、登录并就绪。</p>
        <ol class="runtime-recovery-steps"><li v-for="(step, index) in recovery.diagnosis.steps" :key="index">{{ step }}</li></ol>
        <div class="runtime-actions">
          <button v-if="recovery.diagnosis.can_preserve" type="button" class="runtime-secondary" :disabled="recovery.busy || view.busy" @click="recoveryMode = 'preserve'; recoveryConfirmed = false">保留原配对，修复管理连接</button>
          <button v-if="recovery.diagnosis.can_reconfigure" type="button" class="runtime-secondary" :disabled="recovery.busy || view.busy" @click="recoveryMode = 'new_device'; recoveryConfirmed = false">重新配置连接</button>
          <button v-if="controller.supportsRegistration && recovery.registered" type="button" class="primary-button runtime-primary" :disabled="recovery.busy || view.busy" @click="controller.reconfigureConnection({ mode: 'continue_registration', confirmed: true })">完成连接配置</button>
          <button v-if="recovery.diagnosis.requires_reboot || recovery.diagnosis.category === 'EXECUTION_ACTIVE'" type="button" class="runtime-secondary" :disabled="recovery.busy || view.busy" @click="recoveryMode = 'prepare_reboot'; recoveryConfirmed = false">登记重启恢复</button>
        </div>
      </template>
      <form v-if="recoveryMode" class="runtime-pair-form runtime-recovery-form" @submit.prevent="recoverConnection">
        <h3>{{ recoveryMode === 'preserve' ? '修复原设备' : recoveryMode === 'new_device' ? '建立新设备连接' : '登记重启恢复' }}</h3>
        <p class="runtime-muted">{{ recoveryMode === 'preserve' ? '保留设备编号、配对和插件，仅归档异常管理记录。旧任务记录保留，未确认的旧任务仍需核对。' : recoveryMode === 'new_device' ? '使用新配对码注册新设备，名称可以沿用。旧资料保留，无需查找密钥或备份；网页选择更换旧设备生成的码，可在新连接完成后注销指定旧设备。旧任务不会转移。' : '登记旧执行资源的重启核验信息，保留任务记录和已经保存的新凭据。完成登记后，请正常重启Windows；重新打开客户端，继续完成连接配置，无需再次输入配对码。' }}</p>
        <template v-if="recoveryMode === 'new_device'">
          <div class="field"><label for="recovery-server">企业服务地址</label><input id="recovery-server" v-model.trim="recoveryForm.server" type="url" required autocomplete="url" placeholder="https://企业服务地址" :disabled="recovery.busy"></div>
          <div class="field"><label for="recovery-name">设备名称</label><input id="recovery-name" v-model.trim="recoveryForm.device_name" required maxlength="100" autocomplete="off" :disabled="recovery.busy"></div>
          <div class="field"><label for="recovery-code">新的配对码</label><input id="recovery-code" v-model.trim="recoveryForm.pairing_code" required type="password" maxlength="128" autocomplete="off" :disabled="recovery.busy"></div>
        </template>
        <label class="runtime-confirm"><input v-model="recoveryConfirmed" type="checkbox" :disabled="recovery.busy">我已了解上述处理方式，确认继续</label>
        <div class="runtime-actions"><button type="submit" class="primary-button runtime-primary" :disabled="!recoveryConfirmed || recovery.busy || view.busy">{{ recovery.busy ? '处理中…' : '确认处理' }}</button><button type="button" class="runtime-secondary" :disabled="recovery.busy" @click="recoveryMode = ''; recoveryForm.pairing_code = ''">取消</button></div>
      </form>
      </div>
    </section>

    <section class="runtime-panel" aria-labelledby="connection-title">
      <div class="runtime-section-heading"><h2 id="connection-title">设备连接</h2><button type="button" class="status-action" :disabled="!view.connected || recovery.busy || view.busy" @click="controller.refresh()">刷新状态</button></div>
      <p v-if="!view.state" class="runtime-muted">{{ view.connected ? '正在读取设备状态…' : '管理连接未建立。' }}</p>
      <template v-else>
        <dl class="runtime-facts">
          <div><dt>执行状态</dt><dd>{{ stateLabels[view.state.state] }}</dd></div>
          <div><dt>服务连接</dt><dd>{{ needsRecovery && view.state.connection === 'unpaired' ? '连接尚未确认' : connectionLabels[view.state.connection] }}</dd></div>
          <div><dt>设备</dt><dd>{{ view.state.device?.name || view.state.device?.device_id || (needsRecovery ? '原配对信息暂不可读，请按上方步骤检查' : '尚未配对') }}</dd></div>
        </dl>
        <div v-if="view.state.state === 'draining'" class="inline-notice warning" role="status">正在停止领取并等待已接受的任务收尾。等待时间较长时可以刷新操作进度。</div>
        <div v-if="view.state.state === 'reconciling' || view.state.state === 'blocked'" class="inline-notice warning" role="status">{{ view.state.blocked_reason || '执行事实需要核对，请保留当前设备身份及执行记录。' }}<button v-if="controller.supportsRecovery" type="button" class="status-action" @click="showRecovery">查看连接处理步骤</button></div>
        <div class="runtime-actions">
          <button class="primary-button runtime-primary" type="button" :disabled="cannotAct || view.state.state !== 'stopped' || !view.state.device" @click="controller.mutate('start')">启动执行</button>
          <button class="runtime-secondary" type="button" :disabled="cannotStop || !['starting', 'running', 'reconciling'].includes(view.state.state)" @click="controller.mutate('stop')">停止执行</button>
        </div>
      </template>
      <form v-if="!controller.supportsRecovery || view.state?.state === 'stopped'" class="runtime-pair-form" @submit.prevent="pair">
        <div class="runtime-section-heading"><h3>{{ view.state?.device ? '更换设备配对' : '配对设备' }}</h3></div>
        <p class="runtime-muted">执行运行时，临时网络中断会自动重试；已停止需启动执行，待核对或已阻断需处理对应原因。配对会生成新的设备编号，名称可沿用；完成后需在网页中选定新设备。配对前需停止执行，未确认的旧任务仍保留在原设备。</p>
        <div class="field"><label for="runtime-server">服务地址</label><input id="runtime-server" v-model.trim="form.server" type="url" placeholder="https://企业服务地址" autocomplete="url" required :disabled="!canPair"></div>
        <div class="field"><label for="runtime-device-name">设备名称</label><input id="runtime-device-name" v-model.trim="form.device_name" maxlength="100" placeholder="例如 招聘工作电脑" autocomplete="off" required :disabled="!canPair"></div>
        <div class="field"><label for="runtime-pairing-code">一次性配对码</label><input id="runtime-pairing-code" v-model.trim="form.pairing_code" type="password" autocomplete="off" placeholder="输入管理员提供的配对码" required :disabled="!canPair"></div>
        <button type="submit" class="primary-button runtime-primary" :disabled="!canPair">{{ view.busy ? '处理中…' : '确认配对' }}</button>
      </form>
    </section>


    <section class="runtime-panel" aria-labelledby="plugins-title">
      <div class="runtime-section-heading"><h2 id="plugins-title">第一方插件</h2><button v-if="controller.supportsPlugins" type="button" class="primary-button runtime-primary" :disabled="cannotAct || view.pluginsDirty" @click="controller.importPackage()">安装 / 升级离线包</button></div>
      <p v-if="!controller.supportsPlugins" class="runtime-muted">{{ needsRecovery ? '当前故障状态下暂不读取插件列表，原安装资料会保留。先按上方连接处理步骤恢复。' : '当前 Runtime 尚未提供第一方插件管理能力。' }}</p>
      <p v-else class="runtime-muted">选择已签名的 BOSS、微信或企业微信离线包。安装成功后默认启用，软件登录等条件决定是否就绪。</p>
      <p v-if="view.selectionLabel" class="runtime-muted">已选择：{{ view.selectionLabel }}</p>
      <p v-if="view.plugins && !view.plugins.plugins.length" class="runtime-empty">尚未安装插件。设备连接与插件安装可分别完成。</p>
      <p v-else-if="controller.supportsPlugins && !view.plugins" class="runtime-muted">{{ view.connected ? (view.refreshing ? '正在读取插件列表…' : '插件列表未能读取，请刷新或按连接处理步骤检查。') : '连接后查看已安装插件。' }}</p>
      <ul v-if="view.plugins" class="runtime-plugin-list">
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
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { RuntimeController } from './controller'
import type { RuntimeManagementPort, ConnectionRecoveryInput } from './contracts'

const props = defineProps<{ port: RuntimeManagementPort }>()
const controller = new RuntimeController(props.port)
const view = controller.view
const recovery = controller.recovery
const recoveryPanel = ref<HTMLElement | null>(null)
const recoveryExpanded = ref(false)
const needsRecovery = computed(() => !!view.error || view.state?.state === 'blocked' || view.state?.state === 'reconciling' || view.state?.connection === 'revoked')
const recoveryDetailsVisible = computed(() => recoveryExpanded.value || needsRecovery.value || recovery.busy || !!recovery.diagnosis || !!recovery.error)
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
  if (controller.supportsRegistration) await controller.reconfigureConnection({ mode: 'register_new', confirmed: true, ...params })
  else await controller.mutate('pair', params)
}
async function uninstall(installationId: string) {
  confirmRemoval.value = ''
  await controller.mutate('plugins.uninstall', { installation_id: installationId })
}
async function recoverConnection() {
  if (!recoveryMode.value || !recoveryConfirmed.value || recovery.busy || view.busy) return
  const mode = recoveryMode.value
  const input: ConnectionRecoveryInput = mode === 'new_device' ? { mode: controller.supportsRegistration ? 'register_new' : 'new_device', confirmed: true, ...recoveryForm } : { mode, confirmed: true }
  recoveryForm.pairing_code = ''; recoveryMode.value = ''; recoveryConfirmed.value = false
  await controller.reconfigureConnection(input)
}
async function showRecovery() {
  recoveryExpanded.value = true
  await nextTick()
  recoveryPanel.value?.scrollIntoView?.({ block: 'start' })
  recoveryPanel.value?.focus({ preventScroll: true })
}
onMounted(() => { void controller.connect() })
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
