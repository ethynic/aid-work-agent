<template>
  <div
    ref="panelRef"
    class="w-full md:max-w-[80vw] flex flex-col bg-white h-full flex-shrink-0 pb-[env(safe-area-inset-bottom)] relative"
    :style="panelStyle"
  >
    <!-- 拖拽调整宽度的手柄 -->
    <div
      class="hidden md:block absolute left-0 top-0 bottom-0 w-1.5 cursor-col-resize z-10 hover:bg-primary-300 active:bg-primary-400 transition-colors group"
      @mousedown="startResize"
    >
      <div class="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-0.5 h-8 bg-gray-300 rounded-full opacity-0 group-hover:opacity-100 transition-opacity"></div>
    </div>
    <!-- Header -->
    <div class="flex items-center gap-2 px-4 py-3 border-b border-gray-200 bg-gray-50 flex-shrink-0">
      <!-- 文件图标：与附件 chip / 文件卡片同款类型徽标 -->
      <FileTypeIcon :kind="headerIconKind" class="w-4 h-4 flex-shrink-0" />
      <!-- 文件名 -->
      <span class="text-sm font-medium text-gray-700 truncate flex-1" :title="attachment?.name">
        {{ attachment?.name || '文件预览' }}
      </span>
      <!-- 文件大小 -->
      <span v-if="attachment?.size" class="text-xs text-gray-400 flex-shrink-0">
        {{ formatFileSize(attachment.size) }}
      </span>
      <!-- 下载按钮 -->
      <a
        v-if="attachment?.file_id"
        :href="getFileDownloadUrl(attachment.file_id)"
        download
        class="p-1.5 rounded-lg text-gray-400 hover:text-primary-500 hover:bg-primary-50 transition-colors flex-shrink-0"
        title="下载文件"
      >
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
        </svg>
      </a>
      <!-- 关闭按钮 -->
      <button
        @click="emit('close')"
        class="p-2 min-w-[44px] min-h-[44px] flex items-center justify-center rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-200 transition-colors flex-shrink-0"
        title="关闭"
      >
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>

    <!-- Preview Content -->
    <div class="flex-1 min-h-0 overflow-hidden relative">
      <!-- Loading -->
      <div v-if="loading" class="absolute inset-0 flex items-center justify-center bg-white/80 z-10">
        <div class="flex items-center gap-2 text-gray-400">
          <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          <span class="text-sm">加载中...</span>
        </div>
      </div>

      <!-- Error -->
      <div v-if="error" class="flex flex-col items-center justify-center h-full gap-3 p-6 text-center">
        <svg class="w-12 h-12 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z" />
        </svg>
        <p class="text-sm text-gray-500">{{ error }}</p>
        <a
          v-if="attachment?.file_id"
          :href="getFileDownloadUrl(attachment.file_id)"
          download
          class="inline-flex items-center gap-1.5 px-4 py-2 bg-primary-500 text-white rounded-lg text-sm hover:bg-primary-600 transition-colors"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
          </svg>
          下载文件
        </a>
      </div>

      <!-- Image Preview -->
      <div v-if="previewType === 'image' && attachment?.file_id" class="flex items-center justify-center p-4 min-h-full">
        <img
          :key="mediaKey"
          :src="getFileUrl(attachment.file_id)"
          :alt="attachment.name"
          class="max-w-full max-h-[calc(100vh-120px)] object-contain rounded shadow-sm"
          @load="loading = false"
          @error="handleImageError"
        />
      </div>

      <!-- PDF Preview -->
      <PdfCanvasPreview
        v-if="previewType === 'pdf' && attachment?.file_id"
        :key="mediaKey"
        :file-url="getFileUrl(attachment.file_id)"
        :file-name="attachment.name"
        @loaded="loading = false"
        @error="handlePdfPreviewError"
      />

      <!-- HTML Preview -->
      <iframe
        v-if="previewType === 'html' && attachment?.file_id"
        :key="mediaKey"
        ref="htmlFrameRef"
        :src="getFileUrl(attachment.file_id)"
        class="block w-full h-full min-h-0 border-0"
        sandbox="allow-same-origin allow-scripts"
        @load="handleHtmlLoad"
      ></iframe>

      <!-- Text/Code Preview -->
      <div v-if="previewType === 'text' || previewType === 'markdown'" class="p-4">
        <div
          v-if="previewType === 'markdown'"
          class="markdown-content prose prose-slate max-w-none text-sm"
          v-html="renderedTextContent"
        ></div>
        <pre v-else class="bg-gray-900 text-gray-100 rounded-lg p-4 text-sm overflow-x-auto"><code :class="`hljs language-${textLanguage}`">{{ textContent }}</code></pre>
      </div>

      <!-- DOCX Preview：整页缩放模式，渲染后按容器宽度设置 CSS zoom；overflow-auto 为缩放触底时兜底 -->
      <div v-if="previewType === 'docx'" ref="docxContainer" class="p-4 min-h-full overflow-auto"></div>

      <!-- Excel Preview（设计 §4.2：多 Sheet Tab + 表格容器横向滚动） -->
      <div v-if="previewType === 'excel'" class="flex flex-col h-full">
        <!-- 截断提示（设计 §5.2：静默截断会让用户误以为表格就这么大，必须显眼提示） -->
        <div
          v-if="excelTruncated"
          class="flex items-center gap-1.5 px-4 py-2 text-xs font-medium bg-warning-50 text-warning-700 border-b border-warning-200 flex-shrink-0"
        >
          <svg class="w-3.5 h-3.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z" />
          </svg>
          <span>仅显示前 {{ EXCEL_MAX_RENDER_ROWS }} 行，完整内容请下载</span>
        </div>
        <!-- Sheet 标签栏（单 Sheet 不渲染） -->
        <div v-if="excelSheetNames.length > 1" class="flex border-b border-gray-200 px-4 bg-gray-50 overflow-x-auto flex-shrink-0">
          <button
            v-for="(name, idx) in excelSheetNames"
            :key="idx"
            class="px-3 py-2 text-sm whitespace-nowrap border-b-2 transition-colors"
            :class="excelActiveSheet === idx
              ? 'border-primary-500 text-primary-600 font-medium'
              : 'border-transparent text-gray-500 hover:text-gray-700'"
            @click="switchExcelSheet(idx)"
          >
            {{ name }}
          </button>
        </div>
        <!-- 表格内容：容器 overflow-auto，移动端横向滚动是表格自然交互（设计 §4.4） -->
        <div ref="excelContainer" class="excel-preview-container flex-1 overflow-auto p-4"></div>
      </div>

      <!-- PPTX Preview（设计 §4.2：面板导航翻页，首页/末页禁用） -->
      <div v-if="previewType === 'pptx'" class="flex flex-col h-full">
        <!-- 幻灯片导航（单页不渲染） -->
        <div
          v-if="pptxTotalSlides > 1"
          class="flex items-center justify-center gap-4 py-2 border-b border-gray-200 bg-gray-50 flex-shrink-0"
        >
          <button
            @click="prevSlide"
            :disabled="pptxCurrentSlide <= 0"
            class="p-1.5 rounded hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed text-gray-500"
            title="上一页"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <span class="text-sm text-gray-500">{{ pptxCurrentSlide + 1 }} / {{ pptxTotalSlides }}</span>
          <button
            @click="nextSlide"
            :disabled="pptxCurrentSlide >= pptxTotalSlides - 1"
            class="p-1.5 rounded hover:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed text-gray-500"
            title="下一页"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
            </svg>
          </button>
        </div>
        <!-- 幻灯片内容：按容器宽度等比缩放整页可见，细节滚动查看（设计 §4.4） -->
        <div ref="pptxViewportRef" class="flex-1 overflow-auto p-4 flex items-start justify-center">
          <div ref="pptxContainer" class="pptx-preview-container shadow-lg"></div>
        </div>
      </div>

      <!-- Unsupported -->
      <div v-if="previewType === 'unsupported'" class="flex flex-col items-center justify-center h-full gap-4 p-6 text-center">
        <svg class="w-16 h-16 text-gray-200" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
        <div>
          <!-- 超限 Office 文件与真不支持的文件共用此 UI，文案区分（设计 §5.2） -->
          <p class="text-sm font-medium text-gray-600">{{ officeSizeBlocked ? '文件较大，建议下载查看' : '此文件类型无法预览' }}</p>
          <p class="text-xs text-gray-400 mt-1">{{ attachment?.name }}</p>
        </div>
        <a
          v-if="attachment?.file_id"
          :href="getFileDownloadUrl(attachment.file_id)"
          download
          class="inline-flex items-center gap-1.5 px-4 py-2 bg-primary-500 text-white rounded-lg text-sm hover:bg-primary-600 transition-colors"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
          </svg>
          下载文件
        </a>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, shallowRef, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import type { AttachmentInfo } from '@/types'
