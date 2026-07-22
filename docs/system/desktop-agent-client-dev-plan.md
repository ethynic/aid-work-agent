# Agent 跨平台桌面客户端开发计划

> 日期：2026-07-14
> 状态：🔧 部分完成（Phase 0～3 Windows 完成；Phase 4 被浏览器 Phase 3R 真实门禁阻塞；Phase 5 Windows 自动更新代码与开发包完成，正式签名/发布源/真升级待外部条件；macOS 各 Phase 延后验证）
> 设计基线：[desktop-agent-client-design.md](./desktop-agent-client-design.md)
> 流程：每个非平凡 Phase 严格执行开发 → 独立测试 → Code Review；不自动提交。

> 主从原则：Agent Desktop 是主产品，browser runtime 是可选模块。浏览器方案必须适配桌面主架构；任何浏览器依赖、故障或发布节奏不得阻塞或削弱 Agent 主链路。
>
> 验证策略（2026-07-14 用户确认）：当前开发以 Windows 自动化/真机门禁推进；macOS 不阻塞各 Phase，逐 Phase 保留同等验收清单，后续在 Mac 设备单独补验。不得把 Windows 结果表述为 macOS 已通过。

## 1. 完成定义

Web 构建和 Python 服务端能力无回归；Windows/macOS 签名客户端可安装、登录并完成 Agent 全链路；桌面包从构建产物中排除 `/portal`；浏览器 runtime 集成同一客户端且七类终态零残留；纯 Web 用户仍可使用 server browser；自动化与真机验收均有记录。

## 2. Phase 总览

| Phase | 交付 | 状态 | 退出门禁 |
|---|---|---|---|
| 0 | 基线清单、契约测试、技术 Spike | ✅ Windows 完成；macOS 延后验证 | Windows 自定义 scheme、CORS、SSE、上传下载通过；macOS 清单已保留 |
| 1 | 路由/入口拆分与 Portal 构建隔离 | ✅ Windows 完成；macOS 延后验证 | Web 全功能不变；Desktop 包不含 Portal-only route/layout/admin API |
| 2 | Electron 壳与平台适配层 | ✅ Windows 完成；macOS 延后验证 | Windows 真壳、真实 renderer、单实例、安全边界和离线恢复通过 |
| 3 | 凭证、安全、文件与系统集成 | ✅ Windows 完成；macOS 延后验证 | Windows safeStorage、受控下载/外链、深链与零敏感残留门禁通过 |
| 4 | 可选浏览器 runtime 适配 | ⏸ 等待浏览器 Phase 3R | 统一 Executor contract 与进程回收通过；关闭模块后 Agent 全功能正常 |
| 5 | 签名、更新、CI 与灰度 | 🔧 Windows 本地实现完成；正式发布待证书/发布源 | 更新状态/UI/受控配置/发布元数据和 0.0.2 unsigned dev NSIS 通过；签名、真实升级/回滚尚未验证 |
| 6 | 全量回归、真机验收与文档收口 | ⬜ | 完成定义全部满足并归档索引 |

## 3. Phase 0：基线与 Spike

### 2026-07-14 检查点

- 新增 `clients/agent-desktop/` 独立 Electron + TypeScript Spike，未修改现有 Web 路由或 Python API。
- Windows 自动门禁通过：`npm run typecheck`、`npm test`（5/5）、`npm run build`、`npm run smoke`（`SPIKE_SMOKE_PASS`）。
- smoke 已实际验证 `aidagent://app` history fallback、Portal/traversal 拒绝、精确 `Origin: aidagent://app`、三段流式响应、multipart marker 和 65536 bytes 下载；结束后 Electron 进程回到基线。
- Electron 安全基线已锁定：`nodeIntegration=false`、`contextIsolation=true`、`sandbox=true`、`webSecurity=true`；preload 不暴露 `ipcRenderer`。
- 独立测试与 Code Review 已完成；修复 URL 点路径折叠绕过、跨平台 Electron 可执行路径、SSE/multipart smoke 假阳性和 fixture/子进程清理问题。
- 官方 npm registry 审计为 0 vulnerabilities；默认 npmmirror 不支持 audit API（404），属于环境限制。
- macOS arm64/x64 按用户决定延后到 Mac 设备逐 Phase 补验，不阻塞 Phase 1；真实 Python API、麦克风、PDF/Office 和大文件落盘按后续 Phase 验证。

