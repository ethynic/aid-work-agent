import { ref } from 'vue'
import { importExcel, downloadTemplate } from '@/api/travelQuote'
import type { ImportResult } from '@/api/travelQuote'

export function useImport(onSuccess: () => void) {
  const importing = ref(false)
  const showImportResult = ref(false)
  const importResult = ref<ImportResult | null>(null)

  async function handleImport(file: File) {
    importing.value = true
    try {
      const res = await importExcel(file)
      importResult.value = res.data
      showImportResult.value = true
      if (res.data.total_imported > 0) {
        onSuccess()
      }
    } catch (e: any) {
      alert(e.message || '导入失败')
    } finally {
      importing.value = false
    }
  }

  async function handleDownloadTemplate() {
    try {
      await downloadTemplate()
    } catch (e) {
      alert('下载模板失败')
    }
  }

  function triggerFileInput(input: HTMLInputElement | null) {
    input?.click()
  }

  return {
    importing,
    showImportResult,
    importResult,
    handleImport,
    handleDownloadTemplate,
    triggerFileInput,
  }
}
