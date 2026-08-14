<template>
  <aside class="desktop-sidebar" aria-label="主导航">
    <nav>
      <RouterLink to="/" class="nav-item" aria-current="page">
        <span class="nav-glyph" aria-hidden="true">01</span><span>工作台</span>
      </RouterLink>
      <div class="nav-divider"><span>即将开放</span></div>
      <button v-for="item in futureItems" :key="item.code" class="nav-item nav-item-disabled" type="button" disabled>
        <span class="nav-glyph" aria-hidden="true">{{ item.code }}</span><span>{{ item.label }}</span>
      </button>
    </nav>
    <div class="sidebar-foot">
      <span class="sidebar-label">账户</span>
      <strong>{{ username }}</strong>
      <span>{{ tenantCode }}</span>
      <button type="button" class="quiet-button" @click="$emit('sign-out')">安全退出</button>
    </div>
  </aside>
</template>

<script setup lang="ts">
defineProps<{ username: string; tenantCode: string }>()
defineEmits<{ 'sign-out': [] }>()
const futureItems = [
  { code: '02', label: '会话' }, { code: '03', label: '数字员工' },
  { code: '04', label: '知识库' }, { code: '05', label: '本地工具' }, { code: '06', label: '设置' },
]
</script>
