/**
 * OPERATIONS 注册表：tool 名 → operation + CLI renderer 元数据（实施规格 m02 §1）。
 *
 * CLI command 与 MCP tool handler 都从这里取 operation，保证业务能力只实现一次。
 * cli 元数据供薄 renderer 使用：write=true 的命令在执行前打印 ⚠️ 写动作提示。
 */
import { createBossFilterOperation, createBossClearFilterOperation } from './bossFilter.js'
import { createBossGotoOperation } from './bossGoto.js'
import { createBossGreetOperation } from './bossGreet.js'
import { createBossAcceptResumeOperation } from './bossAcceptResume.js'
import { createBossRejectCurrentOperation } from './bossRejectCurrent.js'
import { createBossInterviewDemoOperation } from './bossInterviewDemo.js'
import { createBossSendToOperation } from './bossSendTo.js'
import { createBossSendCurrentOperation } from './bossSendCurrent.js'
import { createBossListJobsOperation } from './bossListJobs.js'
import { createBossSelectJobOperation } from './bossSelectJob.js'
import { createBossReadResumeOperation } from './bossReadResume.js'
import type { BossOperation } from './types.js'

export interface OperationEntry {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  operation: BossOperation<any>
  /** CLI renderer 元数据 */
  cli: {
    /** 是否有外部写副作用（CLI 执行前打印 ⚠️ 提示） */
    write: boolean
  }
}

export const OPERATIONS: Record<string, OperationEntry> = {
  boss_filter: { operation: createBossFilterOperation(), cli: { write: true } },
  boss_clear_filter: { operation: createBossClearFilterOperation(), cli: { write: true } },
  boss_goto: { operation: createBossGotoOperation(), cli: { write: false } },
  boss_greet: { operation: createBossGreetOperation(), cli: { write: true } },
  boss_accept_resume: { operation: createBossAcceptResumeOperation(), cli: { write: true } },
  boss_reject_current: { operation: createBossRejectCurrentOperation(), cli: { write: true } },
  boss_interview_demo: { operation: createBossInterviewDemoOperation(), cli: { write: false } },
  boss_send_to: { operation: createBossSendToOperation(), cli: { write: true } },
  boss_send_current: { operation: createBossSendCurrentOperation(), cli: { write: true } },
  boss_list_jobs: { operation: createBossListJobsOperation(), cli: { write: false } },
  boss_select_job: { operation: createBossSelectJobOperation(), cli: { write: true } },
  boss_read_resume: { operation: createBossReadResumeOperation(), cli: { write: false } },
}

export const OPERATION_NAMES = Object.keys(OPERATIONS)
