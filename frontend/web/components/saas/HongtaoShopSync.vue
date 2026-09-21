<template>
  <div class="page-container bg-canvas">
    <AppHeader
      title="商城产品同步"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    />

    <div class="page-content p-6 space-y-6">
      <!-- 源未初始化空态 -->
      <div v-if="sourceNotFound" class="bg-warning-50 border border-warning-200 rounded-lg p-4 text-sm text-warning-800">
        <p class="font-medium mb-1">数据源尚未初始化</p>
        <p class="text-warning-700">请联系平台管理员完成宏陶商城数据源初始化后再使用本页面。</p>
      </div>

      <template v-else>
        <!-- ==================== 源设置 ==================== -->
        <div class="bg-surface rounded-lg border border-default p-5">
          <div class="flex items-center justify-between mb-3">
            <h2 class="text-base font-medium text-default">同步设置</h2>
            <div class="flex items-center gap-2">
              <BaseButton intent="ghost" size="sm" :disabled="sourceLoading" @click="loadSource">刷新</BaseButton>
              <BaseButton
                size="sm"
                :disabled="sourceLoading || triggerLoading"
                @click="handleTrigger"
              >
                {{ triggerLoading ? '提交中...' : '立即同步' }}
              </BaseButton>
            </div>
          </div>

          <div v-if="sourceLoading" class="text-center py-6 text-muted text-sm">加载中...</div>
          <template v-else-if="source">
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <label class="block text-xs font-medium text-muted mb-1">定时同步</label>
                <label class="inline-flex items-center gap-2 text-sm text-default">
                  <input
                    type="checkbox"
                    class="accent-primary-600 w-4 h-4"
                    :checked="source.enabled"
                    :disabled="savingSource"
                    @change="handleToggleEnabled"
                  />
                  {{ source.enabled ? '已开启' : '已关闭' }}
                </label>
              </div>
              <div>
                <label class="block text-xs font-medium text-muted mb-1">同步频率</label>
                <BaseSelect
                  :model-value="intervalChoice"
                  size="sm"
                  :disabled="savingSource"
                  class="max-w-40"
                  @update:model-value="onIntervalChange"
                >
                  <option v-if="!INTERVAL_OPTIONS.some((o) => o.value === intervalChoice)" :value="intervalChoice">
                    每 {{ intervalChoice }} 小时
                  </option>
                  <option v-for="opt in INTERVAL_OPTIONS" :key="opt.value" :value="opt.value">
                    {{ opt.label }}
                  </option>
                </BaseSelect>
              </div>
              <div>
                <label class="block text-xs font-medium text-muted mb-1">最近同步</label>
                <p class="text-sm text-default">{{ formatTime(source.last_sync_at) || '尚未同步' }}</p>
              </div>
            </div>

            <div
              v-if="source.last_error"
              class="mt-3 rounded-lg bg-danger-50 border border-danger-200 p-3 text-xs text-danger-800"
            >
              最近错误：{{ source.last_error }}
            </div>
            <p v-if="sourceSavedTip" class="mt-2 text-xs text-success-700">{{ sourceSavedTip }}</p>
            <p v-if="sourceError" class="mt-2 text-xs text-danger-600">{{ sourceError }}</p>
          </template>
        </div>

        <!-- ==================== 产品挑选 ==================== -->
        <div class="bg-surface rounded-lg border border-default p-5">
          <div class="flex items-center justify-between mb-3 flex-wrap gap-2">
            <div>
              <h2 class="text-base font-medium text-default">产品挑选</h2>
              <p class="text-xs text-muted mt-0.5">
                {{ source?.selection_mode === 'ids'
                  ? `白名单模式：已选 ${selectedCount} 个产品，仅选中产品入库`
                  : '全部模式：所有上架产品自动入库' }}
              </p>
            </div>
            <div class="flex items-center gap-2">
              <div class="flex items-center gap-2">
                <BaseButton
                  size="sm"
                  :intent="source?.selection_mode === 'ids' ? 'primary' : 'secondary'"
                  :disabled="savingSelection"
                  @click="handleSelectionMode('ids')"
                >
                  白名单模式
                </BaseButton>
                <BaseButton
                  size="sm"
                  :intent="source?.selection_mode === 'all' ? 'primary' : 'secondary'"
                  :disabled="savingSelection"
                  @click="handleSelectionMode('all')"
                >
                  全部同步
                </BaseButton>
              </div>
            </div>
          </div>

          <!-- 白名单模式下的选择工具行 -->
          <div v-if="source?.selection_mode === 'ids'" class="flex items-center gap-3 mb-3 flex-wrap">
            <div class="flex-1 min-w-52">
              <BaseInput
                v-model="productKeyword"
                size="sm"
                placeholder="按名称 / 型号 / 编码搜索"
                @keyup.enter="searchProducts"
              />
            </div>
            <BaseButton intent="secondary" size="sm" @click="searchProducts">搜索</BaseButton>
            <span class="text-xs text-muted">共 {{ productTotal }} 个产品，本页已选 {{ pageSelectedCount }}</span>
            <BaseButton
              intent="primary"
              size="sm"
              :disabled="savingSelection || !selectionDirty"
              @click="handleSaveSelection"
            >
              {{ savingSelection ? '保存中...' : selectionDirty ? '保存挑选' : '已保存' }}
            </BaseButton>
          </div>

          <div v-if="productsLoading" class="text-center py-8 text-muted text-sm">加载中...</div>
          <template v-else>
            <BaseTable :columns="productColumns" :data="products" row-key="native_id">
              <template #select="{ row }">
                <input
                  v-if="source?.selection_mode === 'ids'"
                  type="checkbox"
                  class="accent-primary-600 w-4 h-4"
                  :checked="draftSelected.has(row.native_id)"
                  @change="toggleProduct(row.native_id)"
                />
                <BaseBadge v-else size="sm" :intent="row.selected ? 'success' : 'neutral'">
                  {{ row.selected ? '同步中' : '未同步' }}
                </BaseBadge>
              </template>
              <template #name="{ row }">
                <span class="text-sm text-default">{{ row.name || `商品 ${row.native_id}` }}</span>
              </template>
              <template #model="{ row }">{{ row.model || '-' }}</template>
              <template #procode="{ row }">{{ row.procode || '-' }}</template>
              <template #status="{ row }">
                <BaseBadge size="sm" :intent="row.status === '1' ? 'success' : 'warning'">
                  {{ row.status === '1' ? '上架' : '下架' }}
                </BaseBadge>
              </template>
              <template #has_doc="{ row }">
                <BaseBadge
                  size="sm"
                  :intent="!row.has_doc ? 'neutral' : row.user_deleted ? 'danger' : 'info'"
                >
                  {{ !row.has_doc ? '未入库' : row.user_deleted ? '已删除' : '已入库' }}
                </BaseBadge>
              </template>
            </BaseTable>
            <p v-if="products.length === 0" class="text-center text-sm text-muted py-6">暂无产品目录（完成一轮同步后可见）</p>
            <div v-if="productTotal > productPageSize" class="mt-3 flex justify-end">
              <BasePagination
                :total="productTotal"
                :current-page="productPage"
                :page-size="productPageSize"
                :show-size-changer="false"
                @change="onProductPageChange"
              />
            </div>
          </template>

          <p
            v-if="source?.selection_mode === 'ids' && selectionDirty"
            class="mt-2 text-xs text-warning-700"
          >
            保存后下一轮同步生效：取消勾选的产品将从知识库下架，重新勾选且内容未变时零费用恢复。
          </p>
        </div>

        <!-- ==================== 运行记录 ==================== -->
        <div class="bg-surface rounded-lg border border-default p-5">
          <div class="flex items-center justify-between mb-3">
            <h2 class="text-base font-medium text-default">运行记录</h2>
            <BaseButton intent="ghost" size="sm" @click="loadRuns">刷新</BaseButton>
          </div>
          <div v-if="runsLoading" class="text-center py-8 text-muted text-sm">加载中...</div>
          <template v-else>
            <BaseTable :columns="runColumns" :data="runs" row-key="id" :on-row-click="toggleRunDetail">
              <template #status="{ row }">
                <BaseBadge :intent="runStatusIntent(row.status)">{{ runStatusLabel(row.status) }}</BaseBadge>
              </template>
              <template #trigger_type="{ row }">{{ triggerLabel(row.trigger_type) }}</template>
              <template #counts="{ row }">
                <span class="text-xs text-muted">
                  新增 {{ row.new_count ?? 0 }} · 更新 {{ row.updated_count ?? 0 }} · 跳过 {{ row.skipped_count ?? 0 }}
                  · 下架 {{ row.deleted_count ?? 0 }} · 恢复 {{ row.restored_count ?? 0 }} · 失败 {{ row.failed_count ?? 0 }}
                </span>
              </template>
              <template #vl="{ row }">
                <span class="text-xs text-muted">
                  VL {{ row.vl_billed_count ?? 0 }} 张 · 嵌入 {{ row.embedding_tokens ?? 0 }} tokens
                  · {{ row.credits_charged ?? 0 }} 积分
                </span>
              </template>
              <template #created_at="{ row }">{{ formatTime(row.created_at) }}</template>
              <template #expand="{ row }">
                <span class="text-primary-600 text-xs">{{ expandedRunId === row.id ? '收起明细 ▲' : '展开明细 ▼' }}</span>
              </template>
            </BaseTable>
            <p v-if="runs.length === 0" class="text-center text-sm text-muted py-6">暂无运行记录</p>

            <div v-if="expandedRunId" class="mt-3 rounded-lg border border-default bg-canvas p-3">
              <div v-if="itemsLoading" class="text-center py-4 text-muted text-sm">加载明细中...</div>
              <template v-else>
                <p v-if="runItems.length === 0" class="text-center text-xs text-muted py-4">无明细</p>
                <div v-else class="max-h-72 overflow-y-auto">
                  <table class="w-full text-xs">
                    <thead>
                      <tr class="text-left text-muted border-b border-default">
                        <th class="py-1.5 pr-3">产品ID</th>
                        <th class="py-1.5 pr-3">动作</th>
                        <th class="py-1.5 pr-3">状态</th>
                        <th class="py-1.5 pr-3">VL</th>
                        <th class="py-1.5 pr-3">嵌入 tokens</th>
                        <th class="py-1.5">错误</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="item in runItems" :key="item.native_id" class="border-b border-default/60">
                        <td class="py-1.5 pr-3 text-default">{{ item.native_id }}</td>
                        <td class="py-1.5 pr-3">{{ actionLabel(item.action) }}</td>
                        <td class="py-1.5 pr-3">
                          <BaseBadge size="sm" :intent="itemStatusIntent(item.status)">{{ item.status || '-' }}</BaseBadge>
                        </td>
                        <td class="py-1.5 pr-3 text-muted">{{ item.vl_billed ?? 0 }}</td>
                        <td class="py-1.5 pr-3 text-muted">{{ item.embedding_tokens ?? 0 }}</td>
                        <td class="py-1.5 text-danger-700">{{ item.error_code || item.error_message || '-' }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </template>
            </div>
          </template>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, onMounted, ref } from 'vue'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable, { type TableColumn } from '@/components/ui/BaseTable.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import {
  getSource,
  patchSource,
  triggerSource,
  getRuns,
  getRun,
  getProducts,
  type HongtaoRun,
  type HongtaoRunItem,
  type HongtaoProduct,
} from '@/api/hongtaoShop'
import { useTenantAuth } from '@/composables/useTenantAuth'

