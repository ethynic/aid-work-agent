/**
 * 交互式对话编排器（CLI chat 子命令）。
 *
 * 用户在一个 REPL 里连续用自然语言提要求（「筛选简历：本科以上，5年经验，15-20K」、
 * 「再打一个」「看看谁给我发简历了」），LLM（DeepSeek）把每句话分类成动作，
 * 编排器调用对应能力执行并把结果用中文回答出来。
 *
 * fail-loud：LLM 返回无法识别的动作按 unknown 处理（回答「没听懂」），绝不乱猜执行写动作。
 */
import type { FilterSpec, PanelRowInfo } from './FilterSetter.js'

export class ChatError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ChatError'
  }
}

export type ChatAction =
  | { type: 'filter_and_greet'; filterRequest: string; limit: number }
  | { type: 'greet'; limit: number }
  | { type: 'goto'; target: 'recommend' | 'chat' }
  | { type: 'accept' }
  | { type: 'clear_filter' }
  | { type: 'help' }
  | { type: 'unknown' }

const DEFAULT_BASE_URL = 'https://api.deepseek.com'
const DEFAULT_MODEL = 'deepseek-v4-flash'
const MAX_LIMIT = 100

export interface ClassifyOptions {
  baseUrl?: string
  model?: string
  apiKey?: string
  fetchImpl?: typeof fetch
  timeoutMs?: number
}

/** 一句话能力清单（help 动作与 REPL 启动横幅共用） */
export const CAPABILITY_HINT =
  '我能做：筛选简历（如「本科以上，5年经验，月薪15-20K，筛选简历」）、打招呼（「给最近1个人打招呼」「再打一个」）、' +
  '跳页面（「去推荐牛人」「去沟通页」）、接收附件简历（「看看谁给我发简历了，同意接收」）、清除筛选（「清除筛选」）。输入「退出」结束。'

/**
 * 把用户一句话分类成动作。history 是最近几轮用户输入（旧的在前），
 * 用于理解「再打一个」这类依赖上文的省略句。
 */
export async function classifyIntent(
  text: string,
  history: string[],
  opts: ClassifyOptions = {},
): Promise<ChatAction> {
  const keys = (opts.apiKey ?? process.env.DEEPSEEK_API_KEYS ?? '')
    .split(',')
    .map((k) => k.trim())
    .filter(Boolean)
  if (keys.length === 0) {
    throw new ChatError('未配置 DEEPSEEK_API_KEYS，对话模式不可用（可在仓库根目录 .env 配置）')
  }
  const baseUrl = (opts.baseUrl ?? process.env.DEEPSEEK_BASE_URL ?? DEFAULT_BASE_URL).replace(/\/+$/, '')
  const model = opts.model ?? process.env.DEEPSEEK_MODEL_CODE ?? DEFAULT_MODEL
  const fetchImpl = opts.fetchImpl ?? fetch
  const timeoutMs = opts.timeoutMs ?? 60000

  const historyDesc = history.length > 0 ? `最近对话（旧的在前）：\n${history.map((h) => `- ${h}`).join('\n')}\n\n` : ''
  const body = {
    model,
    messages: [
      {
        role: 'system',
        content:
          '你是 BOSS 直聘招聘助手的意图分类器。把用户的一句话分类成一个动作，只输出 JSON。' +
          '用户是招聘方，正在筛选简历、给候选人打招呼、接收候选人发来的附件简历。',
      },
      {
        role: 'user',
        content:
          `${historyDesc}用户当前说：「${text}」\n\n` +
          '动作枚举：\n' +
          '- filter_and_greet：描述筛选条件并要求筛选/打招呼（如「本科以上，5年经验，15-20K，筛选简历」），filter_request 填筛选条件原文\n' +
          '- greet：只要求打招呼（如「再打一个」「给最近3个人打招呼」），limit 填人数（没说就 1）\n' +
          '- goto：要求切换页面，target 填 recommend（推荐牛人）或 chat（沟通/消息页）\n' +
          '- accept：要求查看/同意接收候选人发来的附件简历\n' +
          '- clear_filter：要求清除/重置筛选条件\n' +
          '- help：问你能做什么\n' +
          '- unknown：与招聘操作无关或无法理解\n' +
          '输出格式：{"action":"...","filter_request":string|null,"limit":number|null,"target":"recommend"|"chat"|null}，' +
          '用不到的字段填 null。',
      },
    ],
    temperature: 0.1,
    max_tokens: 512,
    response_format: { type: 'json_object' },
  }

  let lastErr: Error | null = null
  for (const key of keys) {
    try {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), timeoutMs)
      const res = await fetchImpl(`${baseUrl}/chat/completions`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      })
      clearTimeout(timer)
      if (!res.ok) throw new Error(`DeepSeek HTTP ${res.status}: ${(await res.text().catch(() => '')).slice(0, 200)}`)
      const data = (await res.json()) as { choices?: Array<{ message?: { content?: string } }> }
      const content = data.choices?.[0]?.message?.content
      if (!content) throw new Error('DeepSeek response missing content')
      return validateAction(content, text)
    } catch (e) {
      if (e instanceof ChatError) throw e
      lastErr = e instanceof Error ? e : new Error(String(e))
    }
  }
  throw lastErr ?? new ChatError('DeepSeek 调用失败')
}

