/**
 * CLI 子命令 resume-detail：读取当前打开的候选人简历详情（薄 renderer，业务在 boss_resume_detail operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js resume-detail --name <姓名> [--save-image <path>]
 *
 * 简历详情是 canvas 像素（DOM 抓不到文字），链路：滚动分段截图 → 重叠拼接。
 * 文本识别在云端（boss_resume_detail 工具层调多模态模型，2026-09-17 去 OCR 化），
 * CLI 本地不再产出文本。
 * 只读操作，但滚动借用真实鼠标（1-2 秒），执行期间请勿移动鼠标；前提已点开候选人详情。
 * 结果按云端简历库契约组装（candidate_name/name_source/images base64）供入库；
 * --name 候选人姓名（必传，缺省报错；云端识别后会与识别文本交叉校验，不符报错）。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'
import type { OperationResult } from '../../main/operations/types.js'

export interface ResumeDetailCommandOptions {
  /** 候选人姓名（必传：缺省 INVALID_ARGUMENT 报错） */
  name?: string
  /** 拼接长图保存路径（PNG，可选） */
  saveImage?: string
  cdpPort?: number
}

export async function resumeDetailCommand(opts: ResumeDetailCommandOptions): Promise<number> {
  console.log(
    '读取候选人简历详情：滚动分段截图 → 拼接（文本在云端识别）。只读操作，但滚动借用真实鼠标（1-2 秒），期间请勿移动鼠标。' +
      '前提：已点开一个候选人的在线简历详情。',
  )
  const onSuccess = (result: OperationResult): void => {
    const segments = typeof result.data.segments === 'number' ? result.data.segments : '?'
    // name_source 现在恒为 'param'（姓名唯一来源=显式入参）；对意外值宽容显示原值，不炸渲染
    const nameSource =
      result.data.name_source === 'param' ? '参数传入' : String(result.data.name_source ?? '?')
    console.log(
      `候选人：${String(result.data.candidate_name ?? '?')}（姓名来源：${nameSource}）；拼接 ${segments} 段；文本将在云端识别后入库。`,
    )
    // P1 接缝质量元信息（operation 消息已含警告，这里给紧凑的定位行）
    const suspectSeams = Array.isArray(result.data.suspect_seams) ? (result.data.suspect_seams as number[]) : []
    if (suspectSeams.length > 0) {
      console.log(
        `⚠️ 可疑拼接接缝（错位：${suspectSeams.join('、')}）：拼接图内容可能有重复/缺失，请人工核对拼接图。`,
      )
    }
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
