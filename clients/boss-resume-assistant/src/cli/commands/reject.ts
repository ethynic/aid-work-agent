/**
 * CLI 子命令 reject：沟通页把当前会话的候选人标记为「不合适」（薄 renderer，业务能力在 boss_reject_current operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js reject
 *
 * 真实写动作 + Win32 真实鼠标：期间请勿移动鼠标，勿遮挡 BOSS 窗口。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'

export interface RejectCommandOptions {
  cdpPort?: number
}

export async function rejectCommand(opts: RejectCommandOptions): Promise<number> {
  console.log('标记不合适：点击当前会话右侧面板的「不合适」按钮（仅当前会话，一次一个）。')
  console.log('⚠️ 这是真实写动作，且借用真实鼠标：请勿移动鼠标，勿遮挡 BOSS 窗口。')
  return runCliOperation(OPERATIONS.boss_reject_current!.operation, {}, { cdpPort: opts.cdpPort })
}
