<template>
  <div class="flex h-full">
    <!-- 左侧：数字员工列表 -->
    <aside class="w-64 flex-shrink-0 border-r border-default bg-surface overflow-y-auto">
      <div class="p-3 border-b border-default">
        <div class="text-sm font-medium text-default">数字员工</div>
        <div class="text-xs text-muted mt-0.5">仅显示支持 API 配置的数字员工</div>
      </div>
      <ul v-if="!loadingAgents" class="py-1">
        <li
          v-for="agent in supportedAgents"
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

    <!-- 右侧：配置文件管理 -->
    <section class="flex-1 overflow-y-auto p-6">
      <div v-if="!selectedAgentId" class="h-full flex items-center justify-center text-muted text-sm">
        请从左侧选择数字员工
      </div>
      <div v-else class="max-w-2xl">
        <div class="flex items-center justify-between mb-2">
          <h3 class="text-lg font-semibold text-default">API 配置文件</h3>
          <span class="text-xs text-muted">{{ selectedAgentName }}</span>
        </div>
        <p class="text-xs text-muted mb-4">
          上传外部系统 API 配置文件（Markdown 格式）。该文件将供 {{ selectedAgentName }} 运行时读取，了解如何调用外部系统接口。
        </p>

        <!-- 已配置状态 -->
        <div v-if="configFileStatus?.configured" class="mb-4 p-3 bg-success-50 border border-success-200 rounded-lg">
          <div class="flex items-center justify-between">
            <div>
              <div class="text-sm text-success-700 font-medium">已配置</div>
              <div class="text-xs text-muted mt-0.5">
                {{ configFileStatus.filename }} · {{ ((configFileStatus.size ?? 0) / 1024).toFixed(1) }} KB
              </div>
            </div>
            <div class="flex gap-2">
              <button
                @click="handleDownload"
                class="text-xs px-3 py-1.5 bg-white border border-default rounded hover:border-primary-400 transition-colors"
              >下载</button>
              <button
                @click="handleDelete"
                class="text-xs px-3 py-1.5 bg-white border border-danger-300 text-danger-600 rounded hover:bg-danger-50 transition-colors"
              >删除</button>
            </div>
          </div>
        </div>
        <div v-else class="mb-4 p-3 bg-canvas border border-default rounded-lg">
          <div class="text-sm text-muted">未配置，请上传 API 配置文件</div>
        </div>

        <!-- 上传区域 -->
        <div
          class="border-2 border-dashed border-default rounded-lg p-4 text-center hover:border-primary-400 transition-colors cursor-pointer relative"
          @click="triggerFileInput"
          @dragover.prevent
          @drop.prevent="handleFileDrop"
        >
          <input ref="fileInputRef" type="file" accept=".md" class="hidden" @change="handleFileSelect" />
          <div class="text-sm text-muted">点击或拖拽上传 .md 文件</div>
          <div class="text-xs text-muted mt-1">{{ selectedAgentName ? `${selectedAgentName}.md` : '配置文件' }}</div>
        </div>

        <div v-if="uploading" class="mt-2 text-xs text-muted">上传中...</div>
        <div v-if="error" class="mt-3 p-2 bg-danger-50 border border-danger-200 rounded text-danger-600 text-sm">{{ error }}</div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useToast } from 'vue-toastification'
import {
  getAllAvailableAgents,
  getConfigFileStatus,
  uploadConfigFile,
  downloadConfigFile,
  deleteConfigFile,
  type AgentItem,
  type ConfigFileStatus,
} from '@/api/saasPermissions'

const route = useRoute()
const toast = useToast()

const tenantId = computed(() => route.params.tenant_id as string)

// 支持 API 配置文件的数字员工 agent_id 列表
// 后端无对应接口返回该标识，沿用 TenantMgmt.vue 的硬编码列表
const configSupportedAgents = ['after-sales', 'order-processing']

const loadingAgents = ref(false)
const availableAgents = ref<AgentItem[]>([])
const supportedAgents = computed(() =>
  availableAgents.value.filter(a => configSupportedAgents.includes(a.agent_id)),
)

const selectedAgentId = ref<string>('')
const selectedAgentName = ref<string>('')
const configFileStatus = ref<ConfigFileStatus | null>(null)
const uploading = ref(false)
const error = ref('')
const fileInputRef = ref<HTMLInputElement | null>(null)

onMounted(async () => {
  loadingAgents.value = true
  try {
    const res = await getAllAvailableAgents()
    if (res.success && res.data) {
      availableAgents.value = res.data
      if (supportedAgents.value.length > 0) {
        selectAgent(supportedAgents.value[0])
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
  error.value = ''
  configFileStatus.value = null
  try {
    configFileStatus.value = await getConfigFileStatus(tenantId.value, agent.agent_id)
  } catch (e) {
    console.error('获取配置文件状态失败:', e)
  }
}

function triggerFileInput() {
  fileInputRef.value?.click()
}

function handleFileSelect(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) doUpload(file)
  input.value = ''
}

function handleFileDrop(event: DragEvent) {
  const file = event.dataTransfer?.files?.[0]
  if (file) doUpload(file)
}

async function doUpload(file: File) {
  if (!file.name.endsWith('.md')) {
    error.value = '仅支持 .md 文件'
    return
  }
  uploading.value = true
  error.value = ''
  try {
    const res = await uploadConfigFile(tenantId.value, selectedAgentId.value, file)
    if (res.success) {
      toast.success('配置文件上传成功')
      configFileStatus.value = await getConfigFileStatus(tenantId.value, selectedAgentId.value)
    }
  } catch (e: any) {
    error.value = e.message || '上传失败'
  } finally {
    uploading.value = false
  }
}

async function handleDownload() {
  try {
    await downloadConfigFile(tenantId.value, selectedAgentId.value)
  } catch (e: any) {
    toast.error(e.message || '下载失败')
  }
}

async function handleDelete() {
  if (!confirm('确定删除配置文件？')) return
  try {
    const res = await deleteConfigFile(tenantId.value, selectedAgentId.value)
    if (res.success) {
      toast.success('配置文件已删除')
      configFileStatus.value = null
    }
  } catch (e: any) {
    toast.error(e.message || '删除失败')
  }
}
</script>
