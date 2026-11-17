import { createHash, randomUUID } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { z } from 'zod'
import { CodedOperationError } from '../operations/types.js'
import { protectText, runDpapiCommand } from '../security/dpapi.js'
import type { NameRead, NameWindow } from './nameSession.js'
import { runPowerShellDriver, type RunPowerShellDriverFn } from './powershell.js'

const OBSERVE_PATH = fileURLToPath(new URL('../../../drivers/ps1/observe-native.ps1', import.meta.url))
const bounds = z.object({
  x0: z.number().int().nonnegative(), y0: z.number().int().nonnegative(),
  x1: z.number().int().positive(), y1: z.number().int().positive(),
}).refine((r) => r.x0 < r.x1 && r.y0 < r.y1)
const snapshotSchema = z.object({
  title: z.string().min(1).max(128),
  session_identity: z.string().min(1).max(256),
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

export function parseNativeObservation(raw: unknown, info: { targetName: string; nativeTitle?: string; nativeSessionIdentity?: string }) {
  const parsed = snapshotSchema.safeParse(raw)
  if (!parsed.success || parsed.data.title !== (info.nativeTitle ?? info.targetName)
    || (info.nativeSessionIdentity !== undefined && parsed.data.session_identity !== info.nativeSessionIdentity)) {
    throw new CodedOperationError('UI_CHANGED', '当前微信原生消息或会话证据不可用')
  }
  const snapshot = parsed.data
  const messageIds = new Set<string>()
  const objects: NameWindow['objects'] = []
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
      bounds: row.bounds,
      clipped: row.clipped,
      evidence_hash: createHash('sha256').update(JSON.stringify([row.kind, row.text, row.sender])).digest('hex'),
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
}, runDriver: RunPowerShellDriverFn = runPowerShellDriver): Promise<NameRead & { nativeSessionIdentity: string }> {
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
    const evidenceId = randomUUID()
    const stateDir = join(process.env.LOCALAPPDATA ?? tmpdir(), 'aid-weixin', 'ocr-evidence')
    await mkdir(stateDir, { recursive: true })
    const evidence = await protectText(snapshot.png, (command, input) => runDpapiCommand(command, input, args.signal))
    await writeFile(join(stateDir, evidenceId + '.dpapi'), evidence, { flag: 'wx', signal: args.signal })
    evidenceRef = 'dpapi:' + evidenceId
  }
  return {
    unchanged: false, title: args.targetName, native_title: snapshot.title, exact_name: true, unique_match: true,
    evidence_ref: evidenceRef, complete: true, input_empty: snapshot.input_empty,
    frame, objects, nativeSessionIdentity: snapshot.session_identity,
  }
}
