<template>
  <div class="h-full overflow-y-auto bg-slate-200/70 px-3 py-4 md:px-5">
    <div class="mx-auto flex max-w-5xl flex-col gap-4">
      <canvas
        v-for="page in pageCount"
        :key="page"
        :ref="element => setCanvasRef(element, page)"
        :aria-label="`${fileName} 第 ${page} 页`"
        class="block h-auto w-full bg-white shadow-md ring-1 ring-slate-300"
      ></canvas>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch, type ComponentPublicInstance } from 'vue'
import type {
  PDFDocumentLoadingTask,
  PDFDocumentProxy,
  RenderTask,
} from 'pdfjs-dist/types/src/display/api'

interface Props {
  fileUrl: string
  fileName: string
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (event: 'loaded'): void
  (event: 'error'): void
}>()

const pageCount = ref(0)
const canvasRefs = new Map<number, HTMLCanvasElement>()
let loadingTask: PDFDocumentLoadingTask | null = null
let documentProxy: PDFDocumentProxy | null = null
let renderTasks: RenderTask[] = []
let loadVersion = 0

function setCanvasRef(
  element: Element | ComponentPublicInstance | null,
  page: number,
) {
  if (element instanceof HTMLCanvasElement) {
    canvasRefs.set(page, element)
  } else {
    canvasRefs.delete(page)
  }
}

async function cleanup() {
  renderTasks.forEach(task => task.cancel())
  renderTasks = []
  canvasRefs.clear()

  if (loadingTask) {
    await loadingTask.destroy()
    loadingTask = null
  }
  documentProxy = null
}

async function loadPdf() {
  const version = ++loadVersion
  await cleanup()
  pageCount.value = 0

  try {
    const pdfjs = await import('pdfjs-dist')
    pdfjs.GlobalWorkerOptions.workerSrc = new URL(
      'pdfjs-dist/build/pdf.worker.min.mjs',
      import.meta.url,
    ).toString()

    loadingTask = pdfjs.getDocument({
      url: props.fileUrl,
      useSystemFonts: true,
    })
    documentProxy = await loadingTask.promise
    if (version !== loadVersion) return

    pageCount.value = documentProxy.numPages
    await nextTick()

    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2)
    let loadedEmitted = false
    for (let pageNumber = 1; pageNumber <= documentProxy.numPages; pageNumber += 1) {
      if (version !== loadVersion) return

      const page = await documentProxy.getPage(pageNumber)
      const viewport = page.getViewport({ scale: 1.5 })
      const canvas = canvasRefs.get(pageNumber)
      const context = canvas?.getContext('2d')
      if (!canvas || !context) throw new Error('PDF canvas 初始化失败')

      canvas.width = Math.floor(viewport.width * pixelRatio)
      canvas.height = Math.floor(viewport.height * pixelRatio)
      canvas.style.aspectRatio = `${viewport.width} / ${viewport.height}`

      const renderTask = page.render({
        canvas,
        canvasContext: context,
        viewport,
        transform: pixelRatio === 1 ? undefined : [pixelRatio, 0, 0, pixelRatio, 0, 0],
      })
      renderTasks.push(renderTask)
      await renderTask.promise
      if (!loadedEmitted) {
        loadedEmitted = true
        emit('loaded')
      }
    }

    if (!loadedEmitted) throw new Error('PDF没有可预览页面')
  } catch (error) {
    if (version === loadVersion && (error as Error)?.name !== 'RenderingCancelledException') {
      console.error('PDF.js 预览失败:', error)
      emit('error')
    }
  }
}

watch(() => props.fileUrl, loadPdf, { immediate: true })

onBeforeUnmount(() => {
  loadVersion += 1
  void cleanup()
})
</script>