import type { WorkBook } from 'xlsx-js-style'
import { getFileUrl, getFileDownloadUrl } from '@/api/agent'
import { renderMarkdown } from '@/utils/markdown'
import { formatFileSize } from '@/utils/file'
import {
  EXCEL_MAX_RENDER_ROWS,
  detectOfficePreviewKind,
  officePreviewMaxBytes,
  truncateSheetRange,
  previewFitScale,
  DOCX_DEFAULT_PAGE_WIDTH,
} from '@/utils/officePreview'
import { detectFileIconKind, type FileIconKind } from '@/utils/file'
import FileTypeIcon from './FileTypeIcon.vue'
import PdfCanvasPreview from './PdfCanvasPreview.vue'

interface Props {
  attachment: AttachmentInfo | null
}

const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'close'): void
}>()

const loading = ref(false)
const error = ref<string | null>(null)
// 媒体元素（img/pdf/iframe）的挂载 key，每次打开附件递增，
// 强制重新挂载以保证 load/error 事件触发（修复重复点击同一卡片时一直转圈的问题）
const mediaKey = ref(0)
const textContent = ref('')
const textLanguage = ref('plaintext')
const docxContainer = ref<HTMLDivElement | null>(null)

// 面板头部文件图标种类（与附件 chip / 文件卡片同款类型徽标）
const headerIconKind = computed<FileIconKind>(() =>
  detectFileIconKind(props.attachment?.mime_type || '', props.attachment?.name || ''),
)
const panelRef = ref<HTMLDivElement | null>(null)
const htmlFrameRef = ref<HTMLIFrameElement | null>(null)
const panelWidth = ref(480)

