/**
 * wecom_send_file operation（M8）：向 target_ref 目标发送 1 个本地文件（智能分发）。
 *
 * 编排与 wecom_send_image 同源（navigate.ts runStageWithNavigation 共享承载）：
 * verifyTargetRef 解出目标 → spawn send-file.ps1（发送阶段驱动：OCR 两带 + Jev
 * 三问判定当前会话/输入点/草稿；判定非目标 → navigate_required → 内部
 * chatSearch+chatSelect → 二次调用）→ 驱动内粘贴发送：点输入框 → 输入区（y≥0.75h、
 * x∈(聊天区左界 max(0.10w,620), 0.74w]，E3 标定带 + 侧栏排除）OCR 干净基线 →
 * Clipboard.SetFileDropList（StringCollection + 绝对路径，5 次重试，全败
 * CONFIG_MISSING）→ attachstate Ctrl+V → 1500ms 后粘贴校验：输入区出现基线没有的
 * 新 token 且归一化 contains 文件名主干（去扩展名取前 12 字；主干归一化 <3 字符回退
 * 完整文件名匹配键）——不含 → UI_CHANGED（此时未按 Enter 无发送副作用）→ 发送前标题
 * 复核（不一致 → UI_CHANGED，文件卡片非文本草稿无法自动清理，已知限制）→ Enter →
 * 1800ms → 终态证据双判据（同为基线差分：输入区文件名新 token 消失 + 会话列表新 token
 * 含文件名）→ Jev #2 判定（no/unclear → EXECUTION_UNKNOWN）。
 * E3 真机验证 2026-09-28（e6-file probe）。
 *
 * 文件契约（用户定稿）：file_path 只收本地绝对路径——调用方（agent/上层）负责把文件
 * 落到装有 runtime 与 wecom-cli 的机器上；扩展名不限（任意文件），≤100MB（对齐
 * RPA AttachmentDownloader 先例）。TS 侧校验语义：不存在/是目录/超限/相对路径/文件名
 * 去空白后不足 3 字符 → INVALID_ARGUMENT；路径存在但读取失败（含 stat/读内容异常）→
 * CONFIG_MISSING。SHA-256 由 TS 计算传入驱动（-FileHash，仅日志用，驱动不复算）。
 *
 * 副作用：粘贴经剪贴板通道，**覆盖用户剪贴板且不恢复**（与 send-image 同款刻意
 * 行为）。写语义：成功 effect=applied；超时 300s → EXECUTION_UNKNOWN；取消 →
 * CANCELLED（effect=unknown）；零自动重试；定位阶段失败 effect=none 且注明未发送文件。
 *
 * M11a 直达模式：target_name 与 target_ref 二选一——name 模式经 resolveTargetByName
 * 内部 search 挑唯一目标转 ref 后走同一链路（内部签发的 ref 含坐标，分发链路可消费），
 * data 附 resolved_target；effect/错误码/零重试契约与 ref 模式一致。
 */
import { createHash } from 'node:crypto'
import { readFileSync, statSync } from 'node:fs'
import { basename, isAbsolute } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runWecomOperation } from './context.js'
import { CodedOperationError, type OperationResult, type OpContext, type WecomOperation } from './types.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from '../platform/powershell.js'
import { artifactDir } from '../platform/environment.js'
import {
  createTargetRef,
  verifyTargetRef,
  type CreateTargetRefFn,
  type VerifyTargetRefFn,
} from '../platform/targetRef.js'
import {
  resolveTargetByName,
  runStageWithNavigation,
  sanitizeTiming,
  validateTargetSelector,
  type ResolvedByNameTarget,
} from './navigate.js'

/** dist/src/operations → 包根 drivers/ps1 */
const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/send-file.ps1', import.meta.url))

export interface WecomSendFileArgs {
  target_ref?: string
  /** M11a 直达模式：按会话名定位（与 target_ref 二选一；内部 search + 身份校验转 ref） */
  target_name?: string
  /** 文件本地绝对路径（调用方负责落盘到本机；扩展名不限） */
  file_path: string
}

/** 文件大小上限 100MB（与 README / MCP description 契约一致，对齐 RPA AttachmentDownloader 先例） */
const MAX_FILE_BYTES = 100 * 1024 * 1024

