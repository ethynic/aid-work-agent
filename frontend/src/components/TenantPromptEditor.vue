<template>
  <div class="h-screen flex flex-col bg-canvas">
    <AppHeader
      :title="`定制提示词 - ${subagentName}`"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    >
      <template #menu-items="{ closeMenu }">
        <button
          class="block w-full text-left px-4 py-2 text-sm text-default hover:bg-surface-hover"
          @click="goBack(closeMenu)"
        >
          返回定制提示词列表
        </button>
      </template>
    </AppHeader>

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
          <div class="flex items-center justify-between mb-3">
            <h3 class="text-sm font-medium text-default">Markdown 内容</h3>
            <div class="text-xs text-muted">
              <span v-if="lastVersion">当前版本 V{{ lastVersion }}</span>
              <span v-else>未配置</span>
            </div>
          </div>

          <textarea
            v-model="content"
            class="w-full h-96 p-3 rounded-lg border border-default bg-canvas text-sm font-mono text-default focus:border-primary-400 focus:ring-1 focus:ring-primary-400 outline-none resize-y"
            placeholder="例如：&#10;&#10;## 本租户定制要求&#10;- 回复中必须使用「贵司」而非「你」&#10;- 所有报价保留两位小数&#10;- 涉及合同条款时必须先确认法务审核"
            :disabled="saving"
          ></textarea>

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
import { getExtraMd, saveExtraMd, deleteExtraMd } from '@/api/subagent'
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

const loading = ref(true)
const saving = ref(false)
const content = ref('')
const initialContent = ref('')
const lastVersion = ref<number | null>(null)
const lastSavedAt = ref('')
const errorMsg = ref('')

const toggleSidebarFn = inject<() => void>('toggleSidebar', () => {})
function handleToggleSidebar() {
  toggleSidebarFn()
}

async function handleLogout() {
  await tenantLogout()
  router.push(`/t/${tenantId.value}/login`)
}

// 返回定制提示词选择页
function goBack(closeMenu?: () => void) {
  closeMenu?.()
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

onMounted(() => {
  if (!subagentName.value) {
    router.push(`/t/${tenantId.value}/extras`)
    return
  }
  loadContent()
})
</script>
