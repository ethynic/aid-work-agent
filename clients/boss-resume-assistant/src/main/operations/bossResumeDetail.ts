/**
 * boss_resume_detail operation（设计 §10.8）：读取当前打开的候选人在线简历详情并组装入库契约 payload。
 *
 * 简历详情是 canvas 像素（WASM fillText），DOM 抓不到文字 → 链路：
 * Win32 滚轮回顶 → 分段整页截图 → 重叠对齐拼接（scripts/cv-stitch.ps1）→ Windows OCR（scripts/cv-ocr.ps1）。
 * 只读（effect=none），但滚动借用真实鼠标约 1-2 秒，执行期间用户手不能碰鼠标。
 *
 * 输出契约（docs/plans/recruiting/resume-detail-cli-integration-handoff.md §2，与云端
 * recruiting_resume_service.create_resume_record_from_tool_result 对齐）：
 * data = { candidate_name（必填）, job_name?, ocr_text, images:[{name,mime_type,base64}], …元信息 }。
 * 云端 BossResumeDetailTool 拿本 payload 直接入库，返回 LLM 的只有紧凑摘要（图片字节绝不进上下文）。
 *
 * candidate_name 优先取入参（智能体会话上下文通常已知姓名），缺省用 OCR 首行启发式
 * （extractCandidateNameFromOcr）兜底；两者都无 → INVALID_ARGUMENT fail-loud，绝不瞎猜入库。
 * job_name 取推荐牛人页职位框当前职位（JobSwitcher.locateJobBox；非推荐页为 null，契约可选）。
 *
 * 前置校验：当前页面必须已打开候选人简历详情（存在大尺寸 CANVAS）→ 否则 WRONG_PAGE。
 * 详见 src/main/boss/ResumeReader.ts 头注释（坐标系/滚轮/到底检测的真机结论）。
 */
