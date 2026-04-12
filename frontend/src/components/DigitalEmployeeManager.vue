<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <!-- Toast Messages -->
    <div class="fixed top-[20%] left-1/2 -translate-x-1/2 z-50 space-y-2">
      <TransitionGroup name="toast">
        <div v-for="toast in toasts" :key="toast.id"
          :class="['px-4 py-2 rounded-lg shadow-lg text-sm flex items-center gap-2',
            toast.type === 'success' ? 'bg-green-500 text-white' : '',
            toast.type === 'error' ? 'bg-red-500 text-white' : '',
            toast.type === 'info' ? 'bg-blue-500 text-white' : ''
          ]">
          <svg v-if="toast.type === 'success'" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
          </svg>
          <span>{{ toast.message }}</span>
        </div>
      </TransitionGroup>
    </div>

    <!-- Main Content -->
    <main class="flex-1 flex overflow-hidden">
      <!-- Session Sidebar -->
      <SessionSidebar
        :is-collapsed="isSidebarCollapsed"
        @collapse="isSidebarCollapsed = true"
      />

      <!-- Right Content Area -->
      <div class="flex-1 flex flex-col min-w-0">
        <!-- Header Bar -->
        <AppHeader
          title="数字员工"
          :is-online="true"
          :is-logged-in="isLoggedIn"
          :user="user"
          @toggle-sidebar="isSidebarCollapsed = !isSidebarCollapsed"
        >
          <template #menu-items="{ closeMenu }">
            <button
              @click="createNew(); closeMenu()"
              class="w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
              </svg>
              新建定制
            </button>
          </template>
        </AppHeader>

        <!-- Content Area -->
        <div class="flex-1 flex overflow-hidden">
          <!-- Left: List -->
          <div class="w-64 flex-shrink-0 border-r border-gray-200 bg-white overflow-y-auto">
        <!-- Builtin -->
        <div class="p-3">
          <div class="text-sm font-bold text-gray-500 mb-2">内置 ({{ builtinList.length }})</div>
          <div v-for="item in builtinList" :key="item.agent_id"
            @click="selectAgent(item)"
            :class="['p-2 rounded-lg cursor-pointer mb-1 transition-colors', selectedAgent?.agent_id === item.agent_id ? 'bg-blue-50 text-blue-700 border border-blue-200' : 'hover:bg-gray-50 text-gray-700']"
          >
            <div class="text-sm font-medium truncate">{{ item.name }}</div>
            <div class="text-xs text-gray-400 truncate mt-0.5">{{ item.description || '无描述' }}</div>
          </div>
        </div>
        <!-- Custom -->
        <div class="p-3 border-t border-gray-100">
          <div class="text-sm font-bold text-gray-500 mb-2">定制 ({{ customList.length }})</div>
          <div v-for="item in customList" :key="item.agent_id"
            @click="selectAgent(item)"
            :class="['p-2 rounded-lg cursor-pointer mb-1 transition-colors', selectedAgent?.agent_id === item.agent_id ? 'bg-blue-50 text-blue-700 border border-blue-200' : 'hover:bg-gray-50 text-gray-700']"
          >
            <div class="text-sm font-medium truncate">{{ item.name }}</div>
            <div class="text-xs text-gray-400 truncate mt-0.5">{{ item.description || '无描述' }}</div>
          </div>
          <div v-if="customList.length === 0" class="text-xs text-gray-400 text-center py-4">暂无定制数字员工</div>
        </div>
      </div>

      <!-- Right: Detail/Edit Panel -->
      <div class="flex-1 overflow-y-auto p-6">
        <!-- Empty State -->
        <div v-if="!selectedAgent && !isNewMode" class="flex items-center justify-center h-full text-gray-400">
          <div class="text-center">
            <svg class="w-16 h-16 mx-auto mb-4 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            <p class="text-base">选择一个数字员工查看详情</p>
            <p class="text-sm mt-1">或点击右上角"新建定制"创建</p>
          </div>
        </div>

        <!-- Read-only View (Builtin) -->
        <div v-else-if="selectedAgent && !isEditMode" class="flex flex-col h-full">
          <div class="flex items-center justify-between mb-6 flex-shrink-0">
            <div>
              <h2 class="text-base font-semibold text-gray-800">{{ detail?.name }}</h2>
              <span :class="['inline-block mt-1 px-2 py-0.5 text-xs rounded-full', selectedAgent.type === 'builtin' ? 'bg-blue-100 text-blue-700' : 'bg-green-100 text-green-700']">
                {{ selectedAgent.type === 'builtin' ? '内置' : '定制' }}
              </span>
            </div>
            <div class="flex gap-2">
              <button v-if="selectedAgent.type === 'custom'" @click="enterEdit" class="px-3 py-1.5 text-sm text-white bg-blue-600 hover:bg-blue-700 rounded-lg">编辑</button>
              <button @click="showDuplicateDialog = true" class="px-3 py-1.5 text-sm text-gray-600 bg-white border border-gray-300 hover:bg-gray-50 rounded-lg">另存为</button>
            </div>
          </div>
          <div class="space-y-4 flex-1 flex flex-col min-h-0">
            <div>
              <label class="block text-sm font-bold text-gray-500 mb-1 bg-gray-200 rounded px-2 py-1">ID</label>
              <p class="text-sm text-gray-800 bg-gray-50 rounded-lg px-3 py-2 font-mono">{{ detail?.agent_id }}</p>
            </div>
            <div>
              <label class="block text-sm font-bold text-gray-500 mb-1 bg-gray-200 rounded px-2 py-1">描述</label>
              <p class="text-sm text-gray-800 whitespace-pre-wrap">{{ detail?.description || '无' }}</p>
            </div>
            <div>
              <label class="block text-sm font-bold text-gray-500 mb-1 bg-gray-200 rounded px-2 py-1">能力标签</label>
              <div class="flex flex-wrap gap-1">
                <span v-for="cap in detail?.capabilities" :key="cap" class="px-2 py-0.5 text-xs bg-gray-200 text-gray-600 rounded">{{ cap }}</span>
                <span v-if="!detail?.capabilities?.length" class="text-sm text-gray-400">无</span>
              </div>
            </div>
            <div>
              <label class="block text-sm font-bold text-gray-500 mb-1 bg-gray-200 rounded px-2 py-1">系统提示词</label>
              <div class="bg-gray-50 rounded-lg p-4 flex-1 overflow-y-auto text-sm text-gray-800 whitespace-pre-wrap" v-html="renderedMarkdown"></div>
            </div>
          </div>
        </div>

        <!-- Edit / Create Mode -->
        <div v-else class="h-full flex flex-col">
          <div class="flex items-center justify-between mb-4 flex-shrink-0">
            <h2 class="text-base font-semibold text-gray-800">{{ isNewMode ? '新建数字员工' : '编辑数字员工' }}</h2>
            <button @click="cancelEdit" class="px-3 py-1.5 text-sm text-gray-600 bg-white border border-gray-300 hover:bg-gray-50 rounded-lg">取消</button>
          </div>
          <div class="flex-1 flex flex-col min-h-0 space-y-4 overflow-y-auto">
            <!-- Agent ID -->
            <div class="flex-shrink-0">
              <label class="block text-sm font-medium text-gray-500 mb-1">ID (目录名)</label>
              <input v-model="editForm.agent_id" :disabled="!isNewMode" type="text" placeholder="如 my-custom-agent"
                class="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-gray-100" />
            </div>
            <!-- Name -->
            <div class="flex-shrink-0">
              <label class="block text-sm font-medium text-gray-500 mb-1">显示名称</label>
              <input v-model="editForm.name" type="text" placeholder="数字员工名称"
                class="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500" />
            </div>
            <!-- Description -->
            <div class="flex-shrink-0">
              <label class="block text-sm font-medium text-gray-500 mb-1">描述</label>
              <textarea v-model="editForm.description" rows="2" placeholder="简要描述此数字员工的用途"
                class="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 resize-y"></textarea>
            </div>
            <!-- Capabilities -->
            <div class="flex-shrink-0">
              <label class="block text-sm font-medium text-gray-500 mb-1">能力标签</label>
              <div class="flex flex-wrap gap-1 mb-1">
                <span v-for="(cap, idx) in editForm.capabilities" :key="idx"
                  class="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-blue-50 text-blue-700 rounded">
                  {{ cap }}
                  <button @click="editForm.capabilities.splice(idx, 1)" class="hover:text-red-500">&times;</button>
                </span>
              </div>
              <div class="flex gap-2">
                <input v-model="newCapability" @keydown.enter.prevent="addCapability" type="text" placeholder="输入后回车添加"
                  class="flex-1 px-3 py-1.5 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500" />
                <button @click="addCapability" class="px-3 py-1.5 text-sm text-blue-600 bg-blue-50 hover:bg-blue-100 rounded-lg">添加</button>
              </div>
            </div>
            <!-- System Prompt - 撑满剩余空间 -->
            <div class="flex-1 flex flex-col min-h-0">
              <div class="flex items-center justify-between mb-1 flex-shrink-0">
                <label class="block text-sm font-medium text-gray-500">系统提示词 (Markdown)</label>
                <div class="flex gap-1">
                  <button @click="promptMode = 'edit'" :class="['px-2 py-1 text-xs rounded', promptMode === 'edit' ? 'bg-blue-100 text-blue-700' : 'text-gray-400 hover:bg-gray-100']">编辑</button>
                  <button @click="promptMode = 'preview'" :class="['px-2 py-1 text-xs rounded', promptMode === 'preview' ? 'bg-blue-100 text-blue-700' : 'text-gray-400 hover:bg-gray-100']">预览</button>
                </div>
              </div>
              <textarea v-if="promptMode === 'edit'" v-model="editForm.system_prompt" placeholder="## 角色定义&#10;&#10;你是一个..."
                class="flex-1 w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 font-mono resize-none min-h-0"></textarea>
              <div v-else class="flex-1 bg-gray-50 rounded-lg p-4 overflow-y-auto text-sm text-gray-800 whitespace-pre-wrap" v-html="editRenderedMarkdown"></div>
            </div>
          </div>

          <!-- Actions - 固定在底部 -->
          <div class="flex gap-2 mt-4 pt-4 border-t border-gray-200 flex-shrink-0">
            <button @click="aiEnhance" :disabled="aiEnhancing" class="px-3 py-1.5 text-sm text-purple-700 bg-purple-50 hover:bg-purple-100 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed">
              {{ aiEnhancing ? 'AI 完善中...' : 'AI 完善' }}
            </button>
            <button @click="saveAgent" class="px-4 py-1.5 text-sm text-white bg-blue-600 hover:bg-blue-700 rounded-lg">保存</button>
            <button v-if="!isNewMode && selectedAgent?.type === 'custom'" @click="deleteCurrentAgent" class="px-3 py-1.5 text-sm text-red-600 bg-red-50 hover:bg-red-100 rounded-lg">删除</button>
            <button @click="showDuplicateDialog = true" class="px-3 py-1.5 text-sm text-gray-600 bg-white border border-gray-300 hover:bg-gray-50 rounded-lg ml-auto">另存为</button>
          </div>
        </div>
          </div>
        </div>
      </div>
    </main>

    <!-- Duplicate Dialog -->
    <div v-if="showDuplicateDialog" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div class="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
        <h3 class="text-base font-semibold mb-4">另存为</h3>
        <div class="space-y-3">
          <div>
            <label class="block text-sm font-medium text-gray-500 mb-1">新 ID</label>
            <input v-model="duplicateForm.new_agent_id" type="text" placeholder="新目录名" class="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500" />
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-500 mb-1">新名称</label>
            <input v-model="duplicateForm.new_name" type="text" placeholder="新显示名称" class="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500" />
          </div>
        </div>
        <div class="flex justify-end gap-2 mt-6">
          <button @click="showDuplicateDialog = false" class="px-3 py-1.5 text-sm text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-lg">取消</button>
          <button @click="duplicateAgent" class="px-3 py-1.5 text-sm text-white bg-blue-600 hover:bg-blue-700 rounded-lg">确认另存</button>
        </div>
      </div>
    </div>

    <!-- AI Enhance Compare Dialog -->
    <div v-if="showAiCompareDialog" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div class="bg-white rounded-xl shadow-xl w-full max-w-5xl max-h-[85vh] flex flex-col p-6">
        <div class="flex items-center justify-between mb-4 flex-shrink-0">
          <h3 class="text-base font-semibold">AI 完善结果</h3>
          <button @click="showAiCompareDialog = false" class="p-1 text-gray-400 hover:text-gray-600">&times;</button>
        </div>
        <div class="flex-1 flex gap-4 overflow-hidden min-h-0">
          <div class="flex-1 flex flex-col min-w-0">
            <div class="text-sm font-medium text-gray-500 mb-1">优化前（原始内容）</div>
            <pre class="flex-1 bg-gray-50 rounded-lg p-3 text-xs text-gray-700 overflow-auto whitespace-pre-wrap font-mono">{{ aiCompareOriginal }}</pre>
          </div>
          <div class="flex-1 flex flex-col min-w-0">
            <div class="text-sm font-medium text-gray-500 mb-1">优化后（AI 建议）</div>
            <pre class="flex-1 bg-blue-50 rounded-lg p-3 text-xs text-gray-700 overflow-auto whitespace-pre-wrap font-mono">{{ aiCompareEnhanced }}</pre>
          </div>
        </div>
        <div class="flex justify-end gap-2 mt-4 flex-shrink-0">
          <button @click="showAiCompareDialog = false" class="px-3 py-1.5 text-sm text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-lg">拒绝</button>
          <button @click="acceptAiEnhance" class="px-3 py-1.5 text-sm text-white bg-blue-600 hover:bg-blue-700 rounded-lg">接受并应用</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { marked } from 'marked'
