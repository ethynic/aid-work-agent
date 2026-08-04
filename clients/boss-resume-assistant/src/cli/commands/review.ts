/**
 * CLI 子命令 review：人工复核队列 + 静态 HTML 报告 + 改判。
 * - review [--all] [--open]：列出 UNCERTAIN 队列并生成 HTML 报告（--open 用系统默认浏览器打开）
 * - review override <评估编号> <QUALIFIED|REJECTED> --reason "..."：改判（原评估行不可变）
 */
import { spawn } from 'node:child_process'
import { getClient } from '../../../db/client.js'
import { ReviewStore, type ReviewItem } from '../../main/storage/reviewStore.js'
import type { Conclusion } from '../../main/screening/ScreeningEngine.js'
import { generateReviewReport } from '../reviewReport.js'
import * as rt from '../cliRuntime.js'

/** 复核队列 → 终端列表行（编号即 evaluations.id，override 改判时用） */
export function renderQueueLines(items: ReviewItem[]): string[] {
  if (items.length === 0) return ['复核队列为空：没有待处理的 UNCERTAIN 项。']
  return items.map((it) => {
    const name = it.candidateName ?? `候选人#${it.candidateId ?? '?'}`
    const reason = (it.reason ?? '').slice(0, 60)
    const overridden = it.override ? `｜已改判→${it.override.overrideConclusion}` : ''
    return `#${it.evaluationId}\t${name}\t${it.conclusion}\t${reason}${overridden}`
  })
}

/** 用系统默认浏览器打开本地文件（Windows start / macOS open / Linux xdg-open） */
function openInBrowser(filePath: string): void {
  const opener =
    process.platform === 'win32'
      ? { cmd: 'cmd', args: ['/c', 'start', '""', filePath] }
      : process.platform === 'darwin'
        ? { cmd: 'open', args: [filePath] }
        : { cmd: 'xdg-open', args: [filePath] }
  const child = spawn(opener.cmd, opener.args, { detached: true, stdio: 'ignore' })
  child.unref()
}

export function reviewListCommand(opts: { dataDir: string; exportsDir: string; all: boolean; open: boolean }): number {
  rt.openCliDb({ dataDir: opts.dataDir })
  try {
    const db = getClient()
    const store = new ReviewStore(db)
    const items = store.list({ includeResolved: opts.all })
    console.log(`复核队列（${items.length} 条${opts.all ? '，含已改判' : ''}）：`)
    for (const line of renderQueueLines(items)) console.log(line)

    const report = generateReviewReport(db, { outDir: opts.exportsDir, includeResolved: opts.all })
    console.log(`复核报告已生成（${report.count} 条）：${report.reportPath}`)
    if (opts.open) openInBrowser(report.reportPath)
    return 0
  } finally {
    rt.closeQuietly()
  }
}

export function reviewOverrideCommand(opts: {
  dataDir: string
  evaluationId: number
  conclusion: Conclusion
  reason: string
}): number {
  rt.openCliDb({ dataDir: opts.dataDir })
  try {
    const store = new ReviewStore(getClient())
    // ReviewStore.override 自身 fail-loud：评估不存在 / 结论非法 / 理由为空
    store.override({ evaluationId: opts.evaluationId, conclusion: opts.conclusion, reason: opts.reason })
    console.log(`改判成功：#${opts.evaluationId} → ${opts.conclusion}（${opts.reason}）`)
    return 0
  } finally {
    rt.closeQuietly()
  }
}
