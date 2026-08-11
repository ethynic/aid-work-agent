/**
 * CLI 子命令 greet：逐个点击推荐列表中可见的「打招呼」按钮（薄 renderer，业务能力在 boss_greet operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js greet [--limit N]
 *
 * 走 Win32 真实鼠标通道，执行期间用户手必须离开鼠标、BOSS 窗口不要被遮挡。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'

export interface GreetCommandOptions {
  /** 上限，默认 10，最大 100（CLI 层校验） */
  limit?: number
  cdpPort?: number
}

export async function greetCommand(opts: GreetCommandOptions): Promise<number> {
  console.log(
    `打招呼：逐个点击「打招呼」按钮（上限 ${opts.limit ?? 10} 个，当前屏点完自动向下滚动，到底结束）。`,
  )
  console.log('⚠️ 这是真实写动作（会向候选人发招呼），且借用真实鼠标：请勿移动鼠标，勿遮挡 BOSS 窗口。')
  return runCliOperation(OPERATIONS.boss_greet!.operation, { limit: opts.limit }, { cdpPort: opts.cdpPort })
}
