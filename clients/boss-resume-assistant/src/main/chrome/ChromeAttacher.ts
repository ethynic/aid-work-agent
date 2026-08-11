/**
 * Chrome attach 常量（CLI 场景，设计 §16 决策 6 修订）。
 * 背景：spawn 全新临时 profile Chrome 扫码登录触发 BOSS 风控封号，CLI 已废弃该路径，
 * 改为 attach 用户日常使用的、带 --remote-debugging-port 启动的 Chrome。
 * CLI 绝不启动/杀死任何 Chrome 进程、绝不触碰任何 profile；端点探测由 CdpGateway 负责。
 */

/** 默认调试端口（可用 --cdp-port 覆盖） */
export const DEFAULT_CDP_PORT = 9222
