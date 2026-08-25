<template>
  <div class="h-screen flex flex-col bg-canvas">
    <AppHeader
      :title="`定制提示词 - ${subagentName}`"
      show-back
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @back="goBack"
      @logout="handleLogout"
    />

    <div class="flex-1 overflow-y-auto p-6">
      <!-- 加载态 -->
      <div v-if="loading" class="flex justify-center py-20">
        <div class="animate-spin rounded-full h-8 w-8 border-2 border-primary-600 border-t-transparent"></div>
      </div>

      <div v-else class="max-w-4xl mx-auto">
        <!-- 说明卡片 -->
        <div class="bg-info-50 border border-info-200 rounded-lg p-4 mb-6">
          <div class="flex items-start gap-2">
            <svg class="w-5 h-5 text-info-600 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <div class="text-sm text-info-700">
              <p class="font-medium mb-1">定制提示词说明</p>
              <p class="text-xs leading-relaxed">
                这里填写的内容会追加到「{{ subagentName }}」的系统提示词末尾，作为本租户的定制需求。
                留空则使用默认配置。修改后立即生效，下次对话即可体现。
              </p>
            </div>
          </div>
        </div>

        <!-- 编辑器 -->
        <div class="bg-surface rounded-xl border border-default p-5">
          <MyTextarea
            v-model="content"
            label="Markdown 内容"
            placeholder="例如：&#10;&#10;## 本租户定制要求&#10;- 回复中必须使用「贵司」而非「你」&#10;- 所有报价保留两位小数&#10;- 涉及合同条款时必须先确认法务审核"
            monospace
            show-char-count
            enable-preview
            :rows="18"
            :auto-resize="false"
            :min-height="'384px'"
            :disabled="saving"
          >
            <template #extra>
              <span class="text-xs text-muted">
                <span v-if="lastVersion">当前版本 V{{ lastVersion }}</span>
                <span v-else>未配置</span>
              </span>
            </template>
          </MyTextarea>

          <!-- 操作按钮 -->
          <div class="flex items-center justify-between mt-4">
            <div class="text-xs text-muted">
              <span v-if="dirty" class="text-warning-600">● 有未保存的修改</span>
              <span v-else-if="lastSavedAt" class="text-success-600">✓ 已保存 {{ lastSavedAt }}</span>
            </div>
            <div class="flex gap-2">
              <button
                class="h-9 px-4 rounded-lg text-sm font-medium border border-default text-muted hover:border-danger-300 hover:text-danger-600 transition-colors disabled:opacity-50"
                :disabled="saving || !content"
                @click="handleClear"
              >
                清空
              </button>
              <button
                class="h-9 px-4 rounded-lg text-sm font-medium bg-primary-600 text-white hover:bg-primary-700 transition-colors disabled:opacity-50"
                :disabled="saving || !dirty"
                @click="handleSave"
              >
                {{ saving ? '保存中...' : '保存' }}
              </button>
            </div>
          </div>
        </div>

        <!-- 模板文件区 -->
        <div class="bg-surface rounded-xl border border-default p-5 mt-6">
          <div class="flex items-start justify-between mb-3 gap-3">
            <div class="min-w-0">
              <h3 class="text-sm font-medium text-default">模板文件</h3>
              <p class="text-xs text-muted mt-1 leading-relaxed">
                上传模板后，系统会在提示词末尾自动追加「### 相关模板位置信息」，以
                <code class="text-xs bg-canvas px-1 rounded">{名称}：{file_id}</code> 形式列出；
                数字员工可按 file_id 调用文档工具（word/excel/pdf_process 等）套用对应模板。点击模板名称可下载。
              </p>
            </div>
            <button
              class="flex-shrink-0 h-8 px-3 rounded-lg text-sm font-medium border border-primary-300 text-primary-600 hover:bg-primary-50 transition-colors disabled:opacity-50"
              :disabled="templateUploading"
              @click="showAddTemplate = !showAddTemplate"
            >
              {{ showAddTemplate ? '取消' : '+ 添加模板' }}
            </button>
          </div>

          <!-- 添加模板表单 -->
          <div v-if="showAddTemplate" class="mb-4 p-3 bg-canvas rounded-lg border border-default">
            <div class="flex flex-col gap-2">
              <div class="text-xs text-muted">模板名称 <span class="text-danger-500">*</span></div>
              <input
                v-model="newTemplateName"
                type="text"
                placeholder="如：标准报价单"
                class="w-full h-9 px-3 rounded-lg border border-default bg-surface text-sm text-default focus:border-primary-400 focus:ring-1 focus:ring-primary-400 outline-none"
                :disabled="templateUploading"
              />
              <div class="text-xs text-muted">模板文件 <span class="text-danger-500">*</span></div>
              <div class="flex gap-2">
                <button
                  class="flex-1 h-9 px-3 rounded-lg border border-dashed border-default text-sm text-muted hover:border-primary-300 hover:text-primary-600 transition-colors flex items-center justify-center gap-2 min-w-0"
                  @click="templateFileInput?.click()"
                >
                  <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.8">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M12 4v12m0-12l-4 4m4-4l4 4" />
                  </svg>
                  <span class="truncate">{{ selectedFile ? selectedFile.name : '选择模板文件' }}</span>
                </button>
                <button
                  class="flex-shrink-0 h-9 px-4 rounded-lg text-sm font-medium bg-primary-600 text-white hover:bg-primary-700 transition-colors disabled:opacity-50"
                  :disabled="templateUploading || !newTemplateName.trim() || !selectedFile"
                  @click="handleUploadTemplate"
                >
                  {{ templateUploading ? '上传中...' : '上传' }}
                </button>
              </div>
              <p class="text-xs text-muted">名称与文件均为必填，缺一不可；支持 .docx / .xlsx / .pptx / .pdf / .md / .txt / .csv</p>
              <input
                ref="templateFileInput"
                type="file"
                accept=".docx,.xlsx,.pptx,.pdf,.md,.txt,.csv"
                class="hidden"
                @change="handleFileSelect"
              />
            </div>
          </div>

          <!-- 模板列表 -->
          <div v-if="templatesLoading" class="py-4 text-center text-sm text-muted">加载中...</div>
          <div v-else-if="templates.length === 0" class="py-4 text-center text-sm text-muted">
            暂无模板文件，点击「添加模板」上传
          </div>
          <div v-else class="space-y-2">
            <div
              v-for="t in templates"
              :key="t.file_id"
              class="flex items-center gap-3 p-2.5 rounded-lg border border-default bg-canvas"
            >
              <svg class="w-5 h-5 flex-shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6">
                <path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              <div class="flex-1 min-w-0">
                <button
                  type="button"
                  class="block max-w-full text-sm text-primary-600 hover:text-primary-700 hover:underline truncate text-left disabled:opacity-50 disabled:no-underline"
                  :title="`下载 ${t.original_name || t.name}`"
                  :disabled="!!downloadingId"
                  @click="handleDownloadTemplate(t)"
                >
                  {{ downloadingId === t.file_id ? '下载中...' : t.name }}
                </button>
                <p class="text-xs text-muted truncate">
                  {{ t.original_name || '-' }} · {{ formatSize(t.size_bytes) }} · {{ t.file_id }}
                </p>
              </div>
              <button
                class="flex-shrink-0 h-7 px-2.5 rounded text-xs text-muted hover:text-danger-600 hover:bg-danger-50 transition-colors"
                :disabled="templateUploading"
                @click="handleDeleteTemplate(t.file_id)"
              >
                删除
              </button>
            </div>
          </div>
        </div>

        <!-- 错误提示 -->
        <div v-if="errorMsg" class="mt-4 bg-danger-50 border border-danger-200 rounded-lg p-3 text-sm text-danger-700">
          {{ errorMsg }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, inject, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import AppHeader from '@/components/AppHeader.vue'
import MyTextarea from '@/components/ui/MyTextarea.vue'
import { getExtraMd, saveExtraMd, deleteExtraMd } from '@/api/subagent'
import { listTemplates, uploadTemplate, deleteTemplate, getTemplateDownloadUrl, type TemplateFile } from '@/api/subagentTemplates'
import { resolveApiUrl, saveDownloadUrl } from '@/platform/urlResolver'
import { getAuthHeader } from '@/api/auth'
import { useTenantAuth } from '@/composables/useTenantAuth'

const router = useRouter()
const route = useRoute()

const subagentName = computed(() => String(route.params.subagent_name || ''))
const tenantId = computed(() => String(route.params.tenant_id || ''))

const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()

const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)
const effectiveUser = computed(() => tenantAdmin.value ? {
  user_id: tenantAdmin.value.user_id,
  username: tenantAdmin.value.username,
  phone: tenantAdmin.value.phone,
} : null)

