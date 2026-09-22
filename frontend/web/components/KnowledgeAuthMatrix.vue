<template>
  <BaseModal
    :model-value="modelValue"
    title="访问授权"
    size="xl"
    mode="edit"
    :is-dirty="isDirty"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="mb-3 text-xs text-muted">
      半选 = 该数字员工未配置授权（默认可访问全部栏目）；勾选 = 允许访问该栏目；取消勾选 = 不允许。修改后点击「保存」生效。
    </div>

    <div v-if="isLoading" class="flex items-center justify-center py-12 text-sm text-muted">加载中...</div>
    <div v-else-if="loadError" class="flex items-center justify-center py-12 text-sm text-danger-600">{{ loadError }}</div>
    <div v-else class="table-scroll-wrapper">
      <BaseTable :columns="columns" :data="matrixRows" row-key="source_type" table-fixed>
        <template #name="{ row }">
          <span class="text-sm text-default">{{ row.display_name }}</span>
        </template>
        <template v-for="a in agents" :key="a.agentId" #[agentCellSlot(a)]="{ row }">
          <div class="flex justify-center">
            <input
              type="checkbox"
              class="table-checkbox"
              :checked="cellState(a, row.source_type) === 'checked'"
              :indeterminate="cellState(a, row.source_type) === 'indeterminate'"
              @change="handleCellClick(a, row.source_type, $event)"
            />
          </div>
        </template>
        <template v-for="a in agents" :key="a.agentId" #[agentHeaderSlot(a)]>
          <div class="flex flex-col items-center gap-1">
            <span class="text-xs font-medium text-default normal-case tracking-normal">{{ a.name }}</span>
            <BaseBadge v-if="isAgentDirty(a)" intent="warning">未保存</BaseBadge>
            <BaseBadge v-else-if="a.configured" intent="info">已配置</BaseBadge>
            <BaseBadge v-else intent="neutral">默认全部</BaseBadge>
          </div>
        </template>
        <template #empty>暂无数字员工或一级栏目</template>
      </BaseTable>
    </div>

    <template #footer>
      <span v-if="saveError" class="mr-auto text-xs text-danger-600">{{ saveError }}</span>
      <BaseButton intent="secondary" @click="handleCloseClick">关闭</BaseButton>
      <BaseButton :disabled="!isDirty || saving" @click="handleSave">{{ saving ? '保存中...' : '保存' }}</BaseButton>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useToast } from 'vue-toastification'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import {
  getTenantAvailableUserAgents,
  getSubagentKnowledgeSources,
  setSubagentKnowledgeSources,
  type KnowledgeSourceItem,
} from '@/api/saasPermissions'

interface MatrixCategory {
  id: number
  source_type: string
  display_name: string | null
}

interface AgentState {
  agentId: string
  name: string
  configured: boolean
  checked: string[]
  initialConfigured: boolean
  initialChecked: string[]
  sharedItems: KnowledgeSourceItem[]
}

const props = defineProps<{
  modelValue: boolean
  tenantId: string
  categories: MatrixCategory[]
}>()

const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const toast = useToast()
const agents = ref<AgentState[]>([])
const isLoading = ref(false)
const loadError = ref('')
const saving = ref(false)
const saveError = ref('')

const matrixRows = computed(() =>
  props.categories.map(c => ({ source_type: c.source_type, display_name: c.display_name || c.source_type }))
)

const columns = computed(() => [
  { key: 'name', label: '栏目', width: '180px' },
  ...agents.value.map(a => ({ key: a.agentId, label: a.name, width: '120px' })),
])

function isAgentDirty(st: AgentState): boolean {
  if (st.configured !== st.initialConfigured) return true
  if (!st.configured) return false
  return [...st.checked].sort().join(',') !== [...st.initialChecked].sort().join(',')
}

const isDirty = computed(() => agents.value.some(isAgentDirty))

watch(() => props.modelValue, async (v) => {
  if (v) await loadMatrix()
})

async function loadMatrix() {
  isLoading.value = true
  loadError.value = ''
  saveError.value = ''
  agents.value = []
  try {
    const res = await getTenantAvailableUserAgents()
    const list = res.data || []
    agents.value = await Promise.all(list.map(async (a) => {
      let sources: KnowledgeSourceItem[] = []
      try {
        const r = await getSubagentKnowledgeSources(props.tenantId, a.agent_id)
        if (r.success) sources = r.data || []
      } catch {
        // 单个员工加载失败按未配置处理
      }
      const own = sources.filter(s => !s.owner_tenant_id).map(s => s.source_type)
      return {
        agentId: a.agent_id,
        name: a.display_name || a.name,
        configured: own.length > 0,
        checked: [...own],
        initialConfigured: own.length > 0,
        initialChecked: [...own],
        sharedItems: sources.filter(s => s.owner_tenant_id),
      }
    }))
  } catch (e: any) {
    loadError.value = `${e?.message || '加载失败'}（仅租户管理员可操作）`
  } finally {
    isLoading.value = false
  }
}

function cellState(st: AgentState, sourceType: string): 'checked' | 'indeterminate' | 'unchecked' {
  if (!st.configured) return 'indeterminate'
  return st.checked.includes(sourceType) ? 'checked' : 'unchecked'
}

function handleCellClick(st: AgentState, sourceType: string, ev?: Event) {
  saveError.value = ''
  if (!st.configured) {
    const catName = matrixRows.value.find(r => r.source_type === sourceType)?.display_name || sourceType
    if (!confirm(`「${st.name}」当前允许访问全部栏目。勾选后将转为仅允许显式勾选的栏目（首次仅勾选「${catName}」），可继续调整其他格子。确定？`)) {
      // confirm 取消时无状态变化、无重渲染，需手动还原浏览器已翻转的 checkbox 视觉状态
      const el = ev?.target as HTMLInputElement | undefined
      if (el) {
        el.checked = false
        el.indeterminate = true
      }
      return
    }
    st.configured = true
    st.checked = [sourceType]
    return
  }
  st.checked = st.checked.includes(sourceType)
    ? st.checked.filter(t => t !== sourceType)
    : [...st.checked, sourceType]
  // 全部取消勾选时恢复为未配置（默认全部允许）
  if (st.checked.length === 0) st.configured = false
}

async function handleSave() {
  if (saving.value || !isDirty.value) return
  saving.value = true
  saveError.value = ''
  try {
    for (const st of agents.value.filter(isAgentDirty)) {
      const ownItems: KnowledgeSourceItem[] = st.configured
        ? st.checked.map(t => ({
            source_type: t,
            display_name: props.categories.find(c => c.source_type === t)?.display_name || t,
            owner_tenant_id: null,
          }))
        : []
      const res = await setSubagentKnowledgeSources(props.tenantId, st.agentId, [...ownItems, ...st.sharedItems])
      if (!res.success) throw new Error(res.error || `保存「${st.name}」授权失败`)
      st.initialConfigured = st.configured
      st.initialChecked = [...st.checked]
    }
    toast.success('访问授权保存成功')
  } catch (e: any) {
    saveError.value = e?.message || '保存失败'
  } finally {
    saving.value = false
  }
}

function handleCloseClick() {
  if (isDirty.value && !confirm('有未保存的修改，确定关闭？')) return
  emit('update:modelValue', false)
}

function agentCellSlot(a: AgentState) {
  return a.agentId
}

function agentHeaderSlot(a: AgentState) {
  return `${a.agentId}_header`
}
</script>
