/**
 * 详情关闭器（设计文档 §10.2）。
 * 用 Input.dispatchKeyEvent 发送 Escape 关闭候选人详情。
 * 失败时直接抛错（调用方应暂停任务），绝不退化为点击不确定的关闭位置。
 *
 * 按键参数与 spike live-escape.mjs 真机验证一致：
 * rawKeyDown + keyUp，key/code = Escape，windowsVirtualKeyCode = 27。
 */
import type { CdpGateway } from '../cdp/CdpGateway.js'

/** 结构子集，便于测试 mock；CdpGateway 天然满足 */
export type EscapeGateway = Pick<CdpGateway, 'dispatchKey'>

export async function sendEscapeClose(gateway: EscapeGateway): Promise<void> {
  const key = { key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 }
  await gateway.dispatchKey({ type: 'rawKeyDown', ...key })
  await gateway.dispatchKey({ type: 'keyUp', ...key })
}
