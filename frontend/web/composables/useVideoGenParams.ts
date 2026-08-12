/**
 * 视频生成参数状态管理（Phase 5.1.6）
 *
 * 设计依据：docs/plans/plan-video-agent-phase1.md §5.1
 *
 * 参数状态在会话内持久化，发送时附加到消息。
 * 用法：
 *   const { params, setMode, setDuration, ... } = useVideoGenParams()
 *   // 弹框确认后调用 commit() 把临时值写入 params
 *   // 发送消息时把 params.value 附加到 payload
 */
import { ref, computed } from 'vue'

export type VideoGenMode = 'refine' | 'agile'

export interface VideoGenParams {
  /** 创作模式：精修 / 敏捷 */
  mode: VideoGenMode
  /** 视频时长（秒） */
  duration_sec: number
  /** 视频比例 */
  ratio: string
  /** 分辨率 */
  resolution: string
  /** 生成条数（精修模式固定 1） */
  card_count: number
  /** 提示词模型（覆盖 SUBAGENT.md 默认值），如 'qwen-vl-max' */
  prompt_model: string
}

export const DEFAULT_PARAMS: VideoGenParams = {
  mode: 'refine',
  duration_sec: 5,
  ratio: '9:16',
  resolution: '720P',
  card_count: 1,
  prompt_model: 'qwen-vl-plus',
}

/** 全局单例状态（在同一会话内保持，切换会话由调用方重置） */
const params = ref<VideoGenParams>({ ...DEFAULT_PARAMS })

/** 弹框临时值（编辑中未确认） */
const draft = ref<VideoGenParams>({ ...DEFAULT_PARAMS })
const isDialogOpen = ref(false)

export function useVideoGenParams() {
  /** 当前生效参数（只读视图，调用方不应直接修改） */
  const currentParams = computed(() => params.value)

  /** 打开弹框，把当前 params 复制到 draft */
  function openDialog() {
    draft.value = { ...params.value }
    isDialogOpen.value = true
  }

  /** 关闭弹框（不应用 draft） */
  function closeDialog() {
    isDialogOpen.value = false
  }

  /** 确认弹框（把 draft 写入 params） */
  function commitDialog() {
    // 精修模式强制 card_count=1
    if (draft.value.mode === 'refine') {
      draft.value.card_count = 1
    }
    params.value = { ...draft.value }
    isDialogOpen.value = false
  }

  /** 直接更新 draft 字段（弹框内调用） */
  function updateDraft(field: keyof VideoGenParams, value: any) {
    ;(draft.value as any)[field] = value
    // 切换到精修模式时，强制 card_count=1
    if (field === 'mode' && value === 'refine') {
      draft.value.card_count = 1
    }
  }

  /** 重置为默认值（切换会话时调用） */
  function reset() {
    params.value = { ...DEFAULT_PARAMS }
    draft.value = { ...DEFAULT_PARAMS }
  }

  return {
    params: currentParams,
    draft,
    isDialogOpen,
    openDialog,
    closeDialog,
    commitDialog,
    updateDraft,
    reset,
  }
}
