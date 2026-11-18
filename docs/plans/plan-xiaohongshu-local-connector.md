# 小红书本机连接器开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 本机 Chrome 扫码及会话复用，连接中心入口，Runtime 按需能力 | ✅ 完成（2026-11-16） | 2026-11-18 配套包安装和本机容器端到端验收通过。 |

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

## 2026-11-18 同步 master

功能分支从 `cd5cfe72` 合并 `origin/master` 的 `52090f05`，以未提交合并供独立测试和审查。8 处冲突保留 master 的插件声明、并行领取、微信原生读取和企微过期监控读取机制，并保留小红书的设备归属、超时和免费配置动作。

小红书包升至 1.0.1，补充新版 Runtime 所需的 v1 全串行工具声明。两项原工具的参数、结果和浏览器 Profile 行为沿用 1.0.0。能力上报消费当前已验签清单，产品信任和库存数量沿 master 的信任配置来源。

开发自测中，WSL2 Docker 后端 local_tools 单测 826 项、Web 构建通过。Windows Core 插件声明/安装/验签 37 项、Runtime 声明/并行/能力/信任 39 项和打包测试 15 项通过。小红书合成浏览器用例在并发构建时首次报页面状态未确认，独立重跑通过，需独立验证继续核对。合并带入的 `plan-test-suite-speedup.md` 原有两处空白使全局 cached diff 检查非零，本次冲突文件检查通过。

## 2026-11-18 本机安装验收

- 打包源码为 `3792969b`，父节点为小红书分支 `cd5cfe72` 和当次最新的 `origin/master` `52090f05`。Runtime 1.0.3 验收安装器与小红书插件 1.0.1 合成一个 ZIP。Runtime 资源清单、插件签名和 ZIP SHA-256 核验通过。本机签名根仅登记于验收构建副本。
- 合并后独立复验通过。Web 和 Runtime 构建、Web 14 项、后端定向 108 项、隔离 PostgreSQL 31 项、Windows 定向 79 项。
- 运行真实验收安装器到隔离目录，安装后的主程序、应用资源、Runtime Host 和信任根与打包资源摘要一致。用安装包内的 Node 与 Host 实际配对、导入已验签插件并上线。导入以本地验收脚本提供固定的离线包路径，Runtime 桌面文件选择对话框不在本轮验证范围。
- 从 Windows Chrome 的连接中心实际操作，使用 WSL2 Docker 本地 Web、API 和专用 PostgreSQL。用户本人扫描本机 Chrome 二维码后打开成功。停掉 Runtime 后确认专用 Chrome 进程数为 0。重新启动 Runtime，再在连接中心点「打开小红书」，直接返回已登录。数据库记录为 `qr_ready → opened → opened`，同一设备、同一 Profile。
