import { createHash, randomUUID } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { z } from 'zod'
import { CodedOperationError } from '../operations/types.js'
import { protectText, runDpapiCommand, type DpapiRunnerFn } from '../security/dpapi.js'
import type { Bubble } from './aligner.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from './powershell.js'

const OBSERVE_PATH = fileURLToPath(new URL('../../../drivers/ps1/observe-native.ps1', import.meta.url))
const RESOLVE_PATH = fileURLToPath(new URL('../../../drivers/ps1/name-resolve-native.ps1', import.meta.url))
const bounds = z.object({
  x0: z.number().int().nonnegative(), y0: z.number().int().nonnegative(),
  x1: z.number().int().positive(), y1: z.number().int().positive(),
}).refine((r) => r.x0 < r.x1 && r.y0 < r.y1)
const identitySchema = z.object({
  title: z.string().min(1).max(128),
  session_identity: z.string().min(1).max(256),
})
const snapshotSchema = identitySchema.extend({
  is_group: z.boolean(),
  input_empty: z.boolean(),
  png: z.string().min(1).optional(),
  rows: z.array(z.object({
    runtime_id: z.string().min(1),
    kind: z.enum(['text', 'time', 'media', 'unknown']),
    text: z.string().max(20_000),
    sender: z.enum(['peer', 'self', 'system', 'unknown']),
    sender_name: z.string().nullish(),
    bounds,
    clipped: z.boolean(),
  })).max(256),
})

export interface NativeMessageWindow {
  native_title: string
  evidence_ref: string
  frame: string
  objects: Array<{ type: 'text' | 'image'; sender: Bubble['sender']; text: string; clipped: boolean }>
}

type NativeObservationRead = ({ unchanged: true; frame: string } | ({ unchanged: false } & NativeMessageWindow))
  & { nativeSessionIdentity: string }

interface EvidenceOptions {
  stateDir?: string
  dpapiRunFn?: DpapiRunnerFn
}

async function saveNativeEvidence(value: string, signal: AbortSignal, opts: EvidenceOptions = {}): Promise<string> {
  const id = randomUUID()
  const stateDir = opts.stateDir ?? join(process.env.LOCALAPPDATA ?? tmpdir(), 'aid-weixin', 'native-evidence')
  const encrypted = await protectText(value, opts.dpapiRunFn ?? ((command, input) => runDpapiCommand(command, input, signal)))
  await mkdir(stateDir, { recursive: true })
  await writeFile(join(stateDir, id + '.dpapi'), encrypted, { flag: 'wx', signal })
  return 'dpapi:' + id
}

export async function resolveWeixinNativeName(
  targetName: string,
  signal: AbortSignal,
  runDriver: RunPowerShellDriverFn = runPowerShellDriver,
  evidenceOptions?: EvidenceOptions,
) {
  const raw = await runDriver({
    script: RESOLVE_PATH, stdin: JSON.stringify({ target_name: targetName }), signal, useVisionProxy: false,
  })
  const confirmation = identitySchema.strict().safeParse(raw)
  if (!confirmation.success || confirmation.data.title !== targetName) {
    throw new CodedOperationError('UI_CHANGED', '微信联系人原生定位证据不可用')
  }
  const evidenceRef = await saveNativeEvidence(JSON.stringify({ version: 1, ...confirmation.data }), signal, evidenceOptions)
  return { ...confirmation.data, evidenceRef }
}

export function parseNativeObservation(raw: unknown, info: { targetName: string; nativeTitle?: string; nativeSessionIdentity?: string }) {
  const parsed = snapshotSchema.safeParse(raw)
  if (!parsed.success || parsed.data.title !== (info.nativeTitle ?? info.targetName)
    || (info.nativeSessionIdentity !== undefined && parsed.data.session_identity !== info.nativeSessionIdentity)) {
    throw new CodedOperationError('UI_CHANGED', '当前微信原生消息或会话证据不可用')
  }
  const snapshot = parsed.data
  const messageIds = new Set<string>()
  const objects: NativeMessageWindow['objects'] = []
  let previousY = -1
  for (const row of snapshot.rows) {
    if (messageIds.has(row.runtime_id) || row.bounds.y0 < previousY) {
      throw new CodedOperationError('UI_CHANGED', '微信消息行顺序不确定')
    }
    messageIds.add(row.runtime_id)
    previousY = row.bounds.y0
    if (row.kind === 'time') continue
    if (row.kind === 'text' && (!row.text.trim() || row.sender === 'system')) {
      throw new CodedOperationError('UI_CHANGED', '微信原生消息正文或类型不确定')
    }
    objects.push({
      type: row.kind === 'text' ? 'text' : 'image',
      sender: row.sender,
      text: row.kind === 'text' ? row.text : '',
      clipped: row.clipped,
    })
  }
  const frame = createHash('sha256').update(JSON.stringify([
    snapshot.title,
    snapshot.session_identity,
    snapshot.rows.map((row) => [row.kind, row.text, row.sender, row.sender_name ?? null, row.clipped]),
  ])).digest('hex')
  return { snapshot, objects, frame }
}

export async function readWeixinNativeObservation(args: {
  targetName: string
  nativeTitle?: string
  nativeSessionIdentity?: string
  previousFrame?: string
  includeEvidence: boolean
  signal: AbortSignal
}, runDriver: RunPowerShellDriverFn = runPowerShellDriver): Promise<NativeObservationRead> {
  const raw = await runDriver({
    script: OBSERVE_PATH,
    stdin: JSON.stringify({
      target_name: args.targetName, native_title: args.nativeTitle,
      session_identity: args.nativeSessionIdentity, include_evidence: args.includeEvidence,
    }),
    signal: args.signal,
    useVisionProxy: false,
  })
  const { snapshot, objects, frame } = parseNativeObservation(raw, args)
  if (frame === args.previousFrame) return { unchanged: true, frame, nativeSessionIdentity: snapshot.session_identity }

  let evidenceRef = 'local-frame:' + frame
  if (args.includeEvidence) {
    if (!snapshot.png) throw new CodedOperationError('UI_CHANGED', '当前微信会话证据不可用')
    evidenceRef = await saveNativeEvidence(snapshot.png, args.signal)
  }
  return {
    unchanged: false, native_title: snapshot.title, evidence_ref: evidenceRef,
    frame, objects, nativeSessionIdentity: snapshot.session_identity,
  }
}
