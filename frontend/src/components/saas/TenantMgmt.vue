<template>
  <div class="p-6">
    <h1 class="text-2xl font-bold text-slate-800 mb-6">租户管理</h1>

    <div v-if="loading" class="text-center py-12 text-slate-500">加载中...</div>

    <div v-else class="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
      <table class="w-full">
        <thead class="bg-slate-50 border-b border-slate-200">
          <tr>
            <th class="px-4 py-3 text-left text-sm font-medium text-slate-600">租户ID</th>
            <th class="px-4 py-3 text-left text-sm font-medium text-slate-600">企业名称</th>
            <th class="px-4 py-3 text-left text-sm font-medium text-slate-600">联系人</th>
            <th class="px-4 py-3 text-left text-sm font-medium text-slate-600">联系电话</th>
            <th class="px-4 py-3 text-left text-sm font-medium text-slate-600">套餐</th>
            <th class="px-4 py-3 text-left text-sm font-medium text-slate-600">状态</th>
            <th class="px-4 py-3 text-left text-sm font-medium text-slate-600">创建时间</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100">
          <tr v-for="tenant in tenants" :key="tenant.tenant_id" class="hover:bg-slate-50">
            <td class="px-4 py-3 text-sm text-slate-800 font-mono">{{ tenant.tenant_id }}</td>
            <td class="px-4 py-3 text-sm text-slate-800">{{ tenant.company_name }}</td>
            <td class="px-4 py-3 text-sm text-slate-600">{{ tenant.contact_name || '-' }}</td>
            <td class="px-4 py-3 text-sm text-slate-600">{{ tenant.contact_phone || '-' }}</td>
            <td class="px-4 py-3 text-sm text-slate-600">{{ tenant.plan }}</td>
            <td class="px-4 py-3">
              <span
                :class="tenant.status === 1 ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'"
                class="px-2 py-1 rounded-full text-xs font-medium"
              >
                {{ tenant.status === 1 ? '正常' : '停用' }}
              </span>
            </td>
            <td class="px-4 py-3 text-sm text-slate-500">{{ formatDate(tenant.created_at) }}</td>
          </tr>
        </tbody>
      </table>

      <div v-if="tenants.length === 0" class="text-center py-12 text-slate-500">
        暂无租户数据
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { listTenants } from '@/api/saasTenant'

const loading = ref(true)
const tenants = ref<any[]>([])

async function loadTenants() {
  loading.value = true
  try {
    const res = await listTenants()
    if (res.success) {
      tenants.value = res.tenants || []
    }
  } catch (e) {
    console.error('加载租户列表失败:', e)
  } finally {
    loading.value = false
  }
}

function formatDate(dateStr: string) {
  if (!dateStr) return '-'
  const date = new Date(dateStr)
  return date.toLocaleDateString('zh-CN')
}

onMounted(() => {
  loadTenants()
})
</script>