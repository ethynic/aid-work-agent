/**
 * wecom_send_image operation（M7）：向 target_ref 目标发送 1 张本地图片（智能分发）。
 *
 * 编排与 wecom_message_send 同源（M7 抽取的 navigate.ts runStageWithNavigation 共享
 * 承载）：verifyTargetRef 解出目标 → spawn send-image.ps1（发送阶段驱动：OCR 两带 +
 * Jev 三问判定当前会话/输入点/草稿；判定非目标 → navigate_required → 内部
 * chatSearch+chatSelect → 二次调用）→ 驱动内粘贴发送：点输入框 → 输入区像素方差
 * baseline → Clipboard.SetImage（5 次重试，全败 CONFIG_MISSING）→ attachstate Ctrl+V →
 * 1500ms 后方差复测确认缩略图出现（未出现 → UI_CHANGED，此时未按 Enter 无发送副作用）
 * → 发送前标题复核（不一致 → UI_CHANGED，图片预览非文本无法自动清理，已知限制）→
 * Enter → 1800ms → 终态证据双判据（输入区方差回落 + 会话列表含「[图片]」）→ Jev #2
 * 判定（no/unclear → EXECUTION_UNKNOWN）。E2 真机验证 2026-09-28（e5-image probe）。
 *
 * 文件契约（用户定稿）：image_path 只收本地绝对路径——调用方（agent/上层）负责把文件
 * 落到装有 runtime 与 wecom-cli 的机器上；png/jpg/jpeg/bmp/gif、≤20MB。TS 侧校验语义：
 * 不存在/类型不符/超限 → INVALID_ARGUMENT；路径存在但读取失败（含 stat/读内容异常）→
 * CONFIG_MISSING。SHA-256 由 TS 计算传入驱动（-ImageHash，仅日志用，驱动不复算）。
 *
 * 副作用：粘贴经剪贴板通道，**覆盖用户剪贴板且不恢复**（与 message-send 多行通道同款
 * 刻意行为）。写语义：成功 effect=applied；超时 300s → EXECUTION_UNKNOWN；取消 →
 * CANCELLED（effect=unknown）；零自动重试；定位阶段失败 effect=none 且注明未发送图片。
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
const DRIVER_PATH = fileURLToPath(new URL('../../../drivers/ps1/send-image.ps1', import.meta.url))

export interface WecomSendImageArgs {
  target_ref?: string
  /** M11a 直达模式：按会话名定位（与 target_ref 二选一；内部 search + 身份校验转 ref） */
  target_name?: string
  /** 图片本地绝对路径（调用方负责落盘到本机） */
  image_path: string
}

/** 图片大小上限 20MB（与 README / MCP description 契约一致） */
const MAX_IMAGE_BYTES = 20 * 1024 * 1024
/** 支持的图片扩展（大小写不敏感；gif 动图按首页帧粘贴） */
const IMAGE_EXT_PATTERN = /\.(png|jpe?g|bmp|gif)$/i

/** 驱动字段防御性解析：有限数字 → 保留 1 位小数，其余 null */
function roundStddev(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? Math.round(v * 10) / 10 : null
}