/** 驱动字段防御性解析：paste_check（粘贴/终态判据结果，bool 字段宽松归一；stem=驱动实际匹配键——主干，主干过短时为完整文件名） */
function parsePasteCheck(raw: unknown): { stem: string; paste_hit: boolean; input_gone: boolean; list_hit: boolean } | null {
  if (raw === null || typeof raw !== 'object') return null
  const pc = raw as Record<string, unknown>
  return {
    stem: typeof pc.stem === 'string' ? pc.stem : '',
    paste_hit: pc.paste_hit === true,
    input_gone: pc.input_gone === true,
    list_hit: pc.list_hit === true,
  }
}

export function createWecomSendFileOperation(
  deps: {
    runDriverFn?: RunPowerShellDriverFn
    verifyRefFn?: VerifyTargetRefFn
    /** 内部 chatSearch 签发 target_ref 用（与 verifyRefFn 配套注入，测试可替换） */
    createRefFn?: CreateTargetRefFn
    artifactDirFn?: () => string | null
  } = {},
): WecomOperation<WecomSendFileArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  const verifyRefFn = deps.verifyRefFn ?? verifyTargetRef
  // 与 chatSearch 的默认签发适配保持一致（坐标随条目写入 payload，select 消费）
  const createRefFn: CreateTargetRefFn =
    deps.createRefFn ?? ((name, type, subtitle, coords) => createTargetRef(name, type, subtitle, { coords }))
  const artifactDirFn = deps.artifactDirFn ?? (() => artifactDir())
  return {
    name: 'wecom_send_file',
    execute(args: WecomSendFileArgs, ctx: OpContext): Promise<OperationResult> {
      return runWecomOperation(
        'write',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（target_ref/target_name 二选一；file_path 必填）'
          }
          const selectorErr = validateTargetSelector(args)
          if (selectorErr !== null) return selectorErr
          if (typeof args.file_path !== 'string' || args.file_path.length === 0) {
            return 'file_path 必填且必须是非空字符串'
          }
          if (!isAbsolute(args.file_path)) {
            return 'file_path 必须是本地绝对路径（文件契约：调用方负责把文件落到装有 runtime 与 wecom-cli 的机器上）'
          }
          // 极短文件名护栏：去空白后不足 3 字符（如「a」「ab」「a b」）时，驱动侧 OCR 判据
          // 无论用主干还是完整文件名都只有 1-2 个字符，contains 匹配会撞上任意无关 token，
          // 发送校验形同虚设 → 拒发（注意「a.txt」不在拒绝范围：主干「a」过短时驱动回退用
          // 完整文件名「a.txt」做匹配键，仍可可靠区分）
          if (basename(args.file_path).replace(/\s+/g, '').length < 3) {
            return '文件名过短（去空白后不足 3 字符），OCR 发送校验无法可靠区分文件卡片与无关文本，请改名后重试'
          }
          return null
        },
        async (opCtx, tracker) => {
          const root = artifactDirFn()
          if (!root) {
            throw new CodedOperationError('INTERNAL_ERROR', '%LOCALAPPDATA% 未设置，无法定位 artifact 目录')
          }

          // 文件契约校验（需要 fs 访问，走 CodedOperationError 精确错误码）：
          // 不存在/是目录/超限 → INVALID_ARGUMENT；存在但无法访问/读取 → CONFIG_MISSING
          let sizeBytes: number
          try {
            const st = statSync(args.file_path)
            if (!st.isFile()) {
              throw new CodedOperationError('INVALID_ARGUMENT', `file_path 不是常规文件：${args.file_path}`)
            }
            sizeBytes = st.size
          } catch (err) {
            if (err instanceof CodedOperationError) throw err
            if ((err as NodeJS.ErrnoException)?.code === 'ENOENT') {
              throw new CodedOperationError('INVALID_ARGUMENT', `文件不存在：${args.file_path}`)
            }
            throw new CodedOperationError(
              'CONFIG_MISSING',
              `文件存在但无法访问（${(err as NodeJS.ErrnoException)?.code ?? '未知错误'}）：${args.file_path}`,
            )
          }
          if (sizeBytes > MAX_FILE_BYTES) {
            throw new CodedOperationError(
              'INVALID_ARGUMENT',
              `文件大小超过 100MB 上限（实际 ${Math.round((sizeBytes / 1024 / 1024) * 10) / 10}MB），请压缩或改用其他方式传输`,
            )
          }
          let sha256: string
          try {
            sha256 = createHash('sha256').update(readFileSync(args.file_path)).digest('hex')
          } catch {
            throw new CodedOperationError('CONFIG_MISSING', `文件读取失败（无法计算 SHA-256）：${args.file_path}`)
          }

          // M11a 直达模式：内部 search 身份定位转 ref（定位失败 effect=none 且注明未发送
          // 文件），定位成功后走与 ref 模式完全一致的既有链路
          let resolved: ResolvedByNameTarget | null = null
          if (args.target_name !== undefined) {
            resolved = await resolveTargetByName(
              { runDriverFn, createRefFn, opCtx, root },
              args.target_name,
              '',
              { notSentNote: '未发送文件' },
            )
          }
          const target = verifyRefFn(resolved !== null ? resolved.target_ref : args.target_ref!)
          // 与 chat_select 同款要求 M4+ 含坐标 ref（send-file 分发链路消费坐标句柄）
          if (target.x === undefined || target.y === undefined) {
            throw new CodedOperationError('INVALID_ARGUMENT', '该 target_ref 不含坐标（旧版签发），请重新 search')
          }
          opCtx.progress({ stage: 'execute', message: `解析目标「${target.name}」，粘贴文件前检查当前会话` })

          const spawnSendFileDriver = (dir: string): Promise<Record<string, unknown>> =>
            runDriverFn({
              script: DRIVER_PATH,
              args: [
                '-TargetName', target.name,
                '-Subtitle', target.subtitle,
                '-Section', target.type,
                '-FilePath', args.file_path,
                '-FileHash', sha256,
                '-ArtifactDir', dir,
              ],
              signal: opCtx.signal,
            })

          const { data, navigated } = await runStageWithNavigation({
            runDriverFn,
            verifyRefFn,
            createRefFn,
            opCtx,
            root,
            target,
            artifactPrefix: 'send-file',
            spawnStage: spawnSendFileDriver,
            texts: {
              writeDesc: '文件消息',
              notSentNote: '未发送文件',
              checkDesc: '粘贴文件前检查',
              afterEnter: '执行文件发送',
            },
          })
          tracker.completed = 1

          // 驱动字段防御性归一（sent_verification / paste_check / timing / screenshots）
          const svRaw = (data.sent_verification ?? null) as Record<string, unknown> | null
          const sentVerification =
            svRaw !== null && typeof svRaw === 'object'
              ? {
                  method: svRaw.method === 'rule_2of2' ? 'rule_2of2' : 'jev',
                  result: typeof svRaw.result === 'string' ? svRaw.result : 'unknown',
                  ...(typeof svRaw.failure_mode === 'string' ? { failure_mode: svRaw.failure_mode } : {}),
                }
              : null
          const methodDesc = sentVerification?.method === 'rule_2of2' ? '规则双判据' : 'Jev'
          return {
            message: `已向「${target.name}」发送文件消息，发送后校验通过（${methodDesc}校验${navigated ? '，已自动搜索并切换会话' : '，当前会话直发'}）`,
            data: {
              target: target.name,
              ...(resolved !== null
                ? { resolved_target: { name: resolved.name, subtitle: resolved.subtitle, section: resolved.section } }
                : {}),
              title: typeof data.title === 'string' && data.title.length > 0 ? data.title : target.name,
              navigated,
              file: { name: basename(args.file_path), size_bytes: sizeBytes, sha256 },
              sent_verification: sentVerification,
              paste_check: parsePasteCheck(data.paste_check),
              timing_ms: sanitizeTiming(data.timing_ms),
              screenshot_paths: Array.isArray(data.screenshot_paths)
                ? data.screenshot_paths.filter((p): p is string => typeof p === 'string')
                : [],
            },
          }
        },
      )
    },
  }
}
