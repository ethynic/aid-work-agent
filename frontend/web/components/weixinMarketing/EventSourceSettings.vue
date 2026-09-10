<template>
  <!-- 事件源设置（P4-B）：列表（凭据掩码）+ 创建（internal/webhook）+ 轮换（确认 + 一次性明文） -->
  <div class="page-container p-5">
    <div class="page-content flex-1 flex flex-col min-h-0">
      <div class="page-toolbar">
        <div class="page-toolbar-left flex-wrap">
          <BaseSelect :model-value="typeFilter" size="sm" class="w-36" @update:model-value="onTypeFilter">
            <option value="">全部类型</option>
            <option value="webhook">webhook</option>
            <option value="internal">internal</option>
          </BaseSelect>
          <BaseButton size="sm" intent="secondary" :disabled="loading" @click="load">刷新</BaseButton>
        </div>
        <div class="page-toolbar-right">
          <BaseButton size="sm" @click="openCreate">新增事件源</BaseButton>
        </div>
      </div>

      <div v-if="error && !items.length" class="page-content p-4">
        <p class="text-sm text-danger-600 mb-2">{{ error }}</p>
        <BaseButton size="sm" intent="secondary" @click="load">重试</BaseButton>
      </div>

      <div v-else class="table-scroll-wrapper flex-1">
        <BaseTable :columns="columns" :data="items" row-key="id">
          <template #source_ref="{ row }">
            <span class="text-sm font-medium text-default" :title="row.source_ref">{{ row.source_ref }}</span>
          </template>
          <template #source_type="{ row }">
            <BaseBadge :intent="row.source_type === 'webhook' ? 'info' : 'neutral'">{{ row.source_type }}</BaseBadge>
          </template>
          <template #status="{ row }">
            <BaseBadge :intent="row.status === 'active' ? 'success' : 'warning'">
              {{ row.status === 'active' ? '启用' : row.status }}
            </BaseBadge>
          </template>
          <template #allowed_event_types="{ row }">
            <span class="text-xs text-muted" :title="(row.allowed_event_types || []).join(', ')">
              {{ row.allowed_event_types && row.allowed_event_types.length ? row.allowed_event_types.join('、') : '不限' }}
            </span>
          </template>
          <template #keys="{ row }">
            <div class="flex items-center gap-1 flex-wrap justify-center">
              <BaseBadge
                v-for="key in row.keys"
                :key="key.key_id"
                :intent="key.status === 'active' ? 'success' : key.status === 'retiring' ? 'warning' : 'neutral'"
                :title="keyStatusHint(key.status)"
              >
                {{ key.key_id }} v{{ key.key_version }}
              </BaseBadge>
              <span v-if="!row.keys.length" class="text-xs text-muted">—</span>
            </div>
          </template>
          <template #created_at="{ row }">{{ formatDateTime(row.created_at) }}</template>
          <template #actions="{ row }">
            <div class="flex items-center justify-center gap-1 whitespace-nowrap">
              <BaseButton
                v-if="row.source_type === 'webhook'"
                size="sm"
                intent="ghost"
                title="复制 webhook 接收路径"
                @click="copyWebhookUrl(row as EventSourceItem)"
              >路径</BaseButton>
              <BaseButton
                v-if="row.source_type === 'webhook'"
                size="sm"
                intent="ghost"
                :disabled="rotating === row.id"
                @click="askRotate(row as EventSourceItem)"
              >{{ rotating === row.id ? '...' : '轮换密钥' }}</BaseButton>
            </div>
          </template>
          <template v-if="loading && !items.length" #empty>加载中...</template>
          <template v-else-if="!items.length" #empty>
            {{ error ? '加载失败' : '暂无事件源，点击右上角「新增事件源」创建' }}
          </template>
        </BaseTable>
      </div>
    </div>

    <!-- 创建弹窗 -->
    <BaseModal :model-value="showCreate" title="新增事件源" size="sm" @update:model-value="v => (showCreate = v)">
      <form class="space-y-3" @submit.prevent="submitCreate">
        <label class="block">
          <span class="text-sm text-default">类型</span>
          <BaseSelect v-model="createForm.source_type" size="sm" class="mt-1 w-full">
            <option value="webhook">webhook（外部签名推送）</option>
            <option value="internal">internal（内部业务事件）</option>
          </BaseSelect>
        </label>
        <label class="block">
          <span class="text-sm text-default">标识（source_ref）</span>
          <BaseInput
            v-model="createForm.source_ref"
            size="sm"
            class="mt-1"
            placeholder="如 crm-order-events"
            maxlength="128"
            required
          />
        </label>
        <label class="block">
          <span class="text-sm text-default">允许的事件类型（逗号分隔，留空=不限）</span>
          <BaseInput
            v-model="createForm.allowedTypes"
            size="sm"
            class="mt-1"
            placeholder="order.completed, order.refunded"
          />
        </label>
        <p class="text-xs text-muted">
          webhook 源创建后生成签名密钥；明文 secret 仅创建响应一次性展示，请立即保存。
        </p>
        <div class="flex justify-end gap-2">
          <BaseButton size="sm" intent="secondary" type="button" :disabled="creating" @click="showCreate = false">取消</BaseButton>
          <BaseButton size="sm" type="submit" :disabled="creating || !createForm.source_ref.trim()">
            {{ creating ? '创建中...' : '创建' }}
          </BaseButton>
        </div>
      </form>
    </BaseModal>

    <!-- 一次性密钥展示（创建/轮换） -->
    <BaseModal
      :model-value="!!secretReveal"
      title="密钥一次性展示"
      size="sm"
      @update:model-value="v => !v && (secretReveal = null)"
    >
      <div v-if="secretReveal" class="space-y-3" data-testid="secret-reveal">
        <p class="text-sm text-warning-700">
          以下明文仅本次展示，关闭后无法再次获取（列表与后续接口不回明文/旧密钥）。
        </p>
        <div class="rounded border border-default bg-canvas p-2 font-mono text-xs break-all">
          {{ secretReveal.secret }}
        </div>
        <div class="text-xs text-muted">key_id：{{ secretReveal.keyId }}</div>
        <div v-if="secretReveal.url" class="text-xs text-muted break-all">
          接收路径：POST {{ secretReveal.url }}（签名头 X-WX-Signature/Timestamp/Nonce/Key-Id）
        </div>
        <div class="flex justify-end gap-2">
          <BaseButton size="sm" intent="secondary" @click="copySecret">复制密钥</BaseButton>
          <BaseButton size="sm" @click="secretReveal = null">我已保存，关闭</BaseButton>
        </div>
      </div>
    </BaseModal>

    <!-- 轮换确认 -->
    <BaseModal :model-value="!!rotateTarget" title="确认轮换签名密钥" size="sm" @update:model-value="v => !v && (rotateTarget = null)">
      <div v-if="rotateTarget" class="space-y-3">
        <p class="text-sm text-default">
          将为事件源 <span class="font-medium">{{ rotateTarget.source_ref }}</span> 生成新密钥；
          旧密钥在并行窗内（默认 15 分钟）仍可验签，窗后自动失效。
        </p>
        <p class="text-xs text-muted">仅事件源创建者或平台管理员可执行轮换。</p>
        <div class="flex justify-end gap-2">
          <BaseButton size="sm" intent="secondary" @click="rotateTarget = null">取消</BaseButton>
          <BaseButton size="sm" intent="danger" :disabled="rotating === rotateTarget.id" @click="confirmRotate">
            {{ rotating === rotateTarget.id ? '轮换中...' : '确认轮换' }}
          </BaseButton>
        </div>
      </div>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useToast } from 'vue-toastification'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import {
  createEventSource,
  listEventSources,
  rotateEventSourceKey,
  type EventSourceItem,
  type EventSourceType,
} from '@/api/weixinMarketing'
import { formatDateTime } from './weixinDisplay'

