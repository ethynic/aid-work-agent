<template>
  <div class="bg-white rounded-xl shadow-sm border border-default p-6">
    <div class="flex items-center justify-between mb-4">
      <h2 class="text-base font-semibold text-default">通知渠道</h2>
      <button
        v-if="activeTarget"
        @click="helpOpen = true"
        class="text-xs text-primary-600 hover:text-primary-700 underline"
      >如何获取 webhook？</button>
    </div>

    <div v-if="loading" class="text-center py-6 text-muted text-sm">加载中...</div>

    <template v-else>
      <!-- 当前已启用渠道 -->
      <div v-if="activeTarget" class="mb-4 p-3 rounded-lg bg-success-50 border border-success-200 flex items-center justify-between">
        <div class="text-sm text-success-700">
          已启用「{{ providerLabel(activeTarget.provider) }}」
          <span class="text-xs text-success-600 block mt-0.5">
            配置内容不回显；更新请在下方重新填写并保存，切换渠道会自动停用当前渠道
          </span>
        </div>
        <div class="flex gap-2 flex-shrink-0">
          <BaseButton size="sm" intent="secondary" :disabled="testing" @click="handleTest">
            {{ testing ? '发送中...' : '发送测试' }}
          </BaseButton>
          <BaseButton size="sm" intent="danger-ghost" :disabled="saving" @click="handleRevoke">
            停用
          </BaseButton>
        </div>
      </div>

      <!-- 渠道选择 + 参数 -->
      <div class="space-y-4">
        <div>
          <label class="block text-sm text-default mb-1">外发渠道（四选一，可不启用；站内信始终开启）</label>
          <div class="grid grid-cols-2 gap-2">
            <button
              v-for="p in PROVIDERS"
              :key="p.value"
              type="button"
              @click="selectedProvider = p.value"
              :class="[
                'px-3 py-2 rounded-lg border text-sm transition-colors text-left',
                selectedProvider === p.value
                  ? 'border-primary-500 bg-primary-50 text-primary-700 font-medium'
                  : 'border-gray-200 text-gray-600 hover:border-gray-300'
              ]"
            >{{ p.label }}</button>
          </div>
        </div>

        <div>
          <label class="block text-sm text-default mb-1">
            {{ selectedProvider === 'smtp' ? '收件邮箱地址' : '机器人 Webhook 地址' }}
            <span class="text-xs text-muted ml-1">{{ selectedProvider === 'smtp' ? '' : '（需 https 官方域名）' }}</span>
          </label>
          <BaseInput
            v-model="configValue"
            :placeholder="selectedProvider === 'smtp' ? 'name@example.com' : 'https://...'"
            size="md"
          />
        </div>

        <div v-if="message" class="p-3 rounded-lg text-sm"
          :class="messageType === 'success' ? 'bg-success-50 border border-success-200 text-success-600' : 'bg-danger-50 border border-danger-200 text-danger-600'">
          {{ message }}
        </div>

        <div class="flex items-center justify-between">
          <button
            v-if="!activeTarget"
            type="button"
            @click="helpOpen = true"
            class="text-xs text-primary-600 hover:text-primary-700 underline"
          >如何获取 webhook？</button>
          <span v-else></span>
          <BaseButton :disabled="saving || !selectedProvider || !configValue.trim()" @click="handleSave">
            {{ saving ? '保存中...' : activeTarget ? '保存更新' : '保存启用' }}
          </BaseButton>
        </div>
      </div>
    </template>

    <!-- 帮助弹框 -->
    <BaseModal v-model="helpOpen" title="如何获取机器人 Webhook" size="md">
      <div class="space-y-3 text-sm text-default leading-relaxed">
        <template v-if="selectedProvider === 'feishu_bot'">
          <p>1. 在飞书中打开目标群聊 → 群设置 → <b>群机器人</b> → 添加机器人 → <b>自定义机器人</b></p>
          <p>2. 复制生成的 <b>Webhook 地址</b> 粘贴到上方输入框</p>
          <p>3. 安全设置选择「自定义关键词」，关键词填 <b>通知</b>（通知内容自动包含该词）</p>
        </template>
        <template v-else-if="selectedProvider === 'dingtalk_bot'">
          <p>1. 在钉钉中打开目标群聊 → 群设置 → <b>机器人</b> → 添加机器人 → <b>自定义（通过 Webhook 接入）</b></p>
          <p>2. 安全设置勾选「自定义关键词」，关键词填 <b>通知</b>，完成创建后复制 <b>Webhook 地址</b></p>
          <p>3. 粘贴到上方输入框（暂不支持加签方式，请使用自定义关键词）</p>
        </template>
        <template v-else-if="selectedProvider === 'wecom_bot'">
          <p>1. 在企业微信中打开目标群聊 → 右上角「...」→ <b>群机器人</b> → 添加机器人 → <b>新创建一个机器人</b></p>
          <p>2. 创建后复制 <b>Webhook 地址</b> 粘贴到上方输入框</p>
        </template>
        <template v-else-if="selectedProvider === 'smtp'">
          <p>填写接收通知的<b>邮箱地址</b>即可，无需任何配置（发件由平台统一邮箱发出）。</p>
          <p class="text-xs text-muted">提示：请留意邮箱的垃圾邮件 / 订阅邮件分组。</p>
        </template>
        <template v-else>
          <p>请先在上方选择外发渠道，将显示对应的配置指引。</p>
        </template>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="helpOpen = false">我知道了</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import { notificationsAPI, type TenantTarget } from '@/api/notifications'

