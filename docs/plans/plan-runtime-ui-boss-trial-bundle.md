# Runtime UI 与 BOSS CLI 老用户试用组合包

> 2026-10-14：最新r13/UI0.0.9修复正常环境缺测试变量时安装退出，已实际本地完成正常非静默向导并退出0，交付`clients/release/AID-Work-Runtime-UI-BOSS-20261014-trial-r13.zip`；r11/r12保留历史，不再交客户安装。Host0.2.19/Core0.2.18/BOSS0.3.5不变。用户批准干净安装、有效身份复用和一次配对自动切换，以[干净安装计划](plan-runtime-clean-install.md)和共同契约2.1为准。源码纳入本次用户授权提交，新配对必须先发布配套服务端，服务器尚未部署。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | Windows 登录自启与正常 Host 恢复 | ✅ 完成（2026-10-12） | 独立定向52项、最终自启9项、真实Electron注册/禁用通过；CR无遗留问题 |
| Phase 2 | 最新签名 BOSS 插件与匹配 UI 安装器 | ✅ 完成（2026-10-12） | 最终验包、包内EXE隔离smoke、真实Host导入BOSS与安全退出通过 |
| Phase 3 | 老用户配置迁移说明与最终 ZIP 校验 | ✅ 完成（2026-10-12） | 11文件ZIP交付；HTML/MD手册、摘要与验证记录；人工验收范围见下 |
| Phase 4 | agent2 冷启动 BOSS 能力自举修复 | ✅ 完成（2026-10-12） | 独立56项、真实旧库存冷升级/能力/负向门禁及r2包smoke通过；CR无遗留 |
| Phase 5 | 自动升级收尾与异常退出恢复 | ✅ 完成（2026-10-12） | r3包内EXE、独立回归及CR通过，ZIP交付；实际客户重启续装待验收 |
| Phase 6 | 安装器补齐与历史记录下退出卸载 | ✅ 完成（2026-10-12） | r4最终EXE与NSIS五组验证、独立测试和CR通过；实际UAC及客户验收待完成 |
| Phase 7 | 跨版本续装冲突与旧版告警修复 | ✅ 完成（2026-10-12） | r5独立10组、最终包启动与CR通过；真实客户重启续装仍待验收 |
| Phase 8 | 重装后Host启动健壮性与自主验证 | ✅ 完成（2026-10-12） | 37场景、实际包启动/只读退出及CR通过，r6 ZIP交付；旧故障接管缺口见恢复计划 |
| Phase 9 | 连接恢复路径复核与源码补漏 | ✅ 完成（2026-10-12） | r6九例及新源码8例/CR通过；完整自助修复未实施，不重新发行r6 |
| Phase 10 | 自助连接恢复及故障说明r7发行 | ✅ 完成（2026-10-12） | 独立故障/实际包启动与CR通过；可操作步骤、BOSS0.3.1和未签名ZIP校验通过 |
| Phase 11 | r8只读故障入口与实际恢复交互修复 | ✅ 完成（2026-10-13） | r7实际复现；r8实际UI5组/在线启停退出/CR与17文件ZIP通过 |
| Phase 12 | 独立新注册与指定旧设备替换配套 | ✅ 完成（2026-10-14） | r9开发验证完成；启动语义由r11取代，真实UAC/boot/agent2仍待现场 |
| Phase 13 | 干净安装与一次配对自动激活 | ✅ 完成（2026-10-14） | r11实际EXE连续安装10/10、包内Host9/9、GUI自动恢复/载荷比对与CR通过；详见干净安装计划 |
| Phase 14 | 安装前旧进程检测及用户确认 | ✅ 完成（2026-10-14） | r12实际安装/静默取消及原NSIS确认/取消通过；增量5场景与CR通过，详情见干净安装计划Phase5 |
| Phase 15 | 正常环境完整安装向导验证及退出修复 | ✅ 完成（2026-10-14） | r12正常交互退出2已复现；r13真实非静默正常安装到完成页退出0，独立回归/CR通过；见干净安装计划Phase6 |

