/**
 * CLI 子命令 interview：约面试表单填充演示（薄 renderer，业务能力在 boss_interview_demo operation）。
 *
 * 用法：
 *   node dist/src/cli/index.js interview [--remark "备注内容"]
 *
 * ⚠️ 演示用途：绝不点击「发送」。Win32 真实鼠标：期间请勿移动鼠标，勿遮挡 BOSS 窗口。
 */
import { OPERATIONS } from '../../main/operations/index.js'
import { runCliOperation } from '../render.js'

export interface InterviewCommandOptions {
  remark?: string
  cdpPort?: number
}

export async function interviewCommand(opts: InterviewCommandOptions): Promise<number> {
  console.log('约面试演示：打开当前会话的面试邀请表单 → 逐字填备注 → 选明天日期 → 取消关闭。')
  console.log('⚠️ 仅演示填充，绝不点击「发送」；借用真实鼠标：请勿移动鼠标，勿遮挡 BOSS 窗口。')
  return runCliOperation(
    OPERATIONS.boss_interview_demo!.operation,
    { remark: opts.remark },
    { cdpPort: opts.cdpPort },
  )
}
