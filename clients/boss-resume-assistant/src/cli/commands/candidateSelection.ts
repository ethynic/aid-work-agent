/** 同名卡片只读查询与用户选定后的单人打招呼；业务只在 operation 实现。 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'

export async function lookupCandidatesCommand(opts: { names: string[]; cdpPort?: number }): Promise<number> {
  return runCliOperation(OPERATIONS.boss_lookup_candidates!.operation, { names: opts.names }, { cdpPort: opts.cdpPort })
}

export async function greetSelectedCommand(opts: { name: string; candidate_ref: string; cdpPort?: number }): Promise<number> {
  console.log('⚠️ 向用户明确选定的单张卡片打招呼；真实写动作，期间请勿移动鼠标。')
  return runCliOperation(OPERATIONS.boss_greet_selected!.operation,
    { name: opts.name, candidate_ref: opts.candidate_ref }, { cdpPort: opts.cdpPort })
}
