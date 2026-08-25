<template>
  <BaseModal v-model="show" title="选择业务页面" size="lg">
    <!-- 搜索栏 + AI 推荐按钮 -->
    <div class="flex items-center gap-2 mb-4">
      <BaseInput v-model="searchQuery" placeholder="搜索页面名称或功能..." size="sm" class="flex-1" />
      <BaseButton size="sm" :disabled="recommending" @click="handleRecommend">
        {{ recommending ? '推荐中...' : 'AI 推荐' }}
      </BaseButton>
    </div>

    <!-- AI 推荐结果区域 -->
    <div v-if="recommendations.length > 0" class="mb-4 bg-primary-50 rounded-lg p-3">
      <div class="text-xs font-medium text-primary-700 mb-2">AI 推荐</div>
      <div v-for="rec in recommendations" :key="rec.page_id"
        class="flex items-center justify-between py-1.5 border-b border-primary-100 last:border-0">
        <div class="flex items-center gap-2">
          <input type="checkbox" :checked="localSelectedIds.has(rec.page_id)"
            @change="toggleSelect(rec.page_id)" class="w-3.5 h-3.5 rounded border-primary-200 text-primary-600 focus:ring-primary-500" />
          <span class="text-sm">{{ rec.title }}</span>
          <span v-if="rec.reason" class="text-xs text-muted">{{ rec.reason }}</span>
        </div>
        <BaseBadge intent="info" size="sm">{{ (rec.relevance_score * 100).toFixed(0) }}%</BaseBadge>
      </div>
    </div>

    <!-- 按业务域分组的页面列表 -->
    <div class="space-y-3 max-h-[50vh] overflow-y-auto">
      <div v-for="group in groupedPages" :key="group.domain">
        <button @click="toggleDomain(group.domain)"
          class="flex items-center gap-2 w-full text-sm font-medium text-default py-1.5">
          <svg :class="['w-3 h-3 transition-transform', expandedDomains.has(group.domain) ? 'rotate-90' : '']"
            fill="currentColor" viewBox="0 0 20 20">
            <path d="M6 4l8 6-8 6V4z"/>
          </svg>
          {{ group.label }}
        </button>
        <div v-show="expandedDomains.has(group.domain)" class="ml-5 space-y-1">
          <div v-for="page in group.pages" :key="page.page_id"
            class="flex items-center gap-3 py-1.5 px-2 rounded hover:bg-surface-hover cursor-pointer"
            @click="toggleSelect(page.page_id)">
            <input type="checkbox" :checked="localSelectedIds.has(page.page_id)"
              @click.stop="toggleSelect(page.page_id)"
              class="w-3.5 h-3.5 rounded border-primary-200 text-primary-600 focus:ring-primary-500" />
            <MenuIcon :icon="page.icon" />
            <div class="flex-1 min-w-0">
              <div class="text-sm text-default">{{ page.title }}</div>
              <div class="text-xs text-muted truncate">{{ page.description }}</div>
            </div>
            <span class="text-xs text-gray-400 whitespace-nowrap">{{ page.route }}</span>
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <span class="text-sm text-muted">已选 {{ localSelectedIds.size }} 个页面</span>
      <div class="flex gap-2 ml-auto">
        <BaseButton intent="secondary" @click="show = false">取消</BaseButton>
        <BaseButton @click="confirmSelect">确认选择</BaseButton>
      </div>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import MenuIcon from '@/components/ui/MenuIcon.vue'
import { listPageMeta, recommendPages } from '@/api/agentDefinitions'
import type { PageMeta, RecommendedPage } from '@/api/agentDefinitions'

const props = defineProps<{
  modelValue: boolean
  selectedPageIds: string[]
  agentDescription: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  'select': [pages: Array<{ id: string; title: string; icon: string; route: string }>]
}>()

const allPages = ref<PageMeta[]>([])
const searchQuery = ref('')
const localSelectedIds = ref<Set<string>>(new Set())
const recommendations = ref<RecommendedPage[]>([])
const recommending = ref(false)
const expandedDomains = ref<Set<string>>(new Set())

const show = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v)
})

// 打开时加载页面列表
watch(() => props.modelValue, async (v) => {
  if (v) {
    localSelectedIds.value = new Set(props.selectedPageIds)
    recommendations.value = []
    try {
      const res = await listPageMeta()
      if (res.success) {
        allPages.value = res.data
        expandedDomains.value = new Set([...new Set(res.data.map(p => p.domain))])
      }
    } catch {
      alert('页面数据加载失败，请稍后重试')
    }
  }
})

// 搜索过滤
const filteredPages = computed(() => {
  const q = searchQuery.value.toLowerCase().trim()
  if (!q) return allPages.value
  return allPages.value.filter(p =>
    p.title.toLowerCase().includes(q) ||
    p.description.toLowerCase().includes(q) ||
    p.domain_label.toLowerCase().includes(q)
  )
})

// 按域分组
const groupedPages = computed(() => {
  const groups: Map<string, { domain: string; label: string; pages: PageMeta[] }> = new Map()
  for (const p of filteredPages.value) {
    if (!groups.has(p.domain)) {
      groups.set(p.domain, { domain: p.domain, label: p.domain_label, pages: [] })
    }
    groups.get(p.domain)!.pages.push(p)
  }
  return [...groups.values()]
})

// AI 推荐
async function handleRecommend() {
  if (!props.agentDescription.trim()) {
    alert('请先填写智能体描述')
    return
  }
  recommending.value = true
  try {
    const res = await recommendPages({
      agent_description: props.agentDescription,
      current_pages: [...localSelectedIds.value]
    })
    if (res.success) {
      recommendations.value = res.data.recommended
    }
  } catch {
    alert('AI 推荐失败，请稍后重试')
  } finally {
    recommending.value = false
  }
}

// 选择/取消选择
function toggleSelect(pageId: string) {
  const s = new Set(localSelectedIds.value)
  if (s.has(pageId)) s.delete(pageId)
  else s.add(pageId)
  localSelectedIds.value = s
}

// 确认选择
function confirmSelect() {
  if (allPages.value.length === 0 && localSelectedIds.value.size > 0) {
    alert('页面数据加载失败，请关闭后重新打开')
    return
  }
  const pageMap = new Map(allPages.value.map(p => [p.page_id, p]))
  const selected = [...localSelectedIds.value]
    .filter(id => pageMap.has(id))
    .map(id => {
      const p = pageMap.get(id)!
      return { id: p.page_id, title: p.title, icon: p.icon, route: p.route }
    })
  emit('select', selected)
  show.value = false
}

function toggleDomain(domain: string) {
  const s = new Set(expandedDomains.value)
  if (s.has(domain)) s.delete(domain)
  else s.add(domain)
  expandedDomains.value = s
}
</script>
