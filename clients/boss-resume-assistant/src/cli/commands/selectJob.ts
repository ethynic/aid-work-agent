/**
 * CLI 子命令 select-job：切换当前招聘职位（薄 renderer，业务能力在 boss_select_job operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js select-job <职位名>
 *
 * 职位名必须精确（可用 list-jobs 查看）。真实写动作，且借用真实鼠标：请勿移动鼠标，勿遮挡 BOSS 窗口。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'

export interface SelectJobCommandOptions {
  /** 目标职位名（精确） */
  jobName: string
  cdpPort?: number
}

export async function selectJobCommand(opts: SelectJobCommandOptions): Promise<number> {
  console.log(`切换职位：切换到「${opts.jobName}」。⚠️ 这是真实写动作，且借用真实鼠标：请勿移动鼠标，勿遮挡 BOSS 窗口。`)
  return runCliOperation(OPERATIONS.boss_select_job!.operation, { job_name: opts.jobName }, { cdpPort: opts.cdpPort })
}
