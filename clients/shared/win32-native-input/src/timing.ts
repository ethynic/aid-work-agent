/**
 * 输入序列内建分段计时（Phase 1 要求：替代旧路径整段计时，单行可解析）。
 * 输出格式与 [boss-perf] 家族一致（key=value 空格分隔，stderr 单行），只记阶段与毫秒数。
 */

/** 输入链路的固定阶段名（按发生顺序） */
export type InputPhase =
  | 'activate'   // 前台激活（绑定模式找窗/AttachThreadInput 或非绑定强制前台）
  | 'discover'   // 窗口探查（render widget 定位 + 坐标换算）
  | 'guard'      // 落点守卫（含抬窗重试）
  | 'move'       // 拟人移动 + 悬停
  | 'press'      // 左键按下
  | 'drag'       // 拖动
  | 'type'       // 逐字键入
  | 'finalize'   // 收尾（LEFTUP 后至 DPI 上下文恢复）

export class SegmentTimer {
  private readonly start = Date.now()
  private readonly phases = new Map<InputPhase, number>()

  /** 同步片段计时 */
  measure<T>(phase: InputPhase, body: () => T): T {
    const begin = Date.now()
    try {
      return body()
    } finally {
      this.record(phase, Date.now() - begin)
    }
  }

  /** 异步片段计时 */
  async measureAsync<T>(phase: InputPhase, body: () => Promise<T>): Promise<T> {
    const begin = Date.now()
    try {
      return await body()
    } finally {
      this.record(phase, Date.now() - begin)
    }
  }

  private record(phase: InputPhase, ms: number): void {
    this.phases.set(phase, (this.phases.get(phase) ?? 0) + ms)
  }

  /** 汇总单行（固定阶段顺序，只输出出现过的阶段） */
  summaryLine(op: string, ok: boolean): string {
    const order: InputPhase[] = ['activate', 'discover', 'guard', 'move', 'press', 'drag', 'type', 'finalize']
    const parts = order.filter((phase) => this.phases.has(phase)).map((phase) => `${phase}_ms=${this.phases.get(phase)}`)
    return `[boss-perf] tool=win32-native-input op=${op} ok=${ok ? 1 : 0} total_ms=${Date.now() - this.start}${parts.length ? ' ' + parts.join(' ') : ''}`
  }
}
