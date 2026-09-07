<template>
  <BaseModal
    v-model="visible"
    :title="`积分用量明细 - ${date}`"
    size="xl"
    mode="view"
    :scrollable="false"
  >
    <div v-if="loading" class="text-center py-8 text-muted">加载中...</div>
    <template v-else>
      <div class="flex flex-col h-[calc(90vh-6rem)]">
        <div class="flex-1 min-h-0 mb-4 table-scroll-wrapper">
          <BaseTable :columns="columns" :data="data" row-key="record_id">
            <template #index="{ index }">
              {{ (currentPage - 1) * pageSize + index + 1 }}
            </template>
            <template #usage_type="{ row }">
              <span v-if="row.usage_type === 'client'"
                    class="text-xs px-1.5 py-0.5 rounded bg-primary-50 text-primary-600 border border-primary-200">客户端</span>
              <span v-else class="text-xs text-muted">对话</span>
            </template>
            <template #session_title="{ row }">
              <!-- client 行会话列改显来源（BOSS 工具/协会采集等 stage 标签） -->
              <span v-if="row.usage_type === 'client'" class="text-xs text-default" :title="row.source_type">{{ row.source_type }}</span>
              <span v-else :title="row.session_title">{{ row.session_title }}</span>
            </template>
            <template #channel_label="{ row }">
              <!-- client 行来源已在会话列展示，渠道列避免重复 -->
              <span v-if="row.usage_type === 'client'">-</span>
              <span v-else :title="row.channel_label || ''">{{ row.channel_label || '-' }}</span>
            </template>
            <template #user_message="{ row }">
              <!-- client 行消息列改显命令名 -->
              <span v-if="row.usage_type === 'client'" class="text-default font-medium" :title="row.command">{{ row.command || '-' }}</span>
              <span v-else :title="row.user_message">{{ truncateText(row.user_message) }}</span>
            </template>
            <template #assistant_message="{ row }">
              <!-- client 行回复列改显参数摘要（JSON，悬停看全量） -->
              <span v-if="row.usage_type === 'client'" class="text-xs text-muted" :title="row.arguments">{{ truncateText(row.arguments, 40) }}</span>
              <span v-else :title="row.assistant_message">{{ truncateText(row.assistant_message) }}</span>
            </template>
            <template #model="{ row }">
              <span :title="row.model">{{ row.model || '-' }}</span>
            </template>
            <template #bd_non_cached_input="{ row }">{{ formatBreakdown(row, 'non_cached_input') }}</template>
            <template #bd_cached_input="{ row }">{{ formatBreakdown(row, 'cached_input') }}</template>
            <template #bd_cache_creation_input="{ row }">{{ formatBreakdown(row, 'cache_creation_input') }}</template>
            <template #bd_output="{ row }">{{ formatBreakdown(row, 'output') }}</template>
            <template #bd_video="{ row }">{{ formatBreakdown(row, 'video') }}</template>
            <template #bd_asr="{ row }">{{ formatBreakdown(row, 'asr') }}</template>
            <template #bd_embedding="{ row }">{{ formatBreakdown(row, 'embedding') }}</template>
            <template #credit_cost="{ row }">
              <span class="text-danger-600 font-medium">{{ formatCredit(row.credit_cost) }}</span>
            </template>
            <template #empty>该日暂无明细数据</template>
          </BaseTable>
        </div>
        <div class="flex-shrink-0 flex items-center justify-center">
          <BasePagination
            :total="total"
            v-model:currentPage="currentPage"
            v-model:pageSize="pageSize"
            :size-options="[20, 50, 100]"
            @change="loadData"
          />
        </div>
      </div>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useToast } from 'vue-toastification'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import { formatCredit } from '@/utils/formatCredit'
import {
  getDailyUsageDetail,
  type DailyUsageDetailItem,
  type BreakdownItem
} from '@/api/billing'

const props = defineProps<{
  modelValue: boolean
  /** 目标租户 ID（平台管理员代管理，注入 X-Tenant-Id） */
  tenantId: string
  /** 查询日期，格式 YYYY-MM-DD */
  date: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()

const toast = useToast()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v)
})

