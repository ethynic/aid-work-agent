/**
 * wecom_add_customer operation（M1 写动作）：按手机号检索微信用户并发送添加邀请。
 *
 * 流程（设计文档 §3.5，步骤已按 2026-08-29 真机实测修正）：
 * TS 校验参数（phone 11 位 1 开头 + 显式 confirm: true）→
 * spawn drivers/ps1/add-customer.ps1 全链路（点「通讯录」导航 → 校验内容子窗口
 * 类名 → OCR 校验「新的客户」页头 → 点「添加」 → SearchExternalsWnd 输入手机号 →
 * PostMessage Enter 检索（结果为空时每 2s 重发，最多 5 次）→ OCR 读结果行微信名 →
 * PostMessage 点「添加」（InputReasonWnd 有数秒延迟，等 15s）→ 点「发送」 →
 * 终态轮询校验「已发送申请」（发送后 InputReasonWnd ≤1s 关闭、结果行 ≤3s 才刷新，每 1s 上限 10s）。
 *
 * 企微 5.0.9 丢弃一切 SendInput/keybd_event 注入输入（键盘与鼠标，2026-08-30 真机复核，
 * 覆盖早期"SendInput 鼠标可用"的误判），全链路纯 PostMessage。
 *
 * 写语义：
 * - 号码无检索结果 → CUSTOMER_NOT_FOUND（写动作未发出，effect=none，不重试）；
 * - 名称读不到 / 终态校验失败 → EXECUTION_UNKNOWN（邀请可能已发出，绝不自动重试）；
 * - 驱动超时（写可能已落地）同样归并 EXECUTION_UNKNOWN，而不是 RESULT_TIMEOUT；
 * - 驱动执行中被取消 → CANCELLED/effect=unknown（动作发出后取消且无法确认，不可误报 none）。
 */
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { artifactDir } from '../platform/environment.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { runWecomOperation } from './context.js'
import {
  CancelledError,
  CodedOperationError,
  type OperationResult,
  type OpContext,
  type WecomOperation,
} from './types.js'

/** dist/src/operations → 包根 drivers/ps1 */
const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/add-customer.ps1', import.meta.url))

export interface WecomAddCustomerArgs {
  phone: string
  /** 对外发邀请的写动作，必须显式 true（CLI --yes / MCP schema 必填） */
  confirm: boolean
}

/** 中国大陆手机号：11 位数字，1 开头 */
const PHONE_RE = /^1\d{10}$/

export function createWecomAddCustomerOperation(
  deps: { runDriverFn?: RunPowerShellDriverFn; artifactDirFn?: () => string | null } = {},
): WecomOperation<WecomAddCustomerArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  const artifactDirFn = deps.artifactDirFn ?? (() => artifactDir())
  return {
    name: 'wecom_add_customer',
    execute(args: WecomAddCustomerArgs, ctx: OpContext): Promise<OperationResult> {
      return runWecomOperation(
        'write',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（phone/confirm 必填）'
          }
          if (typeof args.phone !== 'string' || !PHONE_RE.test(args.phone)) {
            return 'phone 必须是 11 位数字手机号（1 开头）'
          }
          if (args.confirm !== true) {
            return '添加客户是对外发送邀请的写动作，必须显式 confirm: true（CLI 用 --yes）确认后才会执行'
          }
          return null
        },
        async (opCtx, tracker) => {
          // 关键步骤截图存档目录：<artifact 根>/add-customer-<run 时间戳>/
          const root = artifactDirFn()
          if (!root) {
            throw new CodedOperationError('INTERNAL_ERROR', '%LOCALAPPDATA% 未设置，无法定位 artifact 目录')
          }
          const dir = join(root, `add-customer-${new Date().toISOString().replace(/[:.]/g, '-')}`)
          mkdirSync(dir, { recursive: true })

          opCtx.progress({ stage: 'execute', message: `执行添加客户链路（手机号 ${args.phone}）` })
          let data: Record<string, unknown>
          try {
            data = await runDriverFn({
              script: DRIVER_PATH,
              args: ['-Phone', args.phone, '-ArtifactDir', dir],
              signal: opCtx.signal,
            })
          } catch (err) {
            // 驱动超时/被取消都无法确认邀请是否已发出：按写动作 unknown 语义上报，绝不自动重试
            if (err instanceof CodedOperationError && err.code === 'RESULT_TIMEOUT') {
              throw new CodedOperationError('EXECUTION_UNKNOWN', '添加客户链路超时：邀请可能已发出但无法确认（不会自动重试，请人工核对）')
            }
            if (err instanceof CancelledError) {
              throw new CodedOperationError(
                'CANCELLED',
                '添加客户链路被取消：邀请可能已发出但无法确认（不会自动重试，请人工核对）',
                'unknown',
              )
            }
            throw err
          }
          const wechatName = typeof data.wechat_name === 'string' && data.wechat_name.length > 0 ? data.wechat_name : null
          if (!wechatName) {
            // 驱动契约：成功必须带回微信名；缺失按写动作 unknown 处理，不可误报成功
            throw new CodedOperationError('EXECUTION_UNKNOWN', '驱动未返回客户微信名（邀请状态不可知，不会自动重试，请人工核对）')
          }
          tracker.completed = 1
          return {
            message: `已向「${wechatName}」（${args.phone}）发出添加邀请，终态校验通过`,
            data: {
              phone: args.phone,
              wechat_name: wechatName,
              screenshot_paths: Array.isArray(data.screenshot_paths) ? data.screenshot_paths : [],
            },
          }
        },
      )
    },
  }
}
