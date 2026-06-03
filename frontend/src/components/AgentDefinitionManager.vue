<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <!-- Toast -->
    <div class="fixed top-[20%] left-1/2 -translate-x-1/2 z-50 space-y-2">
      <TransitionGroup name="toast">
        <div v-for="toast in toasts" :key="toast.id"
          :class="['px-4 py-2 rounded-lg shadow-lg text-sm flex items-center gap-2',
            toast.type === 'success' ? 'bg-success-500 text-white' : '',
            toast.type === 'error' ? 'bg-danger-500 text-white' : '',
            toast.type === 'info' ? 'bg-info-500 text-white' : ''
          ]">
          <span>{{ toast.message }}</span>
        </div>
      </TransitionGroup>
    </div>

    <main class="flex-1 flex overflow-hidden">
      <div class="flex-1 flex flex-col min-w-0">
        <AppHeader
          title="智能体管理"
          :is-online="true"
          :is-logged-in="effectiveIsLoggedIn"
          :user="demoUser"
        >
        </AppHeader>

        <div class="flex-1 flex overflow-hidden">
          <!-- Left: List -->
          <div class="w-72 flex-shrink-0 border-r border-gray-200 bg-white overflow-y-auto">
            <div class="p-3 space-y-2">
              <button @click="openCreateDialog"
                class="w-full px-3 py-2 text-sm bg-primary-600 text-white rounded-lg hover:bg-primary-700 flex items-center justify-center gap-1">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
                </svg>
                新建智能体
              </button>
              <input v-model="searchQuery" type="text" placeholder="搜索..."
                class="w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-primary-400" />
            </div>
            <div v-if="filteredList.length === 0" class="p-4 text-sm text-gray-400 text-center">
              暂无智能体定义
            </div>
            <div v-for="item in filteredList" :key="item.agent_id"
              @click="selectAgent(item)"
              :class="['p-3 mx-2 mb-1 rounded-lg cursor-pointer transition-colors border',
                selectedAgentId === item.agent_id
                  ? 'bg-primary-50 border-primary-200'
                  : 'border-transparent hover:bg-gray-50']">
              <div class="flex items-center justify-between">
                <span class="text-sm font-medium text-gray-800 truncate">{{ item.name }}</span>
                <span :class="['px-1.5 py-0.5 text-xs rounded',
                  item.status === 'active' ? 'bg-success-100 text-success-700' : 'bg-gray-100 text-gray-500']">
                  {{ item.status === 'active' ? '启用' : '禁用' }}
                </span>
              </div>
              <div class="text-xs text-gray-400 mt-0.5 truncate">{{ item.agent_id }}</div>
              <div v-if="item.production_version" class="text-xs text-gray-400 mt-0.5">
                Prompt V{{ item.production_version }}
              </div>
            </div>
          </div>

          <!-- Right: Detail/Edit -->
          <div class="flex-1 flex flex-col min-w-0 overflow-hidden">
            <div v-if="!selectedAgent" class="flex-1 flex items-center justify-center text-gray-400">
              <div class="text-center">
                <svg class="w-12 h-12 mx-auto mb-3 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                </svg>
                <p class="text-sm">选择左侧智能体查看详情，或新建一个</p>
              </div>
            </div>

            <div v-else class="flex-1 flex overflow-hidden">
              <!-- Definition Area (left half) -->
              <div class="w-[45%] flex flex-col border-r border-gray-200 overflow-y-auto p-4">
                <div class="flex items-center justify-between mb-4">
                  <h3 class="text-sm font-semibold text-gray-700">定义配置</h3>
                  <div class="flex gap-2">
                    <button @click="saveDefinition"
                      class="px-3 py-1.5 text-xs bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50"
                      :disabled="saving">
                      {{ saving ? '保存中...' : '保存定义' }}
                    </button>
                    <button @click="confirmDelete"
                      class="px-3 py-1.5 text-xs text-danger-600 border border-danger-200 rounded-lg hover:bg-danger-50">
                      删除
                    </button>
                  </div>
                </div>

                <div class="space-y-3">
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">Agent ID <span class="text-danger-500">*</span></label>
                    <input v-model="form.agent_id" type="text" disabled
                      class="w-full px-3 py-1.5 text-sm bg-gray-100 border border-gray-200 rounded-lg" />
                  </div>
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">名称 <span class="text-danger-500">*</span></label>
                    <input v-model="form.name" type="text"
                      class="w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-primary-400" />
                  </div>
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">描述</label>
                    <textarea v-model="form.description" rows="2"
                      class="w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-primary-400"></textarea>
                  </div>
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">能力标签</label>
                    <div class="flex flex-wrap gap-1 mb-1">
                      <span v-for="(cap, i) in form.capabilities" :key="i"
                        class="px-2 py-0.5 text-xs bg-info-50 text-info-700 rounded-full flex items-center gap-1">
                        {{ cap }}
                        <button @click="form.capabilities.splice(i, 1)" class="text-info-400 hover:text-info-600">&times;</button>
                      </span>
                    </div>
                    <div class="flex gap-1">
                      <input v-model="newCapability" type="text" placeholder="添加能力标签"
                        class="flex-1 px-2 py-1 text-xs border border-gray-200 rounded-lg"
                        @keyup.enter="addCapability" />
                      <button @click="addCapability" class="px-2 py-1 text-xs bg-gray-100 hover:bg-gray-200 rounded-lg">+</button>
                    </div>
                  </div>
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">工具配置</label>
                    <div class="bg-gray-50 rounded-lg p-2">
                      <label class="flex items-center gap-2 text-xs text-gray-600 mb-2">
                        <input type="checkbox" v-model="toolsInherit" />
                        继承默认工具
                      </label>
                      <div v-if="!toolsInherit">
                        <div v-for="(tool, i) in additionalTools" :key="i"
                          class="flex items-center gap-1 mb-1">
                          <input :value="tool" disabled
                            class="flex-1 px-2 py-1 text-xs bg-white border border-gray-200 rounded-lg" />
                          <button @click="additionalTools.splice(i, 1)" class="text-danger-400 hover:text-danger-600 text-xs">&times;</button>
                        </div>
                        <div class="flex gap-1">
                          <input v-model="newTool" type="text" placeholder="工具名"
                            class="flex-1 px-2 py-1 text-xs border border-gray-200 rounded-lg"
                            @keyup.enter="addTool" />
                          <button @click="addTool" class="px-2 py-1 text-xs bg-gray-100 hover:bg-gray-200 rounded-lg">+</button>
                        </div>
                      </div>
                    </div>
                  </div>
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">技能配置</label>
                    <div class="flex flex-wrap gap-1 mb-1">
                      <span v-for="(skill, i) in form.skills?.allowed || []" :key="i"
                        class="px-2 py-0.5 text-xs bg-success-50 text-success-700 rounded-full flex items-center gap-1">
                        {{ skill }}
                        <button @click="removeSkill(i)" class="text-success-400 hover:text-success-600">&times;</button>
                      </span>
                    </div>
                    <div class="flex gap-1">
                      <input v-model="newSkill" type="text" placeholder="技能名"
                        class="flex-1 px-2 py-1 text-xs border border-gray-200 rounded-lg"
                        @keyup.enter="addSkill" />
                      <button @click="addSkill" class="px-2 py-1 text-xs bg-gray-100 hover:bg-gray-200 rounded-lg">+</button>
                    </div>
                  </div>
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">状态</label>
                    <select v-model="form.status"
                      class="w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-primary-400">
                      <option value="active">启用</option>
                      <option value="disabled">禁用</option>
                    </select>
                  </div>
                </div>
              </div>

              <!-- Prompt Area (right half) -->
              <div class="w-[55%] flex flex-col overflow-hidden">
                <div class="p-4 pb-2 flex-shrink-0">
                  <div class="flex items-center justify-between mb-2">
                    <h3 class="text-sm font-semibold text-gray-700">System Prompt</h3>
                    <div class="flex gap-2">
                      <button @click="savePromptDraft"
                        class="px-3 py-1.5 text-xs border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50"
                        :disabled="promptSaving">
                        {{ promptSaving ? '保存中...' : '保存草稿' }}
                      </button>
                      <button @click="commitPromptVersion"
                        class="px-3 py-1.5 text-xs bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50"
                        :disabled="promptSaving">
                        提交新版本
                      </button>
                    </div>
                  </div>
                  <div v-if="selectedAgent.production_version" class="text-xs text-gray-400 mb-2">
                    当前 production: V{{ selectedAgent.production_version }}
                    <span v-if="draftModified" class="text-warning-600 ml-2">（有未提交的修改）</span>
                  </div>
                </div>
                <div class="flex-1 flex overflow-hidden px-4 pb-4">
                  <!-- Editor -->
                  <div class="flex-1 flex flex-col min-w-0 pr-3">
                    <textarea v-model="promptContent"
                      class="flex-1 w-full p-3 text-sm font-mono border border-gray-200 rounded-lg resize-none focus:outline-none focus:border-primary-400"
                      placeholder="输入 System Prompt 内容..." @input="onPromptInput"></textarea>
                    <div v-if="commitDialogVisible" class="mt-2 flex gap-2">
                      <input v-model="commitMessage" type="text" placeholder="变更说明（可选）"
                        class="flex-1 px-3 py-1.5 text-xs border border-gray-200 rounded-lg" />
                    </div>
                  </div>
                  <!-- Version History -->
                  <div class="w-48 flex-shrink-0">
                    <div class="text-xs font-medium text-gray-500 mb-2">版本历史</div>
                    <div v-if="versionsLoading" class="text-xs text-gray-400">加载中...</div>
                    <div v-else-if="versions.length === 0" class="text-xs text-gray-400">暂无版本</div>
                    <div v-else class="space-y-1.5 max-h-full overflow-y-auto">
                      <div v-for="v in versions" :key="v.version"
                        @click="loadVersionContent(v)"
                        :class="['p-2 rounded-lg cursor-pointer border transition-colors text-xs',
                          isProduction(v.version) ? 'border-success-200 bg-success-50' : 'border-gray-100 hover:border-gray-200',
                          selectedVersionNum === v.version ? 'ring-1 ring-info-300' : '']">
                        <div class="flex items-center gap-1.5">
                          <span class="font-medium" :class="isProduction(v.version) ? 'text-success-700' : 'text-gray-700'">
                            V{{ v.version }}
                          </span>
                          <span v-if="isProduction(v.version)"
                            class="px-1 py-0.5 text-[10px] bg-success-100 text-success-700 rounded">prod</span>
                        </div>
                        <div class="text-[10px] text-gray-400 mt-0.5">
                          {{ formatTime(v.created_at) }}
                        </div>
                        <div v-if="v.commit_message" class="text-[10px] text-gray-500 mt-0.5 truncate">
                          {{ v.commit_message }}
                        </div>
                      </div>
                    </div>
                    <!-- Diff & Rollback -->
                    <div v-if="versions.length >= 2" class="mt-2 pt-2 border-t border-gray-100 flex gap-1">
                      <button @click="showDiffDialog = true"
                        class="flex-1 px-2 py-1 text-[10px] text-info-600 bg-info-50 hover:bg-info-100 rounded-lg">
                        对比
                      </button>
                      <button v-if="selectedVersionNum && !isProduction(selectedVersionNum)"
                        @click="rollbackVersion(selectedVersionNum)"
                        class="flex-1 px-2 py-1 text-[10px] text-warning-600 bg-warning-50 hover:bg-warning-100 rounded-lg">
                        回滚
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>

    <!-- Create Dialog -->
    <div v-if="createDialogVisible" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div class="bg-white rounded-xl shadow-xl w-full max-w-lg p-6">
        <h3 class="text-base font-semibold mb-4">新建智能体</h3>
        <div class="space-y-3">
          <div>
            <label class="text-xs text-gray-500 mb-1 block">Agent ID <span class="text-danger-500">*</span></label>
            <input v-model="createForm.agent_id" type="text" placeholder="如 after-sales-v2"
              class="w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-primary-400" />
          </div>
          <div>
            <label class="text-xs text-gray-500 mb-1 block">名称 <span class="text-danger-500">*</span></label>
            <input v-model="createForm.name" type="text" placeholder="如 售后服务助手"
              class="w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-primary-400" />
          </div>
          <div>
            <label class="text-xs text-gray-500 mb-1 block">描述</label>
            <input v-model="createForm.description" type="text" placeholder="一句话描述智能体用途"
              class="w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-primary-400" />
          </div>
          <div>
            <label class="text-xs text-gray-500 mb-1 block">System Prompt <span class="text-danger-500">*</span></label>
            <textarea v-model="createForm.system_prompt" rows="6"
              class="w-full px-3 py-1.5 text-sm font-mono border border-gray-200 rounded-lg focus:outline-none focus:border-primary-400"
              placeholder="输入初始 System Prompt..."></textarea>
          </div>
        </div>
        <div class="flex justify-end gap-2 mt-4">
          <button @click="createDialogVisible = false"
            class="px-4 py-2 text-sm border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50">取消</button>
          <button @click="doCreate" :disabled="creating"
            class="px-4 py-2 text-sm bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50">
            {{ creating ? '创建中...' : '创建' }}
          </button>
        </div>
      </div>
    </div>

    <!-- Diff Dialog -->
    <div v-if="showDiffDialog" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div class="bg-white rounded-xl shadow-xl w-full max-w-4xl max-h-[80vh] flex flex-col p-6">
        <div class="flex items-center justify-between mb-4 flex-shrink-0">
          <h3 class="text-base font-semibold">版本对比</h3>
          <button @click="showDiffDialog = false" class="p-1 text-gray-400 hover:text-gray-600">&times;</button>
        </div>
        <div class="flex gap-3 mb-4 flex-shrink-0">
          <div class="flex items-center gap-2">
            <label class="text-sm text-gray-500">从</label>
            <select v-model="diffFrom" class="px-2 py-1 text-sm border border-gray-300 rounded-lg">
              <option v-for="v in versions" :key="v.version" :value="v.version">V{{ v.version }}</option>
            </select>
          </div>
          <div class="flex items-center gap-2">
            <label class="text-sm text-gray-500">到</label>
            <select v-model="diffTo" class="px-2 py-1 text-sm border border-gray-300 rounded-lg">
              <option v-for="v in versions" :key="v.version" :value="v.version">V{{ v.version }}</option>
            </select>
          </div>
          <button @click="loadDiff" :disabled="diffLoading"
            class="px-3 py-1 text-sm text-white bg-primary-600 hover:bg-primary-700 rounded-lg disabled:opacity-50">
            {{ diffLoading ? '对比中...' : '对比' }}
          </button>
        </div>
        <div v-if="diffData" class="flex-1 flex gap-4 overflow-hidden min-h-0">
          <div class="flex-1 flex flex-col min-w-0">
            <div class="text-sm font-medium text-gray-500 mb-1">V{{ diffData.from.version }}</div>
            <pre class="flex-1 bg-gray-50 rounded-lg p-3 text-xs text-gray-700 overflow-auto whitespace-pre-wrap font-mono">{{ diffData.from.content }}</pre>
          </div>
          <div class="flex-1 flex flex-col min-w-0">
            <div class="text-sm font-medium text-gray-500 mb-1">V{{ diffData.to.version }}</div>
            <pre class="flex-1 bg-primary-50 rounded-lg p-3 text-xs text-gray-700 overflow-auto whitespace-pre-wrap font-mono">{{ diffData.to.content }}</pre>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import AppHeader from './AppHeader.vue'