const toast = useToast()

const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()
const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)
const effectiveUser = computed(() =>
  tenantAdmin.value
    ? {
        user_id: tenantAdmin.value.user_id,
        username: tenantAdmin.value.username,
        phone: tenantAdmin.value.phone,
      }
    : null,
)
const toggleSidebarFn = inject<() => void>('toggleSidebar')
function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}
async function handleLogout() {
  if (tenantLogout) await tenantLogout()
}

// ==================== 源设置 ====================

const INTERVAL_OPTIONS = [
  { value: '6', label: '每 6 小时' },
  { value: '12', label: '每 12 小时' },
  { value: '24', label: '每天' },
  { value: '48', label: '每 2 天' },
  { value: '72', label: '每 3 天' },
  { value: '168', label: '每周' },
]

const sourceLoading = ref(false)
const sourceNotFound = ref(false)
const sourceError = ref('')
const sourceSavedTip = ref('')
const savingSource = ref(false)
const triggerLoading = ref(false)
const source = ref<Awaited<ReturnType<typeof getSource>>['source'] | null>(null)
const intervalChoice = ref('24')

async function loadSource() {
  sourceLoading.value = true
  sourceError.value = ''
  try {
    const resp = await getSource()
    source.value = resp.source
    intervalChoice.value = String(resp.source.sync_interval_hours)
    sourceNotFound.value = false
  } catch (e) {
    if ((e as Error & { status?: number }).status === 404) {
      sourceNotFound.value = true
    } else {
      sourceError.value = (e as Error).message
    }
  } finally {
    sourceLoading.value = false
  }
}

