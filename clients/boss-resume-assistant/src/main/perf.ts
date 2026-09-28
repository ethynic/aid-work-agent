/**
 * 操作性能埋点（2026-09-28 性能优化前置：先有分步耗时数据，再做数据驱动优化）。
 *
 * 脱敏约束：只记步骤名称与毫秒数，绝不记页面内容/坐标/文本/候选人信息
 * （与 bossContext.logOpFailure 同口径，经 providerManager 落 runtime.log）。
 */

/** 单个步骤名的聚合结果（count 次调用的累计/最大毫秒） */
export interface PerfEntry {
  count: number
  totalMs: number
  maxMs: number
}

export class PerfCollector {
  private readonly entries = new Map<string, PerfEntry>()

  /**
   * 计时执行 fn 并按 name 聚合 count/totalMs/maxMs。
   * fn 抛错同样记时（失败路径的耗时也是优化数据），然后原样重新抛出。
   */
  async time<T>(name: string, fn: () => Promise<T>): Promise<T> {
    const start = Date.now()
    try {
      return await fn()
    } finally {
      this.record(name, Date.now() - start)
    }
  }

  /** 某步骤名的累计毫秒（未记录过返回 0）；runBossOperation 行首 connect/body/close 用 */
  totalMsOf(name: string): number {
    return this.entries.get(name)?.totalMs ?? 0
  }

  /**
   * 单行摘要片段：`name{n=N,total_ms=T,max_ms=M}` 以空格连接，按 total_ms 降序
   * （耗时大头排前，便于肉眼扫日志）；无任何记录时返回空串。
   */
  summaryParts(): string {
    return [...this.entries.entries()]
      .sort((a, b) => b[1].totalMs - a[1].totalMs)
      .map(([name, e]) => `${name}{n=${e.count},total_ms=${e.totalMs},max_ms=${e.maxMs}}`)
      .join(' ')
  }

  /** 只读深拷贝快照（仅供单测断言聚合结果，不参与运行时输出） */
  snapshot(): Map<string, PerfEntry> {
    return new Map([...this.entries].map(([name, e]) => [name, { ...e }]))
  }

  private record(name: string, ms: number): void {
    const e = this.entries.get(name)
    if (e) {
      e.count += 1
      e.totalMs += ms
      e.maxMs = Math.max(e.maxMs, ms)
    } else {
      this.entries.set(name, { count: 1, totalMs: ms, maxMs: ms })
    }
  }
}