import {
  listDefinitions, getDefinition, createDefinition,
  updateDefinition, deleteDefinition, updateSystemPrompt,
  listVersions, getVersion, diffVersions,
  saveDraft, listLabels, setLabel,
  type AgentDefinition, type PromptVersion, type DiffResult,
} from '@/api/agentDefinitions'

// ============== Auth (portal mode) ==============
const effectiveIsLoggedIn = ref(true)
const demoUser = ref({ username: '管理员' })

// ============== State ==============
const agents = ref<AgentDefinition[]>([])
const selectedAgentId = ref<string | null>(null)
const selectedAgent = ref<AgentDefinition | null>(null)
const searchQuery = ref('')
const toasts = ref<{ id: number; message: string; type: string }[]>([])
let toastId = 0

// Form state
const form = ref<Record<string, any>>({})
const toolsInherit = ref(true)
const additionalTools = ref<string[]>([])
const newCapability = ref('')
const newTool = ref('')
const newSkill = ref('')
const saving = ref(false)

// Prompt state
const promptContent = ref('')
const commitMessage = ref('')
const commitDialogVisible = ref(false)
const promptSaving = ref(false)
const draftModified = ref(false)
const versions = ref<PromptVersion[]>([])
const versionsLoading = ref(false)
const selectedVersionNum = ref<number | null>(null)
const labels = ref<{ label: string; version: number | null }[]>([])

