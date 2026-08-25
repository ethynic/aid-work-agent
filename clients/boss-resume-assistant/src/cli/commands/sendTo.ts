/**
 * CLI 子命令 send-to：搜索找人 → 进入对话 → 输入并发送消息（薄 renderer，业务能力在 boss_send_to operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js send-to <姓名> --message <消息> [--dry-run]
 *
 * 默认真发送（写动作）。--dry-run 只输入不点发送（测试链路）。
 * 走 Win32 真实鼠标 + CDP 逐字输入：期间请勿移动鼠标，勿遮挡 BOSS 窗口。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'

export interface SendToCommandOptions {
  /** 联系人姓名（搜索关键词） */
  to: string
  /** 要发送的消息内容 */
  message: string
  /** 只输入不点发送（测试链路） */
  dryRun?: boolean
  cdpPort?: number
}

export async function sendToCommand(opts: SendToCommandOptions): Promise<number> {
  console.log(`发送消息：搜索联系人「${opts.to}」→ 进入对话 → 输入${opts.dryRun ? '（dry-run 不发送）' : '并发送'}消息。`)
  if (opts.dryRun) {
    console.log('dry-run 模式：只输入消息不点发送（测试链路，无真实发送）。')
  } else {
    console.log('⚠️ 这是真实写动作（会向对方发送消息），且借用真实鼠标：请勿移动鼠标，勿遮挡 BOSS 窗口。')
  }
  return runCliOperation(
    OPERATIONS.boss_send_to!.operation,
    { to: opts.to, message: opts.message, dry_run: opts.dryRun === true },
    { cdpPort: opts.cdpPort },
  )
}
