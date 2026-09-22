<template>
  <div class="flex flex-col gap-3">
    <!-- 工具条 -->
    <div class="flex items-center justify-between">
      <div class="text-xs text-muted">
        数据源授权记录决定租户连接中心可见的同步能力；停用后租户侧立即不可见（已入库知识保留）。
      </div>
      <BaseButton size="sm" intent="ghost" :disabled="loading" @click="load">刷新</BaseButton>
    </div>

    <div v-if="loading" class="text-center py-6 text-muted text-sm">加载中…</div>
    <template v-else>
      <!-- 商城产品同步（宏陶商城 API → 产品知识库） -->
      <div class="border border-default rounded-lg p-4">
        <div class="flex items-start justify-between gap-3 flex-wrap">
          <div class="min-w-0">
            <div class="flex items-center gap-2">
              <span class="text-sm font-medium text-default">商城产品同步</span>
              <BaseBadge v-if="state?.granted" intent="success" size="sm">已开通</BaseBadge>
              <BaseBadge v-else intent="neutral" size="sm">未开通</BaseBadge>
            </div>
            <p class="text-xs text-muted mt-1">
              宏陶商城产品 API 定时/手动同步至本租户知识库「产品」分类（含图片识别，按张计费）。
            </p>
          </div>
          <div class="flex gap-2">
            <BaseButton v-if="!state?.granted" size="sm" :disabled="operating" @click="handleGrant">
              {{ operating ? '开通中…' : '开通数据源' }}
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
            <span class="text-xs text-muted block">入库范围</span>
            {{ state.source.selection_mode === 'ids' ? '部分同步（勾选产品）' : '全部上架产品' }}
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
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useToast } from 'vue-toastification'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import { getAdminSource, grantSource, revokeSource, type AdminSourceState } from '@/api/hongtaoShop'

const props = defineProps<{ tenantId: string }>()

const toast = useToast()
const loading = ref(false)
const operating = ref(false)
const state = ref<AdminSourceState | null>(null)

async function load() {
  if (!props.tenantId) return
  loading.value = true
  try {
    state.value = await getAdminSource(props.tenantId)
  } catch (e) {
    toast.error((e as Error).message || '加载授权状态失败')
  } finally {
    loading.value = false
  }
}

async function handleGrant() {
  operating.value = true
  try {
    state.value = await grantSource(props.tenantId)
    toast.success('已开通，租户连接中心将出现「商城产品同步」入口')
  } catch (e) {
    toast.error((e as Error).message || '开通失败')
  } finally {
    operating.value = false
  }
}

async function handleRevoke() {
  if (!confirm('确定停用该租户的「商城产品同步」数据源？停用后：租户侧入口立即隐藏、定时同步停止、排队中的同步将被取消；已入库知识与历史记录保留。注意：重新开通后同步配置将重置为默认（全部产品、每 24 小时），租户此前勾选的产品范围与频率不保留。')) return
  operating.value = true
  try {
    await revokeSource(props.tenantId)
    await load()
    toast.success('已停用开通')
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
