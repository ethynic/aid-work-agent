/**
 * JsonNode 组件单测
 *
 * 验证：
 * - Object/Array 节点渲染复制按钮
 * - String 节点渲染复制按钮
 * - 点击复制把对应节点 JSON 写入剪贴板
 * - 点击复制不触发节点展开/折叠（@click.stop）
 * - String 节点复制原文（不带引号）
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'

import JsonNode from '@/components/ui/JsonNode.vue'

function mockClipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  })
  return writeText
}

describe('JsonNode', () => {
  let writeText: ReturnType<typeof mockClipboard>

  beforeEach(() => {
    writeText = mockClipboard()
  })

  it('Object 节点渲染复制按钮', () => {
    const wrapper = mount(JsonNode, {
      props: { data: { a: 1 }, keyName: 'root', depth: 0 },
    })
    expect(wrapper.find('.json-copy-btn').exists()).toBe(true)
  })

  it('String 节点渲染复制按钮', () => {
    const wrapper = mount(JsonNode, {
      props: { data: 'some content', keyName: 'content', depth: 0 },
    })
    expect(wrapper.find('.json-copy-btn').exists()).toBe(true)
  })

  it('Object 节点点击复制把格式化 JSON 写入剪贴板', async () => {
    const obj = { a: 1, b: 'x' }
    const wrapper = mount(JsonNode, {
      props: { data: obj, keyName: 'root', depth: 0 },
    })
    await wrapper.find('.json-copy-btn').trigger('click')
    expect(writeText).toHaveBeenCalledWith(JSON.stringify(obj, null, 2))
  })

  it('String 节点复制原文（不带引号）', async () => {
    const wrapper = mount(JsonNode, {
      props: { data: 'hello', keyName: 'content', depth: 0 },
    })
    await wrapper.find('.json-copy-btn').trigger('click')
    expect(writeText).toHaveBeenCalledWith('hello')
  })

  it('点击复制不触发节点展开/折叠', async () => {
    const wrapper = mount(JsonNode, {
      props: { data: { a: 1 }, keyName: 'root', depth: 0 },
    })
    await wrapper.find('.json-copy-btn').trigger('click')
    // 折叠态：展开容器 .ml-4 不应渲染
    expect(wrapper.find('.ml-4').exists()).toBe(false)
  })
})