// ===== Excel 预览状态（设计 §4.2）=====
const excelContainer = ref<HTMLDivElement | null>(null)
// WorkBook 体积可能很大，用 shallowRef 避免深度响应式开销
const excelWorkbook = shallowRef<WorkBook | null>(null)
const excelSheetNames = ref<string[]>([])
const excelActiveSheet = ref(0)
// 当前激活 sheet 是否被行数门槛截断（设计 §5.2）
const excelTruncated = ref(false)
// 动态 import 得到的 xlsx 模块句柄（非响应式），sheet 切换时复用
let xlsxLib: typeof import('xlsx-js-style') | null = null

// ===== PPT 预览状态（设计 §4.2）=====
const pptxContainer = ref<HTMLDivElement | null>(null)
const pptxViewportRef = ref<HTMLDivElement | null>(null)
const pptxTotalSlides = ref(0)
const pptxCurrentSlide = ref(0)
// pptx-preview 预览器实例（非响应式，含大量 DOM 引用）
let pptxPreviewer: ReturnType<typeof import('pptx-preview').init> | null = null

// 附件加载纪元：watch 每次附件变化（含组件卸载）递增。
// 各加载任务在 await 检查点比对纪元，不一致即附件已切换、本次结果过期，
// 直接丢弃且不再触碰共享状态/容器——防止快速连续切换附件时，
// 后完成的旧任务覆盖新附件的预览状态与 DOM（竞态守卫）
let attachmentEpoch = 0

const panelStyle = computed(() => ({
  width: `${panelWidth.value}px`,
  minWidth: '320px',
  borderLeft: '1px solid #e5e7eb',
}))

// 拖拽调整宽度
function startResize(e: MouseEvent) {
  e.preventDefault()
  const startX = e.clientX
  const startWidth = panelWidth.value

  function onMouseMove(ev: MouseEvent) {
    // 面板在右侧，向左拖 = 变宽，向右拖 = 变窄
    const delta = startX - ev.clientX
    const newWidth = Math.min(Math.max(startWidth + delta, 320), window.innerWidth * 0.8)
    panelWidth.value = newWidth
  }

  function onMouseUp() {
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }

  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}

