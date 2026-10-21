/**
 * 原生输入错误。exitCode 与 win-click.ps1 的退出码语义一致：
 * 0=成功（不抛错）；1=窗口/参数错误或输入效果未知；2=守卫拒绝（遮挡/STALE_OBSERVATION）。
 */
export type NativeInputFailureKind =
  /** 落点守卫拒绝（不盲点） */
  | 'REFUSED'
  /** 绑定模式下输入开始前的观察失效（唯一性/前台/widget 不可证明） */
  | 'STALE_OBSERVATION'
  /** MarkInputStarted 之后的任何失败：物理事件可能已发出，效果未知 */
  | 'BOSS_INPUT_UNKNOWN'
  /** 窗口未找到 / 参数非法 / 其他非守卫失败 */
  | 'FAILED'

export class NativeInputError extends Error {
  /** 对应 win-click.ps1 退出码（1/2），供调用方映射既有通道语义 */
  readonly exitCode: 1 | 2
  readonly kind: NativeInputFailureKind
  /** 守卫失败时的原生几何取证（无标题/无页面内容） */
  readonly diagnostics?: string

  constructor(message: string, exitCode: 1 | 2, kind: NativeInputFailureKind, diagnostics?: string) {
    super(message)
    this.name = 'NativeInputError'
    this.exitCode = exitCode
    this.kind = kind
    this.diagnostics = diagnostics
  }
}

export function isNativeInputError(error: unknown): error is NativeInputError {
  return error instanceof NativeInputError
}