const PROVIDERS = [
  { value: 'feishu_bot', label: '飞书群机器人', channel: 'im_webhook', configKey: 'webhook_url' },
  { value: 'dingtalk_bot', label: '钉钉群机器人', channel: 'im_webhook', configKey: 'webhook_url' },
  { value: 'wecom_bot', label: '企微群机器人', channel: 'im_webhook', configKey: 'webhook_url' },
  { value: 'smtp', label: '邮件', channel: 'email', configKey: 'email_address' },
] as const

const loading = ref(true)
const saving = ref(false)
const testing = ref(false)
const message = ref('')
const messageType = ref<'success' | 'error'>('success')
const helpOpen = ref(false)
const selectedProvider = ref<string>('')
const configValue = ref('')
const activeTarget = ref<TenantTarget | null>(null)

function providerLabel(provider: string): string {
  return PROVIDERS.find((p) => p.value === provider)?.label || provider
}

function showError(e: any) {
  const code = e?.code || e?.message
  const map: Record<string, string> = {
    NOTIFICATION_PROVIDER_UNAVAILABLE: '渠道暂不可用，请稍后重试',
    NOTIFICATION_TARGET_OWNED: '该配置已被其他租户使用',
    NOTIFICATION_BINDING_CHANGED: '配置已被其他操作修改，请刷新页面后重试',
    NOTIFICATION_BINDING_CONFLICT: '渠道绑定冲突，请刷新页面后重试',
    NOTIFICATION_TEST_RATE_LIMITED: '发送过于频繁，请 1 分钟后再试',
    NOTIFICATION_ENDPOINT_FORBIDDEN: '地址不合法：需为 https 的官方渠道域名',
    NOTIFICATION_INVALID_EMAIL: '邮箱格式不正确',
    NOTIFICATION_TENANT_DISABLED: '当前租户通知功能未开放',
    NOTIFICATION_DISABLED: '通知功能未开启',
    NOTIFICATION_FORBIDDEN: '无权限执行此操作',
    NOTIFICATION_UNAVAILABLE: '通知服务暂不可用，请稍后重试',
  }
  message.value = map[code] || `操作失败（${code}）`
  messageType.value = 'error'
}

async function loadTargets() {
  loading.value = true
  try {
    const res = await notificationsAPI.listTenantTargets()
    activeTarget.value = res.targets.find((t) => t.state === 'active') || null
    if (!selectedProvider.value) {
      selectedProvider.value = activeTarget.value?.provider || 'feishu_bot'
    }
  } catch (e: any) {
    if (e?.code !== 'NOTIFICATION_DISABLED') {
      console.error('加载通知渠道配置失败:', e)
    }
    // 未开启通知的环境保持空态，不阻断企业设置页
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  const provider = PROVIDERS.find((p) => p.value === selectedProvider.value)
  if (!provider || !configValue.value.trim()) return
  saving.value = true
  message.value = ''
  try {
    // 切换渠道：先停用当前渠道（撤旧 + 登记新）
    if (activeTarget.value && activeTarget.value.provider !== provider.value) {
      await notificationsAPI.revokeTenantTarget(
        activeTarget.value.target_id,
        activeTarget.value.binding_version,
      )
      activeTarget.value = null
    }
    const body: Parameters<typeof notificationsAPI.registerTenantTarget>[0] = {
      channel: provider.channel,
      provider: provider.value,
      configuration: { [provider.configKey]: configValue.value.trim() },
    }
    if (activeTarget.value) {
      body.expected_binding_version = activeTarget.value.binding_version
    }
    activeTarget.value = await notificationsAPI.registerTenantTarget(body)
    configValue.value = ''
    message.value = '保存成功'
    messageType.value = 'success'
  } catch (e) {
    showError(e)
  } finally {
    saving.value = false
  }
}

async function handleRevoke() {
  if (!activeTarget.value) return
  if (!confirm('确定停用当前通知渠道？停用后仅站内信可达。')) return
  saving.value = true
  message.value = ''
  try {
    await notificationsAPI.revokeTenantTarget(
      activeTarget.value.target_id,
      activeTarget.value.binding_version,
    )
    activeTarget.value = null
    message.value = '已停用'
    messageType.value = 'success'
  } catch (e) {
    showError(e)
  } finally {
    saving.value = false
  }
}

async function handleTest() {
  if (!activeTarget.value) return
  testing.value = true
  message.value = ''
  try {
    await notificationsAPI.tenantTest({
      target_id: activeTarget.value.target_id,
      binding_version: activeTarget.value.binding_version,
    })
    message.value = '测试通知已发送，请到对应渠道查收'
    messageType.value = 'success'
  } catch (e) {
    showError(e)
  } finally {
    testing.value = false
  }
}

onMounted(loadTargets)
</script>
