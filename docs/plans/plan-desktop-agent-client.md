# Agent 桌面客户端 v3 开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 0 | 源码调研与重新设计 | ✅ 完成（2026-10-08） | 三家开源固定版本调研完成；共用云端 Runner 已确认；方案初稿，首场景待确认；未运行测试 |
| Phase 1 | 云端 Runner 桌面对话闭环 | 📋 待开发 | 复用现有用户会话与公开 Runner API，不依赖旧 D1 |
| Phase 2 | Windows Runtime 受管集成与任务绑定 | 📋 待开发 | 先定稿 grant、固定设备、审批与文件协议；保留客户旧 Runtime |
| Phase 3 | Windows 本地文本文件闭环 | 📋 待开发 | read/search/write/edit、冲突、许可、journal 与补传；完成后方称完整桌面 MVP |
| Phase 4 | 受控命令与应用 Provider | 📋 待开发 | Shell 权限/隔离需先定稿；既有应用能力逐项接入 |
| Phase 5 | macOS 本地能力与发行验收 | 📋 待开发 | 凭证、路径、进程和包资源适配及独立真机验证 |

> 设计：[Agent 桌面客户端 v3](../system/desktop-agent-client-design.md)

> 依据：[Codex、DeepSeek Harness、Hermes 源码调研](../research/desktop-agent-harness-architecture-research.md)；执行侧协作：[Runtime 插件宿主计划](plan-runtime-plugin-host.md)。
>
> 基线：[已完成 Runner 重构](plan-agent-runner-service.md)、[构建手册](../system/desktop-agent-client-build-manual.md)
>
> 本计划跟踪桌面后续工作；不重新打开已完成的 Runner 重构。现有 Shell 属于既有部分完成成果。

## 1. 实施规则与交付边界

共同约束：[Runner / Desktop / Runtime集成契约v1.1](../system/runner-desktop-runtime-integration-contract.md)。公共Electron壳和任务binding由本工作流主导；Runtime工作流交付统一执行环境/core、插件宿主和独立Runtime管理模块。本计划Phase 2接入已有共同实现，不再复制一套Host；H1～H4定义及责任以共同契约为准。

每阶段开始前先核对最终源码与依赖，补齐本阶段契约细节并更新设计；完成后先更新本登记区，再同步索引。阶段间没有日历或人天承诺，按验收依赖推进。

本轮为纯设计文档，主控静态核对与文档检查，不执行完整三角色流程。后续业务实现涉及共享协议、鉴权、设备/进程生命周期、文件副作用和恢复，按高风险执行开发、自测、独立测试、独立 CodeReview、主控整合；只有确实边界清晰的独立 UI 工作可按常规级别处理。

不自动提交、推送或部署。服务器部署、更新脚本及容器重建/重启均等待用户当场明确部署指令；Windows/macOS 本机验证结果与服务端部署结果分别记录。

## 2. Phase 0：设计与基线

- [x] 核对兼容 Agent 壳、共用 Engine、独立 Runner 及 Web 网关。
- [x] 核对独立 Desktop Shell、旧 Coordinator/Host 空壳及旧 D1 的存在。
- [x] 核对 Runtime 的设备协议、Provider、部分 v2 outbox、Windows/DPAPI 依赖及全局 selected 规则。
- [x] 在原桌面设计路径重建方案，登记本计划与关联文档。
- [x] 阅读三个官方仓库固定 commit 的核心、工具/执行环境与用量实现，形成并登记调研报告。
- [x] 用户确认 AgentRunner 作为共用云端核心，客户机 Runtime 执行本地工具。
- [ ] 确认具体首场景、Shell 首发范围与平台优先级；当前按设计建议分期。

验证结论：仅源码与文档检查；没有本轮 build、测试、真实模型或设备执行证据。

## 3. Phase 1：Runner 桌面对话闭环

### 开发内容

- 从 Web Runner Client 中提取可复用 DTO、事件解码和 transport；保留 Web 入口与既有断言，不搬 Web 页面壳。
- 接入已有会话创建/历史、数字员工权限及 Runner 提交/查询；采用 `source=chat` 与已有用户会话。
- 新建桌面对话工作区，展示状态、进度、结果、云端附件、澄清及控制结果。
- 请求键与不可变意图在发送前保存，未确认提交可找回；认证作用域隔离、缓存有界、退出登录停止观察。
- 认证 Fetch SSE + 查询降级；窗口隐藏/切会话不触发 cancel，重启先 discovery。
- 更新 Phase C 占位文案和旧 Coordinator 设计引用，停止依赖 `requestTurn`；依赖盘点后再删除空壳。

