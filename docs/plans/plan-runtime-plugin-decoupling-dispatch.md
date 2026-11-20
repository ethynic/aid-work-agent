# Runtime 插件解耦与并行接单开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
| --- | --- | --- | --- |
| Phase 1 | 插件工具声明与 Runtime 通用接入 | ✅ 完成（2026-11-12） | commit 8e47bd54：三 CLI buildManifest 输出 tool_declaration（仅企微两本地库读工具 parallel）、boss 注解审计修正 5 处对齐写集合；Runtime 宽松解析 +「需要升级插件」门禁 + 派生清单与 TRUSTED 取交集锚定保持现网受信面 + parallel_tools/tool_declarations_v1 心跳上报；未知插件零改动接入有端到端测试。五包 typecheck 0，定向+回归绿（基线失败 stash 对照一致）；CR 4 项记录不阻断 |
| Phase 2 | 服务端按类型派发和 Runtime 并行执行 | ✅ 完成（2026-11-12） | commit 503774f2：claim 可选 parallel_only（jsonb 过滤，缺省 SQL 逐字节不变）+ 心跳 parallel_claim 协商 + 领取状态机（串行占用/后台并行/全部在途 drain）+ 并行跳桌面锁与 Provider 并发 lane。真实 PG 16 集成 8/8（scratch 最小 DDL）、服务端单测 743 过、runtime parallelClaim 7/7、五包 typecheck 0；CR 无 P0/P1，2 P2 记录（支持标记翻转窗口锁兜底、parallel 限只读已补契约）；fail-loud 提前返回判可接受（租约 unknown 兜底不变） |
| Phase 3 | 声明即调度授权：CLI 声明收敛 + Runtime 删交集锚定 + 服务端声明分流 | ✅ 完成（2026-11-12） | 契约 2.20 修订（e0ae04f2）落地：wecom 声明白名单 12 工具（chat_search 等 5 个内部命令不声明）、weixin 声明 6 个 v1 工具（session_observe/v2 发送不声明），两 CLI 清单 tools 与 schema_digest 不动；host-core deriveProviderManifest 受信面=声明全集（删交集锚定，未知插件同一逻辑）+ 心跳新增 declared_tools（与 parallel_tools 同源同过滤）；服务端 claim 行级放行与 device_ready_error 声明分流（未声明覆盖的 provider 与 NULL 行回退目录，旧设备逐字节不变）。服务端单测 765 过（新增 22）、真实 PG 16 集成 5/5（声明端到端）+ Phase 2 集成 8/8 回归、host-core 129/130 与 runtime 340/343（基线失败 stash 对照一致）、三 CLI manifest 测试绿、五包 typecheck 0 |
| Phase 3-R2 | 复审修复：执行通道协商门禁 + 长轮询脱挂解耦 + 正式接入路径 | ✅ 完成（2026-11-12） | 外部复审三处修复 + 执行语义升级：①pollLoop 执行通道同受 serverParallelClaim 门禁（旧服务端下声明并行作业按串行执行，与后续串行作业无并发重叠）；②hasParallelTools 判定——无并行工具设备完全沿用原串行循环（执行中零领取请求、第二条 <1s 启动），解耦模式串行 settle 时在途 parallel_only 长轮询脱挂（detachedClaims 入账、drain 等待其与派生 runner 全部结束），串行队列 <1s 拿到下一条，脱挂轮询返回的并行作业照常后台执行；③Runtime 安装白名单由产品信任配置 trust roots 覆盖 provider_id 并集驱动（ai.aidwork. 命名空间，无信任配置维持 FIRST_PARTY 默认），服务端 get_provider_keys_for_device 并入声明 provider_key（目录外新插件可领，旧设备逐字节不变；get_provider_key_for_device 单数版仅审计不改——default_provider 兼容字段语义保持）。runtime parallelClaim 10/10（新增 4 用例）、productTrust/pluginStore 第 4 方身份安装用例、服务端单测 +2、集成 +1（新插件 provider 领取）；契约 2.20 补执行语义与接入驱动三句；CR 通过（无 P0/P1）：P2 留档 1 项——serverParallelClaim 中途回滚窗口内脱挂轮询返回作业落串行 lane，可与 attached 空参数领取的串行短暂并发（桌面锁 120s 超时/DESKTOP_RESOURCE_BUSY 可重试兜底，租约记账一致、无死锁丢作业；触发需服务端能力标记回滚且 20s 轮询在途，与 Phase 2 翻转窗口锁兜底 P2 同源，较修复前同窗口行为严格更安全）。CR 复跑：runtime 定向 13/13、全量 344/347（2 基线 stash 对照复核一致）、host-core pluginStore 20/20、服务端声明单测 24/24、typecheck 0 |
| Phase 3-R3 | 复审修复：并行领取 lane 专职化 + 连接迁移逐包信任迁移 | ✅ 完成（2026-11-12，CR 无 P0/P1，commit 09d8e360） | 外部复审两处修复：①pollLoop 删除脱挂机制（每条串行 settle 遗留一条在途 parallel_only 轮询，连发五条；脱挂轮询失败进共用退避拖住串行实测 0.76s），改为专职并行领取通道——任一时刻至多 1 个在途 parallel_only 轮询跨串行作业复用，串行占用期间主循环零领取请求只等 settle，失败走 lane 私有退避不触碰主循环退避，DeviceRevoked 沿 fail-loud 停整个循环，stopClaiming/shutdown 经共用 claimController abort 通道，drain 等待 claim+lane+全部 inFlight；②desktop migratePlugins 移除 3 条上限与三家写死名单（第 4 插件重配对全丢缺陷：inventory 4 条整体跳过 → 新连接零插件），放行名单改信任配置派生（同包 productTrust providerIdsOfTrust，与 hostEntry 同目录分发），verifyOfflinePackage 与 PluginStore storeOptions 均传 providers 验签闭环，逐包容错 warn+continue 保留，安装时信任配置与验签规则沿用。测试先行红→绿：parallelClaim +3（503 不拖串行 <1s、5 条串行 parallel_only 峰值并发 1 且完成回落 0、连续 503 私有退避串行节奏稳定）、runtimeInstallation +3（信任配置第 4 方身份 4 条全迁移含停用保留、未授权身份跳过其余迁移、3 条旧行为不变）；回归：runtime 全量 347/350 与 host-core 129/131（基线失败 stash 两态一致：runtime 进程清理 SIGINT/v2 写 shutdown、host-core wecomSendStatus 族）、desktop 全量 111/111 + typecheck 0、服务端 local_tools 单测 767、真实 PG 集成 14/14、五包 typecheck 0；契约 2.20 复审段「串行结束不等待在途长轮询」与「接入驱动」句同步 lane 与逐包迁移语义 |
| Phase 4 | 模型工具定义接入与自动同步 | 🔧 进行中 | 自动同步独立复测通过，待 agent2 网页验收 |