const loading = ref(false)
const data = ref<DailyUsageDetailItem[]>([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)

// usage_breakdown 7 分项列（敏感对账数据，仅管理后台展示）：dataKey 对应 breakdown_items 的分项 key
const bdDetailCols = [
  { key: 'bd_non_cached_input', dataKey: 'non_cached_input', label: '未命中缓存输入', width: '220px' },
  { key: 'bd_cached_input', dataKey: 'cached_input', label: '命中缓存输入', width: '220px' },
  { key: 'bd_cache_creation_input', dataKey: 'cache_creation_input', label: '缓存创建输入', width: '220px' },
  { key: 'bd_output', dataKey: 'output', label: '输出', width: '190px' },
  { key: 'bd_video', dataKey: 'video', label: '视频模型', width: '180px' },
  { key: 'bd_asr', dataKey: 'asr', label: 'ASR', width: '170px' },
  { key: 'bd_embedding', dataKey: 'embedding', label: '向量模型', width: '220px' },
]

// 明细列定义：类型列区分智能体对话与客户端调用（P3 双表口径）；usage_breakdown 7 分项
// （未命中缓存输入/命中缓存输入/缓存创建输入/输出/视频模型/ASR/向量模型）仅管理后台可见
const columns = computed(() => {
  const cols: Array<{ key: string; label: string; width: string; tooltip?: (row: Record<string, any>) => string | undefined }> = [
    { key: 'index', label: '序号', width: '60px' },
    { key: 'usage_type', label: '类型', width: '80px' },
    { key: 'created_at', label: '创建时间', width: '160px' },
    { key: 'session_title', label: '会话/来源', width: '180px' },
    { key: 'user_message', label: '消息/命令', width: '220px' },
    { key: 'assistant_message', label: '回复/参数', width: '220px' },
    { key: 'user_display', label: '用户', width: '200px' },
    { key: 'channel_label', label: '渠道会话', width: '150px' },
    { key: 'source_type', label: '来源', width: '120px' },
    { key: 'model', label: '文本模型', width: '150px' }
  ]
  bdDetailCols.forEach((c) => cols.push({
    key: c.key,
    label: c.label,
    width: c.width,
    // 公式较长可能在列宽内截断，悬停显示完整对账公式
    tooltip: (row: Record<string, any>) => formatBreakdown(row, c.dataKey),
  }))
  cols.push({ key: 'credit_cost', label: '消耗积分', width: '100px' })
  return cols
})

function truncateText(text: string | null | undefined, maxLen: number = 30): string {
  if (!text) return '-'
  if (text.length <= maxLen) return text
  return text.slice(0, maxLen) + '...'
}

// 数值格式化：整数/小数均加千分位，小数保留 6 位去尾零
function fmtNum(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === '') return '-'
  const num = typeof v === 'string' ? parseFloat(v) : Number(v)
  if (isNaN(num)) return '-'
  const trimmed = num.toFixed(6).replace(/\.?0+$/, '')
  const [intPart, decPart] = trimmed.split('.')
  const intFormatted = Number(intPart).toLocaleString('en-US')
  return decPart !== undefined ? `${intFormatted}.${decPart}` : intFormatted
}

// 渲染 usage_breakdown 分项：
// - 新数据（含单价/系数/分项积分）：{数量} * {单价} * {系数} / 1M = {积分}（每百万类）/ {数量} * {单价} * {系数} = {积分}
// - 老数据（8-14 前无单价/系数/分项积分）：降级只显示数量（token数/秒数/次数）
function formatBreakdown(row: Record<string, any>, key: string): string {
  const items: BreakdownItem[] = row.breakdown_items || []
  const item = items.find(i => i.key === key)
  if (!item) return '-'
  const { qty, unit_price, usage_factor, credit } = item
  if (qty === null || qty === undefined) return '-'
  // 老数据：仅数量可追溯，单价/系数/分项积分缺失时只显示数量
  if (unit_price === null || unit_price === undefined || usage_factor === null || usage_factor === undefined || credit === null || credit === undefined) {
    return fmtNum(qty)
  }
  const base = `${fmtNum(qty)} * ${fmtNum(unit_price)} * ${fmtNum(usage_factor)}`
  return item.is_per_million ? `${base} / 1M = ${fmtNum(credit)}` : `${base} = ${fmtNum(credit)}`
}

async function loadData() {
  if (!props.tenantId || !props.date) return
  loading.value = true
  try {
    const res = await getDailyUsageDetail({
      date: props.date,
      page: currentPage.value,
      page_size: pageSize.value,
    }, props.tenantId)
    if (res.success) {
      data.value = res.items || []
      total.value = res.total || 0
    } else {
      toast.error(res.message || '加载积分用量明细失败')
      data.value = []
      total.value = 0
    }
  } catch (error: any) {
    console.error('加载积分用量明细失败:', error)
    toast.error(error.message || '加载积分用量明细失败')
    data.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

// 打开弹框时从第一页加载
watch(() => props.modelValue, (open) => {
  if (open) {
    currentPage.value = 1
    loadData()
  }
})
</script>
