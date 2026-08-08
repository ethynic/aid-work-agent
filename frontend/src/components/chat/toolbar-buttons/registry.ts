/**
 * 聊天工具栏按钮注册表（Phase 5.1.1）
 *
 * 设计依据：docs/plans/plan-video-agent-phase1.md §5.1
 *
 * 不同子智能体会话显示不同按钮组，前端按 id 注册表渲染。
 * 加号上传按钮为所有智能体共有，由 ChatInput 硬编码渲染，不放入注册表。
 *
 * 注册新按钮：
 * 1. 在 toolbar-buttons/ 下新建 <ButtonName>.vue 组件
 * 2. 在本文件 REGISTRY 注册 id -> 组件映射，含 icon/label/order 元信息
 * 3. 在需要启用的子智能体 SUBAGENT.md frontmatter 加 chat_toolbar: [<new_id>]
 */
import type { Component } from 'vue'
import VideoGenButton from './VideoGenButton.vue'

export interface ToolbarButtonMeta {
  /** 按钮 id（与 SUBAGENT.md frontmatter chat_toolbar 中的字符串一致） */
  id: string
  /** 按钮组件 */
  component: Component
  /** 鼠标悬浮提示 */
  label: string
  /** 排序权重（小在前） */
  order: number
}

/** 按钮注册表（按 order 升序排列） */
const REGISTRY: ToolbarButtonMeta[] = [
  {
    id: 'video_gen',
    component: VideoGenButton,
    label: '视频生成',
    order: 100,
  },
]

/** id -> meta 映射，便于 O(1) 查找 */
const REGISTRY_MAP: Record<string, ToolbarButtonMeta> = REGISTRY.reduce(
  (acc, meta) => {
    acc[meta.id] = meta
    return acc
  },
  {} as Record<string, ToolbarButtonMeta>,
)

/**
 * 按 buttonIds 顺序返回按钮 meta（已注册的才返回，未注册的跳过）。
 * 返回结果按 meta.order 升序排序。
 */
export function resolveToolbarButtons(buttonIds: string[] | undefined | null): ToolbarButtonMeta[] {
  if (!buttonIds || buttonIds.length === 0) return []
  const metas: ToolbarButtonMeta[] = []
  for (const id of buttonIds) {
    const meta = REGISTRY_MAP[id]
    if (meta) {
      metas.push(meta)
    } else {
      console.warn(`[ChatToolbar] 未注册的工具栏按钮 id: ${id}`)
    }
  }
  return metas.sort((a, b) => a.order - b.order)
}
