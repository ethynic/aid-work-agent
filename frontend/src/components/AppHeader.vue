<template>
  <header class="flex-shrink-0 h-14 bg-white border-b border-gray-200 flex items-center px-4">
    <div class="flex-1 flex items-center gap-3 min-w-0">
      <!-- Toggle Sidebar Button -->
      <button
        @click="$emit('toggle-sidebar')"
        class="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors flex-shrink-0"
        title="切换侧边栏"
      >
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h7" />
        </svg>
      </button>

      <!-- Page Title -->
      <div class="flex items-center gap-2 min-w-0">
        <h1 class="text-sm font-bold text-gray-800 truncate">
          <slot name="title">{{ title }}</slot>
        </h1>
      </div>
    </div>

    <!-- Right Side - User Info & Actions -->
    <div class="flex items-center gap-3 flex-shrink-0">
      <!-- User Name -->
      <div v-if="isLoggedIn" class="flex items-center gap-2">
        <span class="text-sm text-gray-600">{{ user?.username }}</span>
        <button
          @click="$emit('logout')"
          class="px-2 py-1 text-sm text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded transition-colors"
        >
          退出
        </button>
      </div>

      <!-- Online Status -->
      <div class="flex items-center gap-1.5 px-2 py-1 rounded-full bg-gray-100">
        <span :class="isOnline ? 'bg-success-500' : 'bg-gray-400'" class="w-1.5 h-1.5 rounded-full"></span>
        <span class="text-sm text-gray-500">{{ isOnline ? '在线' : '离线' }}</span>
      </div>

      <!-- More Menu -->
      <div class="relative">
        <button
          @click="showMenuDropdown = !showMenuDropdown"
          class="px-2 py-1.5 text-sm text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
        >
          更多
        </button>

        <!-- Dropdown Menu -->
        <div
          v-if="showMenuDropdown"
          class="absolute right-0 top-full mt-1 w-44 bg-white border border-gray-200 rounded-lg shadow-lg py-1 z-50"
        >
          <slot name="menu-items" :close-menu="closeMenu">
            <slot name="extra-menu-items" :close-menu="closeMenu" />
          </slot>
        </div>
      </div>

      <!-- Click outside to close menu -->
      <div
        v-if="showMenuDropdown"
        class="fixed inset-0 z-40"
        @click="showMenuDropdown = false"
      ></div>
    </div>
  </header>
</template>

<script setup lang="ts">
import { ref } from 'vue'

defineProps<{
  title?: string
  isOnline?: boolean
  isLoggedIn?: boolean
  user?: { username: string; user_id?: string | number } | null
}>()

defineEmits<{
  'toggle-sidebar': []
  'logout': []
}>()

const showMenuDropdown = ref(false)

function closeMenu() {
  showMenuDropdown.value = false
}
</script>
