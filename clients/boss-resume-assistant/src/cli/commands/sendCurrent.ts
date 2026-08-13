/**
 * CLI 子命令 send-current：向当前已选会话输入并发送消息（薄 renderer，业务能力在 boss_send_current operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js send-current --message <消息> [--dry-run]
 *
 * 前提：沟通页已选中一个会话（右侧有发送按钮）。默认真发送（写动作）；--dry-run 只输入不点发送。
 * 走 Win32 真实鼠标 + CDP 逐字输入：期间请勿移动鼠标，勿遮挡 BOSS 窗口。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'

export interface SendCurrentCommandOptions {
  /** 要发送的消息内容 */
  message: string
  /** 只输入不点发送（测试链路） */
  dryRun?: boolean
  cdpPort?: number
}

export async function sendCurrentCommand(opts: SendCurrentCommandOptions): Promise<number> {
  console.log(`发送消息：向当前已选会话输入${opts.dryRun ? '（dry-run 不发送）' : '并发送'}消息。`)
  if (opts.dryRun) {
    console.log('dry-run 模式：只输入消息不点发送（测试链路，无真实发送）。')
  } else {
    console.log('⚠️ 这是真实写动作（会向对方发送消息），且借用真实鼠标：请勿移动鼠标，勿遮挡 BOSS 窗口。')
  }
  return runCliOperation(
    OPERATIONS.boss_send_current!.operation,
    { message: opts.message, dry_run: opts.dryRun === true },
    { cdpPort: opts.cdpPort },
  )
}
