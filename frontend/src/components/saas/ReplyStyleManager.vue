<template>
  <div class="page-container bg-canvas">
    <AppHeader
      title="回复风格"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    />

    <div class="page-content p-6">
      <!-- 操作栏 -->
      <div class="page-toolbar">
        <div class="page-toolbar-left">
          <BaseInput
            v-model="searchQuery"
            placeholder="搜索风格名称..."
            size="sm"
            class="w-80"
            @keyup.enter="handleClientSearch"
          />
          <BaseButton size="sm" @click="handleClientSearch">搜索</BaseButton>
        </div>
        <div class="page-toolbar-right">
          <BaseButton @click="openCreate">新增风格</BaseButton>
        </div>
      </div>

      <!-- 风格列表 -->
      <div class="table-scroll-wrapper">
        <BaseTable :columns="columns" :data="pagedStyles" row-key="style_id">
        <template #col-index="{ index }">
          <span class="text-muted text-xs">{{ seqNumber(index) }}</span>
        </template>
        <template #col-name="{ row }">
          <div>
            <span class="text-default font-medium cursor-pointer hover:text-primary-600" @click="row.is_system ? openView(row) : openEdit(row)">{{ row.name }}</span>
            <BaseBadge v-if="row.is_system" intent="info" class="ml-2">系统</BaseBadge>
          </div>
        </template>
        <template #col-description="{ row }">
          <span class="text-muted text-sm">{{ row.description || '-' }}</span>
        </template>
        <template #col-version="{ row }">
          <span class="text-muted text-sm">v{{ row.version }}</span>
          <BaseButton intent="ghost" size="sm" class="ml-1 text-xs" @click.stop="openVersions(row)">版本</BaseButton>
        </template>
        <template #col-actions="{ row }">
          <div class="flex items-center justify-center gap-1 whitespace-nowrap">
            <BaseButton intent="ghost" size="sm" class="text-xs" @click="openEdit(row)">编辑</BaseButton>
            <BaseButton v-if="!row.is_system" intent="danger-ghost" size="sm" class="text-xs" @click.stop="handleDelete(row)">删除</BaseButton>
          </div>
        </template>
        <template #empty>
          <div class="text-center py-8 text-muted">暂无回复风格</div>
        </template>
      </BaseTable>
      </div>

      <!-- 分页 -->
      <div v-if="total > 0" class="mt-4 flex justify-center">
        <BasePagination
          :total="total"
          v-model:current-page="currentPage"
          :page-size="pageSize"
        />
      </div>
    </div>

    <!-- 新增/编辑弹窗 -->
    <BaseModal v-model="showEditor" :title="editorMode === 'create' ? '新增回复风格' : editorMode === 'edit' ? '编辑回复风格' : '查看回复风格'" size="lg">
      <div class="space-y-4">
        <div>
          <label class="text-sm text-muted mb-1 block">风格标识 <span class="text-danger-500">*</span></label>
          <BaseInput
            v-model="editorForm.style_id"
            :disabled="editorMode !== 'create'"
            placeholder="如 professional（创建后不可修改）"
          />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">风格名称 <span class="text-danger-500">*</span></label>
          <BaseInput
            v-model="editorForm.name"
            :disabled="editorMode === 'view'"
            placeholder="如 专业助手"
          />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">风格描述</label>
          <BaseInput
            v-model="editorForm.description"
            :disabled="editorMode === 'view'"
            placeholder="简短描述风格特点"
          />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">风格内容 <span class="text-danger-500">*</span></label>
          <textarea
            v-model="editorForm.content"
            :disabled="editorMode === 'view'"
            class="w-full px-3 py-2 bg-surface-hover border border-default rounded-lg text-default focus:outline-none focus:border-primary-400 font-mono text-sm"
            rows="28"
            placeholder="## 回复风格指南&#10;&#10;..."
          ></textarea>
        </div>
      </div>
      <template v-if="editorMode !== 'view'" #footer>
        <BaseButton intent="secondary" @click="showEditor = false">取消</BaseButton>
        <BaseButton @click="handleSave" :disabled="saving">{{ saving ? '保存中...' : '保存' }}</BaseButton>
      </template>
      <template v-else #footer>
        <BaseButton intent="secondary" @click="showEditor = false">关闭</BaseButton>
      </template>
    </BaseModal>

    <!-- 版本历史弹窗 -->
    <BaseModal v-model="showVersions" :title="`版本历史：${versionsStyleName}`" size="lg">
      <div class="space-y-2">
        <div
          v-for="v in versions"
          :key="v.version"
          class="flex items-center justify-between p-3 bg-surface-hover rounded-lg"
        >
          <div class="flex items-center gap-3">
            <span class="font-mono text-sm text-default">{{ formatTimeSec(v.created_at) }}</span>
            <BaseBadge v-if="v.is_active" intent="success">当前</BaseBadge>
          </div>
          <div class="flex items-center gap-2">
            <BaseButton intent="ghost" size="sm" @click="openViewVersion(v)">查看</BaseButton>
            <BaseButton
              v-if="!v.is_active && !versionsIsSystem"
              intent="ghost"
              size="sm"
              @click="handleActivate(v.version)"
            >激活</BaseButton>
          </div>
        </div>
        <div v-if="versions.length === 0" class="text-center py-4 text-muted">暂无版本记录</div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showVersions = false">关闭</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, inject } from 'vue'
