/**
 * CLI 子命令 read-chat：读取当前会话消息与全部未读清单（薄 renderer，业务能力在 boss_read_chat operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js read-chat [姓名]
 *
 * 只读：读「对方回了什么」——当前会话消息（我/对方/系统 + 时间 + 已读）+ 左列表全部未读会话 +
 * 左导航总未读徽章。单次 DOMSnapshot，不借用真实鼠标、无写副作用。
 * 可选位置参数姓名：校验当前打开的会话是否是该联系人，不匹配报错（本工具不自动切换会话）。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'
import type { ChatReadResult } from '../../main/boss/ChatReadExecutor.js'

export interface ReadChatCommandOptions {
  /** 可选：联系人姓名（校验当前会话；不匹配报错并列出可用联系人） */
  contact?: string
  cdpPort?: number
}

/** 消息发送方的展示名（me/them/system → 我/对方/系统） */
const SENDER_LABEL: Record<string, string> = { me: '我', them: '对方', system: '系统' }

/** onSuccess 渲染：contact + 消息流（按 sender 标 我/对方/系统）+ 未读清单 */
function renderResult(data: Record<string, unknown>): void {
  const r = data as Partial<ChatReadResult>
  if (Array.isArray(r.messages) && r.messages.length > 0) {
    console.log(`\n与「${r.contact || '未知联系人'}」的会话消息（${r.messages.length} 条）：`)
    for (const m of r.messages) {
      const parts = [`  ${SENDER_LABEL[m.sender] ?? m.sender}`]
      if (m.ts !== undefined) parts.push(m.ts)
      if (m.read === true) parts.push('已读')
      console.log(`${parts.join(' · ')} · ${m.text}`)
    }
  } else if (r.contact !== undefined) {
    console.log(`\n与「${r.contact || '未知联系人'}」的会话暂无可见消息。`)
  }
  if (Array.isArray(r.unread)) {
    if (r.unread.length === 0) {
      console.log('\n未读会话：无（全部已读）')
    } else {
      const badge = r.totalUnreadBadge !== undefined ? `（左导航总徽章 ${r.totalUnreadBadge}）` : ''
      console.log(`\n未读会话（${r.unread.length} 个）${badge}：`)
      r.unread.forEach((item, i) => {
        const parts = [`  ${i + 1}. ${item.name}`]
        if (item.time !== undefined) parts.push(item.time)
        parts.push(`${item.count} 条未读`)
        console.log(parts.join(' · '))
        if (item.lastPreview !== undefined) console.log(`     最后一条：${item.lastPreview}`)
      })
    }
  }
}

export async function readChatCommand(opts: ReadChatCommandOptions): Promise<number> {
  const target = opts.contact?.trim()
  console.log(
    target
      ? `读取会话消息与未读清单（校验当前会话 = 「${target}」；只读，不借用鼠标）。`
      : '读取当前会话消息与未读清单（只读，不借用鼠标）。',
  )
  return runCliOperation(
    OPERATIONS.boss_read_chat!.operation,
    { contact: target || undefined },
    { cdpPort: opts.cdpPort, onSuccess: (result) => renderResult(result.data) },
  )
}
