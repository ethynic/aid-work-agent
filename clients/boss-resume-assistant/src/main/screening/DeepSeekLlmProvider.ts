/**
 * DeepSeek LLM Provider（独立客户端，不复用主项目 Python 网关）。
 * 参考主项目 src/llm/providers/deepseek.py 的协议：
 * - OpenAI 兼容：POST {base}/chat/completions（base 不带 /v1）
 * - Bearer token
 * - model: deepseek-v4-flash（与主项目 config.yaml 一致）
 * - 用 JSON mode 保证筛选判断结构化
 *
 * API key 从环境变量 DEEPSEEK_API_KEYS（复数，逗号分隔，与主项目命名一致）读取，
 * 轮询使用。运行于 CLI 主进程（Node fetch）。
 */
import type { LlmProvider, ScreeningInput, Evidence, Conclusion } from './ScreeningEngine.js'

const DEFAULT_BASE_URL = 'https://api.deepseek.com'
const DEFAULT_MODEL = 'deepseek-v4-flash'

export interface DeepSeekProviderOptions {
  /** 覆盖 base URL（默认 https://api.deepseek.com） */
  baseUrl?: string
  /** 覆盖 model（默认 deepseek-v4-flash） */
  model?: string
  /** 直接传 key（优先于环境变量） */
  apiKey?: string
  /** 可注入 fetch（测试） */
  fetchImpl?: typeof fetch
  /** 请求超时 ms */
  timeoutMs?: number
  /** 温度（筛选判断用低温度保证确定性） */
  temperature?: number
}

interface DeepSeekRawResult {
  conclusion: string
  reason: string
  evidence: Array<{ field: string; rule: string; result: string; detail?: string }>
}

export class DeepSeekLlmProvider implements LlmProvider {
  readonly name = 'deepseek'
  private readonly baseUrl: string
  private readonly model: string
  private readonly fetchImpl: typeof fetch
  private readonly timeoutMs: number
  private readonly temperature: number
  private readonly keys: string[]
  private keyIdx = 0

  constructor(opts: DeepSeekProviderOptions = {}) {
    this.baseUrl = (opts.baseUrl ?? process.env.DEEPSEEK_BASE_URL ?? DEFAULT_BASE_URL).replace(/\/+$/, '')
    this.model = opts.model ?? process.env.DEEPSEEK_MODEL_CODE ?? DEFAULT_MODEL
    this.fetchImpl = opts.fetchImpl ?? fetch
    this.timeoutMs = opts.timeoutMs ?? 120000
    this.temperature = opts.temperature ?? 0.1
    const rawKey = opts.apiKey ?? process.env.DEEPSEEK_API_KEYS ?? ''
    this.keys = rawKey.split(',').map((k) => k.trim()).filter(Boolean)
  }

  get isConfigured(): boolean {
    return this.keys.length > 0
  }

  async screenUndecided(input: ScreeningInput, undecided: Evidence[]): Promise<{
    conclusion: Conclusion
    reason: string
    evidence: Evidence[]
  }> {
    if (!this.isConfigured) {
      throw new Error('DeepSeek provider not configured: set DEEPSEEK_API_KEYS')
    }

    const prompt = buildPrompt(input, undecided)
    const body = {
      model: this.model,
      messages: [
        { role: 'system', content: '你是简历筛选助手。只根据候选人简历内容判断未决条件，输出严格 JSON。无法引用简历证据时 conclusion 必须为 UNCERTAIN。' },
        { role: 'user', content: prompt },
      ],
      temperature: this.temperature,
      max_tokens: 2048,
      response_format: { type: 'json_object' },
    }

    const raw = await this.callChat(body)
    const parsed = this.parseResult(raw)
    return parsed
  }

  /** 调 chat/completions，带 key 轮询和超时 */
  private async callChat(body: Record<string, unknown>): Promise<string> {
    let lastErr: Error | null = null
    for (let attempt = 0; attempt < this.keys.length; attempt++) {
      const key = this.keys[this.keyIdx % this.keys.length]!
      this.keyIdx++
      try {
        const controller = new AbortController()
        const timer = setTimeout(() => controller.abort(), this.timeoutMs)
        const res = await this.fetchImpl(`${this.baseUrl}/chat/completions`, {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${key}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(body),
          signal: controller.signal,
        })
        clearTimeout(timer)
        if (!res.ok) {
          const text = await res.text().catch(() => '')
          throw new Error(`DeepSeek HTTP ${res.status}: ${text.slice(0, 200)}`)
        }
        const data = (await res.json()) as {
          choices?: Array<{ message?: { content?: string } }>
        }
        const content = data.choices?.[0]?.message?.content
        if (!content) throw new Error('DeepSeek response missing content')
        return content
      } catch (e) {
        lastErr = e instanceof Error ? e : new Error(String(e))
        // 继续下一个 key
      }
    }
    throw lastErr ?? new Error('DeepSeek call failed')
  }

  /** 解析 LLM 返回的 JSON，校验 conclusion 合法性，非法降级 UNCERTAIN */
  private parseResult(raw: string): { conclusion: Conclusion; reason: string; evidence: Evidence[] } {
    let obj: DeepSeekRawResult
    try {
      obj = JSON.parse(raw)
    } catch {
      // JSON 解析失败 → 无法采信，抛错由 ScreeningEngine 降级 UNCERTAIN
      throw new Error(`DeepSeek returned non-JSON: ${raw.slice(0, 200)}`)
    }
    const conclusion = normalizeConclusion(obj.conclusion)
    const evidence: Evidence[] = Array.isArray(obj.evidence)
      ? obj.evidence.map((e) => ({
          field: String(e.field ?? ''),
          rule: String(e.rule ?? ''),
          result: normalizeEvidenceResult(e.result),
          ...(e.detail ? { detail: String(e.detail) } : {}),
        }))
      : []
    return {
      conclusion,
      reason: String(obj.reason ?? ''),
      evidence,
    }
  }
}

function normalizeConclusion(v: unknown): Conclusion {
  const s = String(v ?? '').toUpperCase().trim()
  if (s === 'QUALIFIED' || s === 'REJECTED' || s === 'UNCERTAIN') return s
  return 'UNCERTAIN'
}

function normalizeEvidenceResult(v: unknown): Evidence['result'] {
  const s = String(v ?? '').toUpperCase().trim()
  if (s === 'PASS' || s === 'FAIL' || s === 'UNDECIDED') return s
  return 'UNDECIDED'
}

function buildPrompt(input: ScreeningInput, undecided: Evidence[]): string {
  const undecidedDesc = undecided
    .map((e) => `- 字段「${e.field}」规则「${e.rule}」无法用硬规则判定`)
    .join('\n')
  return [
    '岗位知识版本：' + (input.knowledgeVersion ?? 'unknown'),
    '规则版本：' + (input.ruleVersion ?? 'unknown'),
    '',
    '需要你判定的未决条件：',
    undecidedDesc,
    '',
    '候选人简历（Markdown）：',
    '```',
    input.markdown,
    '```',
    '',
    '请输出 JSON，格式：',
    '{"conclusion":"QUALIFIED|REJECTED|UNCERTAIN","reason":"简短理由","evidence":[{"field":"字段","rule":"规则","result":"PASS|FAIL|UNDECIDED","detail":"引用简历原文"}]}',
    '规则：每条结论必须能从简历引用证据；引用不到的判定为 UNDECIDED；任一未决项无法判定则整体 conclusion=UNCERTAIN。',
  ].join('\n')
}
