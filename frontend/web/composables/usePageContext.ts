import { ref } from 'vue'

/**
 * 列表页通用上下文
 *
 * 提供分页序号计算、搜索、刷新等列表页通用逻辑。
 *
 * 使用方式：在列表页组件中调用，将返回的 currentPage、pageSize 传给 BasePagination，
 * seqNumber(index) 用于表格序号列。
 *
 * @example
 * ```ts
 * const items = ref<Item[]>([])
 * const total = ref(0)
 * const { currentPage, pageSize, seqNumber, handleSearch, refresh } =
 *   usePageContext(async () => {
 *     const res = await api.list({ page: currentPage.value, pageSize: pageSize.value })
 *     items.value = res.items
 *     total.value = res.total
 *   })
 * ```
 */
export function usePageContext(fetchData: () => Promise<void>) {
  const currentPage = ref(1)
  const pageSize = ref(10)
  const searchKeyword = ref('')
  const loading = ref(false)

  // 序号计算函数：(currentPage - 1) * pageSize + index + 1
  function seqNumber(index: number): number {
    return (currentPage.value - 1) * pageSize.value + index + 1
  }

  // 搜索（重置到第一页）
  async function handleSearch(keyword?: string) {
    if (keyword !== undefined) {
      searchKeyword.value = keyword
    }
    currentPage.value = 1
    await doFetch()
  }

  // 分页切换
  async function handlePageChange(page: number) {
    currentPage.value = page
    await doFetch()
  }

  // 每页行数切换
  async function handlePageSizeChange(size: number) {
    pageSize.value = size
    currentPage.value = 1
    await doFetch()
  }

  // 刷新当前页
  async function refresh() {
    await doFetch()
  }

  // 内部：带 loading 的数据获取
  async function doFetch() {
    loading.value = true
    try {
      await fetchData()
    } finally {
      loading.value = false
    }
  }

  return {
    currentPage,
    pageSize,
    searchKeyword,
    loading,
    seqNumber,
    handleSearch,
    handlePageChange,
    handlePageSizeChange,
    refresh,
  }
}