## 实施

- Phase 1 为新版 BOSS 和企微 CLI 增加带版本的工具声明，保留旧 Runtime 使用的插件包和调用接口。新版 Runtime 从签名包读取声明，将按插件名或工具名写死的配置和业务判断移到插件声明或对应业务代码。用 BOSS、企微和一个 Runtime 未预先认识的测试插件验证接入。
- Phase 2 在现有领取接口增加按类型领取。服务端负责待领状态和原子领取，Runtime 负责按当前占用继续领取并立即执行。把并行命令从本机串行限制中放开，沿用现有取消、进度和回执。
- 源码实施前在共同契约登记声明与领取接口的版本、生产端和消费端示例。插件负责自身声明，Runtime 负责读取、上报和执行，服务端负责记录并选择任务。按既有能力协商兼容已交付组合。（已登记：[共同契约 2.20 检查点](../system/runner-desktop-runtime-integration-contract.md)，2026-11-11）

## Phase 4 已确认事项

- Runtime 通过现有认证心跳报告已启用插件的清单摘要，服务端只请求缺失或变化的清单，Runtime 自动补报本机安装包的原始 `envelope.json` 和 `runtime-manifest.json`。
- 服务端验签并核对插件身份与完整清单摘要。使用已打包的浏览器 `0.1.0` 验证。 [设计](../system/runtime-plugin-decoupling-dispatch-design.md#runtime-自动同步签名清单)
- Runner 读取当前用户已选中的唯一 Runtime，工具定义取该设备当前启用的插件清单，调用派给同一台设备。不同 Runtime 的清单各自同步。
- 服务端同步的工具定义取自目标 Runtime 安装使用的签名包；设备可执行工具沿用现有 `declared_tools` 上报。
- 内部登录会话自动装配所选 Runtime 的已启用插件工具，沿用现有权限。各 Agent 共用此接入路径，外部会话沿用渠道授权。
- Runner 读取同一份已同步的清单，将原生工具定义交给模型，并用通用入口派发调用。浏览器作为本阶段验收插件。
- 沿用当前 Runtime 的任务派发、执行和标准回执。失败时按既有行为说明原因，用户可以继续会话。浏览器插件与其他插件使用同一套契约。

### Phase 4 实施步骤

1. 扩展心跳，验证首次同步、重复心跳、升级及停用后的设备清单，用现有浏览器 `0.1.0` 签名包核对服务端验签和工具定义读取。 [共同契约](../system/runner-desktop-runtime-integration-contract.md#runtime-插件签名清单同步)
2. 接上 Runner 通用工具装配，从当前用户所选设备读取已启用的插件工具，生成模型使用的工具定义。
3. 接上现有任务派发和结果回传，核对定义、执行设备和回执对应。定向回归权限隔离、设备切换以及既有本地工具流程。
4. 独立测试和评审通过后，按项目部署授权流程进入 agent2 的真实网页验收。更新 Runtime 和服务端，沿用当前 CLI 与浏览器插件包。

本地验证覆盖已交付浏览器包的 35 项工具定义。最终独立复验 125 项单测及 2 项 PostgreSQL 集成通过，验证 Runner 向模型提供原生定义、把调用派给选中设备、返回部分失败回执并继续会话。已入队调用继续观察原设备，未入队的调用按现有失败结果继续会话。公开进度、执行详情和诊断记录保留操作信息，参数值留在原执行链路。额外旧 BOSS 恢复和取消的 6 组超时在基线 `57006423` 同样失败。

自动同步使用真实签名包读取 35 项定义，重复心跳只报摘要。最终独立复验通过 177 项服务端单测、15 项 PostgreSQL 集成、31 项 Runtime 和 29 项共享 Host 测试。覆盖模型响应恢复、停用与降级回收旧队列、新旧长轮询同时领取、同包领取和旧静态工具。额外运行的 3 项旧 Device API 时间测试在基线 `f542e41a` 同样失败。

信任问题会阻止插件工具向模型发布。服务端拒收清单后，Runtime 会按现有心跳节奏重试。正确配置发布者信任后，成功同步的清单只再发送摘要。

### Phase 4 agent2 发布准备

1. 发布 agent2 服务端并更新本机 Runtime。主 API 接收清单时使用现有 `requirements.txt` 中的 `rfc8785` 生成规范 JSON。Runner 直接验证已保存的签名数据，使用原有加密库即可。服务端快捷更新脚本沿用上游 `5ff97350` 的依赖安装流程，对同项目、同镜像的容器同步更新 Python 依赖。
2. 服务端配置 `AGENT_RUNNER_PLUGIN_TRUST_ROOTS` 指向该环境的公开发布者信任文件。agent2 验收用随仓库发布的 `/app/clients/runtime-plugin-packaging/trust/acceptance-roots.json`。插件清单由 Runtime 自动同步。

### Phase 4 agent2 网页验收

1. 用用户的 agent2 内部网页和当前已选中的本机 Runtime，确认心跳自动同步浏览器定义，且服务端读取的清单来自本机已安装的签名包。
2. 在「在线电商客服」网页会话发出测试指令，由 AID 调用插件打开之前测试的 ERP 登录页。登录凭据在本机网页填写，AID 使用原浏览器会话完成登录操作、进入出差申请模块并打开新增表单。
3. 按测试资料填写表单并上传用户授权的测试附件。保存测试申请，核对页面回执和申请列表。从 Runner 任务记录核对调用实际发给所选 Runtime。验收记录使用脱敏信息。
4. 在同一会话安排一次可控失败，确认按现有回执说明原因，再继续问一条其他问题。核对会话继续可用。

## 验收

- 串行任务 A 正在运行，服务端先排入串行 B、再排入并行 C。C 先执行，B 保持待领，A 结束后才领 B。用无标记的命令验证默认串行。
- 同一插件有命令正在执行时，已声明可并行的命令也能启动。串行作业取消时，下一条串行作业等实际停稳再领，独立的并行作业继续。
- Runtime 退出或断线后，未领取的作业继续留在服务端，恢复后照常领取。已领取作业保留原编号、结果与续传语义。
- 用实际已交付的旧 Runtime 分别升级新版 BOSS 和新版企微签名包，保持 Runtime 和服务端原版本。验证验签、安装与升级、版本和就绪检查、已有工具调用和结果上报。旧 Runtime 按原规则串行执行。再用同一份新版插件包验证新版 Runtime 能读到调度声明。
- 新版 Runtime 遇到无声明的旧 CLI 时，提示升级该插件，其他插件继续接单。换成带声明的新版 CLI 后，验证原有命令可执行。带有效声明但所有命令都未标并行的插件，也能接入并按串行执行。
- 独立测试与代码审查覆盖已有串行领取、取消和回执，以及新服务端搭配实际已交付的旧客户端组合。

[独立设计](../system/runtime-plugin-decoupling-dispatch-design.md)
