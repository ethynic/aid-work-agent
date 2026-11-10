/**
 * BOSS 链路等待节奏工具（2026-11-10 读简历提速 Phase 1）。
 *
 * 为什么区间随机化：固定间隔是可被识别的机器人特征——BOSS 风控对操作事件做时序画像，
 * 每份简历都精确等 900ms/2000ms 属于强机器指纹；把等待改成区间内均匀随机（拟人抖动）
 * 可在把固定 sleep 预算整体收紧的同时降低风控识别风险（提速与拟人不冲突）。
 */

/** 区间等待常量的共享类型：min/max 均为毫秒，调用方经 jitter(delay.min, delay.max) 取值 */
export interface DelayRange {
  min: number
  max: number
}

/**
 * 返回 [minMs, maxMs] 闭区间内均匀分布的随机整数（含两端）。
 *
 * @param minMs 区间下界（毫秒）
 * @param maxMs 区间上界（毫秒，须 ≥ minMs）
 * @param rand  随机源（默认 Math.random，返回 [0,1) 浮点）；单测注入固定序列做确定性断言
 */
export function jitter(minMs: number, maxMs: number, rand: () => number = Math.random): number {
  return Math.floor(rand() * (maxMs - minMs + 1)) + minMs
}
