// 打印一次验证运行的摘要。用法：node summarize.cjs [out 目录]
// 只读取 out/ 下的 summary.json 与 jsonl，输出数字与类型。
const fs = require('fs');
const path = require('path');

const dir = path.resolve(process.argv[2] || path.join(__dirname, 'out'));
const pick = (o, keys) => Object.fromEntries(keys.filter((k) => o && o[k] !== undefined && o[k] !== null).map((k) => [k, o[k]]));

for (const name of fs.readdirSync(dir).filter((f) => f.endsWith('.summary.json')).sort()) {
  const s = JSON.parse(fs.readFileSync(path.join(dir, name), 'utf8'));
  console.log('== ' + name.replace('.summary.json', ''));
  console.log('config     ' + JSON.stringify(s.config));
  console.log('timingMs   ' + JSON.stringify(s.timingMs));
  console.log('sameMachine ' + JSON.stringify(s.sameMachine));
  console.log('capture    ' + JSON.stringify(s.capture) + (s.noConnect ? ' 未建连' : ''));
  console.log('stream     seconds=' + s.streamSeconds + ' samples=' + JSON.stringify(s.samples));
  console.log('tx         ' + JSON.stringify(pick(s.tx, ['codec', 'encoder', 'width', 'height', 'encodedFps', 'reportedFpsAvg', 'reportedFpsMin', 'bitrateKbps', 'totalEncoded', 'cpuLimitedSamples'])));
  console.log('rx         ' + JSON.stringify(pick(s.rx, ['decodedFps', 'reportedFpsAvg', 'reportedFpsMin', 'freezeCount', 'freezeSec', 'packetsLost'])));
  console.log('path       ' + JSON.stringify(s.path));
  console.log('cands      ' + JSON.stringify(s.localCandidates));
  console.log('signaling  ' + JSON.stringify(s.signaling));
  console.log('cpu        ' + JSON.stringify({ baseAvg: s.cpu.baselineTotalAvg, streamAvg: s.cpu.streamTotalAvg, coresPct: s.cpu.processCoresPct }));
  console.log('gpu        ' + JSON.stringify({ baseAvg: s.gpu.baselineTotalAvg, streamAvg: s.gpu.streamTotalAvg }));
  if (s.quality && Object.keys(s.quality.byState).length) console.log('quality    ' + JSON.stringify(s.quality));
  console.log('sysEvents  ' + JSON.stringify(s.systemEvents));
  const log = path.join(dir, name.replace('.summary.json', '.jsonl'));
  if (fs.existsSync(log)) {
    const rows = fs.readFileSync(log, 'utf8').trim().split('\n').map((l) => { try { return JSON.parse(l); } catch (e) { return null; } }).filter(Boolean);
    const t0 = (rows.find((r) => r.ev === 'ready') || {}).t;
    for (const e of rows) {
      if (e.ev === 'page' && e.role === 'sender' && /error|failed|closed|ended|expired|machine-check|captured/.test(e.name)) console.log('  ' + Math.round(e.t - t0) + 'ms sender ' + e.name + ' ' + JSON.stringify(e.data || {}).slice(0, 260));
      if (e.ev === 'page' && e.role === 'viewer' && /end|first-frame/.test(e.name)) console.log('  ' + Math.round(e.t - t0) + 'ms viewer ' + e.name + ' ' + JSON.stringify(e.data || {}).slice(0, 120));
    }
  }
}
