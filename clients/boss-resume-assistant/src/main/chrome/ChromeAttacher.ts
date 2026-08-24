/**
 * Chrome attach 常量（CLI 场景，设计 §16 决策 6 修订）。
 * 背景：spawn 全新临时 profile Chrome 扫码登录触发 BOSS 风控封号，CLI 已废弃该路径，
 * 改为 attach 用户日常使用的、带 --remote-debugging-port 启动的 Chrome。
 * CLI 绝不杀死任何 Chrome 进程、绝不触碰任何 profile；端点探测由 CdpGateway 负责。
 *
 * 决策 6 修订二（2026-08-24）：端口不通时允许自动拉起**固定持久 profile** 的调试实例
 * （ChromeLauncher，默认 C:\chrome-debug，与部署手册快捷方式同一目录同一参数）——
 * 固定 profile 指纹/登录态跨次稳定，与用户手动双击快捷方式等价，不属「临时 profile
 * 风控」禁区；仍绝不杀进程、绝不碰用户日常 profile。
 */

/** 默认调试端口（可用 --cdp-port 覆盖） */
export const DEFAULT_CDP_PORT = 9222
