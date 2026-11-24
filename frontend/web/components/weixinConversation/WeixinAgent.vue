<template>
  <WeixinDailySummary v-if="typeof route.query.run === 'string' || route.query.mode === 'daily'" />
  <WeixinGroupTracking v-else :key="String(route.params.tenant_id ?? '')" />
</template>
<script setup lang="ts">
import { useRoute } from 'vue-router'
import WeixinDailySummary from './WeixinDailySummary.vue'
import WeixinGroupTracking from './WeixinGroupTracking.vue'

const route = useRoute()
</script>
