<template>
  <div class="flex h-full">
    <!-- 左侧：数字员工列表 -->
    <aside class="w-64 flex-shrink-0 border-r border-default bg-surface overflow-y-auto">
      <div class="p-3 border-b border-default">
        <div class="text-sm font-medium text-default">数字员工</div>
        <div class="text-xs text-muted mt-0.5">环境变量将注入运行时供 http_api 工具 ${VAR_NAME} 引用</div>
      </div>
      <ul v-if="!loadingAgents" class="py-1">
        <li
          v-for="agent in availableAgents"
          :key="agent.agent_id"
          @click="selectAgent(agent)"
          :class="[
            'px-4 py-2.5 cursor-pointer text-sm transition-colors border-l-2',
            selectedAgentId === agent.agent_id
              ? 'bg-primary-50 text-primary-700 border-primary-500 font-medium'
              : 'text-gray-700 hover:bg-surface-hover border-transparent'
          ]"
        >
          {{ agent.name }}
        </li>
      </ul>
      <div v-else class="p-4 text-center text-xs text-muted">加载中...</div>
    </aside>

    <!-- 右侧：环境变量编辑 -->
    <section class="flex-1 overflow-y-auto p-6">
      <div v-if="!selectedAgentId" class="h-full flex items-center justify-center text-muted text-sm">
        请从左侧选择数字员工
      </div>
      <div v-else class="max-w-4xl">
        <div class="flex items-center justify-between mb-2">
          <h3 class="text-lg font-semibold text-default">环境变量</h3>
          <span class="text-xs text-muted">{{ selectedAgentName }}</span>
        </div>
        <p class="text-xs text-muted mb-4">
          这些变量将在 {{ selectedAgentName }} 运行时注入为环境变量，供 http_api 工具中的 ${VAR_NAME} 引用。
        </p>

        <div v-if="loadingEnvVars" class="text-center py-6 text-muted text-sm">加载中...</div>
        <div v-else>
          <div class="space-y-2">
            <div v-for="(item, idx) in envVarList" :key="idx" class="flex items-start gap-2">
              <input
                v-model="item.name"
                placeholder="变量名"
                class="flex-1 px-2 py-1.5 text-sm border border-default rounded focus:outline-none focus:border-primary-400 font-mono"
              />
              <input
                v-model="item.value"
                placeholder="变量值"
                class="flex-[2] px-2 py-1.5 text-sm border border-default rounded focus:outline-none focus:border-primary-400"
              />
              <input
                v-model="item.description"
                placeholder="说明"
                class="flex-1 px-2 py-1.5 text-sm border border-default rounded focus:outline-none focus:border-primary-400"
              />
              <button
                @click="envVarList.splice(idx, 1)"
                class="text-muted hover:text-danger-500 transition-colors px-1"
                title="删除"
              >
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <button
              @click="envVarList.push({ name: '', value: '', description: '' })"
              class="w-full py-1.5 text-sm text-primary-600 hover:text-primary-700 border border-dashed border-default rounded hover:border-primary-400 transition-colors"
            >+ 添加变量</button>
          </div>

          <div v-if="envVarError" class="mt-3 p-2 bg-danger-50 border border-danger-200 rounded text-danger-600 text-sm">{{ envVarError }}</div>

          <div class="flex gap-3 mt-6">
            <BaseButton intent="secondary" :disabled="savingEnvVars" @click="loadEnvVars">重置</BaseButton>
            <BaseButton :disabled="savingEnvVars" @click="handleSave">{{ savingEnvVars ? '保存中...' : '保存' }}</BaseButton>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useToast } from 'vue-toastification'
import BaseButton from '@/components/ui/BaseButton.vue'
import {
  getAllAvailableAgents,
  getSubagentEnvVars,
  setSubagentEnvVars,
  type AgentItem,
  type EnvVarItem,
} from '@/api/saasPermissions'

const route = useRoute()
const toast = useToast()

const tenantId = computed(() => route.params.tenant_id as string)

const loadingAgents = ref(false)
const availableAgents = ref<AgentItem[]>([])

const selectedAgentId = ref<string>('')
const selectedAgentName = ref<string>('')
const envVarList = ref<Array<{ name: string; value: string; description: string }>>([])
const loadingEnvVars = ref(false)
const savingEnvVars = ref(false)
const envVarError = ref('')

onMounted(async () => {
  loadingAgents.value = true
  try {
    const res = await getAllAvailableAgents()
    if (res.success && res.data) {
      availableAgents.value = res.data
      if (availableAgents.value.length > 0) {
        await selectAgent(availableAgents.value[0])
      }
    }
  } catch (e) {
    console.error('加载可用数字员工失败:', e)
  } finally {
    loadingAgents.value = false
  }
})

async function selectAgent(agent: AgentItem) {
  selectedAgentId.value = agent.agent_id
  selectedAgentName.value = agent.name
  envVarError.value = ''
  await loadEnvVars()
}

async function loadEnvVars() {
  if (!selectedAgentId.value) return
  loadingEnvVars.value = true
  envVarError.value = ''
  try {
    const res = await getSubagentEnvVars(tenantId.value, selectedAgentId.value)
    if (res.success && res.data) {
      envVarList.value = res.data.map((v: EnvVarItem) => ({
        name: v.var_name,
        value: v.var_value || '',
        description: v.description || '',
      }))
    }
    if (envVarList.value.length === 0) {
      envVarList.value = [{ name: '', value: '', description: '' }]
    }
  } catch (e) {
    console.error('加载环境变量失败:', e)
    envVarList.value = [{ name: '', value: '', description: '' }]
  } finally {
    loadingEnvVars.value = false
  }
}

async function handleSave() {
  const vars = envVarList.value.filter(v => v.name.trim())
  const names = vars.map(v => v.name.trim())
  if (new Set(names).size !== names.length) {
    envVarError.value = '变量名不能重复'
    return
  }
  savingEnvVars.value = true
  envVarError.value = ''
  try {
    const res = await setSubagentEnvVars(
      tenantId.value,
      selectedAgentId.value,
      vars.map(v => ({ name: v.name.trim(), value: v.value, description: v.description || undefined })),
    )
    if (res.success) {
      toast.success('环境变量保存成功')
    } else {
      envVarError.value = res.message || '保存失败'
    }
  } catch (e: any) {
    envVarError.value = e.message || '保存失败'
  } finally {
    savingEnvVars.value = false
  }
}
</script>
