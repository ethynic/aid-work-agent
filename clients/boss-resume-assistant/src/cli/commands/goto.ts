/**
 * CLI 子命令 goto：点击左侧导航菜单跳转页面（薄 renderer，业务能力在 boss_goto operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js goto recommend   # 跳到「推荐牛人」
 *   node dist/src/cli/index.js goto chat        # 跳到「沟通」（看打招呼回复）
 *
 * 走 Win32 真实鼠标通道，执行期间请勿移动鼠标。已在目标页时跳过（幂等）。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'

const ALIASES: Record<string, 'recommend' | 'chat'> = {
  recommend: 'recommend',
  推荐牛人: 'recommend',
  chat: 'chat',
  沟通: 'chat',
}

export interface GotoCommandOptions {
  target: string
  cdpPort?: number
}

export async function gotoCommand(opts: GotoCommandOptions): Promise<number> {
  const target = ALIASES[opts.target]
  if (!target) {
    console.error(`❌ 未知目标页面「${opts.target}」，支持：recommend（推荐牛人）/ chat（沟通）`)
    return 2
  }
  console.log(`页面跳转：点击左侧菜单「${target === 'recommend' ? '推荐牛人' : '沟通'}」。请勿移动鼠标。`)
  return runCliOperation(OPERATIONS.boss_goto!.operation, { target }, { cdpPort: opts.cdpPort })
}
