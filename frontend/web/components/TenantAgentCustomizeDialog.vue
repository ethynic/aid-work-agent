<template>
  <BaseModal v-model="visible" :title="`定制 - ${displayName}`" size="xl">
    <div class="space-y-5">
      <!-- 基础信息 -->
      <section>
        <h4 class="text-sm font-semibold text-default mb-2">基础信息</h4>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label class="text-sm text-muted mb-1 block">显示名（留空继承父定义）</label>
            <BaseInput v-model="form.display_name" placeholder="留空则使用默认名称" :disabled="saving" />
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">简介描述（留空继承父定义）</label>
            <BaseInput v-model="form.description" placeholder="留空则使用默认描述" :disabled="saving" />
          </div>
        </div>
      </section>

      <!-- 扩展提示词 -->
      <section>
        <h4 class="text-sm font-semibold text-default mb-2">扩展提示词</h4>
        <p class="text-xs text-muted mb-2 leading-relaxed">
          追加到该数字员工系统提示词末尾的本租户定制需求，留空使用默认配置。
        </p>
        <MyTextarea
          v-model="form.extra_prompt"
          :rows="6"
          :min-height="'140px'"
          :auto-resize="false"
          monospace
          show-char-count
          enable-preview
          :disabled="saving"
          placeholder="例如：&#10;- 回复中必须使用「贵司」而非「你」&#10;- 所有报价保留两位小数"
        />
      </section>

      <!-- 模板文件 -->
      <section>
        <div class="flex items-start justify-between gap-3 mb-2">
          <div class="min-w-0">
            <h4 class="text-sm font-semibold text-default">模板文件</h4>
            <p class="text-xs text-muted mt-1 leading-relaxed">
              上传后系统会在提示词末尾自动追加「相关模板位置信息」，数字员工可按 file_id 调用文档工具套用模板；点击模板名称可下载。
            </p>
          </div>
          <BaseButton
            intent="secondary"
            size="sm"
            class="flex-shrink-0"
            :disabled="templateUploading"
            @click="showAddTemplate = !showAddTemplate"
          >
            {{ showAddTemplate ? '取消' : '+ 添加模板' }}
          </BaseButton>
        </div>

        <!-- 添加模板表单 -->
        <div v-if="showAddTemplate" class="mb-3 p-3 bg-canvas rounded-lg border border-default space-y-3">
          <div>
            <label class="text-sm text-muted mb-1 block">模板名称 <span class="text-danger-500">*</span></label>
            <BaseInput v-model="newTemplateName" placeholder="如：标准报价单" :disabled="templateUploading" />
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">模板文件 <span class="text-danger-500">*</span></label>
            <div class="flex gap-2">
              <button
                type="button"
                class="flex-1 h-10 px-3 rounded-lg border border-dashed border-default text-sm text-muted hover:border-primary-300 hover:text-primary-600 transition-colors flex items-center justify-center gap-2 min-w-0 disabled:opacity-50"
                :disabled="templateUploading"
                @click="templateFileInput?.click()"
              >
                <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.8">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M12 4v12m0-12l-4 4m4-4l4 4" />
                </svg>
                <span class="truncate">{{ selectedFile ? selectedFile.name : '选择模板文件' }}</span>
              </button>
              <BaseButton
                :disabled="templateUploading || !newTemplateName.trim() || !selectedFile"
                @click="handleUploadTemplate"
              >
                {{ templateUploading ? '上传中...' : '上传' }}
              </BaseButton>
            </div>
            <p class="text-xs text-muted mt-1">名称与文件均为必填；支持 .docx / .xlsx / .pptx / .pdf / .md / .txt / .csv</p>
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
        <div v-if="templatesLoading" class="py-3 text-center text-xs text-muted">加载中...</div>
        <div v-else-if="templates.length === 0" class="py-3 text-center text-xs text-muted">暂无模板文件，点击右上角「+ 添加模板」上传</div>
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
            <BaseButton
              intent="danger-ghost"
              size="sm"
              class="flex-shrink-0"
              :disabled="templateUploading"
              @click="handleDeleteTemplate(t.file_id)"
            >
              删除
            </BaseButton>
          </div>
        </div>
      </section>

      <!-- 知识库栏目 -->
      <section>
        <h4 class="text-sm font-semibold text-default mb-2">知识库栏目</h4>
        <p class="text-xs text-muted mb-2 leading-relaxed">
          该数字员工可检索的知识库栏目与共享来源，统一在知识库页面维护。
        </p>
        <BaseButton intent="secondary" size="sm" @click="openKnowledgePage">
          跳转知识库
        </BaseButton>
      </section>

      <!-- 空态简介与快捷按钮 -->
      <section>
        <h4 class="text-sm font-semibold text-default mb-2">新会话简介与快捷按钮</h4>
        <p class="text-xs text-muted mb-2 leading-relaxed">
          覆盖该数字员工新会话页展示的能力摘要与快捷操作按钮；留空由 AI 自动生成。
        </p>
        <div class="mb-3">
          <label class="text-sm text-muted mb-1 block">能力摘要（留空 AI 生成）</label>
          <textarea
            v-model="form.greeting_summary"
            rows="2"
            maxlength="500"
            class="w-full px-3 py-2 rounded-lg border border-default bg-surface text-sm text-default focus:border-primary-400 focus:ring-1 focus:ring-primary-400 outline-none resize-y"
            :disabled="saving"
          />
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div v-for="(p, i) in greetingPrompts" :key="i" class="space-y-2">
            <label class="text-sm text-muted mb-0 block">快捷按钮 {{ i + 1 }}（留空不显示）</label>
            <BaseInput v-model="p.label" placeholder="按钮文案，≤6 字" maxlength="20" :disabled="saving" />
            <BaseInput v-model="p.message" placeholder="点击后发送的完整指令" maxlength="200" :disabled="saving" />
          </div>
        </div>
      </section>

      <!-- 错误提示 -->
      <div v-if="errorMsg" class="bg-danger-50 border border-danger-200 rounded-lg p-3 text-sm text-danger-700">
        {{ errorMsg }}
      </div>
    </div>

    <template #footer>
      <div class="w-full flex items-center gap-2">
        <!-- 副本实例操作：复制来源可为基础员工或本租户实例（克隆其覆盖项） -->
        <BaseButton intent="secondary" size="sm" :disabled="copying" @click="handleCopy">
          {{ copying ? '复制中...' : '复制为副本' }}
        </BaseButton>
        <BaseInput v-model="copyName" placeholder="副本显示名" size="sm" class="w-40" :disabled="copying" />
        <BaseButton
          v-if="isCopy"
          intent="danger-ghost"
          size="sm"
          :disabled="deleting"
          @click="handleDelete"
        >
          {{ deleting ? '删除中...' : '删除副本' }}
        </BaseButton>
        <div class="flex-1" />
        <BaseButton intent="secondary" @click="handleClose">取消</BaseButton>
        <BaseButton :disabled="saving" @click="handleSave">
          {{ saving ? '保存中...' : '保存' }}
        </BaseButton>
      </div>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import MyTextarea from '@/components/ui/MyTextarea.vue'
