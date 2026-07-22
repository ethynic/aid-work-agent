import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { RawCdpClient } from './client.mjs';
import { JsonlAuditWriter } from './audit.mjs';

function valueOf(name, fallback) {
  const index = process.argv.indexOf(name);
  return index === -1 ? fallback : process.argv[index + 1];
}

if (!process.argv.includes('--confirm-page-ready')) {
  throw new Error('Refusing to connect: user must manually log in, close popups, open recommendations, then pass --confirm-page-ready');
}
if (!process.argv.includes('--confirm-plaintext-artifacts')) {
  throw new Error('Refusing to capture: Phase 0 artifacts contain plaintext PII; use a protected temporary directory and pass --confirm-plaintext-artifacts');
}
const endpoint = valueOf('--endpoint', 'http://127.0.0.1:9222');
const outputDir = resolve(valueOf('--output', './artifacts'));
await mkdir(outputDir, { recursive: true });
const versionResponse = await fetch(`${endpoint}/json/version`);
if (!versionResponse.ok) throw new Error(`Cannot read CDP endpoint: HTTP ${versionResponse.status}`);
const version = await versionResponse.json();
const client = new RawCdpClient({ auditWriter: new JsonlAuditWriter(resolve(outputDir, 'cdp-audit.jsonl')) });
await client.connect(version.webSocketDebuggerUrl);
try {
  const targets = await client.send('Target.getTargets');
  const page = targets.targetInfos.find((item) => item.type === 'page' && item.url.includes('zhipin.com/web/chat/recommend'));
  if (!page) throw new Error('No ready BOSS recommendation page target found');
  const { sessionId } = await client.send('Target.attachToTarget', { targetId: page.targetId, flatten: true });
  try {
    const snapshot = await client.send('DOMSnapshot.captureSnapshot', { computedStyles: [], includePaintOrder: true, includeDOMRects: true }, sessionId);
    await writeFile(resolve(outputDir, 'recommend-snapshot.json'), JSON.stringify(snapshot), 'utf8');
    await client.send('Page.enable', {}, sessionId);
    const shot = await client.send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false }, sessionId);
    await writeFile(resolve(outputDir, 'recommend-viewport.png'), Buffer.from(shot.data, 'base64'));
    await client.send('Page.disable', {}, sessionId);
  } finally {
    await client.send('Target.detachFromTarget', { sessionId });
  }
} finally {
  await client.close();
}
console.log(`Read-only baseline captured in ${outputDir}. No click, navigation, refresh, or write action was performed.`);
