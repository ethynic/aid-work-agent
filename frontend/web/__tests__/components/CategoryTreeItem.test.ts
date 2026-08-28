/**
 * CategoryTreeItem 组件测试
 *
 * 验证点：
 * - showActions 默认 true 显示 rename/delete 悬浮按钮，传 false 时隐藏（移动弹框复用场景）
 * - defaultExpanded 默认 false（子分类折叠），传 true 时全部展开（移动弹框浏览场景）
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import CategoryTreeItem from '@/components/knowledge/CategoryTreeItem.vue'
import type { CategoryResponse } from '@/api/knowledge'

interface CategoryTreeNode extends CategoryResponse {
  children: CategoryTreeNode[]
  rootSourceType?: string
}

function buildTree(): CategoryTreeNode {
  const grandchild: CategoryTreeNode = {
    id: 3,
    source_type: 'grandchild',
    display_name: '三级分类',
    parent_id: 2,
    document_count: 0,
    created_at: null,
    children: [],
    rootSourceType: 'top',
  }
  const child: CategoryTreeNode = {
    id: 2,
    source_type: 'child',
    display_name: '二级分类',
    parent_id: 1,
    document_count: 0,
    created_at: null,
    children: [grandchild],
    rootSourceType: 'top',
  }
  return {
    id: 1,
    source_type: 'top',
    display_name: '顶级分类',
    parent_id: null,
    document_count: 1,
    created_at: null,
    children: [child],
    rootSourceType: 'top',
  }
}

function mountItem(extraProps: Record<string, any> = {}) {
  return mount(CategoryTreeItem, {
    props: {
      category: buildTree(),
      depth: 0,
      selectedSourceType: null,
      selectedSubCategory: null,
      ...extraProps,
    },
  })
}

describe('CategoryTreeItem', () => {
  it('showActions 默认 true 时显示 rename/delete 悬浮按钮', () => {
    const wrapper = mountItem()
    // 每层有子节点的节点行 = 1 展开箭头 + 2 操作按钮（rename/delete）
    // top（有子）+ child（有子）共 2 行 => 2 * 3 = 6
    expect(wrapper.findAll('button').length).toBe(6)
    // rename/delete 图标按钮存在
    expect(wrapper.find('button.hover\\:bg-primary-100').exists()).toBe(true)
    expect(wrapper.find('button.hover\\:bg-danger-100').exists()).toBe(true)
  })

  it('showActions=false 时隐藏 rename/delete 悬浮按钮（移动弹框复用）', () => {
    const wrapper = mountItem({ showActions: false })
    // 只剩展开箭头（top + child 各 1 个）
    expect(wrapper.findAll('button').length).toBe(2)
    expect(wrapper.find('button.hover\\:bg-primary-100').exists()).toBe(false)
    expect(wrapper.find('button.hover\\:bg-danger-100').exists()).toBe(false)
  })

  it('默认只展开顶级，二级分类的子级不渲染', () => {
    const wrapper = mountItem()
    // 顶级 depth=0 默认展开 -> 二级渲染；二级 depth=1 默认折叠 -> 三级不渲染
    expect(wrapper.text()).toContain('二级分类')
    expect(wrapper.text()).not.toContain('三级分类')
  })

  it('defaultExpanded=true 时全部展开（移动弹框浏览场景）', () => {
    const wrapper = mountItem({ defaultExpanded: true })
    expect(wrapper.text()).toContain('二级分类')
    expect(wrapper.text()).toContain('三级分类')
  })
})
