# Runtime 插件解耦与并行接单开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
| --- | --- | --- | --- |
| Phase 1 | 插件工具声明与 Runtime 通用接入 | ✅ 完成（2026-11-12） | commit 8e47bd54：三 CLI buildManifest 输出 tool_declaration（仅企微两本地库读工具 parallel）、boss 注解审计修正 5 处对齐写集合；Runtime 宽松解析 +「需要升级插件」门禁 + 派生清单与 TRUSTED 取交集锚定保持现网受信面 + parallel_tools/tool_declarations_v1 心跳上报；未知插件零改动接入有端到端测试。五包 typecheck 0，定向+回归绿（基线失败 stash 对照一致）；CR 4 项记录不阻断 |
| Phase 2 | 服务端按类型派发和 Runtime 并行执行 | ✅ 完成（2026-11-12） | commit 503774f2：claim 可选 parallel_only（jsonb 过滤，缺省 SQL 逐字节不变）+ 心跳 parallel_claim 协商 + 领取状态机（串行占用/后台并行/全部在途 drain）+ 并行跳桌面锁与 Provider 并发 lane。真实 PG 16 集成 8/8（scratch 最小 DDL）、服务端单测 743 过、runtime parallelClaim 7/7、五包 typecheck 0；CR 无 P0/P1，2 P2 记录（支持标记翻转窗口锁兜底、parallel 限只读已补契约）；fail-loud 提前返回判可接受（租约 unknown 兜底不变） |
| Phase 3 | 声明即调度授权：CLI 声明收敛 + Runtime 删交集锚定 + 服务端声明分流 | ✅ 完成（2026-11-12） | 契约 2.20 修订（e0ae04f2）落地：wecom 声明白名单 12 工具（chat_search 等 5 个内部命令不声明）、weixin 声明 6 个 v1 工具（session_observe/v2 发送不声明），两 CLI 清单 tools 与 schema_digest 不动；host-core deriveProviderManifest 受信面=声明全集（删交集锚定，未知插件同一逻辑）+ 心跳新增 declared_tools（与 parallel_tools 同源同过滤）；服务端 claim 行级放行与 device_ready_error 声明分流（未声明覆盖的 provider 与 NULL 行回退目录，旧设备逐字节不变）。服务端单测 765 过（新增 22）、真实 PG 16 集成 5/5（声明端到端）+ Phase 2 集成 8/8 回归、host-core 129/130 与 runtime 340/343（基线失败 stash 对照一致）、三 CLI manifest 测试绿、五包 typecheck 0 |

## 实施

- Phase 1 为新版 BOSS 和企微 CLI 增加带版本的工具声明，保留旧 Runtime 使用的插件包和调用接口。新版 Runtime 从签名包读取声明，将按插件名或工具名写死的配置和业务判断移到插件声明或对应业务代码。用 BOSS、企微和一个 Runtime 未预先认识的测试插件验证接入。
- Phase 2 在现有领取接口增加按类型领取。服务端负责待领状态和原子领取，Runtime 负责按当前占用继续领取并立即执行。把并行命令从本机串行限制中放开，沿用现有取消、进度和回执。
- 源码实施前在共同契约登记声明与领取接口的版本、生产端和消费端示例。插件负责自身声明，Runtime 负责读取、上报和执行，服务端负责记录并选择任务。按既有能力协商兼容已交付组合。（已登记：[共同契约 2.20 检查点](../system/runner-desktop-runtime-integration-contract.md)，2026-11-11）

## 验收

- 串行任务 A 正在运行，服务端先排入串行 B、再排入并行 C。C 先执行，B 保持待领，A 结束后才领 B。用无标记的命令验证默认串行。
- 同一插件有命令正在执行时，已声明可并行的命令也能启动。串行作业取消时，下一条串行作业等实际停稳再领，独立的并行作业继续。
- Runtime 退出或断线后，未领取的作业继续留在服务端，恢复后照常领取。已领取作业保留原编号、结果与续传语义。
- 用实际已交付的旧 Runtime 分别升级新版 BOSS 和新版企微签名包，保持 Runtime 和服务端原版本。验证验签、安装与升级、版本和就绪检查、已有工具调用和结果上报。旧 Runtime 按原规则串行执行。再用同一份新版插件包验证新版 Runtime 能读到调度声明。
- 新版 Runtime 遇到无声明的旧 CLI 时，提示升级该插件，其他插件继续接单。换成带声明的新版 CLI 后，验证原有命令可执行。带有效声明但所有命令都未标并行的插件，也能接入并按串行执行。
- 独立测试与代码审查覆盖已有串行领取、取消和回执，以及新服务端搭配实际已交付的旧客户端组合。

[独立设计](../system/runtime-plugin-decoupling-dispatch-design.md)
