import { ref, computed } from 'vue'

export interface UseTableSelectionOptions<T = number> {
  /**
   * 从行数据中提取ID的函数
   */
  getRowId: (row: T) => T
}

export function useTableSelection<T = number>(options: UseTableSelectionOptions<T>) {
  const selectedIds = ref<Set<T>>(new Set())

  const selectedArr = computed<T[]>({
    get: () => [...selectedIds.value],
    set: (vals: T[]) => {
      selectedIds.value = new Set(vals)
    }
  })

  const isAllSelected = computed(() => {
    return (rows: T[]) => {
      if (rows.length === 0) return false
      return rows.every((row) => selectedIds.value.has(options.getRowId(row)))
    }
  })

  const isIndeterminate = computed(() => {
    return (rows: T[]) => {
      if (rows.length === 0) return false
      const selectedCount = rows.filter((row) => selectedIds.value.has(options.getRowId(row))).length
      return selectedCount > 0 && selectedCount < rows.length
    }
  })

  function toggleAll(rows: T[], checked: boolean) {
    if (checked) {
      selectedIds.value = new Set(rows.map((row) => options.getRowId(row)))
    } else {
      selectedIds.value.clear()
    }
  }

  function toggleRow(row: T) {
    const id = options.getRowId(row)
    if (selectedIds.value.has(id)) {
      selectedIds.value.delete(id)
    } else {
      selectedIds.value.add(id)
    }
  }

  function clearSelection() {
    selectedIds.value.clear()
  }

  function isSelected(row: T): boolean {
    return selectedIds.value.has(options.getRowId(row))
  }

  return {
    selectedIds,
    selectedArr,
    isAllSelected,
    isIndeterminate,
    toggleAll,
    toggleRow,
    clearSelection,
    isSelected
  }
}
