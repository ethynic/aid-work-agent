/**
 * 既有 executor Error → 统一错误码（实施规格 m02 §3）。
 *
 * 消息匹配表集中在本文件，不散落到各 operation。匹配顺序即判定优先级：
 * 取消 > Chrome 连接 > 登录态 > 付费墙 > 参数类 > 写后校验失败 > 已知 executor Error > 兜底。
 */
import { FilterSetError } from '../boss/FilterSetter.js'
import { GreetError } from '../boss/GreetExecutor.js'
import { ConsentError } from '../boss/ResumeConsentExecutor.js'
import { NavError } from '../boss/PageNavigator.js'
import { ChatRejectError } from '../boss/ChatRejectExecutor.js'
import { InterviewDemoError } from '../boss/InterviewDemoExecutor.js'
import { ChatSendError } from '../boss/ChatSendExecutor.js'
import { ChatSearchError } from '../boss/ChatSearchExecutor.js'
import { ChatReadError } from '../boss/ChatReadExecutor.js'
import { ChatOpenError } from '../boss/ChatOpenExecutor.js'
import { OverlayDismissError } from '../boss/OverlayDismissExecutor.js'
import { JobSwitchError } from '../boss/JobSwitcher.js'
import { ResumeReadError } from '../boss/ResumeReader.js'
import { ResumeBatchError } from '../boss/ResumeBatchReader.js'
import { WinClickError } from '../input/WinMouseClicker.js'
import { CancelledError, CodedOperationError, type ErrorCode } from './types.js'

export interface MappedError {
  code: ErrorCode
  message: string
}

/** 写后校验失败标记：点击已发出但结果校验不过（规格 §3「校验失败/未减少/未生效/未关闭/未打开」等） */
const POST_WRITE_VERIFY_MARKERS = [
  '校验失败',
  '未减少',
  '未生效',
  '未关闭',
  '未打开',
  '未消失',
  '未出现',
  '未显示',
  '仍存在',
  '无法确认',
  '未跳转',
]

/**
 * Chrome 连接失败标记：fetch ECONNREFUSED（cause.code）/ fetch failed / CDP 超时 /
 * CDP 断连（2026-09-01 真机事故：用户在长任务执行中关掉调试 Chrome，报错被兜底成
 * INTERNAL_ERROR「未预期错误」误导排障方向——断连属环境问题，可重试）
 */
const CHROME_UNAVAILABLE_MARKERS = [
  'ECONNREFUSED',
  'fetch failed',
  'timed out',
  'timeout',
  'socket is not connected',
  'WebSocket disconnected',
  'socket closed',
  'session mismatch',
]

/** 未登录/未打开 BOSS 页面标记 */
const NOT_LOGGED_IN_MARKERS = ['请确认已登录', 'no BOSS page target found']

/** 参数类 FilterSetError 标记（规格 §3） */
const FILTER_ARG_MARKERS = ['行为单选', '未提供任何筛选条件']

/**
 * ChatReadError 前置类标记（§10.9）：未打开会话 / 会话存在但未切换 / 联系人不存在。
 * 属「页面状态与预期不符」而非页面改版——按 WRONG_PAGE 处理（可重试：用户打开/切换会话后再读即可）
 */
const CHAT_READ_PRECONDITION_MARKERS = ['未打开会话', '存在但未打开', '不存在']

/**
 * ChatOpenError 前置类标记（2026-08-27）：「会话列表中不存在」属联系人/页面状态问题——
 * WRONG_PAGE 可重试（用户确认姓名/创建会话后重试）；其余（同名多命中/滚动失败/头部未切换）
 * 是切换未生效或结构异常 → UI_CHANGED（不可自动重试，人工查看）。
 * 注意 ChatOpenError 的判定块必须在 POST_WRITE_VERIFY_MARKERS 之前（其文案会内嵌搜索失败
 * 原因，可能含写后校验标记词，但本 operation 是 readonly，不应误判 EXECUTION_UNKNOWN）。
 */
const CHAT_OPEN_PRECONDITION_MARKERS = ['会话列表中不存在']

function causeCode(err: unknown): string | undefined {
  const cause = (err as { cause?: { code?: unknown } } | null)?.cause
  return typeof cause?.code === 'string' ? cause.code : undefined
}

