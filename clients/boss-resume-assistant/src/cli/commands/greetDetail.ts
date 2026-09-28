/**
 * CLI 子命令 greet-detail：在当前打开的简历详情页点「打招呼」（薄 renderer，业务在 boss_greet_detail operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js greet-detail <姓名> [--dry-run]
 *
 * 校验详情属于指定候选人（防打错人）→ 定位详情操作列「打招呼」→ Win32 点击 → 校验翻转
 * 「继续沟通」→ Escape 关闭。已打过幂等返回；--dry-run 只定位不点击。
 * 真实写动作，且借用真实鼠标：期间请勿移动鼠标，勿遮挡 BOSS 窗口。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'

export interface GreetDetailCommandOptions {
  /** 候选人姓名（详情归属校验依据，防打错人） */
  name: string
  /** 只定位不点击（默认 false 真实点击） */
  dryRun?: boolean
  cdpPort?: number
}

export async function greetDetailCommand(opts: GreetDetailCommandOptions): Promise<number> {
  console.log(
    `详情页打招呼：向「${opts.name}」发送打招呼（校验详情属于该候选人，防止打错人；打完自动关闭详情` +
      `${opts.dryRun ? '；本次 --dry-run 只定位不点击' : ''}）。`,
  )
  console.log('⚠️ 这是真实写动作，且借用真实鼠标：请勿移动鼠标，勿遮挡 BOSS 窗口。')
  return runCliOperation(
    OPERATIONS.boss_greet_detail!.operation,
    { name: opts.name, dry_run: opts.dryRun === true },
    { cdpPort: opts.cdpPort },
  )
}
