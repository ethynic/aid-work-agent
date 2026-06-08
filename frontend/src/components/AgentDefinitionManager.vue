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

                  <!-- Tools Picker -->
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">工具配置</label>
                    <div class="bg-gray-50 rounded-lg p-2">
                      <label class="flex items-center gap-2 text-xs text-gray-600 mb-2">
                        <input type="checkbox" v-model="toolsInherit" />
                        继承默认工具
                      </label>
                      <div v-if="!toolsInherit" class="max-h-40 overflow-y-auto space-y-1">
                        <label v-for="tool in availableTools" :key="tool.id"
                          class="flex items-center gap-2 px-2 py-1 text-xs bg-white rounded border border-gray-100 hover:border-gray-200 cursor-pointer">
                          <input type="checkbox" :checked="additionalTools.includes(tool.id)"
                            @change="toggleTool(tool.id)" />
                          <span class="font-medium text-gray-700">{{ tool.name }}</span>
                          <span class="text-gray-400 truncate flex-1">{{ tool.description }}</span>
                        </label>
                        <div v-if="availableTools.length === 0" class="text-xs text-gray-400 py-1">加载中...</div>
                      </div>
                    </div>
                  </div>

                  <!-- Skills Picker -->
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">技能配置</label>
                    <div class="bg-gray-50 rounded-lg p-2 max-h-40 overflow-y-auto space-y-1">
                      <label v-for="skill in availableSkills" :key="skill.id"
                        class="flex items-center gap-2 px-2 py-1 text-xs bg-white rounded border border-gray-100 hover:border-gray-200 cursor-pointer">
                        <input type="checkbox" :checked="form.skills?.allowed?.includes(skill.id)"
                          @change="toggleSkill(skill.id)" />
                        <span class="font-medium text-gray-700">{{ skill.name }}</span>
                        <span class="text-gray-400 truncate flex-1">{{ skill.description }}</span>
                      </label>
                      <div v-if="availableSkills.length === 0" class="text-xs text-gray-400 py-1">加载中...</div>
                    </div>
                  </div>

                  <!-- Reply Style Selector -->
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">回复风格</label>
                    <select v-model="form.reply_style"
                      class="w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-primary-400">
                      <option :value="null">默认</option>
                      <option v-for="style in replyStyles" :key="style.id" :value="style.id">
                        {{ style.name }} — {{ style.description }}
                      </option>
                    </select>
                  </div>

                  <!-- Business Pages Editor -->
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">业务页面</label>
                    <div class="bg-gray-50 rounded-lg p-2 space-y-1.5">
                      <div v-for="(page, i) in businessPages" :key="i"
                        class="flex items-center gap-1 bg-white rounded-lg p-1.5 border border-gray-100">
                        <input v-model="page.icon" placeholder="图标"
                          class="w-10 px-1 py-1 text-xs text-center border border-gray-200 rounded" />
                        <input v-model="page.title" placeholder="标题"
                          class="flex-1 min-w-0 px-2 py-1 text-xs border border-gray-200 rounded" />
                        <input v-model="page.route" placeholder="路由"
                          class="flex-1 min-w-0 px-2 py-1 text-xs border border-gray-200 rounded" />
                        <button @click="businessPages.splice(i, 1)"
                          class="text-danger-400 hover:text-danger-600 text-sm px-1">&times;</button>
                      </div>
                      <button @click="addBusinessPage"
                        class="w-full px-2 py-1 text-xs text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-lg">
                        + 添加页面
                      </button>
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

              <!-- Prompt Area (right half) — Tab-based sections editor -->
              <div class="w-[55%] flex flex-col overflow-hidden">
                <div class="p-4 pb-2 flex-shrink-0">
                  <div class="flex items-center justify-between mb-2">
                    <h3 class="text-sm font-semibold text-gray-700">System Prompt</h3>
                    <div class="flex gap-2">
                      <button @click="saveCurrentSection"
                        class="px-3 py-1.5 text-xs border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50 disabled:opacity-50"
                        :disabled="promptSaving">
                        {{ promptSaving ? '保存中...' : '保存分段' }}
                      </button>
                      <button @click="commitAllSections"
                        class="px-3 py-1.5 text-xs bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50"
                        :disabled="promptSaving">
                        全部保存
                      </button>
                    </div>
                  </div>
                  <div v-if="selectedAgent.production_version" class="text-xs text-gray-400 mb-2">
                    当前 production: V{{ selectedAgent.production_version }}
                  </div>
                </div>

                <!-- Section Tabs -->
                <div class="flex px-4 gap-1 border-b border-gray-200 mb-0 flex-shrink-0">
                  <button v-for="sk in SECTION_KEYS" :key="sk.key"
                    @click="activeSectionKey = sk.key"
                    :class="['px-3 py-1.5 text-xs rounded-t-lg transition-colors whitespace-nowrap',
                      activeSectionKey === sk.key
                        ? 'bg-white text-primary-700 font-medium border border-gray-200 border-b-white -mb-px'
                        : 'text-gray-500 hover:text-gray-700']">
                    {{ sk.label }}
                  </button>
                </div>

                <!-- Section Editor + Version History -->
                <div class="flex-1 flex overflow-hidden px-4 pb-4">
                  <!-- Editor -->
                  <div class="flex-1 flex flex-col min-w-0 pr-3">
                    <div class="flex items-center justify-between mb-1 mt-2">
                      <span class="text-xs text-gray-400">
                        {{ SECTION_KEYS.find(s => s.key === activeSectionKey)?.label }}
                      </span>
                      <button v-if="sections[activeSectionKey]"
                        @click="optimizeCurrentSection"
                        class="px-2 py-1 text-[10px] text-info-600 bg-info-50 hover:bg-info-100 rounded-lg flex items-center gap-1 disabled:opacity-50"
                        :disabled="optimizing || !sections[activeSectionKey]?.trim()">
                        <svg v-if="optimizing" class="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
                        </svg>
                        {{ optimizing ? '优化中...' : 'AI 优化' }}
                      </button>
                    </div>
                    <textarea v-model="sections[activeSectionKey]"
                      class="flex-1 w-full p-3 text-sm font-mono border border-gray-200 rounded-lg resize-none focus:outline-none focus:border-primary-400"
                      :placeholder="`输入 ${SECTION_KEYS.find(s => s.key === activeSectionKey)?.label} 内容...`"></textarea>
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
  updateDefinition, deleteDefinition,
  listVersions, diffVersions,
  listLabels, setLabel,
  listToolsMeta, listSkillsMeta, listReplyStylesMeta,
  getSections, saveSection, optimizeSection,
  SECTION_KEYS,
  type AgentDefinition, type PromptVersion, type DiffResult,
  type ToolMeta, type SkillMeta, type ReplyStyleMeta,
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
const saving = ref(false)

