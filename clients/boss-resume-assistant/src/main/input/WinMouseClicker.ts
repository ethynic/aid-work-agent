/**
 * Win32 真实鼠标点击通道（设计文档 §10.3 通道 2）。
 * 包装 scripts/win-click.ps1：page 坐标（DOMSnapshot device px）+ 页面截图尺寸 →
 * 屏幕坐标实时校准（GetWindowRect）→ WindowFromPoint 落点守卫 → 拟人移动点击。
 * ps1 守卫失败（落点被遮挡）exit 2，本类转为 WinClickError fail-loud，绝不盲点。
 *
 * 用途：BOSS 反作弊 SDK 选择性拦截 CDP 合成点击的控件（筛选按钮/面板选项等，§16 决策 8）。
 * 注意：借用真实光标，点击期间（约 1s）用户手不能碰鼠标，CLI 调用前必须提示。
 */
import { execFile } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { ClickPoint } from '../boss/domSnapshot.js'

export class WinClickError extends Error {
  constructor(
    message: string,
    readonly exitCode?: number,
  ) {
    super(message)
    this.name = 'WinClickError'
  }
}

type ExecFileLike = (
  file: string,
  args: string[],
  opts: { timeout: number },
) => Promise<{ stdout: string; stderr: string }>

/** 默认实现：promisify 手动包（execFile 的 promisify 类型在 node:test 注入时不便） */
const defaultExecFile: ExecFileLike = (file, args, opts) =>
  new Promise((resolve, reject) => {
    execFile(file, args, opts, (error, stdout, stderr) => {
      if (error) {
        const code = typeof error.code === 'number' ? error.code : undefined
        const err = new WinClickError(`win-click.ps1 执行失败(exit=${code ?? 'unknown'}): ${stderr || error.message}`, code)
        reject(err)
      } else {
        resolve({ stdout: String(stdout), stderr: String(stderr) })
      }
    })
  })

/**
 * scripts/win-click.ps1 绝对路径。
 * 源码（src/main/input）向上 3 级、编译后（dist/src/main/input）向上 4 级是工程根，
 * 两个候选按存在性取一，都找不到 fail-loud。
 */
function defaultScriptPath(): string {
  const here = path.dirname(fileURLToPath(import.meta.url))
  for (const up of ['..\\..\\..\\..', '..\\..\\..']) {
    const candidate = path.resolve(here, up, 'scripts', 'win-click.ps1')
    if (fs.existsSync(candidate)) return candidate
  }
  throw new WinClickError(`未找到 scripts/win-click.ps1（已从 ${here} 向上探测）`)
}

export class WinMouseClicker {
  private readonly scriptPath: string
  private readonly execFileImpl: ExecFileLike

  constructor(opts: { scriptPath?: string; execFileImpl?: ExecFileLike } = {}) {
    this.scriptPath = opts.scriptPath ?? defaultScriptPath()
    this.execFileImpl = opts.execFileImpl ?? defaultExecFile
  }

  /**
   * 真实鼠标点击 page 坐标。viewport 为页面截图尺寸（device px），用于 DPI 缩放换算。
   * 失败抛 WinClickError（含 ps1 exit code：2=落点被遮挡守卫拒绝）。
   */
  async click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void> {
    const { x, y } = point
    this.assertClickable(point, viewport)
    await this.execFileImpl(
      'powershell.exe',
      [
        '-NoProfile',
        '-ExecutionPolicy',
        'Bypass',
        '-File',
        this.scriptPath,
        '-X',
        String(Math.round(x)),
        '-Y',
        String(Math.round(y)),
        '-CssW',
        String(viewport.width),
        '-CssH',
        String(viewport.height),
      ],
      { timeout: 20000 },
    )
  }

  /**
   * Win32 原子「点击聚焦 + 真实键盘逐字输入」：同一次 ps1 调用内先拟人点击目标点，
   * 紧接着 SendInput KEYEVENTF_UNICODE 逐字键入 text（200ms/字）——拆成两次调用会在
   * 间隙被抢焦点。2026-08-26 决策：点击/输入类第一优先 Win32 真实事件（防爬）。
   */
  async clickAndType(
    point: ClickPoint,
    viewport: { width: number; height: number },
    text: string,
  ): Promise<void> {
    this.assertClickable(point, viewport)
    if (typeof text !== 'string' || text.length === 0) {
      throw new WinClickError('clickAndType 输入文本不能为空')
    }
    await this.execFileImpl(
      'powershell.exe',
      [
        '-NoProfile',
        '-ExecutionPolicy',
        'Bypass',
        '-File',
        this.scriptPath,
        '-X',
        String(Math.round(point.x)),
        '-Y',
        String(Math.round(point.y)),
        '-CssW',
        String(viewport.width),
        '-CssH',
        String(viewport.height),
        '-Text',
        text,
      ],
      // 点击 ~5s + 聚焦 0.5s + 每字 0.2s，留足余量防长消息误超时
      { timeout: 30000 + text.length * 500 },
    )
  }

  /** 坐标/视口合法性校验（click 与 clickAndType 共用），非法即抛 WinClickError，绝不盲点 */
  private assertClickable(point: ClickPoint, viewport: { width: number; height: number }): void {
    const { x, y } = point
    if (![x, y, viewport.width, viewport.height].every(Number.isFinite) || viewport.width <= 0 || viewport.height <= 0) {
      throw new WinClickError(`非法点击参数: point=(${x},${y}) viewport=${viewport.width}x${viewport.height}`)
    }
    if (x < 0 || y < 0 || x > viewport.width || y > viewport.height) {
      throw new WinClickError(`点击坐标 (${x},${y}) 超出视口 ${viewport.width}x${viewport.height}，拒绝盲点`)
    }
  }
}