### 工作

- 盘点桌面需保留的 Agent 路由、Portal-only 组件、所有 API base 拼接和直接 Token 存取点。
- 固化 Web 路由清单与构建产物快照，增加 `/portal`、`/t/:tenant_id`、根登录的回归测试。
- 最小 Electron Spike 验证：自定义 standard/secure scheme + `createWebHistory`、FastAPI 精确 CORS、流式 Fetch/SSE、FormData 上传、大文件下载、音频录制、PDF/Office 预览。
- Windows 11 x64、macOS arm64 至少各验证一次；macOS x64 由 CI 构建验证，最终 Phase 做真机。
- 决定自定义 scheme Origin 是否可直接 CORS；失败则锁定 main-process 固定域 transport，不允许用 `webSecurity: false`。

### 验证

- 形成可执行 Spike 测试记录，明确每个 Web API 在桌面协议下的行为。
- 原 `npm test`、`npm run build`、Python 关键测试记录为基线。

## 4. Phase 1：前端双入口与路由隔离

### 2026-07-14 Windows 检查点

- 完成 Web/Desktop 双入口和路由拆分；Web 继续组合 Agent 与 Portal，Desktop 只引用 Agent 路由并拒绝 `/portal/**` 与 Web-only `/subagents`。
- 将租户端公共壳拆为 `TenantLayout`，Desktop 不再间接打入 `PortalLayout`、`DigitalEmployeeManager` 或 `adminSubagent`；Web 的 Portal 页面和原 URL 保持可用。
- Windows 门禁通过：20 项定向测试、`vue-tsc`、Web production build、Desktop production build全部成功；Desktop artifact 校验通过（51 个 manifest entry、406 个 bundled module）。
- Desktop 深链使用根路径资产地址，`aidagent://app/t/:tenant_id/**` 不会错误解析到租户子目录。
- 共享 tenant API 中历史 `/portal` 与 `portal_token` 兼容分支尚未迁移；按计划由 Phase 3 的 `CredentialStore`/API resolver 收口，最终安装包仍执行零残留扫描。
- macOS 构建与深链验证保留到 Mac 设备补验，不作为当前 Phase 2 的阻塞项。

### 工作

- 提取共享 bootstrap、`agentRoutes`、`portalRoutes`。
- 保持 `main.ts` 为 Web 入口，新增 `main.desktop.ts` 和独立桌面 HTML/Vite build。
- 将 API URL、平台能力和 credential access 建立 adapter 接口，首期 Web adapter 行为不变。
- 新增桌面 `/portal` 深链拒绝与 fallback。
- 新增解包/静态依赖检查，禁止 Portal-only 组件进入桌面 artifact。

### 验证

- Web 路由快照与现状一致，Portal 登录、租户登录和 Agent 对话组件测试通过。
- Web production build 通过。
- Desktop build 只含允许路由；解包扫描无 Portal-only route/component。

## 5. Phase 2：Electron 基础客户端

### 2026-07-14 Windows 检查点

- Electron 壳已加载真实 `frontend/dist-desktop`，不再使用 Phase 0 静态 renderer；hashed assets、Agent history 深链与缺失资源 404 均由自定义 scheme 稳定处理。
- 完成单实例、加载期第二实例聚焦、窗口状态可见性恢复、系统主题、窗口内缩放和最后窗口关闭即退出；本阶段保持无托盘，避免后台残留。
- 安全门禁保持 `nodeIntegration=false`、`contextIsolation=true`、`sandbox=true`、`webSecurity=true`，并增加精确 API origin CSP、主框架导航/新窗口/WebView/权限默认拒绝和脱敏错误日志。
- preload 仅暴露冻结、版本化的只读 runtime；API base 由 main 严格校验后运行时注入。兼容现有两种 Vite API 基址变量，但 Phase 3 仍需统一 API resolver。
- Windows 三智能体流程及主控终检通过：前端 23 项定向测试、类型检查、Web build；客户端 12/12 测试、Desktop build/artifact verifier（51 entries、407 modules）及真实 Electron smoke 全绿，输出 `AGENT_DESKTOP_SMOKE_PASS`，结束后工作区 Electron 进程为 0。
- smoke 覆盖真实 Agent renderer、精确 CORS、health、投诉 legacy API、三段流式响应、FormData、64 KiB 下载和 history；缺失/非法 API 配置均以退出码 1 失败且日志不泄漏凭据。
- 已知后续项：下载路径仍有 `window.location.origin` 拼接；connectivity banner 清理与并发代次保护可在适配层收口；正式 asar 资源路径属于 Phase 5。macOS 按用户决定延后到 Mac 设备补验。

