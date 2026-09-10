/**
 * boss_resume_detail operation（设计 §10.8）：读取当前打开的候选人在线简历详情并组装入库契约 payload。
 *
 * 简历详情是 canvas 像素（WASM fillText），DOM 抓不到文字 → 链路：
 * Win32 滚轮回顶 → 分段整页截图（字节差 + cv-segdiff.ps1 像素确认 + 连续两次相同才判到底）→
 * 重叠对齐拼接 + 逐段落盘 crop（scripts/cv-stitch.ps1）→ 逐段批量 OCR（P2 起 RapidOCR 主引擎
 * scripts/cv-ocr-rapid.py，机器上不可用/整批失败自动回退 WinRT scripts/cv-ocr.ps1，见 ocrBatch）
 * + 空格清理（cleanOcrText）+ 归一化重叠去重合并（mergeSegmentTexts）。
 * 只读（effect=none），但滚动借用真实鼠标约 1-2 秒，执行期间用户手不能碰鼠标。
 *
 * 输出契约（docs/plans/recruiting/resume-detail-cli-integration-handoff.md §2，与云端
 * recruiting_resume_service.create_resume_record_from_tool_result 对齐）：
 * data = { candidate_name（必填）, name_source:'param'（云端观测字段：标注姓名来源非 OCR，
 * job_name?, ocr_text, images:[{name,mime_type,base64}], …元信息 }。
 * 云端 BossResumeDetailTool 拿本 payload 直接入库，返回 LLM 的只有紧凑摘要（图片字节绝不进上下文）。
 *
 * candidate_name 必传（智能体会话上下文的候选人姓名）：姓名唯一来源=非 OCR，缺省在 validate 阶段
 * （connect Chrome 之前、读取之前）直接 INVALID_ARGUMENT 报错，绝不用 OCR 猜名入库（错名入库后
 * 打招呼会打错人）。传入后与 OCR 文本头部交叉校验（ocrNameMatches，容忍 1 字 OCR 误差），
 * 未命中 = 疑似当前打开的简历与所传候选人不符 → 报错不入库。
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
  ocrNameMatches,
  locateResumeCanvas,
  type DeviceRect,
  type OcrEngine,
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
  /** 候选人姓名（必传：智能体会话上下文已知的姓名；缺省直接 INVALID_ARGUMENT 报错，绝不 OCR 猜名入库） */
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
 * cropDir 可选：cv-stitch.ps1 顺带落盘每段裁剪图（crop-00.png..），供逐段 OCR。
 * export：bossResumeBatch operation 复用（同域共用）。
 */
