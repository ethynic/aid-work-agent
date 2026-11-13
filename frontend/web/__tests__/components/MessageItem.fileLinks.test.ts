import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import MessageItem from '@/components/MessageItem.vue'
import { replyFileId } from '@/utils/replyResources'

vi.mock('@/composables/useAgent', () => ({ useAgent: () => ({ liveVerbose: { value: null }, sessionId: { value: 'session-a' } }) }))
vi.mock('@/composables/useTenantAuth', () => ({ useTenantAuth: () => ({ getAuthHeader: () => ({}) }) }))
vi.mock('@/platform/runtime', () => ({ getRuntime: () => ({ target: 'web' }) }))

describe('reply markdown file references resolve instead of hijacking the router', () => {
  it('recognizes bare file ids and file_id: scheme, rejects other paths', () => {
    expect(replyFileId('file_3780abab9efc')).toBe('file_3780abab9efc')
    expect(replyFileId('file_3780abab9efc.docx')).toBe('file_3780abab9efc.docx')
    expect(replyFileId('/api/files/file_3780abab9efc/download')).toBe('file_3780abab9efc')
    expect(replyFileId('https://example.com/file_3780abab9efc')).toBe(null)
    expect(replyFileId('/t/tenant1/chat/file_3780abab9efc')).toBe(null)
    expect(replyFileId('file_wrong')).toBe(null)
  })

  it('converts bare file id links in reply text to download URLs', () => {
    const wrapper = mount(MessageItem, { props: { message: {
      role: 'assistant',
      content: '[供应商资质审查报告.docx](file_3780abab9efc)\n\n[方案](file_id:file_abc123def456)\n\n[来源](https://example.org)',
      timestamp: Date.now(),
    } } })
    const hrefs = wrapper.findAll('a').map(a => a.attributes('href'))
    expect(hrefs).toEqual([
      '/api/files/file_3780abab9efc/download',
      '/api/files/file_abc123def456/download',
      'https://example.org',
    ])
    wrapper.unmount()
  })
})
