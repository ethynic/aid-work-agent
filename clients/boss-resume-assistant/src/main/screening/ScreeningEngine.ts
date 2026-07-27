/**
 * 筛选引擎（设计文档 §9）。
 * 两阶段：硬规则（确定性）→ LLM（处理未决项）。
 * 三态结论：QUALIFIED / REJECTED / UNCERTAIN。
 * 无证据 / 字段冲突 / LLM 响应失败 / 无法引用简历证据 → 一律 UNCERTAIN。
 *
 * LLM provider 由外部注入（独立客户端，不复用主项目网关）。
 */

export type Conclusion = 'QUALIFIED' | 'REJECTED' | 'UNCERTAIN'

export interface HardRule {
  field: string
  description: string
  /** 判定函数：返回 pass/fail/null(无法判定) */
  judge: (doc: ScreeningInput) => boolean | null
}

export interface Evidence {
  field: string
  rule: string
  result: 'PASS' | 'FAIL' | 'UNDECIDED'
  detail?: string
}

export interface ScreeningInput {
  markdown: string
  fields: Record<string, string | undefined>
  /** 岗位知识版本 + 规则版本（供 LLM 审计） */
  knowledgeVersion?: string
  ruleVersion?: string
}

export interface ScreeningResult {
  conclusion: Conclusion
  reason: string
  evidence: Evidence[]
  model?: string
  promptVersion?: string
  inputHash?: string
  durationMs?: number
}

/** LLM provider 契约：仅分析未决项，输出结论+证据，JSON Schema 校验 */
export interface LlmProvider {
  readonly name: string
  screenUndecided(input: ScreeningInput, undecided: Evidence[]): Promise<{
    conclusion: Conclusion
    reason: string
    evidence: Evidence[]
  }>
}

export class ScreeningEngine {
  constructor(
    private hardRules: HardRule[],
    private llm?: LlmProvider,
  ) {}

  async screen(input: ScreeningInput): Promise<ScreeningResult> {
    const startedAt = Date.now()
    const evidence: Evidence[] = []

    // 阶段一：硬规则
    let anyFail = false
    const undecided: Evidence[] = []
    for (const rule of this.hardRules) {
      let result: boolean | null
      try {
        result = rule.judge(input)
      } catch {
        result = null
      }
      const ev: Evidence = {
        field: rule.field,
        rule: rule.description,
        result: result === null ? 'UNDECIDED' : result ? 'PASS' : 'FAIL',
      }
      evidence.push(ev)
      if (ev.result === 'FAIL') anyFail = true
      if (ev.result === 'UNDECIDED') undecided.push(ev)
    }

    // 任一硬规则 FAIL → REJECTED（确定性）
    if (anyFail) {
      return {
        conclusion: 'REJECTED',
        reason: 'hard rule failed',
        evidence,
        durationMs: Date.now() - startedAt,
      }
    }

    // 无未决项且全 PASS → QUALIFIED
    if (undecided.length === 0) {
      return {
        conclusion: 'QUALIFIED',
        reason: 'all hard rules passed',
        evidence,
        durationMs: Date.now() - startedAt,
      }
    }

    // 阶段二：LLM 处理未决项
    if (!this.llm) {
      // 无 LLM，未决项无法判定 → UNCERTAIN
      return {
        conclusion: 'UNCERTAIN',
        reason: 'undecided hard rules and no LLM provider',
        evidence,
        durationMs: Date.now() - startedAt,
      }
    }

    try {
      const llmResult = await this.llm.screenUndecided(input, undecided)
      // LLM 结论必须能引用证据，否则降级 UNCERTAIN
      if (llmResult.evidence.length === 0 && llmResult.conclusion !== 'UNCERTAIN') {
        return {
          conclusion: 'UNCERTAIN',
          reason: 'LLM conclusion without evidence',
          evidence,
          model: this.llm.name,
          durationMs: Date.now() - startedAt,
        }
      }
      return {
        conclusion: llmResult.conclusion,
        reason: llmResult.reason,
        evidence: [...evidence, ...llmResult.evidence],
        model: this.llm.name,
        durationMs: Date.now() - startedAt,
      }
    } catch (e) {
      // LLM 响应失败 → UNCERTAIN
      return {
        conclusion: 'UNCERTAIN',
        reason: `LLM failed: ${e instanceof Error ? e.message : String(e)}`,
        evidence,
        model: this.llm.name,
        durationMs: Date.now() - startedAt,
      }
    }
  }
}