### 工作

- 新建 `clients/agent-desktop/`：main、preload、renderer build 接入、窗口/托盘/单实例、日志和崩溃边界。
- `nodeIntegration=false`、`contextIsolation=true`、sandbox、CSP、导航/窗口/权限拦截默认启用。
- 实现运行时 API 地址、健康检查、离线/版本过低页面和安全重连。
- 适配全局快捷键仅限窗口内；支持系统主题、缩放、窗口状态恢复。
- 不实现浏览器 runtime，先完成完整远程 Agent 能力。

### 验证

- Windows/macOS：安装开发包 → 登录 → 新建会话 → SSE 回复 → 中断 → 历史会话 → 退出。
- 服务端不可达、401、429、5xx、SSE 中断均有明确状态且可恢复。
- Electron 安全配置自动化断言通过。

## 6. Phase 3：凭证、文件与系统能力

### 2026-07-14 Windows 检查点

- 建立 Web/Desktop `CredentialStore` 契约：Web 保持原 localStorage key 行为；Desktop 启动时从 Electron `safeStorage` 加密文件 hydrate 到内存，同步读取、异步白名单 IPC 持久化，不回退明文 localStorage。
- Desktop 只允许 Agent/demo/tenant 凭证 key，拒绝 `portal_*` 和任意 key；tenant key 最长 128 字符、value 最大 64 KiB。写入/删除只有在磁盘成功后才更新内存，删除失败保留登录态并报错，避免旧密文下次启动“复活”。
- 完成 Agent/Tenant 主认证链与 Desktop 可达 legacy token 读取迁移；Desktop 自动 artifact 门禁扫描 `portal_token`、Portal credential、管理员/Portal-only import 和敏感 localStorage 直接访问，最终均为 0。
- 增加版本化白名单 IPC：仅当前主窗口、主 frame、合法 `aidagent://app` Agent/tenant 页面可调用；preload 不暴露通用 IPC。
- 外链仅允许无凭据 HTTPS；下载仅允许配置 API origin，手动同源重定向最多 5 次，声明/实际流量均限制 100 MiB，同目录临时文件后原子替换；dialog 取消、非 2xx、超限和 redirect body 均安全收口。
- deep link 支持冷/热启动 pending 队列，只接受 Agent/tenant route，拒绝 Portal、traversal、query/fragment；safeStorage/hydrate 失败显示固定本地安全失败 UI，不泄漏底层异常。
- Windows 三智能体与主控终检通过：前端 33 项 scoped tests、typecheck、Web build；客户端 23/23、Desktop artifact（51 entries、409 modules）、真实 smoke 全绿并输出 `AGENT_DESKTOP_SMOKE_PASS`；`git diff --check` 通过，工作区 Electron 残留为 0。
- 后续项：下载当前最多在内存缓存 100 MiB，未来可流式直写临时文件；safeStorage 文件格式升级前需增加 schema version；协议注册和安装后 deep link 属于 Phase 5 真安装包门禁。macOS 延后补验。

### 工作

- 将认证代码迁移到 `CredentialStore`；Web localStorage adapter 与 Desktop safeStorage adapter 跑同一 contract suite。
- 清理 API base 双变量和 `window.location.origin` 拼接；统一上传、预览、下载 URL resolver。
- 实现受控文件选择/保存、系统浏览器外链、`aidagent://` 深链和 Web 一次性拉起 ticket。
- 增加 Token/正文日志脱敏、Profile/缓存清理和退出时短期会话撤销。

### 验证

- 磁盘扫描不出现明文 access token、短信验证码、模型 Key。
- 上传图片/文档、下载、PDF/Office 预览、麦克风授权在双平台通过。
- 恶意导航、任意 IPC、任意下载路径、非白名单协议/域名均被拒绝。

## 7. Phase 4：集成浏览器 runtime

### 进入条件

