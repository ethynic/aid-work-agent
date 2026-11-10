/** 图片发送使用本地库基线、双点缓存驱动及只读发送对账。 */
import { createMediaSendOperation, type MediaDeps } from './mediaSend.js'

export interface WecomSendImageArgs {
  target_ref?: string
  target_name?: string
  image_path: string
}

export function createWecomSendImageOperation(deps: MediaDeps = {}) {
  return createMediaSendOperation('image', deps)
}
