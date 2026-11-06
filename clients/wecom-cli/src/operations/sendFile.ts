/** 文件发送使用本地库基线、双点缓存驱动及只读发送对账。 */
import { createMediaSendOperation, type MediaDeps } from './mediaSend.js'

export interface WecomSendFileArgs {
  target_ref?: string
  target_name?: string
  file_path: string
}

export function createWecomSendFileOperation(deps: MediaDeps = {}) {
  return createMediaSendOperation('file', deps)
}
