/**
 * CLI 子命令 resume-batch：批量打开推荐牛人并读取简历（薄 renderer，业务在 boss_resume_batch operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js resume-batch [--limit N] [--save-dir <目录>]
 *
 * 逐个点开当前视口牛人卡片 → 滚动分段截图 → 拼接 → OCR → Escape 关闭 → 下一份。
 * 只读操作，但每份简历的滚动借用真实鼠标（约 30 秒/份），执行期间请勿移动鼠标。
 * 结果 data.resumes 为云端简历库契约 payload 数组（MCP 链路逐份入库）；
 * CLI 打印每份姓名/字数与失败列表（批量太长，每份只打 OCR 前 200 字预览）。
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
  ocr_text?: unknown
  ocr_chars?: unknown
  job_name?: unknown
}

export async function resumeBatchCommand(opts: ResumeBatchCommandOptions): Promise<number> {
  const limit = opts.limit ?? 1
  console.log(
    `批量读取简历：逐个点开当前视口牛人卡片（上限 ${limit} 份），滚动分段截图 → 拼接 → OCR → 关闭 → 下一份。` +
      '只读操作，但滚动借用真实鼠标（约 30 秒/份），期间请勿移动鼠标、勿遮挡 Chrome 窗口。',
  )
  const onSuccess = (result: OperationResult): void => {
    const resumes = Array.isArray(result.data.resumes) ? (result.data.resumes as ResumePayloadView[]) : []
    for (const r of resumes) {
      const name = typeof r.candidate_name === 'string' ? r.candidate_name : '?'
      const chars = typeof r.ocr_chars === 'number' ? r.ocr_chars : String(r.ocr_text ?? '').length
      const job = typeof r.job_name === 'string' ? ` · ${r.job_name}` : ''
      console.log(`\n===== ${name}${job}（OCR ${chars} 字）=====`)
      const text = typeof r.ocr_text === 'string' ? r.ocr_text : ''
      // 批量不打印全文（太长）：每份只打前 200 字预览
      console.log(text.slice(0, 200) + (text.length > 200 ? '…（后文省略，完整内容见 --save-dir 或简历库）' : ''))
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
