/**
 * CLI 子命令 filter-options：查询筛选面板全部可选档位（薄 renderer，业务在 boss_filter_options operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js filter-options
 *
 * 只读探查（开面板 → 读各行选项 → 收起还原），供 AI/用户把口语化要求（15k-20k / 5年以上）
 * 映射成页面实际存在的精确档位后再调 filter。开/收面板借用真实鼠标约 2 秒，期间请勿移动鼠标。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'
import type { OperationResult } from '../../main/operations/types.js'

export interface FilterOptionsCommandOptions {
  cdpPort?: number
}

export async function filterOptionsCommand(opts: FilterOptionsCommandOptions): Promise<number> {
  console.log('查询筛选可选档位：打开筛选面板读取各行选项后收起还原。只读，但开/收面板借用真实鼠标（约 2 秒），期间请勿移动鼠标。')
  const onSuccess = (result: OperationResult): void => {
    const lines = result.data.readable_lines
    if (Array.isArray(lines)) {
      console.log('\n===== 可选档位 =====')
      for (const line of lines) console.log(`  ${String(line)}`)
      console.log('====================')
    }
  }
  return runCliOperation(OPERATIONS.boss_filter_options!.operation, {}, { cdpPort: opts.cdpPort, onSuccess })
}