import SessionSidebar from './SessionSidebar.vue'
import AppHeader from './AppHeader.vue'
import { useAuth } from '@/composables/useAuth'
import {
  listSubagents,
  getSubagentDetail,
  getSubagentContent,
  createSubagent,
  updateSubagent,
  deleteSubagent,
  duplicateSubagent,
  aiEnhanceSubagent,
  type SubagentListItem,
  type SubagentDetail,
} from '../api/adminSubagent'

const { user, isLoggedIn } = useAuth()
const isSidebarCollapsed = ref(false)

// State
const allList = ref<SubagentListItem[]>([])
const selectedAgent = ref<SubagentListItem | null>(null)
const detail = ref<SubagentDetail | null>(null)
const isNewMode = ref(false)
const isEditMode = ref(false)
const promptMode = ref<'edit' | 'preview'>('edit')
const loading = ref(false)

// Edit form
const editForm = ref({
  agent_id: '',
  name: '',
  description: '',
  capabilities: [] as string[],
  system_prompt: '',
})
const newCapability = ref('')

// Duplicate dialog
const showDuplicateDialog = ref(false)
const duplicateForm = ref({ new_agent_id: '', new_name: '' })

// AI enhance
const aiEnhancing = ref(false)
const showAiCompareDialog = ref(false)
const aiCompareOriginal = ref('')
const aiCompareEnhanced = ref('')