/** 解析并校验 LLM 输出；无法识别一律按 unknown（宁可不动作，不乱执行写动作） */
function validateAction(raw: string, originalText: string): ChatAction {
  let parsed: { action?: unknown; filter_request?: unknown; limit?: unknown; target?: unknown }
  try {
    parsed = JSON.parse(raw) as typeof parsed
  } catch {
    return { type: 'unknown' }
  }
  const limit = (): number => {
    const n = typeof parsed.limit === 'number' ? Math.floor(parsed.limit) : 1
    return Math.min(MAX_LIMIT, Math.max(1, n))
  }
  switch (parsed.action) {
    case 'filter_and_greet': {
      const req = typeof parsed.filter_request === 'string' && parsed.filter_request.trim()
        ? parsed.filter_request.trim()
        : originalText
      return { type: 'filter_and_greet', filterRequest: req, limit: limit() }
    }
    case 'greet':
      return { type: 'greet', limit: limit() }
    case 'goto':
      return parsed.target === 'recommend' || parsed.target === 'chat'
        ? { type: 'goto', target: parsed.target }
        : { type: 'unknown' }
    case 'accept':
      return { type: 'accept' }
    case 'clear_filter':
      return { type: 'clear_filter' }
    case 'help':
      return { type: 'help' }
    default:
      return { type: 'unknown' }
  }
}

/** 编排器依赖的能力（CLI 层用真实 CDP/Win32 实现，测试用 mock） */
export interface ChatHandlers {
  /** 跳转页面（已在目标页自动跳过）；返回实际落点说明 */
  gotoPage(target: 'recommend' | 'chat'): Promise<void>
  /** 打开筛选面板并 probe 合法选项 */
  probePanel(): Promise<PanelRowInfo[]>
  /** LLM 翻译筛选要求（probe 词表约束，fail-loud） */
  translateFilter(request: string, panel: PanelRowInfo[]): Promise<FilterSpec>
  /** 应用筛选，返回徽章计数 */
  applyFilter(spec: FilterSpec): Promise<{ filterCount: number }>
  /** 给可见牛人打招呼 */
  greet(limit: number): Promise<{ greeted: number; reachedEnd: boolean }>
  /** 同意接收附件简历并预览 */
  acceptResumes(): Promise<{ accepted: number; previewed: number }>
  /** 清除全部筛选 */
  clearFilter(): Promise<void>
}

export interface ChatOrchestratorDeps {
  handlers: ChatHandlers
  /** 注入意图分类（默认走 DeepSeek；测试用 mock） */
  classify?: (text: string, history: string[]) => Promise<ChatAction>
}

export class ChatOrchestrator {
  private readonly deps: ChatOrchestratorDeps
  private readonly history: string[] = []

  constructor(deps: ChatOrchestratorDeps) {
    this.deps = deps
  }

  /** 处理一句用户输入，返回要打印给用户的中文回答行 */
  async handle(text: string): Promise<string[]> {
    const classify = this.deps.classify ?? ((t: string, h: string[]) => classifyIntent(t, h))
    const action = await classify(text, [...this.history])
    const lines = await this.execute(action)
    this.history.push(text)
    if (this.history.length > 10) this.history.shift()
    return lines
  }

  private async execute(action: ChatAction): Promise<string[]> {
    const h = this.deps.handlers
    switch (action.type) {
      case 'filter_and_greet': {
        await h.gotoPage('recommend')
        const panel = await h.probePanel()
        const spec = await h.translateFilter(action.filterRequest, panel)
        const { filterCount } = await h.applyFilter(spec)
        const result = await h.greet(action.limit)
        return [
          `筛选已生效（徽章 筛选·${filterCount}）：经验=${spec.experience ?? '不限'} 学历=${spec.educations?.join('/') ?? '不限'} 薪资=${spec.salary ?? '不限'}。`,
          `成功打招呼 ${result.greeted} 人${result.reachedEnd ? '（列表已到底）' : ''}。`,
        ]
      }
      case 'greet': {
        await h.gotoPage('recommend')
        const result = await h.greet(action.limit)
        return [`成功打招呼 ${result.greeted} 人${result.reachedEnd ? '（列表已到底）' : ''}。`]
      }
      case 'goto':
        await h.gotoPage(action.target)
        return [action.target === 'recommend' ? '已切换到「推荐牛人」页。' : '已切换到「沟通」页。']
      case 'accept': {
        await h.gotoPage('chat')
        const result = await h.acceptResumes()
        return [
          result.accepted > 0
            ? `同意接收了 ${result.accepted} 人的附件简历，已逐份打开预览。`
            : '当前没有等待你同意接收的附件简历。',
        ]
      }
      case 'clear_filter':
        await h.gotoPage('recommend')
        await h.clearFilter()
        return ['筛选条件已全部清除。']
      case 'help':
        return [CAPABILITY_HINT]
      default:
        return [`没听懂这句。${CAPABILITY_HINT}`]
    }
  }
}
