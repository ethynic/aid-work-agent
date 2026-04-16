<template>
  <div class="relative">
    <button
      @click="isOpen = !isOpen"
      class="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 hover:text-primary-600 hover:bg-primary-50 rounded-lg transition-colors"
      title="切换主题"
    >
      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 21a4 4 0 01-4-4V5a2 2 0 012-2h4a2 2 0 012 2v12a4 4 0 01-4 4zm0 0h12a2 2 0 002-2v-4a2 2 0 00-2-2h-2.343M11 7.343l1.657-1.657a2 2 0 012.828 0l2.829 2.829a2 2 0 010 2.828l-8.486 8.485M7 17h.01" />
      </svg>
      <span class="hidden sm:inline">{{ currentThemeConfig?.label || '主题' }}</span>
      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
      </svg>
    </button>

    <!-- Dropdown -->
    <div
      v-if="isOpen"
      class="absolute bottom-full left-0 mb-2 w-48 bg-white rounded-lg shadow-lg border border-gray-200 py-1 z-50"
    >
      <div class="px-3 py-2 text-xs font-medium text-gray-500 uppercase tracking-wider">
        选择主题
      </div>
      <button
        v-for="theme in availableThemes"
        :key="theme.name"
        @click="selectTheme(theme.name)"
        :class="[
          'w-full flex items-center gap-3 px-3 py-2.5 text-left text-sm transition-colors',
          currentTheme === theme.name
            ? 'bg-primary-50 text-primary-700'
            : 'text-gray-700 hover:bg-gray-50'
        ]"
      >
        <!-- Theme Color Preview -->
        <div
          class="w-6 h-6 rounded-full border-2 border-white shadow-sm"
          :style="{ backgroundColor: getThemePreviewColor(theme.name) }"
        />
        <span class="flex-1">{{ theme.label }}</span>
        <svg
          v-if="currentTheme === theme.name"
          class="w-4 h-4 text-primary-600"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
        </svg>
      </button>
    </div>

    <!-- Click outside to close -->
    <div
      v-if="isOpen"
      class="fixed inset-0 z-40"
      @click="isOpen = false"
    />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useTheme, type ThemeName } from '@/composables/useTheme'

const isOpen = ref(false)
const { currentTheme, currentThemeConfig, setTheme, getAvailableThemes } = useTheme()

const availableThemes = getAvailableThemes()

function selectTheme(theme: ThemeName) {
  setTheme(theme)
  isOpen.value = false
}

function getThemePreviewColor(themeName: ThemeName): string {
  // 返回主题的主色用于预览
  switch (themeName) {
    case 'blue':
      return '#003A8C'
    case 'gray':
      return '#374151'
    case 'green':
      return '#059669'
    case 'burgundy':
      return '#991B1B'
    case 'orange':
      return '#EA580C'
    default:
      return '#003A8C'
  }
}
</script>
