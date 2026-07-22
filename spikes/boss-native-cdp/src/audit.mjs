import { appendFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { dirname } from 'node:path';

function hash(value) {
  return value ? createHash('sha256').update(value).digest('hex').slice(0, 16) : null;
}

export function summarizeParams(method, params = {}) {
  if (method === 'Page.captureScreenshot') return { format: params.format, captureBeyondViewport: params.captureBeyondViewport };
  if (method.startsWith('Input.')) return { type: params.type, x: params.x, y: params.y, deltaY: params.deltaY, key: params.key };
  return redact(params);
}

function redact(value) {
  if (Array.isArray(value)) return value.map(redact);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => !/cookie|security|body|data|token|authorization|source/i.test(key))
      .map(([key, nested]) => [key, redact(nested)]),
  );
}

export class JsonlAuditWriter {
  constructor(path) { this.path = path; }
  async write(entry) {
    await mkdir(dirname(this.path), { recursive: true });
    await appendFile(this.path, `${JSON.stringify({ ...entry, sessionIdHash: hash(entry.sessionId), sessionId: undefined })}\n`, 'utf8');
  }
}
