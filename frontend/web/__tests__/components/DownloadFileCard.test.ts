/**
 * DownloadFileCard 组件单测 —— Office 预览放行与大小门槛
 *
 * 验证（设计 §4.1 / §5.2）：
 * - isPreviewable 放行 docx/xlsx/pptx（docx 此前缺失，回归项）
 * - 大小门槛边界：Excel 9.9/10/10.1MB，Word/PPT 19.9/20/20.1MB
 * - .doc/.ppt 旧格式不放行（点击直接下载）
 * - .xls 在门槛内放行（面板侧尝试解析，失败引导下载）
 * - 可预览卡片点击打开预览面板；超限卡片点击不打开面板
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'

import DownloadFileCard from '@/components/DownloadFileCard.vue'
import FileTypeIcon from '@/components/FileTypeIcon.vue'
import { useAttachmentPreview } from '@/composables/useAttachmentPreview'
import type { DownloadableFile } from '@/types'
import { EXCEL_MAX_PREVIEW_BYTES, OFFICE_DOC_MAX_PREVIEW_BYTES } from '@/utils/officePreview'

const MB = 1024 * 1024

function makeFile(overrides: Partial<DownloadableFile> = {}): DownloadableFile {
  return {
    file_id: 'file_office_1',
    file_name: 'report.xlsx',
    file_size: 1024,
    download_url: '/api/files/file_office_1/download',
    mime_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    ...overrides,
  }
}

/** 可预览 = 眼睛图标按钮；不可预览 = 下载图标按钮（模板二选一，按图标 path 区分） */
const EYE_ICON_PATH = 'M15 12a3 3 0 11-6 0 3 3 0 016 0z'
function isPreviewVariant(wrapper: ReturnType<typeof mount>): boolean {
  return wrapper.find('button').html().includes(EYE_ICON_PATH)
}

const { previewAttachment, closePreview } = useAttachmentPreview()

