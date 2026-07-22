import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { RawCdpClient } from './client.mjs';
import { JsonlAuditWriter } from './audit.mjs';
import { locateUniqueText } from './snapshot-locator.mjs';

function valueOf(name, fallback) {
  const index = process.argv.indexOf(name);
  if (index === -1) return fallback;
  const value = process.argv[index + 1];
  if (!value || value.startsWith('--')) throw new Error(`${name} requires a value`);
  return value;
}

if (!process.argv.includes('--confirm-readonly-click')) {
  throw new Error('Refusing to click: pass --confirm-readonly-click after the user confirms the recommendation page is ready');
}
if (!process.argv.includes('--confirm-plaintext-artifacts')) {
  throw new Error('Refusing to capture: output contains plaintext candidate PII; use a protected temporary directory and pass --confirm-plaintext-artifacts');
}

const endpoint = valueOf('--endpoint', 'http://127.0.0.1:9222');
const baselineValue = valueOf('--baseline-snapshot', '');
const outputDir = resolve(valueOf('--output', './artifacts/live-readonly'));
const candidateText = valueOf('--candidate-text', '');
if (!baselineValue || !candidateText) throw new Error('Both --baseline-snapshot and --candidate-text are required');
const baselinePath = resolve(baselineValue);

const baseline = JSON.parse(await readFile(baselinePath, 'utf8'));
locateUniqueText(baseline, candidateText);
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
    const freshSnapshot = await client.send('DOMSnapshot.captureSnapshot', { computedStyles: [], includePaintOrder: true, includeDOMRects: true }, sessionId);
    const point = locateUniqueText(freshSnapshot, candidateText);
    await client.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: point.x, y: point.y, button: 'left', clickCount: 1 }, sessionId);
    await client.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: point.x, y: point.y, button: 'left', clickCount: 1 }, sessionId);
    await new Promise((resolveWait) => setTimeout(resolveWait, 2500));
    const snapshot = await client.send('DOMSnapshot.captureSnapshot', { computedStyles: [], includePaintOrder: true, includeDOMRects: true }, sessionId);
    await writeFile(resolve(outputDir, 'detail-snapshot.json'), JSON.stringify(snapshot), 'utf8');
    await client.send('Page.enable', {}, sessionId);
    const shot = await client.send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false }, sessionId);
    await writeFile(resolve(outputDir, 'detail-viewport.png'), Buffer.from(shot.data, 'base64'));
    await client.send('Page.disable', {}, sessionId);
  } finally {
    await client.send('Target.detachFromTarget', { sessionId });
  }
} finally {
  await client.close();
}
console.log(`Read-only detail capture completed at ${outputDir}`);
