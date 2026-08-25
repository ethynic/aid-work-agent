<template>
  <div class="image-gallery" :class="`layout-${layoutMode}`">
    <div
      v-for="(img, idx) in images"
      :key="img.file_id"
      class="gallery-item"
      @click="openLightbox(idx)"
    >
      <img
        :src="img.download_url"
        :alt="img.display_name"
        loading="lazy"
        class="gallery-img"
        @error="onImgError($event, img)"
      />
      <div class="source-badge">{{ sourceLabel(img.source) }}</div>
    </div>

    <!-- Lightbox -->
    <BaseModal
      v-if="lightboxOpen && lightboxIndex >= 0"
      :model-value="lightboxOpen"
      size="xl"
      :title="images[lightboxIndex]?.display_name"
      @update:model-value="lightboxOpen = false"
    >
      <img
        :src="images[lightboxIndex]?.download_url"
        class="lightbox-img"
        :alt="images[lightboxIndex]?.display_name"
      />
      <template #footer>
        <BaseButton intent="secondary" @click="lightboxOpen = false">关闭</BaseButton>
        <BaseButton @click="downloadCurrent">下载</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { ImageRef } from '@/types'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseModal from '@/components/ui/BaseModal.vue'

/**
 * ImageGallery 组件
 *
 * 一组图片的展示组件，支持：
 * - 自适应布局（1 张单图 / 2-3 张横排 / 4+ 张网格）
 * - 懒加载（loading="lazy"）
 * - source 标签（右下角徽章）
 * - 点击放大（lightbox，复用 BaseModal）
 * - 图片错误降级（broken 占位）
 */

const props = defineProps<{
  images: ImageRef[]
}>()

/** 布局模式：根据图片数量自动选择 */
const layoutMode = computed<'single' | 'row' | 'grid'>(() => {
  const n = props.images.length
  if (n === 1) return 'single'
  if (n <= 3) return 'row'
  return 'grid'
})

/** source 标签映射 */
const SOURCE_LABELS: Record<string, string> = {
  knowledge_base: '知识库',
  tool_generated: 'AI 生成',
  user_upload: '上传',
  web_fetch: '网络',
  screenshot: '截图',
}

function sourceLabel(source: string | undefined): string {
  if (!source) return '未知'
  return SOURCE_LABELS[source] || source
}

/** Lightbox 状态 */
const lightboxOpen = ref(false)
const lightboxIndex = ref(-1)

function openLightbox(idx: number) {
  lightboxIndex.value = idx
  lightboxOpen.value = true
}

/** 图片加载失败降级：隐藏 img，父元素加 broken 类显示占位 */
function onImgError(ev: Event, _img: ImageRef) {
  const target = ev.target as HTMLImageElement
  target.style.display = 'none'
  target.parentElement?.classList.add('broken')
}

/** 下载当前 lightbox 中的图片 */
function downloadCurrent() {
  const img = props.images[lightboxIndex.value]
  if (!img) return
  const a = document.createElement('a')
  a.href = img.download_url
  a.download = img.display_name || 'image'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}
</script>

<style scoped>
.image-gallery {
  margin-top: 0.5rem;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

/* 单图：max-width 600px，不强制等宽 */
.image-gallery.layout-single .gallery-item {
  flex: 0 0 auto;
  max-width: 600px;
}

/* 2-3 张：横向并排，等宽，正方形 */
.image-gallery.layout-row .gallery-item {
  flex: 1 1 0;
  min-width: 120px;
  max-width: 300px;
  aspect-ratio: 1;
}

/* 4+ 张：CSS Grid 瀑布流 */
.image-gallery.layout-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 8px;
}

.image-gallery.layout-grid .gallery-item {
  aspect-ratio: 1;
}

/* gallery-item 通用样式 */
.gallery-item {
  position: relative;
  overflow: hidden;
  border-radius: 8px;
  border: 1px solid var(--border-default);
  cursor: zoom-in;
  background: var(--bg-surface);
}

/* 图片错误占位（::before 由 .broken 类触发） */
.gallery-item.broken::before {
  content: '加载失败';
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  color: var(--text-muted);
  font-size: 11px;
}

.gallery-item .gallery-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

/* source 标签 */
.gallery-item .source-badge {
  position: absolute;
  right: 4px;
  bottom: 4px;
  background: rgba(0, 0, 0, 0.55);
  color: white;
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 4px;
  pointer-events: none;
}

/* lightbox 图片 */
.lightbox-img {
  max-width: 100%;
  max-height: 70vh;
  object-fit: contain;
  display: block;
  margin: 0 auto;
}
</style>
