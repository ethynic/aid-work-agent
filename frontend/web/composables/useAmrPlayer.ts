import { ref } from 'vue'
import BenzAMRRecorder from 'benz-amr-recorder'

/**
 * AMR 语音播放器（前端解码 + Web Audio API 播放）
 *
 * 背景：企业微信客服等渠道返回的语音是 AMR 格式，浏览器原生 <audio> 不支持解码
 * （`canPlayType('audio/amr')` 返回空串）。本 composable 用 benz-amr-recorder
 * 在前端把 AMR 解码为 PCM 后通过 Web Audio API 播放。
 *
 * 设计要点：
 * - recorders 缓存在模块级 Map，跨组件实例共享，避免重复解码
 * - 同一时刻只允许一个语音在播，开始新语音时自动 stop 旧的
 * - 组件卸载 / 切换客户时由调用方调用 stopAll() 释放资源
 */

const recorders = new Map<string, BenzAMRRecorder>()
const currentlyPlayingId = ref<string | null>(null)
const loadingIds = ref<Set<string>>(new Set())

function attachListeners(rec: BenzAMRRecorder, mediaId: string) {
  const r = rec as any
  r.on('play', () => {
    currentlyPlayingId.value = mediaId
  })
  r.on('ended', () => {
    if (currentlyPlayingId.value === mediaId) currentlyPlayingId.value = null
  })
  r.on('pause', () => {
    if (currentlyPlayingId.value === mediaId) currentlyPlayingId.value = null
  })
  r.on('stop', () => {
    if (currentlyPlayingId.value === mediaId) currentlyPlayingId.value = null
  })
}

async function ensureLoaded(mediaId: string, url: string): Promise<BenzAMRRecorder> {
  const existing = recorders.get(mediaId)
  if (existing) return existing

  loadingIds.value.add(mediaId)
  try {
    const rec = new BenzAMRRecorder()
    attachListeners(rec, mediaId)
    await rec.initWithUrl(url)
    recorders.set(mediaId, rec)
    return rec
  } finally {
    loadingIds.value.delete(mediaId)
  }
}

export function useAmrPlayer() {
  async function play(mediaId: string, url: string): Promise<void> {
    if (currentlyPlayingId.value && currentlyPlayingId.value !== mediaId) {
      const prev = recorders.get(currentlyPlayingId.value)
      prev?.stop()
    }
    const rec = await ensureLoaded(mediaId, url)
    rec.play()
  }

  function pause(mediaId: string): void {
    const rec = recorders.get(mediaId)
    rec?.pause()
  }

  async function toggle(mediaId: string, url: string): Promise<void> {
    if (currentlyPlayingId.value === mediaId) {
      pause(mediaId)
    } else {
      await play(mediaId, url)
    }
  }

  function stopAll(): void {
    for (const rec of recorders.values()) {
      rec.stop()
    }
    currentlyPlayingId.value = null
  }

  function isPlaying(mediaId: string): boolean {
    return currentlyPlayingId.value === mediaId
  }

  function isLoading(mediaId: string): boolean {
    return loadingIds.value.has(mediaId)
  }

  return { play, pause, toggle, stopAll, isPlaying, isLoading }
}