浏览器执行架构 Phase 0-3 的 RunManager、Executor、人工接管和 suspend/resume 契约已稳定；若其开发尚未完成，本 Phase 不提前复制临时代码。

2026-07-22 真实环境发现 Redis 4.3.0 不支持当前 resume Stream，且验证码登录页未确定性触发人工接管。进入条件因此明确为浏览器 Phase 3R 全部门禁通过：PostgreSQL lease 队列、确定性人工需求检测、跨事件循环测试隔离及双 Gunicorn worker 真实 E2E 均有证据；仅“代码已合并”不满足条件。

本 Phase 是 Agent Desktop 已稳定后的可选增强，不是桌面 MVP 的发布前置条件。浏览器工程必须适配 Phase 0～3 已确定的认证、更新、安全和生命周期边界。

### 工作

- 在 `clients/agent-desktop/browser-runtime/` 实现内置可选模块；不创建第二个客户端工程或 shell。
- 增加 browser runtime feature flag、延迟加载和故障隔离；关闭/缺失/崩溃时不得影响桌面启动、登录、对话和非浏览器工具。
- 实现 `browser/1.0`、短期 desktop browser session、Agent Web launch ticket、DesktopRuntimeExecutor、Profile、Worker 和进程托管边界。
- Desktop 登录 presence 自动注册 browser capability；Web 可用一次性深链拉起同一应用。
- Vue UI 显示 browser run、人工接管、继续/取消和 Agent Desktop 更新状态。
- 复用 Agent Desktop 的 CredentialStore、installation identity、API 配置、签名安装包、更新器、托盘和设置页；browser runtime 不拥有独立发布状态。

### 验证

- Python/TypeScript golden fixtures 和 local/desktop runtime executor contract tests 通过。
- Windows/macOS 可见浏览器、Profile 复用、验证码人工处理和原 run 自动续跑通过。
- success/error/cancel/timeout/app quit/断网/update 七类终态进程回基线。
- Web 未安装 Desktop 时，server headless 和网页人工接管回归通过。
- 禁用 runtime、Worker 启动失败、协议版本不兼容三种情况下，Agent Desktop 主链路回归全部通过。

## 8. Phase 5：发布、签名和更新

### 2026-07-14 Windows 开发包检查点

- 引入 electron-builder 26，生成 Windows x64、user-scope NSIS；配置固定 `appId` 与 `aidagent` protocol，asar 只包含编译后的 main/preload/renderer 运行时文件。
- 开发渠道明确为 `development-unsigned`；release 缺 `CSC_LINK`/`WIN_CSC_LINK` 会在清理产物和调用 builder 前失败，Authenticode 非 `Valid` 也不得发布。unsigned dev 包不能被后处理伪装成 release。
- 标准 `npm run package:win:dev` 必须完整重跑：先清空 release、typecheck、26/26 tests、Desktop artifact verifier、electron-builder、SBOM/audit/license/manifest 和 ASAR verifier；已删除可复用旧 installer 的 `--postprocess-existing` 旁路。
- manifest 记录版本、平台/架构、commit/dirty、installer size/SHA-256/签名状态和完整构建输入 SHA-256；manifest、CycloneDX SBOM、npm audit、license 均原子写入并严格校验。npm audit 明确走官方 advisory endpoint，镜像 404 不再被误报为 0 漏洞。
- Windows 主控最终产物：`AID-Work-Agent-0.0.1-win-x64-dev-unsigned.exe`，100,215,084 bytes，SHA-256 `20555b354fe2d506d98d61c7eef98625e073b5c3eac6b3cde66996c41ee37979`，Authenticode `NotSigned`，与 manifest 一致；release 目录已 gitignore。
- packaged `win-unpacked/AID Work Agent.exe` 在临时 API fixture 与隔离 userData 下输出 `AGENT_DESKTOP_SMOKE_PASS`，退出码 0，临时目录与 Electron/packaged 进程残留为 0。
- 此检查点当时仅完成可审计的 Windows 开发安装包，不代表正式可分发；2026-07-20 已补齐自动更新代码与本地门禁，但正式 `.ico`、代码签名证书、真实更新源，以及安装/卸载、协议注册、升级/回滚真机验证仍未完成。单独存在 `.blockmap` 不能作为生产升级已验收的证据。

### 2026-07-20 Windows 自动更新开发检查点