// 判断预览类型
const previewType = computed(() => {
  if (!props.attachment) return 'unsupported'
  const mime = props.attachment.mime_type || ''
  const name = props.attachment.name || ''
  const ext = name.includes('.') ? name.split('.').pop()!.toLowerCase() : ''

  // 图片
  if (mime.startsWith('image/') || ['png', 'jpg', 'jpeg', 'gif'].includes(ext)) return 'image'

  // PDF
  if (mime === 'application/pdf' || ext === 'pdf') return 'pdf'

  // Markdown
  if (ext === 'md' || ext === 'markdown') return 'markdown'

  // HTML
  if (ext === 'html' || ext === 'htm' || mime === 'text/html') return 'html'

  // 文本/代码
  const textExts = ['txt', 'json', 'csv', 'js', 'ts', 'py', 'vue', 'css', 'xml', 'yaml', 'yml', 'sh', 'bat', 'sql', 'log', 'ini', 'conf', 'md']
  const textMimes = ['text/', 'application/json', 'application/javascript', 'application/xml']
  if (textExts.includes(ext) || textMimes.some(m => mime.startsWith(m))) return 'text'

  // Office 三格式（设计 §4.2 + §5.2 大小门槛；.doc/.ppt 旧格式不识别，走 unsupported）
  const kind = detectOfficePreviewKind(mime, ext)
  if (kind) {
    return (props.attachment.size ?? 0) <= officePreviewMaxBytes(kind) ? kind : 'unsupported'
  }

  return 'unsupported'
})

// Office 文件是否因超过大小门槛被拦下（Excel >10MB、Word/PPT >20MB，设计 §5.2）。
// 与真不支持的文件共用 unsupported UI，但提示文案不同
const officeSizeBlocked = computed(() => {
  if (!props.attachment) return false
  const name = props.attachment.name || ''
  const ext = name.includes('.') ? name.split('.').pop()!.toLowerCase() : ''
  const kind = detectOfficePreviewKind(props.attachment.mime_type || '', ext)
  if (!kind) return false
  return (props.attachment.size ?? 0) > officePreviewMaxBytes(kind)
})

// Markdown 渲染后的内容
const renderedTextContent = computed(() => {
  if (previewType.value === 'markdown' && textContent.value) {
    return renderMarkdown(textContent.value)
  }
  return ''
})

// 文本文件语言检测
function detectLanguage(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase() || ''
  const langMap: Record<string, string> = {
    'js': 'javascript', 'ts': 'typescript', 'py': 'python', 'vue': 'html',
    'html': 'html', 'css': 'css', 'json': 'json', 'xml': 'xml',
    'yaml': 'yaml', 'yml': 'yaml', 'sh': 'bash', 'bat': 'bat',
    'sql': 'sql', 'md': 'markdown', 'csv': 'plaintext', 'txt': 'plaintext',
    'log': 'log', 'ini': 'ini', 'conf': 'nginx'
  }
  return langMap[ext] || 'plaintext'
}

// 加载文本内容
async function loadTextContent() {
  if (!props.attachment?.file_id) return
  if (previewType.value !== 'text' && previewType.value !== 'markdown') return

  loading.value = true
  error.value = null

  try {
    const url = getFileUrl(props.attachment.file_id)
    const response = await fetch(url)
    if (!response.ok) throw new Error('文件加载失败')
    textContent.value = await response.text()
    textLanguage.value = detectLanguage(props.attachment.name)
  } catch (e) {
    error.value = '文件内容加载失败，请尝试下载查看'
  } finally {
    loading.value = false
  }
}

// 加载 DOCX
// 最近一次渲染的 docx 页宽（取自 section 内联宽度）：窗口 resize 时只需重算缩放，无需重新渲染
let lastDocxPageWidth = 0

/**
 * 按容器内容宽度对 docx wrapper 设置整页缩放（仿 PPT 模式）。
 * 注意：docx-preview 未传 styleContainer 时会把 <style> 注入到渲染容器内，
 * 必须按类名定位 wrapper（className 'docx-preview' → wrapper 类名 .docx-preview-wrapper），
 * 不能用 firstElementChild（那会命中 <style>，缩放不生效）。
 * CSS zoom 参与布局计算，滚动尺寸正确；容器 overflow-auto 兜底（缩放触底时不裁切）。
 */
function applyDocxZoom() {
  const el = docxContainer.value
  const wrapper = el?.querySelector('.docx-preview-wrapper') as HTMLElement | null
  if (!el || !wrapper) return
  wrapper.style.zoom = String(previewFitScale(el.clientWidth - 32 /* p-4 内边距，与 pptx 测宽口径一致 */, lastDocxPageWidth))
}

