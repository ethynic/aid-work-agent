/**
 * download 工具单测 —— 微信内置浏览器下载兼容
 *
 * 验证：
 * - isWeChatBrowser：微信 / 企业微信 UA（均含 MicroMessenger）命中，普通浏览器不命中
 * - triggerCompatibleDownload：
 *   - 微信 / 企业微信内整页导航（window.location.href 赋值），不走 anchor 原生下载
 *   - 普通浏览器走 anchor click 原生下载，不改写 location.href
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

import { isWeChatBrowser, triggerCompatibleDownload } from '@/utils/download'

const CHROME_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
const WECHAT_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49(0x18003135) NetType/WIFI Language/zh_CN'
const WECOM_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 wxwork/4.1.36 MicroMessenger/8.0.49 NetType/WIFI Language/zh_CN'

const originalLocation = window.location

/** jsdom 不允许直接写 location.href，按仓库既有手法（tenantStorage.test.ts）重写 location */
function stubLocation() {
  Object.defineProperty(window, 'location', {
    value: { ...originalLocation, href: '' },
    writable: true,
    configurable: true,
  })
}

describe('isWeChatBrowser —— UA 检测', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function setUA(ua: string) {
    vi.stubGlobal('navigator', { userAgent: ua })
  }

  it('微信内置浏览器 UA（MicroMessenger）命中', () => {
    setUA(WECHAT_UA)
    expect(isWeChatBrowser()).toBe(true)
  })

  it('企业微信 UA（wxwork + MicroMessenger）命中', () => {
    setUA(WECOM_UA)
    expect(isWeChatBrowser()).toBe(true)
  })

  it('普通浏览器 UA 不命中', () => {
    setUA(CHROME_UA)
    expect(isWeChatBrowser()).toBe(false)
  })
})

describe('triggerCompatibleDownload —— 微信整页导航，普通浏览器原生下载', () => {
  let clickSpy: ReturnType<typeof vi.spyOn>
  const clickedAnchors: HTMLAnchorElement[] = []

  beforeEach(() => {
    stubLocation()
    clickedAnchors.length = 0
    clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clickedAnchors.push(this)
    })
  })

  afterEach(() => {
    clickSpy.mockRestore()
    Object.defineProperty(window, 'location', {
      value: originalLocation,
      writable: true,
      configurable: true,
    })
    vi.unstubAllGlobals()
  })

  function setUA(ua: string) {
    vi.stubGlobal('navigator', { userAgent: ua })
  }

  it('微信 UA：location.href 被赋值为下载 URL，不触发 anchor click', () => {
    setUA(WECHAT_UA)
    triggerCompatibleDownload('/api/files/file_1/download', '表格.xlsx')
    expect(window.location.href).toBe('/api/files/file_1/download')
    expect(clickSpy).not.toHaveBeenCalled()
  })

  it('企业微信 UA：同样整页导航', () => {
    setUA(WECOM_UA)
    triggerCompatibleDownload('/api/files/file_2/download')
    expect(window.location.href).toBe('/api/files/file_2/download')
    expect(clickSpy).not.toHaveBeenCalled()
  })

  it('普通浏览器 UA：走 anchor click 原生下载，location.href 不变', () => {
    setUA(CHROME_UA)
    triggerCompatibleDownload('/api/files/file_3/download', '报告.pdf')
    expect(clickSpy).toHaveBeenCalledTimes(1)
    expect(clickedAnchors[0].href.endsWith('/api/files/file_3/download')).toBe(true)
    expect(clickedAnchors[0].getAttribute('download')).toBe('报告.pdf')
    expect(window.location.href).toBe('')
  })
})
