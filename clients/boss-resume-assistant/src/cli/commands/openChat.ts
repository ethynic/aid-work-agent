/**
 * CLI 子命令 open-chat：切换到指定联系人的会话（薄 renderer，业务能力在 boss_open_chat operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js open-chat <姓名>
 *
 * read-chat 只能读当前会话；open-chat 负责切换：已在目标会话零点击；否则优先搜索找人，
 * 搜索失败回退点击左侧会话列表项（视口外自动滚动）。无外部写副作用（不发消息），但
 * 搜索/列表点击与姓名输入借用真实鼠标，操作期间勿动鼠标。打开后可接着 read-chat 读取消息。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'

export interface OpenChatCommandOptions {
  /** 要打开的联系人姓名（必填） */
  contact: string
  cdpPort?: number
}

/** via 的用户可读路径名（operation message 已含同样信息，此处为 data 渲染兜底） */
const VIA_TEXT: Record<string, string> = {
  already: '已在目标会话（未点击）',
  search: '已通过搜索打开',
  list: '已通过会话列表点击打开',
}

export async function openChatCommand(opts: OpenChatCommandOptions): Promise<number> {
  const target = opts.contact.trim()
  if (!target) {
    console.error('open-chat 缺少联系人姓名：open-chat <姓名>')
    return 2
  }
  console.log(`打开会话「${target}」（优先搜索找人，失败回退点击会话列表；借用真实鼠标，期间勿动鼠标）。`)
  return runCliOperation(
    OPERATIONS.boss_open_chat!.operation,
    { contact: target },
    {
      cdpPort: opts.cdpPort,
      onSuccess: (result) => {
        const via = String(result.data.via ?? '')
        if (VIA_TEXT[via] !== undefined) {
          console.log(`\n${VIA_TEXT[via]}「${String(result.data.contact ?? target)}」的会话，可接着执行 read-chat 读取消息。`)
        }
      },
    },
  )
}
