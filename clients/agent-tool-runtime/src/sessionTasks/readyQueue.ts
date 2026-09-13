/**
 * 会话任务就绪队列（C2，设计 §7）。
 *
 * 取出顺序：next_due_at（到期才可服务）→ 优先级（control > send > observe）
 * → 上次服务时间 FIFO。连续服务同任务最多 1 个动作单元即让出（excludeTaskId），
 * 除非它是唯一就绪项（由调用方决定是否破例；本队列如实返回 null 让出）。
 */
export type QueueKind = 'control' | 'send' | 'observe'

export const QUEUE_PRIORITY: Record<QueueKind, number> = { control: 0, send: 1, observe: 2 }

export interface QueueEntry {
  taskId: string
  kind: QueueKind
  /** 下次可服务时刻（ms epoch）；到期项才可被 pop */
  nextDueAt: number
  /** 上次服务时刻（公平 FIFO 键；未服务过为 0） */
  lastServedAt: number
}

export class ReadyQueue {
  private readonly entries = new Map<string, QueueEntry>()

  /** 新增或更新任务项（相位迁移时改 kind/nextDueAt） */
  set(taskId: string, kind: QueueKind, nextDueAt: number): void {
    const existing = this.entries.get(taskId)
    this.entries.set(taskId, { taskId, kind, nextDueAt, lastServedAt: existing?.lastServedAt ?? 0 })
  }

  remove(taskId: string): void {
    this.entries.delete(taskId)
  }

  get(taskId: string): QueueEntry | undefined {
    return this.entries.get(taskId)
  }

  get size(): number {
    return this.entries.size
  }

  dueCount(now: number): number {
    let n = 0
    for (const e of this.entries.values()) if (e.nextDueAt <= now) n++
    return n
  }

  /**
   * 取下一个应服务项；excludeTaskId = 刚服务完的任务（让位）。
   * 无到期项（或仅排除项到期）返回 null。
   */
  peek(now: number, opts?: { excludeTaskId?: string }): QueueEntry | null {
    let best: QueueEntry | null = null
    for (const e of this.entries.values()) {
      if (e.nextDueAt > now) continue
      if (opts?.excludeTaskId && e.taskId === opts.excludeTaskId && this.dueCount(now) > 1) continue
      if (best === null) {
        best = e
        continue
      }
      if (QUEUE_PRIORITY[e.kind] < QUEUE_PRIORITY[best.kind]) {
        best = e
      } else if (QUEUE_PRIORITY[e.kind] === QUEUE_PRIORITY[best.kind] && e.lastServedAt < best.lastServedAt) {
        best = e
      }
    }
    return best
  }

  /** 服务完成：刷新上次服务时间（不改变 due/priority，由调用方 set 更新） */
  markServed(taskId: string, now: number): void {
    const e = this.entries.get(taskId)
    if (e) e.lastServedAt = now
  }
}
