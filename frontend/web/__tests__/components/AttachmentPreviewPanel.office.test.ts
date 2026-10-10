/**
 * AttachmentPreviewPanel 组件单测 —— Office 三格式预览（Excel / PPT / Word）
 *
 * 验证（设计 §4.2 / §5.2 / §2.4）：
 * - previewType 分支：xlsx→excel、pptx→pptx、xls→excel、doc→unsupported、超限→unsupported+文件较大文案
 * - Excel 真实渲染：node 侧读 test_uploads/office_preview 夹具经 MSW 下发，
 *   断言表格 DOM、Sheet Tab 数量与切换
 * - 1000 行截断：bigrows.xlsx（1251 行）只渲染 1000 行 + 顶部截断提示
 * - PPT 真实渲染：sample.pptx 渲染 4 页，翻页导航与首页/末页禁用
 * - 加载失败降级：错误提示 + 下载按钮（复用现有错误 UI）
 * - docx 移动端 ignoreWidth（docx-preview mock，仅断言调用参数）
 */
import { describe, it, expect, beforeEach, beforeAll, afterEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { http, HttpResponse } from 'msw'

// docx-preview 与渲染质量无关，此处只断言 renderAsync 调用参数（移动端 ignoreWidth）
vi.mock('docx-preview', () => ({ renderAsync: vi.fn() }))

import { renderAsync } from 'docx-preview'
import AttachmentPreviewPanel from '@/components/AttachmentPreviewPanel.vue'
import { server } from '../mocks/server'
import type { AttachmentInfo } from '@/types'
import { EXCEL_MAX_PREVIEW_BYTES, OFFICE_DOC_MAX_PREVIEW_BYTES } from '@/utils/officePreview'

// pptx-preview 首次动态 import 会牵引 echarts 大依赖，vite-node 下需要真实耗时，
// 排空微任务不够 —— 文件级预热一次，后续组件内 import 走模块缓存
beforeAll(async () => {
  await import('pptx-preview')
})

const FIXTURE_DIR = resolve(process.cwd(), '../test_uploads/office_preview')
const MULTISHEET_XLSX = readFileSync(resolve(FIXTURE_DIR, 'multisheet.xlsx'))
const BIGROWS_XLSX = readFileSync(resolve(FIXTURE_DIR, 'bigrows.xlsx'))
const SAMPLE_PPTX = readFileSync(resolve(FIXTURE_DIR, 'sample.pptx'))
const SAMPLE_DOCX = readFileSync(resolve(FIXTURE_DIR, 'sample.docx'))

/** Buffer → 独立 ArrayBuffer（MSW arrayBuffer 响应体要求） */
function toArrayBuffer(buf: Buffer): ArrayBuffer {
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer
}

function makeAttachment(overrides: Partial<AttachmentInfo> = {}): AttachmentInfo {
  return {
    file_id: 'file_office_test',
    name: '数据.xlsx',
    size: 1024,
    mime_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    type: 'file',
    ...overrides,
  }
}

/** 让 MSW 按 fileId 下发夹具字节 */
function serveFile(fileId: string, body: Buffer) {
  const arrayBuffer = toArrayBuffer(body)
  server.use(http.get(`/api/files/${fileId}`, () => HttpResponse.arrayBuffer(arrayBuffer)))
}

/** 让 MSW 延迟 delayMs 下发夹具字节（模拟慢网络，用于附件切换竞态）。
 * 注：HttpResponse init 的 delay 选项在 msw 2.13.4 对 arrayBuffer 不生效，用异步 resolver 实现 */
function serveFileDelayed(fileId: string, body: Buffer, delayMs: number) {
  const arrayBuffer = toArrayBuffer(body)
  server.use(http.get(`/api/files/${fileId}`, async () => {
    await new Promise(resolve => setTimeout(resolve, delayMs))
    return HttpResponse.arrayBuffer(arrayBuffer)
  }))
}

/** 让 MSW 对该文件返回 404（错误降级路径） */
function failFile(fileId: string) {
  server.use(http.get(`/api/files/${fileId}`, () => new HttpResponse(null, { status: 404 })))
}

function mountPanel(attachment: AttachmentInfo | null) {
  return mount(AttachmentPreviewPanel, { props: { attachment } })
}

async function settle() {
  // 动态 import + fetch + arrayBuffer + nextTick 链路：
  // 两轮微任务排空为主，补一个真实定时器等待兜底模块加载的墙钟耗时
  await flushPromises()
  await new Promise(r => setTimeout(r, 150))
  await flushPromises()
}

/**
 * 轮询等待条件成立（全量并发跑测时机器负载高，pptx-preview 的
 * import/解析/渲染需要真实墙钟时间，固定 sleep 不稳定）
 */
async function waitFor(cond: () => boolean, timeoutMs = 8000, stepMs = 100): Promise<boolean> {
  const start = Date.now()
  while (!cond()) {
    if (Date.now() - start > timeoutMs) return false
    await new Promise(r => setTimeout(r, stepMs))
    await flushPromises()
  }
  return true
}

describe('AttachmentPreviewPanel.previewType 分支', () => {
  beforeEach(() => {
    // 默认 404，避免未处理请求打到真实网络；分支断言不依赖加载成功
    server.use(http.get('/api/files/:id', () => new HttpResponse(null, { status: 404 })))
  })

  it('xlsx → excel 分支（Excel 表格容器渲染）', async () => {
    const wrapper = mountPanel(makeAttachment({ name: '数据.xlsx' }))
    await settle()
    // Excel 分支模板渲染（Sheet Tab 栏按数据出现，此处断言容器存在性）
    expect(wrapper.find('.excel-preview-container').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('此文件类型无法预览')
  })

  it('xls → excel 分支（尝试解析，失败引导下载，设计 §2.4）', async () => {
    const wrapper = mountPanel(makeAttachment({ name: '旧表.xls', mime_type: 'application/vnd.ms-excel' }))
    await settle()
    // 404 → 解析失败 → 复用现有错误 UI：提示 + 下载按钮
    expect(await waitFor(() => wrapper.text().includes('表格预览加载失败，请尝试下载查看'))).toBe(true)
    expect(wrapper.text()).toContain('下载文件')
  })

  it('pptx → pptx 分支（幻灯片容器渲染），加载失败走错误 UI', async () => {
    const wrapper = mountPanel(makeAttachment({
      name: '演示.pptx',
      mime_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    }))
    await settle()
    expect(wrapper.find('.pptx-preview-container').exists()).toBe(true)
    // Promise.all 需等 import 腿完成（并发跑测时冷加载更慢），轮询等待错误文案出现
    expect(await waitFor(() => wrapper.text().includes('演示文稿预览加载失败，请尝试下载查看'))).toBe(true)
    // 总页数为 0（未渲染成功）时不显示翻页导航
    expect(wrapper.find('button[title="上一页"]').exists()).toBe(false)
  })

  it('docx → docx 分支（renderAsync 被调用，无错误）', async () => {
    serveFile('file_office_test', SAMPLE_DOCX)
    const wrapper = mountPanel(makeAttachment({
      name: '报告.docx',
      mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    }))
    await settle()
    expect(await waitFor(() => vi.mocked(renderAsync).mock.calls.length === 1)).toBe(true)
    expect(wrapper.text()).not.toContain('文档预览加载失败')
  })

  it('.doc 旧格式 → unsupported（此文件类型无法预览 + 下载按钮）', async () => {
    const wrapper = mountPanel(makeAttachment({ name: '旧文档.doc', mime_type: 'application/msword' }))
    await settle()
    expect(wrapper.text()).toContain('此文件类型无法预览')
    expect(wrapper.text()).toContain('下载文件')
    expect(wrapper.find('.pptx-preview-container').exists()).toBe(false)
  })

  it('.ppt 旧格式 → unsupported', async () => {
    const wrapper = mountPanel(makeAttachment({ name: '旧演示.ppt', mime_type: 'application/vnd.ms-powerpoint' }))
    await settle()
    expect(wrapper.text()).toContain('此文件类型无法预览')
  })

  it('超限 Excel（>10MB）→ unsupported + 文件较大文案（设计 §5.2）', async () => {
    const wrapper = mountPanel(makeAttachment({ name: '超大.xlsx', size: EXCEL_MAX_PREVIEW_BYTES + 1 }))
    await settle()
    expect(wrapper.text()).toContain('文件较大，建议下载查看')
    expect(wrapper.text()).toContain('下载文件')
  })

  it('超限 PPT（>20MB）→ unsupported + 文件较大文案', async () => {
    const wrapper = mountPanel(makeAttachment({
      name: '超大.pptx',
      size: OFFICE_DOC_MAX_PREVIEW_BYTES + 1,
      mime_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    }))
    await settle()
    expect(wrapper.text()).toContain('文件较大，建议下载查看')
  })

  it('门槛内的 Office 文件不显示"文件较大"文案（xlsx 正常进入 excel 分支）', async () => {
    const wrapper = mountPanel(makeAttachment({ name: '正常.xlsx', size: EXCEL_MAX_PREVIEW_BYTES }))
    await settle()
    expect(wrapper.text()).not.toContain('文件较大，建议下载查看')
    expect(wrapper.text()).not.toContain('此文件类型无法预览')
  })
})

describe('AttachmentPreviewPanel —— Excel 真实渲染（multisheet.xlsx，3 Sheet）', () => {
  beforeEach(() => {
    serveFile('file_multisheet', MULTISHEET_XLSX)
  })

  function mountMultisheet() {
    return mountPanel(makeAttachment({ file_id: 'file_multisheet', name: '多表.xlsx' }))
  }

  it('渲染表格 DOM、内容与 3 个 Sheet Tab，默认激活第一个', async () => {
    const wrapper = mountMultisheet()
    await settle()

    // 表格存在且带样式钩子类；首个 Sheet 内容渲染（轮询兜底并发负载）
    expect(await waitFor(() => wrapper.find('table.excel-preview-table').exists())).toBe(true)
    const table = wrapper.find('table.excel-preview-table')
    expect(await waitFor(() => table.text().includes('2026 年 Q3 项目预算总览'))).toBe(true)

    // 3 个 Sheet Tab
    const tabTexts = wrapper.findAll('button').map(b => b.text())
    expect(tabTexts).toContain('项目概览')
    expect(tabTexts).toContain('成员名单')
    expect(tabTexts).toContain('月度统计')

    // 22 行（A1:E22）小表不触发截断提示
    expect(wrapper.text()).not.toContain('仅显示前 1000 行')
  })

  it('点击 Sheet Tab 切换渲染内容', async () => {
    const wrapper = mountMultisheet()
    await settle()

    // Tab 在工作簿解析完成后才渲染
    expect(await waitFor(() => wrapper.findAll('button').some(b => b.text() === '成员名单'))).toBe(true)
    const memberTab = wrapper.findAll('button').find(b => b.text() === '成员名单')!
    await memberTab.trigger('click')

    const table = wrapper.find('table.excel-preview-table')
    expect(await waitFor(() => table.text().includes('王浩静'))).toBe(true)
    expect(table.text()).toContain('研发中心成员名单')
    // 切换后原 Sheet 内容被替换
    expect(table.text()).not.toContain('2026 年 Q3 项目预算总览')
  })
})

describe('AttachmentPreviewPanel —— Excel 1000 行截断（bigrows.xlsx，1251 行）', () => {
  beforeEach(() => {
    serveFile('file_bigrows', BIGROWS_XLSX)
  })

  it('只渲染前 1000 行并显示截断提示', async () => {
    const wrapper = mountPanel(makeAttachment({ file_id: 'file_bigrows', name: '大表.xlsx' }))
    await settle()

    // 显眼截断提示（设计 §5.2 指定文案，轮询等待渲染完成）
    expect(await waitFor(() => wrapper.text().includes('仅显示前 1000 行，完整内容请下载'))).toBe(true)

    // 表格恰好 1000 行
    const rows = wrapper.findAll('table.excel-preview-table tr')
    expect(rows).toHaveLength(1000)

    // 内容断言：首行表头 + 数据首行存在
    expect(wrapper.find('table.excel-preview-table').text()).toContain('订单编号')

    // 第 1001 行不存在（SheetJS 单元格 id 以 sjs- 为前缀）
    const html = wrapper.find('table.excel-preview-table').html()
    expect(html).toContain('sjs-I1000')
    expect(html).not.toContain('sjs-I1001')
  })

  it('切换附件后截断状态随附件重置', async () => {
    serveFile('file_multisheet', MULTISHEET_XLSX)
    const wrapper = mountPanel(makeAttachment({ file_id: 'file_bigrows', name: '大表.xlsx' }))
    await settle()
    expect(await waitFor(() => wrapper.text().includes('仅显示前 1000 行'))).toBe(true)

    await wrapper.setProps({ attachment: makeAttachment({ file_id: 'file_multisheet', name: '多表.xlsx' }) })
    // 新附件 22 行：无截断提示，表格为多 Sheet 内容
    expect(await waitFor(() => wrapper.find('table.excel-preview-table').text().includes('2026 年 Q3 项目预算总览'))).toBe(true)
    expect(wrapper.text()).not.toContain('仅显示前 1000 行')
  })
})

describe('AttachmentPreviewPanel —— Excel 加载失败降级', () => {
  it('fetch 404 → 错误提示 + 下载引导', async () => {
    failFile('file_excel_dead')
    const wrapper = mountPanel(makeAttachment({ file_id: 'file_excel_dead', name: '坏文件.xlsx' }))
    await settle()
    expect(await waitFor(() => wrapper.text().includes('表格预览加载失败，请尝试下载查看'))).toBe(true)
    // 下载按钮指向直链下载地址
    const downloadLink = wrapper.find('a[download]')
    expect(downloadLink.exists()).toBe(true)
    expect(downloadLink.attributes('href')).toContain('/api/files/file_excel_dead/download')
  })
})

describe('AttachmentPreviewPanel —— PPT 真实渲染（sample.pptx，4 页 4:3）', () => {
  beforeEach(() => {
    serveFile('file_pptx', SAMPLE_PPTX)
  })

  function mountPptx() {
    return mountPanel(makeAttachment({
      file_id: 'file_pptx',
      name: '演示.pptx',
      mime_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    }))
  }

  it('真实渲染幻灯片并驱动翻页导航（首页/末页禁用）', async () => {
    const wrapper = mountPptx()

    // pptx-preview 实际渲染出第一页 DOM（轮询等待，全量并发跑测时更慢）
    const rendered = await waitFor(() => wrapper.find('.pptx-preview-slide-wrapper').exists())
    expect(rendered).toBe(true)
    expect(wrapper.text()).not.toContain('演示文稿预览加载失败')

    // 导航显示 1 / 4；首页上一页禁用、下一页可用
    expect(await waitFor(() => wrapper.text().includes('1 / 4'))).toBe(true)
    const prev = wrapper.find('button[title="上一页"]')
    const next = wrapper.find('button[title="下一页"]')
    expect(prev.attributes()).toHaveProperty('disabled')
    expect(next.attributes()).not.toHaveProperty('disabled')

    // 下一页 → 2 / 4
    await next.trigger('click')
    expect(await waitFor(() => wrapper.text().includes('2 / 4'))).toBe(true)
    expect(wrapper.find('button[title="上一页"]').attributes()).not.toHaveProperty('disabled')

    // 连续翻到末页 4 / 4，下一页禁用
    await wrapper.find('button[title="下一页"]').trigger('click')
    expect(await waitFor(() => wrapper.text().includes('3 / 4'))).toBe(true)
    await wrapper.find('button[title="下一页"]').trigger('click')
    expect(await waitFor(() => wrapper.text().includes('4 / 4'))).toBe(true)
    expect(wrapper.find('button[title="下一页"]').attributes()).toHaveProperty('disabled')

    // 末页回退 → 3 / 4
    await wrapper.find('button[title="上一页"]').trigger('click')
    expect(await waitFor(() => wrapper.text().includes('3 / 4'))).toBe(true)
  })
})

describe('AttachmentPreviewPanel —— 附件快速切换竞态守卫', () => {
  it('Excel：慢响应的旧附件后到达，不覆盖新附件的表格与状态', async () => {
    // A=multisheet 慢 400ms；B=bigrows 立即返回
    serveFileDelayed('file_race_slow', MULTISHEET_XLSX, 400)
    serveFile('file_race_fast', BIGROWS_XLSX)

    const wrapper = mountPanel(makeAttachment({ file_id: 'file_race_slow', name: '慢表.xlsx' }))
    // 确保 A 的 fetch 已在途
    await new Promise(r => setTimeout(r, 50))
    await wrapper.setProps({ attachment: makeAttachment({ file_id: 'file_race_fast', name: '快表.xlsx' }) })

    // B（bigrows）先渲染：截断提示出现
    expect(await waitFor(() => wrapper.text().includes('仅显示前 1000 行，完整内容请下载'))).toBe(true)

    // A 的响应在其后（>400ms）到达——过期结果应被丢弃
    await new Promise(r => setTimeout(r, 600))
    await flushPromises()

    // 直接断言（B 已在上面 waitFor 确认渲染完成）：内容仍是 B（bigrows），未被 A 覆盖
    const table = wrapper.find('table.excel-preview-table')
    expect(table.text()).toContain('订单编号')
    // 旧附件（multisheet）的内容与 Tab 未覆盖新状态
    expect(table.text()).not.toContain('2026 年 Q3 项目预算总览')
    expect(wrapper.findAll('button').map(b => b.text())).not.toContain('项目概览')
    expect(wrapper.text()).not.toContain('表格预览加载失败')
  })

  it('PPT：慢响应的旧附件后到达，不挤出/叠放到新附件的预览器', async () => {
    serveFileDelayed('file_pptx_race_slow', SAMPLE_PPTX, 500)
    serveFile('file_pptx_race_fast', SAMPLE_PPTX)

    const wrapper = mountPanel(makeAttachment({
      file_id: 'file_pptx_race_slow',
      name: '慢演示.pptx',
      mime_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    }))
    await new Promise(r => setTimeout(r, 50))
    await wrapper.setProps({ attachment: makeAttachment({
      file_id: 'file_pptx_race_fast',
      name: '快演示.pptx',
      mime_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    }) })

    // B 先渲染完成；两个夹具同文件，给 B 的 wrapper 打标记以便区分归属
    expect(await waitFor(() => wrapper.text().includes('1 / 4'))).toBe(true)
    const containerEl = wrapper.get('.pptx-preview-container').element
    const bWrapper = containerEl.querySelector('.pptx-preview-wrapper')
    expect(bWrapper).toBeTruthy()
    bWrapper!.setAttribute('data-test-owner', 'B')

    // A 的响应在 500ms 后到达——不得重建/叠放 wrapper、不得报错
    await new Promise(r => setTimeout(r, 700))
    await flushPromises()

    const containers = wrapper.findAll('.pptx-preview-container')
    expect(containers).toHaveLength(1)
    // 容器内库 wrapper 恰好一个（无叠放），且仍是 B 渲染的那个（未被旧任务挤出重建）
    const wrappers = containers[0].element.querySelectorAll('.pptx-preview-wrapper')
    expect(wrappers).toHaveLength(1)
    expect(wrappers[0].getAttribute('data-test-owner')).toBe('B')
    expect(wrapper.text()).toContain('1 / 4')
    expect(wrapper.text()).not.toContain('演示文稿预览加载失败')
  })
})

describe('AttachmentPreviewPanel —— 切到不可预览类型时 loading 遮罩必须熄灭', () => {
  it('在途 Excel 未完成时切到 .doc：遮罩熄灭，unsupported UI 与下载按钮不再被盖住', async () => {
    serveFileDelayed('file_stuck_slow', MULTISHEET_XLSX, 400)
    const wrapper = mountPanel(makeAttachment({ file_id: 'file_stuck_slow', name: '慢表.xlsx' }))
    // A 的 fetch 在途，loading 遮罩显示中
    await new Promise(r => setTimeout(r, 50))
    expect(wrapper.text()).toContain('加载中')

    // 切到旧格式 .doc（unsupported，无加载流程）
    await wrapper.setProps({ attachment: makeAttachment({
      file_id: 'file_doc_old', name: '旧文档.doc', mime_type: 'application/msword',
    }) })
    await flushPromises()

    // 遮罩必须熄灭，unsupported UI 与下载按钮可交互
    expect(wrapper.text()).not.toContain('加载中')
    expect(wrapper.text()).toContain('此文件类型无法预览')
    expect(wrapper.find('a[download]').exists()).toBe(true)

    // 慢响应在其后（>400ms）到达：过期任务不得翻转任何状态，遮罩保持熄灭
    await new Promise(r => setTimeout(r, 600))
    await flushPromises()
    expect(wrapper.text()).not.toContain('加载中')
    expect(wrapper.text()).toContain('此文件类型无法预览')
  })

  it('在途加载时附件清空（null）：遮罩同样熄灭', async () => {
    serveFileDelayed('file_stuck_null', MULTISHEET_XLSX, 400)
    const wrapper = mountPanel(makeAttachment({ file_id: 'file_stuck_null', name: '慢表.xlsx' }))
    await new Promise(r => setTimeout(r, 50))
    expect(wrapper.text()).toContain('加载中')

    await wrapper.setProps({ attachment: null })
    await flushPromises()
    expect(wrapper.text()).not.toContain('加载中')
  })
})

describe('AttachmentPreviewPanel —— docx 整页缩放（仿 PPT，按容器内容宽度缩放整页可见）', () => {
  beforeEach(() => {
    vi.mocked(renderAsync).mockReset()
    // 模拟 docx-preview 的真实 DOM 结构：先注入 <style>（styleContainer 缺省即渲染容器本身），
    // 再追加 wrapper（类名 {className}-wrapper）+ 带内联页宽的 section。
    // 真实库会把 <style> 放在容器首位——缩放定位必须按类名而非 firstElementChild（回归防护）
    vi.mocked(renderAsync).mockImplementation(async (_data, container) => {
      const style = document.createElement('style')
      style.textContent = '.docx-preview-wrapper {}'
      ;(container as HTMLElement).appendChild(style)
      const wrapper = document.createElement('div')
      wrapper.className = 'docx-preview-wrapper'
      const section = document.createElement('section')
      section.style.width = '794px'
      wrapper.appendChild(section)
      ;(container as HTMLElement).appendChild(wrapper)
    })
  })

  let originalDescriptor: PropertyDescriptor | undefined

  /** stub 所有元素的 clientWidth（docx 容器内容宽度 = clientWidth - 32 p-4 内边距） */
  function setContainerClientWidth(width: number) {
    originalDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
      configurable: true,
      get: () => width,
    })
  }

  afterEach(() => {
    if (originalDescriptor) {
      Object.defineProperty(HTMLElement.prototype, 'clientWidth', originalDescriptor)
    } else {
      delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth
    }
    originalDescriptor = undefined
  })

  it.each([
    { clientWidth: 490, zoom: '0.577', label: '窄面板（桌面窗口但面板 <A4 页宽）整页等比缩放，内容完整可见' },
    { clientWidth: 375, zoom: '0.432', label: '手机宽度面板整页缩放' },
    { clientWidth: 1100, zoom: '1', label: '宽容器不放大，保持原始页宽' },
  ])('$label', async ({ clientWidth, zoom }) => {
    serveFile('file_docx', readFileSync(resolve(FIXTURE_DIR, 'sample.docx')))
    setContainerClientWidth(clientWidth)
    const wrapper = mountPanel(makeAttachment({
      file_id: 'file_docx',
      name: '报告.docx',
      mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    }))
    await settle()

    expect(await waitFor(() => vi.mocked(renderAsync).mock.calls.length === 1)).toBe(true)
    const options = vi.mocked(renderAsync).mock.calls[0][3]
    // 整页缩放模式：保留原始页宽与版式（不做 ignoreWidth 重排），缩放交给 CSS zoom
    expect(options?.ignoreWidth).toBe(false)
    const zoomed = wrapper.element.querySelector('.docx-preview-wrapper') as HTMLElement | null
    expect(zoomed?.style.zoom).toBe(zoom)
    expect(wrapper.text()).not.toContain('文档预览加载失败')
  })
})
