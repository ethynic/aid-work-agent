# 小红书本机连接器开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 本机 Chrome 扫码及会话复用，连接中心入口，Runtime 按需能力 | ✅ 完成（2026-11-16） | 真实扫码重开、独立测试及代码审查通过，待发布验收。 |

按 [本机连接器设计](../design/xiaohongshu-local-connector-design.md) 与 [共同检查点](../system/runner-desktop-runtime-integration-contract.md#小红书本机网页登录连接器) 实施。

1. 增加 Windows 小红书 Provider 和固定的扫码、打开动作。使用隔离 Profile 实测二维码，验证多次打开使用同一 Profile，另一个 Profile 不能读到首个的会话。
2. 接入 Runtime 受信清单、按需安装包与设备能力协商。连接中心具备扫码和打开入口，沿现有用户/设备授权投递和查询调用。测试错误覆盖设备离线、缺少插件、会话过期、退出后重开与不同用户的隔离。
3. 在 WSL2 Docker 按项目手册验证 Python 路由和服务、前端构建与交互。在 Windows 验证 Provider、MCP、安装包和 Runtime 协商。独立测试和只读审查覆盖服务端单独升级时的旧 BOSS / WeCom 路径。
4. 实际扫码由用户完成。验收使用独立设备及专用 Profile，扫码后关闭窗口或重启 Runtime，再从连接中心打开首页，确认仍是同一小红书账号。分别记录匿名页、测试页和真实扫码验证范围。

## 验证记录

- Windows 本机真实小红书网页。用户扫描独立 Chrome 窗口中显示的二维码后，实际 MCP Provider 返回 `opened`。关闭 Chrome 和 MCP、重启 Provider，再用原 Profile 键打开，仍返回 `opened`。未扫码的独立路径收到 `XHS_LOGIN_REQUIRED`。临时测试 Profile 已清理。
- WSL2 本地 Docker。后端定向单测 738 项和隔离 PostgreSQL 集成 15 项通过，覆盖选定设备、过期请求、设备回执、跨租户不可读及旧 BOSS 接口。Web 生产构建通过。相关组件 30 项通过，另有 4 个旧企微菜单和 Portal 快照用例，在 `HEAD` 基线上均复现。Windows Chrome 用模拟 API 验证生产构建的 1280px 和 390px 页面，覆盖扫码、打开、过期、离线。
- Windows Runtime 构建及 54 项独立验证通过。旧限制把四项插件库存误判为损坏，已改为使用受信 Provider 清单长度。装入四个签名测试插件后重启的回归由失败转为通过。本机 Provider 2 项测试、打包 15 项测试和小红书隔离签名包离线验包通过。
- 旧端兼容证据包括真实 PostgreSQL 的旧 API 形态和 Windows 历史 Provider 测试夹具。实际签名的 BOSS 0.3.27 与小红书 1.0.0 在隔离 Runtime 插件库共存，重启后保留旧 BOSS 能力和两项小红书能力。已交付的旧 Runtime 与新服务端整体联调，以及正式签名、安装和部署，属于发布验收。