### 验收门槛

- 实际 Desktop 页面经过主 API → 独立 Runner HTTP → worker；不能仅用兼容 Agent 的返回替身证明服务接入。
- 模拟接单响应丢失：同键重试仅一个任务；服务不可达不切旧 D1。
- 关闭页面后云端任务继续，重启找回；事件断线、乱序/过期 revision、查询失败不丢原输入。
- 澄清答复续原 runner；普通输入排队；paused/interrupted/取消请求和终态准确展示。
- 切账号/租户不重发旧请求或读取旧缓存；云端历史与投影无重复写入。
- 定向 Client/renderer 回归、frontend build、Desktop typecheck/build 与真实窗口检查；受影响 Web Runner 行为回归。

本阶段完成只能声明桌面对话已接入，不能声明本地文件/Shell 可用。

## 4. Phase 2：Runtime 集成和固定任务环境

### 开工前定稿

- grant 创建/撤销、账户绑定、有效版本与安全存储；云端与 Host 的校验责任。
- `execution_binding` 公共字段、兼容摘要、持久输入/checkpoint/ToolExecutionContext/子执行传递。
- 固定设备路径与旧 selected 路径的 dispatch、claim、结果接纳规则。
- 文件 Provider 的工具 schema、写操作回执、结果 outbox 与核对语义。
- 持久操作审批接口和等待适配，区别于已有澄清 controls；逐次许可绑定参数摘要与原 invocation。
- 本阶段所需表与迁移若有新增，登记系统/业务表分类、tenant 隔离及数据库变更。
- 与 Runtime 插件宿主计划核对 shared core、配对、插件和监管的实现归属，复用其成果，不另建调度/安装链。

### 开发内容

- 提供不加载无人值守会话任务的受管 Runtime 启动边界，Main 管理进程，renderer 仅调用窄 IPC。
- 复用设备配对，独立保存设备 token；处理旧外置 Runtime、重复实例与主体切换，不覆盖已有客户配置。
- 增加原生工作目录选择、grant 管理和脱敏状态；本机根路径不进入普通业务请求。
- 任务绑定持久化、派发与恢复重验；继承到子执行。
- 核对打包 Node 入口、Provider 依赖、路径与更新/退出 drain；不假定 process.execPath 在 Electron 内可直接复用。

### 验收门槛

- 在任务接受后更改全局 selected：新任务仍只执行原固定设备；旧工具链沿原路径正常工作。
- 跨租户/用户绑定、伪造 grant、撤权、旧授权版本和能力缺失均被可信边界拒绝。
- 第二实例、账号切换、Runtime 崩溃和应用退出不造成两个领取者，不将杀进程显示为操作取消成功。
- 更新前不丢 pending 结果；受控 Node/Provider 在安装包中实际启动且不泄露凭证。
- 设备协议、授权、公开投影与 Runtime 生命周期独立测试和审查；既有 BOSS/微信/skill-runner 按依赖回归。

## 5. Phase 3：本地文本文件 MVP

### 开发内容

- 实现文件引用与双执行器路由：本机 DeviceFileRef 与云端 file_id 清晰区分。
- 实现有界 batch read/search，逐项结果、失败及 revision 明确；避免重复小请求和全部目录上传。
- 实现文本 list/read/search、受权 write/edit；读返回 revision，写前检查权限与内容版本。
- 目录边界与安全打开、大小/编码/输出限制、原子写、journal、回执及 ACK 前结果补传。
- 明确本地修改/产物位置、冲突、需要批准和未知效果的 UI；云端模型数据传输告知。
- 原调用恢复按证据核对，不能直接重跑整个文件工具；审批与原调用、参数/授权版本绑定。

### 验收门槛

| 场景 | 业务意图 |
|---|---|
| 文件位于本机批准目录，服务器存在同名相对路径 | 必须读取/写入选定设备，不能误用云端文件 |
| 路径穿越、软链接/reparse point、目录替换及校验竞态 | 阻止访问批准范围外的对象 |
| 用户在 read 与 edit 之间改文件 | 返回冲突并保留用户修改 |
| 写前崩溃、写后 ACK 前断线、结果重复补传 | 核对原操作，副作用不重复 |
| 撤权、旧 claim、旧 attempt、批准过期/参数变更 | 失效执行权不能继续写入 |
| 结果无法证明、磁盘满/journal 失败 | 停止并显示核对或失败，不隐瞒未知效果 |
| 云端附件、服务器产物与本地结果同时存在 | 展示和路由正确，上传/保存由明确操作触发 |

