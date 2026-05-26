<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-default">智能体管理</h1>
      <button
        @click="showCreate = true"
        class="px-4 py-2 bg-primary-500 hover:bg-primary-700 text-white rounded-lg text-sm font-medium transition-colors"
      >
        创建实例
      </button>
    </div>

    <!-- 加载状态 -->
    <div v-if="loading" class="text-center py-12 text-muted">加载中...</div>

    <!-- 实例列表 -->
    <div v-else-if="instances.length > 0" class="bg-white rounded-xl shadow-sm border border-default overflow-hidden">
      <table class="w-full">
        <thead class="bg-canvas border-b border-default">
          <tr>
            <th class="px-4 py-3 text-left text-xs font-medium text-muted uppercase">名称</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-muted uppercase">类型</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-muted uppercase">状态</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-muted uppercase">创建时间</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-muted uppercase">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-default">
          <tr v-for="inst in instances" :key="inst.instance_id" class="hover:bg-surface-hover">
            <td class="px-4 py-3 text-sm text-default">{{ inst.display_name }}</td>
            <td class="px-4 py-3 text-sm text-default">{{ inst.subagent_type }}</td>
            <td class="px-4 py-3">
              <span
                class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium"
                :class="inst.status === 'running' ? 'bg-success-100 text-success-700' : 'bg-surface-hover text-default'"
              >
                {{ inst.status === 'running' ? '运行中' : '已停止' }}
              </span>
            </td>
            <td class="px-4 py-3 text-sm text-muted">{{ inst.created_at }}</td>
            <td class="px-4 py-3">
              <div class="flex items-center gap-2">
                <button
                  v-if="inst.status !== 'running'"
                  @click="handleStart(inst.instance_id)"
                  class="text-xs px-2 py-1 bg-success-100 text-success-700 rounded hover:bg-success-200 transition-colors"
                >启动</button>
                <button
                  v-if="inst.status === 'running'"
                  @click="handleStop(inst.instance_id)"
                  class="text-xs px-2 py-1 bg-yellow-100 text-yellow-700 rounded hover:bg-yellow-200 transition-colors"
                >停止</button>
                <button
                  @click="handleDelete(inst.instance_id)"
                  class="text-xs px-2 py-1 bg-danger-100 text-danger-700 rounded hover:bg-danger-200 transition-colors"
                >删除</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 空状态 -->
    <div v-else class="text-center py-12 text-muted">
      <p class="text-lg mb-2">暂无智能体实例</p>
      <p class="text-sm">点击"创建实例"按钮添加第一个智能体</p>
    </div>

    <!-- 创建弹窗 -->
    <div v-if="showCreate" class="fixed inset-0 z-50 flex items-center justify-center">
      <div class="absolute inset-0 bg-black/50" @click="showCreate = false"></div>
      <div class="relative bg-white rounded-xl shadow-2xl w-full max-w-md mx-4 p-6">
        <h3 class="text-lg font-bold text-default mb-4">创建智能体实例</h3>
        <div class="space-y-4">
          <div>
            <label class="block text-sm text-default mb-1">显示名称</label>
            <input v-model="form.display_name" type="text" placeholder="例：客服助手"
              class="w-full px-3 py-2 bg-surface-hover border border-hover rounded-lg text-default focus:outline-none focus:border-primary-400" />
          </div>
          <div>
            <label class="block text-sm text-default mb-1">智能体类型</label>
            <select v-model="form.subagent_type"
              class="w-full px-3 py-2 bg-surface-hover border border-hover rounded-lg text-default focus:outline-none focus:border-primary-400">
              <option value="customer_service">客服助手</option>
              <option value="hr_assistant">HR 助手</option>
              <option value="finance_assistant">财务助手</option>
              <option value="general">通用助手</option>
            </select>
          </div>
          <div>
            <label class="block text-sm text-default mb-1">套餐</label>
            <select v-model="form.plan"
              class="w-full px-3 py-2 bg-surface-hover border border-hover rounded-lg text-default focus:outline-none focus:border-primary-400">
              <option value="basic">基础版</option>
              <option value="standard">标准版</option>
              <option value="premium">高级版</option>
            </select>
          </div>
          <div>
            <label class="block text-sm text-default mb-1">计费周期</label>
            <select v-model="form.billing_cycle"
              class="w-full px-3 py-2 bg-surface-hover border border-hover rounded-lg text-default focus:outline-none focus:border-primary-400">
              <option value="monthly">月付</option>
              <option value="yearly">年付</option>
            </select>
          </div>
        </div>
        <div v-if="createError" class="mt-3 p-2 bg-danger-50 border border-danger-200 rounded text-danger-600 text-sm">{{ createError }}</div>
        <div class="flex gap-3 mt-6">
          <button @click="showCreate = false" class="flex-1 py-2 border border-hover rounded-lg text-default hover:bg-surface-hover transition-colors">取消</button>
          <button @click="handleCreate" :disabled="creating" class="flex-1 py-2 bg-primary-500 hover:bg-primary-700 disabled:bg-gray-300 text-white rounded-lg transition-colors">
            {{ creating ? '创建中...' : '确认创建' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useToast } from 'vue-toastification'
import { listInstances, createInstance, startInstance, stopInstance, deleteInstance } from '@/api/saasTenant'

const toast = useToast()

const loading = ref(true)
const instances = ref<any[]>([])
const showCreate = ref(false)
const creating = ref(false)
const createError = ref('')

const form = ref({
  display_name: '',
  subagent_type: 'customer_service',
  plan: 'basic',
  billing_cycle: 'monthly'
})

async function loadInstances() {
  loading.value = true
  try {
    const res = await listInstances()
    instances.value = res.instances || []
  } catch (e) {
    console.error('加载实例列表失败:', e)
  } finally {
    loading.value = false
  }
}

async function handleCreate() {
  if (!form.value.display_name) {
    createError.value = '请输入显示名称'
    return
  }
  creating.value = true
  createError.value = ''
  try {
    await createInstance(form.value)
    showCreate.value = false
    form.value = { display_name: '', subagent_type: 'customer_service', plan: 'basic', billing_cycle: 'monthly' }
    await loadInstances()
  } catch (e: any) {
    createError.value = e.message || '创建失败'
  } finally {
    creating.value = false
  }
}

async function handleStart(id: string) {
  try {
    await startInstance(id)
    await loadInstances()
  } catch (e: any) {
    toast.error(e.message || '启动失败')
  }
}

async function handleStop(id: string) {
  try {
    await stopInstance(id)
    await loadInstances()
  } catch (e: any) {
    toast.error(e.message || '停止失败')
  }
}

async function handleDelete(id: string) {
  if (!confirm('确定要删除此实例吗？')) return
  try {
    await deleteInstance(id)
    await loadInstances()
  } catch (e: any) {
    toast.error(e.message || '删除失败')
  }
}

onMounted(() => loadInstances())
</script>
