/**
 * boss_read_resume operation（设计 §10.8）：读取推荐牛人页当前打开的候选人在线简历全文。
 *
 * 简历详情是 canvas 像素（WASM fillText），DOM 抓不到文字 → 链路：
 * Win32 滚轮回顶 → 分段整页截图 → 重叠对齐拼接（scripts/cv-stitch.ps1）→ Windows OCR（scripts/cv-ocr.ps1）。
 * 只读（effect=none），但滚动借用真实鼠标约 1-2 秒，执行期间用户手不能碰鼠标。
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
  locateResumeCanvas,
  type DeviceRect,
} from '../boss/ResumeReader.js'
import { viewportOf } from '../boss/FilterSetter.js'
import { WinClickError } from '../input/WinMouseClicker.js'
import {
  defaultSessionFactory,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import { CodedOperationError, type BossOperation, type OperationResult, type OpContext } from './types.js'

export interface BossReadResumeArgs {
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

export function createBossReadResumeOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<BossReadResumeArgs> {
  return {
    name: 'boss_read_resume',
    execute(args: BossReadResumeArgs, ctx: OpContext): Promise<OperationResult> {
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
          const truncateWarn = result.bottomReached
            ? ''
            : `；⚠️ 已达分段上限 ${MAX_SEGMENTS} 段仍未确认到底，简历内容可能被截断，请人工核对`
          return {
            message:
              `完成：读取简历全文 ${result.chars} 字（${result.segments} 段拼接，${result.width}x${result.height}）` +
              truncateWarn,
            data: {
              text: result.text,
              chars: result.chars,
              segments: result.segments,
              bottom_reached: result.bottomReached,
              width: result.width,
              height: result.height,
            },
            effect: 'none',
          }
        },
      )
    },
  }
}
