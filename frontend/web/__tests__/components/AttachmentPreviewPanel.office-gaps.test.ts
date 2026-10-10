/**
 * AttachmentPreviewPanel Office 预览覆盖缺口补充测试（独立测试轮新增）
 *
 * 补齐设计 §6 中可自动化但既有用例未覆盖的行项：
 * - Excel / PPT 内容容器横向滚动（设计 §4.4 / §6 移动端行项的 jsdom 可自动化部分）：
 *   断言内容容器携带 overflow-auto（滚动能力来自该类；jsdom 无布局，真实滚动归手动验收）
 *
 * 说明（独立测试轮结论，登记备查）：
 * - 设计 §6 行项 1「Word (.docx) 正确渲染」曾尝试在本文件用真实 docx-preview 渲染
 *   sample.docx 断言 DOM 文本，但在 vitest/jsdom 下 jszip（docx-preview 依赖）对
 *   undici Response.blob() 产物的跨 realm instanceof 识别失败而抛
 *   "Can't read the data of the loaded zip file"（浏览器有原生 Blob 不受影响），
 *   故 docx 实际渲染无法在本环境自动化；office.test.ts 对 docx-preview 做 mock、
 *   仅断言 renderAsync 调用参数（含新增的 ignoreWidth 移动端参数）是当前唯一可行做法，
 *   真实渲染由手动验收夹具（sample.docx）覆盖。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'

import AttachmentPreviewPanel from '@/components/AttachmentPreviewPanel.vue'
import { server } from '../mocks/server'
import type { AttachmentInfo } from '@/types'

function makeAttachment(overrides: Partial<AttachmentInfo> = {}): AttachmentInfo {
  return {
    file_id: 'file_scroll_test',
    name: '数据.xlsx',
    size: 1024,
    mime_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    type: 'file',
    ...overrides,
  }
}

async function settle() {
  await flushPromises()
  await new Promise(r => setTimeout(r, 150))
  await flushPromises()
}

describe('AttachmentPreviewPanel —— Office 内容容器滚动类（设计 §4.4 / §6 移动端行项）', () => {
  beforeEach(() => {
    // 分支模板断言不依赖加载成功；404 避免未处理请求外泄
    server.use(http.get('/api/files/:id', () => new HttpResponse(null, { status: 404 })))
  })

  it('Excel 表格容器 overflow-auto（横向滚动是表格自然交互）', async () => {
    const wrapper = mount(AttachmentPreviewPanel, {
      props: { attachment: makeAttachment({
        file_id: 'file_excel_scroll',
        name: '数据.xlsx',
        mime_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      }) },
    })
    await settle()
    const container = wrapper.find('.excel-preview-container')
    expect(container.exists()).toBe(true)
    expect(container.classes()).toContain('overflow-auto')
  })

  it('PPT 视口容器 overflow-auto（缩放后整页可见，细节滚动查看）', async () => {
    const wrapper = mount(AttachmentPreviewPanel, {
      props: { attachment: makeAttachment({
        file_id: 'file_pptx_scroll',
        name: '演示.pptx',
        mime_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      }) },
    })
    await settle()
    const viewport = wrapper.find('.pptx-preview-container').element.parentElement
    expect(viewport).not.toBeNull()
    expect(viewport!.classList.contains('overflow-auto')).toBe(true)
  })
})