async function saveSourcePatch(patch: Parameters<typeof patchSource>[0], tip: string) {
  savingSource.value = true
  sourceError.value = ''
  sourceSavedTip.value = ''
  try {
    const resp = await patchSource(patch)
    source.value = resp.source
    intervalChoice.value = String(resp.source.sync_interval_hours)
    sourceSavedTip.value = tip
    toast.success(tip)
  } catch (e) {
    sourceError.value = (e as Error).message
    toast.error((e as Error).message)
  } finally {
    savingSource.value = false
  }
}

async function handleToggleEnabled() {
  if (!source.value) return
  await saveSourcePatch(
    { enabled: !source.value.enabled },
    source.value.enabled ? '已关闭定时同步' : '已开启定时同步',
  )
}

// 频率下拉变更即保存（保存失败时回显旧值）
async function onIntervalChange(value: string) {
  if (!source.value) return
  const previous = String(source.value.sync_interval_hours)
  const hours = parseInt(value, 10)
  if (!hours || hours === source.value.sync_interval_hours) return
  intervalChoice.value = value
  await saveSourcePatch({ sync_interval_hours: hours }, `同步频率已改为每 ${hours} 小时`)
  if (sourceError.value) intervalChoice.value = previous
}

async function handleTrigger() {
  triggerLoading.value = true
  try {
    const resp = await triggerSource()
    if (resp.run.deduped) {
      toast.info('已有同步任务在队列中，无需重复触发')
    } else {
      toast.success('已加入同步队列，可在运行记录中查看进度')
    }
    loadRuns()
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    triggerLoading.value = false
  }
}

