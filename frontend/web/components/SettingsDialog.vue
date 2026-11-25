<template>
  <Teleport to="body">
    <div v-if="visible" class="fixed inset-0 z-50 flex items-center justify-center">
      <!-- Backdrop -->
      <div class="absolute inset-0 bg-black/50 backdrop-blur-sm" @click="$emit('close')"></div>

      <!-- Dialog -->
      <div class="relative bg-white rounded-2xl shadow-2xl w-full max-w-2xl mx-4 max-h-[85vh] flex flex-col overflow-hidden">
        <!-- Header -->
        <div class="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <h2 class="text-base font-semibold text-gray-800">设置</h2>
          <button @click="$emit('close')" class="p-1 text-gray-400 hover:text-gray-600 rounded transition-colors text-base">&times;</button>
        </div>

        <!-- Body -->
        <div class="flex-1 overflow-y-auto p-6">
          <div class="space-y-5">
            <!-- Avatar -->
            <div class="flex items-center gap-4">
              <div class="w-16 h-16 rounded-full bg-gray-200 flex items-center justify-center overflow-hidden flex-shrink-0">
                <img v-if="profileForm.avatar_url" :src="profileForm.avatar_url" alt="头像" class="w-full h-full object-cover" />
                <svg v-else class="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                </svg>
              </div>
              <div class="flex-1">
                <label class="block text-sm font-medium text-gray-700 mb-1">头像 URL</label>
                <input
                  v-model="profileForm.avatar_url"
                  type="text"
                  placeholder="输入头像图片链接"
                  class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
            </div>

            <!-- Display Name -->
            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">显示名称</label>
              <input
                v-model="profileForm.username"
                type="text"
                placeholder="请输入显示名称"
                class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              />
            </div>

            <!-- Phone (readonly) -->
            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">手机号码</label>
              <input
                :value="user?.phone || '未绑定'"
                type="text"
                readonly
                class="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-gray-50 text-gray-500"
              />
            </div>

            <!-- Save Button -->
            <div class="pt-3 flex justify-end">
              <button
                @click="saveProfile"
                :disabled="profileSaving"
                class="px-5 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {{ profileSaving ? '保存中...' : '保存' }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, reactive, watch } from 'vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { useToast } from 'vue-toastification'
import { updateProfile as apiUpdateProfile } from '@/api/settings'

const props = defineProps<{
  visible: boolean
}>()

defineEmits<{
  (e: 'close'): void
}>()

// 租户登录态的管理员信息（username/user_id/phone 供基础设置展示）
const { admin: user } = useTenantAuth()
const toast = useToast()

// ====== Basic Profile ======
const profileForm = reactive({
  username: '',
  avatar_url: '',
})
const profileSaving = ref(false)

watch(() => props.visible, (val) => {
  if (val && user.value) {
    profileForm.username = user.value.username || ''
    profileForm.avatar_url = (user.value as Record<string, any>).avatar_url || ''
  }
})

async function saveProfile() {
  if (profileSaving.value) return
  profileSaving.value = true
  try {
    const result = await apiUpdateProfile({
      username: profileForm.username,
      avatar_url: profileForm.avatar_url || undefined,
    })
    if (result.success && result.user) {
      // token 不变，就地更新登录态中的管理员信息
      if (user.value) {
        user.value = { ...user.value, username: result.user.username }
      }
      toast.success('资料更新成功')
    } else {
      toast.error(result.error || '更新失败')
    }
  } catch (e: any) {
    toast.error('更新失败: ' + e.message)
  } finally {
    profileSaving.value = false
  }
}
</script>
