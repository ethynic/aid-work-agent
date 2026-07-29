<template>
  <BaseModal
    v-model="show"
    title="报告推送配置"
    size="lg"
  >
    <div class="space-y-6">
      <!-- 顶部提示 -->
      <div class="px-4 py-2 rounded-lg bg-warning-50 border border-warning-200 text-warning-700 text-xs">
        生成日报、周报、月报会消耗少量积分。
      </div>

      <div v-if="loading" class="text-center py-8 text-muted">加载中...</div>

      <div v-else-if="prefs">
        <!-- 个人报告配置 -->
        <div class="space-y-3">
          <div class="flex items-center justify-between">
            <div>
              <div class="text-sm font-medium text-default">个人报告推送</div>
              <div class="text-xs text-muted">关闭后将不再接收个人日报/周报/月报推送</div>
            </div>
            <label class="inline-flex items-center cursor-pointer">
              <input type="checkbox" v-model="prefs.personal_report_enabled" class="sr-only peer" />
              <div class="w-11 h-6 bg-surface-hover peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:start-[2px] after:bg-white after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary-600 relative"></div>
            </label>
          </div>

          <div class="pl-4 border-l-2 border-default space-y-3">
            <div>
              <label class="block text-sm text-muted mb-1">订阅类型（可复选）</label>
              <div class="flex gap-2">
                <label
                  v-for="t in REPORT_TYPES"
                  :key="t.value"
                  class="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border cursor-pointer"
                  :class="prefs.personal_report_types.includes(t.value)
                    ? 'border-primary-400 bg-primary-50 text-primary-700'
                    : 'border-default text-default'"
                >
                  <input
                    type="checkbox"
                    :value="t.value"
                    v-model="prefs.personal_report_types"
                  />
                  <span class="text-sm">{{ t.label }}</span>
                </label>
              </div>
            </div>

            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="block text-sm text-muted mb-1">推送渠道</label>
                <div class="flex gap-2">
                  <label
                    v-for="ch in PUSH_CHANNELS"
                    :key="ch.value"
                    class="inline-flex items-center gap-1 text-sm"
                  >
                    <input
                      type="checkbox"
                      :value="ch.value"
                      v-model="prefs.personal_push_channels"
                    />
                    {{ ch.label }}
                  </label>
                </div>
              </div>
              <div>
                <label class="block text-sm text-muted mb-1">推送时间</label>
                <input
                  v-model="prefs.personal_push_time"
                  type="time"
                  class="w-full px-3 py-2 bg-surface border border-default rounded-lg text-default text-sm"
                />
              </div>
            </div>
          </div>
        </div>

        <!-- 团队报告配置（仅管理员可见） -->
        <div v-if="isAdmin" class="pt-4 border-t border-default space-y-3">
          <div class="flex items-center justify-between">
            <div>
              <div class="text-sm font-medium text-default">团队报告推送</div>
              <div class="text-xs text-muted">仅管理员可配置；关闭后将不再接收团队日报/周报/月报推送</div>
            </div>
            <label class="inline-flex items-center cursor-pointer">
              <input type="checkbox" v-model="prefs.team_report_enabled" class="sr-only peer" />
              <div class="w-11 h-6 bg-surface-hover peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:start-[2px] after:bg-white after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary-600 relative"></div>
            </label>
          </div>

          <div class="pl-4 border-l-2 border-default space-y-3">
            <div>
              <label class="block text-sm text-muted mb-1">订阅类型（可复选）</label>
              <div class="flex gap-2">
                <label
                  v-for="t in REPORT_TYPES"
                  :key="t.value"
                  class="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border cursor-pointer"
                  :class="prefs.team_report_types.includes(t.value)
                    ? 'border-primary-400 bg-primary-50 text-primary-700'
                    : 'border-default text-default'"
                >
                  <input
                    type="checkbox"
                    :value="t.value"
                    v-model="prefs.team_report_types"
                  />
                  <span class="text-sm">{{ t.label }}</span>
                </label>
              </div>
            </div>

            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="block text-sm text-muted mb-1">推送渠道</label>
                <div class="flex gap-2">
                  <label
                    v-for="ch in PUSH_CHANNELS"
                    :key="ch.value"
                    class="inline-flex items-center gap-1 text-sm"
                  >
                    <input
                      type="checkbox"
                      :value="ch.value"
                      v-model="prefs.team_push_channels"
                    />
                    {{ ch.label }}
                  </label>
                </div>
              </div>
              <div>
                <label class="block text-sm text-muted mb-1">推送时间</label>
                <input
                  v-model="prefs.team_push_time"
                  type="time"
                  class="w-full px-3 py-2 bg-surface border border-default rounded-lg text-default text-sm"
                />
              </div>
            </div>
          </div>
        </div>
      </div>

      <div v-if="errorMessage" class="px-4 py-2 rounded-lg bg-danger-50 border border-danger-200 text-danger-600 text-sm">
        {{ errorMessage }}
      </div>
    </div>

    <template #footer>
      <BaseButton intent="secondary" @click="show = false">取消</BaseButton>
      <BaseButton @click="handleSave" :disabled="saving">{{ saving ? '保存中...' : '保存' }}</BaseButton>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import {
  getPreferences,
  updatePreferences,
  type ReportPreferences,
  type ReportType,
} from '@/api/workReports'
import { useTenantAuth } from '@/composables/useTenantAuth'

const emit = defineEmits<{ close: [] }>()

const { admin: tenantAdmin } = useTenantAuth()
const isAdmin = computed(() => {
  const role = tenantAdmin.value?.role
  return role === 'tenant_admin' || role === 'platform_admin'
})

const REPORT_TYPES: Array<{ value: ReportType; label: string }> = [
  { value: 'daily', label: '日报' },
  { value: 'weekly', label: '周报' },
  { value: 'monthly', label: '月报' },
]

const PUSH_CHANNELS: Array<{ value: string; label: string }> = [
  { value: 'in_app', label: '站内' },
  { value: 'wecom', label: '企业微信' },
  { value: 'email', label: '邮件' },
]

const show = ref(true)
const loading = ref(false)
const saving = ref(false)
const prefs = ref<ReportPreferences | null>(null)
const errorMessage = ref('')

watch(show, (val) => {
  if (!val) emit('close')
})

async function loadPreferences() {
  loading.value = true
  errorMessage.value = ''
  try {
    const res = await getPreferences()
    prefs.value = res.data
  } catch (e) {
    errorMessage.value = e instanceof Error ? e.message : '加载推送配置失败'
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  if (!prefs.value) return
  saving.value = true
  errorMessage.value = ''
  try {
    await updatePreferences({
      personal_report_enabled: prefs.value.personal_report_enabled,
      personal_report_types: prefs.value.personal_report_types,
      personal_push_channels: prefs.value.personal_push_channels,
      personal_push_time: prefs.value.personal_push_time,
      team_report_enabled: prefs.value.team_report_enabled,
      team_report_types: prefs.value.team_report_types,
      team_push_channels: prefs.value.team_push_channels,
      team_push_time: prefs.value.team_push_time,
    })
    show.value = false
  } catch (e) {
    errorMessage.value = e instanceof Error ? e.message : '保存推送配置失败'
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  loadPreferences()
})
</script>