- 新前端提交包含 `MenuSidebar.vue`、对话组件和 `useAgent.ts`，均属于 Desktop renderer 输入，必须重新构建安装包；同版本 `0.0.1` 重打包不能触发升级，下一测试版本需提升至 `0.0.2`。
- 接入 `electron-updater` + NSIS Generic Provider；生产更新地址由 `AID_AGENT_UPDATE_BASE_URL` 在构建时写入包内配置。缺地址、非 HTTPS、未打包或 development-unsigned 渠道全部 fail-closed 为 `disabled`。
- main 进程维护更新状态机，关闭自动下载和退出时自动安装；preload 仅暴露状态订阅、手动检查、下载、重启安装。左下角仅 Desktop 显示更新提示，下载完成必须由用户确认重启。
- 本地自动化覆盖配置校验、状态转换、IPC sender 校验和 Web 无桥接降级；开发包只验证 UI/状态/产物，不连接或伪造生产更新源。
- 外部阻塞项：生产 HTTPS 更新目录、Authenticode 证书/CI Secret、真实的旧版→新版与坏签名/回滚真机矩阵。在这些条件完成前不得标记生产自动更新完成。
- 三智能体与主控终检完成：Desktop 37/37、Frontend updater/runtime 7/7、Web 与 Desktop production build、开发包 verifier（326 ASAR files）、packaged smoke 和零残留均通过。最终开发包 `AID-Work-Agent-0.0.2-win-x64-dev-unsigned.exe` 为 100,415,947 bytes，SHA-256 `36e017ea473486ee3b59288fe8a7cf95b43c6abfb9b3eda6ecf87a4d17b0ab70`，Authenticode `NotSigned`，包内 API 为 `https://agent2.aidingyi.cn/api`，更新源为 `null`。

### 工作

- electron-builder：Windows x64 NSIS；macOS x64/arm64 universal DMG + ZIP 更新产物。
- Windows 签名、macOS Developer ID + Hardened Runtime + notarization；CI `forceCodeSigning`。
- 分平台 CI matrix，构建产物生成 SBOM、SHA-256、版本清单并上传受控发布源。
- staged rollout、强制最低版本、下载后验签、失败回滚和 browser protocol 兼容策略。

### 验证

- 全新安装、覆盖升级、跳版本升级、降级拒绝、损坏包/错误签名拒绝通过。
- Windows SmartScreen/签名信息、macOS Gatekeeper/notarization 真机通过。
- 更新过程中进行中的 Agent/browser run 有明确延后或安全取消策略。

## 9. Phase 6：全量验收与收口

### 自动化

- Frontend 全量 tests + Web/Desktop production builds。
- Python 受影响模块 unit/integration + import/启动检查。
- Electron unit/integration/e2e；artifact Portal exclusion；依赖漏洞和许可证检查。
- Browser runtime contract/lifecycle/security/e2e。

### 真机矩阵

- Windows 10 x64、Windows 11 x64。
- macOS 13+ Intel、macOS 13+ Apple Silicon。
- 企业代理网络、断网重连、系统睡眠/唤醒、多显示器、应用强退、服务端滚动升级。

### 文档

- 更新桌面设计/计划状态、部署和用户安装指南。
- 同步浏览器 v2.7 设计与计划的 Phase 4 跨项目依赖和联合验收状态。
- 完成后将本条从 `docs/ideas.md` 移至 `docs/ideas_finished.md`。

## 10. 建议排期与依赖

在 1 名熟悉现有前端的开发者 + 1 名 Electron/发布工程师条件下，建议 6～9 周；浏览器 RunManager/Executor 未完成会阻塞 Phase 4，但不阻塞 Phase 0～3。

推荐顺序：先交付不含本地浏览器能力的 Desktop MVP（Phase 0～3），验证双入口和服务端兼容；再接入已经通过服务端门禁的 browser runtime，避免桌面壳与浏览器架构同时失稳。

## 11. 不允许的范围漂移

- 不修改 `/portal` 业务功能。
- 不把 Python/数据库/模型 Key 打进客户端。
- 不用关闭 Web Security、启用 Node integration 或暴露通用 IPC 赶进度。
- 不复制 Agent UI 或 browser runtime 形成长期双实现。
- 不在没有 Windows/macOS 真机证据时标记完成。