/**
 * 把 operation 执行期抛出的任意错误映射为统一错误码。
 * completed：出错前已完成的写动作数量（仅用于生成更准确的提示，不影响 code）。
 */
export function mapExecutorError(err: unknown): MappedError {
  if (err instanceof CancelledError) {
    return { code: 'CANCELLED', message: err.message }
  }
  if (err instanceof CodedOperationError) {
    return { code: err.code, message: err.message }
  }
  const message = err instanceof Error ? err.message : String(err)

  if (causeCode(err) === 'ECONNREFUSED' || CHROME_UNAVAILABLE_MARKERS.some((m) => message.includes(m))) {
    return {
      code: 'CHROME_UNAVAILABLE',
      message: `无法连接 Chrome 调试端口：${message}。请确认 Chrome 已带 --remote-debugging-port 启动`,
    }
  }
  if (NOT_LOGGED_IN_MARKERS.some((m) => message.includes(m))) {
    return {
      code: 'NOT_LOGGED_IN',
      message: `未找到已登录的 BOSS 页面：${message}。请确认已在该 Chrome 登录 BOSS 直聘并打开页面`,
    }
  }
  if (err instanceof GreetError && message.includes('付费墙')) {
    return { code: 'PAYWALL', message }
  }
  if (err instanceof FilterSetError && FILTER_ARG_MARKERS.some((m) => message.includes(m))) {
    return { code: 'INVALID_ARGUMENT', message }
  }
  if (err instanceof ChatReadError && CHAT_READ_PRECONDITION_MARKERS.some((m) => message.includes(m))) {
    return { code: 'WRONG_PAGE', message }
  }
  if (err instanceof ChatOpenError) {
    // 前置类（会话列表中不存在）→ WRONG_PAGE 可重试（确认姓名/创建会话后重试）；
    // 其余（同名多命中/滚动失败/头部未切换）→ UI_CHANGED（不可自动重试，人工查看）。
    // 本块必须在 POST_WRITE_VERIFY_MARKERS 之前：ChatOpenError 的兜底文案会内嵌搜索路径失败
    // 原因（可能含「未关闭」等写后校验标记词），但 open-chat 是 readonly 无外部写效应，
    // 不能被误判为 EXECUTION_UNKNOWN
    if (CHAT_OPEN_PRECONDITION_MARKERS.some((m) => message.includes(m))) {
      return { code: 'WRONG_PAGE', message }
    }
    return { code: 'UI_CHANGED', message }
  }
  if (err instanceof OverlayDismissError) {
    // 白名单违规（「立即领取」类）→ INVALID_ARGUMENT（编排契约错误，重试无意义）；
    // 未找到控件/点击后未关闭 → UI_CHANGED（弹层状态异常，人工查看）
    if (message.includes('不在关闭语义白名单内')) {
      return { code: 'INVALID_ARGUMENT', message }
    }
    return { code: 'UI_CHANGED', message }
  }
  if (POST_WRITE_VERIFY_MARKERS.some((m) => message.includes(m))) {
    return {
      code: 'EXECUTION_UNKNOWN',
      message: `${message}（写动作已发出但结果无法确认，请人工查看页面后再决定下一步，系统不会自动重试）`,
    }
  }
  if (err instanceof WinClickError && message.includes('未找到 scripts/')) {
    return { code: 'INTERNAL_ERROR', message: `PowerShell 脚本缺失：${message}` }
  }
  if (
    err instanceof GreetError ||
    err instanceof ConsentError ||
    err instanceof FilterSetError ||
    err instanceof NavError ||
    err instanceof ChatRejectError ||
    err instanceof InterviewDemoError ||
    err instanceof ChatSendError ||
    err instanceof ChatSearchError ||
    err instanceof ChatReadError ||
    err instanceof OverlayDismissError ||
    err instanceof JobSwitchError ||
    err instanceof ResumeReadError ||
    err instanceof ResumeBatchError ||
    err instanceof WinClickError
  ) {
    return { code: 'UI_CHANGED', message }
  }
  return { code: 'INTERNAL_ERROR', message: `未预期错误：${message}` }
}
