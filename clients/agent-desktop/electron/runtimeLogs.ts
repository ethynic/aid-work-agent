import { createHash } from 'node:crypto'
import { createWriteStream, readFileSync } from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import yazl from 'yazl'
import type { LogHours, RuntimeDiagnosticFields, RuntimeLogCollectionOptions, RuntimeLogSnapshot } from '@aid/local-tool-host-core/legacy/logDiagnostics'
import type { RuntimeHomeProfile } from '@aid/local-tool-host-core/legacy/diagnostics'
import { readRuntimeDiagnosticHomes, type RuntimeDiagnosticHomes, type RuntimeDiagnosticInstallation } from './runtimeInstallation.js'
import { readRuntimeDiagnosticEvidence, type RuntimeDiagnosticEvidence } from './runtimeDiagnosticEvidence.js'

interface Collector {
  collectRuntimeLogs(input: RuntimeLogCollectionOptions): RuntimeLogSnapshot
  recordDesktopDiagnostic(directory: string, event: string, now?: number, details?: RuntimeDiagnosticFields): boolean
}
export interface RuntimeLogsOptions {
  hostEntry: string | null; desktopDirectory: string; home(): string | null
  appVersion?: string
  appDataPath: string; profile: RuntimeHomeProfile
  installation?: RuntimeDiagnosticInstallation
  chooseSave(defaultPath: string): Promise<string | null>
  loadCollector?: () => Promise<Collector>
}
export function readLogHours(input: unknown): LogHours {
  if (input === undefined) return 24
  if (!input || typeof input !== 'object' || Array.isArray(input) || Object.keys(input).some(key => key !== 'hours')) throw new Error('日志时间范围无效')
  const hours = (input as { hours?: unknown }).hours ?? 24
  if (hours !== 1 && hours !== 24 && hours !== 168) throw new Error('日志时间范围无效')
  return hours
}
export async function writeLogArchive(file: string, snapshot: RuntimeLogSnapshot, evidence?: RuntimeDiagnosticEvidence): Promise<void> {
  const events = snapshot.entries.map(entry => JSON.stringify(entry)).join('\n') + (snapshot.entries.length ? '\n' : '')
  const { entries: _entries, ...coverage } = snapshot
  const manifest = { ...coverage, ...(evidence ? { evidence } : {}), versionScope: 'current-installation; historical event versions were not collected', privacy: 'Only projected diagnostic metadata. No raw log, business content or credentials.', entries: snapshot.entries.length, eventsSha256: createHash('sha256').update(events).digest('hex') }
  const summary = `Runtime 客户端诊断\n生成时间：${snapshot.generatedAt}\n当前安装：客户端 ${snapshot.versions.app ?? '未知'} / Host ${snapshot.versions.host ?? '未知'} / Core ${snapshot.versions.core ?? '未知'}（不推断历史事件版本）\n请求范围：${snapshot.range.from} — ${snapshot.range.to}\n可展示事件：${snapshot.entries.length}\n截断：${snapshot.truncated ? '是' : '否'}\n跳过未能安全投影的日志行：${snapshot.sources.reduce((count, source) => count + source.skippedLines, 0)}\n\n仅包含白名单诊断元数据，不含原始日志、消息正文或凭据。源缺失、读取错误和截断见 manifest.json。\n`
  const archive = new yazl.ZipFile()
  archive.addBuffer(Buffer.from(summary), 'summary.txt')
  archive.addBuffer(Buffer.from(JSON.stringify(manifest, null, 2) + '\n'), 'manifest.json')
  archive.addBuffer(Buffer.from(events), 'events.jsonl')
  await new Promise<void>((resolve, reject) => {
    const output = createWriteStream(file, { flags: 'w' })
    archive.outputStream.on('error', reject); output.on('error', reject); output.on('close', resolve)
    archive.outputStream.pipe(output); archive.end()
  })
}
export class RuntimeLogs {
  private collector: Promise<Collector>
  private logWriteWarning = false
  private versions: RuntimeLogSnapshot['versions']
  constructor(private options: RuntimeLogsOptions) {
    const version = (file: string): string | null => {
      try {
        const value: unknown = JSON.parse(readFileSync(file, 'utf8')).version
        return typeof value === 'string' && /^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/.test(value) ? value : null
      } catch { return null }
    }
    this.versions = { app: options.appVersion ?? null, host: options.hostEntry ? version(path.join(path.dirname(options.hostEntry), 'package.json')) : null, core: options.hostEntry ? version(path.join(path.dirname(options.hostEntry), 'node_modules/@aid/local-tool-host-core/package.json')) : null }
    this.collector = options.loadCollector ? options.loadCollector() : options.hostEntry
      ? import(pathToFileURL(path.join(path.dirname(options.hostEntry), 'node_modules/@aid/local-tool-host-core/dist/legacy/logDiagnostics.js')).href)
      : Promise.reject(new Error('diagnostic-collector-unavailable'))
    // A damaged or older installation cannot prevent the shell from opening.
    void this.collector.catch(() => {})
  }
  async read(input?: unknown): Promise<RuntimeLogSnapshot> {
    return this.snapshot(readLogHours(input), this.homes())
  }
  private homes(): RuntimeDiagnosticHomes | undefined {
    if (this.options.installation) return readRuntimeDiagnosticHomes(this.options.installation)
  }
  private async snapshot(hours: LogHours, homes?: RuntimeDiagnosticHomes): Promise<RuntimeLogSnapshot> {
    const collector = await this.collector.catch(() => null)
    const sources = homes ? { home: homes.active.home, activeStatus: homes.active.status, priorHome: homes.prior.home, priorStatus: homes.prior.status }
      : { home: this.options.home(), appDataPath: this.options.appDataPath, profile: this.options.profile }
    if (collector) return { ...collector.collectRuntimeLogs({ ...sources, desktopDirectory: this.options.desktopDirectory, hours }), versions: this.versions }
    const now = Date.now()
    return { version: 1, generatedAt: new Date(now).toISOString(), hours, versions: this.versions, range: { from: new Date(now - hours * 3600_000).toISOString(), to: new Date(now).toISOString(), observedFrom: null, observedTo: null }, entries: [], sources: [{ name: 'diagnostic-collector', status: 'unreadable', bytesRead: 0, totalBytes: 0, skippedLines: 0, partialLines: 0, truncated: false }], health: null, truncated: false }
  }
  record(event: string, details?: RuntimeDiagnosticFields): void {
    void this.collector.then(collector => {
      if (!collector.recordDesktopDiagnostic(this.options.desktopDirectory, event, undefined, details) && !this.logWriteWarning) {
        this.logWriteWarning = true; console.error('AGENT_DESKTOP:diagnostic-log-unavailable')
      }
    }).catch(() => {})
  }
  async export(input?: unknown): Promise<{ canceled: boolean; fileName?: string }> {
    const hours = readLogHours(input)
    const selected = await this.options.chooseSave(`Runtime-诊断-${new Date().toISOString().slice(0, 10)}.zip`)
    if (!selected) return { canceled: true }
    const homes = this.homes(), snapshot = await this.snapshot(hours, homes)
    const evidence = homes && this.options.installation ? await readRuntimeDiagnosticEvidence(this.options.installation, homes,
      Date.parse(snapshot.range.from), Date.parse(snapshot.range.to)) : undefined
    await writeLogArchive(selected, snapshot, evidence)
    return { canceled: false, fileName: path.basename(selected) }
  }
}
