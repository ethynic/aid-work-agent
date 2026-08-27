/**
 * boss_read_chat operation（设计 §10.9）：读取当前沟通会话消息 + 全部未读会话清单（只读）。
 *
 * 已有 send-to/send-current 发消息能力，本 operation 读「对方回了什么」：
 * 当前会话消息流（谁发的+正文+时间+已读）+ 左列表全部未读会话（含视口外）+ 左导航总未读徽章。
 * 单次 DOMSnapshot 纯只读，不借用真实鼠标、无任何写副作用（effect=none）。
 *
 * 前置校验：
 * - URL 必须已在沟通页（/web/chat/index）→ 否则 WRONG_PAGE 提示先 goto chat（**不自动跳转**——
 *   与 send-to 不同：读操作发生在用户当前关注的页面上，静默切页反而打断用户）。
 * - contact 若提供必须是非空字符串（connect Chrome 前 fail-fast）。
 * - contact 给定但当前会话不是该联系人 → executor fail-loud（存在但未打开 / 不存在+可用名单），
 *   本工具不自动切换会话（切换属写操作走既有点击链路）。
 */
import { ChatReadExecutor } from '../boss/ChatReadExecutor.js'
import {
  defaultSessionFactory,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import { CodedOperationError, type BossOperation, type OperationResult, type OpContext } from './types.js'

export interface BossReadChatArgs {
  /** 可选：联系人姓名——校验当前打开的会话是否是该联系人（trim 全等）；不匹配报错并列出可用联系人 */
  contact?: string
}

export function createBossReadChatOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<BossReadChatArgs> {
  return {
    name: 'boss_read_chat',
    execute(args: BossReadChatArgs, ctx: OpContext): Promise<OperationResult> {
      const rawContact = args?.contact
      return runBossOperation(
        'readonly',
        ctx,
        sessionFactory,
        () => {
          if (rawContact !== undefined && (typeof rawContact !== 'string' || !rawContact.trim())) {
            return 'contact（联系人姓名）若提供必须是非空字符串'
          }
          return null
        },
        async (session) => {
          // 前置校验：必须已在沟通页（只读能力不自动跳转，与 send-to 的 ensureChatPage 有意不同）
          const url = await session.getUrl()
          if (!url.includes('/web/chat/index')) {
            throw new CodedOperationError(
              'WRONG_PAGE',
              '当前不在沟通页（URL 不含 /web/chat/index）：请先执行 goto chat 切换到沟通页后再读取会话（本工具不自动跳转）',
            )
          }
          const executor = new ChatReadExecutor({ snapshot: session.snapshot })
          ctx.progress({ stage: 'execute', message: '读取当前会话消息与未读清单' })
          // executor 的 fail-loud 文案已面向用户可操作（切换会话/列可用名单），异常直接透传 errorMapping
          const result = await executor.read({ contact: rawContact })
          const badgeSuffix = result.totalUnreadBadge !== undefined ? `，总未读 ${result.totalUnreadBadge}` : ''
          return {
            message:
              `完成：读取「${result.contact || '(未知联系人)'}」会话消息 ${result.messages.length} 条，未读会话 ${result.unread.length} 个${badgeSuffix}`,
            // data = ChatReadResult 原样（contact/messages/unread/totalUnreadBadge?）
            data: { ...result },
            effect: 'none',
          }
        },
      )
    },
  }
}