async function renderDocxContent(blob: Blob, el: HTMLDivElement) {
  const { renderAsync } = await import('docx-preview')
  el.innerHTML = '' // 同容器二次渲染（docx→docx 切换附件）前清空：库是追加式写入，不清会双文档
  await renderAsync(blob, el, undefined, {
    className: 'docx-preview',
    inWrapper: true,
    // 整页缩放模式（仿 PPT，2026-10-10 验收反馈）：保留原始页宽与版式，
    // 渲染后由 applyDocxZoom 按容器宽度 CSS zoom 等比缩放整页可见，不做 ignoreWidth 重排
    ignoreWidth: false,
    ignoreHeight: true,
    ignoreFonts: false,
    breakPages: true,
  })
  // docx-preview 给每个 section 写内联页宽（px）；缺失时按 A4 纵向兜底
  lastDocxPageWidth = parseFloat(el.querySelector('section')?.style.width || '') || DOCX_DEFAULT_PAGE_WIDTH
  applyDocxZoom()
}

async function loadDocx() {
  if (!props.attachment?.file_id) return
  if (previewType.value !== 'docx') return

  const epoch = attachmentEpoch

  loading.value = true
  error.value = null

  try {
    // fetch 与 docx-preview 懒加载并行预热；renderDocxContent 内会再次动态 import（模块已缓存）
    const [docxBlob] = await Promise.all([
      fetch(getFileUrl(props.attachment.file_id)).then(r => {
        if (!r.ok) throw new Error('文件加载失败')
        return r.blob()
      }),
      import('docx-preview'),
    ])
    if (epoch !== attachmentEpoch) return // 附件已切换：丢弃过期结果

    await nextTick()
    if (epoch !== attachmentEpoch) return
    if (docxContainer.value) {
      await renderDocxContent(docxBlob, docxContainer.value)
    }
  } catch (e) {
    // 过期任务的失败不归属当前附件，不写错误状态
    if (epoch === attachmentEpoch) {
      error.value = '文档预览加载失败，请尝试下载查看'
    }
  } finally {
    // 过期任务不得熄灭新任务的 loading 遮罩
    if (epoch === attachmentEpoch) loading.value = false
  }
}

/** 窗口 resize 后面板宽度变化，重算 docx/Excel 缩放（防抖；无需重新拉取文件） */
let previewResizeTimer: ReturnType<typeof setTimeout> | undefined
function handleWindowResize() {
  if (previewResizeTimer !== undefined) clearTimeout(previewResizeTimer)
  previewResizeTimer = setTimeout(() => {
    previewResizeTimer = undefined
    if (previewType.value === 'docx') applyDocxZoom()
    else if (previewType.value === 'excel') renderExcelSheet(excelActiveSheet.value)
  }, 200)
}

// 加载 Excel（xlsx-js-style 动态 import 懒加载，设计 §2.2/§5.1）
async function loadExcel() {
  if (!props.attachment?.file_id) return
  if (previewType.value !== 'excel') return

  const epoch = attachmentEpoch

  loading.value = true
  error.value = null
  excelWorkbook.value = null
  excelSheetNames.value = []
  excelActiveSheet.value = 0
  excelTruncated.value = false

  try {
    const [mod, blob] = await Promise.all([
      import('xlsx-js-style'),
      fetch(getFileUrl(props.attachment.file_id)).then(r => {
        if (!r.ok) throw new Error('文件加载失败')
        return r.blob()
      }),
    ])
    if (epoch !== attachmentEpoch) return // 附件已切换：丢弃过期结果
    // CJS 互操作兜底：部分打包路径下命名导出挂在 default 上
    xlsxLib = ((mod as { default?: typeof import('xlsx-js-style') }).default ?? mod) as typeof import('xlsx-js-style')

    // 传 Uint8Array 而非 ArrayBuffer：SheetJS 官方支持，且规避
    // blob.arrayBuffer() 产物在部分运行环境的跨 realm instanceof 识别问题
    const arrayBuffer = await blob.arrayBuffer()
    if (epoch !== attachmentEpoch) return
    excelWorkbook.value = xlsxLib.read(new Uint8Array(arrayBuffer), { type: 'array' })
    excelSheetNames.value = excelWorkbook.value.SheetNames

    await nextTick()
    if (epoch !== attachmentEpoch) return // nextTick 期间附件可能又切换
    renderExcelSheet(0)
  } catch (e) {
    // 过期任务的失败不归属当前附件，不写错误状态
    if (epoch === attachmentEpoch) {
      // .xls 旧格式等解析失败统一走下载引导（设计 §2.4）
      error.value = '表格预览加载失败，请尝试下载查看'
    }
  } finally {
    // 过期任务不得熄灭新任务的 loading 遮罩
    if (epoch === attachmentEpoch) loading.value = false
  }
}

