<template>
  <div class="h-full flex flex-col bg-canvas">
    <AppHeader
      title="使用教程"
      :is-logged-in="isLoggedIn"
      :user="headerUser"
      @toggle-sidebar="toggleSidebar"
      @logout="logout"
    />

    <div class="flex-1 flex overflow-hidden">
      <!-- 左侧：文档目录（窄屏隐藏，用顶部下拉替代） -->
      <aside class="hidden lg:flex lg:flex-col w-64 shrink-0 border-r border-default bg-surface overflow-y-auto">
        <div v-for="cat in catalog?.categories || []" :key="cat.dir" class="px-3 py-2">
          <div class="text-xs font-semibold text-muted uppercase tracking-wide px-2 py-1">{{ cat.name }}</div>
          <ul>
            <li v-for="doc in cat.docs" :key="doc.path">
              <button
                type="button"
                class="w-full text-left text-sm px-2 py-1.5 rounded truncate block"
                :class="doc.path === currentPath ? 'bg-primary-50 text-primary-700 font-medium' : 'text-default hover:bg-surface-hover'"
                :title="doc.title"
                @click="openDoc(doc.path)"
              >{{ doc.title }}</button>
            </li>
          </ul>
        </div>
        <p v-if="catalog && catalog.categories.every(c => c.docs.length === 0)" class="text-sm text-muted px-4 py-3">
          docs/guides/ 下暂无文档。
        </p>
      </aside>

      <!-- 中间：文档渲染区 -->
      <div class="flex-1 flex flex-col overflow-hidden">
        <div class="lg:hidden px-3 py-2 border-b border-default bg-surface">
          <BaseSelect v-model="narrowSelected" class="w-full" @change="openDoc(narrowSelected)">
            <option value="" disabled>选择文档…</option>
            <optgroup v-for="cat in catalog?.categories || []" :key="cat.dir" :label="cat.name">
              <option v-for="doc in cat.docs" :key="doc.path" :value="doc.path">{{ doc.title }}</option>
            </optgroup>
          </BaseSelect>
        </div>

        <div class="flex items-center justify-between gap-2 px-4 sm:px-6 py-3 border-b border-default bg-surface">
          <div class="min-w-0">
            <h2 class="text-base sm:text-lg font-semibold truncate">{{ currentDoc?.title || '使用教程' }}</h2>
            <p v-if="currentDoc" class="text-xs text-muted mt-0.5">更新时间：{{ currentDoc.updated_at }}</p>
          </div>
          <BaseButton intent="secondary" size="sm" :disabled="docLoading" @click="reload">刷新</BaseButton>
        </div>

        <p v-if="docError" role="alert" class="text-sm text-danger-700 px-4 sm:px-6 py-3">{{ docError }}</p>

        <div ref="contentWrapRef" class="flex-1 overflow-hidden" @click="onContentClick">
          <div v-if="docLoading && !currentDoc" class="text-sm text-muted p-6">加载中…</div>
          <MarkdownPreview v-else-if="currentDoc" :source="currentDoc.content" font-size="base" />
        </div>
      </div>

      <!-- 右侧：文档内目录（xl 以上常驻） -->
      <aside class="hidden xl:flex xl:flex-col w-60 shrink-0 border-l border-default bg-surface overflow-y-auto">
        <div class="text-xs font-semibold text-muted uppercase tracking-wide px-4 py-3">本页目录</div>
        <ul class="px-2 pb-4 space-y-0.5">
          <li v-for="item in tocItems" :key="item.id">
            <button
              type="button"
              class="w-full text-left text-sm px-2 py-1 rounded truncate block"
              :class="[
                item.level === 3 ? 'pl-5' : '',
                item.level === 4 ? 'pl-8' : '',
                item.id === activeTocId ? 'text-primary-700 bg-primary-50 font-medium' : 'text-muted hover:bg-surface-hover hover:text-default'
              ]"
              :title="item.text"
              @click="scrollToHeading(item.id)"
            >{{ item.text }}</button>
          </li>
        </ul>
        <p v-if="tocItems.length === 0" class="text-xs text-muted px-4">本文档无二级及以下标题。</p>
      </aside>
    </div>

    <!-- 窄屏目录抽屉 -->
    <button
      v-if="tocItems.length > 0"
      type="button"
      class="xl:hidden fixed bottom-5 right-5 z-30 rounded-full bg-primary-600 text-white shadow-lg px-4 py-2.5 text-sm"
      @click="tocDrawerOpen = true"
    >目录</button>
    <div v-if="tocDrawerOpen" class="xl:hidden fixed inset-0 z-40" @click.self="tocDrawerOpen = false">
      <div class="absolute inset-0 bg-black/40" />
      <div class="absolute right-0 top-0 bottom-0 w-72 max-w-[80vw] bg-surface shadow-xl overflow-y-auto">
        <div class="flex items-center justify-between px-4 py-3 border-b border-default">
          <span class="text-sm font-semibold">本页目录</span>
          <BaseButton intent="ghost" size="sm" @click="tocDrawerOpen = false">关闭</BaseButton>
        </div>
        <ul class="px-2 py-2 space-y-0.5">
          <li v-for="item in tocItems" :key="item.id">
            <button
              type="button"
              class="w-full text-left text-sm px-2 py-1.5 rounded truncate block"
              :class="[
                item.level === 3 ? 'pl-5' : '',
                item.level === 4 ? 'pl-8' : '',
                item.id === activeTocId ? 'text-primary-700 bg-primary-50 font-medium' : 'text-muted hover:bg-surface-hover'
              ]"
              @click="scrollToHeading(item.id); tocDrawerOpen = false"
            >{{ item.text }}</button>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import MarkdownPreview from '@/components/ui/MarkdownPreview.vue'
