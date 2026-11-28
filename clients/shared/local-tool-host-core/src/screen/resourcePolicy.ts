/**
 * 任务优先于观看，但观看就是为了看任务执行，所以能降画质就不停观看（设计 4.5）。
 *
 * 每 5 秒一个采样。「资源不足」指这台电脑正在执行 Agent 任务，且电脑 CPU 占用达到阈值，
 * 或编码器报告因 CPU 受限。连续两次都是资源不足才动作，中途一次不再不足就把计数清零：
 *   降低画质（继续观看）→ 降低后仍连续两次不足才停止观看 → 任务结束后恢复。
 * 任务还在跑但资源不再紧张时不恢复；没有任务在跑时不做任何动作。
 */

/** 电脑 CPU 占用达到这个百分比算资源不足。暂定，Phase 4 用真实任务校准 */
export const CPU_BUSY_THRESHOLD_PERCENT = 90
/** 连续多少次采样资源不足才动作 */
export const RESOURCE_BUSY_STREAK = 2

export interface ResourceSample {
  taskRunning: boolean
  /** 整机忙碌百分比；读数不可用时为 null，此时只看编码器的信号 */
  machineCpuBusy: number | null
  /** 编码器是否报告因 CPU 受限（连接统计里发送方向的 qualityLimitationReason 为 cpu） */
  encoderCpuLimited: boolean
}

export type ResourceAction = 'none' | 'degrade' | 'restore' | 'stop'

export class ResourcePolicy {
  private streak = 0
  private lowered = false

  /** 当前是否处于降低画质的状态 */
  get degraded(): boolean {
    return this.lowered
  }

  sample(sample: ResourceSample): ResourceAction {
    if (!sample.taskRunning) {
      this.streak = 0
      if (!this.lowered) return 'none'
      this.lowered = false
      return 'restore'
    }
    const insufficient = (sample.machineCpuBusy !== null && sample.machineCpuBusy >= CPU_BUSY_THRESHOLD_PERCENT) || sample.encoderCpuLimited
    this.streak = insufficient ? this.streak + 1 : 0
    if (this.streak < RESOURCE_BUSY_STREAK) return 'none'
    this.streak = 0
    if (this.lowered) return 'stop'
    this.lowered = true
    return 'degrade'
  }
}