// ==================== 产品挑选 ====================

const productsLoading = ref(false)
const products = ref<HongtaoProduct[]>([])
const productTotal = ref(0)
const productPage = ref(1)
const productPageSize = 20
const productKeyword = ref('')
const savingSelection = ref(false)
const draftSelected = ref<Set<string>>(new Set())
const selectionDirty = ref(false)

const selectedCount = computed(() => draftSelected.value.size)
const pageSelectedCount = computed(
  () => products.value.filter((p) => draftSelected.value.has(p.native_id)).length,
)

const productColumns = computed<TableColumn[]>(() => [
  { key: 'select', label: source.value?.selection_mode === 'ids' ? '选择' : '状态' },
  { key: 'name', label: '产品名称' },
  { key: 'model', label: '型号' },
  { key: 'procode', label: '编码' },
  { key: 'status', label: '上架状态' },
  { key: 'has_doc', label: '知识库' },
])

async function loadProducts() {
  productsLoading.value = true
  try {
    const resp = await getProducts({
      keyword: productKeyword.value,
      page: productPage.value,
      page_size: productPageSize,
    })
    products.value = resp.products
    productTotal.value = resp.total
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    productsLoading.value = false
  }
}

function searchProducts() {
  productPage.value = 1
  loadProducts()
}

function onProductPageChange(page: number) {
  productPage.value = page
  loadProducts()
}

function toggleProduct(nativeId: string) {
  const next = new Set(draftSelected.value)
  if (next.has(nativeId)) {
    next.delete(nativeId)
  } else {
    next.add(nativeId)
  }
  draftSelected.value = next
  selectionDirty.value = true
}

