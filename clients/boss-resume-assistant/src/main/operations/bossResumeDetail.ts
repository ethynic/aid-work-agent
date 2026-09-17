/**
 * boss_resume_detail operation（设计 §10.8）：读取当前打开的候选人在线简历详情并组装入库契约 payload。
 *
 * 简历详情是 canvas 像素（WASM fillText），DOM 抓不到文字 → 链路：
 * Win32 滚轮回顶 → 分段整页截图（字节差 + cv-segdiff.ps1 像素确认 + 连续两次相同才判到底）→
 * 重叠对齐拼接（scripts/cv-stitch.ps1）。文本识别在云端（boss_resume_detail 工具层调
 * GLM-5.3-Flash 多模态读拼接长图，2026-09-17 去 OCR 化）——本地 OCR 链路（RapidOCR/WinRT）
 * 已整体下线，客户端不再产出文本。
 * 只读（effect=none），但滚动借用真实鼠标约 1-2 秒，执行期间用户手不能碰鼠标。
 *
 * 输出契约（docs/plans/recruiting/resume-detail-cli-integration-handoff.md §2，与云端
 * recruiting_resume_service.create_resume_record_from_tool_result 对齐）：
 * data = { candidate_name（必填）, name_source:'param'（云端观测字段：标注姓名来源非截图识别）,
 * job_name?, images:[{name,mime_type,base64}], …元信息 }（ocr_text 由云端识别后回填）。
 * 云端 BossResumeDetailTool 拿本 payload 识别+入库，返回 LLM 的只有紧凑摘要（图片字节绝不进上下文）。
 *
 * candidate_name 必传（智能体会话上下文的候选人姓名）：姓名唯一来源=非截图识别，缺省在 validate
 * 阶段（connect Chrome 之前、读取之前）直接 INVALID_ARGUMENT 报错，绝不用识别猜名入库（错名入库后
 * 打招呼会打错人）。姓名与识别文本的交叉校验在云端做（resume_name_matches，容忍 1 字识别误差）。
 * job_name 取推荐牛人页职位框当前职位（JobSwitcher.locateJobBox；非推荐页为 null，契约可选）。
 *
 * 前置校验：当前页面必须已打开候选人简历详情（存在大尺寸 CANVAS）→ 否则 WRONG_PAGE。
 * 详见 src/main/boss/ResumeReader.ts 头注释（坐标系/滚轮/到底检测的真机结论）。
 */
import { execFile } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  MAX_SEGMENTS,
  ResumeReader,
  ResumeReadError,
  locateResumeCanvas,
  type DeviceRect,
  type ResumeReadResult,
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
  /** 候选人姓名（必传：智能体会话上下文已知的姓名；缺省直接 INVALID_ARGUMENT 报错，绝不猜名入库） */
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
 *
 * export：bossResumeBatch operation（同域：读简历 canvas）复用同一套 PS runner。
 */