// Create dialog
const createDialogVisible = ref(false)
const creating = ref(false)
const createForm = ref({ agent_id: '', name: '', description: '', system_prompt: '' })

// Diff dialog
const showDiffDialog = ref(false)
const diffFrom = ref(1)
const diffTo = ref(2)
const diffData = ref<DiffResult | null>(null)
const diffLoading = ref(false)

// ============== Computed ==============
const filteredList = computed(() => {
  if (!searchQuery.value) return agents.value
  const q = searchQuery.value.toLowerCase()
  return agents.value.filter(a =>
    a.name.toLowerCase().includes(q) ||
    a.agent_id.toLowerCase().includes(q)
  )
})

// ============== Toast ==============
function showToast(message: string, type = 'success') {
  const id = ++toastId
  toasts.value.push({ id, message, type })
  setTimeout(() => { toasts.value = toasts.value.filter(t => t.id !== id) }, 2500)
}

// ============== Data Loading ==============
async function loadList() {
  try {
    const res = await listDefinitions({ page: 1, page_size: 100 })
    if (res.success) agents.value = res.data.items
  } catch (e) { console.error('加载列表失败', e) }
}

async function selectAgent(item: AgentDefinition) {
  selectedAgentId.value = item.agent_id
  try {
    const res = await getDefinition(item.agent_id)
    if (res.success) {
      selectedAgent.value = res.data
      populateForm(res.data)
      await loadVersionsAndLabels()
      await loadLatestPromptContent()
    }
  } catch (e) { console.error('加载详情失败', e) }
}