// Metadata for pickers
const availableTools = ref<ToolMeta[]>([])
const availableSkills = ref<SkillMeta[]>([])
const replyStyles = ref<ReplyStyleMeta[]>([])

// Business pages
const businessPages = ref<{ id: string; title: string; icon: string; route: string }[]>([])

// Sections state
const activeSectionKey = ref('role_description')
const sections = ref<Record<string, string>>({
  role_description: '',
  responsibilities: '',
  workflow: '',
  reply_style: '',
  other_notes: '',
})
const optimizing = ref(false)

// Prompt version state
const promptSaving = ref(false)
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

// ============== Metadata Loading ==============
async function loadMetadata() {
  try {
    const [tRes, sRes, rRes] = await Promise.all([
      listToolsMeta(),
      listSkillsMeta(),
      listReplyStylesMeta(),
    ])
    if (tRes.success) availableTools.value = tRes.data
    if (sRes.success) availableSkills.value = sRes.data
    if (rRes.success) replyStyles.value = rRes.data
  } catch (e) { console.error('加载元数据失败', e) }
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
      await loadSections()
    }
  } catch (e) { console.error('加载详情失败', e) }
}

function populateForm(data: AgentDefinition) {
  form.value = {
    agent_id: data.agent_id,
    name: data.name,
    description: data.description || '',
    skills: { ...data.skills },
    reply_style: data.reply_style || null,
    status: data.status || 'active',
  }
  toolsInherit.value = data.tools?.inherit !== false
  additionalTools.value = data.tools?.additional || []
  businessPages.value = (data.business_pages || []).map((p: any) => ({ ...p }))
}

