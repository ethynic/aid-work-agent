/** Synthetic stdio fixture: no browser, desktop input, credentials or real images. */
import { appendFileSync } from 'node:fs'
import readline from 'node:readline'

const lines = readline.createInterface({ input: process.stdin })
lines.on('line', (line) => {
  const request = JSON.parse(line)
  if (request.id === undefined) return
  if (request.method === 'initialize') {
    process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: {
      protocolVersion: request.params.protocolVersion, capabilities: { tools: {} },
      serverInfo: { name: 'synthetic-large-result', version: '1' },
    } }) + '\n')
    return
  }
  const args = request.params?.arguments ?? {}
  if (args.counter_file) appendFileSync(args.counter_file, 'call\n')
  if (args.malformed) {
    process.stdout.write('not-json PRIVATE_CANDIDATE PRIVATE_TOKEN\n', () => process.exit(0))
    return
  }
  if (args.crash) { process.exit(1); return }
  const data = { success: true, code: 'OK', effect: 'none', retryable: false,
    message: 'synthetic read completed', run_id: 'synthetic-result',
    data: { images: [{ base64: 'A'.repeat(args.base64_bytes ?? 1) }], padding: '' } }
  const result = { content: args.wire_bytes ? [] : [{ type: 'text', text: JSON.stringify(data) }],
    structuredContent: data }
  const response = { jsonrpc: '2.0', id: request.id, result }
  if (args.wire_bytes) {
    const overhead = Buffer.byteLength(JSON.stringify(response) + '\n')
    data.data.padding = 'A'.repeat(args.wire_bytes - overhead)
  }
  process.stdout.write(JSON.stringify(response) + '\n')
})
lines.on('close', () => process.exit(0))
