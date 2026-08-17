/**
 * CLI 子命令 read-resume：读取当前打开的候选人在线简历全文（薄 renderer，业务在 boss_read_resume operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js read-resume [--save-image <path>]
 *
 * 简历详情是 canvas 像素（DOM 抓不到文字），链路：滚动分段截图 → 重叠拼接 → Windows OCR。
 * 只读操作，但滚动借用真实鼠标（1-2 秒），执行期间请勿移动鼠标；前提已在推荐牛人页点开候选人详情。
 * 成功后打印 OCR 全文（用户要看全文）；--save-image 可保存拼接长图（缺省不保留图片）。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'
import type { OperationResult } from '../../main/operations/types.js'

export interface ReadResumeCommandOptions {
  saveImage?: string
  cdpPort?: number
}

export async function readResumeCommand(opts: ReadResumeCommandOptions): Promise<number> {
  console.log(
    '读取在线简历：滚动分段截图 → 拼接 → OCR 识别。只读操作，但滚动借用真实鼠标（1-2 秒），期间请勿移动鼠标。' +
      '前提：已在推荐牛人页点开一个候选人的在线简历详情。',
  )
  const onSuccess = (result: OperationResult): void => {
    const text = typeof result.data.text === 'string' ? result.data.text : ''
    if (text) {
      console.log('\n===== 简历全文（OCR 识别，可能含少量错字）=====')
      console.log(text)
      console.log('===== 全文结束 =====')
    }
    if (opts.saveImage) {
      console.log(`拼接长图已保存到：${opts.saveImage}`)
    } else {
      console.log('（拼接长图未保存；需要留存可用 --save-image <路径>）')
    }
  }
  return runCliOperation(
    OPERATIONS.boss_read_resume!.operation,
    opts.saveImage ? { save_image_to: opts.saveImage } : {},
    { cdpPort: opts.cdpPort, onSuccess },
  )
}