import { getGuideDoc, getGuidesCatalog } from '@/api/guides'
import type { GuideCatalog, GuideDoc } from '@/api/guides'
import { useTenantAuth } from '@/composables/useTenantAuth'

const route = useRoute()
const router = useRouter()
const { admin, isLoggedIn, logout } = useTenantAuth()

const headerUser = computed(() => admin.value ? { user_id: admin.value.user_id, username: admin.value.username, phone: admin.value.phone } : null)
const toggleSidebarFn = inject<() => void>('toggleSidebar')
function toggleSidebar() { toggleSidebarFn?.() }

// ============== 目录与文档加载 ==============

const catalog = ref<GuideCatalog | null>(null)
const catalogError = ref('')
const currentDoc = ref<GuideDoc | null>(null)
const docLoading = ref(false)
const docError = ref('')
const narrowSelected = ref('')

const currentPath = computed(() => {
  const p = route.params.doc_path
  return typeof p === 'string' ? p : Array.isArray(p) ? p.join('/') : ''
})

async function loadCatalog() {
  catalogError.value = ''
  try {
    catalog.value = await getGuidesCatalog()
  } catch (e) {
    catalogError.value = e instanceof Error ? e.message : String(e)
  }
}

async function loadDoc(path: string) {
  if (!path) {
    currentDoc.value = null
    return
  }
  docLoading.value = true
  docError.value = ''
  try {
    currentDoc.value = await getGuideDoc(path)
    narrowSelected.value = path
    await nextTick()
    buildToc()
  } catch (e) {
    currentDoc.value = null
    docError.value = e instanceof Error ? `加载失败：${e.message}` : `加载失败：${String(e)}`
  } finally {
    docLoading.value = false
  }
}

function openDoc(path: string) {
  if (!path) return
  if (path === currentPath.value) {
    // 路由未变化时（默认文档 / 重复点击）直接加载
    if (currentDoc.value?.path !== path) loadDoc(path)
    return
  }
  router.push(`/portal/guides/${path}`)
}

function reload() {
  if (currentPath.value) loadDoc(currentPath.value)
}

function openDefaultDoc() {
  const first = catalog.value?.categories.flatMap(c => c.docs)[0]
  if (!first) return
  // URL 同步深链；加载不依赖路由 watch（避免默认文档双重请求或依赖路由跳转）
  Promise.resolve(router.push(`/portal/guides/${first.path}`)).catch(() => {})
  loadDoc(first.path)
}

// ============== 文档内目录（TOC）与 scrollspy ==============

interface TocItem { id: string; text: string; level: number }

const contentWrapRef = ref<HTMLElement | null>(null)
const tocItems = ref<TocItem[]>([])
const activeTocId = ref('')
const tocDrawerOpen = ref(false)

let scrollEl: HTMLElement | null = null

function markdownBodyEl(): HTMLElement | null {
  return contentWrapRef.value?.querySelector('.markdown-body') ?? null
}

function buildToc() {
  detachScroll()
  tocItems.value = []
  activeTocId.value = ''
  const body = markdownBodyEl()
  if (!body) return
  const headings = body.querySelectorAll('h2, h3, h4')
  headings.forEach((h, i) => {
    const id = `guide-h-${i}`
    h.setAttribute('id', id)
    tocItems.value.push({ id, text: h.textContent?.trim() || `标题 ${i + 1}`, level: Number(h.tagName.slice(1)) })
  })
  scrollEl = body.classList.contains('overflow-auto') ? body : (body.closest('.overflow-auto') as HTMLElement | null)
  scrollEl?.addEventListener('scroll', onScroll, { passive: true })
  onScroll()
}

function onScroll() {
  if (!scrollEl || tocItems.value.length === 0) return
  const top = scrollEl.scrollTop + 80
  let active = ''
  for (const item of tocItems.value) {
    const el = document.getElementById(item.id)
    if (el && el.offsetTop <= top) active = item.id
  }
  activeTocId.value = active || tocItems.value[0]?.id || ''
}

function scrollToHeading(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function detachScroll() {
  scrollEl?.removeEventListener('scroll', onScroll)
  scrollEl = null
}

// ============== 文档间相对链接拦截 ==============

function onContentClick(e: MouseEvent) {
  const anchor = (e.target as HTMLElement).closest('a')
  if (!anchor) return
  const href = anchor.getAttribute('href') || ''
  if (!href || href.startsWith('http://') || href.startsWith('https://') || href.startsWith('file_id:')) return
  if (href.startsWith('#')) {
    const target = markdownBodyEl()?.querySelector(`#${CSS.escape(href.slice(1))}`)
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    e.preventDefault()
    return
  }
  if (href.endsWith('.md')) {
    e.preventDefault()
    const base = (currentDoc.value?.path || currentPath.value).split('/')
    base.pop()
    const parts = href.split('/')
    const stack = [...base]
    for (const part of parts) {
      if (part === '.' || part === '') continue
      if (part === '..') stack.pop()
      else stack.push(part)
    }
    const normalized = stack.join('/')
    if (normalized) openDoc(normalized)
  }
}

// ============== 生命周期 ==============

watch(currentPath, (p) => { if (p && currentDoc.value?.path !== p) loadDoc(p) })
watch(narrowSelected, (p) => { openDoc(p) })
onMounted(async () => {
  await loadCatalog()
  if (currentPath.value) {
    loadDoc(currentPath.value)
  } else {
    openDefaultDoc()
  }
})
onBeforeUnmount(detachScroll)
</script>
