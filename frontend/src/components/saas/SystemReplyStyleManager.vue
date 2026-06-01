<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-default">系统回复风格</h1>
      <button
        @click="openCreate"
        class="px-4 py-2 bg-primary-500 hover:bg-primary-700 text-white rounded-lg text-sm font-medium transition-colors"
      >
        新增风格
      </button>
    </div>

    <p class="text-muted text-sm mb-4">系统内置风格对所有租户可见。租户可创建同名风格覆盖。</p>

    <!-- 加载状态 -->
    <div v-if="loading" class="text-center py-12 text-muted">加载中...</div>

    <!-- 风格列表 -->
    <div v-else-if="styles.length > 0" class="bg-white rounded-xl shadow-sm border border-default overflow-hidden">
      <table class="w-full">
        <thead class="bg-canvas border-b border-default">
          <tr>
            <th class="px-4 py-3 text-left text-xs font-medium text-muted uppercase w-16">序号</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-muted uppercase">名称</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-muted uppercase">描述</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-muted uppercase w-20">版本</th>
            <th class="px-4 py-3 text-left text-xs font-medium text-muted uppercase w-64">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-default">
          <tr v-for="(s, idx) in styles" :key="s.style_id" class="hover:bg-surface-hover">
            <td class="px-4 py-3 text-sm text-muted">{{ idx + 1 }}</td>
            <td class="px-4 py-3 text-sm text-default font-medium">{{ s.name }}</td>
            <td class="px-4 py-3 text-sm text-muted">{{ s.description || '-' }}</td>
            <td class="px-4 py-3 text-sm text-muted">v{{ s.version }}</td>
            <td class="px-4 py-3">
              <div class="flex items-center gap-2">
                <button @click="openEdit(s)" class="text-xs px-2 py-1 bg-primary-100 text-primary-700 rounded hover:bg-primary-200 transition-colors">编辑</button>
                <button @click="openVersions(s)" class="text-xs px-2 py-1 bg-info-100 text-info-700 rounded hover:bg-info-200 transition-colors">版本</button>
                <button @click="handleDelete(s)" class="text-xs px-2 py-1 bg-danger-100 text-danger-700 rounded hover:bg-danger-200 transition-colors">删除</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-else class="text-center py-12 text-muted">暂无系统回复风格</div>

    <!-- 编辑弹窗 -->
    <div v-if="showEditor" class="fixed inset-0 z-50 flex items-center justify-center">
      <div class="absolute inset-0 bg-black/50" @click="showEditor = false"></div>
      <div class="relative bg-white rounded-xl shadow-2xl w-full max-w-2xl mx-4 p-6 max-h-[90vh] overflow-y-auto">
        <h3 class="text-lg font-bold text-default mb-4">{{ editorMode === 'create' ? '新增系统风格' : '编辑系统风格' }}</h3>
        <div class="space-y-4">
          <div>
            <label class="block text-sm text-default mb-1">风格标识 <span class="text-danger-500">*</span></label>
            <input v-model="editorForm.style_id" :disabled="editorMode !== 'create'"
              class="w-full px-3 py-2 bg-surface-hover border border-default rounded-lg text-default focus:outline-none focus:border-primary-400"
              placeholder="如 professional（创建后不可修改）" />
          </div>
          <div>
            <label class="block text-sm text-default mb-1">风格名称 <span class="text-danger-500">*</span></label>
            <input v-model="editorForm.name"
              class="w-full px-3 py-2 bg-surface-hover border border-default rounded-lg text-default focus:outline-none focus:border-primary-400"
              placeholder="如 专业助手" />
          </div>
          <div>
            <label class="block text-sm text-default mb-1">风格描述</label>
            <input v-model="editorForm.description"
              class="w-full px-3 py-2 bg-surface-hover border border-default rounded-lg text-default focus:outline-none focus:border-primary-400"
              placeholder="简短描述风格特点" />
          </div>
          <div>
            <label class="block text-sm text-default mb-1">风格内容 <span class="text-danger-500">*</span></label>
            <textarea v-model="editorForm.content"
              class="w-full px-3 py-2 bg-surface-hover border border-default rounded-lg text-default focus:outline-none focus:border-primary-400 font-mono text-sm"
              rows="28" placeholder="## 回复风格指南&#10;&#10;..."></textarea>
          </div>
        </div>
        <div class="flex gap-3 mt-6">
          <button @click="showEditor = false" class="flex-1 py-2 border border-default rounded-lg text-default hover:bg-surface-hover transition-colors">取消</button>
          <button @click="handleSave" :disabled="saving" class="flex-1 py-2 bg-primary-500 hover:bg-primary-700 disabled:bg-gray-300 text-white rounded-lg transition-colors">
            {{ saving ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 版本历史弹窗 -->
    <div v-if="showVersions" class="fixed inset-0 z-50 flex items-center justify-center">
      <div class="absolute inset-0 bg-black/50" @click="showVersions = false"></div>
      <div class="relative bg-white rounded-xl shadow-2xl w-full max-w-2xl mx-4 p-6 max-h-[90vh] overflow-y-auto">
        <h3 class="text-lg font-bold text-default mb-4">版本历史：{{ versionsStyleName }}</h3>
        <div class="space-y-2">
          <div v-for="v in versions" :key="v.version"
            class="flex items-center justify-between p-3 bg-surface-hover rounded-lg">
            <div class="flex items-center gap-3">
              <span class="font-mono text-sm text-default">{{ formatTimeSec(v.created_at) }}</span>
              <span v-if="v.is_active" class="text-xs px-2 py-0.5 rounded-full bg-success-100 text-success-700 font-medium">当前</span>
            </div>
            <div class="flex items-center gap-2">
              <button @click="openViewVersion(v)" class="text-xs px-2 py-1 bg-primary-100 text-primary-700 rounded hover:bg-primary-200 transition-colors">查看</button>
              <button v-if="!v.is_active" @click="handleActivate(v.version)"
                class="text-xs px-2 py-1 bg-success-100 text-success-700 rounded hover:bg-success-200 transition-colors">激活</button>
            </div>
          </div>
          <div v-if="versions.length === 0" class="text-center py-4 text-muted">暂无版本记录</div>
        </div>
        <div class="flex gap-3 mt-6">
          <button @click="showVersions = false" class="flex-1 py-2 border border-default rounded-lg text-default hover:bg-surface-hover transition-colors">关闭</button>
        </div>
      </div>
    </div>

    <!-- 查看内容弹窗 -->
    <div v-if="showViewer" class="fixed inset-0 z-50 flex items-center justify-center">
      <div class="absolute inset-0 bg-black/50" @click="showViewer = false"></div>
      <div class="relative bg-white rounded-xl shadow-2xl w-full max-w-2xl mx-4 p-6 max-h-[90vh] overflow-y-auto">
        <h3 class="text-lg font-bold text-default mb-4">查看风格内容</h3>
        <pre class="whitespace-pre-wrap text-sm text-default bg-surface-hover p-4 rounded-lg">{{ viewerContent }}</pre>
        <div class="flex gap-3 mt-6">
          <button @click="showViewer = false" class="flex-1 py-2 border border-default rounded-lg text-default hover:bg-surface-hover transition-colors">关闭</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useToast } from 'vue-toastification'
import {
  listSystemStyles, getSystemStyle, createSystemStyle, updateSystemStyle,
  deleteSystemStyle, listSystemVersions, activateSystemVersion,
  type ReplyStyleVersion,
} from '@/api/replyStyle'

const toast = useToast()

const loading = ref(false)
const styles = ref<any[]>([])

const showEditor = ref(false)
const editorMode = ref<'create' | 'edit'>('create')
const editorForm = ref({ style_id: '', name: '', description: '', content: '' })
const editingStyleId = ref('')
const saving = ref(false)

const showVersions = ref(false)
const versions = ref<ReplyStyleVersion[]>([])
const versionsStyleId = ref('')
const versionsStyleName = ref('')

const showViewer = ref(false)
const viewerContent = ref('')

async function loadStyles() {
  loading.value = true
  try {
    const res = await listSystemStyles()
    styles.value = res.styles || []
  } catch (e: any) {
    toast.error(e.message || '加载失败')
  } finally {
    loading.value = false
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
    const res = await getSystemStyle(row.style_id)
    const s = res.style
    editorMode.value = 'edit'
    editorForm.value = { style_id: s.style_id, name: s.name, description: s.description || '', content: s.content }
    editingStyleId.value = s.style_id
    showEditor.value = true
  } catch (e: any) {
    toast.error(e.message || '获取详情失败')
  }
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
      await createSystemStyle({ style_id: form.style_id, name: form.name, description: form.description || undefined, content: form.content })
      toast.success('创建成功')
    } else {
      await updateSystemStyle(editingStyleId.value, { name: form.name, description: form.description || undefined, content: form.content })
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
  if (!confirm(`确定删除系统风格"${row.name}"？所有版本将被删除，所有租户将无法使用此风格。`)) return
  try {
    await deleteSystemStyle(row.style_id)
    toast.success('删除成功')
    await loadStyles()
  } catch (e: any) {
    toast.error(e.message || '删除失败')
  }
}

async function openVersions(row: any) {
  versionsStyleId.value = row.style_id
  versionsStyleName.value = row.name
  try {
    const res = await listSystemVersions(row.style_id)
    versions.value = res.versions || []
    showVersions.value = true
  } catch (e: any) {
    toast.error(e.message || '获取版本历史失败')
  }
}

function openViewVersion(v: ReplyStyleVersion) {
  viewerContent.value = v.content
  showVersions.value = false
  showViewer.value = true
}

async function handleActivate(version: number) {
  if (!confirm('确定激活此版本？当前版本将被替换。')) return
  try {
    await activateSystemVersion(versionsStyleId.value, version)
    toast.success('已激活')
    const res = await listSystemVersions(versionsStyleId.value)
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