const toast = useToast()

const loading = ref(false)
const error = ref('')
const items = ref<EventSourceItem[]>([])
const typeFilter = ref<'' | EventSourceType>('')

const showCreate = ref(false)
const creating = ref(false)
const createForm = ref({ source_type: 'webhook' as EventSourceType, source_ref: '', allowedTypes: '' })

const secretReveal = ref<{ secret: string; keyId: string; url?: string } | null>(null)
const rotateTarget = ref<EventSourceItem | null>(null)
const rotating = ref('')

const columns = [
  { key: 'source_ref', label: '标识', width: '18%' },
  { key: 'source_type', label: '类型', width: '10%' },
  { key: 'status', label: '状态', width: '10%' },
  { key: 'allowed_event_types', label: '事件类型白名单', width: '22%' },
  { key: 'keys', label: '密钥（掩码）', width: '20%' },
  { key: 'created_at', label: '创建时间', width: '12%' },
  { key: 'actions', label: '操作', width: '8%' },
]

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await listEventSources({ source_type: typeFilter.value })
    items.value = result.items
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载事件源失败'
  } finally {
    loading.value = false
  }
}

function onTypeFilter(value: string | number) {
  typeFilter.value = (value as '' | EventSourceType) || ''
  load()
}

function openCreate() {
  createForm.value = { source_type: 'webhook', source_ref: '', allowedTypes: '' }
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  try {
    const allowed = createForm.value.allowedTypes
      .split(',')
      .map(s => s.trim())
      .filter(Boolean)
    const result = await createEventSource({
      source_ref: createForm.value.source_ref.trim(),
      source_type: createForm.value.source_type,
      allowed_event_types: allowed.length ? allowed : undefined,
    })
    showCreate.value = false
    if (result.secret) {
      secretReveal.value = {
        secret: result.secret,
        keyId: result.key_id || '',
        url: result.source.webhook_url,
      }
    } else {
      toast.success('internal 事件源已创建（无需密钥）')
    }
    await load()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '创建事件源失败')
  } finally {
    creating.value = false
  }
}

function askRotate(row: EventSourceItem) {
  rotateTarget.value = row
}

async function confirmRotate() {
  if (!rotateTarget.value) return
  rotating.value = rotateTarget.value.id
  try {
    const result = await rotateEventSourceKey(rotateTarget.value.id)
    rotateTarget.value = null
    secretReveal.value = {
      secret: result.secret,
      keyId: result.key_id,
      url: `/api/weixin-marketing/webhooks/${result.source_id}`,
    }
    await load()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '轮换密钥失败')
  } finally {
    rotating.value = ''
  }
}

function copySecret() {
  if (!secretReveal.value) return
  navigator.clipboard?.writeText(secretReveal.value.secret).then(
    () => toast.success('已复制'),
    () => toast.warning('复制失败，请手动选择复制'),
  )
}

function keyStatusHint(status: string): string {
  if (status === 'active') return '当前验签密钥'
  if (status === 'retiring') return '轮换并行窗内的旧密钥：窗口期内仍可验签，到期自动失效（不再用于新签名）'
  return '已退役密钥（仅留档）'
}

function copyWebhookUrl(row: EventSourceItem) {
  const url = row.webhook_url || `/api/weixin-marketing/webhooks/${row.id}`
  navigator.clipboard?.writeText(url).then(
    () => toast.success(`已复制 ${url}`),
    () => toast.warning('复制失败，请手动选择复制'),
  )
}

onMounted(load)
</script>
