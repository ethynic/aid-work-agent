/**
 * CLI 子命令 resume-batch：批量打开推荐牛人并读取简历（薄 renderer，业务在 boss_resume_batch operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js resume-batch [--limit N] [--save-dir <目录>]
 *
 * 逐个点开当前视口牛人卡片 → 滚动分段截图 → 拼接 → Escape 关闭 → 下一份。
 * 文本识别在云端（boss_resume_batch 工具层逐份调多模态模型，2026-09-17 去 OCR 化），
 * CLI 本地不再产出文本。
 * 只读操作，但每份简历的滚动借用真实鼠标（约 30 秒/份），执行期间请勿移动鼠标。
 * 结果 data.resumes 为云端简历库契约 payload 数组（MCP 链路逐份识别入库）；
 * CLI 打印每份姓名与失败列表。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'
import type { OperationResult } from '../../main/operations/types.js'

export interface ResumeBatchCommandOptions {
  /** 读取份数上限（默认 1，最大 10） */
  limit?: number
  /** 拼接长图保存目录（每份存 <姓名>.png，可选） */
  saveDir?: string
  cdpPort?: number
}

interface ResumePayloadView {
  candidate_name?: unknown
  job_name?: unknown
  segments?: unknown
  suspect_seams?: unknown
}

export async function resumeBatchCommand(opts: ResumeBatchCommandOptions): Promise<number> {
  const limit = opts.limit ?? 1
  console.log(
    `批量读取简历：逐个点开当前视口牛人卡片（上限 ${limit} 份），滚动分段截图 → 拼接 → 关闭 → 下一份（文本在云端识别）。` +
      '只读操作，但滚动借用真实鼠标（约 30 秒/份），期间请勿移动鼠标、勿遮挡 Chrome 窗口。',
  )
  const onSuccess = (result: OperationResult): void => {
    const resumes = Array.isArray(result.data.resumes) ? (result.data.resumes as ResumePayloadView[]) : []
    for (const r of resumes) {
      const name = typeof r.candidate_name === 'string' ? r.candidate_name : '?'
      const job = typeof r.job_name === 'string' ? ` · ${r.job_name}` : ''
      const segments = typeof r.segments === 'number' ? r.segments : '?'
      // P1 接缝质量标记（详细序号在 payload 元信息里）：存在可疑接缝时提示人工核对该份
      const seamSuspect = Array.isArray(r.suspect_seams) && (r.suspect_seams as number[]).length > 0
      const seamMark = seamSuspect ? ' ⚠️ 可疑拼接接缝，请人工核对' : ''
      console.log(`\n===== ${name}${job}（${segments} 段拼接，云端识别）${seamMark}=====`)
    }
    const failures = Array.isArray(result.data.failures)
      ? (result.data.failures as Array<{ name?: unknown; error?: unknown }>)
      : []
    if (failures.length > 0) {
      console.log('\n===== 失败列表 =====')
      for (const f of failures) {
        console.log(`· ${typeof f.name === 'string' ? f.name : '未知姓名'}：${String(f.error ?? '?')}`)
      }
    }
    if (opts.saveDir) console.log(`\n拼接长图已保存到目录：${opts.saveDir}`)
  }
  return runCliOperation(
    OPERATIONS.boss_resume_batch!.operation,
    { limit, ...(opts.saveDir ? { save_dir: opts.saveDir } : {}) },
    { cdpPort: opts.cdpPort, onSuccess },
  )
}
