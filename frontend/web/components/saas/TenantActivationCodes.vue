<template>
  <div class="flex flex-col gap-3">
    <!-- 工具条 -->
    <div class="flex items-center justify-between">
      <div class="text-xs text-muted">
        为该租户生成协会客户端激活码；客户端用激活码换 access_token 绑定机器。
      </div>
      <div class="flex gap-2">
        <BaseButton size="sm" intent="ghost" @click="loadCodes">刷新</BaseButton>
        <BaseButton size="sm" @click="openGenerate">生成激活码</BaseButton>
      </div>
    </div>

    <!-- 列表 -->
    <div v-if="loading" class="text-center py-6 text-muted text-sm">加载中…</div>
    <div v-else-if="codes.length === 0" class="text-center py-8 text-muted text-sm border border-dashed border-default rounded-lg">
      暂无激活码，点击右上角「生成激活码」
    </div>
    <BaseTable v-else :columns="columns" :data="codes" row-key="id">
      <template #code="{ row }">
        <div class="flex items-center gap-1">
          <span class="font-mono text-sm text-default">{{ row.code }}</span>
          <button
            class="text-muted hover:text-primary-600 transition-colors rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
            title="复制激活码"
            aria-label="复制激活码"
            @click="copyCode(row.code)"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-2M8 5a2 2 0 002 2h4a2 2 0 002-2M8 5a2 2 0 012-2h4a2 2 0 012 2m0 0h2a2 2 0 012 2v3" />
            </svg>
          </button>
        </div>
      </template>
      <template #status="{ row }">
        <BaseBadge :intent="statusBadge(row.status).intent" size="sm">
          {{ statusBadge(row.status).label }}
        </BaseBadge>
      </template>
      <template #usage="{ row }">
        <span class="text-sm text-default">{{ row.used_count }}/{{ row.max_uses }}</span>
      </template>
      <template #expires_at="{ row }">
        <span class="text-sm text-muted">{{ row.expires_at ? formatDate(row.expires_at) : '永久' }}</span>
      </template>
      <template #created_at="{ row }">
        <span class="text-sm text-muted">{{ formatDate(row.created_at) }}</span>
      </template>
      <template #actions="{ row }">
        <div class="flex justify-center gap-1">
          <BaseButton
            v-if="row.status !== 'disabled'"
            intent="ghost" size="sm" title="踢该码已激活的客户端下线"
            @click="handleRevoke(row)"
          >吊销</BaseButton>
          <BaseButton
            v-if="row.status !== 'disabled'"
            intent="danger-ghost" size="sm"
            @click="handleDisable(row)"
          >禁用</BaseButton>
          <span v-else class="text-xs text-muted">已禁用</span>
        </div>
      </template>
      <template #empty>暂无激活码</template>
    </BaseTable>

    <!-- 生成激活码子弹窗 -->
    <BaseModal v-model="showGenerate" title="生成激活码" size="md">
      <div class="space-y-4">
        <div>
          <label class="block">
            <span class="text-sm text-muted mb-1 block">客户名（可选）</span>
            <BaseInput v-model="genForm.client_name" placeholder="如：张三的电脑…" />
          </label>
        </div>
        <div>
          <label class="block">
            <span class="text-sm text-muted mb-1 block">最大激活次数</span>
            <BaseInput v-model="genForm.max_uses" type="number" placeholder="1" />
          </label>
          <div class="text-xs text-muted mt-1">同一激活码可在多台机器激活的次数上限，默认 1。</div>
        </div>
        <div>
          <label class="block">
            <span class="text-sm text-muted mb-1 block">有效期（可选，留空=永久）</span>
            <BaseInput v-model="genForm.expires_at" type="datetime-local" />
          </label>
        </div>
        <div v-if="genError" class="p-2 bg-danger-50 border border-danger-200 rounded text-danger-600 text-sm">{{ genError }}</div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showGenerate = false">取消</BaseButton>
        <BaseButton :disabled="generating" @click="handleGenerate">
          {{ generating ? '生成中…' : '生成' }}
        </BaseButton>
      </template>
    </BaseModal>

    <!-- 生成结果（明文 code 当场展示 + 复制） -->
    <BaseModal v-model="showCreated" title="激活码已生成" size="sm">
      <div class="text-center space-y-3">
        <div class="text-sm text-muted">请立即复制并妥善保存，分派给客户用于客户端激活：</div>
        <div class="py-3 bg-surface-hover rounded-lg">
          <span class="font-mono text-xl font-bold text-primary-600 tracking-wide select-all">{{ createdCode?.code }}</span>
        </div>
        <BaseButton size="sm" @click="copyCode(createdCode?.code || '')">复制激活码</BaseButton>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showCreated = false">我已保存</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { useToast } from 'vue-toastification'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseTable, { type TableColumn } from '@/components/ui/BaseTable.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import {
  listActivationCodes,
  createActivationCode,
  disableActivationCode,
  revokeActivationBinding,
  type ActivationCode,
  type CreatedActivationCode,
} from '@/api/clientActivation'