// Toast messages
const toasts = ref<Array<{ id: number; message: string; type: 'success' | 'error' | 'info' }>>([])
let toastId = 0

function showToast(message: string, type: 'success' | 'error' | 'info' = 'info') {
  const id = toastId++
  toasts.value.push({ id, message, type })
  setTimeout(() => {
    toasts.value = toasts.value.filter(t => t.id !== id)
  }, 3000)
}

// Computed
const builtinList = computed(() => allList.value.filter(i => i.type === 'builtin'))
const customList = computed(() => allList.value.filter(i => i.type === 'custom'))

const renderedMarkdown = computed(() => {
  if (!detail.value?.system_prompt) return ''
  return marked(detail.value.system_prompt, { breaks: true })
})

const editRenderedMarkdown = computed(() => {
  if (!editForm.value.system_prompt) return ''
  return marked(editForm.value.system_prompt, { breaks: true })
})

// Methods
async function loadList() {
  try {
    const res = await listSubagents()
    if (res.success) {
      allList.value = res.data || []
    }
  } catch (e: any) {
    console.error('临时调试：加载列表失败', e)
  }
}

async function selectAgent(item: SubagentListItem) {
  selectedAgent.value = item
  isNewMode.value = false
  isEditMode.value = false
  loading.value = true
  try {
    const res = await getSubagentDetail(item.agent_id)
    if (res.success) {
      detail.value = res.data
    }
  } catch (e: any) {
    console.error('临时调试：加载详情失败', e)
  } finally {
    loading.value = false
  }
}

