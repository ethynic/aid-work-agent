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
    // 飞书/企微等内置浏览器内核较旧（< Chrome 119），缺少 Promise.withResolvers 等 ES2024 API，
    // 现代构建会直接抛错；legacy 构建自带 polyfill 且语法已降级
    const pdfjs = await import('pdfjs-dist/legacy/build/pdf.mjs')
    pdfjs.GlobalWorkerOptions.workerSrc = new URL(
      'pdfjs-dist/legacy/build/pdf.worker.min.mjs',
      import.meta.url,
    ).toString()

    // 非嵌入字体 PDF（中文报价单等工具生成）需要 CMap 解码 CID 编码、
    // 标准字体数据兜底 Helvetica 等，缺任一组资源文字层整体不渲染。
    // 资源拷贝自 pdfjs-dist@5.4.624（public/pdfjs/），升级依赖时需同步重拷
    loadingTask = pdfjs.getDocument({
      url: props.fileUrl,
      useSystemFonts: true,
      cMapUrl: '/pdfjs/cmaps/',
      cMapPacked: true,
      standardFontDataUrl: '/pdfjs/standard_fonts/',
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