async function loadSections() {
  if (!selectedAgentId.value) return
  try {
    const res = await getSections(selectedAgentId.value)
    if (res.success) {
      // Reset all to empty
      for (const sk of SECTION_KEYS) {
        sections.value[sk.key] = ''
      }
      // Populate from API
      for (const s of res.data) {
        sections.value[s.section_key] = s.content || ''
      }
    }
  } catch (e) { console.error('加载分段失败', e) }
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

async function loadVersionContent(v: PromptVersion) {
  selectedVersionNum.value = v.version
  // When viewing a version, show the monolithic content in a read-only way
  // by loading it into the sections (best effort — sections are the edit interface)
  // For now, just highlight the version in the sidebar
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

// ============== Tool/Skill Toggles ==============
function toggleTool(toolId: string) {
  const idx = additionalTools.value.indexOf(toolId)
  if (idx >= 0) {
    additionalTools.value.splice(idx, 1)
  } else {
    additionalTools.value.push(toolId)
  }
}

function toggleSkill(skillId: string) {
  if (!form.value.skills) form.value.skills = { allowed: [] }
  if (!form.value.skills.allowed) form.value.skills.allowed = []
  const arr: string[] = form.value.skills.allowed
  const idx = arr.indexOf(skillId)
  if (idx >= 0) {
    arr.splice(idx, 1)
  } else {
    arr.push(skillId)
  }
}

// ============== Business Pages ==============
function addBusinessPage() {
  businessPages.value.push({
    id: `page_${Date.now()}`,
    title: '',
    icon: '📋',
    route: '',
  })
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
      tools: { inherit: toolsInherit.value, additional: additionalTools.value },
      skills: form.value.skills,
      reply_style: form.value.reply_style || null,
      business_pages: businessPages.value.length > 0 ? businessPages.value : null,
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

// ============== Sections Management ==============
async function saveCurrentSection() {
  if (!selectedAgentId.value) return
  promptSaving.value = true
  try {
    const res = await saveSection(
      selectedAgentId.value,
      activeSectionKey.value,
      sections.value[activeSectionKey.value],
    )
    if (res.success) {
      showToast('分段已保存并提交新版本')
      await loadList()
      await loadVersionsAndLabels()
    }
  } catch (e: any) {
    showToast(e.message || '保存失败', 'error')
  } finally { promptSaving.value = false }
}

async function commitAllSections() {
  if (!selectedAgentId.value) return
  promptSaving.value = true
  try {
    for (const sk of SECTION_KEYS) {
      const content = sections.value[sk.key]
      if (content !== undefined) {
        await saveSection(selectedAgentId.value, sk.key, content)
      }
    }
    showToast('所有分段已保存并提交新版本')
    await loadList()
    await loadVersionsAndLabels()
  } catch (e: any) {
    showToast(e.message || '提交失败', 'error')
  } finally { promptSaving.value = false }
}

async function optimizeCurrentSection() {
  if (!selectedAgentId.value) return
  const key = activeSectionKey.value
  const content = sections.value[key]?.trim()
  if (!content) {
    showToast('分段内容为空，无法优化', 'error')
    return
  }
  optimizing.value = true
  try {
    const res = await optimizeSection(selectedAgentId.value, key, {
      content,
      agent_name: selectedAgent.value?.name,
      agent_description: selectedAgent.value?.description || undefined,
    })
    if (res.success) {
      sections.value[key] = res.data.content
      showToast('AI 优化完成，请检查后保存', 'info')
    }
  } catch (e: any) {
    showToast(e.message || 'AI 优化失败', 'error')
  } finally { optimizing.value = false }
}

// ============== Version History ==============
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
onMounted(() => {
  loadList()
  loadMetadata()
})
</script>
