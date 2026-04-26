import { ref, computed, watch } from 'vue'

// 主题类型定义
export type ThemeName = 'blue' | 'gray' | 'pine' | 'burgundy' | 'bamboo' | 'iris' | 'sunrise' | 'peony'

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

// 石墨灰主题配置 - 主色 #2F3641
const grayTheme: ThemeConfig = {
  name: 'gray',
  label: '石墨灰',
  colors: {
    primary: {
      50: '#F4F5F7',
      100: '#E8EAED',
      200: '#D1D5DB',
      300: '#A9AEB8',
      400: '#717785',
      500: '#4A505C',
      600: '#2F3641',
      700: '#242933',
      800: '#1A1F27',
      900: '#11141A',
      950: '#090B0E',
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

// 墨松绿主题配置 - 主色 #164E42
const pineTheme: ThemeConfig = {
  name: 'pine',
  label: '墨松绿',
  colors: {
    primary: {
      50: '#EBF4F1',
      100: '#D7E9E3',
      200: '#A8CFBF',
      300: '#74B299',
      400: '#41886F',
      500: '#266654',
      600: '#164E42',
      700: '#0F3D33',
      800: '#0A2C26',
      900: '#051A17',
      950: '#020D0C',
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

// 紫檀红主题配置 - 主色 #5C1A21
const burgundyTheme: ThemeConfig = {
  name: 'burgundy',
  label: '紫檀红',
  colors: {
    primary: {
      50: '#F5EBED',
      100: '#EAD6DB',
      200: '#D1A8B2',
      300: '#A86D7B',
      400: '#834553',
      500: '#6E2F3C',
      600: '#5C1A21',
      700: '#49121A',
      800: '#360D14',
      900: '#23080D',
      950: '#120406',
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

// 翠竹绿主题配置 - 主色 #25B26B（清新绿色）
const bambooTheme: ThemeConfig = {
  name: 'bamboo',
  label: '翠竹绿',
  colors: {
    primary: {
      50: '#EFF8F3',
      100: '#DFF0E0',
      200: '#AEDDB8',
      300: '#7DCA8F',
      400: '#4CB766',
      500: '#31A058',
      600: '#25B26B',
      700: '#1A854C',
      800: '#13643A',
      900: '#0C4327',
      950: '#062214',
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

// 凌霄紫主题配置 - 主色 #8551F9（优雅紫色）
const irisTheme: ThemeConfig = {
  name: 'iris',
  label: '凌霄紫',
  colors: {
    primary: {
      50: '#F5F1FF',
      100: '#EBE2FF',
      200: '#D4BCFF',
      300: '#BC95FF',
      400: '#A46EFF',
      500: '#9551FF',
      600: '#8551F9',
      700: '#6735D6',
      800: '#4D23A8',
      900: '#35167A',
      950: '#1A0B3D',
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

// 旭日橙主题配置 - 主色 #FF7D00（活力橙色）
const sunriseTheme: ThemeConfig = {
  name: 'sunrise',
  label: '旭日橙',
  colors: {
    primary: {
      50: '#FFF5EB',
      100: '#FFEBD6',
      200: '#FFD39E',
      300: '#FFBB66',
      400: '#FFA32E',
      500: '#FF9500',
      600: '#FF7D00',
      700: '#E66F00',
      800: '#CC5E00',
      900: '#994700',
      950: '#663000',
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

// 牡丹红主题配置 - 主色 #D91A80（玫红色）
const peonyTheme: ThemeConfig = {
  name: 'peony',
  label: '牡丹红',
  colors: {
    primary: {
      50: '#FDF1F8',
      100: '#FBE3F1',
      200: '#F7B8DB',
      300: '#F28DC5',
      400: '#EC57AA',
      500: '#E63595',
      600: '#D91A80',
      700: '#B31569',
      800: '#8D1052',
      900: '#670C3B',
      950: '#33061E',
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
  pine: pineTheme,
  burgundy: burgundyTheme,
  bamboo: bambooTheme,
  iris: irisTheme,
  sunrise: sunriseTheme,
  peony: peonyTheme,
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
