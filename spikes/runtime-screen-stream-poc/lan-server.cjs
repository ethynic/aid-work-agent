// 内网地址源测试用的页面服务器（独立 Node 进程，只用于验证）。
// 让测试页从「内网地址」打开；接口请求转发到回环地址上的 Electron 验证服务。
// 用法：POC_LAN_PORT=8792 POC_HTTP_PORT=8791 node lan-server.cjs
const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = Number(process.env.POC_LAN_PORT || 8792);
const UPSTREAM = { host: '127.0.0.1', port: Number(process.env.POC_HTTP_PORT || 8791) };
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8' };

http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const rel = url.pathname === '/' ? '/viewer.html' : url.pathname;
  const file = path.join(__dirname, rel);
  if (req.method === 'GET' && /^\/[A-Za-z0-9_.-]+\.(html|js)$/.test(rel) && fs.existsSync(file)) {
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
    return fs.createReadStream(file).pipe(res);
  }
  if (url.pathname.startsWith('/api/')) {
    const up = http.request({ host: UPSTREAM.host, port: UPSTREAM.port, method: req.method, path: req.url, headers: req.headers }, (r) => {
      res.writeHead(r.statusCode, r.headers);
      r.pipe(res);
    });
    up.on('error', () => { res.writeHead(502); res.end('upstream unavailable'); });
    return req.pipe(up);
  }
  res.writeHead(404);
  res.end('not found');
}).listen(PORT, '0.0.0.0', () => console.log('LAN_SERVER_READY ' + PORT));