import { useToast } from 'vue-toastification'
import { usePageContext } from '@/composables/usePageContext'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import AppHeader from '@/components/AppHeader.vue'
import {
  listStyles, getStyle, createStyle, updateStyle, deleteStyle,
  listVersions, activateVersion,
  type ReplyStyleVersion,
} from '@/api/replyStyle'

const toast = useToast()

// 侧边栏控制
const toggleSidebarFn = inject<() => void>('toggleSidebar')
const effectiveIsLoggedIn = inject('effectiveIsLoggedIn', ref(true))
const effectiveUser = inject('effectiveUser', ref<{ username: string; user_id?: string | number } | null>(null))

function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}
function handleLogout() {}

// 数据
const styles = ref<any[]>([])
const searchQuery = ref('')

const { currentPage, pageSize, seqNumber } = usePageContext(async () => {
  // 客户端分页，无需额外加载
})

const filteredStyles = computed(() => {
  if (!searchQuery.value) return styles.value
  const q = searchQuery.value.toLowerCase()
  return styles.value.filter((s: any) =>
    s.name?.toLowerCase().includes(q) || s.style_id?.toLowerCase().includes(q),
  )
})

const total = computed(() => filteredStyles.value.length)

const pagedStyles = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  const end = start + pageSize.value
  return filteredStyles.value.slice(start, end)
})

function handleClientSearch() {
  currentPage.value = 1
}

const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'name', label: '名称' },
  { key: 'description', label: '描述' },
  { key: 'version', label: '版本', width: '80px' },
  { key: 'actions', label: '操作', width: '120px', thAlign: 'center' },
]

// 编辑器
const showEditor = ref(false)
const editorMode = ref<'create' | 'edit' | 'view'>('create')
const editorForm = ref({ style_id: '', name: '', description: '', content: '' })
const saving = ref(false)
const editingStyleId = ref('')

// 版本历史
const showVersions = ref(false)
const versions = ref<ReplyStyleVersion[]>([])
const versionsStyleId = ref('')
const versionsStyleName = ref('')
const versionsIsSystem = ref(false)

async function loadStyles() {
  try {
    const res = await listStyles()
    styles.value = res.styles || []
  } catch (e: any) {
    toast.error(e.message || '加载风格列表失败')
  }
}

function openCreate() {
  editorMode.value = 'create'
  editorForm.value = { style_id: '', name: '', description: '', content: '' }
  editingStyleId.value = ''
  showEditor.value = true
}

async function openEdit(row: any) {
  try {
    const res = await getStyle(row.style_id)
    const s = res.style
    editorMode.value = 'edit'
    editorForm.value = {
      style_id: s.style_id,
      name: s.name,
      description: s.description || '',
      content: s.content,
    }
    editingStyleId.value = s.style_id
    showEditor.value = true
  } catch (e: any) {
    toast.error(e.message || '获取风格详情失败')
  }
}

async function openView(row: any) {
  try {
    const res = await getStyle(row.style_id)
    const s = res.style
    editorMode.value = 'view'
    editorForm.value = {
      style_id: s.style_id,
      name: s.name,
      description: s.description || '',
      content: s.content,
    }
    editingStyleId.value = s.style_id
    showEditor.value = true
  } catch (e: any) {
    toast.error(e.message || '获取风格详情失败')
  }
}

function openViewVersion(v: ReplyStyleVersion) {
  editorMode.value = 'view'
  editorForm.value = {
    style_id: versionsStyleId.value,
    name: v.name,
    description: '',
    content: v.content,
  }
  showVersions.value = false
  showEditor.value = true
}

async function handleSave() {
  const form = editorForm.value
  if (!form.style_id || !form.name || !form.content) {
    toast.error('请填写必填项')
    return
  }
  saving.value = true
  try {
    if (editorMode.value === 'create') {
      await createStyle({
        style_id: form.style_id,
        name: form.name,
        description: form.description || undefined,
        content: form.content,
      })
      toast.success('创建成功')
    } else {
      await updateStyle(editingStyleId.value, {
        name: form.name,
        description: form.description || undefined,
        content: form.content,
      })
      toast.success('更新成功')
    }
    showEditor.value = false
    await loadStyles()
  } catch (e: any) {
    toast.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function handleDelete(row: any) {
  if (!confirm(`确定删除风格"${row.name}"？所有版本将被删除。`)) return
  try {
    await deleteStyle(row.style_id)
    toast.success('删除成功')
    await loadStyles()
  } catch (e: any) {
    toast.error(e.message || '删除失败')
  }
}

async function openVersions(row: any) {
  versionsStyleId.value = row.style_id
  versionsStyleName.value = row.name
  versionsIsSystem.value = row.is_system
  try {
    const res = await listVersions(row.style_id)
    versions.value = res.versions || []
    showVersions.value = true
  } catch (e: any) {
    toast.error(e.message || '获取版本历史失败')
  }
}

async function handleActivate(version: number) {
  if (!confirm(`确定激活 v${version}？当前版本将被替换。`)) return
  try {
    await activateVersion(versionsStyleId.value, version)
    toast.success('已激活')
    // 刷新版本列表
    const res = await listVersions(versionsStyleId.value)
    versions.value = res.versions || []
    await loadStyles()
  } catch (e: any) {
    toast.error(e.message || '激活失败')
  }
}

function formatTimeSec(t: string | null): string {
  if (!t) return '-'
  const d = new Date(t)
  if (isNaN(d.getTime())) return t
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}

onMounted(() => loadStyles())
</script>
