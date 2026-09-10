/**
 * ContentBlockEditor 图片块关键路径测试（P4-A，R57）
 *
 * 1. 加图片：打开素材库 → 选择 → 追加 {type:'image', asset_id} 块（判别联合）；
 * 2. 更换图片：既有图片块位置替换 asset_id（不新增块）；
 * 3. 图片块校验：空 asset_id 报错（validateBlock image 分支）；
 * 4. 移动/删除含图片块的混排顺序；
 * 5. 图片块预览：asset_id → blob URL（fetchAssetObjectUrl 鉴权 fetch mock）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

vi.mock('vue-toastification', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}))

import ContentBlockEditor from '@/components/weixinMarketing/ContentBlockEditor.vue'
import type { AssetItem, ContentBlockSpec } from '@/api/weixinMarketing'

const ASSET_A = 'aaaaaaaa-1111-4111-8111-111111111111'
const ASSET_B = 'bbbbbbbb-2222-4222-8222-222222222222'

function assetMeta(id: string): AssetItem {
  return {
    id, mime: 'image/png', size: 1024, width: 120, height: 80,
    status: 'active', reference_count: 1, created_at: '2026-09-01T08:00:00Z',
  }
}

function envelope(data: unknown) {
  return { ok: true, status: 200, json: async () => ({ success: true, data }) } as unknown as Response
}

function blobResponse() {
  return { ok: true, status: 200, blob: async () => new Blob(['x'], { type: 'image/png' }) } as unknown as Response
}

function mountEditor(blocks: ContentBlockSpec[] = []) {
  return mount(ContentBlockEditor, {
    props: { blocks, disabled: false },
    attachTo: document.body,
    global: { stubs: { teleport: true } },
  })
}

beforeEach(() => {
  vi.stubGlobal(
    'URL',
    Object.assign(URL, {
      createObjectURL: vi.fn(() => `blob:mock-${Math.random().toString(16).slice(2)}`),
      revokeObjectURL: vi.fn(),
    }),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('ContentBlockEditor 图片块', () => {
  it('加图片 → 素材库选择 → 追加 image 块（判别联合）', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: any) => {
      const url = String(input)
      if (url.includes('/content')) return blobResponse()
      return envelope(assetMeta(ASSET_A))
    })
    const wrapper = mountEditor([{ type: 'text', text_content: '文字' }])

    // 打开素材库（子组件 AssetLibrary）
    const addButton = wrapper.findAll('button').find(b => b.text().includes('加图片'))!
    await addButton.trigger('click')
    const library = wrapper.findComponent({ name: 'AssetLibrary' })
    expect(library.exists()).toBe(true)
    expect((library.props() as { modelValue: boolean }).modelValue).toBe(true)

    // 模拟素材库回传选择（父组件回填 blocks —— update:blocks 单向数据流）
    library.vm.$emit('selected', assetMeta(ASSET_A))
    await flushPromises()

    const emitted = wrapper.emitted('update:blocks')
    expect(emitted).toBeTruthy()
    const blocks = emitted![emitted!.length - 1][0] as ContentBlockSpec[]
    expect(blocks).toEqual([
      { type: 'text', text_content: '文字' },
      { type: 'image', asset_id: ASSET_A },
    ])
    await wrapper.setProps({ blocks })
    await flushPromises()
    // 预览元数据/缩略已拉取（鉴权 fetch）
    expect(fetchMock).toHaveBeenCalled()
    expect(wrapper.find('[data-testid="image-asset-id"]').text()).toContain(ASSET_A)
  })

  it('更换图片：替换既有块的 asset_id，不新增块', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: any) => {
      const url = String(input)
      if (url.includes('/content')) return blobResponse()
      return envelope(assetMeta(ASSET_B))
    })
    const wrapper = mountEditor([
      { type: 'image', asset_id: ASSET_A },
      { type: 'text', text_content: '文字' },
    ])
    await flushPromises()

    const replaceButton = wrapper.findAll('button').find(b => b.text() === '更换图片')!
    await replaceButton.trigger('click')
    const library = wrapper.findComponent({ name: 'AssetLibrary' })
    library.vm.$emit('selected', assetMeta(ASSET_B))
    await flushPromises()

    const events = wrapper.emitted('update:blocks')!; const blocks = events[events.length - 1][0] as ContentBlockSpec[]
    expect(blocks).toHaveLength(2)
    expect(blocks[0]).toEqual({ type: 'image', asset_id: ASSET_B })
    expect(blocks[1]).toEqual({ type: 'text', text_content: '文字' })
  })

  it('图片块校验：空 asset_id 报错文案', async () => {
    const wrapper = mountEditor([{ type: 'image', asset_id: '' }])
    await flushPromises()
    expect(wrapper.text()).toContain('未选择图片素材')
  })

  it('混排顺序移动与删除（text/image/link）', async () => {
    const wrapper = mountEditor([
      { type: 'text', text_content: 'A' },
      { type: 'image', asset_id: ASSET_A },
      { type: 'link', url: 'https://e.com/b' },
    ])
    await flushPromises()

    // 下移第一个块（图片到首位）——emit 后由父组件回填（单向数据流）
    const moveDown = wrapper.findAll('button').filter(b => b.text() === '下移')
    await moveDown[0].trigger('click')
    const moveEvents = wrapper.emitted('update:blocks')!; let blocks = moveEvents[moveEvents.length - 1][0] as ContentBlockSpec[]
    expect(blocks[0]).toEqual({ type: 'image', asset_id: ASSET_A })
    await wrapper.setProps({ blocks })
    await flushPromises()

    // 删除（现在首位的）图片块
    const removeButtons = wrapper.findAll('button').filter(b => b.text() === '删除')
    await removeButtons[0].trigger('click')
    const delEvents = wrapper.emitted('update:blocks')!; blocks = delEvents[delEvents.length - 1][0] as ContentBlockSpec[]
    expect(blocks).toEqual([
      { type: 'text', text_content: 'A' },
      { type: 'link', url: 'https://e.com/b' },
    ])
  })
})
