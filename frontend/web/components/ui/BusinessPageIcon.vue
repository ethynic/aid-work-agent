<template>
  <svg
    class="w-5 h-5 flex-shrink-0"
    fill="none"
    stroke="currentColor"
    viewBox="0 0 24 24"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
  >
    <path :d="iconPath" />
  </svg>
</template>

<script setup lang="ts">
import { computed } from 'vue'

/**
 * 业务子菜单图标
 *
 * 设计目的：不再依赖后端 `page.icon` 字段（数据库历史数据中可能含 emoji
 * 或不一致的字符串），改为按 `page.title` 关键字在前端固定图标库中匹配。
 *
 * - 匹配到：返回对应的语义 SVG path
 * - 匹配不到：返回通用"文档"图标作为默认
 */
const props = defineProps<{
  /** 业务页面标题，用于关键字匹配 */
  title: string
}>()

// 关键字 → SVG path 映射
// 关键字按"长→短"顺序匹配，匹配越精确优先级越高
const ICON_RULES: Array<{ keywords: string[]; path: string }> = [
  // 商品类（用户截图中提到）
  { keywords: ['商品类目', '类目'], path: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z' },
  { keywords: ['商品管理', '商品'], path: 'M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16zM3.27 6.96L12 12.01l8.73-5.05M12 22.08V12' },

  // 客户/销售人员
  { keywords: ['销售代表', '销售人员', '销售员'], path: 'M12 12a4 4 0 100-8 4 4 0 000 8zM4 21v-1a6 6 0 016-6h4a6 6 0 016 6v1M16 4l2 2 4-4' },
  { keywords: ['客户管理', '我的客户', '客户'], path: 'M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2M9 11a4 4 0 100-8 4 4 0 000 8zM22 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75' },

  // 通讯/邮件
  { keywords: ['邮件记录', '邮件'], path: 'M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2zM22 6l-10 7L2 6' },

  // 统计
  { keywords: ['匹配统计', '订单统计', '投诉统计', '统计'], path: 'M3 21h18M6 17V9M11 17V5M16 17v-4M21 17v-7' },
  { keywords: ['转化漏斗', '漏斗'], path: 'M3 4h18l-7 9v6l-4 2v-8L3 4z' },

  // 旅游类
  { keywords: ['车辆价格', '车辆', '运输'], path: 'M3 17h2l1-4h12l1 4h2v-3l-2-5a2 2 0 00-1.9-1.4H6.9A2 2 0 005 5L3 9v8zM7 17a1.5 1.5 0 100 3 1.5 1.5 0 000-3zM17 17a1.5 1.5 0 100 3 1.5 1.5 0 000-3z' },
  { keywords: ['景点门票', '景点'], path: 'M3 21h18M5 21V8l7-4 7 4v13M9 21v-6h6v6' },
  { keywords: ['酒店房型', '酒店'], path: 'M3 18v-6a2 2 0 012-2h14a2 2 0 012 2v6M3 18h18M3 18v3M21 18v3M7 10V6a1 1 0 011-1h2a1 1 0 011 1v4' },
  { keywords: ['餐标价格', '餐标', '餐饮', '餐'], path: 'M5 3v18M5 3h10a4 4 0 014 4v0a4 4 0 01-4 4H5M19 3v18' },
  { keywords: ['导游费用', '导游'], path: 'M12 12a4 4 0 100-8 4 4 0 000 8zM4 21v-1a6 6 0 016-6h4a6 6 0 016 6v1' },
  { keywords: ['其他费用', '费用', '资金'], path: 'M12 1v22M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6' },

  // 客户跟进
  { keywords: ['线索管理', '线索'], path: 'M12 22a10 10 0 100-20 10 10 0 000 20zM12 6v6l4 2M22 2l-5 5M17 2h5v5' },
  { keywords: ['跟进记录', '跟进'], path: 'M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7M18.5 2.5a2.121 2.121 0 113 3L12 15l-4 1 1-4 9.5-9.5z' },

  // 投诉
  { keywords: ['投诉列表', '投诉记录', '投诉'], path: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01' },

  // 售后
  { keywords: ['售后工单', '工单'], path: 'M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16zM3.27 6.96L12 12.01l8.73-5.05M12 22.08V12' },
  { keywords: ['退换货', '退货', '换货'], path: 'M3 7v6h6M21 17a9 9 0 11-3-6.7L21 13M21 7v6h-6' },

  // 订单
  { keywords: ['订单管理', '订单'], path: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01' },
  { keywords: ['库存查询', '库存', '仓储'], path: 'M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16zM3.27 6.96L12 12.01l8.73-5.05M12 22.08V12' },
  { keywords: ['发货记录', '发货', '物流'], path: 'M3 17h2l1-4h12l1 4h2v-3l-2-5a2 2 0 00-1.9-1.4H6.9A2 2 0 005 5L3 9v8zM7 17a1.5 1.5 0 100 3 1.5 1.5 0 000-3zM17 17a1.5 1.5 0 100 3 1.5 1.5 0 000-3z' },

  // 数据源
  { keywords: ['数据源管理', '数据源'], path: 'M4 6a2 2 0 012-2h12a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h12a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM6 11h12' },

  // 视频创作
  { keywords: ['素材库'], path: 'M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z' },
  { keywords: ['视频库'], path: 'M4 4h16v16H4zM10 9l5 3-5 3V9z' },
  { keywords: ['提示词库', '提示词'], path: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z' },
  { keywords: ['视频创作', '视频'], path: 'M4 4h16v16H4zM10 9l5 3-5 3V9z' },
]

// 默认图标：通用文档
const DEFAULT_ICON = 'M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8l-6-6zM14 2v6h6M9 13h6M9 17h6'

const iconPath = computed(() => {
  const title = (props.title || '').trim()
  if (!title) return DEFAULT_ICON

  // 优先按规则列表顺序匹配（前面的规则更具体）
  for (const rule of ICON_RULES) {
    for (const kw of rule.keywords) {
      if (title.includes(kw)) {
        return rule.path
      }
    }
  }

  return DEFAULT_ICON
})
</script>