describe('DownloadFileCard.isPreviewable —— Office 三格式放行', () => {
  beforeEach(() => {
    closePreview()
  })

  it('小体积 docx 放行（回归：此前 isPreviewable 不含 docx）', () => {
    const wrapper = mount(DownloadFileCard, { props: { file: makeFile({ file_name: '报告.docx', mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', file_size: 5 * MB }) } })
    expect(isPreviewVariant(wrapper)).toBe(true)
  })

  it('小体积 xlsx 放行', () => {
    const wrapper = mount(DownloadFileCard, { props: { file: makeFile({ file_size: 5 * MB }) } })
    expect(isPreviewVariant(wrapper)).toBe(true)
  })

  it('小体积 pptx 放行', () => {
    const wrapper = mount(DownloadFileCard, { props: { file: makeFile({ file_name: '演示.pptx', mime_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation', file_size: 5 * MB }) } })
    expect(isPreviewVariant(wrapper)).toBe(true)
  })

  it('.xls 在门槛内放行（面板侧尝试解析）', () => {
    const wrapper = mount(DownloadFileCard, { props: { file: makeFile({ file_name: '旧表.xls', mime_type: 'application/vnd.ms-excel', file_size: 1024 }) } })
    expect(isPreviewVariant(wrapper)).toBe(true)
  })

  it('.doc 旧格式不放行（点击直接下载，设计 §2.4）', () => {
    const wrapper = mount(DownloadFileCard, { props: { file: makeFile({ file_name: '旧文档.doc', mime_type: 'application/msword', file_size: 1024 }) } })
    expect(isPreviewVariant(wrapper)).toBe(false)
  })

  it('.ppt 旧格式不放行', () => {
    const wrapper = mount(DownloadFileCard, { props: { file: makeFile({ file_name: '旧演示.ppt', mime_type: 'application/vnd.ms-powerpoint', file_size: 1024 }) } })
    expect(isPreviewVariant(wrapper)).toBe(false)
  })

  it('仅扩展名、无 MIME 也可识别（如 mime 缺失的 xlsx）', () => {
    const wrapper = mount(DownloadFileCard, { props: { file: makeFile({ file_name: '数据.XLSX', mime_type: '', file_size: 1024 }) } })
    expect(isPreviewVariant(wrapper)).toBe(true)
  })
})

describe('DownloadFileCard.isPreviewable —— Excel 10MB 门槛边界', () => {
  beforeEach(() => closePreview())

  it('9.9MB 放行', () => {
    const wrapper = mount(DownloadFileCard, { props: { file: makeFile({ file_size: Math.floor(9.9 * MB) }) } })
    expect(isPreviewVariant(wrapper)).toBe(true)
  })

  it('恰好 10MB 放行（含边界）', () => {
    const wrapper = mount(DownloadFileCard, { props: { file: makeFile({ file_size: EXCEL_MAX_PREVIEW_BYTES }) } })
    expect(isPreviewVariant(wrapper)).toBe(true)
  })

  it('10MB + 1 字节不放行', () => {
    const wrapper = mount(DownloadFileCard, { props: { file: makeFile({ file_size: EXCEL_MAX_PREVIEW_BYTES + 1 }) } })
    expect(isPreviewVariant(wrapper)).toBe(false)
  })

  it('10.1MB 不放行（点击直接下载）', () => {
    const wrapper = mount(DownloadFileCard, { props: { file: makeFile({ file_size: Math.floor(10.1 * MB) }) } })
    expect(isPreviewVariant(wrapper)).toBe(false)
  })
})

describe('DownloadFileCard.isPreviewable —— Word/PPT 20MB 门槛边界', () => {
  beforeEach(() => closePreview())

  it.each([['报告.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'], ['演示.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation']] as const)
  ('%s 19.9MB 放行', (fileName, mimeType) => {
    const wrapper = mount(DownloadFileCard, { props: { file: makeFile({ file_name: fileName, mime_type: mimeType, file_size: Math.floor(19.9 * MB) }) } })
    expect(isPreviewVariant(wrapper)).toBe(true)
  })

  it.each([['报告.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'], ['演示.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation']] as const)
  ('%s 恰好 20MB 放行（含边界）', (fileName, mimeType) => {
    const wrapper = mount(DownloadFileCard, { props: { file: makeFile({ file_name: fileName, mime_type: mimeType, file_size: OFFICE_DOC_MAX_PREVIEW_BYTES }) } })
    expect(isPreviewVariant(wrapper)).toBe(true)
  })

  it.each([['报告.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'], ['演示.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation']] as const)
  ('%s 20MB + 1 字节不放行', (fileName, mimeType) => {
    const wrapper = mount(DownloadFileCard, { props: { file: makeFile({ file_name: fileName, mime_type: mimeType, file_size: OFFICE_DOC_MAX_PREVIEW_BYTES + 1 }) } })
    expect(isPreviewVariant(wrapper)).toBe(false)
  })

  it.each([['报告.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'], ['演示.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation']] as const)
  ('%s 20.1MB 不放行', (fileName, mimeType) => {
    const wrapper = mount(DownloadFileCard, { props: { file: makeFile({ file_name: fileName, mime_type: mimeType, file_size: Math.floor(20.1 * MB) }) } })
    expect(isPreviewVariant(wrapper)).toBe(false)
  })
})

describe('DownloadFileCard 点击行为', () => {
  beforeEach(() => {
    closePreview()
    // 下载点击会请求直链外的 _ticket 等？不会 —— 直链原生下载只建 <a>，不发请求。
    // 这里拦截文件直链以防万一
    server.use(http.get('/api/files/:id', () => new HttpResponse(null, { status: 404 })))
  })

  it('可预览 Office 文件点击后打开预览面板（docx 放行回归）', async () => {
    const file = makeFile({ file_id: 'file_docx_click', file_name: '报告.docx', mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', file_size: 2048 })
    const wrapper = mount(DownloadFileCard, { props: { file } })
    await wrapper.find('button').trigger('click')
    expect(previewAttachment.value).not.toBeNull()
    expect(previewAttachment.value).toMatchObject({
      file_id: 'file_docx_click',
      name: '报告.docx',
      size: 2048,
      mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    })
  })

  it('超限 Excel 点击直接下载，不打开面板', async () => {
    const file = makeFile({ file_id: 'file_oversize', file_name: '超大.xlsx', file_size: OFFICE_DOC_MAX_PREVIEW_BYTES })
    const wrapper = mount(DownloadFileCard, { props: { file } })
    await wrapper.find('button').trigger('click')
    expect(previewAttachment.value).toBeNull()
  })

  it('超限 PPT 点击直接下载，不打开面板', async () => {
    const file = makeFile({
      file_id: 'file_oversize_ppt',
      file_name: '超大.pptx',
      mime_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      file_size: Math.floor(20.1 * MB),
    })
    const wrapper = mount(DownloadFileCard, { props: { file } })
    await wrapper.find('button').trigger('click')
    expect(previewAttachment.value).toBeNull()
  })

  it('Office 文件显示对应类型标签（Excel 表格 / PPT 演示 / Word 文档）', () => {
    const xlsx = mount(DownloadFileCard, { props: { file: makeFile({}) } })
    expect(xlsx.text()).toContain('Excel 表格')
    const pptx = mount(DownloadFileCard, { props: { file: makeFile({ file_name: '演示.pptx' }) } })
    expect(pptx.text()).toContain('PPT 演示')
    const docx = mount(DownloadFileCard, { props: { file: makeFile({ file_name: '报告.docx' }) } })
    expect(docx.text()).toContain('Word 文档')
  })

  it('Office 文件显示对应类型图标徽标（FileTypeIcon：word/excel/ppt 一眼可辨）', () => {
    const docx = mount(DownloadFileCard, { props: { file: makeFile({ file_name: '报告.docx', mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }) } })
    expect(docx.findComponent(FileTypeIcon).props('kind')).toBe('word')
    const xlsx = mount(DownloadFileCard, { props: { file: makeFile({}) } })
    expect(xlsx.findComponent(FileTypeIcon).props('kind')).toBe('excel')
    const pptx = mount(DownloadFileCard, { props: { file: makeFile({ file_name: '演示.pptx', mime_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation' }) } })
    expect(pptx.findComponent(FileTypeIcon).props('kind')).toBe('ppt')
  })
})
