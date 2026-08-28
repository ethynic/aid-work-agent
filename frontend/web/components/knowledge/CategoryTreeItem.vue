<template>
  <div>
    <div
      class="group flex items-center justify-between w-full px-3 py-2 rounded-lg text-sm transition-colors cursor-pointer"
      :class="isActive ? 'bg-primary-50 text-primary-700 font-medium' : 'text-default hover:bg-surface-hover'"
      :style="{ paddingLeft: ((depth ?? 0) * 14 + 8) + 'px' }"
      @click="handleSelect"
    >
      <div class="flex items-center flex-1">
        <button
          v-if="hasChildren"
          class="flex-shrink-0 mr-1 text-muted hover:text-default rounded p-0.5"
          :title="expanded ? '折叠' : '展开'"
          @click.stop="expanded = !expanded"
        >
          <svg class="w-3.5 h-3.5 transition-transform duration-150" :class="{ 'rotate-90': expanded }" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
          </svg>
        </button>
        <span v-else class="w-4 flex-shrink-0 mr-1" />
        <span class="whitespace-nowrap" :title="category.display_name || category.source_type">{{ category.display_name || category.source_type }}&nbsp;&nbsp;</span>
      </div>
      <div class="flex items-center gap-1 flex-shrink-0">
        <span class="text-xs text-muted">{{ category.document_count }}</span>
        <span v-if="showActions" class="hidden group-hover:flex items-center gap-0.5">
          <button @click.stop="$emit('rename', category)" class="p-0.5 rounded hover:bg-primary-100 text-muted hover:text-primary-600">
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" /></svg>
          </button>
          <button @click.stop="$emit('delete', category)" class="p-0.5 rounded hover:bg-danger-100 text-muted hover:text-danger-600">
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
          </button>
        </span>
      </div>
    </div>

    <template v-if="expanded">
      <CategoryTreeItem
        v-for="child in category.children"
        :key="child.id"
        :category="child"
        :depth="(depth ?? 0) + 1"
        :selected-source-type="selectedSourceType"
        :selected-sub-category="selectedSubCategory"
        :show-actions="showActions"
        :default-expanded="defaultExpanded"
        @select="handleChildSelect"
        @rename="$emit('rename', $event)"
        @delete="$emit('delete', $event)"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { CategoryResponse } from '@/api/knowledge'

interface CategoryTreeNode extends CategoryResponse {
  children: CategoryTreeNode[]
  rootSourceType?: string
}

const props = withDefaults(defineProps<{
  category: CategoryTreeNode
  depth?: number
  selectedSourceType: string | null
  selectedSubCategory: string | null
  showActions?: boolean
  defaultExpanded?: boolean
}>(), {
  showActions: true,
  defaultExpanded: false
})

const emit = defineEmits<{
  (e: 'select', category: CategoryTreeNode): void
  (e: 'rename', category: CategoryTreeNode): void
  (e: 'delete', category: CategoryTreeNode): void
}>()

// 默认顶级分类展开、子分类折叠；defaultExpanded=true 时（如移动弹框）全部展开
const expanded = ref(props.defaultExpanded ? true : (props.depth ?? 0) === 0)

const hasChildren = computed(() => (props.category.children?.length || 0) > 0)

const isActive = computed(() => {
  if (props.selectedSourceType !== props.category.rootSourceType) return false
  if (props.category.parent_id === null) {
    return props.selectedSubCategory === null
  }
  return props.selectedSubCategory === props.category.source_type
})

function handleSelect() {
  emit('select', props.category)
}

function handleChildSelect(child: CategoryTreeNode) {
  emit('select', child)
}
</script>
