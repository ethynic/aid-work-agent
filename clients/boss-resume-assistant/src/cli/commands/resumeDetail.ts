/**
 * CLI 子命令 resume-detail：读取当前打开的候选人简历详情（薄 renderer，业务在 boss_resume_detail operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js resume-detail [--name <姓名>] [--save-image <path>]
 *
 * 简历详情是 canvas 像素（DOM 抓不到文字），链路：滚动分段截图 → 重叠拼接 → Windows OCR。
 * 只读操作，但滚动借用真实鼠标（1-2 秒），执行期间请勿移动鼠标；前提已点开候选人详情。
 * 结果按云端简历库契约组装（candidate_name/job_name/ocr_text/images base64），供 MCP 链路入库；
 * CLI 本地成功后打印 OCR 全文。--name 候选人姓名（缺省从 OCR 首行自动识别）。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'
import type { OperationResult } from '../../main/operations/types.js'

export interface ResumeDetailCommandOptions {
  /** 候选人姓名（缺省从 OCR 首行自动识别，识别失败报错提示传 --name） */
  name?: string
  /** 拼接长图保存路径（PNG，可选） */
  saveImage?: string
  cdpPort?: number
}

export async function resumeDetailCommand(opts: ResumeDetailCommandOptions): Promise<number> {
  console.log(
    '读取候选人简历详情：滚动分段截图 → 拼接 → OCR 识别。只读操作，但滚动借用真实鼠标（1-2 秒），期间请勿移动鼠标。' +
      '前提：已点开一个候选人的在线简历详情。',
  )
  const onSuccess = (result: OperationResult): void => {
    const text = typeof result.data.ocr_text === 'string' ? result.data.ocr_text : ''
    if (text) {
      console.log('\n===== 简历全文（OCR 识别，可能含少量错字）=====')
      console.log(text)
      console.log('===== 全文结束 =====')
    }
    const segments = typeof result.data.segments === 'number' ? result.data.segments : '?'
    const nameSource = result.data.name_source === 'param' ? '参数传入' : 'OCR 首行识别'
    console.log(`候选人：${String(result.data.candidate_name ?? '?')}（${nameSource}）；拼接 ${segments} 段。`)
    if (opts.saveImage) {
      console.log(`拼接长图已保存到：${opts.saveImage}`)
    } else {
      console.log('（拼接长图未落盘；需要留存可用 --save-image <路径>；图片 base64 已随 data 返回）')
    }
  }
  return runCliOperation(
    OPERATIONS.boss_resume_detail!.operation,
    {
      ...(opts.name ? { candidate_name: opts.name } : {}),
      ...(opts.saveImage ? { save_image_to: opts.saveImage } : {}),
    },
    { cdpPort: opts.cdpPort, onSuccess },
  )
}
