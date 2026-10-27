/**
 * download 工具单测 —— 移动端下载策略
 *
 * 验证：
 * - isWeChatBrowser：微信 / 企业微信 UA（均含 MicroMessenger）命中，普通浏览器不命中
 * - isMobileBrowser / resolveDownloadStrategy：微信恒引导、非微信移动端 <10MB blob、
 *   其余（PC、≥10MB）native
 * - downloadViaBlob：fetch + blob URL + a[download]，携带 header，延迟 revoke
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

import {
  downloadViaBlob,
  isMobileBrowser,
  isWeChatBrowser,
  MOBILE_BLOB_DOWNLOAD_MAX_BYTES,
  resolveDownloadStrategy,
} from '@/utils/download'

const CHROME_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
const WECHAT_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49(0x18003135) NetType/WIFI Language/zh_CN'
const WECOM_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 wxwork/4.1.36 MicroMessenger/8.0.49 NetType/WIFI Language/zh_CN'

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

describe('isMobileBrowser / resolveDownloadStrategy —— 移动端下载策略', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function setUA(ua: string, maxTouchPoints = 0) {
    vi.stubGlobal('navigator', { userAgent: ua, maxTouchPoints })
  }

  const ANDROID_UA = 'Mozilla/5.0 (Linux; U; Android 16; zh-cn) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.7049.79 Mobile Safari/537.36 XiaoMi/MiuiBrowser/20.29.1031010'
  const IOS_SAFARI_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1'
  const IPAD_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'

  it('isMobileBrowser：iOS/安卓 UA 命中，PC Chrome 不命中，iPad 桌面模式 UA 命中', () => {
    setUA(IOS_SAFARI_UA)
    expect(isMobileBrowser()).toBe(true)
    setUA(ANDROID_UA)
    expect(isMobileBrowser()).toBe(true)
    setUA(CHROME_UA)
    expect(isMobileBrowser()).toBe(false)
    setUA(IPAD_UA, 5)
    expect(isMobileBrowser()).toBe(true)
  })

  it('微信 UA 恒为 wechat_guide（无论大小、无论平台）', () => {
    setUA(WECHAT_UA)
    expect(resolveDownloadStrategy(1024)).toBe('wechat_guide')
    expect(resolveDownloadStrategy(100 * 1024 * 1024)).toBe('wechat_guide')
    expect(resolveDownloadStrategy(undefined)).toBe('wechat_guide')
    setUA(WECOM_UA)
    expect(resolveDownloadStrategy(1024)).toBe('wechat_guide')
  })

  it('非微信移动端 <10MB 走 blob', () => {
    setUA(IOS_SAFARI_UA)
    expect(resolveDownloadStrategy(MOBILE_BLOB_DOWNLOAD_MAX_BYTES - 1)).toBe('blob')
    expect(resolveDownloadStrategy(undefined)).toBe('blob')
  })

  it('非微信移动端 ≥10MB 走 native（大文件不适合整包进内存）', () => {
    setUA(ANDROID_UA)
    expect(resolveDownloadStrategy(MOBILE_BLOB_DOWNLOAD_MAX_BYTES)).toBe('native')
    expect(resolveDownloadStrategy(50 * 1024 * 1024)).toBe('native')
  })

  it('PC 端恒为 native（与文件大小无关）', () => {
    setUA(CHROME_UA)
    expect(resolveDownloadStrategy(1024)).toBe('native')
    expect(resolveDownloadStrategy(undefined)).toBe('native')
  })
})

describe('downloadViaBlob —— fetch + blob 本地下载', () => {
  let clickSpy: ReturnType<typeof vi.spyOn>
  const clickedAnchors: HTMLAnchorElement[] = []
  const createdUrls: string[] = []
  const revokedUrls: string[] = []

  beforeEach(() => {
    clickedAnchors.length = 0
    createdUrls.length = 0
    revokedUrls.length = 0
    clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clickedAnchors.push(this)
    })
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn((blob: Blob) => {
        createdUrls.push(`blob:mock-${createdUrls.length}-${blob.size}`)
        return createdUrls[createdUrls.length - 1]
      }),
      revokeObjectURL: vi.fn((url: string) => revokedUrls.push(url)),
    })
  })

  afterEach(() => {
    clickSpy.mockRestore()
    vi.unstubAllGlobals()
  })

  it('fetch 成功：blob URL + a[download] 触发下载，携带认证 header，延迟 revoke', async () => {
    vi.useFakeTimers()
    const fetchSpy = vi.spyOn(window, 'fetch').mockResolvedValue(new Response(new Blob(['excel-bytes']), { status: 200 }))
    try {
      await downloadViaBlob('/api/files/f1/download', '报表.xlsx', { Authorization: 'Bearer t' })
      expect(fetchSpy).toHaveBeenCalledWith('/api/files/f1/download', { headers: { Authorization: 'Bearer t' } })
      expect(clickedAnchors).toHaveLength(1)
      expect(clickedAnchors[0].download).toBe('报表.xlsx')
      expect(clickedAnchors[0].href).toMatch(/^blob:mock-/)
      expect(createdUrls).toHaveLength(1)
      // revoke 延迟 60s，避免部分移动内核立即 revoke 中断未启动的下载
      expect(revokedUrls).toEqual([])
      vi.advanceTimersByTime(60_000)
      expect(revokedUrls).toEqual(createdUrls)
    } finally {
      vi.useRealTimers()
      fetchSpy.mockRestore()
    }
  })

  it('fetch 非 2xx：抛错且不触发 anchor 下载', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch').mockResolvedValue(new Response('gone', { status: 404 }))
    try {
      await expect(downloadViaBlob('/api/files/f2/download', '文件.pdf')).rejects.toThrow('下载失败: 404')
      expect(clickedAnchors).toHaveLength(0)
    } finally {
      fetchSpy.mockRestore()
    }
  })
})
