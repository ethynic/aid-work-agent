/**
 * 自然语言筛选翻译器（CLI ask 子命令）。
 *
 * 用户用自然语言描述筛选要求（如「本科以上学历，5年经验，15-20K」），
 * LLM（DeepSeek，协议与 DeepSeekLlmProvider 一致）将其翻译成筛选面板上的合法选项。
 * 合法词表来自 FilterSetter.describePanel() 的真机 probe，LLM 只能选词表内的值。
 *
 * fail-loud：LLM 返回词表外选项时直接报错并列出合法值，绝不静默应用错误筛选。
 */
import type { FilterSpec, PanelRowInfo } from './FilterSetter.js'

export class NlFilterError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'NlFilterError'
  }
}

const DEFAULT_BASE_URL = 'https://api.deepseek.com'
const DEFAULT_MODEL = 'deepseek-v4-flash'

export interface NlTranslateOptions {
  baseUrl?: string
  model?: string
  apiKey?: string
  fetchImpl?: typeof fetch
  timeoutMs?: number
}

interface RawSpec {
  experience?: string | null
  educations?: string[] | null
  salary?: string | null
}

/** 把自然语言筛选要求翻译成 FilterSpec（选项必须是 probe 词表内的原文） */
export async function translateFilterRequest(
  request: string,
  panel: PanelRowInfo[],
  opts: NlTranslateOptions = {},
): Promise<FilterSpec> {
  const keys = (opts.apiKey ?? process.env.DEEPSEEK_API_KEYS ?? '')
    .split(',')
    .map((k) => k.trim())
    .filter(Boolean)
  if (keys.length === 0) {
    throw new NlFilterError('未配置 DEEPSEEK_API_KEYS，自然语言筛选不可用（可在仓库根目录 .env 配置）')
  }
  const baseUrl = (opts.baseUrl ?? process.env.DEEPSEEK_BASE_URL ?? DEFAULT_BASE_URL).replace(/\/+$/, '')
  const model = opts.model ?? process.env.DEEPSEEK_MODEL_CODE ?? DEFAULT_MODEL
  const fetchImpl = opts.fetchImpl ?? fetch
  const timeoutMs = opts.timeoutMs ?? 60000

  const rowDesc = panel
    .map((row) => {
      const multi = row.label === '学历要求' ? '可多选' : '单选'
      return `- ${row.label}（${multi}）：${row.options.map((o) => o.text).join('、')}`
    })
    .join('\n')

  const body = {
    model,
    messages: [
      {
        role: 'system',
        content:
          '你是 BOSS 直聘筛选条件翻译器。把用户的自然语言要求翻译成筛选面板上的合法选项，只输出 JSON。',
      },
      {
        role: 'user',
        content:
          `筛选面板合法选项：\n${rowDesc}\n\n` +
          `用户要求：「${request}」\n\n` +
          '规则：\n' +
          '1. 只能使用上面列出的合法选项原文，禁止编造\n' +
          '2. 学历「本科以上/及以上」等语义，展开为多选行中该档及更高的全部选项\n' +
          '3. 薪酬是区间选项，用户区间落在哪个选项区间内就选哪个（如 15000-20000 → 10-20K）\n' +
          '4. 用户未提及的行输出 null；多选行输出数组，单选行输出字符串\n' +
          '5. 输出格式：{"experience": string|null, "educations": string[]|null, "salary": string|null}',
      },
    ],
    temperature: 0.1,
    max_tokens: 1024,
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
      return validateSpec(parseJson(content), panel)
    } catch (e) {
      if (e instanceof NlFilterError) throw e // 词表校验失败不换 key 重试
      lastErr = e instanceof Error ? e : new Error(String(e))
    }
  }
  throw lastErr ?? new NlFilterError('DeepSeek 调用失败')
}

function parseJson(raw: string): RawSpec {
  try {
    return JSON.parse(raw) as RawSpec
  } catch {
    throw new NlFilterError(`LLM 返回的不是合法 JSON：${raw.slice(0, 200)}`)
  }
}

/** 校验 LLM 输出必须是 probe 词表内的选项原文，非法即报错并列出合法值 */
function validateSpec(raw: RawSpec, panel: PanelRowInfo[]): FilterSpec {
  // 行标签前缀匹配：真机薪资待遇行标签带后缀「薪资待遇[单选]」（2026-08-06 probe 实测）
  const rowOptions = (label: string): string[] =>
    panel.find((r) => r.label === label || r.label.startsWith(label))?.options.map((o) => o.text) ?? []
  const check = (label: string, value: string): string => {
    const legal = rowOptions(label)
    if (!legal.includes(value)) {
      throw new NlFilterError(`LLM 返回了「${label}」行的非法选项「${value}」，合法值：${legal.join('、')}`)
    }
    return value
  }
  const spec: FilterSpec = {}
  if (raw.experience != null) spec.experience = check('经验要求', raw.experience)
  if (raw.educations != null && raw.educations.length > 0) {
    spec.educations = raw.educations.map((e) => check('学历要求', e))
  }
  if (raw.salary != null) spec.salary = check('薪资待遇', raw.salary)
  if (!spec.experience && !spec.educations && !spec.salary) {
    throw new NlFilterError('LLM 未能从要求中翻译出任何筛选条件，请把要求说得更具体（如学历/经验/薪酬）')
  }
  return spec
}