export function createWecomSendImageOperation(
  deps: {
    runDriverFn?: RunPowerShellDriverFn
    verifyRefFn?: VerifyTargetRefFn
    /** 内部 chatSearch 签发 target_ref 用（与 verifyRefFn 配套注入，测试可替换） */
    createRefFn?: CreateTargetRefFn
    artifactDirFn?: () => string | null
  } = {},
): WecomOperation<WecomSendImageArgs> {
  const runDriverFn = deps.runDriverFn ?? runPowerShellDriver
  const verifyRefFn = deps.verifyRefFn ?? verifyTargetRef
  // 与 chatSearch 的默认签发适配保持一致（坐标随条目写入 payload，select 消费）
  const createRefFn: CreateTargetRefFn =
    deps.createRefFn ?? ((name, type, subtitle, coords) => createTargetRef(name, type, subtitle, { coords }))
  const artifactDirFn = deps.artifactDirFn ?? (() => artifactDir())
  return {
    name: 'wecom_send_image',
    execute(args: WecomSendImageArgs, ctx: OpContext): Promise<OperationResult> {
      return runWecomOperation(
        'write',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（target_ref/target_name 二选一；image_path 必填）'
          }
          const selectorErr = validateTargetSelector(args)
          if (selectorErr !== null) return selectorErr
          if (typeof args.image_path !== 'string' || args.image_path.length === 0) {
            return 'image_path 必填且必须是非空字符串'
          }
          if (!isAbsolute(args.image_path)) {
            return 'image_path 必须是本地绝对路径（文件契约：调用方负责把图片落到装有 runtime 与 wecom-cli 的机器上）'
          }
          if (!IMAGE_EXT_PATTERN.test(args.image_path)) {
            return '不支持的图片类型（仅支持 png/jpg/jpeg/bmp/gif）'
          }
          return null
        },
        async (opCtx, tracker) => {
          const root = artifactDirFn()
          if (!root) {
            throw new CodedOperationError('INTERNAL_ERROR', '%LOCALAPPDATA% 未设置，无法定位 artifact 目录')
          }

          // 文件契约校验（需要 fs 访问，走 CodedOperationError 精确错误码）：
          // 不存在/非常规文件 → INVALID_ARGUMENT；存在但无法访问/读取 → CONFIG_MISSING
          let sizeBytes: number
          try {
            const st = statSync(args.image_path)
            if (!st.isFile()) {
              throw new CodedOperationError('INVALID_ARGUMENT', `image_path 不是常规文件：${args.image_path}`)
            }
            sizeBytes = st.size
          } catch (err) {
            if (err instanceof CodedOperationError) throw err
            if ((err as NodeJS.ErrnoException)?.code === 'ENOENT') {
              throw new CodedOperationError('INVALID_ARGUMENT', `图片文件不存在：${args.image_path}`)
            }
            throw new CodedOperationError(
              'CONFIG_MISSING',
              `图片文件存在但无法访问（${(err as NodeJS.ErrnoException)?.code ?? '未知错误'}）：${args.image_path}`,
            )
          }
          if (sizeBytes > MAX_IMAGE_BYTES) {
            throw new CodedOperationError(
              'INVALID_ARGUMENT',
              `图片大小超过 20MB 上限（实际 ${Math.round((sizeBytes / 1024 / 1024) * 10) / 10}MB），请压缩后重试`,
            )
          }
          let sha256: string
          try {
            sha256 = createHash('sha256').update(readFileSync(args.image_path)).digest('hex')
          } catch {
            throw new CodedOperationError('CONFIG_MISSING', `图片文件读取失败（无法计算 SHA-256）：${args.image_path}`)
          }

          // M11a 直达模式：内部 search 身份定位转 ref（定位失败 effect=none 且注明未发送
          // 图片），定位成功后走与 ref 模式完全一致的既有链路
          let resolved: ResolvedByNameTarget | null = null
          if (args.target_name !== undefined) {
            resolved = await resolveTargetByName(
              { runDriverFn, createRefFn, opCtx, root },
              args.target_name,
              '',
              { notSentNote: '未发送图片' },
            )
          }
          const target = verifyRefFn(resolved !== null ? resolved.target_ref : args.target_ref!)
          // 与 chat_select 同款要求 M4+ 含坐标 ref（send-image 分发链路消费坐标句柄）
          if (target.x === undefined || target.y === undefined) {
            throw new CodedOperationError('INVALID_ARGUMENT', '该 target_ref 不含坐标（旧版签发），请重新 search')
          }
          opCtx.progress({ stage: 'execute', message: `解析目标「${target.name}」，粘贴图片前检查当前会话` })

          const spawnSendImageDriver = (dir: string): Promise<Record<string, unknown>> =>
            runDriverFn({
              script: DRIVER_PATH,
              args: [
                '-TargetName', target.name,
                '-Subtitle', target.subtitle,
                '-Section', target.type,
                '-ImagePath', args.image_path,
                '-ImageHash', sha256,
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
            artifactPrefix: 'send-image',
            spawnStage: spawnSendImageDriver,
            texts: {
              writeDesc: '图片消息',
              notSentNote: '未发送图片',
              checkDesc: '粘贴图片前检查',
              afterEnter: '执行图片发送',
            },
          })
          tracker.completed = 1

          // 驱动字段防御性归一（sent_verification / input_stddev / timing / screenshots）
          const svRaw = (data.sent_verification ?? null) as Record<string, unknown> | null
          const sentVerification =
            svRaw !== null && typeof svRaw === 'object'
              ? {
                  method: svRaw.method === 'rule_2of2' ? 'rule_2of2' : 'jev',
                  result: typeof svRaw.result === 'string' ? svRaw.result : 'unknown',
                  ...(typeof svRaw.failure_mode === 'string' ? { failure_mode: svRaw.failure_mode } : {}),
                }
              : null
          const sdRaw = (data.input_stddev ?? null) as Record<string, unknown> | null
          const inputStddev =
            sdRaw !== null && typeof sdRaw === 'object'
              ? {
                  before: roundStddev(sdRaw.before),
                  paste: roundStddev(sdRaw.paste),
                  after: roundStddev(sdRaw.after),
                }
              : null
          const methodDesc = sentVerification?.method === 'rule_2of2' ? '规则双判据' : 'Jev'
          return {
            message: `已向「${target.name}」发送图片消息，发送后校验通过（${methodDesc}校验${navigated ? '，已自动搜索并切换会话' : '，当前会话直发'}）`,
            data: {
              target: target.name,
              ...(resolved !== null
                ? { resolved_target: { name: resolved.name, subtitle: resolved.subtitle, section: resolved.section } }
                : {}),
              title: typeof data.title === 'string' && data.title.length > 0 ? data.title : target.name,
              navigated,
              image: { name: basename(args.image_path), size_bytes: sizeBytes, sha256 },
              sent_verification: sentVerification,
              input_stddev: inputStddev,
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
