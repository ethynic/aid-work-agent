<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-slate-800">Skill 管理</h1>
      <button @click="openAdd"
        class="px-4 py-2 bg-cyan-500 hover:bg-cyan-600 text-white rounded-lg text-sm font-medium transition-colors">
        上传 Skill
      </button>
    </div>

    <div v-if="loading" class="text-center py-12 text-slate-500">加载中...</div>

    <!-- Skill 列表 -->
    <div v-else-if="skills.length > 0" class="space-y-3">
      <div v-for="skill in skills" :key="skill.name" class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
        <div class="flex items-center justify-between">
          <div>
            <h3 class="text-sm font-medium text-slate-800">{{ skill.name }}</h3>
            <p class="text-xs text-slate-500 mt-1">{{ skill.path }}</p>
            <p class="text-xs text-slate-400 mt-0.5">{{ skill.created_at }}</p>
          </div>
          <div class="flex items-center gap-2">
            <button @click="openEdit(skill)" class="text-xs px-2 py-1 bg-blue-100 text-blue-700 rounded hover:bg-blue-200 transition-colors">编辑</button>
            <button @click="handleDelete(skill.name)" class="text-xs px-2 py-1 bg-red-100 text-red-700 rounded hover:bg-red-200 transition-colors">删除</button>
          </div>
        </div>
      </div>
    </div>

    <div v-else class="text-center py-12 text-slate-500">
      <p class="text-lg mb-2">暂无自定义 Skill</p>
      <p class="text-sm">点击"上传 Skill"添加自定义技能包</p>
    </div>

    <!-- 编辑/上传弹窗 -->
    <div v-if="showForm" class="fixed inset-0 z-50 flex items-center justify-center">
      <div class="absolute inset-0 bg-black/50" @click="showForm = false"></div>
      <div class="relative bg-white rounded-xl shadow-2xl w-full max-w-2xl mx-4 p-6 max-h-[90vh] overflow-y-auto">
        <h3 class="text-lg font-bold text-slate-800 mb-4">{{ editingName ? '编辑 Skill' : '上传 Skill' }}</h3>
        <div class="space-y-4">
          <div v-if="!editingName">
            <label class="block text-sm text-slate-600 mb-1">Skill 名称</label>
            <input v-model="form.name" type="text" placeholder="例：custom-faq-1.0.0"
              class="w-full px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 focus:outline-none focus:border-cyan-400" />
          </div>
          <div>
            <label class="block text-sm text-slate-600 mb-1">SKILL.md 内容</label>
            <textarea v-model="form.content" rows="16" placeholder="---&#10;name: Skill 名称&#10;description: 描述&#10;---&#10;&#10;Skill 正文内容..."
              class="w-full px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 font-mono text-sm focus:outline-none focus:border-cyan-400 resize-y"></textarea>
          </div>
        </div>
        <div v-if="formError" class="mt-3 p-2 bg-red-50 border border-red-200 rounded text-red-600 text-sm">{{ formError }}</div>
        <div class="flex gap-3 mt-6">
          <button @click="showForm = false" class="flex-1 py-2 border border-slate-300 rounded-lg text-slate-600 hover:bg-slate-50 transition-colors">取消</button>
          <button @click="handleSubmit" :disabled="submitting" class="flex-1 py-2 bg-cyan-500 hover:bg-cyan-600 disabled:bg-slate-300 text-white rounded-lg transition-colors">
            {{ submitting ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useToast } from 'vue-toastification'
import { listSkills, uploadSkill, updateSkill, deleteSkill } from '@/api/saasTenant'

const toast = useToast()

const loading = ref(true)
const skills = ref<any[]>([])
const showForm = ref(false)
const submitting = ref(false)
const formError = ref('')
const editingName = ref<string | null>(null)

const form = ref({ name: '', content: '' })

function openAdd() {
  editingName.value = null
  form.value = { name: '', content: '' }
  formError.value = ''
  showForm.value = true
}

function openEdit(skill: any) {
  editingName.value = skill.name
  form.value = { name: skill.name, content: '' }
  formError.value = ''
  showForm.value = true
}

async function loadSkills() {
  loading.value = true
  try {
    const res = await listSkills()
    skills.value = res.skills || []
  } catch (e) {
    console.error('加载 Skill 列表失败:', e)
  } finally {
    loading.value = false
  }
}

async function handleSubmit() {
  if (!form.value.content) {
    formError.value = '请输入 SKILL.md 内容'
    return
  }
  submitting.value = true
  formError.value = ''
  try {
    if (editingName.value) {
      await updateSkill(editingName.value, { content: form.value.content })
    } else {
      if (!form.value.name) {
        formError.value = '请输入 Skill 名称'
        submitting.value = false
        return
      }
      await uploadSkill({ name: form.value.name, content: form.value.content })
    }
    showForm.value = false
    await loadSkills()
  } catch (e: any) {
    formError.value = e.message || '保存失败'
  } finally {
    submitting.value = false
  }
}

async function handleDelete(name: string) {
  if (!confirm(`确定要删除 Skill "${name}" 吗？`)) return
  try {
    await deleteSkill(name)
    await loadSkills()
  } catch (e: any) {
    toast.error(e.message || '删除失败')
  }
}

onMounted(() => loadSkills())
</script>