// 渲染指定 sheet；超过行数门槛时以截断后的 !ref 视图渲染，不改写工作簿本身
function renderExcelSheet(index: number) {
  const workbook = excelWorkbook.value
  const XLSX = xlsxLib
  if (!workbook || !XLSX || !excelContainer.value) return
  const sheet = workbook.Sheets[workbook.SheetNames[index]]
  if (!sheet) return

  excelActiveSheet.value = index

  // 行数门槛（设计 §5.2）：解码 !ref → 纯函数截断 → 以重写 !ref 的副本渲染
  let renderSheet = sheet
  if (sheet['!ref']) {
    const result = truncateSheetRange(XLSX.utils.decode_range(sheet['!ref']), EXCEL_MAX_RENDER_ROWS)
    if (result.truncated) {
      renderSheet = { ...sheet, '!ref': XLSX.utils.encode_range(result.range) }
    }
    excelTruncated.value = result.truncated
  } else {
    excelTruncated.value = false
  }

  excelContainer.value.innerHTML = XLSX.utils.sheet_to_html(renderSheet, { editable: false })
  // sheet_to_html 产出无类名的 <table>，补上样式钩子（样式见 <style> 中 .excel-preview-table）
  const table = excelContainer.value.querySelector('table')
  table?.classList.add('excel-preview-table')
  // 整表按容器宽度缩放（与 docx 整页缩放同模式，2026-10-10 验收反馈）：
  // 适度偏宽的表缩放后整表可见；极宽表触底下限后退回横向滚动（表格的自然交互）
  if (table instanceof HTMLElement && excelContainer.value) {
    table.style.zoom = String(previewFitScale(excelContainer.value.clientWidth - 32 /* p-4 内边距 */, table.offsetWidth))
  }
}

// 切换 Sheet Tab
function switchExcelSheet(index: number) {
  if (index === excelActiveSheet.value) return
  renderExcelSheet(index)
}

// 加载 PPT（pptx-preview 动态 import 懒加载，设计 §2.3/§5.1）
async function loadPptx() {
  if (!props.attachment?.file_id) return
  if (previewType.value !== 'pptx') return

  const epoch = attachmentEpoch

  loading.value = true
  error.value = null
  pptxTotalSlides.value = 0
  pptxCurrentSlide.value = 0

  try {
    const [{ init }, blob] = await Promise.all([
      import('pptx-preview'),
      fetch(getFileUrl(props.attachment.file_id)).then(r => {
        if (!r.ok) throw new Error('文件加载失败')
        return r.blob()
      }),
    ])
    if (epoch !== attachmentEpoch) return // 附件已切换：尚未触碰共享容器，直接丢弃

    const container = pptxContainer.value
    const viewport = pptxViewportRef.value
    if (!container || !viewport) return

    const arrayBuffer = await blob.arrayBuffer()
    if (epoch !== attachmentEpoch) return
    // pptx-preview 声明入参为 ArrayBuffer，内部直接转交 jszip（对 Uint8Array
    // 同样官方支持）；传 Uint8Array 规避跨 realm instanceof 识别问题
    const pptxData = new Uint8Array(arrayBuffer) as unknown as ArrayBuffer

    // 按容器宽度等比缩放整页可见（设计 §4.4）：
    // 库内部以 viewPort.width / pptx.width 计算缩放比，高度按幻灯片实际比例推导。
    // 先按 16:9 估算视口高度，加载后如实际比例不同（如 4:3），按真实比例重建预览器
    const width = Math.max(320, Math.floor(viewport.clientWidth) - 32 /* p-4 内边距 */)
    const estimatedHeight = Math.round((width * 9) / 16)
    container.innerHTML = '' // 尚为最新任务时才清容器（此点之后所有共享 DOM 写入同理）
    let previewer = init(container, { width, height: estimatedHeight, mode: 'slide' })
    await previewer.preview(pptxData)
    if (epoch !== attachmentEpoch) return discardPptxPreviewer(previewer, container)

    const slideWidth = previewer.pptx?.width
    const slideHeight = previewer.pptx?.height
    if (slideWidth && slideHeight) {
      const realHeight = Math.round((width * slideHeight) / slideWidth)
      if (Math.abs(realHeight - estimatedHeight) > 1) {
        // 视口高度与真实比例不符会导致幻灯片顶部裁切，按真实比例重建；
        // 重建失败沿用外层 catch 走下载引导
        previewer.destroy()
        container.innerHTML = ''
        previewer = init(container, { width, height: realHeight, mode: 'slide' })
        await previewer.preview(pptxData)
        if (epoch !== attachmentEpoch) return discardPptxPreviewer(previewer, container)
      }
    }

    if (epoch !== attachmentEpoch) return discardPptxPreviewer(previewer, container)

    // 提交：此时仍是最新附件，独占更新共享状态（pptxPreviewer 的销毁由 destroyPptx 统一负责）
    pptxPreviewer = previewer
    pptxTotalSlides.value = previewer.slideCount
    pptxCurrentSlide.value = previewer.currentIndex
  } catch (e) {
    // 过期任务的失败不归属当前附件，不写错误状态
    if (epoch === attachmentEpoch) {
      error.value = '演示文稿预览加载失败，请尝试下载查看'
    }
  } finally {
    // 过期任务不得熄灭新任务的 loading 遮罩
    if (epoch === attachmentEpoch) loading.value = false
  }
}

