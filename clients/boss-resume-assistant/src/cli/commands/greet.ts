/**
 * CLI 子命令 greet：逐个点击推荐牛人列表中可见的「打招呼」按钮（薄 renderer，业务能力在 boss_greet operation）。
 *
 * 用法（2026-08-26 语义收紧：必须显式选择模式，防止误触发真实写动作）：
 *   node dist/src/cli/index.js greet --names 冯修业,李四 [--limit N]   定向：只打姓名精确匹配的人（最多 3 人）
 *   node dist/src/cli/index.js greet --all [--limit N]                 全量：从列表顶部逐个打（默认 10 上限，最大 100）
 *
 * 走 Win32 真实鼠标通道，执行期间用户手必须离开鼠标、BOSS 窗口不要被遮挡。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'

export interface GreetCommandOptions {
  /** 上限，默认 10，最大 100（CLI 层校验） */
  limit?: number
  /** 定向名单（与 all 互斥，校验在 validate.ts）：只打姓名精确匹配的卡片，配对失败一律跳过 */
  names?: string[]
  /** 全量模式显式标记（与 names 互斥）：从推荐列表顶部逐个打 */
  all?: boolean
  cdpPort?: number
}

export async function greetCommand(opts: GreetCommandOptions): Promise<number> {
  // 防御性门禁（正常路径已被 validateCommandArgs 拦截）：两模式都没给时拒绝执行，
  // 避免任何绕过校验层的调用又回到「默认全量 10 人」的旧行为
  if (opts.names === undefined && !opts.all) {
    console.error('greet 需要显式选择模式：定向请传 names，全量请传 all（防止误触发真实写动作）')
    return 2
  }
  if (opts.names !== undefined) {
    console.log(
      `定向打招呼：目标 ${opts.names.length} 人（${opts.names.join('、')}），只点姓名精确匹配的卡片，配对失败一律跳过——宁可不打，不能打错。`,
    )
    console.log('⚠️ 这是真实写动作（会向候选人发招呼），且借用真实鼠标：请勿移动鼠标，勿遮挡 BOSS 窗口。')
    // 定向模式传 { limit, names }；operation 层 names 留 undefined 即全量
    return runCliOperation(
      OPERATIONS.boss_greet!.operation,
      { limit: opts.limit, names: opts.names },
      { cdpPort: opts.cdpPort },
    )
  }
  console.log(
    `打招呼（全量模式 --all）：从推荐列表顶部逐个点击「打招呼」按钮（上限 ${opts.limit ?? 10} 个，当前屏点完自动向下滚动，到底结束）。`,
  )
  console.log('⚠️ 这是真实写动作（会向列表顶部的候选人依次发招呼），且借用真实鼠标：请勿移动鼠标，勿遮挡 BOSS 窗口。')
  // 全量模式：不传 names（operation 层 names=undefined 即非定向全量）
  return runCliOperation(OPERATIONS.boss_greet!.operation, { limit: opts.limit }, { cdpPort: opts.cdpPort })
}
