import { ref } from 'vue'
import { importExcel, importVehicleExcel, importHotelExcelKB, importAttractionExcelKB, downloadTemplate } from '@/api/travelQuote'
import type { ImportResult, VehicleImportResult, HotelKBImportResult, AttractionKBImportResult, UuidImportResult } from '@/api/travelQuote'

export function useUuidImport(importFn: (file: File) => Promise<{ success: boolean; data: UuidImportResult }>, onSuccess: () => void) {
  const importing = ref(false)
  const showImportResult = ref(false)
  const importResult = ref<UuidImportResult | null>(null)

  async function handleImport(file: File) {
    importing.value = true
    try {
      const res = await importFn(file)
      importResult.value = res.data
      showImportResult.value = true
      if (res.data.imported > 0 || res.data.updated > 0) {
        onSuccess()
      }
    } catch (e: any) {
      alert(e.message || '导入失败')
    } finally {
      importing.value = false
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
    triggerFileInput,
  }
}

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

export function useHotelKBImport(onSuccess: () => void) {
  const importing = ref(false)
  const showImportResult = ref(false)
  const importResult = ref<HotelKBImportResult | null>(null)

  async function handleImport(file: File) {
    importing.value = true
    try {
      const res = await importHotelExcelKB(file)
      importResult.value = res.data
      showImportResult.value = true
      if (res.data.imported > 0) {
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

export function useAttractionKBImport(onSuccess: () => void) {
  const importing = ref(false)
  const showImportResult = ref(false)
  const importResult = ref<AttractionKBImportResult | null>(null)

  async function handleImport(file: File) {
    importing.value = true
    try {
      const res = await importAttractionExcelKB(file)
      importResult.value = res.data
      showImportResult.value = true
      if (res.data.imported > 0) {
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

export function useVehicleImport(onSuccess: () => void) {
  const importing = ref(false)
  const showImportResult = ref(false)
  const importResult = ref<VehicleImportResult | null>(null)

  async function handleImport(file: File) {
    importing.value = true
    try {
      const res = await importVehicleExcel(file)
      importResult.value = res.data
      showImportResult.value = true
      if (res.data.imported > 0) {
        onSuccess()
      }
    } catch (e: any) {
      alert(e.message || '导入失败')
    } finally {
      importing.value = false
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
    triggerFileInput,
  }
}