/**
 * 丢弃在途（已过期）的 pptx 预览器：销毁其内部资源（echarts 实例等），
 * 仅当该预览器的 wrapper 仍挂在共享容器时移除之——若新任务已提交并
 * 重写容器，此处不得误删新附件的渲染产物
 */
function discardPptxPreviewer(
  previewer: ReturnType<typeof import('pptx-preview').init>,
  container: HTMLElement,
) {
  try {
    previewer.destroy()
  } catch {
    // destroy 内部清理失败不影响后续使用
  }
  const wrapper = previewer.wrapper
  if (wrapper && wrapper.parentElement === container) {
    container.removeChild(wrapper)
  }
}

// PPT 翻页：首页/末页由按钮 disabled 保证不出界
function gotoSlide(index: number) {
  const previewer = pptxPreviewer
  if (!previewer) return
  if (index < 0 || index >= pptxTotalSlides.value) return
  previewer.renderSingleSlide(index)
  pptxCurrentSlide.value = index
}

function prevSlide() {
  gotoSlide(pptxCurrentSlide.value - 1)
}

function nextSlide() {
  gotoSlide(pptxCurrentSlide.value + 1)
}

// 释放 pptx 预览器（echarts 实例等），附件切换/组件卸载时调用
function destroyPptx() {
  if (pptxPreviewer) {
    try {
      pptxPreviewer.destroy()
    } catch {
      // destroy 内部清理失败不影响面板后续使用
    }
    pptxPreviewer = null
  }
  if (pptxContainer.value) {
    pptxContainer.value.innerHTML = ''
  }
  pptxTotalSlides.value = 0
  pptxCurrentSlide.value = 0
}

// 图片加载错误
function handleImageError() {
  loading.value = false
  error.value = '图片加载失败，文件可能已过期'
}

function handlePdfPreviewError() {
  loading.value = false
  error.value = 'PDF预览加载失败，请下载文件查看'
}

// 生成类 HTML 可能包含面向打印的 100vh/overflow:hidden 样式。
// 预览时恢复文档滚动，避免长内容在 iframe 中被截断。
function handleHtmlLoad() {
  loading.value = false

  try {
    const doc = htmlFrameRef.value?.contentDocument
    if (!doc) return

    const style = doc.createElement('style')
    style.dataset.previewScrollFix = 'true'
    style.textContent = `
      html, body {
        height: auto !important;
        min-height: 100% !important;
        max-height: none !important;
        overflow-x: auto !important;
        overflow-y: auto !important;
        overscroll-behavior: contain;
      }
      body > main,
      body > .page,
      body > .container,
      body > .document,
      body > .report {
        height: auto !important;
        max-height: none !important;
        overflow: visible !important;
      }
    `
    doc.head?.appendChild(style)
  } catch {
    // 跨域或浏览器安全策略阻止访问时，保留 iframe 自身的默认滚动行为。
  }
}

