/**
 * 内置受信 manifest（静态常量，不接受云端下发 executable/cwd/env/argv）。
 *
 * - claim 到的 tool_name 必须 ∈ TRUSTED_MANIFEST.tools，否则直接回 TOOL_NOT_ALLOWED。
 * - manifestDigest 随 heartbeat 上报，供云端核对。
 */
import { createHash } from 'node:crypto'

export const TRUSTED_MANIFEST = {
  provider_key: 'boss-recruiting',
  provider_id: 'ai.aidwork.boss-recruiting',
  tools: [
    'boss_filter',
    'boss_clear_filter',
    'boss_goto',
    'boss_greet',
    'boss_accept_resume',
    'boss_reject_current',
    'boss_interview_demo',
  ],
  execution_target: 'local_required',
} as const

/** 写动作集合：外部副作用不可逆，崩溃/锁屏策略与只读不同 */
const WRITE_TOOLS: ReadonlySet<string> = new Set([
  'boss_greet',
  'boss_accept_resume',
  'boss_reject_current',
  'boss_interview_demo',
])

export function isToolAllowed(toolName: string): boolean {
  return (TRUSTED_MANIFEST.tools as readonly string[]).includes(toolName)
}

export function isWriteTool(toolName: string): boolean {
  return WRITE_TOOLS.has(toolName)
}

export function manifestDigest(): string {
  const stable = JSON.stringify({
    provider_id: TRUSTED_MANIFEST.provider_id,
    tools: [...TRUSTED_MANIFEST.tools].sort(),
    execution_target: TRUSTED_MANIFEST.execution_target,
  })
  return createHash('sha256').update(stable).digest('hex')
}