// ============== extra_md 状态 ==============
const loading = ref(true)
const saving = ref(false)
const content = ref('')
const initialContent = ref('')
const lastVersion = ref<number | null>(null)
const lastSavedAt = ref('')
const errorMsg = ref('')

// ============== 模板文件状态 ==============
const templates = ref<TemplateFile[]>([])
const templatesLoading = ref(false)
const templateUploading = ref(false)
const showAddTemplate = ref(false)
const newTemplateName = ref('')
const selectedFile = ref<File | null>(null)
const templateFileInput = ref<HTMLInputElement | null>(null)
const downloadingId = ref('')

const ALLOWED_TEMPLATE_EXTS = ['.docx', '.xlsx', '.pptx', '.pdf', '.md', '.txt', '.csv']

const toggleSidebarFn = inject<() => void>('toggleSidebar', () => {})
function handleToggleSidebar() {
  toggleSidebarFn()
}

async function handleLogout() {
  await tenantLogout()
  router.push(`/t/${tenantId.value}/login`)
}

// 返回定制提示词选择页
function goBack() {
  router.push(`/t/${tenantId.value}/extras`)
}

const dirty = computed(() => content.value !== initialContent.value)

// 离开页面前提示保存（dirty 时）
watch(dirty, (val) => {
  if (val) {
    window.onbeforeunload = () => '有未保存的修改，确定离开吗？'
  } else {
    window.onbeforeunload = null
  }
})

