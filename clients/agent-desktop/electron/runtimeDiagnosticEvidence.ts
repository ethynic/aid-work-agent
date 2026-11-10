import { execFile } from 'node:child_process'
import { lstatSync, readFileSync, realpathSync } from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { readLocalRuntimeIdentity, type RuntimeDiagnosticHomes, type RuntimeDiagnosticInstallation, type RuntimeEvidenceStatus } from './runtimeInstallation.js'

const providers = {
  'Microsoft-Windows-Kernel-Power': { 41: 'unexpected-restart', 42: 'sleep-entered', 107: 'sleep-resumed' },
  'Microsoft-Windows-Kernel-Boot': { 27: 'kernel-boot' },
  'Microsoft-Windows-Kernel-General': { 12: 'os-started', 13: 'os-shutdown' },
  'Microsoft-Windows-Power-Troubleshooter': { 1: 'power-resumed' },
  EventLog: { 6005: 'event-log-started', 6006: 'event-log-stopped', 6008: 'unexpected-shutdown' },
  User32: { 1074: 'shutdown-requested' },
} as const
const powerKinds = new Set<string>(Object.values(providers).flatMap(items => Object.values(items)))
const MAX_POWER_EVENTS = 64
export interface PowerEvidence {
  status: 'read' | 'partial' | 'unavailable' | 'unsupported'
  truncated: boolean
  events: { kind: string; at: string; bootType?: 0 | 1 | 2 }[]
}
export function runtimePowerScript(from: number, to: number): string {
  const queries = Object.entries(providers).map(([name, ids]) => {
    const map = Object.entries(ids).map(([id, kind]) => `${id}='${kind}'`).join(';')
    return `Read-PowerEvents '${name}' @{${map}}`
  }).join('\n')
  return `
$ProgressPreference = 'SilentlyContinue'
$start = [DateTimeOffset]::Parse('${new Date(from).toISOString()}').LocalDateTime
$end = [DateTimeOffset]::Parse('${new Date(to).toISOString()}').LocalDateTime
$script:failures = 0
function Read-PowerEvents([string]$provider, [hashtable]$kinds) {
  try {
    $found = @(Get-WinEvent -FilterHashtable @{ LogName='System'; ProviderName=$provider; Id=@($kinds.Keys); StartTime=$start; EndTime=$end } -MaxEvents ${MAX_POWER_EVENTS + 1} -ErrorAction Stop)
    foreach ($event in $found) {
      if ($event.ProviderName -eq $provider -and $kinds.ContainsKey([int]$event.Id) -and $event.TimeCreated) {
        $result = @{ kind=$kinds[[int]$event.Id]; at=$event.TimeCreated.ToUniversalTime().ToString('o') }
        if ($result.kind -eq 'kernel-boot') {
          try {
            $data = ([xml]$event.ToXml()).Event.EventData.Data | Where-Object { $_.Name -eq 'BootType' } | Select-Object -First 1
            if ($data.'#text' -match '^(?:0x)?0*([012])$') { $result.bootType = [int]$Matches[1] }
          } catch { }
        }
        $result
      }
    }
  } catch {
    if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') { $script:failures++ }
  }
}
$events = @(
${queries}
) | Sort-Object { $_.at } -Descending
@{ status=$(if ($script:failures -eq ${Object.keys(providers).length}) { 'unavailable' } elseif ($script:failures) { 'partial' } else { 'read' }); truncated=(@($events).Count -gt ${MAX_POWER_EVENTS}); events=@($events | Select-Object -First ${MAX_POWER_EVENTS}) } | ConvertTo-Json -Compress -Depth 4
`
}
export function parseRuntimePowerEvidence(raw: string, from: number, to: number): PowerEvidence {
  const unavailable = (): PowerEvidence => ({ status: 'unavailable', truncated: false, events: [] })
  try {
    const value = JSON.parse(raw)
    if (!['read', 'partial', 'unavailable'].includes(value?.status) || typeof value.truncated !== 'boolean' || !Array.isArray(value.events)) return unavailable()
    const events: PowerEvidence['events'] = []
    for (const row of value.events.slice(0, MAX_POWER_EVENTS)) {
      if (!row || !powerKinds.has(row.kind) || typeof row.at !== 'string') continue
      const time = Date.parse(row.at)
      if (time >= from && time <= to) events.push({ kind: row.kind, at: new Date(time).toISOString(),
        ...(row.kind === 'kernel-boot' && [0, 1, 2].includes(row.bootType) ? { bootType: row.bootType } : {}) })
    }
    return { status: value.status, truncated: value.truncated || value.events.length > MAX_POWER_EVENTS, events }
  } catch { return unavailable() }
}
export async function readRuntimePowerEvidence(hostEntry: string, from: number, to: number): Promise<PowerEvidence> {
  if (process.platform !== 'win32') return { status: 'unsupported', truncated: false, events: [] }
  try {
    const protection = await import(pathToFileURL(path.join(path.dirname(hostEntry), 'dpapi.js')).href)
    const { executable, cwd, env } = protection.trustedSystemPowerShell()
    const script = Buffer.from(runtimePowerScript(from, to), 'utf16le').toString('base64')
    return await new Promise(resolve => execFile(executable, ['-NoProfile', '-NonInteractive', '-EncodedCommand', script], {
      cwd, windowsHide: true, timeout: 5000, maxBuffer: 32 * 1024,
      env: { SystemRoot: env.SystemRoot, windir: env.windir, Path: env.Path, PSModulePath: env.PSModulePath },
    }, (error, output) => resolve(parseRuntimePowerEvidence(error ? '' : output, from, to))))
  } catch { return { status: 'unavailable', truncated: false, events: [] } }
}

