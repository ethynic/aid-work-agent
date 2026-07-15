import path from 'node:path'

export const MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024

export function assertDownloadSize(value: string | null | number): void {
  if (value === null) return
  const bytes = typeof value === 'number' ? value : Number(value)
  if (!Number.isSafeInteger(bytes) || bytes < 0 || bytes > MAX_DOWNLOAD_BYTES) {
    throw new Error('download exceeds size limit')
  }
}

export async function readDownloadBody(response: Response): Promise<Buffer> {
  assertDownloadSize(response.headers.get('content-length'))
  if (!response.body) throw new Error('download response body is unavailable')
  const reader = response.body.getReader()
  const chunks: Buffer[] = []
  let total = 0
  try {
    for (;;) {
      const chunk = await reader.read()
      if (chunk.done) break
      total += chunk.value.byteLength
      assertDownloadSize(total)
      chunks.push(Buffer.from(chunk.value))
    }
  } catch (error) {
    await reader.cancel().catch(() => undefined)
    throw error
  }
  return Buffer.concat(chunks, total)
}

export function normalizeExternalUrl(value: unknown): string {
  if (typeof value !== 'string') throw new Error('external URL must be a string')
  const url = new URL(value)
  if (url.protocol !== 'https:' || url.username || url.password) throw new Error('only credential-free HTTPS external URLs are allowed')
  return url.toString()
}

export function normalizeDownloadUrl(value: unknown, apiOrigin: string): string {
  if (typeof value !== 'string') throw new Error('download URL must be a string')
  const url = new URL(value)
  if (url.origin !== apiOrigin || url.username || url.password) throw new Error('download URL must use the configured API origin')
  return url.toString()
}

export function safeSuggestedName(value: unknown): string {
  if (typeof value !== 'string') return 'download'
  const name = path.basename(value).replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').slice(0, 180)
  return name || 'download'
}