async function handleSelectionMode(mode: 'all' | 'ids') {
  if (!source.value || source.value.selection_mode === mode) return
  if (mode === 'all') {
    if (!confirm('切换为全部同步后，所有上架产品将入库（含此前取消勾选的）。确认切换？')) return
    await saveSourcePatch({ selection_mode: 'all' }, '已切换为全部同步')
    selectionDirty.value = false
  } else {
    // 危险操作对称确认：空白名单意味着下一轮同步会把已入库产品全部下架
    if (!confirm('切换为白名单模式将从空白名单开始：保存前不会影响知识库，'
      + '但若未勾选任何产品就发生同步，已入库产品将被全部下架。确认切换？')) return
    await saveSourcePatch({ selection_mode: 'ids', selected_ids: [] }, '已切换为白名单模式，请勾选产品后保存')
  }
  loadProducts()
}

async function handleSaveSelection() {
  savingSelection.value = true
  try {
    const resp = await patchSource({
      selection_mode: 'ids',
      selected_ids: [...draftSelected.value],
    })
    source.value = resp.source
    selectionDirty.value = false
    toast.success(`已保存挑选（${draftSelected.value.size} 个产品），下一轮同步生效`)
    loadProducts()
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    savingSelection.value = false
  }
}

// ==================== 运行记录 ====================

const runsLoading = ref(false)
const runs = ref<HongtaoRun[]>([])
const expandedRunId = ref<number | null>(null)
const itemsLoading = ref(false)
const runItems = ref<HongtaoRunItem[]>([])

const runColumns: TableColumn[] = [
  { key: 'id', label: 'ID' },
  { key: 'status', label: '状态' },
  { key: 'trigger_type', label: '触发' },
  { key: 'counts', label: '产品计数' },
  { key: 'vl', label: '用量 / 计费' },
  { key: 'created_at', label: '时间' },
  { key: 'expand', label: '' },
]

async function loadRuns() {
  runsLoading.value = true
  try {
    const resp = await getRuns(20)
    runs.value = resp.runs
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    runsLoading.value = false
  }
}

function toggleRunDetail(row: Record<string, any>) {
  const runId = row.id as number
  if (expandedRunId.value === runId) {
    expandedRunId.value = null
    return
  }
  expandedRunId.value = runId
  itemsLoading.value = true
  getRun(runId)
    .then((resp) => {
      runItems.value = resp.items
    })
    .catch((e: Error) => toast.error(e.message))
    .finally(() => {
      itemsLoading.value = false
    })
}

// ==================== 展示辅助 ====================

function runStatusLabel(status: string): string {
  const map: Record<string, string> = {
    queued: '排队中',
    running: '进行中',
    success: '成功',
    partial_failed: '部分失败',
    failed: '失败',
    skipped_no_credit: '余额不足',
    interrupted: '已中断',
  }
  return map[status] || status
}

function runStatusIntent(status: string): 'success' | 'danger' | 'warning' | 'info' | 'neutral' {
  if (status === 'success') return 'success'
  if (status === 'running' || status === 'queued') return 'info'
  if (status === 'partial_failed' || status === 'interrupted') return 'warning'
  return 'danger'
}

function itemStatusIntent(status: string | null): 'success' | 'danger' | 'warning' | 'info' | 'neutral' {
  if (status === 'success' || status === 'skipped') return 'success'
  if (status === 'running' || status === 'pending') return 'info'
  if (status === 'interrupted') return 'warning'
  return 'danger'
}

function triggerLabel(trigger: string): string {
  const map: Record<string, string> = {
    manual: '手动',
    scheduled: '定时',
    retry: '重试',
  }
  return map[trigger] || trigger
}

function actionLabel(action: string | null): string {
  const map: Record<string, string> = {
    new: '新增',
    update: '更新',
    skip: '跳过',
    delete: '下架',
    restore: '恢复',
  }
  return action ? map[action] || action : '-'
}

function formatTime(value: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

// ==================== 初始化 ====================

onMounted(async () => {
  await loadSource()
  if (!sourceNotFound.value) {
    // 白名单模式下初始化草稿集（含未加载页的全部选中项）
    if (source.value?.selection_mode === 'ids') {
      draftSelected.value = new Set(source.value.selected_ids)
    }
    await Promise.all([loadProducts(), loadRuns()])
  }
})
</script>
