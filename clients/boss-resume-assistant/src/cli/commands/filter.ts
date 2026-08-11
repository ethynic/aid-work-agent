/**
 * CLI 子命令 filter：自动设置推荐牛人页筛选面板（薄 renderer，业务能力在 boss_filter operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js filter --experience 5-10年 --education 本科,硕士,博士 --salary 10-20K
 *   node dist/src/cli/index.js filter --clear
 *
 * 筛选类控件被 BOSS 风控拦截 CDP 合成点击，全部走 Win32 真实鼠标通道。
 * 执行期间借用真实光标，用户手必须离开鼠标、BOSS 窗口不要被遮挡。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'

export interface FilterCommandOptions {
  experience?: string
  educations?: string[]
  salary?: string
  cdpPort?: number
  /** 清除模式：开面板 → 清除 → 确定，清空全部筛选条件 */
  clear?: boolean
}

export async function filterCommand(opts: FilterCommandOptions): Promise<number> {
  if (opts.clear) {
    console.log('清除筛选：打开面板 → 清除 → 确定。')
    console.log('⚠️ 即将通过 Win32 真实鼠标操作筛选面板：请勿移动鼠标，勿遮挡 BOSS 窗口。')
    return runCliOperation(OPERATIONS.boss_clear_filter!.operation, {}, { cdpPort: opts.cdpPort })
  }
  console.log('筛选条件:', JSON.stringify({ experience: opts.experience, educations: opts.educations, salary: opts.salary }))
  console.log('⚠️ 即将通过 Win32 真实鼠标操作筛选面板：请勿移动鼠标，勿遮挡 BOSS 窗口。')
  return runCliOperation(
    OPERATIONS.boss_filter!.operation,
    { experience: opts.experience, educations: opts.educations, salary: opts.salary },
    { cdpPort: opts.cdpPort },
  )
}
