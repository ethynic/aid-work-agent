import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { RawCdpClient } from './client.mjs';
import { JsonlAuditWriter } from './audit.mjs';

if (!process.argv.includes('--confirm-detail-open')) throw new Error('Refusing to send Escape without confirmation');
if (!process.argv.includes('--confirm-plaintext-artifacts')) {
  throw new Error('Refusing to capture: output contains plaintext candidate PII; use a protected temporary directory and pass --confirm-plaintext-artifacts');
}
const outputIndex = process.argv.indexOf('--output');
const outputValue = outputIndex < 0 ? './artifacts/live-escape' : process.argv[outputIndex + 1];
if (!outputValue || outputValue.startsWith('--')) throw new Error('--output requires a value');
const outputDir = resolve(outputValue);
await mkdir(outputDir, { recursive: true });
const version = await (await fetch('http://127.0.0.1:9222/json/version')).json();
const client = new RawCdpClient({ auditWriter: new JsonlAuditWriter(resolve(outputDir, 'cdp-audit.jsonl')) });
await client.connect(version.webSocketDebuggerUrl);
try {
  const targets = await client.send('Target.getTargets');
  const page = targets.targetInfos.find((item) => item.type === 'page' && item.url.includes('zhipin.com/web/chat/recommend'));
  if (!page) throw new Error('No ready recommendation target found');
  const { sessionId } = await client.send('Target.attachToTarget', { targetId: page.targetId, flatten: true });
  try {
    const key = { key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 };
    await client.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', ...key }, sessionId);
    await client.send('Input.dispatchKeyEvent', { type: 'keyUp', ...key }, sessionId);
    await new Promise((done) => setTimeout(done, 800));
    await client.send('Page.enable', {}, sessionId);
    const shot = await client.send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false }, sessionId);
    await writeFile(resolve(outputDir, 'after-escape.png'), Buffer.from(shot.data, 'base64'));
    await client.send('Page.disable', {}, sessionId);
  } finally {
    await client.send('Target.detachFromTarget', { sessionId });
  }
} finally {
  await client.close();
}
