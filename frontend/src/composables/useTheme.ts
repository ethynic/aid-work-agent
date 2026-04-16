import { ref, computed, watch } from 'vue'

// 主题类型定义
export type ThemeName = 'blue' | 'gray' | 'green' | 'burgundy' | 'orange'

// 颜色配置接口
interface ColorScale {
  50: string
  100: string
  200: string
  300: string
  400: string
  500: string
  600: string
  700: string
  800: string
  900: string
  950?: string
}

interface ThemeColors {
  primary: ColorScale
  success: ColorScale
  warning: ColorScale
  danger: ColorScale
  info: ColorScale
  gray: ColorScale
}

interface ThemeConfig {
  name: string
  label: string
  colors: ThemeColors
}

// 蓝色主题配置 - 主色 #003A8C
const blueTheme: ThemeConfig = {
  name: 'blue',
  label: '商务蓝',
  colors: {
    // 主色 - 基于 #003A8C
    primary: {
      50: '#E6F0FF',
      100: '#CCE0FF',
      200: '#99C2FF',
      300: '#66A3FF',
      400: '#3385FF',
      500: '#0066FF',
      600: '#003A8C', // 主色
      700: '#002E70',
      800: '#002354',
      900: '#001738',
      950: '#000C1C',
    },
    // 成功色 - 翠绿
    success: {
      50: '#ECFDF5',
      100: '#D1FAE5',
      200: '#A7F3D0',
      300: '#6EE7B7',
      400: '#34D399',
      500: '#10B981',
      600: '#059669',
      700: '#047857',
      800: '#065F46',
      900: '#064E3B',
      950: '#022C22',
    },
    // 警告色 - 琥珀
    warning: {
      50: '#FFFBEB',
      100: '#FEF3C7',
      200: '#FDE68A',
      300: '#FCD34D',
      400: '#FBBF24',
      500: '#F59E0B',
      600: '#D97706',
      700: '#B45309',
      800: '#92400E',
      900: '#78350F',
      950: '#451A03',
    },
    // 危险色 - 玫瑰
    danger: {
      50: '#FFF1F2',
      100: '#FFE4E6',
      200: '#FECDD3',
      300: '#FDA4AF',
      400: '#FB7185',
      500: '#F43F5E',
      600: '#E11D48',
      700: '#BE123C',
      800: '#9F1239',
      900: '#881337',
      950: '#4C0519',
    },
    // 信息色 - 天蓝
    info: {
      50: '#F0F9FF',
      100: '#E0F2FE',
      200: '#BAE6FD',
      300: '#7DD3FC',
      400: '#38BDF8',
      500: '#0EA5E9',
      600: '#0284C7',
      700: '#0369A1',
      800: '#075985',
      900: '#0C4A6E',
      950: '#082F49',
    },
    // 中性灰
    gray: {
      50: '#F9FAFB',
      100: '#F3F4F6',
      200: '#E5E7EB',
      300: '#D1D5DB',
      400: '#9CA3AF',
      500: '#6B7280',
      600: '#4B5563',
      700: '#374151',
      800: '#1F2937',
      900: '#111827',
      950: '#030712',
    },
  },
}

// 极简灰主题配置 - 主色 #374151
const grayTheme: ThemeConfig = {
  name: 'gray',
  label: '极简灰',
  colors: {
    primary: {
      50: '#F3F4F6',
      100: '#E5E7EB',
      200: '#D1D5DB',
      300: '#9CA3AF',
      400: '#6B7280',
      500: '#4B5563',
      600: '#374151',
      700: '#1F2937',
      800: '#111827',
      900: '#030712',
      950: '#000000',
    },
    success: {
      50: '#ECFDF5',
      100: '#D1FAE5',
      200: '#A7F3D0',
      300: '#6EE7B7',
      400: '#34D399',
      500: '#10B981',
      600: '#059669',
      700: '#047857',
      800: '#065F46',
      900: '#064E3B',
      950: '#022C22',
    },
    warning: {
      50: '#FFFBEB',
      100: '#FEF3C7',
      200: '#FDE68A',
      300: '#FCD34D',
      400: '#FBBF24',
      500: '#F59E0B',
      600: '#D97706',
      700: '#B45309',
      800: '#92400E',
      900: '#78350F',
      950: '#451A03',
    },
    danger: {
      50: '#FFF1F2',
      100: '#FFE4E6',
      200: '#FECDD3',
      300: '#FDA4AF',
      400: '#FB7185',
      500: '#F43F5E',
      600: '#E11D48',
      700: '#BE123C',
      800: '#9F1239',
      900: '#881337',
      950: '#4C0519',
    },
    info: {
      50: '#F0F9FF',
      100: '#E0F2FE',
      200: '#BAE6FD',
      300: '#7DD3FC',
      400: '#38BDF8',
      500: '#0EA5E9',
      600: '#0284C7',
      700: '#0369A1',
      800: '#075985',
      900: '#0C4A6E',
      950: '#082F49',
    },
    gray: {
      50: '#F9FAFB',
      100: '#F3F4F6',
      200: '#E5E7EB',
      300: '#D1D5DB',
      400: '#9CA3AF',
      500: '#6B7280',
      600: '#4B5563',
      700: '#374151',
      800: '#1F2937',
      900: '#111827',
      950: '#030712',
    },
  },
}