function createNew() {
  selectedAgent.value = null
  detail.value = null
  isNewMode.value = true
  isEditMode.value = true
  editForm.value = {
    agent_id: '',
    name: '',
    description: '',
    capabilities: [],
    system_prompt: '',
  }
  promptMode.value = 'edit'
}

function enterEdit() {
  if (!detail.value) return
  isEditMode.value = true
  editForm.value = {
    agent_id: detail.value.agent_id,
    name: detail.value.name,
    description: detail.value.description,
    capabilities: [...(detail.value.capabilities || [])],
    system_prompt: detail.value.system_prompt,
  }
  promptMode.value = 'edit'
}

function cancelEdit() {
  if (isNewMode.value) {
    isNewMode.value = false
    selectedAgent.value = null
    detail.value = null
  } else {
    isEditMode.value = false
  }
}

function addCapability() {
  const val = newCapability.value.trim().toLowerCase().replace(/\s+/g, '_')
  if (val && !editForm.value.capabilities.includes(val)) {
    editForm.value.capabilities.push(val)
  }
  newCapability.value = ''
}

async function saveAgent() {
  if (!editForm.value.agent_id.trim() || !editForm.value.name.trim()) {
    showToast('ID 和名称不能为空', 'error')
    return
  }
  try {
    let res
    if (isNewMode.value) {
      res = await createSubagent(editForm.value)
    } else {
      res = await updateSubagent(editForm.value.agent_id, editForm.value)
    }
    if (res.success) {
      showToast('保存成功', 'success')
      await loadList()
      // Select the saved agent
      const agentId = isNewMode.value ? editForm.value.agent_id : selectedAgent.value?.agent_id
      if (agentId) {
        const found = allList.value.find(i => i.agent_id === agentId)
        if (found) await selectAgent(found)
      }
    } else {
      showToast(res.error || '保存失败', 'error')
    }
  } catch (e: any) {
    showToast(e.message || '保存失败', 'error')
  }
}

