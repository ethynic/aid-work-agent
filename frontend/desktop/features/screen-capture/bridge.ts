/**
 * 采集页的 preload 桥（electron/screen-preload.cts 暴露的 window.screenCapture）。
 * 只暴露启动参数、收 Host 消息与发页面消息三个能力；页面不访问网络，
 * 信令全部经这两个专用通道转交（设计 6.1）。
 */

export interface ScreenCaptureParams {
  sessionId: string
  stunUrls: readonly string[]
}

export interface ScreenCaptureBridge {
  params: ScreenCaptureParams
  /** Host→采集页的消息（ToPageMessage 形状，逐条核对） */
  onHostMessage(callback: (message: unknown) => void): () => void
  /** 采集页→Host 的消息（FromPageMessage 形状）；Main 只接受采集窗口本身发送 */
  sendPageMessage(message: unknown): void
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** 桥不完整时页面无法工作，fail loud */
export function getScreenCaptureBridge(): ScreenCaptureBridge {
  const bridge = (globalThis as unknown as Record<string, unknown>)['screenCapture']
  if (!isRecord(bridge) || !isRecord(bridge.params) || typeof bridge.params['sessionId'] !== 'string'
    || !Array.isArray(bridge.params['stunUrls']) || !bridge.params['stunUrls'].every((url) => typeof url === 'string')
    || typeof bridge.onHostMessage !== 'function' || typeof bridge.sendPageMessage !== 'function') {
    throw new Error('screen capture bridge unavailable or incompatible')
  }
  return bridge as unknown as ScreenCaptureBridge
}