// 活力绿主题配置 - 主色 #059669
const greenTheme: ThemeConfig = {
  name: 'green',
  label: '活力绿',
  colors: {
    primary: {
      50: '#ECFDF5',
      100: '#D1FAE5',
      200: '#A7F3D0',
      300: '#6EE7B7',
      400: '#34D399',
      500: '#10B981',
      600: '#059669',
      700: '#047857',
      800: '#065F46',
      900: '#064E3B',
      950: '#022C22',
    },
    success: {
      50: '#ECFDF5',
      100: '#D1FAE5',
      200: '#A7F3D0',
      300: '#6EE7B7',
      400: '#34D399',
      500: '#10B981',
      600: '#059669',
      700: '#047857',
      800: '#065F46',
      900: '#064E3B',
      950: '#022C22',
    },
    warning: {
      50: '#FFFBEB',
      100: '#FEF3C7',
      200: '#FDE68A',
      300: '#FCD34D',
      400: '#FBBF24',
      500: '#F59E0B',
      600: '#D97706',
      700: '#B45309',
      800: '#92400E',
      900: '#78350F',
      950: '#451A03',
    },
    danger: {
      50: '#FFF1F2',
      100: '#FFE4E6',
      200: '#FECDD3',
      300: '#FDA4AF',
      400: '#FB7185',
      500: '#F43F5E',
      600: '#E11D48',
      700: '#BE123C',
      800: '#9F1239',
      900: '#881337',
      950: '#4C0519',
    },
    info: {
      50: '#F0F9FF',
      100: '#E0F2FE',
      200: '#BAE6FD',
      300: '#7DD3FC',
      400: '#38BDF8',
      500: '#0EA5E9',
      600: '#0284C7',
      700: '#0369A1',
      800: '#075985',
      900: '#0C4A6E',
      950: '#082F49',
    },
    gray: {
      50: '#F9FAFB',
      100: '#F3F4F6',
      200: '#E5E7EB',
      300: '#D1D5DB',
      400: '#9CA3AF',
      500: '#6B7280',
      600: '#4B5563',
      700: '#374151',
      800: '#1F2937',
      900: '#111827',
      950: '#030712',
    },
  },
}

// 酒红主题配置 - 主色 #991B1B
const burgundyTheme: ThemeConfig = {
  name: 'burgundy',
  label: '玫瑰红',
  colors: {
    primary: {
      50: '#FEF2F2',
      100: '#FEE2E2',
      200: '#FECACA',
      300: '#FCA5A5',
      400: '#F87171',
      500: '#EF4444',
      600: '#991B1B',
      700: '#7F1D1D',
      800: '#681414',
      900: '#4C0519',
      950: '#2D0A0A',
    },
    success: {
      50: '#ECFDF5',
      100: '#D1FAE5',
      200: '#A7F3D0',
      300: '#6EE7B7',
      400: '#34D399',
      500: '#10B981',
      600: '#059669',
      700: '#047857',
      800: '#065F46',
      900: '#064E3B',
      950: '#022C22',
    },
    warning: {
      50: '#FFFBEB',
      100: '#FEF3C7',
      200: '#FDE68A',
      300: '#FCD34D',
      400: '#FBBF24',
      500: '#F59E0B',
      600: '#D97706',
      700: '#B45309',
      800: '#92400E',
      900: '#78350F',
      950: '#451A03',
    },
    danger: {
      50: '#FFF1F2',
      100: '#FFE4E6',
      200: '#FECDD3',
      300: '#FDA4AF',
      400: '#FB7185',
      500: '#F43F5E',
      600: '#E11D48',
      700: '#BE123C',
      800: '#9F1239',
      900: '#881337',
      950: '#4C0519',
    },
    info: {
      50: '#F0F9FF',
      100: '#E0F2FE',
      200: '#BAE6FD',
      300: '#7DD3FC',
      400: '#38BDF8',
      500: '#0EA5E9',
      600: '#0284C7',
      700: '#0369A1',
      800: '#075985',
      900: '#0C4A6E',
      950: '#082F49',
    },
    gray: {
      50: '#F9FAFB',
      100: '#F3F4F6',
      200: '#E5E7EB',
      300: '#D1D5DB',
      400: '#9CA3AF',
      500: '#6B7280',
      600: '#4B5563',
      700: '#374151',
      800: '#1F2937',
      900: '#111827',
      950: '#030712',
    },
  },
}

