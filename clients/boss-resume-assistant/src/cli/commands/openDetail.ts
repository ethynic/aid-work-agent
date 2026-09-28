/**
 * CLI 子命令 open-detail：打开指定姓名候选人的简历详情（薄 renderer，业务在 boss_open_detail operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js open-detail <姓名>
 *
 * 推荐牛人页按姓名找人（当前屏没有自动滚动查找）并点开在线简历详情；详情保持打开，
 * 供 resume-detail 读取。点击与找人滚动借用真实鼠标，期间请勿移动鼠标。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'

export interface OpenDetailCommandOptions {
  /** 候选人姓名（精确，与列表卡片姓名一致） */
  name: string
  cdpPort?: number
}

export async function openDetailCommand(opts: OpenDetailCommandOptions): Promise<number> {
  console.log(
    `打开候选人简历详情：在推荐牛人列表查找「${opts.name}」并点开（详情保持打开，供 resume-detail 读取）。` +
      '点击与找人滚动借用真实鼠标，期间请勿移动鼠标。',
  )
  return runCliOperation(OPERATIONS.boss_open_detail!.operation, { name: opts.name }, { cdpPort: opts.cdpPort })
}
