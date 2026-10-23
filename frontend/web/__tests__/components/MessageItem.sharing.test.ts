import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import MessageItem from '@/components/MessageItem.vue'
import { mergeRunnerMessages } from '@/utils/runnerMessages'
import { runnerView } from '../mocks/runnerView'

vi.mock('@/composables/useAgent', () => ({ useAgent: () => ({ liveVerbose: { value: null }, sessionId: { value: 'session-a' } }) }))
vi.mock('@/composables/useTenantAuth', () => ({ useTenantAuth: () => ({ getAuthHeader: () => ({}) }) }))
vi.mock('@/platform/runtime', () => ({ getRuntime: () => ({ target: 'web' }) }))

describe('share exactly the reply shown in the conversation', () => {
  it('uses the final Runner projection when history reuses an intermediate message ID for an attachment-only reply', async () => {
    const row = runnerView({ status: 'completed', result: { output: '', assistant_metadata: {
      downloadableFiles: [{ file_id: 'file_report', file_name: 'report.pdf', file_size: 4, download_url: '/api/files/file_report/download', mime_type: 'application/pdf' }],
    } } as any })
    const message = mergeRunnerMessages([{ role: 'assistant', runnerId: row.runner_id,
      messageId: 'intermediate-history-id', content: 'Preparing report' }], [row]).slice(-1)[0]!
    expect(message.messageId).toBe('intermediate-history-id')
    const wrapper = mount(MessageItem, { props: { message }, global: { stubs: {
      MessageShareDialog: { name: 'MessageShareDialog', props: ['sessionId', 'messageId'], template: '<div />' },
    } } })
    await wrapper.get('button[aria-label="复制分享链接"]').trigger('click')
    await flushPromises()
    expect(wrapper.getComponent({ name: 'MessageShareDialog' }).props('messageId')).toBe(row.runner_id + ':assistant')
    wrapper.unmount()
  })

  it('keeps the share action and timestamp in one compact row with an accessible icon', () => {
    const wrapper = mount(MessageItem, { props: { message: {
      role: 'assistant', messageId: 'reply-a', content: 'Selected reply', timestamp: Date.now(),
    } } })
    const action = wrapper.get('button[aria-label="复制分享链接"]')
    expect(action.find('svg[aria-hidden="true"]').exists()).toBe(true)
    expect(action.element.parentElement?.textContent).toMatch(/\d{2}:\d{2}.*分享/)
    wrapper.unmount()
  })
})