// 珊瑚橙主题配置 - 主色 #EA580C
const orangeTheme: ThemeConfig = {
  name: 'orange',
  label: '珊瑚橙',
  colors: {
    primary: {
      50: '#FFF7ED',
      100: '#FFEDD5',
      200: '#FED7AA',
      300: '#FDBA74',
      400: '#FB923C',
      500: '#F97316',
      600: '#EA580C',
      700: '#C2410C',
      800: '#9A3412',
      900: '#7C2D12',
      950: '#431407',
    },
    success: {
      50: '#ECFDF5',
      100: '#D1FAE5',
      200: '#A7F3D0',
      300: '#6EE7B7',
      400: '#34D399',
      500: '#10B981',
      600: '#059669',
      700: '#047857',
      800: '#065F46',
      900: '#064E3B',
      950: '#022C22',
    },
    warning: {
      50: '#FFFBEB',
      100: '#FEF3C7',
      200: '#FDE68A',
      300: '#FCD34D',
      400: '#FBBF24',
      500: '#F59E0B',
      600: '#D97706',
      700: '#B45309',
      800: '#92400E',
      900: '#78350F',
      950: '#451A03',
    },
    danger: {
      50: '#FFF1F2',
      100: '#FFE4E6',
      200: '#FECDD3',
      300: '#FDA4AF',
      400: '#FB7185',
      500: '#F43F5E',
      600: '#E11D48',
      700: '#BE123C',
      800: '#9F1239',
      900: '#881337',
      950: '#4C0519',
    },
    info: {
      50: '#F0F9FF',
      100: '#E0F2FE',
      200: '#BAE6FD',
      300: '#7DD3FC',
      400: '#38BDF8',
      500: '#0EA5E9',
      600: '#0284C7',
      700: '#0369A1',
      800: '#075985',
      900: '#0C4A6E',
      950: '#082F49',
    },
    gray: {
      50: '#F9FAFB',
      100: '#F3F4F6',
      200: '#E5E7EB',
      300: '#D1D5DB',
      400: '#9CA3AF',
      500: '#6B7280',
      600: '#4B5563',
      700: '#374151',
      800: '#1F2937',
      900: '#111827',
      950: '#030712',
    },
  },
}

// 主题配置映射
const themes: Record<ThemeName, ThemeConfig> = {
  blue: blueTheme,
  gray: grayTheme,
  green: greenTheme,
  burgundy: burgundyTheme,
  orange: orangeTheme,
}

// 当前主题
const currentThemeName = ref<ThemeName>('blue')
const currentTheme = computed(() => themes[currentThemeName.value])

// 从 localStorage 加载主题
const loadThemeFromStorage = () => {
  const saved = localStorage.getItem('app-theme')
  if (saved && saved in themes) {
    currentThemeName.value = saved as ThemeName
  }
}

// 保存主题到 localStorage
const saveThemeToStorage = (theme: ThemeName) => {
  localStorage.setItem('app-theme', theme)
}

// 应用 CSS 变量
const applyThemeVariables = () => {
  const root = document.documentElement
  const colors = currentTheme.value.colors

  // 应用主色
  Object.entries(colors.primary).forEach(([key, value]) => {
    root.style.setProperty(`--color-primary-${key}`, value)
  })

  // 应用成功色
  Object.entries(colors.success).forEach(([key, value]) => {
    root.style.setProperty(`--color-success-${key}`, value)
  })

  // 应用警告色
  Object.entries(colors.warning).forEach(([key, value]) => {
    root.style.setProperty(`--color-warning-${key}`, value)
  })

  // 应用危险色
  Object.entries(colors.danger).forEach(([key, value]) => {
    root.style.setProperty(`--color-danger-${key}`, value)
  })

  // 应用信息色
  Object.entries(colors.info).forEach(([key, value]) => {
    root.style.setProperty(`--color-info-${key}`, value)
  })

  // 应用中性色
  Object.entries(colors.gray).forEach(([key, value]) => {
    root.style.setProperty(`--color-gray-${key}`, value)
  })

  // 设置数据属性用于 Tailwind 选择器
  root.setAttribute('data-theme', currentThemeName.value)
}

// 设置主题
const setTheme = (theme: ThemeName) => {
  if (theme in themes) {
    currentThemeName.value = theme
    saveThemeToStorage(theme)
    applyThemeVariables()
  }
}

// 获取所有可用主题列表
const getAvailableThemes = () => {
  return Object.entries(themes).map(([key, config]) => ({
    name: key as ThemeName,
    label: config.label,
  }))
}

// 监听主题变化
watch(currentThemeName, applyThemeVariables, { immediate: true })

// 初始化主题
const initTheme = () => {
  loadThemeFromStorage()
  applyThemeVariables()
}

export function useTheme() {
  return {
    currentTheme: currentThemeName,
    currentThemeConfig: currentTheme,
    setTheme,
    getAvailableThemes,
    initTheme,
    colors: computed(() => currentTheme.value.colors),
  }
}