使用隔离临时工作区与真实文件执行器验证，模型可用替身控制场景，但不能替换路径校验、写入与回执代码。至少一次 Windows 安装包执行“选目录 → 读文本 → 批准修改 → 实际写入 → 重启核对”闭环，再分别验证真实模型联调；记录未验证边界。

本阶段通过后才可将 Windows 本地文本文件 MVP 标为已完成开发；发行及服务器部署验收按实际授权和结果另记。

## 6. Phase 4：命令与应用 Provider

- 先完成 Shell 权限与隔离设计，明确可验证的 OS 边界；没有隔离时据实按当前用户系统权限逐次授权。
- 精确 command/args、cwd、环境、网络/子进程权限、超时与取消；输出有界，环境按批准白名单传递。
- 受信 Provider 按能力登记、安装和启用；沿既有业务许可与计费，不能让 MCP 自报 schema 获得权限。
- 验证批准参数篡改、命令逃逸假设、进程树取消、未知副作用、Provider 崩溃与结果补传。
- BOSS/微信等共享桌面资源按原锁机制仲裁，保证旧自动化任务兼容。
- 支持受权脚本一次完成多步确定性处理；模型判断仍回 Runner，云端费用不由设备自报。完整 PTC 在性能需求证实后独立细化。

通用 Shell 与具体应用可拆成独立交付；任何单项成功不表示其他能力已通过。

## 7. Phase 5：macOS 与发行

- 替换 Windows-only 凭证/平台假设，验证路径、符号链接、安全打开、进程/Provider、系统授权和签名包资源。
- macOS arm64/x64 按实际承诺平台分别验证；Windows 结果不能替代 Mac 证据。
- 安装、升级/退出时操作未完、回执待 ACK、最低版本阻断、协议不兼容恢复分别验证。
- 正式签名、更新源和灰度验收独立记录；构建成功不等于发布/部署成功。

## 8. 验证与登记要求

验证仅覆盖当前阶段和真实受影响依赖。后端按 `.claude/rules/testing.md` 与项目测试入口选择定向测试；前端使用现有 scripts，源码变化运行 frontend build；客户端使用各 package 的 typecheck/build/test，涉及进程与包资源增加真实启动检查。

独立测试与 CR 的交接包含：范围、最终文件、业务意图、风险、命令、退出码、通过/失败/跳过及未验证项。修复后重跑受影响检查；同一状态已有有效证据不机械重跑。

性能验收分别记录模型、排队/领取、Provider 启动/执行、回执接纳、输出字节、模型轮数和 token。用相同结果比较逐项读取与 batch；未测量前不承诺延迟或成本收益。设备快路径不作为现有协议首版替换要求。

各阶段在本计划记录实际结果、问题和遗留；索引只保留一句话状态。全部约定范围完成后再移动到 `ideas_finished.md`。如分期取消或另行立项，先调整设计和计划，再同步索引。

## 9. Desktop / Runtime 共同边界登记

| 检查点 | 主导 | 状态 | 本批写入者/记录 |
|---|---|---|---|
| H1 管理port与wire schema | Runtime | [见Runtime计划](plan-runtime-plugin-host.md#6-desktop--runtime-共同边界登记) | 桌面提供consumer adapter/fake，接入同一管理语义 |
| H2 固定设备binding/grant/审批 | Desktop | 📋 待开发 | 本批唯一写入者与文件清单实施前登记，Runtime提供核验adapter |
| H3 公共壳与实例监管 | Desktop | 📋 待开发 | 接线统一core/独立Runtime模块，保留已有实例身份与生命周期差异 |
| H4 插件登记与产物 | Runtime | [见Runtime计划](plan-runtime-plugin-host.md#6-desktop--runtime-共同边界登记) | 桌面展示与模型输入按共同artifact契约适配 |

2026-10-08：共同契约v1.0已登记到双方设计/计划和AGENTS；wire schema、代码及双向兼容验证未完成。双方阶段编号不决定底层实现归属。

2026-10-08：用户向Runtime工作流转交桌面定位，共同契约补充为v1.1，明确统一任务执行环境、本地批量计算和审批/计费边界；本计划原文件/程序分期保持，具体schema仍先经H1～H4冻结。