const props = defineProps<{ tenantId: string }>()
const toast = useToast()

const columns: TableColumn[] = [
  { key: 'code', label: '激活码', width: '220px' },
  { key: 'status', label: '状态', width: '90px', thAlign: 'center' },
  { key: 'usage', label: '已用/上限', width: '90px', thAlign: 'center' },
  { key: 'expires_at', label: '有效期', width: '150px' },
  { key: 'client_name', label: '客户名', width: '140px' },
  { key: 'created_at', label: '创建时间', width: '150px' },
  { key: 'actions', label: '操作', width: '130px', thAlign: 'center' },
]

const codes = ref<ActivationCode[]>([])
const loading = ref(false)

// 生成激活码
const showGenerate = ref(false)
const generating = ref(false)
const genError = ref('')
const genForm = ref({ client_name: '', max_uses: '1', expires_at: '' })

// 生成结果
const showCreated = ref(false)
const createdCode = ref<CreatedActivationCode | null>(null)

async function loadCodes() {
  if (!props.tenantId) return
  loading.value = true
  try {
    codes.value = await listActivationCodes(props.tenantId)
  } catch (e: any) {
    toast.error(e?.message || '加载激活码失败')
  } finally {
    loading.value = false
  }
}

function openGenerate() {
  genForm.value = { client_name: '', max_uses: '1', expires_at: '' }
  genError.value = ''
  showGenerate.value = true
}

async function handleGenerate() {
  genError.value = ''
  const maxUses = parseInt(genForm.value.max_uses, 10)
  if (isNaN(maxUses) || maxUses < 1) {
    genError.value = '最大激活次数需为不小于 1 的整数'
    return
  }
  generating.value = true
  try {
    const created = await createActivationCode({
      tenant_id: props.tenantId,
      client_name: genForm.value.client_name.trim() || undefined,
      max_uses: maxUses,
      // datetime-local 值形如 2026-08-15T14:30，直接作为 ISO 传给后端（后端 fromisoformat 解析为本地时间）
      expires_at: genForm.value.expires_at || undefined,
    })
    createdCode.value = created
    showGenerate.value = false
    showCreated.value = true
    await loadCodes()
    toast.success('激活码已生成')
  } catch (e: any) {
    genError.value = e?.message || '生成激活码失败'
  } finally {
    generating.value = false
  }
}

async function handleDisable(row: any) {
  if (!confirm(`确定禁用激活码 ${row.code}？禁用后未激活的不可再激活。`)) return
  try {
    await disableActivationCode(row.id)
    toast.success('已禁用')
    await loadCodes()
  } catch (e: any) {
    toast.error(e?.message || '禁用失败')
  }
}

async function handleRevoke(row: any) {
  if (!confirm(`确定吊销激活码 ${row.code} 关联的客户端绑定？将踢已激活的客户端下线。`)) return
  try {
    const { revoked_count } = await revokeActivationBinding(row.id)
    toast.success(`已吊销 ${revoked_count} 个客户端绑定`)
    await loadCodes()
  } catch (e: any) {
    toast.error(e?.message || '吊销失败')
  }
}

async function copyCode(code: string) {
  if (!code) return
  try {
    await navigator.clipboard.writeText(code)
    toast.success('已复制：' + code)
  } catch {
    // 降级：选中文本提示手动复制
    toast.info('请手动复制：' + code)
  }
}

function statusBadge(status: string): { intent: 'success' | 'neutral' | 'danger' | 'info'; label: string } {
  if (status === 'unused') return { intent: 'success', label: '未使用' }
  if (status === 'used') return { intent: 'neutral', label: '已用完' }
  if (status === 'disabled') return { intent: 'danger', label: '已禁用' }
  return { intent: 'info', label: status }
}

function formatDate(iso: string | null): string {
  if (!iso) return '-'
  // 后端返回本地时间不带 Z，直接解析（禁止拼时区后缀，见 frontend_dev.md）
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

onMounted(loadCodes)
watch(() => props.tenantId, (id) => { if (id) loadCodes() })
</script>
