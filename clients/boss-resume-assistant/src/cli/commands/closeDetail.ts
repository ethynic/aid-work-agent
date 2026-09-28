/**
 * CLI 子命令 close-detail：关闭当前打开的简历详情弹层（薄 renderer，业务在 boss_close_detail operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js close-detail
 *
 * 筛选不合格时的清理步（boss_open_detail → boss_resume_detail 后不打招呼则关闭）；
 * 无业务副作用；详情本来就没打开时幂等成功。Escape 借用键盘事件，期间请勿动鼠标键盘。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'

export interface CloseDetailCommandOptions {
  cdpPort?: number
}

export async function closeDetailCommand(opts: CloseDetailCommandOptions): Promise<number> {
  console.log('关闭当前打开的简历详情弹层（无业务副作用；详情未打开时幂等成功）。')
  return runCliOperation(OPERATIONS.boss_close_detail!.operation, {}, { cdpPort: opts.cdpPort })
}
