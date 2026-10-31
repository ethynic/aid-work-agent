/** Anonymous encrypted DBs exercise the real operation/driver/cache mapping boundary. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync, unlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createWecomReadSessionOperation } from '../src/operations/readSession.js'
import { TOOL_DEFS } from '../src/mcp/toolDefs.js'
import { createWecomReadSessionsOperation } from '../src/operations/readSessions.js'
import { fixturePython, groupMonitorFixture } from './helpers/groupMonitorFixture.js'
import type { OpContext } from '../src/operations/types.js'

const root = fileURLToPath(new URL('../../', import.meta.url))
const windows = { skip: process.platform !== 'win32' }
const ctx = (): OpContext => ({ signal: new AbortController().signal, progress() {} })
async function fixture() {
  const base = mkdtempSync(join(tmpdir(), 'wecom-attachments-'))
  try {
    const f = await groupMonitorFixture(base)
    const ids = JSON.parse(execFileSync(fixturePython, [join(root, 'tests', 'py', 'attachment_fixture.py'), base],
      { encoding: 'utf8', windowsHide: true })) as { image_message_id: number; file_message_id: number; text: string }
    const op = createWecomReadSessionOperation({ localdb: f.deps })
    return { ...f, base, ids, op, close() { rmSync(base, { recursive: true, force: true }) } }
  } catch (error) {
    rmSync(base, { recursive: true, force: true })
    throw error
  }
}

test('timeline metadata identifies attachments without expanding private contents', windows, async () => {
  const f = await fixture()
  try {
    const r = await f.op.execute({ session: '合成群甲' }, ctx())
    assert.equal(r.success, true, r.message)
    const messages = r.data.messages as Array<Record<string, unknown>>
    assert.deepEqual(messages.filter(m => m.attachment).map(m => m.kind), ['image', 'file'])
    assert.equal((messages.find(m => m.kind === 'file')!.attachment as Record<string, unknown>).name, 'sample.md')
    assert.ok(messages.filter(m => m.attachment).every(m => (m.attachment as Record<string, unknown>).status === 'available'))
    const serialized = JSON.stringify(r.data)
    assert.ok(!serialized.includes('example.invalid'), 'private resource references stay on the client')
    assert.ok(!serialized.includes(f.ids.text.slice(0, 40)), 'ordinary reads do not expand documents')
    assert.ok(!serialized.includes('base64'))
    const batch = await createWecomReadSessionsOperation(f.deps).execute({ sessions: [r.data.session as string] }, ctx())
    assert.equal(batch.success, true)
    const sessions = batch.data.sessions as { messages: { kind: string }[] }[]
    assert.deepEqual(sessions[0]!.messages.filter(m => ['image', 'file'].includes(m.kind)).map(m => m.kind), ['image', 'file'])
  } finally { f.close() }
})

test('explicit file read returns UTF8 byte range and continuation without a timeline cursor', windows, async () => {
  const f = await fixture()
  try {
    const r = await f.op.execute({ session: '合成群甲', attachment: { message_id: f.ids.file_message_id, max_bytes: 21 } }, ctx())
    assert.equal(r.success, true, r.message)
    const first = r.data.attachment as Record<string, unknown>
    assert.equal(first.status, 'available')
    assert.equal(first.text, Buffer.from(f.ids.text).subarray(0, 21).toString('utf8'))
    assert.equal(first.next_offset, 21)
    assert.equal(first.has_more, true)
    assert.equal(r.data.cursor, undefined)
    assert.equal(r.data.messages, undefined)
    const next = await f.op.execute({ session: r.data.session as string,
      attachment: { message_id: f.ids.file_message_id, offset: first.next_offset as number, max_bytes: 21 } }, ctx())
    assert.equal(next.success, true, next.message)
    const second = next.data.attachment as Record<string, unknown>
    assert.equal(second.offset, 21)
    assert.ok(f.ids.text.startsWith(String(first.text) + String(second.text)))
  } finally { f.close() }
})

test('null attachment is an ordinary timeline read through MCP schema and native operation', windows, async () => {
  const f = await fixture()
  try {
    const schema = TOOL_DEFS.find(t => t.name === 'wecom_read_session')!.zodShape.attachment!
    assert.equal(schema.safeParse(null).success, true, 'cloud Optional null must reach the native ordinary-read path')
    const ordinary = await f.op.execute({ session: '合成群甲' }, ctx())
    const explicitNull = await f.op.execute({ session: '合成群甲', attachment: null }, ctx())
    assert.equal(explicitNull.success, true, explicitNull.message)
    assert.deepEqual(explicitNull.data, ordinary.data)
    assert.equal(explicitNull.data.attachment, undefined, 'null must not expand any attachment')
    const cursor = ordinary.data.cursor as string
    const plainNext = await f.op.execute({ session: '合成群甲', cursor }, ctx())
    const nullNext = await f.op.execute({ session: '合成群甲', cursor, attachment: null }, ctx())
    assert.equal(nullNext.success, true, nullNext.message)
    assert.deepEqual(nullNext.data, plainNext.data, 'null preserves the ordinary incremental cursor semantics')
    assert.deepEqual(nullNext.data.new_messages, [])
  } finally { f.close() }
})

test('explicit image read transports actual bytes with detected format, not a client path', windows, async () => {
  const f = await fixture()
  try {
    const r = await f.op.execute({ session: '合成群甲', attachment: { message_id: f.ids.image_message_id } }, ctx())
    assert.equal(r.success, true, r.message)
    const a = r.data.attachment as Record<string, unknown>
    assert.equal(a.status, 'available')
    assert.equal(a.mime_type, 'image/png', 'cache extension is .jpg but the actual image is PNG')
    const image = a.image as { type: string; mimeType: string; data: string }
    assert.equal(image.type, 'image')
    assert.equal(image.mimeType, 'image/png')
    assert.equal(Buffer.from(image.data, 'base64').subarray(1, 4).toString(), 'PNG')
    assert.ok(!JSON.stringify(r.data).includes(f.current.dataDir.dir!))
    assert.equal(r.data.cursor, undefined)
    assert.equal(r.data.new_messages, undefined)
  } finally { f.close() }
})

test('missing attachment remains one readable status; neighboring chat and cursor still work', windows, async () => {
  const f = await fixture()
  try {
    unlinkSync(join(f.current.dataDir.dir!, 'Cache', 'Image', 'anonymous.jpg'))
    const r = await f.op.execute({ session: '合成群甲', attachment: { message_id: f.ids.image_message_id } }, ctx())
    assert.equal(r.success, true)
    assert.equal((r.data.attachment as Record<string, unknown>).status, 'not_cached')
    const timeline = await f.op.execute({ session: '合成群甲' }, ctx())
    assert.equal(timeline.success, true)
    assert.equal(typeof timeline.data.cursor, 'string')
    assert.equal((timeline.data.messages as unknown[]).length, 3)
    const missing = await f.op.execute({ session: 'missing attachment conversation', attachment: { message_id: f.ids.image_message_id } }, ctx())
    assert.equal(missing.code, 'TARGET_NOT_FOUND', 'target resolution failure must not masquerade as an unavailable database')
  } finally { f.close() }
})

test('attachment selection is optional and cannot silently consume an incremental cursor', async () => {
  const op = createWecomReadSessionOperation({ localdb: { probeAccountFn: async () => { throw new Error('must not probe') } } })
  for (const args of [
    { session: 'Fixture', cursor: 'wb1.original', attachment: { message_id: 3 } },
    { session: 'Fixture', resolve_only: true, attachment: { message_id: 3 } },
    { session: 'Fixture', attachment: { message_id: 3, max_bytes: 8193 } },
    { target_name: 'Fixture', attachment: { message_id: 3 } },
  ]) {
    const result = await op.execute(args, ctx())
    assert.equal(result.code, 'INVALID_ARGUMENT')
  }
  const definition = TOOL_DEFS.find(t => t.name === 'wecom_read_session')!
  assert.ok(definition.zodShape.attachment!.isOptional())
  assert.equal(definition.annotations.readOnlyHint, true)
})

test('CLI parses attachment JSON before performing any client operation', () => {
  try {
    execFileSync(process.execPath, [join(root, 'dist', 'src', 'cli', 'index.js'), 'read-session',
      '--session', 'Fixture', '--attachment', '{bad json', '--json'], { encoding: 'utf8', windowsHide: true })
    assert.fail('invalid JSON must reject')
  } catch (error) {
    const e = error as { stdout: string; status: number }
    const result = JSON.parse(e.stdout.trim().split(/\r?\n/).at(-1)!)
    assert.equal(e.status, 2)
    assert.equal(result.code, 'INVALID_ARGUMENT')
  }
})
