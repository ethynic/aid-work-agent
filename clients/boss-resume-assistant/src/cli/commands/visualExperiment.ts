import { createBossScreenshotOperation } from '../../main/operations/bossScreenshot.js'
import { visualDiagnose, VisualDiagnoseError, type VisualDiagnoseOptions } from '../../main/boss/visualDiagnoseClient.js'
import { runCliOperation } from '../render.js'
import { captureWindow, clickWindow } from '../../main/input/windowExperiment.js'
import { createPageClickExperiment, type PageClickExperimentArgs } from '../../main/operations/bossPageClickExperiment.js'

export async function pageClickCommand(opts: PageClickExperimentArgs & { cdpPort?: number; json?: boolean }): Promise<number> {
  const operation = createPageClickExperiment()
  const ac = new AbortController()
  const cancel = () => ac.abort()
  process.on('SIGINT', cancel)
  try {
    const result = await operation.execute(opts, { cdpPort: opts.cdpPort, signal: ac.signal, progress: () => {} })
    console.log(JSON.stringify(result, null, opts.json ? undefined : 2))
    return result.success ? 0 : result.code === 'INVALID_ARGUMENT' ? 2 : 1
  } finally { process.removeListener('SIGINT', cancel) }
}

export async function screenshotCommand(opts: { output: string; snapshotOutput?: string; scope?: 'page' | 'window'; cdpPort?: number; json?: boolean }): Promise<number> {
  if (opts.scope === 'window') {
    try {
      const data = await captureWindow(opts.output, opts.cdpPort)
      console.log(JSON.stringify({ success: true, data }, null, opts.json ? undefined : 2))
      return 0
    } catch (error) {
      console.error(`window screenshot: ${error instanceof Error ? error.message : '采图失败'}`)
      return 1
    }
  }
  const operation = createBossScreenshotOperation()
  if (!opts.json) return runCliOperation(operation, { output: opts.output, snapshot_output: opts.snapshotOutput }, {
    cdpPort: opts.cdpPort, onSuccess: (result) => console.log(JSON.stringify(result.data, null, 2)),
  })
  const ac = new AbortController()
  const cancel = () => ac.abort()
  process.on('SIGINT', cancel)
  try {
    const result = await operation.execute({ output: opts.output, snapshot_output: opts.snapshotOutput }, { cdpPort: opts.cdpPort, signal: ac.signal, progress: () => {} })
    console.log(JSON.stringify(result))
    return result.success ? 0 : result.code === 'INVALID_ARGUMENT' ? 2 : 1
  } finally { process.removeListener('SIGINT', cancel) }
}

export async function windowClickCommand(opts: { observation: string; x: number; y: number; maxAgeSeconds?: number; json?: boolean }): Promise<number> {
  console.error('window-click 将借用真实鼠标执行一次点击，请勿移动鼠标。')
  try {
    const data = await clickWindow(opts.observation, opts.x, opts.y, opts.maxAgeSeconds)
    console.log(JSON.stringify({ success: true, data }, null, opts.json ? undefined : 2))
    return 0
  } catch (error) {
    console.error(`window-click: ${error instanceof Error ? error.message : '点击未确认'}`)
    return 1
  }
}

export async function visualDiagnoseCommand(opts: VisualDiagnoseOptions & { json?: boolean }): Promise<number> {
  try {
    const result = await visualDiagnose(opts)
    // 保留后台诊断及收费回执原结构；HTTP 状态单独写 stderr，不污染 JSON stdout。
    console.error(`visual-diagnose HTTP ${result.status} request_id=${opts.requestId}`)
    console.log(JSON.stringify(result.body, null, opts.json ? undefined : 2))
    return result.status >= 200 && result.status < 300 ? 0 : 1
  } catch (err) {
    const message = err instanceof VisualDiagnoseError ? err.message : '无法读取实验 PNG 文件；未取得后台回执'
    console.error(`visual-diagnose: ${message}; request_id=${opts.requestId}`)
    return 1
  }
}
