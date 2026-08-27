/**
 * boss_open_chat operation（2026-08-27）：沟通页切换到指定联系人的会话（搜索优先，列表兜底）。
 *
 * read-chat 只能读当前已打开的会话；本 operation 负责打开/切换：已在目标会话零点击返回（already）；
 * 否则优先搜索找人（ChatSearchExecutor 主链路），搜索失败回退点击左侧会话列表项（ChatOpenExecutor）。
 *
 * kind/effect 决策（参照 bossGoto）：切换会话只是页面内 UI 状态变化（显示哪个会话），
 * 不产生任何外部业务写效应（不发消息/不改候选人状态/不改筛选），故 kind='readonly'、
 * effect='none'——与 goto 跳页、read-chat 同级；cli 元数据 write=false。
 * 注意它仍**借用真实鼠标**（搜索点击/列表点击/输入），执行期间勿动鼠标（文案已提示）。
 *
 * 前置校验：URL 必须已在沟通页（/web/chat/index）→ 否则 WRONG_PAGE 提示先 goto chat
 * （与 read-chat 同款「不自动跳转」——切页属打断用户当前关注页面的动作）。
 * contact 必填非空（connect Chrome 前 fail-fast）。
 */
import { ChatOpenExecutor } from '../boss/ChatOpenExecutor.js'
import {
  defaultSessionFactory,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import { CodedOperationError, type BossOperation, type OperationResult, type OpContext } from './types.js'

export interface BossOpenChatArgs {
  /** 要打开的联系人姓名（精确，与会话列表/头部姓名 trim 全等） */
  contact: string
}

const VIA_LABEL: Record<string, string> = {
  already: '已在目标会话（未点击）',
  search: '已通过搜索进入',
  list: '已通过会话列表点击进入',
}

export function createBossOpenChatOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<BossOpenChatArgs> {
  return {
    name: 'boss_open_chat',
    execute(args: BossOpenChatArgs, ctx: OpContext): Promise<OperationResult> {
      const contact = args?.contact ?? ''
      return runBossOperation(
        'readonly',
        ctx,
        sessionFactory,
        () => {
          if (typeof contact !== 'string' || !contact.trim()) {
            return 'contact（联系人姓名）不能为空'
          }
          return null
        },
        async (session) => {
          // 前置校验：必须已在沟通页（与 read-chat 一致不自动跳转；跳转走 goto chat）
          const url = await session.getUrl()
          if (!url.includes('/web/chat/index')) {
            throw new CodedOperationError(
              'WRONG_PAGE',
              '当前不在沟通页（URL 不含 /web/chat/index）：请先执行 goto chat 切换到沟通页后再打开会话（本工具不自动跳转）',
            )
          }
          const executor = new ChatOpenExecutor({
            snapshot: session.snapshot,
            click: session.click,
            clickAndType: session.clickAndType,
            mouseWheel: session.mouseWheel,
            pressEscape: session.pressEscape,
            clearInput: session.clearInput,
            signal: ctx.signal,
          })
          ctx.progress({ stage: 'execute', message: `打开会话「${contact.trim()}」（优先搜索找人，失败回退会话列表）` })
          const r = await executor.open({ contact })
          return {
            message: `完成：${VIA_LABEL[r.via]}「${r.contact}」的会话，可用 read-chat 读取消息`,
            data: { contact: r.contact, via: r.via },
            effect: 'none',
          }
        },
      )
    },
  }
}