export async function wheelAt(
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

/**
 * 裁剪 + 对齐 + 拼接：解析 cv-stitch.ps1 stdout（overlapNN=<n> mis=<rate> / stitched=<w>x<h>）。
 * seamMis = 每接缝错配率（P1 接缝质量元信息，>SEAM_MIS_SUSPECT 由 ResumeReader 归入 suspectSeams）。
 * export：bossResumeBatch operation 复用（同域共用）。
 */
export async function stitchParts(
  parts: string[],
  rect: DeviceRect,
  outFile: string,
): Promise<{ width: number; height: number; overlaps: number[]; seamMis: number[] }> {
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
  // 接缝行形如 "overlap01=746 mis=0.08"（mis 为 InvariantCulture F2）；overlap/mis 成对解析
  const seamLines = [...stdout.matchAll(/overlap\d+=(\d+)\s+mis=([\d.]+)/g)]
  return {
    width: Number(stitched[1]),
    height: Number(stitched[2]),
    overlaps: seamLines.map((m) => Number(m[1]!)),
    seamMis: seamLines.map((m) => Number(m[2]!)),
  }
}

/** 同画面判定阈值：cv-segdiff.ps1 采样错配率 ≤ 1% 视为同一画面（同一帧 WASM 重绘的亚像素差异 <1%） */
export const SAME_VIEW_MISRATE_MAX = 0.01

/**
 * 像素级同画面确认（P1 到底判定加固，仿 wheelAt 模式调 scripts/cv-segdiff.ps1）：
 * 比较两段整页截图的 canvas 区域（rect）是否同一画面。裁剪越界（窗口中途变化）时 PS exit 1
 * → runPs 抛 ResumeReadError（fail-loud，由 ResumeReader 包装后上抛）。
 * export：bossResumeBatch operation 复用（同域共用）。
 */
export async function sameViewAt(a: string, b: string, rect: DeviceRect): Promise<boolean> {
  const { stdout } = await runPs(
    cvScriptPath('cv-segdiff.ps1'),
    [
      '-A', a,
      '-B', b,
      '-X', String(rect.x),
      '-Y', String(rect.y),
      '-W', String(rect.w),
      '-H', String(rect.h),
    ],
    120000,
    'cv-segdiff.ps1',
    ResumeReadError,
  )
  const m = /misrate=([\d.]+)/.exec(stdout)
  if (!m) {
    throw new ResumeReadError(`cv-segdiff.ps1 输出缺少 misrate=<x> 行：${stdout.trim()}`)
  }
  return Number(m[1]!) <= SAME_VIEW_MISRATE_MAX
}

/**
 * 组装云端简历库契约 payload（handoff §2，与 recruiting_resume_service.create_resume_record_from_tool_result
 * 对齐）：candidate_name 必填，name_source 标注姓名来源（'param'=显式入参 / 'dom'=卡片
 * DOM 配对；云端观测不拦截，向后兼容旧客户端），job_name 可选，
 * images base64 + 截图/拼接元信息。ocr_text/ocr_chars/ocr_engine 由云端 VL 识别后回填
 * （2026-09-17 去 OCR 化），客户端不产出文本。boss_resume_detail / boss_resume_batch
 * 两个 operation 共用（单份与批量同契约）。
 */
export function buildResumePayload(
  candidateName: string,
  jobName: string | null,
  readResult: ResumeReadResult,
  nameSource: 'param' | 'dom',
): Record<string, unknown> {
  return {
    candidate_name: candidateName,
    name_source: nameSource,
    ...(jobName ? { job_name: jobName } : {}),
    images: [
      {
        name: 'resume_full.png',
        mime_type: 'image/png',
        base64: readResult.imageBuffer.toString('base64'),
      },
    ],
    // 元信息（云端识别后回填 ocr_text/ocr_chars/ocr_engine；本批字段云端契约不读取，供 CLI 展示/调试）
    segments: readResult.segments,
    bottom_reached: readResult.bottomReached,
    image_width: readResult.width,
    image_height: readResult.height,
    // P1 拼接加固元信息（云端按需忽略）：接缝错配率与可疑接缝（1-based 序号）
    seam_mis: readResult.seamMis,
    suspect_seams: readResult.suspectSeams,
    fetched_at: new Date().toISOString(),
  }
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
        // 参数前置校验（connect Chrome 之前 fail-fast，types.ts 契约）：candidate_name 必传
        // 缺参在此即报 INVALID_ARGUMENT——绝不让「读完 ~30 秒简历才发现缺姓名」浪费一次真实鼠标滚动
        () => {
          if (!nameParam) {
            return '未传 candidate_name：请显式传 candidate_name 参数（智能体会话上下文已知的候选人姓名）后重试（姓名唯一来源=非截图识别，缺省不读取猜测）'
          }
          if (rawSaveTo !== undefined && !saveImageTo) return 'save_image_to（拼接图保存路径）不能为空'
          return null
        },
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
            sameView: (a, b) => sameViewAt(a, b, rect),
            stitch: stitchParts,
            signal: ctx.signal,
            onProgress: (stage, info) => {
              if (stage === 'stitch') {
                ctx.progress({ stage: 'execute', message: `拼接 ${info.segments} 段截图（云端识别准备）` })
              }
            },
          })
          ctx.progress({ stage: 'execute', message: '回顶并分段截图（借用真实鼠标，请勿移动）' })
          const result = await reader.readResume({ saveImageTo: saveImageTo || undefined })
          // 候选人姓名：唯一来源 = 显式入参（validate 已在读取前校验必传，此处恒非空）；
          // 张冠李戴防护交叉校验在云端做（resume_name_matches 对云端识别文本，容忍 1 字误差）
          const candidateName = nameParam
          const truncateWarn = result.bottomReached
            ? ''
            : `；⚠️ 已达分段上限 ${MAX_SEGMENTS} 段仍未确认到底，简历内容可能被截断，请人工核对`
          // P1 接缝质量警告：错位接缝（图像层 mis 超阈值）非空时提示人工核对——
          // 拼接图该处可能有重复/缺失（真机 2026-09-02 黄钰鑫份曾中段重复/错乱）
          const seamWarn =
            result.suspectSeams.length > 0
              ? `；⚠️ 可疑拼接接缝（错位：${result.suspectSeams.join('、')}）：拼接图内容可能有重复/缺失，请人工核对拼接图`
              : ''
          return {
            message:
              `完成：读取「${candidateName}」简历拼接图（${result.segments} 段，${result.width}x${result.height}，云端识别后入库）` +
              truncateWarn +
              seamWarn,
            // 云端入库契约 payload（handoff §2）：candidate_name 必填，name_source='param'（姓名=显式入参），
            // 其余可选；额外元信息云端按需忽略
            data: buildResumePayload(candidateName, jobBox.name, result, 'param'),
            effect: 'none',
          }
        },
      )
    },
  }
}
