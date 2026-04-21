<template>
  <div class="p-6">
    <h1 class="text-2xl font-bold text-slate-800 mb-6">计费管理</h1>

    <div v-if="loading" class="text-center py-12 text-slate-500">加载中...</div>

    <template v-else>
      <!-- 套餐选择 -->
      <div class="mb-8">
        <h2 class="text-lg font-medium text-slate-700 mb-4">可选套餐</h2>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div v-for="plan in plans" :key="plan.name"
            class="bg-white rounded-xl shadow-sm border-2 p-5 transition-colors"
            :class="selectedPlan === plan.name ? 'border-cyan-500' : 'border-slate-200 hover:border-cyan-300'">
            <h3 class="text-lg font-bold text-slate-800">{{ plan.display_name }}</h3>
            <div class="mt-2">
              <span class="text-3xl font-bold text-cyan-600">¥{{ plan.price }}</span>
              <span class="text-sm text-slate-500">/月</span>
            </div>
            <ul class="mt-4 space-y-2 text-sm text-slate-600">
              <li>Token 配额：{{ formatNum(plan.token_quota) }}</li>
              <li>最大实例：{{ plan.max_instances === -1 ? '不限' : plan.max_instances }}</li>
              <li>最大用户：{{ plan.max_users === -1 ? '不限' : plan.max_users }}</li>
            </ul>
            <button
              @click="selectedPlan = plan.name"
              class="w-full mt-4 py-2 rounded-lg text-sm font-medium transition-colors"
              :class="selectedPlan === plan.name ? 'bg-cyan-500 text-white' : 'border border-cyan-500 text-cyan-600 hover:bg-cyan-50'"
            >
              {{ selectedPlan === plan.name ? '已选择' : '选择套餐' }}
            </button>
          </div>
        </div>
        <div v-if="selectedPlan" class="mt-4 flex gap-3 items-center">
          <select v-model="billingCycle"
            class="px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 text-sm">
            <option value="monthly">月付</option>
            <option value="yearly">年付</option>
          </select>
          <button @click="handleSubscribe" :disabled="subscribing"
            class="px-6 py-2 bg-cyan-500 hover:bg-cyan-600 disabled:bg-slate-300 text-white rounded-lg text-sm font-medium transition-colors">
            {{ subscribing ? '处理中...' : '订阅' }}
          </button>
        </div>
      </div>

      <!-- 当前订阅 + Token 用量 -->
      <div class="mb-8">
        <h2 class="text-lg font-medium text-slate-700 mb-4">当前订阅</h2>
        <div v-if="billingUsage.length > 0" class="space-y-3">
          <div v-for="u in billingUsage" :key="u.subscription_id" class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
            <div class="flex items-center justify-between mb-2">
              <span class="font-medium text-slate-800">{{ u.plan_name }}</span>
              <span class="text-sm text-slate-500">已用 {{ u.usage_percentage.toFixed(1) }}%</span>
            </div>
            <div class="w-full bg-slate-200 rounded-full h-3">
              <div
                class="h-3 rounded-full transition-all"
                :class="u.usage_percentage > 80 ? 'bg-red-500' : 'bg-cyan-500'"
                :style="{ width: Math.min(u.usage_percentage, 100) + '%' }"
              ></div>
            </div>
            <div class="flex justify-between text-xs text-slate-400 mt-1">
              <span>已用 {{ formatNum(u.tokens_used) }}</span>
              <span>配额 {{ formatNum(u.token_quota) }}</span>
            </div>
          </div>
        </div>
        <div v-else class="text-slate-400 text-sm">暂无订阅</div>
      </div>

      <!-- 订单列表 -->
      <div>
        <h2 class="text-lg font-medium text-slate-700 mb-4">支付订单</h2>
        <div v-if="subscriptions.length > 0" class="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
          <table class="w-full">
            <thead class="bg-slate-50 border-b border-slate-200">
              <tr>
                <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">套餐</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">周期</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">状态</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">支付状态</th>
                <th class="px-4 py-2 text-left text-xs font-medium text-slate-500">创建时间</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-100">
              <tr v-for="sub in subscriptions" :key="sub.subscription_id" class="hover:bg-slate-50">
                <td class="px-4 py-2 text-sm text-slate-800">{{ sub.plan_name }}</td>
                <td class="px-4 py-2 text-sm text-slate-600">{{ sub.billing_cycle === 'monthly' ? '月付' : '年付' }}</td>
                <td class="px-4 py-2">
                  <span class="text-xs px-2 py-0.5 rounded-full"
                    :class="sub.status === 'active' ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-600'">
                    {{ sub.status === 'active' ? '活跃' : sub.status }}
                  </span>
                </td>
                <td class="px-4 py-2">
                  <span class="text-xs px-2 py-0.5 rounded-full"
                    :class="sub.payment_status === 'paid' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'">
                    {{ sub.payment_status === 'paid' ? '已支付' : sub.payment_status }}
                  </span>
                </td>
                <td class="px-4 py-2 text-sm text-slate-500">{{ sub.created_at }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="text-slate-400 text-sm">暂无订单</div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useToast } from 'vue-toastification'
import { getPlans, listSubscriptions, getUsage, createSubscription, payOrder } from '@/api/saasTenant'

const toast = useToast()

const loading = ref(true)
const plans = ref<any[]>([])
const subscriptions = ref<any[]>([])
const billingUsage = ref<any[]>([])
const selectedPlan = ref('')
const billingCycle = ref('monthly')
const subscribing = ref(false)

function formatNum(n: number): string {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K'
  return String(n)
}

async function loadData() {
  loading.value = true
  try {
    const [plansRes, subsRes, usageRes] = await Promise.allSettled([
      getPlans(),
      listSubscriptions(),
      getUsage()
    ])
    if (plansRes.status === 'fulfilled') plans.value = plansRes.value.plans || []
    if (subsRes.status === 'fulfilled') subscriptions.value = subsRes.value.subscriptions || []
    if (usageRes.status === 'fulfilled') billingUsage.value = usageRes.value.usage || []
  } catch (e) {
    console.error('加载计费信息失败:', e)
  } finally {
    loading.value = false
  }
}

async function handleSubscribe() {
  if (!selectedPlan.value) return
  subscribing.value = true
  try {
    const res = await createSubscription({
      plan: selectedPlan.value,
      billing_cycle: billingCycle.value
    })
    if (res.order?.order_id && res.order?.payment_status !== 'paid') {
      // 有待支付订单，发起支付
      const payRes = await payOrder(res.order.order_id)
      if (payRes.payment_url) {
        window.open(payRes.payment_url, '_blank')
      }
    }
    await loadData()
  } catch (e: any) {
    toast.error(e.message || '订阅失败')
  } finally {
    subscribing.value = false
  }
}

onMounted(() => loadData())
</script>