interface FileEvidence { status: RuntimeEvidenceStatus; lastWriteAt: string | null }
function fileEvidence(file: string): FileEvidence {
  try {
    const stat = lstatSync(file)
    const linked = stat.isSymbolicLink() || path.resolve(file).toLowerCase() !== realpathSync(file).toLowerCase()
    return { status: linked ? 'linked' : stat.isFile() && stat.size <= 256 * 1024 ? 'read' : 'unreadable',
      lastWriteAt: linked ? null : stat.mtime.toISOString() }
  } catch (error) {
    return { status: (error as NodeJS.ErrnoException).code === 'ENOENT' ? 'missing' : 'unreadable', lastWriteAt: null }
  }
}
const readJson = (file: string) => JSON.parse(readFileSync(file, 'utf8').replace(/^\uFEFF/, ''))
function markerEvidence(home: string | null, status: RuntimeEvidenceStatus) {
  if (!home) return { status, lastWriteAt: null, schemaVersion: null }
  const file = path.join(home, 'host-running.json'), fileState = fileEvidence(file)
  if (fileState.status !== 'read') return { ...fileState, schemaVersion: null }
  try {
    const marker = readJson(file)
    if (typeof marker?.instance_id !== 'string' || (marker.schemaVersion !== undefined && (marker.schemaVersion !== 2 || typeof marker.supervision !== 'string'))) throw new Error()
    return { ...fileState, schemaVersion: marker.schemaVersion === 2 ? 2 : 1 }
  } catch { return { ...fileState, status: 'unreadable' as const, schemaVersion: null } }
}
function lastStopEvidence(home: string | null, status: RuntimeEvidenceStatus) {
  const empty = { operationStatus: null, reason: null }
  if (!home) return { status, lastWriteAt: null, ...empty }
  const file = path.join(home, 'management-operations.json'), fileState = fileEvidence(file)
  if (fileState.status !== 'read') return { ...fileState, ...empty }
  try {
    const value = readJson(file)
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error()
    const stop = Object.values(value).reverse().map(entry => (entry as { operation?: { operation?: string; status?: string; message?: string } })?.operation)
      .find(op => op?.operation === 'stop')
    if (!stop) return { ...fileState, ...empty }
    if (!['succeeded', 'running', 'failed', 'reconciling'].includes(stop.status ?? '')) throw new Error()
    const reason = stop.message === '旧执行尚未确认停止，请停止原Runtime后重试；不会重发旧任务（代码 11）'
      ? 'previous-execution-not-confirmed-stopped' : stop.message === '执行停止事实尚需核对（代码 11）' ? 'execution-stop-unconfirmed' : null
    return { ...fileState, operationStatus: stop.status!, reason }
  } catch { return { ...fileState, status: 'unreadable' as const, ...empty } }
}
async function registrationEvidence(options: RuntimeDiagnosticInstallation, homes: RuntimeDiagnosticHomes) {
  const empty = { schemaVersion: null, phase: null, protocol: null, referencesActiveHome: null }
  if (!homes.metadataHome) return { status: homes.pointer, lastWriteAt: null, ...empty }
  const state = fileEvidence(path.join(homes.metadataHome, 'clean-registration.bin'))
  if (state.status !== 'read') return { ...state, ...empty }
  try {
    const store = await import(pathToFileURL(path.join(path.dirname(options.hostEntry), 'cleanRegistration.js')).href)
    const r = await store.readCleanRegistration(homes.metadataHome)
    if (!r) return { status: 'missing' as const, lastWriteAt: null, ...empty }
    if ((r.version !== 1 && r.version !== 2) || !['requested', 'prepared', 'confirming', 'paired', 'installed'].includes(r.phase)) throw new Error()
    return { ...state, schemaVersion: r.version, phase: r.phase, protocol: new URL(r.server).protocol === 'https:' ? 'https' : 'http',
      referencesActiveHome: r.home && homes.active.home ? path.resolve(r.home).toLowerCase() === path.resolve(homes.active.home).toLowerCase() : null }
  } catch { return { ...state, status: 'unreadable' as const, ...empty } }
}
export async function readRuntimeDiagnosticEvidence(options: RuntimeDiagnosticInstallation, homes: RuntimeDiagnosticHomes, from: number, to: number) {
  const sampledAt = new Date().toISOString()
  const inspect = async ({ home, status }: RuntimeDiagnosticHomes['active']) => {
    const marker = markerEvidence(home, status), lastStop = lastStopEvidence(home, status)
    const [identity, supervision] = await Promise.all([
      home && readLocalRuntimeIdentity(options, home),
      home && marker.schemaVersion === 2 ? markerMatch(options.hostEntry, home) : null,
    ])
    return { status, identityReadable: home ? Boolean(identity) : null, marker, lastStop, supervision }
  }
  const [active, prior, registration, windowsPower] = await Promise.all([
    inspect(homes.active), inspect(homes.prior), registrationEvidence(options, homes), readRuntimePowerEvidence(options.hostEntry, from, to),
  ])
  return { sampledAt, pointer: homes.pointer, active, prior, registration, windowsPower }
}
async function markerMatch(hostEntry: string, home: string) {
  try {
    const resources = await import(pathToFileURL(path.join(path.dirname(hostEntry), 'connectionRecoveryResources.js')).href)
    const match = await resources.readRuntimeMarkerMatch(home)
    if (match.status === 'read' && typeof match.bootMatchesMarker === 'boolean' && typeof match.sessionMatchesHost === 'boolean' && typeof match.rebootOnly === 'boolean') {
      return { status: 'read', bootMatchesMarker: match.bootMatchesMarker, sessionMatchesHost: match.sessionMatchesHost, rebootOnly: match.rebootOnly }
    }
    if (match.status === 'unreadable' || match.status === 'unsupported') return { status: match.status }
  } catch { /* Older Host resources may not expose this diagnostic helper. */ }
  return { status: 'unavailable' }
}
export type RuntimeDiagnosticEvidence = Awaited<ReturnType<typeof readRuntimeDiagnosticEvidence>>
