/**
 * CLI 子命令 list-jobs：列出当前招聘者的所有职位（薄 renderer，业务能力在 boss_list_jobs operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js list-jobs
 *
 * 打开职位下拉解析当前招聘者的全部职位（职位名/城市/薪资）。只读操作，但需借用真实鼠标点开下拉，
 * 执行期间请勿移动鼠标。用于在 select-job 前确认精确职位名。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'

export interface ListJobsCommandOptions {
  cdpPort?: number
}

export async function listJobsCommand(opts: ListJobsCommandOptions): Promise<number> {
  console.log('列出职位：打开职位下拉解析当前招聘者的所有职位。只读操作，但需借用真实鼠标点开下拉，请勿移动鼠标。')
  return runCliOperation(OPERATIONS.boss_list_jobs!.operation, {}, { cdpPort: opts.cdpPort })
}