async function deleteCurrentAgent() {
  if (!selectedAgent.value) return
  const name = selectedAgent.value.name
  if (!confirm(`确定要删除"${name}"吗？此操作不可恢复。`)) return
  try {
    const res = await deleteSubagent(selectedAgent.value.agent_id)
    if (res.success) {
      showToast('已删除', 'success')
      selectedAgent.value = null
      detail.value = null
      await loadList()
    } else {
      showToast(res.error || '删除失败', 'error')
    }
  } catch (e: any) {
    showToast(e.message || '删除失败', 'error')
  }
}

async function duplicateAgent() {
  if (!selectedAgent.value) return
  if (!duplicateForm.value.new_agent_id.trim() || !duplicateForm.value.new_name.trim()) {
    showToast('新 ID 和名称不能为空', 'error')
    return
  }
  try {
    const res = await duplicateSubagent(selectedAgent.value.agent_id, duplicateForm.value)
    if (res.success) {
      showToast('另存为成功', 'success')
      showDuplicateDialog.value = false
      await loadList()
      const found = allList.value.find(i => i.agent_id === duplicateForm.value.new_agent_id)
      if (found) await selectAgent(found)
    } else {
      showToast(res.error || '另存为失败', 'error')
    }
  } catch (e: any) {
    showToast(e.message || '另存为失败', 'error')
  }
}

async function aiEnhance() {
  if (!editForm.value.system_prompt.trim()) {
    showToast('系统提示词不能为空', 'error')
    return
  }
  // Build the full SUBAGENT.md content from current form state
  const frontmatter = [
    '---',
    `name: ${editForm.value.name}`,
    `description: ${editForm.value.description}`,
    `version: 1.0.0`,
    `author: admin`,
    editForm.value.capabilities.length ? `capabilities:\n${editForm.value.capabilities.map(c => `  - ${c}`).join('\n')}` : '',
    '---',
  ].filter(Boolean).join('\n')
  const fullContent = `${frontmatter}\n\n${editForm.value.system_prompt}`

  aiEnhancing.value = true
  try {
    const agentId = isNewMode.value ? editForm.value.agent_id || 'new' : (selectedAgent.value?.agent_id || 'new')
    const res = await aiEnhanceSubagent(agentId, fullContent)
    if (res.success && res.data) {
      aiCompareOriginal.value = res.data.original_content
      aiCompareEnhanced.value = res.data.enhanced_content
      showAiCompareDialog.value = true
    } else {
      showToast(res.error || 'AI 完善失败', 'error')
    }
  } catch (e: any) {
    showToast(e.message || 'AI 完善失败', 'error')
  } finally {
    aiEnhancing.value = false
  }
}

function acceptAiEnhance() {
  // Extract the system_prompt from the enhanced content (after the second ---)
  const parts = aiCompareEnhanced.value.split(/^---\s*$/m)
  if (parts.length >= 3) {
    // Also try to extract name from YAML frontmatter
    const yamlPart = parts[1]
    const nameMatch = yamlPart.match(/name:\s*(.+)/)
    if (nameMatch?.[1]?.trim()) {
      editForm.value.name = nameMatch[1].trim()
    }
    // Extract capabilities
    const capMatch = yamlPart.match(/capabilities:\s*\n((?:  - .+\n?)+)/)
    if (capMatch?.[1]) {
      editForm.value.capabilities = capMatch[1].split('\n').filter(l => l.trim().startsWith('- ')).map(l => l.replace(/^\s*- /, '').trim())
    }
    // The body after the second --- is the system_prompt
    editForm.value.system_prompt = parts.slice(2).join('---').trim()
  } else {
    // Fallback: use the whole content as system_prompt
    editForm.value.system_prompt = aiCompareEnhanced.value.trim()
  }
  showAiCompareDialog.value = false
  promptMode.value = 'edit'
}

onMounted(() => {
  loadList()
})
</script>