import { execFile } from 'node:child_process'
import fs from 'node:fs'
import fsp from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  MAX_SEGMENTS,
  ResumeReader,
  ResumeReadError,
  extractCandidateNameFromOcr,
  locateResumeCanvas,
  type DeviceRect,
} from '../boss/ResumeReader.js'
import { JobSwitcher } from '../boss/JobSwitcher.js'
import { viewportOf } from '../boss/FilterSetter.js'
import { WinClickError } from '../input/WinMouseClicker.js'
import {
  defaultSessionFactory,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import { CodedOperationError, type BossOperation, type OperationResult, type OpContext } from './types.js'

export interface BossResumeDetailArgs {
  /** 候选人姓名（智能体会话上下文已知时传入；缺省用 OCR 首行启发式识别，识别失败报错） */
  candidate_name?: string
  /** 可选：拼接长图保存路径（PNG） */
  save_image_to?: string
}

type PsErrorCtor = new (message: string, exitCode?: number) => Error

/** 执行 PowerShell 脚本（复用 win-click.ps1 的调用约定）；非零退出 fail-loud */
function runPs(
  scriptPath: string,
  args: string[],
  timeoutMs: number,
  label: string,
  ErrorCtor: PsErrorCtor,
): Promise<{ stdout: string }> {
  return new Promise((resolve, reject) => {
    execFile(
      'powershell.exe',
      ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', scriptPath, ...args],
      { timeout: timeoutMs },
      (error, stdout, stderr) => {
        if (error) {
          const code = typeof error.code === 'number' ? error.code : undefined
          reject(new ErrorCtor(`${label} 执行失败(exit=${code ?? 'unknown'}): ${stderr || error.message}`, code))
        } else {
          resolve({ stdout: String(stdout) })
        }
      },
    )
  })
}

/**
 * scripts/<name> 绝对路径（与 WinMouseClicker.defaultScriptPath 同款探测）。
 * 源码（src/main/operations）向上 3 级、编译后（dist/src/main/operations）向上 4 级是工程根。
 */
function cvScriptPath(name: string): string {
  const here = path.dirname(fileURLToPath(import.meta.url))
  for (const up of ['..\\..\\..\\..', '..\\..\\..']) {
    const candidate = path.resolve(here, up, 'scripts', name)
    if (fs.existsSync(candidate)) return candidate
  }
  throw new WinClickError(`未找到 scripts/${name}（已从 ${here} 向上探测）`)
}

/**
 * Win32 滚轮：滚动点 = canvas 中心（page device px），CssW/CssH = viewport device 尺寸（DPI 换算在 ps1 内）。
 * TitleKeyword 显式传 win-click.ps1 同款「BOSS直聘 - Google Chrome」：不用 cv-wheel 的默认 "Google Chrome"，
 * 否则多 Chrome 窗口时 EnumWindows 按 Z 序可能命中其他窗口 → 错滚 → BOSS canvas 纹丝不动 →
 * 各段截图字节相同被判「到底」→ 静默只返回首屏半成品（违背 fail-loud，真机标题 2026-08-14 实证）。
 */
async function wheelAt(
  rect: DeviceRect,
  viewport: { width: number; height: number },
  deltaY: number,
  notches: number,
): Promise<void> {
  const cx = Math.round(rect.x + rect.w / 2)
  const cy = Math.round(rect.y + rect.h / 2)
  await runPs(
    cvScriptPath('cv-wheel.ps1'),
    [
      '-X', String(cx),
      '-Y', String(cy),
      '-CssW', String(viewport.width),
      '-CssH', String(viewport.height),
      '-DeltaY', String(deltaY),
      '-Notches', String(notches),
      '-TitleKeyword', 'BOSS直聘 - Google Chrome',
    ],
    60000,
    'cv-wheel.ps1',
    WinClickError,
  )
}

/** 裁剪 + 对齐 + 拼接：解析 cv-stitch.ps1 stdout（overlapNN=<n> / stitched=<w>x<h>） */
async function stitchParts(
  parts: string[],
  rect: DeviceRect,
  outFile: string,
): Promise<{ width: number; height: number; overlaps: number[] }> {
  const { stdout } = await runPs(
    cvScriptPath('cv-stitch.ps1'),
    [
      '-Parts', parts.join(','),
      '-X', String(rect.x),
      '-Y', String(rect.y),
      '-W', String(rect.w),
      '-H', String(rect.h),
      '-Out', outFile,
    ],
    180000,
    'cv-stitch.ps1',
    ResumeReadError,
  )
  const stitched = /stitched=(\d+)x(\d+)/.exec(stdout)
  if (!stitched) {
    throw new ResumeReadError(`cv-stitch.ps1 输出缺少 stitched=<w>x<h> 行：${stdout.trim()}`)
  }
  const overlaps = [...stdout.matchAll(/overlap\d+=(\d+)/g)].map((m) => Number(m[1]!))
  return { width: Number(stitched[1]), height: Number(stitched[2]), overlaps }
}

/** WinRT OCR：cv-ocr.ps1 把 UTF-8 文本写 <img>.txt，node 读回 */
async function ocrImage(imgFile: string): Promise<string> {
  const outFile = `${imgFile}.txt`
  await runPs(cvScriptPath('cv-ocr.ps1'), ['-Img', imgFile, '-Out', outFile], 300000, 'cv-ocr.ps1', ResumeReadError)
  return await fsp.readFile(outFile, 'utf8')
}

export function createBossResumeDetailOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<BossResumeDetailArgs> {
  return {
    name: 'boss_resume_detail',
    execute(args: BossResumeDetailArgs, ctx: OpContext): Promise<OperationResult> {
      const rawName = args?.candidate_name
      const nameParam = (rawName ?? '').trim()
      const rawSaveTo = args?.save_image_to
      const saveImageTo = (rawSaveTo ?? '').trim()
      return runBossOperation(
        'readonly',
        ctx,
        sessionFactory,
        () => (rawSaveTo !== undefined && !saveImageTo ? 'save_image_to（拼接图保存路径）不能为空' : null),
        async (session) => {
          // 前置校验：必须已打开候选人简历详情（存在大尺寸 CANVAS）
          const probe = await session.snapshot()
          const rect = locateResumeCanvas(probe)
          if (!rect) {
            throw new CodedOperationError(
              'WRONG_PAGE',
              '当前页面没有打开候选人在线简历详情（未找到简历画布）：请先在推荐牛人页点开一个候选人详情，再重试',
            )
          }
          // job_name：推荐牛人页职位框的当前职位（JobSwitcher.locateJobBox 复用；非推荐页为 null，契约可选）
          const jobBox = new JobSwitcher({ snapshot: session.snapshot, click: session.click }).locateJobBox(probe)
          const viewport = viewportOf(probe)
          const reader = new ResumeReader({
            snapshot: session.snapshot,
            captureFullpage: session.captureFullpage,
            wheel: (deltaY, notches) => wheelAt(rect, viewport, deltaY, notches),
            stitch: stitchParts,
            ocr: ocrImage,
            signal: ctx.signal,
            onProgress: (stage, info) => {
              if (stage === 'stitch') {
                ctx.progress({ stage: 'execute', message: `拼接 ${info.segments} 段截图` })
              } else {
                ctx.progress({ stage: 'execute', message: 'OCR 识别简历全文' })
              }
            },
          })
          ctx.progress({ stage: 'execute', message: '回顶并分段截图（借用真实鼠标，请勿移动）' })
          const result = await reader.readResume({ saveImageTo: saveImageTo || undefined })
          // 候选人姓名：入参优先（智能体上下文已知），缺省 OCR 首行启发式；都无 → fail-loud 绝不瞎猜入库
          const candidateName = nameParam || extractCandidateNameFromOcr(result.text) || ''
          if (!candidateName) {
            throw new CodedOperationError(
              'INVALID_ARGUMENT',
              '无法确定候选人姓名（未传 candidate_name 且 OCR 首行未识别出姓名）：请显式传 candidate_name 参数（当前会话候选人的姓名）后重试',
            )
          }
          const truncateWarn = result.bottomReached
            ? ''
            : `；⚠️ 已达分段上限 ${MAX_SEGMENTS} 段仍未确认到底，简历内容可能被截断，请人工核对`
          return {
            message:
              `完成：读取「${candidateName}」简历全文 ${result.chars} 字（${result.segments} 段拼接，${result.width}x${result.height}）` +
              truncateWarn,
            // 云端入库契约 payload（handoff §2）：candidate_name 必填，其余可选；额外元信息云端按需忽略
            data: {
              candidate_name: candidateName,
              ...(jobBox.name ? { job_name: jobBox.name } : {}),
              ocr_text: result.text,
              images: [
                {
                  name: 'resume_full.png',
                  mime_type: 'image/png',
                  base64: result.imageBuffer.toString('base64'),
                },
              ],
              // 元信息（云端契约不读取，供 CLI 展示/调试）
              name_source: nameParam ? 'param' : 'ocr',
              ocr_chars: result.chars,
              segments: result.segments,
              bottom_reached: result.bottomReached,
              image_width: result.width,
              image_height: result.height,
              fetched_at: new Date().toISOString(),
            },
            effect: 'none',
          }
        },
      )
    },
  }
}