function populateForm(data: AgentDefinition) {
  form.value = {
    agent_id: data.agent_id,
    name: data.name,
    description: data.description || '',
    capabilities: [...(data.capabilities || [])],
    skills: { ...data.skills },
    status: data.status || 'active',
  }
  toolsInherit.value = data.tools?.inherit !== false
  additionalTools.value = data.tools?.additional || []
}

async function loadVersionsAndLabels() {
  if (!selectedAgentId.value) return
  versionsLoading.value = true
  try {
    const [vRes, lRes] = await Promise.all([
      listVersions(selectedAgentId.value, 1, 50),
      listLabels(selectedAgentId.value),
    ])
    if (vRes.success) versions.value = vRes.data.items
    if (lRes.success) labels.value = lRes.data
    if (versions.value.length >= 2) {
      diffFrom.value = versions.value[1].version
      diffTo.value = versions.value[0].version
    }
  } catch (e) { console.error('加载版本失败', e) }
  finally { versionsLoading.value = false }
}

async function loadLatestPromptContent() {
  if (!selectedAgent.value?.production_version) {
    // Try latest version
    if (versions.value.length > 0) {
      await loadVersionContent(versions.value[0])
    } else {
      promptContent.value = ''
    }
    return
  }
  try {
    const res = await getVersion(selectedAgentId.value!, selectedAgent.value.production_version)
    if (res.success) promptContent.value = res.data.content
  } catch (e) { console.error('加载 prompt 内容失败', e) }
}

