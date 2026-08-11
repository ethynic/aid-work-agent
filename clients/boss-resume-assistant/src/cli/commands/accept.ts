/**
 * CLI 子命令 accept：沟通页批量「同意」接收附件简历（薄 renderer，业务能力在 boss_accept_resume operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js accept [--limit N] [--no-preview]
 *
 * 真实写动作 + Win32 真实鼠标：期间请勿移动鼠标，勿遮挡 BOSS 窗口。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'

export interface AcceptCommandOptions {
  /** 上限，默认 20，最大 100（CLI 层校验） */
  limit?: number
  /** 同意后接着点开附件简历预览再关闭（默认开启，--no-preview 关闭） */
  preview?: boolean
  cdpPort?: number
}

export async function acceptCommand(opts: AcceptCommandOptions): Promise<number> {
  console.log(`同意接收附件简历：逐个打开「对方想发送附件简历」的会话并点「同意」（上限 ${opts.limit ?? 20} 个）。`)
  console.log('⚠️ 这是真实写动作，且借用真实鼠标：请勿移动鼠标，勿遮挡 BOSS 窗口。')
  return runCliOperation(
    OPERATIONS.boss_accept_resume!.operation,
    { limit: opts.limit, preview: opts.preview },
    { cdpPort: opts.cdpPort },
  )
}
