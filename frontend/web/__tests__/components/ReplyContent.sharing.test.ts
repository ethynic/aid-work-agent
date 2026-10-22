import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ReplyContent from '@/components/chat/ReplyContent.vue'
import AttachmentPreviewPanel from '@/components/AttachmentPreviewPanel.vue'
import { useAttachmentPreview } from '@/composables/useAttachmentPreview'
import { replyFileResolverKey } from '@/utils/replyResources'
import { renderMarkdown } from '@/utils/markdown'
import type { ChatMessage } from '@/types'

const resource = (id: string, download = false) => '/api/public/reply-shares/example/files/' + id + '?ticket=fixture' + (download ? '&download=1' : '')
const message: ChatMessage = {
  role: 'assistant', content: '# 报告\n\n|项目|值|\n|---|---|\n|收入|100|\n\n![内嵌图](file_id:asset_0)\n\n[来源](https://example.org)\n\n```js\nconst total = 100\n```',
  images: [{ file_id: 'asset_0', download_url: '/api/files/asset_0/download', display_name: '趋势图', mime_type: 'image/png', size_bytes: 100, source: 'tool_generated', usage: 'inline', placement: 'before_text' }],
  attachments: [{ file_id: 'asset_1', name: '报告.png', size: 100, type: 'image', mime_type: 'image/png' }],
  downloadableFiles: [{ file_id: 'asset_2', file_name: '数据.zip', file_size: 30, download_url: '/api/files/asset_2/download' }],
}
afterEach(() => { useAttachmentPreview().closePreview(); vi.restoreAllMocks() })

describe('one reply renderer for chat and sharing', () => {
  it('keeps tables, code, links, gallery images, inline images and every attachment', () => {
    const wrapper = mount(ReplyContent, { props: { message }, global: { provide: { [replyFileResolverKey as symbol]: resource } } })
    expect(wrapper.findAll('td').map(cell => cell.text())).toEqual(['收入', '100'])
    expect(wrapper.find('pre code').text()).toContain('const total = 100')
    expect(wrapper.find('a').attributes('href')).toBe('https://example.org')
    expect(wrapper.findAll('img')).toHaveLength(2)
    for (const image of wrapper.findAll('img')) expect(image.attributes('src')).toBe(resource('asset_0'))
    expect(wrapper.text()).toContain('报告.png')
    expect(wrapper.text()).toContain('数据.zip')
    wrapper.unmount()
  })

  it('renders image-only and file-only replies instead of hiding their contents', () => {
    for (const content of [
      { role: 'assistant', content: '', images: message.images },
      { role: 'assistant', content: '', downloadableFiles: message.downloadableFiles },
    ] as ChatMessage[]) {
      const wrapper = mount(ReplyContent, { props: { message: content } })
      expect(wrapper.findAll('img').length + wrapper.findAll('button').length).toBeGreaterThan(0)
      wrapper.unmount()
    }
  })

  it('opens the existing attachment preview with shared URLs and downloads through the same authorization', async () => {
    const provide = { [replyFileResolverKey as symbol]: resource }
    const wrapper = mount(ReplyContent, { props: { message }, global: { provide } })
    await wrapper.findAll('button').find(button => button.text().includes('报告.png'))!.trigger('click')
    const preview = mount(AttachmentPreviewPanel, { props: { attachment: useAttachmentPreview().previewAttachment.value }, global: { provide } })
    expect(preview.find('img').attributes('src')).toBe(resource('asset_1'))
    expect(preview.find('a[download]').attributes('href')).toBe(resource('asset_1', true))
    const urls: string[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { urls.push(this.getAttribute('href') || '') })
    await wrapper.findAll('button').find(button => button.text().includes('数据.zip'))!.trigger('click')
    expect(urls).toContain(resource('asset_2', true))
    preview.unmount(); wrapper.unmount()
  })

  it('keeps normal formatting and media while removing executable HTML in the common renderer', () => {
    const html = renderMarkdown('<img src="/api/files/asset_0/download" onerror="alert(1)"><script>alert(1)</script>\n[bad](javascript:alert(1))', resource)
    expect(html).toContain(resource('asset_0'))
    expect(html).not.toContain('onerror')
    expect(html).not.toContain('<script')
    expect(html).not.toContain('href="javascript:')
  })
})
