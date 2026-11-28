/**
 * 整台电脑的 CPU 占用（设计 4.5）。os.cpus() 给每个核的各项累计时间，前后两次读数相减：
 * 忙碌百分比 = 1 - Δidle / Δtotal。Windows 上 idle 字段就是空闲时间，nice 和 irq 一般是 0。
 *
 * 读数不可用、或者两次读数之间没有间隔时返回 null：调用方据此只去看编码器的信号，
 * 而不是把读数当成 0% 或 100%。
 */
import { cpus, type CpuInfo } from 'node:os'

export type CpuTimes = CpuInfo['times']

function totals(cores: readonly CpuTimes[] | null | undefined): { idle: number; total: number } | null {
  if (!cores || cores.length === 0) return null
  let idle = 0
  let total = 0
  for (const times of cores) {
    idle += times.idle
    total += times.user + times.nice + times.sys + times.idle + times.irq
  }
  return { idle, total }
}

/** 两次读数之间的整机忙碌百分比（0～100）；读数不可用或 Δtotal 为 0 时为 null */
export function busyPercentBetween(previous: readonly CpuTimes[] | null | undefined, current: readonly CpuTimes[] | null | undefined): number | null {
  const before = totals(previous)
  const after = totals(current)
  if (!before || !after) return null
  const deltaTotal = after.total - before.total
  if (deltaTotal <= 0) return null
  return (1 - (after.idle - before.idle) / deltaTotal) * 100
}

/**
 * 读数器：每次调用读一次 os.cpus()，与上一次读数比较。第一次调用只记基线，返回 null。
 */
export function createCpuBusyReader(read: () => readonly CpuInfo[] | null | undefined = () => cpus()): () => number | null {
  let previous: readonly CpuTimes[] | null | undefined
  return () => {
    const cores = read()?.map((cpu) => cpu.times)
    const busy = busyPercentBetween(previous, cores)
    previous = cores
    return busy
  }
}
