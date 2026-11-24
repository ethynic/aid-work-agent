<template>
  <div class="border border-default rounded-lg p-4">
    <div class="flex items-start justify-between gap-3 flex-wrap">
      <div class="min-w-0">
        <div class="flex items-center gap-2">
          <span class="text-sm font-medium text-default">{{ title }}</span>
          <BaseBadge v-if="state?.granted" intent="success" size="sm">已开通</BaseBadge>
          <BaseBadge v-else intent="neutral" size="sm">未开通</BaseBadge>
        </div>
        <p class="text-xs text-muted mt-1">{{ description }}</p>
      </div>
      <div class="flex gap-2">
        <BaseButton v-if="!state?.granted" size="sm" :disabled="operating" @click="openGrantModal">
          {{ operating ? '处理中…' : '开通数据源' }}
        </BaseButton>
        <BaseButton v-else intent="danger-ghost" size="sm" :disabled="operating" @click="handleRevoke">
          {{ operating ? '处理中…' : '停用开通' }}
        </BaseButton>
      </div>
    </div>

    <!-- 已开通：只读展示当前配置（同步细节由租户管理员在连接中心配置） -->
    <div v-if="state?.granted && state.source" class="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-2 mt-3 text-sm">
      <div>
        <span class="text-xs text-muted block">同步方式</span>
        {{ state.source.enabled ? '自动同步' : '仅手动同步' }}
      </div>
      <div>
        <span class="text-xs text-muted block">同步频率</span>
        {{ state.source.enabled ? `每 ${state.source.sync_interval_hours} 小时` : '—' }}
      </div>
      <div>
        <span class="text-xs text-muted block">数据库连接</span>
        {{ state.source.connection_configured ? '已配置（加密存储）' : '未配置' }}
      </div>
      <div>
        <span class="text-xs text-muted block">最近同步</span>
        {{ formatDate(state.source.last_sync_at) || '尚未同步' }}
      </div>
    </div>
    <div
      v-if="state?.granted && state.source?.last_error"
      class="mt-2 rounded bg-danger-50 border border-danger-200 px-2 py-1 text-xs text-danger-800"
    >
      最近错误：{{ state.source.last_error }}
    </div>

    <!-- 开通表单：客户数据库连接配置（password 必填；凭据加密落库、不回显） -->
    <BaseModal v-model="grantModalVisible" :title="`开通${title}`" size="md" mode="create">
      <div class="space-y-3">
        <p class="text-xs text-muted">
          填写客户业务库只读连接（开通后可再次填写以更新连接配置）。凭据 Fernet 加密存储，平台界面不回显。
        </p>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="block text-xs font-medium text-muted mb-1">主机 host *</label>
            <BaseInput v-model="form.db_host" size="sm" placeholder="例如 192.168.1.10" />
          </div>
          <div>
            <label class="block text-xs font-medium text-muted mb-1">端口 port *</label>
            <BaseInput v-model="form.db_port" size="sm" type="number" placeholder="3306" />
          </div>
          <div>
            <label class="block text-xs font-medium text-muted mb-1">数据库 *</label>
            <BaseInput v-model="form.db_database" size="sm" placeholder="数据库名" />
          </div>
          <div>
            <label class="block text-xs font-medium text-muted mb-1">用户 *</label>
            <BaseInput v-model="form.db_user" size="sm" placeholder="只读账号" />
          </div>
          <div class="col-span-2">
            <label class="block text-xs font-medium text-muted mb-1">密码 *</label>
            <BaseInput v-model="form.db_password" size="sm" type="password" placeholder="必填" autocomplete="new-password" />
          </div>
        </div>
        <p v-if="formError" class="text-xs text-danger-600">{{ formError }}</p>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="grantModalVisible = false">取消</BaseButton>
        <BaseButton :disabled="operating" @click="handleGrant">
          {{ operating ? '开通中…' : '确认开通' }}
        </BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { useToast } from 'vue-toastification'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import {
  getAdminSource,
  grantSource,
  revokeSource,
  type AdminSyncSourceState,
} from '@/api/employmentSync'

const props = defineProps<{
  tenantId: string
  modulePath: string
  entryLabel: string
  title: string
  description: string
}>()

const emit = defineEmits<{ (e: 'changed'): void }>()

const toast = useToast()
const operating = ref(false)
const state = ref<AdminSyncSourceState | null>(null)
const grantModalVisible = ref(false)
const formError = ref('')
const form = reactive({
  db_host: '',
  db_port: '3306',
  db_database: '',
  db_user: '',
  db_password: '',
})

async function load() {
  if (!props.tenantId) return
  try {
    state.value = await getAdminSource(props.modulePath, props.tenantId)
  } catch (e) {
    toast.error((e as Error).message || '加载授权状态失败')
  }
}

function openGrantModal() {
  formError.value = ''
  grantModalVisible.value = true
}

async function handleGrant() {
  if (operating.value) return
  formError.value = ''
  if (!form.db_host.trim() || !form.db_database.trim() || !form.db_user.trim() || !form.db_password) {
    formError.value = '请完整填写连接配置（密码必填）'
    return
  }
  operating.value = true
  try {
    state.value = await grantSource(props.modulePath, props.tenantId, {
      db_host: form.db_host.trim(),
      db_port: Number(form.db_port) || 3306,
      db_database: form.db_database.trim(),
      db_user: form.db_user.trim(),
      db_password: form.db_password,
    })
    grantModalVisible.value = false
    form.db_password = ''
    toast.success(`已开通，租户连接中心将出现「${props.entryLabel}」入口`)
    emit('changed')
  } catch (e) {
    formError.value = (e as Error).message || '开通失败'
  } finally {
    operating.value = false
  }
}

async function handleRevoke() {
  if (operating.value) return
  if (!confirm(`确定停用该租户的「${props.title}」数据源？停用后：租户侧入口立即隐藏、定时同步停止、排队中的同步将被取消；已入库知识与历史记录保留。重新开通需重新填写数据库连接，租户此前的范围/频率设置不保留。`)) return
  operating.value = true
  try {
    await revokeSource(props.modulePath, props.tenantId)
    await load()
    toast.success('已停用开通')
    emit('changed')
  } catch (e) {
    toast.error((e as Error).message || '停用失败')
  } finally {
    operating.value = false
  }
}

function formatDate(value: string | null): string {
  if (!value) return ''
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

watch(() => props.tenantId, (id) => { if (id) load() }, { immediate: true })
</script>