// ============== extra_md 加载/保存/清空 ==============
async function loadContent() {
  loading.value = true
  errorMsg.value = ''
  try {
    const res = await getExtraMd(subagentName.value)
    content.value = res.content || ''
    initialContent.value = content.value
    lastVersion.value = res.version ?? null
  } catch (e: any) {
    errorMsg.value = e.message || '加载失败'
    content.value = ''
    initialContent.value = ''
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  if (!dirty.value || saving.value) return
  saving.value = true
  errorMsg.value = ''
  try {
    const res = await saveExtraMd(subagentName.value, content.value)
    if (res.success) {
      initialContent.value = content.value
      lastVersion.value = res.version ?? lastVersion.value
      lastSavedAt.value = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    } else {
      errorMsg.value = res.message || '保存失败'
    }
  } catch (e: any) {
    errorMsg.value = e.message || '保存失败'
  } finally {
    saving.value = false
  }
}

async function handleClear() {
  if (!confirm('确定清空定制提示词？清空后将恢复默认配置。')) return
  saving.value = true
  errorMsg.value = ''
  try {
    const res = await deleteExtraMd(subagentName.value)
    if (res.success) {
      content.value = ''
      initialContent.value = ''
      lastVersion.value = null
      lastSavedAt.value = ''
    } else {
      errorMsg.value = res.message || '清空失败'
    }
  } catch (e: any) {
    errorMsg.value = e.message || '清空失败'
  } finally {
    saving.value = false
  }
}

// ============== 模板文件 加载/上传/删除 ==============
function formatSize(bytes?: number): string {
  if (!bytes) return '-'
  if (bytes < 1024) return bytes + 'B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + 'KB'
  return (bytes / 1024 / 1024).toFixed(1) + 'MB'
}

async function loadTemplates() {
  templatesLoading.value = true
  try {
    const res = await listTemplates(subagentName.value)
    templates.value = res.data || []
  } catch {
    // 静默失败，不阻塞 extra_md 编辑
    templates.value = []
  } finally {
    templatesLoading.value = false
  }
}

function handleFileSelect(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  // 重置 input value 允许重复选择同一文件
  input.value = ''
  if (!file) return
  const dotIdx = file.name.lastIndexOf('.')
  const ext = dotIdx >= 0 ? file.name.slice(dotIdx).toLowerCase() : ''
  if (!ALLOWED_TEMPLATE_EXTS.includes(ext)) {
    alert(`不支持的文件格式（仅支持 ${ALLOWED_TEMPLATE_EXTS.join(' / ')}）`)
    return
  }
  selectedFile.value = file
}

async function handleUploadTemplate() {
  const name = newTemplateName.value.trim()
  if (!name || !selectedFile.value || templateUploading.value) return
  templateUploading.value = true
  errorMsg.value = ''
  try {
    const res = await uploadTemplate(subagentName.value, name, selectedFile.value)
    if (res.success) {
      templates.value = res.data || []
      newTemplateName.value = ''
      selectedFile.value = null
      showAddTemplate.value = false
    } else {
      errorMsg.value = res.error || '上传失败'
    }
  } catch (e: any) {
    errorMsg.value = e.message || '上传失败'
  } finally {
    templateUploading.value = false
  }
}

async function handleDeleteTemplate(fileId: string) {
  if (!confirm('确定删除该模板？')) return
  errorMsg.value = ''
  try {
    const res = await deleteTemplate(subagentName.value, fileId)
    if (res.success) {
      templates.value = res.data || []
    } else {
      errorMsg.value = res.error || '删除失败'
    }
  } catch (e: any) {
    errorMsg.value = e.message || '删除失败'
  }
}

// 点击模板名称下载：桌面端走受控保存，Web 端 fetch blob 触发浏览器下载
async function handleDownloadTemplate(t: TemplateFile) {
  if (downloadingId.value) return
  downloadingId.value = t.file_id
  try {
    const url = resolveApiUrl(getTemplateDownloadUrl(t.file_id))
    const fileName = t.original_name || t.name
    if (window.agentDesktop) {
      await saveDownloadUrl(url, fileName, getAuthHeader())
      return
    }
    const response = await fetch(url)
    if (!response.ok) throw new Error(`下载失败: ${response.status}`)
    const blob = await response.blob()
    const blobUrl = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = blobUrl
    a.download = fileName
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    setTimeout(() => URL.revokeObjectURL(blobUrl), 1000)
  } catch (e: any) {
    console.error('模板下载失败:', e)
    errorMsg.value = e.message || '模板下载失败'
  } finally {
    downloadingId.value = ''
  }
}

onMounted(() => {
  if (!subagentName.value) {
    router.push(`/t/${tenantId.value}/extras`)
    return
  }
  // extra_md 与模板列表并行加载，互不阻塞
  loadContent()
  loadTemplates()
})
</script>