import {
  getTenantAgentInstanceOrDefault,
  updateTenantAgentInstance,
  createTenantAgentInstance,
  deleteTenantAgentInstance,
  type TenantAgentInstance,
  type GreetingPrompt,
} from '@/api/tenantAgentInstances'
import {
  listTemplates,
  uploadTemplate,
  deleteTemplate,
  getTemplateDownloadUrl,
  type TemplateFile,
} from '@/api/subagentTemplates'
import { resolveApiUrl, saveDownloadUrl } from '@/platform/urlResolver'
import { getAuthHeader } from '@/api/auth'
import { triggerNativeDownload } from '@/utils/download'

const props = defineProps<{
  modelValue: boolean
  tenantId: string
  /** 目标 agent_id：基础员工 id 或副本实例 id */
  agentId: string
  /** 卡片展示名（无覆盖时即父定义名） */
  agentName: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  /** 保存/复制/删除成功后通知父页面刷新列表 */
  changed: []
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const displayName = computed(() => form.value.display_name || props.agentName)
const isCopy = ref(false)
const saving = ref(false)
const copying = ref(false)
const deleting = ref(false)
const errorMsg = ref('')

const form = ref({
  display_name: '',
  description: '',
  extra_prompt: '',
  greeting_summary: '',
})
const greetingPrompts = ref<GreetingPrompt[]>([
  { label: '', message: '' },
  { label: '', message: '' },
])
const copyName = ref('')

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

const router = useRouter()

function openKnowledgePage() {
  const { href } = router.resolve(`/t/${props.tenantId}/knowledge`)
  window.open(href, '_blank')
}

async function loadInstance() {
  errorMsg.value = ''
  const inst: TenantAgentInstance | null = await getTenantAgentInstanceOrDefault(
    props.tenantId, props.agentId,
  )
  isCopy.value = inst ? inst.is_copy : isLikelyCopy()
  form.value = {
    display_name: inst?.display_name || '',
    description: inst?.description || '',
    extra_prompt: inst?.extra_prompt || '',
    greeting_summary: inst?.greeting_summary || '',
  }
  const gp = inst?.greeting_prompts
  greetingPrompts.value = [0, 1].map(i => ({
    label: gp?.[i]?.label || '',
    message: gp?.[i]?.message || '',
  }))
}

// 实例行未落库时（基础员工未定制过）按 id 猜测副本实例标记仅作展示兜底；
// 后端权威判定以保存后返回为准
function isLikelyCopy(): boolean {
  return props.agentId !== props.agentName && /-[a-z0-9-]{2,}$/.test(props.agentId)
}

// ============== 模板文件 加载/上传/删除/下载 ==============
function formatSize(bytes?: number): string {
  if (!bytes) return '-'
  if (bytes < 1024) return bytes + 'B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + 'KB'
  return (bytes / 1024 / 1024).toFixed(1) + 'MB'
}

async function loadTemplates() {
  templatesLoading.value = true
  try {
    const res = await listTemplates(props.agentId)
    templates.value = res.data || []
  } catch {
    // 静默失败，不阻塞定制表单
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
    errorMsg.value = `不支持的文件格式（仅支持 ${ALLOWED_TEMPLATE_EXTS.join(' / ')}）`
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
    const res = await uploadTemplate(props.agentId, name, selectedFile.value)
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
    const res = await deleteTemplate(props.agentId, fileId)
    if (res.success) {
      templates.value = res.data || []
    } else {
      errorMsg.value = res.error || '删除失败'
    }
  } catch (e: any) {
    errorMsg.value = e.message || '删除失败'
  }
}

// 点击模板名称下载：桌面端走受控保存，Web 端直链原生下载
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
    triggerNativeDownload(url, fileName)
  } catch (e: any) {
    errorMsg.value = e.message || '模板下载失败'
  } finally {
    downloadingId.value = ''
  }
}

