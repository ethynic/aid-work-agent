// 隐藏采集窗口的 preload（设计 6.1）。只暴露启动参数、收 Host 消息与发页面消息三个能力，
// 不暴露任何网络或 Node 能力；页面不访问网络，信令全部经这两个专用通道转交。
const { contextBridge, ipcRenderer } = require('electron') as typeof import('electron')

function readArgument(name: string): string | undefined {
  const prefix = `--${name}=`
  return process.argv.find((argument) => argument.startsWith(prefix))?.slice(prefix.length)
}

// stun_urls 由 Main 用逗号连接（stun:/stuns: 地址不含逗号）；解析不出时按未配置处理
const stunUrls = (readArgument('aid-screen-stun-urls') ?? '')
  .split(',')
  .map((url) => url.trim())
  .filter((url) => /^(stun|stuns):/.test(url))

const params = Object.freeze({
  sessionId: readArgument('aid-screen-session-id') ?? '',
  stunUrls: Object.freeze(stunUrls),
})

contextBridge.exposeInMainWorld('screenCapture', Object.freeze({
  params,
  /** Host→Main→采集页的消息（ToPageMessage 形状由采集页逐条核对） */
  onHostMessage: (callback: (message: unknown) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, message: unknown) => callback(message)
    ipcRenderer.on('desktop:screen:host-message', listener)
    return () => ipcRenderer.removeListener('desktop:screen:host-message', listener)
  },
  /** 采集页→Main→Host 的消息（FromPageMessage 形状）；Main 只接受采集窗口本身发送 */
  sendPageMessage: (message: unknown) => ipcRenderer.send('desktop:screen:page-message', message),
}))