// 监听附件变化
watch(() => props.attachment, (newAtt) => {
  // 纪元递增：使所有在途加载任务的后续检查点判定过期
  attachmentEpoch++

  // 重置状态
  textContent.value = ''
  error.value = null
  // Excel/PPT 上一个附件的预览产物
  excelWorkbook.value = null
  excelSheetNames.value = []
  excelActiveSheet.value = 0
  excelTruncated.value = false
  lastDocxPageWidth = 0
  destroyPptx()

  if (!newAtt) {
    // 面板关闭/附件清空：在途任务的过期 finally 不再清 loading，这里兜底熄灭遮罩
    loading.value = false
    return
  }

  // 图片、PDF、HTML 设置 loading
  if (previewType.value === 'image' || previewType.value === 'pdf' || previewType.value === 'html') {
    loading.value = true
    // 递增 key 强制重新挂载媒体元素，避免 src 未变时不触发 load 事件导致一直转圈
    mediaKey.value++
  }

  // 文本文件需要 fetch 内容
  if (previewType.value === 'text' || previewType.value === 'markdown') {
    loadTextContent()
  }

  // DOCX 需要 fetch 并渲染
  if (previewType.value === 'docx') {
    loadDocx()
  }

  // Excel 需要 fetch 并解析渲染
  if (previewType.value === 'excel') {
    loadExcel()
  }

  // PPT 需要 fetch 并渲染
  if (previewType.value === 'pptx') {
    loadPptx()
  }

  // unsupported（旧格式/超限）无加载流程：上一个附件在途任务的过期 finally
  // 不会清 loading，必须在此熄灭遮罩，否则半透明层永久盖住"不支持预览"UI
  // 与下载按钮
  if (previewType.value === 'unsupported') {
    loading.value = false
  }
}, { immediate: true })

// ESC 关闭
function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    emit('close')
  }
}

onMounted(() => {
  document.addEventListener('keydown', handleKeydown)
  // 面板宽度随窗口变化，docx 重排决策可能翻转，需要重渲染
  window.addEventListener('resize', handleWindowResize)
})

onUnmounted(() => {
  attachmentEpoch++ // 组件卸载后，在途任务的过期检查一律失败，结果丢弃
  document.removeEventListener('keydown', handleKeydown)
  window.removeEventListener('resize', handleWindowResize)
  if (previewResizeTimer !== undefined) clearTimeout(previewResizeTimer)
  lastDocxPageWidth = 0
  destroyPptx()
})
</script>

<style scoped>
/* docx-preview：className 选项传 'docx-preview'，库生成的 wrapper 实际类名为 .docx-preview-wrapper */
:deep(.docx-preview-wrapper) {
  background: white;
  padding: 0;
}

/* Excel 表格（设计 §4.3，2026-10-10 验收样式优化）：
   列宽按内容自适应不拉伸铺满、舒适内边距、表头底部分隔线、数据行隔行浅底。
   sheet_to_html 只产出无边框的 td 表格，首行并非 th，用 tr:first-child 呈现表头 */
:deep(.excel-preview-table) {
  border-collapse: collapse;
  font-size: 13px;
  width: max-content;
}
:deep(.excel-preview-table td) {
  @apply border border-gray-200 px-3 py-1.5 text-left whitespace-nowrap;
}
:deep(.excel-preview-table tr:first-child td) {
  @apply bg-gray-50 font-medium text-gray-600 border-b-2 border-gray-300;
}
/* 数据行隔行浅底：宽表横向扫读不串行（表头是第 1 行奇数位，不与之冲突） */
:deep(.excel-preview-table tr:nth-child(even) td) {
  /* gray-50 (#f9fafb) 60% —— @apply 不支持 bg-gray-50/60 的透明度写法，用原生 CSS */
  background-color: rgb(249 250 251 / 0.6);
}

/* PPT 容器（设计 §4.3）：
   - 库内置 wrapper 是内联黑底，预览统一白底（需 !important 覆盖内联样式）
   - 库内置的翻页按钮/页码与面板顶部导航重复，隐藏 */
:deep(.pptx-preview-wrapper) {
  background: #ffffff !important;
}
:deep(.pptx-preview-wrapper-next),
:deep(.pptx-preview-wrapper-pre),
:deep(.pptx-preview-wrapper-pagination) {
  display: none !important;
}
</style>
