<template>
  <div class="p-6">
    <h1 class="text-2xl font-bold text-slate-800 mb-6">仪表盘</h1>

    <!-- 加载状态 -->
    <div v-if="loading" class="text-center py-12 text-slate-500">加载中...</div>

    <!-- 统计卡片 -->
    <div v-else class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="bg-white rounded-xl shadow-sm p-5 border border-slate-200">
        <div class="text-sm text-slate-500 mb-1">智能体实例</div>
        <div class="text-3xl font-bold text-slate-800">{{ stats.total_instances }}</div>
        <div class="text-xs text-slate-400 mt-1">{{ stats.active_instances }} 个运行中</div>
      </div>
      <div class="bg-white rounded-xl shadow-sm p-5 border border-slate-200">
        <div class="text-sm text-slate-500 mb-1">企业用户</div>
        <div class="text-3xl font-bold text-slate-800">{{ stats.total_users }}</div>
        <div class="text-xs text-slate-400 mt-1">已注册用户</div>
      </div>
      <div class="bg-white rounded-xl shadow-sm p-5 border border-slate-200">
        <div class="text-sm text-slate-500 mb-1">Token 用量</div>
        <div class="text-3xl font-bold text-slate-800">{{ formatTokens(stats.total_tokens_used) }}</div>
        <div class="mt-2" v-if="billingUsage.length > 0">
          <div class="w-full bg-slate-200 rounded-full h-2">
            <div
              class="h-2 rounded-full transition-all"
              :class="billingUsage[0].usage_percentage > 80 ? 'bg-red-500' : 'bg-cyan-500'"
              :style="{ width: Math.min(billingUsage[0].usage_percentage, 100) + '%' }"
            ></div>
          </div>
          <div class="text-xs text-slate-400 mt-1">{{ billingUsage[0].tokens_used }} / {{ billingUsage[0].token_quota }}</div>
        </div>
      </div>
      <div class="bg-white rounded-xl shadow-sm p-5 border border-slate-200">
        <div class="text-sm text-slate-500 mb-1">活跃订阅</div>
        <div class="text-3xl font-bold text-slate-800">{{ activeSubscriptions }}</div>
        <div class="text-xs text-slate-400 mt-1">当前套餐数</div>
      </div>
    </div>

    <!-- 错误信息 -->
    <div v-if="error" class="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-600 text-sm">
      {{ error }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { getTenantStats, getUsage, listSubscriptions } from '@/api/saasTenant'

const loading = ref(true)
const error = ref('')

const stats = ref({
  total_instances: 0,
  active_instances: 0,
  total_users: 0,
  total_tokens_used: 0
})

const billingUsage = ref<any[]>([])
const subscriptions = ref<any[]>([])

const activeSubscriptions = computed(() =>
  subscriptions.value.filter(s => s.status === 'active').length
)

function formatTokens(n: number): string {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K'
  return String(n)
}

async function loadData() {
  loading.value = true
  error.value = ''
  try {
    const [statsRes, usageRes, subsRes] = await Promise.allSettled([
      getTenantStats(),
      getUsage(),
      listSubscriptions()
    ])
    if (statsRes.status === 'fulfilled' && statsRes.value.stats) {
      stats.value = statsRes.value.stats
    }
    if (usageRes.status === 'fulfilled' && usageRes.value.usage) {
      billingUsage.value = usageRes.value.usage
    }
    if (subsRes.status === 'fulfilled' && subsRes.value.subscriptions) {
      subscriptions.value = subsRes.value.subscriptions
    }
  } catch (e: any) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => loadData())
</script>