export async function stitchParts(
  parts: string[],
  rect: DeviceRect,
  outFile: string,
  cropDir?: string,
): Promise<{ width: number; height: number; overlaps: number[]; seamMis: number[] }> {
  const args = [
    '-Parts', parts.join(','),
    '-X', String(rect.x),
    '-Y', String(rect.y),
    '-W', String(rect.w),
    '-H', String(rect.h),
    '-Out', outFile,
  ]
  if (cropDir) args.push('-CropDir', cropDir)
  const { stdout } = await runPs(cvScriptPath('cv-stitch.ps1'), args, 180000, 'cv-stitch.ps1', ResumeReadError)
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
 * WinRT OCR（P2 起为兜底引擎）：cv-ocr.ps1 把 UTF-8 文本写 <img>.txt，node 读回。
 * 逐段调用（单张 crop 图），超时 120s（单段内容少、识别更快；某一段超时 fail-loud 由调用方决定重试）。
 * export：bossResumeBatch operation 复用（同域共用）；ocrBatch 的回退路径也走这里 */
export async function ocrImage(imgFile: string): Promise<string> {
  const outFile = `${imgFile}.txt`
  await runPs(cvScriptPath('cv-ocr.ps1'), ['-Img', imgFile, '-Out', outFile], 120000, 'cv-ocr.ps1', ResumeReadError)
  return await fsp.readFile(outFile, 'utf8')
}

// ---------- OCR 引擎选择（P2：RapidOCR 主引擎 + WinRT 兜底，绝不因缺 RapidOCR 而失败） ----------

/**
 * 子进程执行器形状（execFile 包装）。探测 RapidOCR 可用性与跑 cv-ocr-rapid.py 适配器共用，
 * 测试注入 fake runner 免起真进程（本文件全部子进程调用都经它，可完全离线单测）。
 */
export type ExecRunner = (
  file: string,
  args: string[],
  opts: { timeout: number },
) => Promise<{ stdout: string; stderr: string }>

const defaultExecRunner: ExecRunner = (file, args, opts) =>
  new Promise((resolve, reject) => {
    execFile(file, args, { timeout: opts.timeout }, (error, stdout, stderr) => {
      if (error) {
        const code = typeof error.code === 'number' ? error.code : undefined
        reject(new Error(`子进程执行失败(${file} exit=${code ?? 'unknown'}): ${stderr || error.message}`))
      } else {
        resolve({ stdout: String(stdout), stderr: String(stderr) })
      }
    })
  })

/** RapidOCR import 探测超时（ms）：onnxruntime DLL 冷加载最坏十几秒 */
const RAPID_PROBE_TIMEOUT_MS = 15000
/** RapidOCR 批量适配器超时（ms）：MAX_SEGMENTS=16 段上限 × ~5s/段 + 引擎 init */
const RAPID_BATCH_TIMEOUT_MS = 300000
/** doctor 单次推理自检（cv-ocr-rapid.py --bench）超时：引擎 init ~0.2s + 推理秒级，60s 已极宽裕 */
export const RAPID_BENCH_TIMEOUT_MS = 60000

/**
 * python 解释器定位（优先级）：env AID_BOSS_RAPIDOCR_PY（python.exe 路径）> 捆绑便携 OCR 环境
 * （包根 ocr-python/python.exe：scripts/build-ocr-python.mjs 产出的可嵌入 Python + 预装全家桶，
 * 与 scripts/ 同款上溯探测，源码/编译/npm 安装布局均命中）> 仓库布局 venv（开发机兜底，
 * npm 安装布局下不存在则跳过）> PATH 上的 python。
 * 注意：开发机只要构建过 ocr-python（npm run build:ocr-python）也会优先走捆绑环境（DirectML）——
 * 这是有意的：开发机上跑的就是客户机要用的发货物，开发/客户两侧行为一致才验证得了。
 * exists 参数仅测试注入（离线断言候选顺序，不起子进程）；生产缺省用真实 fs.existsSync。
 */
export function rapidPythonCandidates(exists: (p: string) => boolean = fs.existsSync): string[] {
  const candidates: string[] = []
  const envPy = (process.env.AID_BOSS_RAPIDOCR_PY ?? '').trim()
  if (envPy) candidates.push(envPy)
  try {
    const here = path.dirname(fileURLToPath(import.meta.url))
    for (const up of ['..\\..\\..\\..', '..\\..\\..']) {
      const bundledPy = path.resolve(here, up, 'ocr-python', 'python.exe')
      if (exists(bundledPy)) {
        candidates.push(bundledPy)
        break
      }
    }
    for (const up of ['..\\..\\..\\..', '..\\..\\..']) {
      const venvPy = path.resolve(here, up, '..', '..', 'venv', 'Scripts', 'python.exe')
      if (exists(venvPy)) {
        candidates.push(venvPy)
        break
      }
    }
  } catch {
    // 布局探测异常（理论不可达）：跳过，落到 PATH python
  }
  candidates.push('python')
  return candidates
}

/** RapidOCR 可用性探测：逐候选跑 `<py> -c "import rapidocr_onnxruntime, PIL"`（真机 ~1s） */
async function probeRapidOcr(
  runner: ExecRunner,
): Promise<{ available: boolean; python?: string; reason?: string }> {
  const failures: string[] = []
  for (const py of rapidPythonCandidates()) {
    try {
      await runner(py, ['-c', 'import rapidocr_onnxruntime, PIL'], { timeout: RAPID_PROBE_TIMEOUT_MS })
      return { available: true, python: py }
    } catch (err) {
      failures.push(`${py}: ${err instanceof Error ? err.message : String(err)}`)
    }
  }
  return { available: false, reason: `RapidOCR 未安装/未配置（${failures.join('；')}）` }
}

/**
 * 解析适配器 stdout 的加速后端（dml/cpu）：取**最后一个** engine= 行——DML 推理中途失败时
 * 适配器整批回退 CPU 重跑，会先后打出 engine=dml、engine=cpu 两行，最终产出文本的是最后一行
 * 标注的引擎；按首行/任意行 grep 会把回退后的 CPU 结果误报成 dml。缺行宽容为 cpu（旧版适配器兼容）。
 */
function parseAccel(stdout: string): 'dml' | 'cpu' {
  const lines = [...stdout.matchAll(/engine=(dml|cpu)/g)]
  return (lines[lines.length - 1]?.[1] ?? 'cpu') as 'dml' | 'cpu'
}

/**
 * RapidOCR 单次推理实测（doctor 装机验收用）：跑 `<py> cv-ocr-rapid.py --bench`
 * （适配器内合成中文图推理计时，模型加载不计入），返回秒数与加速后端
 * （dml=DirectML GPU / cpu=CPU，适配器 stdout 的 engine= 行）。失败抛错（调用方决定是否致命——
 * doctor 场景探测已通过、bench 失败只降级为不展示耗时，不算装机失败）。
 */
export async function benchRapidOcr(
  python: string,
  runner: ExecRunner = defaultExecRunner,
): Promise<{ seconds: number; accel: 'dml' | 'cpu' }> {
  const { stdout } = await runner(python, [cvScriptPath('cv-ocr-rapid.py'), '--bench'], {
    timeout: RAPID_BENCH_TIMEOUT_MS,
  })
  const m = /bench=([\d.]+)s/.exec(stdout)
  if (!m) {
    throw new Error(`cv-ocr-rapid.py --bench 输出缺少 bench=<秒> 行：${stdout.trim()}`)
  }
  return { seconds: Number(m[1]), accel: parseAccel(stdout) }
}

/** 引擎决策结果（engine + 人类可读原因，doctor 展示与 fail-loud 报错共用文案） */
export interface OcrEnginePlan {
  engine: OcrEngine
  /** engine='rapid' 时实际使用的 python 解释器 */
  python?: string
  /** env 显式强制（AID_BOSS_OCR_ENGINE=rapid/winrt）：rapid 强制下运行期批量失败也 fail-loud，
   *  不静默回退（见 ocrBatch；auto 才有「绝不因 RapidOCR 失败而失败」的回退语义） */
  forced?: boolean
  reason: string
}

let ocrEnginePlanCache: OcrEnginePlan | null = null

/** 引擎决策日志是否已落（每进程一条；ocrBatch 每份简历都会走 resolveOcrEngine，不能逐次刷屏） */
let ocrEnginePlanLogged = false

/**
 * 引擎决策落一次日志（stderr，经 providerManager 转发落 runtime.log）：auto 模式下 RapidOCR
 * 缺失时此前静默回退 WinRT，客户机日志零痕迹（2026-09-10 排障事故整改——低质量 winrt 文本
 * 拖垮姓名交叉校验时，日志里必须能看到引擎实际是什么）。
 */
function logOcrEnginePlanOnce(plan: OcrEnginePlan): OcrEnginePlan {
  if (!ocrEnginePlanLogged) {
    ocrEnginePlanLogged = true
    // reason 截断 200：winrt 回退时含逐候选探测失败明细，可达数百字符（与 logOpFailure 同策略）
    const reason = plan.reason.length > 200 ? `${plan.reason.slice(0, 200)}…` : plan.reason
    process.stderr.write(`[boss-ocr] OCR 引擎=${plan.engine}${plan.python ? `（python: ${plan.python}）` : ''}：${reason}\n`)
  }
  return plan
}

/** 重置引擎解析缓存（仅测试用：注入 fake runner 前重置，避免跨用例/跨文件污染模块级缓存） */
export function resetOcrEngineCacheForTest(): void {
  ocrEnginePlanCache = null
  ocrEnginePlanLogged = false
}

/**
 * OCR 引擎解析（模块级缓存一次，进程内不重复探测）：
 * - env AID_BOSS_OCR_ENGINE=winrt → 强制 WinRT（不探测）；
 * - env AID_BOSS_OCR_ENGINE=rapid → 强制 Rapid，探测不可用直接 fail-loud（报部署要求；
 *   运行期批量失败同样 fail-loud，见 ocrBatch）；
 * - 缺省/其他值 auto → 探测 RapidOCR，可用用之，不可用静默回退 WinRT（零依赖可用性）。
 */
export async function resolveOcrEngine(runner: ExecRunner = defaultExecRunner): Promise<OcrEnginePlan> {
  if (ocrEnginePlanCache) return ocrEnginePlanCache
  const forced = (process.env.AID_BOSS_OCR_ENGINE ?? 'auto').trim().toLowerCase()
  if (forced === 'winrt') {
    ocrEnginePlanCache = {
      engine: 'winrt',
      forced: true,
      reason: 'AID_BOSS_OCR_ENGINE=winrt 强制使用系统 WinRT OCR',
    }
    return logOcrEnginePlanOnce(ocrEnginePlanCache)
  }
  const probe = await probeRapidOcr(runner)
  if (forced === 'rapid') {
    if (!probe.available) {
      throw new ResumeReadError(
        `AID_BOSS_OCR_ENGINE=rapid 强制 RapidOCR 但机器上不可用：${probe.reason ?? ''}。` +
          '正常发行包已内置 OCR 环境（包根 ocr-python/，含 Python 与全部依赖，无需安装）；' +
          '若缺失：开发机执行 npm run build:ocr-python 构建，或 pip install rapidocr-onnxruntime==1.4.4 Pillow，' +
          '或设 AID_BOSS_RAPIDOCR_PY=<python.exe 路径> 指定已装解释器，或改 AID_BOSS_OCR_ENGINE=auto 回退 WinRT',
      )
    }
    ocrEnginePlanCache = {
      engine: 'rapid',
      python: probe.python,
      forced: true,
      reason: `AID_BOSS_OCR_ENGINE=rapid 强制 RapidOCR（python: ${probe.python}）`,
    }
    return logOcrEnginePlanOnce(ocrEnginePlanCache)
  }
  ocrEnginePlanCache = probe.available
    ? { engine: 'rapid', python: probe.python, reason: `RapidOCR 可用（python: ${probe.python}）` }
    : {
        engine: 'winrt',
        reason: `RapidOCR 不可用，回退系统 WinRT OCR（${probe.reason ?? '未知原因'}；` +
          '可选安装提升识别精度：pip install rapidocr-onnxruntime==1.4.4 Pillow）',
      }
  return logOcrEnginePlanOnce(ocrEnginePlanCache)
}

/** ocrBatch 可注入依赖（测试用；生产缺省走真实子进程 + ocrImage） */
export interface OcrBatchDeps {
  /** 子进程 runner（探测 + cv-ocr-rapid.py 适配器调用） */
  runner?: ExecRunner
  /** WinRT 单图 OCR 回退实现（默认 ocrImage） */
  winrt?: (imgFile: string) => Promise<string>
}

/**
 * 批量 OCR（P2）：全部段一次调用。rapid 路径 = 一次 python 子进程跑 cv-ocr-rapid.py
 * （模型 session 只加载一次，~4.2s/段），读回各 <img>.rapid.txt；进程失败或任一结果文件缺失：
 * - auto（缺省）：整批回退 WinRT（逐文件 ocrImage，120s/个），回退前 stderr 打一行原因
 *   （不静默）——绝不因 RapidOCR 失败而失败；
 * - AID_BOSS_OCR_ENGINE=rapid 强制：fail-loud 抛 ResumeReadError（与探测不可用的 fail-loud 对称：
 *   「强制」= 必须用 rapid，静默降级 WinRT 会产出用户明确要避免的低质量文本，且连带拖垮姓名
 *   交叉校验，比报错更糟；要允许回退请显式用 auto）。
 * 返回 engine 标识实际使用的引擎（ResumeReadResult.ocrEngine 元信息 → payload ocr_engine）。
 * export：bossResumeBatch operation 复用；ResumeReader 的 deps.ocrBatch 真实实现。
 */
export async function ocrBatch(
  files: string[],
  deps: OcrBatchDeps = {},
): Promise<{ texts: string[]; engine: OcrEngine; accel: 'dml' | 'cpu' | 'none' }> {
  const runner = deps.runner ?? defaultExecRunner
  const winrt = deps.winrt ?? ocrImage
  const plan = await resolveOcrEngine(runner)
  if (files.length === 0) return { texts: [], engine: plan.engine, accel: 'none' } // 空批不起子进程（调用方保证 ≥1 段）
  if (plan.engine === 'rapid') {
    let rapidTexts: string[] | null = null
    let accel: 'dml' | 'cpu' = 'cpu'
    let rapidErr: unknown = null
    try {
      const { stdout } = await runner(plan.python!, [cvScriptPath('cv-ocr-rapid.py'), ...files], {
        timeout: RAPID_BATCH_TIMEOUT_MS,
      })
      // 适配器 stdout 的 engine=dml|cpu 行（P2 提速：装了 onnxruntime-directml 自动走 DirectML；
      // DML 失败整批回退 CPU 时有两行，取最后一行=最终引擎，见 parseAccel）
      accel = parseAccel(stdout)
      rapidTexts = []
      for (const file of files) {
        rapidTexts.push(await fsp.readFile(`${file}.rapid.txt`, 'utf8'))
      }
    } catch (err) {
      rapidErr = err
      rapidTexts = null // 进程失败/结果文件缺失：整批回退（适配器任何异常退出码均非 0）
    }
    if (rapidTexts) return { texts: rapidTexts, engine: 'rapid', accel }
    const reason = rapidErr instanceof Error ? rapidErr.message : String(rapidErr)
    if (plan.forced) {
      throw new ResumeReadError(
        `AID_BOSS_OCR_ENGINE=rapid 强制 RapidOCR 但批量执行失败（${reason}）：` +
          '请排查 python/RapidOCR 环境后重试，或改 AID_BOSS_OCR_ENGINE=auto 允许失败回退 WinRT',
      )
    }
    // auto：保「绝不因 RapidOCR 失败而失败」的可用性，但不再完全静默——stderr 留一行原因供排障
    process.stderr.write(`[boss-ocr] RapidOCR 批量执行失败，整批回退 WinRT：${reason}\n`)
  }
  const texts: string[] = []
  for (const file of files) {
    texts.push(await winrt(file))
  }
  return { texts, engine: 'winrt', accel: 'none' }
}

/**
 * 组装云端简历库契约 payload（handoff §2，与 recruiting_resume_service.create_resume_record_from_tool_result
 * 对齐）：candidate_name 必填，name_source 标注姓名来源（'param'=显式入参 / 'dom'=卡片
 * DOM 配对；云端观测不拦截，向后兼容旧客户端），job_name 可选，
 * ocr_text + images base64 + 元信息。boss_resume_detail / boss_resume_batch 两个 operation 共用（单份与批量同契约）。
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
    ocr_text: readResult.text,
    images: [
      {
        name: 'resume_full.png',
        mime_type: 'image/png',
        base64: readResult.imageBuffer.toString('base64'),
      },
    ],
    // 元信息（云端契约不读取，供 CLI 展示/调试）
    ocr_chars: readResult.chars,
    ocr_engine: readResult.ocrEngine, // P2：实际使用的 OCR 引擎（rapid 主 / winrt 兜底）
    ocr_accel: readResult.ocrAccel, // P2 提速：rapid 的加速后端（dml=DirectML GPU / cpu / none=非 rapid）
    segments: readResult.segments,
    bottom_reached: readResult.bottomReached,
    image_width: readResult.width,
    image_height: readResult.height,
    // P1 拼接加固元信息（云端按需忽略）：接缝错配率与可疑/未对上接缝（1-based 序号）、空 OCR 段数
    seam_mis: readResult.seamMis,
    suspect_seams: readResult.suspectSeams,
    text_seam_unmatched: readResult.textSeamUnmatched,
    ocr_empty_segments: readResult.ocrEmptySegments,
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
            return '未传 candidate_name：请显式传 candidate_name 参数（智能体会话上下文已知的候选人姓名）后重试（姓名唯一来源=非 OCR，缺省不读取猜测）'
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
            ocrBatch,
            signal: ctx.signal,
            onProgress: (stage, info) => {
              if (stage === 'stitch') {
                ctx.progress({ stage: 'execute', message: `拼接 ${info.segments} 段截图` })
              } else {
                ctx.progress({ stage: 'execute', message: `逐段 OCR 识别（${info.segments} 段）` })
              }
            },
          })
          ctx.progress({ stage: 'execute', message: '回顶并分段截图（借用真实鼠标，请勿移动）' })
          const result = await reader.readResume({ saveImageTo: saveImageTo || undefined })
          // 候选人姓名：唯一来源 = 显式入参（validate 已在读取前校验必传，此处恒非空）；
          // 张冠李戴防护交叉校验：所传姓名必须在 OCR 文本头部模糊命中（容忍 1 字 OCR 误差）；
          // 未命中 = 疑似当前打开的简历与所传候选人不符 → fail-loud 绝不入库
          if (!ocrNameMatches(nameParam, result.text)) {
            throw new CodedOperationError(
              'INVALID_ARGUMENT',
              `姓名交叉校验未通过（所传 candidate_name「${nameParam}」未在简历 OCR 文本头部命中）：疑似当前打开的简历与所传候选人不符，请人工核对后重试`,
            )
          }
          const candidateName = nameParam
          const truncateWarn = result.bottomReached
            ? ''
            : `；⚠️ 已达分段上限 ${MAX_SEGMENTS} 段仍未确认到底，简历内容可能被截断，请人工核对`
          // P1 接缝质量警告：错位接缝（图像层 mis 超阈值）或文本接缝未对上（合并层无重叠命中）
          // 非空时提示人工核对——OCR 文本可能有重复/缺失（真机 2026-09-02 黄钰鑫份曾中段重复/错乱）
          const seamWarn =
            result.suspectSeams.length > 0 || result.textSeamUnmatched.length > 0
              ? `；⚠️ 可疑拼接接缝（错位：${result.suspectSeams.join('、') || '无'}；文本未对上：${
                  result.textSeamUnmatched.join('、') || '无'
                }）：OCR 文本可能有重复/缺失，请人工核对拼接图`
              : ''
          return {
            message:
              `完成：读取「${candidateName}」简历全文 ${result.chars} 字（${result.segments} 段拼接，${result.width}x${result.height}）` +
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
