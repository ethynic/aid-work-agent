import { ref, computed } from 'vue'
import { useMobile } from '@/composables/useMobile'
import type { ListResponse } from '@/api/aiLeadGen'

/**
 * AI获客列表页通用上下文：PC 端分页替换 + 手机端滚动累积加载
 *
 * 手机端（<768px）不显示分页器，滚动到底部自动追加下一页（追加不替换），
 * 加载状态用独立的 isLoadingMore 避免列表卸载抖动；序号按累积列表计算。
 */
export function useLeadGenList<T>(
  fetchPage: (page: number, pageSize: number) => Promise<ListResponse<T>>,
  getRowId: (item: T) => number | string,
) {
  const { isMobile } = useMobile()
  const items = ref<T[]>([])
  const total = ref(0)
  const currentPage = ref(1)
  const pageSize = ref(20)
  const searchKeyword = ref('')
  const loading = ref(false)
  const isLoadingMore = ref(false)
  const scrollContainer = ref<HTMLElement | null>(null)

  const hasMore = computed(() => items.value.length < total.value)

  function seqNumber(index: number): number {
    if (isMobile.value) return index + 1
    return (currentPage.value - 1) * pageSize.value + index + 1
  }

  async function fetchAndReplace() {
    loading.value = true
    try {
      const res = await fetchPage(currentPage.value, pageSize.value)
      items.value = res.items
      total.value = res.total
    } catch (e: any) {
      console.error('加载列表失败:', e)
    } finally {
      loading.value = false
    }
  }

  async function handleSearch(keyword?: string) {
    if (keyword !== undefined) searchKeyword.value = keyword
    currentPage.value = 1
    await fetchAndReplace()
  }

  async function handlePageChange(page: number) {
    currentPage.value = page
    await fetchAndReplace()
  }

  async function refresh() {
    await fetchAndReplace()
  }

  async function loadMore() {
    if (loading.value || isLoadingMore.value || !hasMore.value) return
    const nextPage = currentPage.value + 1
    isLoadingMore.value = true
    try {
      const res = await fetchPage(nextPage, pageSize.value)
      const current = items.value as T[]
      const existingIds = new Set(current.map(i => getRowId(i)))
      const newItems = res.items.filter(i => !existingIds.has(getRowId(i)))
      items.value = [...current, ...newItems] as T[]
      currentPage.value = res.page
      total.value = res.total
    } catch (e: any) {
      console.error('加载更多失败:', e)
    } finally {
      isLoadingMore.value = false
    }
  }

  async function handleScroll() {
    if (!isMobile.value) return
    const el = scrollContainer.value
    if (!el) return
    if (loading.value || isLoadingMore.value || !hasMore.value) return
    const { scrollTop, scrollHeight, clientHeight } = el
    if (scrollHeight - scrollTop - clientHeight < 80) {
      await loadMore()
    }
  }

  return {
    isMobile,
    items,
    total,
    currentPage,
    pageSize,
    searchKeyword,
    loading,
    isLoadingMore,
    hasMore,
    scrollContainer,
    seqNumber,
    handleSearch,
    handlePageChange,
    refresh,
    loadMore,
    handleScroll,
  }
}