async function loadVersionContent(v: PromptVersion) {
  selectedVersionNum.value = v.version
  promptContent.value = v.content
  draftModified.value = false
}

function isProduction(version: number): boolean {
  const label = labels.value.find(l => l.label === 'production')
  return label?.version === version
}

function formatTime(ts: string): string {
  if (!ts) return ''
  const d = new Date(ts)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// ============== Definition CRUD ==============
function openCreateDialog() {
  createForm.value = { agent_id: '', name: '', description: '', system_prompt: '' }
  createDialogVisible.value = true
}

async function doCreate() {
  const f = createForm.value
  if (!f.agent_id || !f.name || !f.system_prompt) {
    showToast('请填写必填字段', 'error')
    return
  }
  creating.value = true
  try {
    const res = await createDefinition(f)
    if (res.success) {
      showToast('创建成功')
      createDialogVisible.value = false
      await loadList()
      const created = agents.value.find(a => a.agent_id === f.agent_id)
      if (created) await selectAgent(created)
    } else {
      showToast('创建失败', 'error')
    }
  } catch (e: any) {
    showToast(e.message || '创建失败', 'error')
  } finally { creating.value = false }
}

async function saveDefinition() {
  if (!selectedAgentId.value) return
  saving.value = true
  try {
    const data: Record<string, any> = {
      name: form.value.name,
      description: form.value.description || null,
      capabilities: form.value.capabilities,
      tools: { inherit: toolsInherit.value, additional: additionalTools.value },
      skills: form.value.skills,
      status: form.value.status,
    }
    const res = await updateDefinition(selectedAgentId.value, data)
    if (res.success) {
      showToast('定义已保存')
      await loadList()
    } else {
      showToast('保存失败', 'error')
    }
  } catch (e: any) {
    showToast(e.message || '保存失败', 'error')
  } finally { saving.value = false }
}

function confirmDelete() {
  if (!selectedAgentId.value) return
  if (!confirm(`确定删除智能体 "${form.value.name}"？此操作不可恢复。`)) return
  doDelete()
}

async function doDelete() {
  if (!selectedAgentId.value) return
  try {
    const res = await deleteDefinition(selectedAgentId.value)
    if (res.success) {
      showToast('已删除')
      selectedAgent.value = null
      selectedAgentId.value = null
      await loadList()
    } else {
      showToast('删除失败', 'error')
    }
  } catch (e: any) {
    showToast(e.message || '删除失败', 'error')
  }
}

// ============== Capability / Tool / Skill helpers ==============
function addCapability() {
  const v = newCapability.value.trim()
  if (v && !form.value.capabilities.includes(v)) {
    form.value.capabilities.push(v)
    newCapability.value = ''
  }
}

function addTool() {
  const v = newTool.value.trim()
  if (v && !additionalTools.value.includes(v)) {
    additionalTools.value.push(v)
    newTool.value = ''
  }
}

function addSkill() {
  const v = newSkill.value.trim()
  if (!v) return
  if (!form.value.skills) form.value.skills = { allowed: [] }
  if (!form.value.skills.allowed) form.value.skills.allowed = []
  if (!form.value.skills.allowed.includes(v)) {
    form.value.skills.allowed.push(v)
    newSkill.value = ''
  }
}

function removeSkill(i: number) {
  form.value.skills?.allowed?.splice(i, 1)
}

// ============== Prompt Management ==============
function onPromptInput() {
  draftModified.value = true
}

async function savePromptDraft() {
  if (!selectedAgentId.value) return
  promptSaving.value = true
  try {
    const res = await saveDraft(selectedAgentId.value, {
      content: promptContent.value,
      base_version: selectedAgent.value?.production_version || undefined,
    })
    if (res.success) {
      showToast('草稿已保存')
      draftModified.value = false
    }
  } catch (e: any) {
    showToast(e.message || '保存草稿失败', 'error')
  } finally { promptSaving.value = false }
}

async function commitPromptVersion() {
  if (!selectedAgentId.value) return
  const msg = prompt('提交新版本，变更说明（可选）：')
  if (msg === null) return // cancelled
  promptSaving.value = true
  try {
    const res = await updateSystemPrompt(selectedAgentId.value, {
      content: promptContent.value,
      commit_message: msg || undefined,
    })
    if (res.success) {
      showToast('新版本已提交并标记为 production')
      draftModified.value = false
      await loadList()
      await selectAgent(agents.value.find(a => a.agent_id === selectedAgentId.value)!)
    } else {
      showToast('提交失败', 'error')
    }
  } catch (e: any) {
    showToast(e.message || '提交失败', 'error')
  } finally { promptSaving.value = false }
}

async function rollbackVersion(version: number) {
  if (!selectedAgentId.value) return
  if (!confirm(`确定要回滚到 V${version} 吗？`)) return
  try {
    const res = await setLabel(selectedAgentId.value, 'production', version)
    if (res.success) {
      showToast(`已回滚到 V${version}`)
      await loadList()
      await selectAgent(agents.value.find(a => a.agent_id === selectedAgentId.value)!)
    }
  } catch (e: any) {
    showToast(e.message || '回滚失败', 'error')
  }
}

async function loadDiff() {
  if (!selectedAgentId.value) return
  diffLoading.value = true
  diffData.value = null
  try {
    const res = await diffVersions(selectedAgentId.value, diffFrom.value, diffTo.value)
    if (res.success) diffData.value = res.data
  } catch (e) { console.error('对比失败', e) }
  finally { diffLoading.value = false }
}

// ============== Init ==============
onMounted(loadList)
</script>