async function handleSave() {
  if (saving.value) return
  saving.value = true
  errorMsg.value = ''
  try {
    const prompts: GreetingPrompt[] = greetingPrompts.value
      .map(p => ({ label: p.label.trim(), message: p.message.trim() }))
      .filter(p => p.label && p.message)

    await updateTenantAgentInstance(props.agentId, {
      // 空串=清除覆盖恢复继承；null=不改（后端契约）
      display_name: form.value.display_name.trim(),
      description: form.value.description.trim(),
      extra_prompt: form.value.extra_prompt,
      greeting_summary: form.value.greeting_summary.trim(),
      greeting_prompts: prompts,
    })
    emit('changed')
    visible.value = false
  } catch (e: any) {
    errorMsg.value = e.message || '保存失败'
  } finally {
    saving.value = false
  }
}

async function handleCopy() {
  const name = copyName.value.trim()
  if (!name) {
    errorMsg.value = '请填写副本显示名'
    return
  }
  copying.value = true
  errorMsg.value = ''
  try {
    await createTenantAgentInstance(props.agentId, name)
    emit('changed')
    visible.value = false
  } catch (e: any) {
    errorMsg.value = e.message || '复制失败'
  } finally {
    copying.value = false
  }
}

async function handleDelete() {
  if (!confirm('确定删除该副本？删除后新会话无法继续使用。')) return
  deleting.value = true
  errorMsg.value = ''
  try {
    await deleteTenantAgentInstance(props.agentId)
    emit('changed')
    visible.value = false
  } catch (e: any) {
    errorMsg.value = e.message || '删除失败'
  } finally {
    deleting.value = false
  }
}

function handleClose() {
  visible.value = false
}

/** 清空表单：关闭弹框时调用，避免下次打开先闪一下上一个员工的覆盖项 */
function resetForm() {
  form.value = {
    display_name: '',
    description: '',
    extra_prompt: '',
    greeting_summary: '',
  }
  greetingPrompts.value = [
    { label: '', message: '' },
    { label: '', message: '' },
  ]
}

watch(visible, (v) => {
  if (v) {
    copyName.value = ''
    errorMsg.value = ''
    showAddTemplate.value = false
    newTemplateName.value = ''
    selectedFile.value = null
    templates.value = []
    loadInstance()
    loadTemplates()
  } else {
    resetForm()
  }
})
</script>
