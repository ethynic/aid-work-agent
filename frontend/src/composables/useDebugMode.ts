import { ref } from 'vue'

const isDebugEnabled = ref(false)

// 初始化时从 URL 读取一次
if (typeof window !== 'undefined') {
  const params = new URLSearchParams(window.location.search)
  isDebugEnabled.value = params.get('debug') === '1' || params.get('debug') === 'true'
}

export function useDebugMode() {
  return { isDebugEnabled }
}
