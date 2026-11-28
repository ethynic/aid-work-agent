/**
 * 隐藏采集窗口的页面入口（设计 6.1）。无界面：加载后立即建连，等 Host 的同机判定，
 * 确认非本机才采集。窗口随会话结束被壳销毁，页面不监听任何 UI 事件。
 */
import { getScreenCaptureBridge } from './bridge'
import { ScreenCaptureClient } from './screenCaptureClient'

const bridge = getScreenCaptureBridge()
if (!bridge.params.sessionId) throw new Error('screen capture session id missing')
new ScreenCaptureClient(bridge).start()
