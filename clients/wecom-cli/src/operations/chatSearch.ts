/** Read-only name resolution before any WeCom UI search. */
import { runWecomOperation } from './context.js'
import { CodedOperationError, type OpContext, type WecomOperation } from './types.js'
import { createTargetRef, type CreateTargetRefFn } from '../platform/targetRef.js'
import { readSessionDirectory, type DirectoryArgs } from './sessionDirectory.js'
import type { OperationOutcome } from './context.js'

export interface WecomChatSearchArgs {
  query: string
  type?: 'contact' | 'group' | 'any'
  limit?: number
}

interface Candidate { name: string; section: '联系人' | '群聊' }

export function createWecomChatSearchOperation(deps: {
  createRefFn?: CreateTargetRefFn
  readCatalogFn?: (args: DirectoryArgs, ctx: OpContext) => Promise<OperationOutcome>
} = {}): WecomOperation<WecomChatSearchArgs> {
  const createRef = deps.createRefFn ?? ((name, type, subtitle, coords, account) =>
    createTargetRef(name, type, subtitle, { coords, account }))
  const readCatalog = deps.readCatalogFn ?? readSessionDirectory
  return {
    name: 'wecom_chat_search',
    execute: (args, ctx) => runWecomOperation('readonly', ctx, () => {
      if (!args || typeof args !== 'object' || Array.isArray(args) ||
          typeof args.query !== 'string' || !args.query.trim() || args.query.length > 100 ||
          args.type !== undefined && !['contact', 'group', 'any'].includes(args.type) ||
          args.limit !== undefined && (!Number.isInteger(args.limit) || args.limit < 1 || args.limit > 20))
        return 'query 必填且最多100字；type 为 contact/group/any；limit 为1..20'
      return null
    }, async opCtx => {
      const query = args.query.trim()
      const catalog = await readCatalog({ mode: 'search', query, type: args.type ?? 'any', limit: args.limit ?? 20 }, opCtx)
      const raw = catalog.data ?? {}
      const candidates = Array.isArray(raw.candidates) ? raw.candidates as Candidate[] : []
      const count = typeof raw.match_count === 'number' ? raw.match_count : 0
      const common = { query, match_kind: raw.match_kind, match_count: count,
        candidates: candidates.map(row => ({ name: row.name, section: row.section })),
        source_generation: raw.source_generation }
      if (count === 0) {
        throw new CodedOperationError('TARGET_NOT_FOUND',
          `本地名称中未找到「${query}」；请根据候选名称确认是否有错字，无法确认时请用户提供正确名称`,
          'none', common)
      }
      if (count > 1) {
        const truncated = count > candidates.length ? `；共 ${count} 项，仅展示前 ${candidates.length} 项，请缩小关键词` : ''
        throw new CodedOperationError('TARGET_AMBIGUOUS', (raw.same_name === true
          ? `「${query}」命中多个同名目标，无法区分；请先备注或重命名`
          : `「${query}」命中多个不同名称；请用户指定完整名称`) + truncated,
        'none', { ...common, same_name: raw.same_name === true })
      }
      const candidate = candidates[0]
      if (!candidate || typeof candidate.name !== 'string' ||
          !['联系人', '群聊'].includes(candidate.section) || typeof raw.self_user_id !== 'string')
        throw new CodedOperationError('LOCALDB_UNAVAILABLE', '本地名称查询结果不完整')
      // Ctrl+F 的默认结果不区分联系人/群聊；签发前须核对完整名称跨类型仍唯一。
      if ((args.type ?? 'any') !== 'any') {
        const global = (await readCatalog({ mode: 'search', query: candidate.name, type: 'any', limit: 20 }, opCtx)).data ?? {}
        const globalCandidates = Array.isArray(global.candidates) ? global.candidates as Candidate[] : []
        if (global.self_user_id !== raw.self_user_id)
          throw new CodedOperationError('IDENTITY_UNCONFIRMED', '名称复查期间当前账号变化')
        if (global.match_count !== 1 || globalCandidates[0]?.name !== candidate.name) {
          throw new CodedOperationError('TARGET_AMBIGUOUS', global.same_name === true
            ? `「${candidate.name}」存在同名联系人或群聊，默认搜索结果无法区分；请先备注或重命名`
            : `「${candidate.name}」跨联系人与群聊有多个匹配名称；请用户指定完整名称`,
            'none', { query, match_count: global.match_count, same_name: global.same_name === true,
              candidates: globalCandidates.map(row => ({ name: row.name, section: row.section })) })
        }
      }
      const target_ref = createRef(candidate.name, candidate.section === '群聊' ? 'group' : 'contact',
        '', undefined, raw.self_user_id)
      const item = { name: candidate.name, section: candidate.section, subtitle: '', target_ref }
      return { message: `唯一匹配「${candidate.name}」；可调用 wecom_chat_select 进入会话`,
        data: { ...common, search_successful: true, best: item, items: [item], target_ref_account_bound: true } }
    }),
  }
}
