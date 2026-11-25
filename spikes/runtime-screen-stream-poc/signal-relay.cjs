// 信令中转（只用于验证）：放到两端都能访问的机器上运行，模拟产品里「服务器只转交建连信令」。
// 只保存和转交握手消息并统计字节数，不接触画面，也不保存任何信令内容到磁盘。
// 用法：node signal-relay.cjs
// 环境变量：POC_RELAY_PORT（默认 8799）、POC_STUN（逗号分隔的 STUN 地址，默认同验证程序；留空则不配置）。
// Runtime 侧以 POC_SIGNAL_URL=http://<本机地址>:<端口> 启动验证程序的 serve 模式；观看端用浏览器打开同地址的 viewer.html。
// 公网使用时由反向代理提供 HTTPS，本脚本自身只提供 HTTP。
'use strict';
const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = Number(process.env.POC_RELAY_PORT || 8799);
const STUN = process.env.POC_STUN === undefined ? 'stun.miwifi.com:3478' : process.env.POC_STUN;
const ICE_SERVERS = STUN ? [{ urls: STUN.split(',').map((u) => (/^(stun|stuns):/.test(u) ? u : 'stun:' + u)) }] : []; // 只配 STUN，不支持 TURN
const MAX_SIGNALS = 500; // 没人读取时最多留这么多条，避免无限增长
// 两个方向各一个队列：runtime 是发给 Runtime 侧的，viewer 是发给观看端的。
const queues = { runtime: [], viewer: [] };
const seqs = { runtime: 0, viewer: 0 };
const stats = { since: Date.now(), requests: 0, bytesIn: 0, bytesOut: 0 };
const logStats = () => console.log('RELAY_STATS ' + JSON.stringify(Object.assign({ seconds: Math.round((Date.now() - stats.since) / 1000) }, stats, { since: undefined })));

const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8' };

http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  stats.requests += 1;
  stats.bytesIn += Number(req.headers['content-length'] || 0);
  const json = (code, obj) => {
    const body = JSON.stringify(obj);
    stats.bytesOut += Buffer.byteLength(body);
    res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'Access-Control-Allow-Origin': '*' });
    res.end(body);
  };
  const pending = (queue, after) => {
    const seq = seqs[queue];
    return json(200, { seq, signals: queues[queue].filter((s) => s.seq > after) });
  };
  if (req.method === 'GET' && url.pathname === '/api/config') return json(200, { iceServers: ICE_SERVERS });
  if (req.method === 'GET' && url.pathname === '/api/session') return pending('viewer', Number(url.searchParams.get('after') || 0));
  if (req.method === 'GET' && url.pathname === '/api/runtime') return pending('runtime', Number(url.searchParams.get('after') || 0));
  if (req.method === 'POST' && (url.pathname === '/api/signal' || url.pathname === '/api/runtime')) {
    let body = '';
    req.on('data', (c) => { body += c; });
    return req.on('end', () => {
      let m = {};
      try { m = JSON.parse(body); } catch (e) { /* 非 JSON 按空处理 */ }
      const fromViewer = url.pathname === '/api/signal';
      // 观看端发起一次新的会话：清空两个方向的旧信令。序号不归零，读取方不会因为序号回退而漏掉新会话。
      if (fromViewer && m.kind === 'start') { queues.runtime.length = 0; queues.viewer.length = 0; }
      const queue = fromViewer ? 'runtime' : 'viewer';
      if (queues[queue].length >= MAX_SIGNALS) queues[queue].shift();
      queues[queue].push({ seq: ++seqs[queue], kind: m.kind, data: m.data });
      json(200, { ok: true });
    });
  }
  if (req.method === 'GET' && /^\/[A-Za-z0-9_-]+\.js$/.test(url.pathname)) {
    const script = path.join(__dirname, url.pathname);
    if (!fs.existsSync(script)) return json(404, { ok: false });
    res.writeHead(200, { 'Content-Type': TYPES['.js'], 'Cache-Control': 'no-store' });
    return res.end(fs.readFileSync(script));
  }
  if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/viewer.html' || url.pathname === '/p4.html')) {
    res.writeHead(200, { 'Content-Type': TYPES['.html'], 'Cache-Control': 'no-store' });
    return res.end(fs.readFileSync(path.join(__dirname, url.pathname === '/p4.html' ? 'p4.html' : 'viewer.html')));
  }
  json(404, { ok: false });
}).listen(PORT, '0.0.0.0', () => console.log('RELAY_READY ' + PORT));

setInterval(logStats, 10000);
process.on('SIGINT', () => { logStats(); process.exit(0); });