上一客户包为 **r8**：UI0.0.5、Host包0.2.17、Core及H1上报0.2.16、BOSS0.3.1、Node22.23.3；修复r7只读故障下假刷新和自动诊断禁用处理入口，连接步骤首屏可见，检查只验证配置/服务器。实际EXE5组恢复及在线启停退出、独立CR和ZIP复核通过；详情见[恢复计划Phase11](plan-runtime-upgrade-recovery.md#phase11-最终交付2026-10-13)。ZIP `131135565`字节、17文件，SHA256 `127f9193c2130055af2a82706f6f998694658a715e8d8338fd1c0eeb4f9475ee`。真实客户UAC/Windows重启与BOSS业务待现场验收，旧r7以下保留历史。

**最新候选r9组合ZIP已生成，未签名，仅按用户安排交自愿客户试用。** UI0.0.6、Host0.2.18/Core0.2.17、BOSS0.3.5、Node22.23.3；`clients/release/AID-Work-Runtime-UI-BOSS-20261013-trial-r9.zip`，131212947字节/26文件，SHA256 `bc9a8ec9cfd2d8d023251876cad82aa7a325cc3c4e952df655274ed8b9f165a7`。最终包启动/模块绑定、独立BOSS实际UI导入/ready/能力及只读Device→MCP→ACK、故障5组和CR通过；真实安装/重启及agent2业务尚未验收。 旧资料缺失不阻止独立新注册；新凭据加密保存并验证、旧资源交接后才完成指定旧设备替换。网页“更换此设备”、替换码与完成接口需要服务端迁移/API/Web配套部署；普通新配对仍兼容旧服务器。源码、独立测试、CR及实际包验证详情见[恢复计划Phase13](plan-runtime-upgrade-recovery.md#phase13-实现及验证登记2026-10-13)。不能将旧r8 ZIP中的客户端冒称具备此能力，也不将本机候选安装器当客户环境已经通过验收；本包携带客户简明/完整配置故障说明、管理员手动发布与现场步骤、摘要及分项验证记录；不承诺现场未验收项。

此前r6交付以[自动升级恢复计划](plan-runtime-upgrade-recovery.md)和r6修订为准。r6移除启动重写旧管理记录，兼容BOM并固定系统PowerShell；资料异常时保留只读管理与退出。保留r5续装轮换及r4历史记录下空闲退出。已自主验证37个场景，不再要求客户运行诊断或提供更多材料。具体发行记录与未验证边界见恢复计划。

后续代码审计确认r6并未形成完整连接恢复：同名pair会新建设备，缺配置+旧历史会持续只读，identity-reconciliation标记没有解除入口。BOM仅管理读取兼容、Windows启动与ACK身份读取有遗漏，后续源码已补。不得以r6启动/退出通过宣称上述故障已恢复，也不再建议客户反复覆盖安装碰运气。确定条件及源码修复见恢复计划Phase9；该缺口已在r7/Phase10补齐，以下r6记录仅保留历史。

r6最终组合包：`clients/release/AID-Work-Runtime-UI-BOSS-20261012-trial-r6.zip`，131032987字节、17文件，SHA256 `81a5d5a0fa42997de715eeb1f9a450c192f1cb25cdf40d1f3dfc024bd8dc3e38`。UI0.0.3、Host0.2.15、BOSS0.3.1、Node22.23.3；Windows未签名验收版。Runtime源码提交`1d67fa42`，完整工作区基线`0957c632`，交付说明有本地更新；未部署服务器。旧r5故障接管未确认Main退出码，不承诺现场覆盖必定恢复。

r5最终组合包：`clients/release/AID-Work-Runtime-UI-BOSS-20261012-trial-r5.zip`，131019297字节、14文件，SHA256 `96c1927632559632c3ba5a79a53f375dbd23489dfca760d121ae8e5ca41c2366`。UI0.0.2、Host0.2.14、BOSS0.3.1、Node22.23.3；Windows未签名验收版。基线`ae10e453`加本次未提交修复，未部署服务器。

r4最终组合包：`clients/release/AID-Work-Runtime-UI-BOSS-20261012-trial-r4.zip`，131014331字节、12文件，SHA256 `d93044ac7baf9d5d91161ca39bef1ee76fe0e459b260573d48193089638704f2`。UI0.0.2、Host0.2.14、BOSS0.3.1、Node22.23.3；Windows未签名验收版。构建基线`ebd8120e`加交付时未提交增量；后续源码提交核对见恢复计划。本轮未部署服务器。

r3最终组合包：`clients/release/AID-Work-Runtime-UI-BOSS-20261012-trial-r3.zip`，131009495字节、11文件，SHA256 `231ac282ea8153acd1a9bdc15136011bdac6fb4052e2bb4284e214000fcc9b12`。UI 0.0.2、Host 0.2.14、BOSS 0.3.1、Node 22.23.3；Windows未签名试用验收版。安装器及插件摘要、独立测试和客户验收边界见恢复计划及ZIP内验证记录；r1/r2原包保留用于核对。

2026-10-12 升级保护检查点：当前主控唯一写入公共NSIS钩子、发行配置说明与只读诊断脚本；禁止默认安装/卸载检查终止Runtime进程，未退出或进程检查失败时退出安装。未变更Runtime恢复语义、Host wire或业务执行，旧marker仍不能靠空journal/outbox自动删除。BOSS v1没有完整本机准入账本，当前机器恢复须结合原设备云端历史及本机进程/启动事实核对。

2026-10-12 增量共同检查点：当前主控唯一写入 `clients/shared/local-tool-host-core/src/plugins/store.ts`、其测试与 `clients/agent-tool-runtime/src/runtimeHost.ts`。共享方法添加可选受信bootstrapKeys，默认行为兼容；组合层仅允许boss-recruiting在已验签/完整/版本核验但业务doctor未就绪时保留入口。UI的ready诊断和其他Provider就绪门不变，合法调用仍走原云端invocation/桌面锁/业务检查，不改schema、binding、许可或契约版本。不部署agent2。

## 范围与共同检查点

2026-10-12 用户要求最新 Runtime UI 与最新 BOSS CLI，ZIP 包含安装器、离线插件和配置说明，并支持开机自启；用户明确选择试用验收包，标注 Windows 未签名。基线为[共同契约 v2.0](../system/runner-desktop-runtime-integration-contract.md)、[Runtime 架构](../system/runtime-plugin-host-architecture-design.md)及[统一计划](plan-runtime-plugin-host.md)。不开放第二部分或变更 Device/管理 wire、binding、许可。

本批唯一写入者为当前主控，公共壳文件：`clients/agent-desktop/electron/main.ts`、新增 `electron/runtimeStartup.ts` 与 `tests/runtimeStartup.test.ts`、`electron-builder.yml`、必要发行脚本和 NSIS 钩子。Runtime core/CLI/BOSS 源码复用当前 HEAD，不另实现执行引擎。两份共同计划登记本批入口；版本仍为契约 v2.0 / 候选0.3，旧 CLI 和完整 Desktop 启动语义保持。

## 实现与兼容

- 仅安装的 Windows Runtime 产品首次启动登记当前用户登录项，参数 `--aidwork-runtime-login`，隐藏窗口进入托盘；首次手动启动仍展示配对/安装页。使用 Electron [login item API](https://www.electronjs.org/docs/latest/api/app#appsetloginitemsettingssettings)。托盘复选项控制启用；后续不覆盖用户或 Windows 禁用选择。
- 登录时仅从正常 Host 管理端查询状态；已配对且 stopped 才发送一次 start，不自动配对、不运行 BOSS 业务动作、不重试不确定请求。blocked/reconciling/revoked 显示窗口，保留原 journal/outbox/恢复标记。
- 验收版独立 home `%APPDATA%/aidwork-tool-runtime-acceptance`；不复制旧 DPAPI、身份、pending 结果或插件信任。旧用户先安全收尾并停 `AidWorkToolRuntime` / `AidWorkToolRuntimeWatchdog` 和旧进程，再新配对并在云端选择新设备；未确认结果需要管理员核对，不能自动重放。
- 同次生成隔离签名根和 BOSS `.aidplugin.zip`，根嵌入安装器。Node 22.23.3 随包；客户机无需 npm/Python/Git。试用包不宣称正式发行或真实业务验收完成。

## 验证与交付

高风险流程：主控自测与构建 → 独立测试角色 → 独立 CR → 最终修正/验包。验证包括自启注册/禁用、状态门禁、打包白名单、固定 Node 依赖图、BOSS 签名/清单/version/doctor、最终安装器验包、隔离启动与退出，以及 ZIP 清单/SHA256。不触发真实招聘业务、服务器部署或 Git 提交。

### 2026-10-12 完成交付记录

- 基线 `7e165526` 加本批未提交修正；UI 0.0.2、Host 0.2.14、BOSS 0.3.0、Node 22.23.3/ABI127。BOSS实包22工具、3605文件；完整签名/manifest与只读version/doctor检查exit0，执行后payload不变。
- Desktop build、BOSS build、Host pack exit0。缺失的构建依赖按锁文件补齐；npm pack自动改动的Runtime锁peer元数据已恢复，不带入源码差异。
- 独立测试52项通过；最终自启适配修改后9项重测通过，未机械重复无关测试。真实Electron43发现带args的openAtLogin读回异常，按唯一产品name的launchItems.enabled修正；唯一临时登录项登记/禁用/二次初始化保留选择通过并清理。
- CR提出更新卸载误删Run、异步start失败和隐藏窗口不能focus恢复，均修复；最终CR无P0/P1/P2。覆盖更新不清登录项，普通卸载仅清对应产品登录项和UI初始化标记，保留执行事实。
- 真实包内Node/Host经RuntimeSupervisor和RuntimeSelections安装BOSS：0.3.0、enabled=true，平台取包一次；无pair/start/业务，stopAndExit确认安全退出。此检查复用UI实际安装适配器，未替代OS文件选择框。
- 最终`package-runtime-win.mjs acceptance`：`RUNTIME_PACKAGE_PASS`、`RUNTIME_VERIFY_PASS`，exit0；3682资源文件，Windows NotSigned。最终包内EXE在临时Windows profile中启动，`AGENT_DESKTOP_SMOKE_PASS`、exit0；无遗留直属Node、Host lease/running标记或验收自启项。
- ZIP `clients/release/AID-Work-Runtime-UI-BOSS-20261012-trial.zip`：130966871字节、11文件，CRC及全部SHA256复读通过。内含安装EXE、签名BOSS插件、HTML/MD说明、版本、摘要、两份清单和独立/主控验证记录。
- ZIP SHA256：`a8eadf3c995e225887d08e2913c3c786779dca31c539fc1107df56576065b5fa`。
- 安装器SHA256：`80537a38b0dbfab3949f97f528b324420cf96711c8d7909f79c22d444367298e`；BOSS插件SHA256：`4c82186d853627803cec44780bd2f0f8889b10d2958ff7867db5d05857c40845`。
- 尚需客户机人工验收：NSIS安装/卸载UI、OS原生选框、Windows注销/重登录自启、真实云端配对及BOSS业务。试用发行已按用户选择交付，以上不记为已通过；不代表统一Runtime A4正式发行/业务验收完成。本批未提交、推送或服务器部署。

### 2026-10-12 agent2 问题与 r2 修订

用户现场截图：设备已配对、选中、运行在线；BOSS 0.3.0 已启用、未就绪。只读查询agent2追踪库，`tr_runner_b2121c8d43374dd28a888322f42b4775` 的 `boss_goto(target=recommend)` 在云端直接失败：`DEVICE_UNAVAILABLE`，消息为选定设备不支持BOSS招聘操作，未派发客户机。最终模型回复误导为需要重新配对/勾选招聘能力。

根因是共享store将doctor业务ready作为入口加载门：未开调试Chrome或未登录时删除BOSS入口，Host启动ProviderSet为空，heartbeat与canClaim也不包含BOSS。既有CLI虽然具备ensureDebugChrome，但正常云端调用无法到达它，形成自举死循环。

修复为共享store可选的受信bootstrapKeys，默认空保留兼容；Runtime组合层仅传boss-recruiting。必须已验签、完整文件核对、实际version/manifest一致且enabled、未卸载、有可信manifest，才允许业务doctor未就绪的BOSS保留入口。UI仍如实显示ready=false，原业务页/登录/桌面/锁/写动作许可检查保持；微信企微仍保留ready门。安装和刷新仍只运行version/doctor，不主动打开Chrome、业务调用或重放unknown。

验证与兼容交付：

- 主控store/package17项通过，Runtime build/pack exit0。独立core/store/package/management31项、Host12项、Provider相邻13项共56项通过；最终CR无P0/P1/P2。
- 实际首包旧Host与本批签名BOSS在隔离home安装/配对后，模拟doctor访问随机未开放端口，确认旧版ready=false时不报BOSS；新Host复开同home保留installation_id/device_id，实际heartbeat报BOSS、ProviderSet.has=true、canClaim=true、FakeCloud空claim可见。
- 真实新包固定Node/Host/core复核冷升级、tamper、disabled、reenabled-readyfalse、uninstalled五场景：篡改/禁用/卸载不报BOSS、不装配Provider、不领取，重新启用恢复可用。未访问真实Chrome/云端或执行业务动作；安全退出后系统namedpipe实例锁可重新获取/释放。
- r2原样复用首包BOSS签名ZIP与旧公钥根，逐字节SHA核对通过，避免新根导致旧库存失信任及同版本不同release不能升级。已有首包用户正常退出后覆盖安装即可保留设备/插件；不需要重配对或重新安装BOSS。手册新增r2升级说明，旧CLI迁移流程仍保留。
- 最终`RUNTIME_PACKAGE_PASS`/`RUNTIME_VERIFY_PASS` exit0，3682资源文件；r2包内EXE实际隔离smoke PASS/exit0，无自启注册或遗留进程。安装器126244650字节，SHA256 `215f97e1476eccb2740ec4f698145d4ae1ff77fcdce85dd06adf1a1a7cd428eb`。
- 新包 `clients/release/AID-Work-Runtime-UI-BOSS-20261012-trial-r2.zip`，130968128字节、11文件，CRC与全部文件摘要复读通过；SHA256 `96792c5e80180a96676b504566ed319673172174b90126b8043c8b610b7cd155`。首包ZIP保留用于核对，不再推荐使用。
- 仍需客户实际NSIS覆盖升级、注销/重登录及agent2/BOSS真实派发验收；本修复不需要服务器代码变更，未部署agent2，未提交/推送。验收版仍未签Windows证书。

### 2026-10-12 关闭窗口后覆盖安装导致待核对

用户确认先点击系统X，再直接覆盖安装。源码事实：main.ts close事件preventDefault后window.hide，保留托盘和执行进程；只有托盘Exit经before-quit/stopAndExit等待安全收尾。electron-builder默认NSIS检查会对安装目录执行Stop-Process，随后强制重试，包含直属Host Node；未走安全stop时host-running.json保留，新Host构造时进入reconciling、connection=offline，start/stop/pair被阻断。正常Host mutation串行及parent-disconnect等待queue未发现marker重建竞态。

预防改动：仅Runtime NSIS customCheckAppRunning替换默认分支，检测本产品EXE仍运行或检测失败时退出安装code2，603确认未运行才放行；不Kill/Close。真实Unicode NSIS临时同名Node/timer fixture测试四项通过：安装与卸载运行时code2且fixture存活，停止专有fixture后两者code0。未对客户机执行安装或终止程序，旧r2 ZIP尚未替换，不能声称既有客户已获得保护。

现场恢复边界：旧marker只有instance_id；BOSS v1未持久化完整准入/结果账本，空journal/outbox或已释放Host/桌面锁都不能证明旧动作未发生或Provider已停。只读查询agent2当前选中设备`84fedcd0-45ba-4234-b888-92063b52177b`全部invocation为零；另一旧设备有unknown记录，不能混用。当前客户仍需本机标记、启动时间、进程及状态目录事实；不自动删除marker、不重配、不重放业务。

只读诊断交付：`clients/release/Runtime-诊断摘要-20261012.zip`，包含ASCII脚本runtime-diagnostic-summary.ps1（UTF8 BOM）及中文bat。只输出设备编号/服务域名、bootUTC、marker摘要/时间、状态目录和管理操作计数、当前用户已知Runtime进程编号；不读取凭证或日志任务payload，不停进程、不改配对、不清数据，只在桌面写摘要。匹配补旧agent-tool-runtime CLI，明确known-paths摘要不是完整后代停止证明。CR本批无P0/P1/P2；恢复与新完整安装器交付待现场摘要及验证。

独立诊断验证：真实Windows PowerShell5.1 ParseFile零错误/BOM通过；隔离APPDATA/USERPROFILE/Desktop运行原脚本exit0，非白名单正文sentinel不进入12字段JSON，origin和计数准确，Runtime fixture全部SHA前后不变，仅临时Desktop生成摘要。中文bat实际cmd/c执行exit0且内容ASCII。前次复合测试命令被自动审批策略拒绝后，改为只读解析/隔离摘要测试完成，无客户机进程/marker动作。
