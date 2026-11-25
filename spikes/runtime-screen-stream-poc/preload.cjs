// 验证页面的 preload：只暴露收发消息和上报统计，不提供任何 Node 能力。
const { contextBridge, ipcRenderer } = require('electron');

const listeners = new Set();
ipcRenderer.on('poc:signal', (_event, message) => listeners.forEach((fn) => fn(message)));
ipcRenderer.on('poc:start', () => listeners.forEach((fn) => fn({ kind: 'start' })));

contextBridge.exposeInMainWorld('poc', {
  role: process.argv.find((a) => a.startsWith('--poc-role='))?.split('=')[1] || 'unknown',
  ready: () => ipcRenderer.send('poc:ready'),
  signal: (to, kind, data) => ipcRenderer.send('poc:signal', { to, kind, data }),
  event: (name, data) => ipcRenderer.send('poc:event', { name, data }),
  onMessage: (fn) => { listeners.add(fn); return () => listeners.delete(fn); },
});
