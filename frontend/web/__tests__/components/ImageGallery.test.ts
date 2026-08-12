/**
 * ImageGallery 组件单测
 *
 * 验证：
 * - 单图布局（layout-single）
 * - 2-3 图横排布局（layout-row）
 * - 4+ 图网格布局（layout-grid）
 * - 点击图片打开 lightbox
 * - lightbox 关闭按钮关闭
 * - source 徽章显示中文标签
 * - 图片 error 事件给父元素加 'broken' 类（降级占位）
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

import ImageGallery from '@/components/ui/ImageGallery.vue'
import type { ImageRef } from '@/types'

/** 构造测试 ImageRef */
function makeImage(overrides: Partial<ImageRef> = {}): ImageRef {
  return {
    file_id: `file_${Math.random().toString(36).slice(2, 10)}`,
    download_url: '/api/files/test/download',
    display_name: '测试图片',
    mime_type: 'image/png',
    size_bytes: 1024,
    source: 'knowledge_base',
    usage: 'inline',
    placement: 'after_text',
    ...overrides,
  }
}

function makeImages(n: number, overrides: Partial<ImageRef> = {}): ImageRef[] {
  return Array.from({ length: n }, (_, i) =>
    makeImage({ file_id: `file_${i}`, ...overrides }),
  )
}

/**
 * 挂载组件（attachTo: document.body 让 BaseModal Teleport 内容可被 document.querySelector 检索）
 */
function mountGallery(images: ImageRef[]) {
  const div = document.createElement('div')
  div.id = 'test-mount'
  document.body.appendChild(div)
  return mount(ImageGallery, {
    attachTo: div,
    props: { images },
  })
}

function bodyHtml(): string {
  return document.body.innerHTML
}

describe('ImageGallery', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('renders single image layout', () => {
    const wrapper = mountGallery(makeImages(1))
    const gallery = wrapper.find('.image-gallery')
    expect(gallery.exists()).toBe(true)
    expect(gallery.classes()).toContain('layout-single')
    expect(gallery.findAll('.gallery-item')).toHaveLength(1)
  })

  it('renders row layout for 2 images', () => {
    const wrapper = mountGallery(makeImages(2))
    const gallery = wrapper.find('.image-gallery')
    expect(gallery.classes()).toContain('layout-row')
    expect(gallery.findAll('.gallery-item')).toHaveLength(2)
  })

  it('renders row layout for 3 images', () => {
    const wrapper = mountGallery(makeImages(3))
    const gallery = wrapper.find('.image-gallery')
    expect(gallery.classes()).toContain('layout-row')
    expect(gallery.findAll('.gallery-item')).toHaveLength(3)
  })

  it('renders grid layout for 4+ images', () => {
    const wrapper = mountGallery(makeImages(4))
    const gallery = wrapper.find('.image-gallery')
    expect(gallery.classes()).toContain('layout-grid')
    expect(gallery.findAll('.gallery-item')).toHaveLength(4)
  })

  it('renders grid layout for many images (6)', () => {
    const wrapper = mountGallery(makeImages(6))
    const gallery = wrapper.find('.image-gallery')
    expect(gallery.classes()).toContain('layout-grid')
    expect(gallery.findAll('.gallery-item')).toHaveLength(6)
  })

  it('click image opens lightbox', async () => {
    const wrapper = mountGallery(makeImages(2))
    // 初始无 lightbox（BaseModal 不渲染）
    expect(bodyHtml()).not.toContain('lightbox-img')
    const firstItem = wrapper.findAll('.gallery-item')[0]
    await firstItem.trigger('click')
    await flushPromises()
    // lightbox 打开后 body 中应有 lightbox-img（BaseModal 通过 Teleport 挂到 body）
    expect(bodyHtml()).toContain('lightbox-img')
  })

  it('lightbox close button closes', async () => {
    const wrapper = mountGallery(makeImages(1))
    // 打开 lightbox
    await wrapper.find('.gallery-item').trigger('click')
    await flushPromises()
    expect(bodyHtml()).toContain('lightbox-img')
    // 点击「关闭」按钮（Teleport 到 body，用原生 DOM）
    const closeBtn = Array.from(document.querySelectorAll('button')).find(b =>
      b.textContent?.trim() === '关闭',
    ) as HTMLButtonElement | undefined
    expect(closeBtn).toBeTruthy()
    closeBtn!.click()
    await flushPromises()
    // 关闭后 lightbox-img 不再存在
    expect(bodyHtml()).not.toContain('lightbox-img')
  })

  it('source badge shows label for knowledge_base', () => {
    const wrapper = mountGallery(
      makeImages(1, { source: 'knowledge_base' }),
    )
    expect(wrapper.find('.source-badge').text()).toBe('知识库')
  })

  it('source badge shows label for tool_generated', () => {
    const wrapper = mountGallery(
      makeImages(1, { source: 'tool_generated' }),
    )
    expect(wrapper.find('.source-badge').text()).toBe('AI 生成')
  })

  it('source badge shows label for user_upload', () => {
    const wrapper = mountGallery(
      makeImages(1, { source: 'user_upload' }),
    )
    expect(wrapper.find('.source-badge').text()).toBe('上传')
  })

  it('source badge shows label for web_fetch', () => {
    const wrapper = mountGallery(makeImages(1, { source: 'web_fetch' }))
    expect(wrapper.find('.source-badge').text()).toBe('网络')
  })

  it('source badge shows label for screenshot', () => {
    const wrapper = mountGallery(makeImages(1, { source: 'screenshot' }))
    expect(wrapper.find('.source-badge').text()).toBe('截图')
  })

  it('source badge falls back to raw value for unknown source', () => {
    // @ts-expect-error 测试未知 source 容错
    const wrapper = mountGallery(makeImages(1, { source: 'unknown_x' }))
    expect(wrapper.find('.source-badge').text()).toBe('unknown_x')
  })

  it('image error adds broken class to parent', async () => {
    const wrapper = mountGallery(makeImages(1))
    const img = wrapper.find('.gallery-img')
    expect(img.exists()).toBe(true)
    // 模拟图片加载失败
    await img.trigger('error')
    await flushPromises()
    const item = wrapper.find('.gallery-item')
    expect(item.classes()).toContain('broken')
  })

  it('renders loading="lazy" on all images', () => {
    const wrapper = mountGallery(makeImages(3))
    const imgs = wrapper.findAll('.gallery-img')
    expect(imgs).toHaveLength(3)
    imgs.forEach(img => {
      expect(img.attributes('loading')).toBe('lazy')
    })
  })

  it('empty images renders no gallery-item', () => {
    const wrapper = mountGallery([])
    // gallery 根容器仍在（layoutMode computed 仍返回 grid，但无 item）
    const items = wrapper.findAll('.gallery-item')
    expect(items).toHaveLength(0)
  })
})
