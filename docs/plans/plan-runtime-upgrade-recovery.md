# Runtime 覆盖升级与异常退出自动恢复

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 升级协调退出与原运行意图恢复 | ✅ 完成（2026-10-12） | 安装器协调 stop/drain；保护运行意图，取消安装恢复自启选择 |
| Phase 2 | Windows 进程监督与跨重启恢复 | ✅ 完成（2026-10-12） | Job监督、DPAPI启动账本与重启见证验证通过；保留身份与业务事实 |
| Phase 3 | 独立验证、CR及未签名验收ZIP | ✅ 完成（2026-10-12） | 最终包内EXE与独立Main8项通过，CR无P0/P1/P2；r3 ZIP摘要/CRC通过 |
| Phase 4 | 安装完成自启恢复提示与分支验证 | ✅ 完成（2026-10-12） | NSIS五组隔离验证、无pending免调用及自足恢复通过；现场告警未复现，真实UAC待验收 |
| Phase 5 | 历史待核对状态下退出与卸载 | ✅ 完成（2026-10-12） | 最终包内EXE历史状态卸载维护/退出/锁释放/记录保留通过；独立测试与CR通过 |
| Phase 6 | 跨版本续装材料轮换与旧客户端告警 | ✅ 完成（2026-10-12） | r5独立10组/最终包启动/CR通过，ZIP交付；真实客户重启续装仍待验收 |
| Phase 7 | 重装后Host代码11启动诊断 | ✅ 完成（2026-10-12） | 只读工具29例及CR通过；用户拒绝追加资料，转自主复现，不再要求客户运行 |
| Phase 8 | 自主复现及Host启动健壮性修复 | ✅ 完成（2026-10-12） | 37场景及实际包启动/只读退出通过，r6 ZIP交付；旧故障接管退出码缺口保留 |
| Phase 9 | 连接失效与重新配对恢复路径审计 | ✅ 完成（2026-10-12） | r6九例确定路径；新源码独立8例、CR通过，UI/BOM补漏；完整修复仍待Phase10 |
| Phase 10 | 完整自助连接修复与发行验收 | ✅ 完成（2026-10-12） | r7诊断/恢复、独立故障验证与实际包启动通过；现场UAC/重启待验收 |
| Phase 11 | 只读故障状态下可见、可点击的连接处理入口 | ✅ 完成（2026-10-13） | r7实际包两缺陷复现；r8实际UI5场景、在线启停退出及CR通过，ZIP交付 |
| Phase 12 | r8混合历史故障的重新配置阻断与提示覆盖 | ✅ 完成（2026-10-13） | 最终实际包两混合故障pair各一次；检查/重连/重开保留步骤，旧资源未证不激活或注销 |
| Phase 13 | 失效连接新注册与指定旧设备替换注销 | ✅ 完成（2026-10-13） | 独立源码/后端/Web及CR通过；实际包注册激活/同home启停退出通过；首停止脚本误判已由Phase14归因，未部署 |
| Phase 14 | 客户交付门逐项验证 | 🔧 进行中 | 1/5/6/7隔离发行检查完成；2/3待自愿现场，4由用户手动部署后联调 |

## 共同检查点与版本登记（2026-10-12）

共同契约仍为 v2.0 / H1 候选0.3；Device API、binding、任务授权不变。当前主控唯一写入 core management/Provider launch 和 Runtime 组合层、Electron升级入口、NSIS及发行材料；原生进程监督由有界开发角色负责，独立测试与CR只读产品代码。

- H1严格state/schema保持不变；恢复标志仅经直属受信Node→Main私有IPC `runtime_recovery` v1传递，instance_id绑定当前握手，不转发renderer。只有已证明旧资源停止才发送，不代表业务结案。
- 内部 HostAdapter 增加可选 executionMarker/recoverPreviousExecution，canChangePlugins兼容增加可选method；无恢复实现的旧适配器保持 fail closed。
- 本机 `host-running.json` v2 保存 instance_id 与 DPAPI保护的执行监督记录引用；同用户/home唯一Host写入。旧 `{instance_id}` 格式保持识别，但必须先登记 DPAPI保护的重启见证，再验证 Windows kernel BootIdentifier 已变化才能归档。
- 本机 process-supervision v1 记录按当前用户DPAPI保护，含 generation/home/Windows session/jobName；Provider启动前先落盘，原生辅助器将Node挂入KILL_ON_JOB_CLOSE Job后才执行。同boot恢复必须在原Windows session查询Job，切换会话则要求重启。不管理外部已有Chrome。
- 归档异常marker创建身份核对 fence，不能冒充业务ACK；journal/outbox/session/云端unknown保留。恢复连接不重放旧工具调用。只允许经验签/更高版本门的import，保留旧release全部文件，新增调用用新版本；已有未结案v2/session仍阻断，换身份和破坏性插件操作仍阻断，不清fence。
- `--aidwork-exit-for-update` 仅安装的Runtime处理；协调正常停止，安装等待有界，未成功时不覆盖。旧r1/r2没有该入口，要求一次正常Windows重启迁移，之后升级自动协调。
- r4增量共同检查点：当前主控唯一写入core.dispose、managed-entry及Supervisor/Main；新增直属Main→Host私有生命周期v1帧runtime_lifecycle、runtime_shutdown_request/response，绑定当前instance及Main generation，不开放renderer/H1。dispose先冻结准入并等待已接受管理队列；当前Host未拥有执行时可释放自身管理lease，旧marker/业务记录/历史状态保留，不宣称旧执行已停止。当前Host已拥有执行仍按原stop/drain，不通过历史状态猜测放行。
- r5增量共同检查点：当前主控仅写NSIS与legacy-upgrade.ps1，独立测试角色写安装器回归；schema仍为本机upgrade-pending v1，严格校验原SID/APPID/home/目录/载荷摘要/RunOnce后归档旧控制目录，恢复首次登录选择再登记新版续装。旧witness及业务记录不变；Main/core/H1/Device/binding无改动。真实UAC与重启续装仍须验收，不把隔离结果当客户安装通过。
- r6增量共同检查点：主控唯一写core management及Runtime组合层/对应测试，Windows平台角色唯一写共享legacy/dpapi及其定向测试；独立测试/CR只读产品。新增可选仅Runtime_app的启动失败只读管理模式，H1仍用已有blocked/blocked_reason及code11、现有私有生命周期v1，不加wire字段或方法、不重建密钥、不结案或重放历史、不自动换身份。CLI默认抛错兼容；启动时旧ops仅校验/内存投影，实际mutation才写。标准CurrentUser DPAPI方案不变。实施后登记模块/矩阵验证，不再要求客户运行诊断或提供更多日志。
- r6发行识别：当前主控唯一更新Desktop、Host和core的package/lock版本元数据为UI0.0.3、Host/core0.2.15，依赖版本不升级；避免r1～r5同名0.0.2安装器混用。H1候选0.3和私有生命周期v1仍兼容。共享系统PowerShell配置同步用于LegacyCIM与Job ownerStart，不依赖PATH中的同名程序。
- Phase9实施前登记：主控唯一写Runtime supervision/config ACK读取、core管理操作错误投影、Runtime页面/Controller及其定向测试；独立角色只读产品。H1方法、字段、operation状态及Device/binding均不变，允许UI发起已有stop检查，Host仍校验当前执行所有权；不引入新身份重置、自动清fence或历史业务结案。BOM读取兼容补齐其他既有消费者，CLI兼容身份门缺口先据实登记。本批源修复不回写已交付r6 ZIP。
- Phase10实施前共同检查点：主控唯一写Core本地受信恢复检查、Runtime诊断/事务与共用startup gate、Main/Supervisor/preload恢复协调、可选RuntimeManagementPort恢复方法及UI/测试；Windows角色唯一写connectionRecoveryResources及测试。H1候选0.3方法/回复/state不扩字段，Device API/binding不变；新增直属Main↔Host私有connection v1，严格instance/generation/request/action绑定。H3 bridge3增加可选Runtime-only diagnose/reconfigure能力，旧port隐藏新动作。保留原身份仅归档管理secret/ops，业务/session/plugin/fence留执行原位置；新身份整home同父原子归档，不迁移或重放旧device任务。固定同父DPAPI保护事务先fsync，再变更，CLI与Host启动先恢复同一事务，不自动再消费配对码。心跳探针不启动claim，实际Host退出及Main重新取得lease后重验资源。原设备换新ID需Web选定；异常需要明确action。未知业务不伪造ACK，不删除旧证据。实施后必须真实producer/consumer及资源/崩溃/配对失败验证，才发行下一未签名包。

## 验证与限制

验证同boot/跨boot、篡改或误绑定记录、真实进程树及Host退出、在途任务升级、未配对与显式停止、自动恢复不重放、安装包内容和隔离启动。网络不通且原v1结果无法交付时，升级等待超时并停止安装，保留旧进程；本轮不改云端结果协议或扩大为v1持久结果改造。未知业务事实需要业务核对，不能通过卸载删除。

## 最终实现与独立验证（2026-10-12）

- 从 r3 开始，安装/卸载经固定维护入口停止领取新任务、等待原业务和结果收尾，再等待直属Host实际退出；升级等待600秒，超时暂停安装，不强杀。X继续隐藏到托盘，升级无需用户先手动退出。
- Main先冻结新管理请求再读取状态、保存原运行意图。意图使用既有CurrentUser DPAPI保护，FIFO串行读写和归档；维护期间禁止恢复启动，迟到的renderer回复不能覆盖意图。维护失败后的新明确start/stop可覆盖旧意图；显式停止不会被升级或登录恢复误启动。
- 旧r1/r2不认识维护入口。安装器为相同Windows用户/home登记保护的kernel BootIdentifier见证、验证摘要的安装器副本及一次RunOnce续装；用户正常重启后才证明旧进程已停止。取消/失败按绑定恢复原自启项，不强制重启、不要求例行提交日志。
- 新版受管Node先挂入Windows Job再运行，Host退出或异常死亡回收所属后代；外部已有Chrome不纳入。默认同时回收本次启动的Chrome，保留登录profile。启动前保护账本落盘，恢复限定home、generation、boot和session；证据不足保持阻断。
- 配对、插件、业务journal/outbox/session以及身份核对fence保留。进程资源恢复不冒充业务ACK、不重放unknown。保留r1/r2公钥；BOSS升级到0.3.1补齐签名物理点击C#依赖，已有旧release文件保留。
- 独立core/store24项、Runtime15项、Desktop22项通过；恢复、原生监督、实际compiled Main异步入口与NSIS隔离fixture验证通过。最后新增Main回归包含维护期间迟到start/stop回复，8项通过。最终CR无剩余P0/P1/P2；详细命令和实际结果随ZIP保存在独立验证记录。
- 实际包内EXE维护退出、DPAPI跨进程读取、重开消费、无实例维护及smoke验证；包内模块与独立测试dist核对，确认进程退出及home锁释放。真实用户自启项未修改。

实际Windows重启/RunOnce登录续装、客户完整安装卸载、真实云端配对和BOSS业务仍需验收。隔离NSIS fixture和模拟跨boot验证不能替代以上验收；未执行真实长任务业务升级或不同Windows session恢复。包构建交付时尚未提交/推送，服务器未部署。

## r3 交付记录

- ZIP：`clients/release/AID-Work-Runtime-UI-BOSS-20261012-trial-r3.zip`，131009495字节、11文件；CRC及全部文件SHA256复读通过。
- ZIP SHA256：`231ac282ea8153acd1a9bdc15136011bdac6fb4052e2bb4284e214000fcc9b12`。
- 安装器126280275字节，SHA256 `2163a20b15f7ec412acc62ca1db7683195b851c4691449ee1d69eb9d2ab036d6`，Windows NotSigned。
- BOSS 0.3.1插件SHA256 `4b06d9c6d2c2cc19dba0eadd2debdf342cf324bd872484eb5a256ed67e1d6892`，本批隔离发布者签名，22工具；安装器保留旧发布者根。
- 最新包内Main SHA256 `624f726318473c95521d3eabeaddab3f223fd1b8ff974234d8e951805883bf1f` 与独立compiled回归一致；同EXE实际维护退出/DPAPI读取/重开消费/无实例/smoke全部通过，进程及home锁释放，真实Run未变。

## 提交核对（2026-10-12）

用户明确授权提交；fetch后快进同步远程master至`195d1efe`，Runtime/BOSS相关源码没有远程重叠改动。保留其他任务7个文件及ideas中的数据分析条目；本提交只包含Runtime/BOSS相关代码、配置说明与登记文档。同步后Desktop typecheck及启动/监督/升级22项再次通过；包内程序和ZIP摘要保持不变，未部署服务器。

## r3 安装收尾告警（2026-10-12）

用户截图确认安装已写入完成，customInstall的Restore调用非零时统一弹出登录启动项恢复警告；用户选择“仅为我安装”。截图不能区分PowerShell启动、脚本环境或保护状态校验错误，不能据此断言配对丢失。代码发现该hook依赖CHECK_APP_RUNNING释放脚本，但builder的提权inner安装跳过该检查；这是独立可复现路径风险，不作为当前用户的确切根因。修复将普通无pending安装与实际恢复区分、保证hook自足，并补真实安装Section的普通及inner分支验证；不读取客户日志或删除配对/任务状态。

## r4 历史状态退出与安装器修复（2026-10-12）

截图中的未配对、离线、待核对Host没有当前执行所有权，却被历史marker阻止stop/dispose；旧Supervisor要求必须stopped才退出，因此卸载无法完成。这是生命周期边界错误。r4先冻结准入并等已接受队列结束；未拥有执行且处于stopped/reconciling/blocked的管理Host可以释放自身lease，原marker、操作及业务事实不变。当前确实拥有执行仍正常stop/drain。

Main/Host增加直属私有生命周期v1，严格绑定instance_id、generation、request及closed:true证明；只有私有响应成功送达后Host断开，Main必须观察实际child退出。H1、Device、binding及业务结果协议不变。安装/卸载/托盘退出文案区分实际动作。

NSIS无pending不启动PowerShell，Restore/Cancel独立解包并在原用户下处理；最终目录归一后及silent inner覆盖前均核验旧程序已退出。负数退出码或启动error/timeout仅重试一次，校验失败不重试。已安装r3不支持私有关闭时，15秒后只有严格验证的旧v1 marker可登记一次重启续装；v2任务继续正常600秒drain，卸载不登记续装。安装器不强杀。

- 主控core/store27项、Desktop25项、Runtime17项通过；独立core17项、Supervisor12项、IPC2项及NSIS五组通过，最终CR无P0/P1/P2。
- 真正最终EXE的历史状态卸载维护入口通过：七个已知进程退出、系统lease可重新取得，旧marker/operations/三类业务事实逐字节保留；smoke PASS/exit0，真实产品Run未变。包内七个模块与独立测试dist字节一致。最终原生EXE的真实Job/session/owner/outsider和delayed-launch重测通过。
- NSIS真实builder Section在隔离home/UUID启动项中验证无pending、恢复/取消、inner init、直接调用PRE、旧v1续装、v2拒绝续装及进程失败重试；直接调用PRE不等于实际交互MUI验证。真实UAC跨SID回调、Windows重启/RunOnce、客户完整安装卸载、真实云端/BOSS业务及长任务升级仍未执行。仅当前用户安装告警未现场复现，具体原因未确认为已解决。
- r4验收包构建基线为`ebd8120e`加本次增量，构建交付时尚未提交/推送；r3已按用户要求提交并推送，服务器未部署。

## r4 最终交付

- ZIP：`clients/release/AID-Work-Runtime-UI-BOSS-20261012-trial-r4.zip`，131014331字节、12文件；CRC和全部文件SHA256复读通过。
- ZIP SHA256：`d93044ac7baf9d5d91161ca39bef1ee76fe0e459b260573d48193089638704f2`。
- 安装器126281757字节，SHA256 `976119d4d93f4f51c5fb63970481ef51e99591483d0f47402be01d4d92377b08`，Windows NotSigned。
- UI0.0.2、Host0.2.14、BOSS0.3.1、Node22.23.3；BOSS沿用r3签名ZIP，SHA256 `4b06d9c6d2c2cc19dba0eadd2debdf342cf324bd872484eb5a256ed67e1d6892`，保留r1/r2信任根。手册包含旧r3卸载脱困及r4续装流程，旧ZIP不覆盖。

## r4 提交核对（2026-10-12）

用户授权提交后fetch，master快进至远程`33349477`；远程仅修改企业后台七张宽表，与Runtime修复无重叠。本次只提交Runtime生命周期、安装器、对应回归测试与说明/计划登记，保留其他任务的数据分析改动。Runtime产品源码与独立验证报告绑定摘要一致，复用同一源码状态已通过的验证，ZIP未重建；提交目标为已确认的`origin/master`，不部署服务器。

## r5 跨版本续装修复（2026-10-12）

用户按r4原地安装后仍看到旧Main「升级已暂停」，确认点击确定后没有「恢复已登记」提示。旧PS用相同home、安装目录及真实已交付r3/r4安装器摘要复现：r3 Prepare成功，r4 Prepare返回1/PENDING_INSTALLER_MISMATCH，全部执行事实与启动项不变。代码同时确认Restore只归档state却留下固定installer.exe，完成过的续装也会阻止下一不同包；此前测试漏了跨摘要场景。没有读取客户配置，因此不能把截图直接认定为这一具体pending内容。

r5在原SID/APPID/home/目录、旧受保护状态/载荷摘要和ownRunOnce校验后，恢复首次originalRun、清本产品Once，再将整个旧控制目录Move至固定upgrade-history/UUID，重新登记新包。Move前失败恢复旧registry；Move后失败保留已恢复的Run及absentOnce，不能恢复指向旧路径的命令。已完成的残留载荷需匹配受保护archive证据；同当前显式安装包摘要且无Once的无state缓存允许复用，覆盖复制发布后state尚未写的中断场景。新复制载荷WriteThrough/Flush(true)后才发布state及RunOnce。旧witness及配对/任务事实完全不变。

NSIS检测程序在运行时先Prepare；旧v1可直接提示重启登记，不向旧Main请求失败的退出，不再等待15秒。仅缺失marker或不支持的非旧v1返回2后进入正常stop/drain；其他登记失败立即退出并显示数字错误码，不再伪装为任务收尾，也不展示含路径的原始PowerShell输出。当前任务的正常安全收尾、旧未知业务不重放仍保持。

主控Windows PowerShell5.1语法检查、Runtime发行定向10项、最终package/verify通过；独立九组基线、另一个归档回滚组共10组通过，修正测试证明门后五类负向定点重测通过。归档前/后两处故障注入分别恢复旧启动项、原Run/absentOnce，事实保留；最终包EXE smoke PASS/exit0，Host系统锁释放且真实Run不变。最终PS SHA256 `05f3cef65a9fa93c4f6730acb48deabc11ba31c399fdb58dbb65c82fedfb3ebe`、NSIS `3943cf615e0d402f3da053996dd45d1ca9379b6506cd102afaae84731c894396`；产品及测试最终CR无P0/P1/P2。七个包内模块和EXE与r4验证字节一致，相关结果明确复用。

真实旧r3程序隔离启动两次未取得Host锁，已精确清理自有fixture，不列为旧UI与新guard成功联调；证明来自实际旧包摘要PS复现及真实builder Section+专有timer不请求旧维护入口。真实UAC、交互MUI遍历、重启/断电及客户完整安装卸载仍未实测。源码基线`ae10e453`加本次未提交修复，未部署服务器。

## r5 最终交付

- ZIP：`clients/release/AID-Work-Runtime-UI-BOSS-20261012-trial-r5.zip`，131019297字节、14文件；CRC和全部文件SHA256复读通过。
- ZIP SHA256：`96c1927632559632c3ba5a79a53f375dbd23489dfca760d121ae8e5ca41c2366`。
- 安装器126283157字节，SHA256 `7e81738d787ba73e35ae46a39633424c2a6d5b12a5f653fa3bb1fee9d1cab6da`；Windows NotSigned。
- UI0.0.2、Host0.2.14、BOSS0.3.1、Node22.23.3；Host/插件/发布者根逐字节复用r4。新增真实旧包跨摘要复现及最终启动报告随ZIP交付，旧ZIP未覆盖。

## r4 重装后管理握手代码11（2026-10-12）

客户自行重启卸载后重装r4，UI显示RuntimeHost.open的通用代码11。客户提供的只读摘要：已重启、runningMarker=null、home存在；config-unreadable、device/server均null；13次stop:reconciling及其他旧操作仍在，业务状态目录计数0，已知进程列表只有Electron。该摘要不包含secret或操作字段完整性，不能证明DPAPI/管理密钥损坏、Node完全不存在、配置已删除或无未知业务。

源码事实：loadConfig缺失/读取/JSON失败只返回null并进入未配对，不能单凭config-unreadable归因这条代码11。优先待区分management-secret不存在且operations存在、密钥读/解保护/hex格式、operations JSON/schema或persist IO、pairing.pending恢复失败，以及插件目录初始化原生IO。当前marker确实不存在才排除执行恢复分支。r5未改Host，不能称覆盖r5可解决此错误。

新增只读runtime-startup-check.ps1/bat仅输出布尔状态、计数和固定错误类别；不输出密钥/凭据/原始异常，不调用Host.open、recoverPairing、插件probe或任何业务恢复，不改Runtime home，仅生成Desktop摘要。PowerShell5.1 JSON检查不是直接Node解析，4MiB/权限/链接拒读标未评估，系统DPAPI上下文与Host环境区别明确。当前主控唯一维护这三个诊断文件，core/H1/Device/binding及安装器不改；独立测试/审查按敏感数据诊断风险执行。

最终只读工具真实Windows PowerShell5.1独立29例PASS/exit0，包括密钥缺失/损坏/合法CurrentUserDPAPI、JSON/BOM/字段类型及大小写、prepared_import数组真值、配对事务、oversize未评估与隐私sentinel。各隔离home文件和结构前后完全一致，仅独立输出目录生成JSON；未触碰客户home、真实Desktop、自启或业务。最终CR无P0/P1/P2；BAT仅静态检查，PS5.1实际执行/解析/BOM及BAT ASCII验证通过。

工具ZIP `clients/release/Runtime-启动检查-20261012.zip`，8741字节、6文件，CRC及全部文件复读一致；SHA256 `fe5e34f3d677837760f16f6b31ff5c59ddeb5d8c63f56dc9e8d121161c23641d`。PS SHA256 `16a17d57fb6f6456fd87fe37d0a4f54487c85715e597421ef682cb16c1277223`。包含英文BAT/PS、中文TXT说明、独立验证与摘要清单。此为Phase7历史记录；用户明确拒绝客户继续协助，Phase8改为完全自主复现，不再等待或请求新摘要。

## r6 自主复现与修复（2026-10-12）

在隔离目录运行实际旧包，23场景独立复现：只读operations文件会因构造器无条件persist而使管理握手代码11；坏管理密钥、操作记录或配对事务也导致管理面不可打开。PATH中的同名PowerShell可使旧进程检查失败。单纯缺失/坏JSON配置进入未配对，不能归因该通用错误；PS7模块污染测试未复现，不将其说成客户根因。

r6取消启动时持久化旧操作，仅校验与内存投影；实际mutation仍要求合法密钥与可写账本。读取配置/操作兼容单个UTF8 BOM，严格校验字段，拒绝额外载荷和非法身份类型。CurrentUser DPAPI仍使用原格式，解保护兼容原ASCII折行；DPAPI、LegacyCIM、Job ownerStart共用固定绝对系统PowerShell及受控进程环境，不依赖PATH中的替代程序。

Runtime_app在代码11时可保留管理lease、H1只读状态和现有私有退出协议；既有文件/密钥/marker/业务事实不清空，CLI仍保持严格失败。初始化前设置显式home作用域，防止读取默认设备；无配置但有旧pair/start的succeeded/running/reconciling记录时保持原身份待核对。只读管理不是自动重建身份，也不宣称云端已连通。

主控core management/startup/DPAPI 40例、Runtime lifecycle/host/process cleanup 17例、Desktop supervisor/startup/update 25例通过，相关TypeScript检查通过。独立新版37场景包括真实managed-entry、实际H1消费者、坏密钥/记录、BOM、隔离home、CLI严格兼容及只读退出。正常有效配对的本机假云端start/heartbeat/模拟MCP调用/stop/exit，实际Windows Job回收及lease释放通过；原18条历史保留。真实BOSS账号/客户云端未调用。

独立最终CR无剩余P0/P1/P2。UI0.0.3、Host/core0.2.15，依赖未升级；Runtime源码已由外部操作提交为`1d67fa42`，本会话未执行新增提交或部署。最终12个包内模块与独立验证字节匹配；实际EXE smoke及坏密钥下历史只读维护退出均PASS/exit0，5个自有子进程退出、lease释放，旧secret/ops/业务事实与真实Run原样。NSIS/PS摘要与r5十组证据一致，明确复用。

旧r5管理失败接管探针仅部分通过：实际Section日志为Prepare2/NO_LEGACY→maintenance_sent→Restore0→section_completed；Main退出码两次有界等待未确认，自有路径进程最终核对0。测试以同SID隔离ExecWait代替ExecShellAsUser，真实UAC/Explorer传递未测，不将Section完成日志当完整覆盖安装或客户恢复保证。客户精确资料故障、原身份恢复与真实云端连接仍未证明，不再向客户索取材料。源开发及隔离发行验证完成，与现场恢复结论区分。

## r6 最终交付

- ZIP：`clients/release/AID-Work-Runtime-UI-BOSS-20261012-trial-r6.zip`，131032987字节、17文件；CRC及全部文件SHA256复读通过。
- ZIP SHA256：`81a5d5a0fa42997de715eeb1f9a450c192f1cb25cdf40d1f3dfc024bd8dc3e38`。
- 安装器：`AID-Work-Runtime-0.0.3-win-x64-acceptance-unsigned.exe`，126285801字节，SHA256 `6305e8f8f87714338776716e946b7b834e877dfbb698e4aacfb06ed46c1b1263`；Windows NotSigned。
- Host0.2.15 TGZ SHA256 `e29a4ae62b1ae38dc41cab924170c2c0b4f8c92155e5b7055e1768a4bedf3c4d`；BOSS0.3.1保持22工具、签名物理输入依赖及原发布者根。Host/Node已内嵌，不要求客户安装TGZ/npm。
- 包含HTML/MD配置迁移说明、版本与文件摘要、独立37场景/实际包/CR、旧包复现与旧r5部分接管报告；旧r1～r5交付ZIP保留。未部署服务器，交付说明更新未新增提交。

## Phase9：连接失效与恢复路径的确定行为

本节来自客户端/服务端代码及隔离验证，描述当前实现，不要求客户尝试命令、删文件或补日志。r6的37场景证明管理可读和退出，不证明下列资料损坏有自助恢复；此前将这两者混称“恢复”的口径撤回。

### 三种不同操作与设备名

- 页面顶部按钮调用`RuntimeController.connect`，仅建立本机Main→Host管理连接、describe/getState/plugins.list；不会调用start、云端心跳、续期token或pair。本次源码改名「重新连接管理服务」。
- 原设备网络重连由运行中的PollLoop用原server/device token重试，退避1～30秒。手动已停止/升级保留停止意图时需正常启动；离线本身不要求换设备。
- pair使用服务地址、一次性码和名称。`src/local_tools/pairing.py`每次调用`repository.create_device`，`repository.py`执行INSERT，不按名字或machine_fingerprint续用旧device_id。名称可沿用，但新pair创建新编号，必须在云端选定新设备；原任务不会搬过去。旧凭据缺失/被撤销没有原device token续期接口，不能称作“改回名字恢复旧连接”。

### 状态矩阵：当前有什么出口

| 触发条件 | 当前确定行为 | 已实现的恢复路径 | 缺口/本批处置 |
|---|---|---|---|
| 同Windows用户、同UI profile/home，config与credentials有效 | 安装器保留身份文件，Host显示原device；未开始时offline | 正常启动后以原token心跳；升级运行意图或登录自启按既有选择恢复 | 正常路径不要求重新pair |
| 普通网络/超时/HTTP非401错误且执行循环还在运行 | heartbeat显示offline并有界退避重试 | 网络恢复后自动以原身份重连 | 管理重连按钮不代替此过程；非401拒绝也不自动判断为token有效 |
| config结构合法但服务地址错误、TLS/DNS/接口404或403持续失败 | 非401仍投影offline，循环可能持续重试 | 正确的原服务网络/接口恢复后才可同身份连接 | UI目前没有保留原device/token的服务地址修复动作；只能输入新pair不能算原连接修复 |
| runtime已手动停止，管理状态stopped/offline | 没有heartbeat循环 | 点击已有start；不重新pair | 属执行停止，不应指引用户更名 |
| credentials缺失/无法解密，config仍有效 | start报9；已初始化资源清理成功后回stopped | 无旧fence/未结案事实时可用新码pair；会创建新device | 同用户DPAPI环境需合法，不能替用户恢复不存在的旧token |
| heartbeat/claim收到401 | connection=revoked；循环shutdown；当前owned执行进入reconciling | 正常stop成功回stopped后，允许条件满足时新pair | 旧UI禁用reconciling stop，本批补回；不保证存在历史未结案时也能pair |
| 待核对属于当前owned执行，stop本身能成功 | core本来允许stop/drain，清当前marker回stopped | 用户发起已有stop检查 | UI/Controller过去受历史operation/reconciling锁住；本批修复，不改历史op成功状态 |
| 旧最后operation为reconciling，新Host实际已经stopped | backend按当前state/身份事实校验新请求 | 新明确start/stop/pair，仍按Host门禁 | 原Controller重连保留旧operation造成永久UI锁，本批移除此错误门 |
| 仅旧running marker、当前Host未owned | 状态reconciling，stop返回11 | 下次Host open经ProcessSupervision核验同boot Job/同session或真实boot变化后归档marker | 资源恢复不等于业务结案；下面的fence仍存在 |
| config缺失/坏JSON/不可读，且旧pair/start succeeded或reconciling（running投影） | r6 Runtime_app直接blocked只读，拒绝所有mutation | 目前只有退出；再次管理重连、重启或重装仍读同一条件 | 无自助修复入口。已确认产品死路，r6不能用作此类配对恢复方案 |
| management-secret缺失但operations存在、secret解保护/格式错，operations坏schema | r6管理只读；坏ops不伪装为空历史 | 管理查看/退出 | 无管理资料修复入口；密钥与设备token是两个文件，不能把管理坏密钥当作服务器token撤销 |
| pairing.pending合法事务且CurrentUser解保护成功 | initialize恢复写credentials/config后删除pending | 原事务自动完成，不再次消费码 | 不会自动提交另一台设备 |
| pairing.pending坏JSON/结构/密文/写入失败 | initialize报11只读；原事务保留 | 目前只读/退出 | 没有安全撤销/恢复事务入口，重新pair也被阻断 |
| identity-reconciliation.bin存在 | canReplaceIdentity第一项即拒绝；enable/disable/uninstall也拒绝，import例外 | 原config/token正常时允许同device start | 全部非测试源码只有写入，没有清除/归档/核对完成入口。即使其余proof全合法仍拒绝pair；重启/卸载重装不解除 |
| result-outbox非空 | 拒绝pair/unpair | 原身份start重投原结果；2xx ACK先写原证明再删除条目 | 不重放原业务动作；5次启动尝试未成功条目仍在 |
| outbox gave_up/坏JSON/临时残余；原token已失效 | 仍非空阻pair；gave_up跳过、坏条目保留 | 当前无自助解决入口 | “恢复网络再试”不能解决确定性4xx拒绝或原token已失效 |
| journal有记录却无合法原ACK proof，或effect=unknown | 拒绝pair；不能推断为空outbox即成功 | 尚有原待传结果时原身份ACK可生成合法proof | 旧无proof或已ACK unknown没有追补/人工确认入口；仅重启不解除 |
| session任务为completed/stopped且全seq ACK、控制epoch合法 | engine drain写CurrentUser保护proof | 原身份会话引擎正常恢复/停止后可生成证明 | 已实现正向路径；原fence仍会先拒绝pair |
| session旧终态无proof/未ACK/blocked/坏目录 | 拒绝pair，即使当前sessionTasks未启用 | 启用原身份引擎且满足终态/ACK条件才有正向路径 | 不满足条件时无显式对账/归档入口，不能叫用户重复配对 |
| 相同home的另一Host仍持有lease | open报4，不会抢占 | 原实例正常退出后新管理连接；X仅隐藏托盘 | 管理重连不能强关另一实例；不能凭屏幕关闭推断进程退出 |
| 旧CLI同用户进程仍在、插件目录/原生资源/系统保护初始化不可用 | Host初始化拒绝或进入只读，具体分支由既有错误码/阶段消息确定 | 原实例正常停止、可信安装资源和CurrentUser保护环境正常后重开管理 | 尚无应用内修复资源/ACL/系统DPAPI的动作，不能笼统用新配对覆盖本机故障 |
| 旧CLI→acceptance UI、不同Windows用户或不同profile | 新home不自动读取原身份，跨用户DPAPI不能解密原密文 | 按既有隔离迁移方案新pair | 是不同身份目录，不能承诺保留原连接，不能用设备名称判断同一设备 |
| 本实例runFailure非空、stop已释放部分资源后仍抛11 | owned执行停留reconciling；连续stop仍拒绝 | 当前只有Windows资源恢复流程，缺正常本实例故障退出闭环 | 不在本次局部UI修复中冒称已解决 |

### 已确认缺陷的源码修复与剩余恢复工程

本次补齐ProcessSupervision.marker、recordOperationAck的单UTF8 BOM读取。旧r6仅loadConfig兼容，Windows marker实际JSON读取仍失败；原ACK取不到身份会生成resolved=false。测试区分单BOM与doubleBOM/unknown，不能把未知业务变已完成。

管理operation在reconciling时经message保留原固定错误及编号，不再用统一“执行停止事实”遮掉具体原因；保持已有H1规定的code0/error空，真实消费者与退出重开共同验证。UI终态历史op不再成为永久操作锁；可发起新明确stop或当前状态允许的动作，仍由Host当前执行所有权/身份门决定是否放行。运行中/接单中、连接不确定或状态未刷新仍保持原限制，未自动重放旧请求。该源码修复不覆盖已发r6 ZIP、不新增服务器部署或提交。

完整自助恢复尚未实施，应作为下一阶段独立工程：先输出机器可判定的阻断类别和允许动作；把管理控制资料修复与设备身份重建分开。原身份能恢复则验证原server/device/token；原身份不能恢复时提供明确确认的新设备配置流程，保存原证据、证明旧执行资源停止、旧任务固定旧device且不重放。只有历史缺证明时应提供有授权与审计的核对/归档出口，不能由installer清记录、凭空生成业务ACK，也不能把永久fence当恢复机制。需要同时更新并验证Host与UI消费者，不能再只验证启动/退出后交客户。

本批主控core管理/ACK读取20例、真实Windows native+DPAPI BOM marker1例、前端Page/Controller28例通过；core及Runtime TypeScript检查通过，Web与最终Desktop生产构建通过。审查曾发现reconciling非零code/error违反H1的P1，以及importPackage历史门和网络重试文案P2，均已修正并最终复核无P0/P1/P2。独立r6固定旧包九例恢复矩阵明确验证正常路径与持续死路，报告位于`clients/agent-desktop/build/runtime-recovery-closure-20261012/r6-recovery-path-independent-validation.json`，不将其算本次新源码的复测。新源码闭环独立验证另登记，不重新发行r6，不部署、不提交。

最终新源码独立8场景通过，报告`clients/agent-desktop/build/runtime-recovery-closure-20261012/directional-fix-independent-validation.json`，5个compiled/native模块与隔离测试快照摘要一致。后端5场景：真实Windows/DPAPI BOM config start→heartbeat→stop、BOM原ACK证明、owned reconciling stop/pair及H1重开、真实FakeCloud401撤销后stop/pair、passive stop仍拒绝并保留原事实。前端3场景：实际H1历史回复消费后的新start、验签导入入口选择、新stop及仍由server guard拒绝pair。均exit0。未实际安装新补丁/新发行，也未宣称缺身份/fence可自助修复；像素布局未另验，DOM交互与构建已验证。

CLI在config缺失+旧pair/start记录时没有同等Runtime_app只读身份门，属于共同入口兼容缺口；不得建议客户借CLI绕过UI。本节记录当前事实，未改变CLI行为或冒称其已修复。

## Phase10 最终交付与服务端状态复核（2026-10-12）

当前交付r7：UI0.0.4、Host/core0.2.16、BOSS0.3.1、Node22.23.3 win-x64 ABI127，Windows未签名验收包。H1候选0.3、Device/binding不变；H3 bridge3增加Runtime-only可选恢复端口，直属connection v1严格绑定instance/request/generation，普通Desktop不暴露。

- 固定原配置/凭据的真实heartbeat `{}`探针，严格success=true/selected boolean，不启动claim、不改能力。DNS/TLS链/网络/401/403/404/5xx/坏shape分类给步骤；selected=false提示网页选设备，心跳不冒充远端ID匹配。
- 保留原配对仅归档管理secret/ops/retired，业务/session/plugin/fence保持作用。marker留到completed受保护intent、原identity/anchor及资源独立核验成功，再交既有Core归档；known retired keys不截断，缺文件由completed intent恢复，坏文件在initializer前只读。
- 新设备恢复先实际退出旧Host并重新取得同home OS lease、二次资源复核，再同父整home原子归档，新epoch不迁移旧业务。CurrentUser保护sidecar/fsync/anchor让CLI与Host续办同一次事务，不自动重用OTP，不伪造ACK。
- 网络drain卡住仍可仅登记重启证明，登记后拒绝排队/新start、pair和插件变化，保留stop。仅清理同home/generation/boot/session已登记Job；未知资源正常Windows重启。Main恢复与退出/维护/选包互斥，迟到回执不清停止意图；第一屏错误可跳到步骤，确认与OTP即时清除。
- 安装器30秒有界等待；PrepareDeferred仅登记同产品/SID/scope/hash的受保护续装控制、备份Run并临时关闭本产品旧自启，不创建resource witness或ACK。重启后续装，未知资源仍需客户端独立核验；不承诺所有情况只重启一次，UAC原用户/HKCU归属不变。
- Windows Electron appData忽略APPDATA覆盖；显式SMOKE=1且SMOKE_USER_DATA时才采用隔离目录，正常路径不变。实际包在新临时home启动，epoch+mgsecret生成，Main退出0，Host lease释放，真实Run/RunOnce不变。

### 服务端标记的精确边界

| 状态 | 心跳 / 新配对影响 | 处理 |
|---|---|---|
| device非active、旧token无效/已撤销 | active token_hash鉴权返回401；重装不恢复旧token | 新有效码建立新active设备 |
| selected=false | 不拒心跳，业务selected/online/catalog门受限 | 网页选定本次配对设备，名称相同不代表原编号 |
| 配对码过期/已用/新码作废旧码 | 五分钟、单次原子消费 | 使用最新码，不自动重复不确定请求 |
| invocation unknown、会话blocked、binding automation_blocked | 对应业务准入阻断，不在心跳鉴权路径 | 按业务恢复流程处理，重装不清业务事实 |
| 机器指纹/名称 | create_device无旧撤销封禁查询；DB仅token_hash唯一 | 可新建active设备，不自动转移原任务/binding |

已核对API、pairing、repository、device_policy、DB初始化SQL及会话/绑定调用链；未核验或修改客户实际数据库状态，未部署服务器。

### 验证证据与限制

独立HTTP/TLS/伪造IPC/归档结果、Main并发与smoke目录、UI确认/入口验证通过；20坏home及11真实子进程rename/crash/多轮tombstone有可核验原实现字节相等与依赖SHA复用证据。资源作者自身22单元测试、Root自测未冒充独立角色。安装器9行为场景/8测试覆盖登记、重复、轮换、Restore及失败回滚，原业务/resource witness字节不变；真实builder另21例通过。

Root core23、资源/BOM23、Desktop生命周期25、Frontend基线58及最终独立UI8、schema7测试/63fixture、web/desktop build通过。CR无P0/P1/P2，绑定28源码SHA，8个换行等价例外明确登记。最终实际EXE/asar与独立编译、installer/manifest匹配；临时home、退出与lease、真实Run/RunOnce验证通过。

报告在 `clients/agent-desktop/build/runtime-recovery-phase10-20261012/`，并收入ZIP。真实UAC、客户重启、RunOnce登录与BOSS业务仍属现场验收，不把模拟boot或隔离测试当客户已连接。当前Runtime修复未提交，外部无关data-analysis提交改变HEAD已登记。

最终ZIP：`clients/release/AID-Work-Runtime-UI-BOSS-20261012-trial-r7.zip`，131048505字节、16文件，SHA256 `370402410e04efcabe82a31d6295cbb4e117d0463c3fef27871742e4012498d9`。安装器SHA256 `6c85a82cf0195adf133967464ae6dab824a085b399154db3714358aba457f3b1`。


## r7 提交前 agent2 实际状态复核（2026-10-12 23:37，Asia/Shanghai）

用户授权只读查询 agent2，已通过 SSH 检查实际业务库、观测库及 Nginx 访问日志；未部署或修改服务器数据。给定 trace 与用户、租户和设备记录一致。笔记本与办公电脑均为 active、未撤销；笔记本为 selected，最后心跳 13:33:07，办公电脑最后心跳 10:26:31。该用户未结束 invocation 为零、session_tasks 与 BOSS conversation binding 均无记录；办公电脑三条历史 unknown 已有 finished_at，不构成笔记本连接锁。

13:07 的 boss_goto trace 返回 DEVICE_UNAVAILABLE，明确原因是选定设备未上报 BOSS 能力；当前笔记本 providers 和 provider_manifests 都为空。13:24 心跳曾出现短暂 502，随后恢复 200 至 13:33；之后没有新成功配对或设备心跳。18:09 客户诊断为 config-unreadable、deviceId/server 为空，与本地管理初始化失败一致；不能据此认定具体 DPAPI 失败原因。当前实际证据不支持服务端撤销或永久锁定，不需清除服务端标记。

提交核对：fetch 后当前 master 与 origin/master 一致；当前源码摘要与独立测试、CR记录一致，实际安装包启动/退出验证可复用，r7 ZIP 摘要保持不变。仅提交本次 Runtime 修复、测试、契约、版本及发行说明；无关 plans/session_replay 文件保留，不暂存。

## Phase11 实施前共同检查点（2026-10-13）

主控唯一修改 Runtime 页面/Controller及定向测试、Desktop package/lock发行版本和配置说明；独立测试与审查只读产品代码。H1候选0.3、H3 bridge3/connection v1、Device API、身份和业务恢复事务均不变；只读 describe 未协商 first_party_plugins 时不查询插件列表，不把稳定故障显示为持续刷新。页面处理入口不受诊断busy禁用，故障步骤在首屏直接展示，取消启动时自动诊断，显式检查才进入忙碌。用户旧配对未知时不误导其已丢失；修复管理资料与新设备配对仍经已有资源/事务验证。UI版本规划0.0.5、Host/core继续0.2.16；必须复现旧包并验证新包故障点击、保留/新配对恢复和连接步骤后才交付r8，现场Windows登录与UAC限制如实保留。

Phase11范围补充：独立真实32个历史监督账本诊断耗时56.754秒，确认读取历史资源与配置/服务器检查耦合。主控唯一修改connectionRecoveryInspection，将diagnose限定配置/凭据/服务器检查，resources_safe固定false；资源证明仍在明确恢复确认的prepare动作与Main持锁二次复核中完成，不降低归档门。Host版本升为0.2.17、Core继续0.2.16，UI0.0.5；connection v1字段不变。历史账本较多的prepare操作整轮超时/协作取消仍是已发现遗留，本批不以诊断成功冒称资源安全。

### Phase11 已完成源码与构建核对

独立r7实际EXE复现：只读state已读取但未协商插件列表仍被查询，pluginsDirty导致假刷新；两枚步骤按钮在自动诊断期间真实disabled=true，检查后恢复enabled且实际滚动成功，说明交付包保留了忙碌禁导航缺陷。客户管理secret缺失的起因仍未证明，不将自造fixture当客户事实。

本批取消自动诊断、导航只负责展开/滚动/聚焦且永可点击；故障静态步骤置于错误下方，原配对信息不可读时不冒称已失效；只在协商插件能力时刷新列表，refreshing跟随真实pending并绑定generation。62前端用例包含旧刷新结束不清新刷新竞态；Windows managed/resource26例通过（新增2例持久回归）；Web构建、最终Desktop构建、Host0.2.17构建通过。独立CurrentUser/假云压力32ledger诊断由56.754秒减至4.767秒，历史读取/CIM/native/Job动作全部为零，resources_safe仍false；显式prepare安全门继续拒未知资源，未归档或改原凭据。

原builder下载等待中止，仅终止本任务已确认PID/父PID的builder进程。使用已安装Electron43.1.0分发包，75文件逐一与固定npmchecksums ZIP摘要a07dc1e3d5e589593d37e3b19d1b373e02bb58270e2eb0d6633eee0198ad09f0一致；ignored包装脚本仅调整import定位及electronDist，原发布验证保留，独立CR额外绑定包装脚本/分发proof。r8候选安装器0.0.5验包NotSigned通过，126391574字节、SHA256087bcec42b6a31f63a318d23d236556af16edd8ccb83b3f8552a3d9fee180c71；最终包显式隔离smoke退出0、Host lease释放。所有证据在build/runtime-recovery-phase11-20261013，实际UI恢复独立验证待完成。

Host发行包0.2.17、共享Core0.2.16，H1及常规心跳上报继续使用Core版本0.2.16；UI安装器0.0.5与Host包摘要区分本次交付。未改版本读取协议，不将两者混为同一版本。历史资源大量积累时显式prepare可持续约一分钟，原整轮超时/协作取消仍是遗留；本批不声称真实客户重启/UAC/agent2配对/BOSS业务已验收。

### Phase11 最终交付（2026-10-13）

独立实际r8 EXE的renderer/preload/Main/Host恢复5组全通过：缺密钥导航/focus与显式诊断、保留原设备编号和凭据的管理修复、新配对仅一次并验证新编号/心跳、18条历史start未知资源仅登记真实保护的重启见证且不归档，以及两个恢复后home的UI启动→真实running/online常规心跳→停止→marker移除→正常退出。三个基础场景与两个执行场景均Main退出0、实际home OS lease释放；原身份/既存账本保留，真实Run/RunOnce摘要不变，0claim/0业务调用。9个包内程序模块、6个包内renderer资源与当前编译相等，16个源摘要绑定。

报告`clients/agent-desktop/build/runtime-recovery-phase11-20261013/independent-validation.json`包含真实case与失败尝试边界；首次r7仅APPDATA探针未证隔离且debugger退出未算成功，r8均先暂停确认fixture appData再运行并正常退出，不把这些缺口隐去。源码CR无遗留P0/P1/P2，最终源码/测试/安全依赖摘要与当前字节一致。未做真实UAC/Windows重启/客户agent2配对或BOSS业务。

ZIP：`clients/release/AID-Work-Runtime-UI-BOSS-20261013-trial-r8.zip`，131135565字节、17文件；CRC和逐文件SHA256复读通过。SHA256 `127f9193c2130055af2a82706f6f998694658a715e8d8338fd1c0eeb4f9475ee`。包含UI0.0.5未签名安装器、Host0.2.17/Core0.2.16、BOSS0.3.1、HTML/MD安装配置及处理步骤、VERSION、清单与验证记录。未覆盖旧r7 ZIP；本批未提交或部署。

### r8 提交核对（2026-10-13）

用户明确要求提交。fetch后远程新增HTTP错误识别与通知路由两个无客户端重叠提交；仅临时保存configs/config.yaml及src/config/settings.py的搜索任务改动，快进至e792ee29后还原，其新增/删除行逐项一致，无冲突，其余搜索任务文件和索引保留。Runtime源码与独立CR/实际包验证摘要一致，最终构建、真实EXE关键启动及五组交互结果可复用，不重复无关检查；r8 ZIP字节和SHA保持不变。本提交仅Runtime相关17个文件，不包含搜索任务、运行配置或会话回放。服务器未部署。

## Phase12 r8重新配置后仍报错的实际审计（2026-10-13）

当前仅完成只读调查和原r8实际包缺陷复现，未修改产品或重新发行。新证据表明r8存在P1：待完成的资源重启步骤会被连接身份检查覆盖，导致用户可能同boot循环填写配对码；此前Phase11正向新配对用可证明fresh epoch，未知历史用原配置有效样本，未覆盖本次缺config+缺management-secret+旧启动历史组合，不能把那五组通过等同所有混合故障闭环。

### 服务端实际记录（13:08至13:12，Asia/Shanghai）

只读SSH/业务库/访问日志确认，同租户同用户原孙晨笔记本/办公电脑两记录已在今天11:01:13及11:01:15撤销，日志对应DELETE200；这不同于昨晚23:37的active快照，不能沿用旧结论。另一新记录陆伟笔记本11:27:11成功配对，目前active/selected且持续心跳，不能将该名称当客户这台电脑已恢复。13:02:24最新码used_at为空，13:07:24已过期；13点至查询时Nginx无runtime/pair请求，只有正常另一设备心跳。表仅id/token_hash唯一，无机器指纹/名称唯一约束，无device触发器；当前pair实现也无旧revoked/机器封禁查询。该用户无session_tasks或BOSS绑定阻断。旧撤销使旧token失效，但不禁止新有效码创建新设备，更不能产生客户端CONFIG_MISSING。

### 客户截图对应的确切代码条件

- Core management173/187：当前home管理secret不存在且operations文件存在，runtime_app进入只读blocked；不要求旧start才产生该文本。
- connectionDiagnosis54：当前home的config.json未被existsSync检测到，返回CONFIG_MISSING；此前当前用户DPAPI及目录读写探针已通过，此分支不解密旧凭据、也不请求服务器。密钥和配置最初为什么缺失仍未证明。
- Main reconfigure先prepare_new资源门，未确认停止时直接返回action_required/new_device=false，尚未执行archive或pair，用户输入码仍未发送。缺marker、没有可证明fresh epoch且有旧start历史的home触发本次boot重启见证登记。首次返回REBOOT_PREPARED要求正常重启，不表示已经重新配对。
- 恢复后的自动connect(recovering=true)本身保留结果；但再次点击连接检查会无条件替换诊断成CONFIG_MISSING/requires_reboot=false；普通管理重连会清诊断。轻量诊断不投影已登记witness，于是重启入口/说明消失，用户被再次引导填码。
- 输入未实际确认、资源门/退出/lease/本机保护写入失败均可停在pair之前。真正发出pair后的无效码/网络/本地提交失败应投影PAIRING_INCOMPLETE等错误；若之后再查，也可能掩盖阶段，但新home已经生成管理secret，不能将正常成功pair解释成截图中的原只读状态。

### 实际r8包复现

独立角色使用未修改r8 EXE和真实preload/Main/Host，分别构造凭据缺失/存在两home：均config/secret/marker/epoch缺失，按原诊断构造13 stop reconciling+1 stop succeeded+2 start succeeded+1 import+1 pair共18条记录。两组实际UI提交new_device后产生真实CurrentUser DPAPI witness、pair/HB/allHTTP均0，原事实不变、无归档；再check确实返回CONFIG_MISSING并隐藏重启按钮，管理重连清诊断，witness SHA不变。每组Main正常退出0、OS lease释放、真实Run/RunOnce不变。不能据此断言客户按了同样顺序，服务器未收13点pair与该前置返回路径相符。

报告：`clients/agent-desktop/build/runtime-recovery-phase12-20261013/r8-mixed-home-reproduction-independent-validation.json`，DEFECT_REPRODUCED，2组/10阶段，SHA943c03a6386ff691d7e17b78a37e9c80dada6d650414b996ed6a085dc6368008；绑定r8安装器087bcec...，不修改已交付ZIP。

### 确定解除条件与待修正项

现有门在已登记witness后检测到真正不同kernel boot、资源digest稳定、无旧CLI、系统检查可用时才能继续；此前未登记的重启不能证明本次历史状态，因此不能只泛化建议重复重启。解除前没有发送配对码；解除后使用当时新生成有效码，成功写config/credentials再验证心跳并网页选设备。

需要将身份故障与待完成的恢复步骤独立投影，检查/重连不得丢失已登记重启提示；资源预检应在要求用户填新配对码前执行，而不是输入后返回。跨程序重开必须依靠受保护的步骤记录，不能只在UI临时记状态；不恢复耗时全量自动诊断、不用提示状态冒充停止证明，最终prepare/Main二次门仍必做。产品修复待实施，不再用正常fresh home验证冒称这一混合路径已修复。

### Phase12 配对前置条件合理性审计（2026-10-13，尚未实施）

用户明确要求逐项评价条件及可否去掉，不能仅修补重启提示。当前Main先暂停/处理旧执行、prepare资源核验、Host实际退出、取得原home lease、受保护归档、重新初始化，最后才调用pair；因此任一前置失败都可能令用户确认后没有HTTP请求。纯配对是服务器发放新的设备身份，接管旧目录及使用执行资源是不同动作；未来需要分阶段，不把未结案历史当整个客户端的永久连接锁。

| 条件 | 当前影响 | 处理与合理性结论（拟议，未改变现有代码） |
|---|---|---|
| 明确用户确认、合法服务器地址与有效单次配对码 | parseInput/Host/Server验证，不合规则不发送或400 | 保留；一次性码应在本机准备之后要求输入，明确未发送/已发送/服务器接受/本机保存阶段，不静默返回 |
| 当前用户DPAPI、配置目录读写与落盘能力 | 诊断不能验证则can_reconfigure=false；新凭据保护/事务写入失败中断 | 保留新凭据安全落盘要求；先检查并报告确切失败项，自动创建正确目录，可识别系统组件缺失/权限/空间不足；不能为恢复而明文保存token |
| 旧management-secret/operations/retired记录完整 | Core会在initialize前只读blocked并拒普通pair | 旧控制资料不能自动重放；但它们不应要求用户先修好才能建立独立新连接。隔离保存旧资料，新控制/配对事务独立建立；激活前仍保证唯一写入者 |
| 原config/旧credentials可读且原凭据验证成功 | preserve模式必须可验证；new_device允许缺失，但仍受资源门 | 保留原身份修复需要验证；新配对不需要旧凭据有效，不能因CONFIG_MISSING循环禁止它 |
| 当前实例仍执行或收尾、退出未确认、原home lease被占 | Main停止/退出/持锁失败便未到pair | 必须保留同目录写入与执行交接保护；可在独立受保护暂存区完成新配对，旧实例/旧配置不覆盖，新身份激活和新执行仍须安全交接。不能仅移走if后写旧home |
| 同Windows用户任意旧CLI start进程 | legacyProcessCheck无法证明home，现全局拒绝，不同配置也影响 | 对连接的限制过宽；应定位本设备/同home/确有共享资源冲突的实例，自动暂停已知owned执行，不杀全部Node；无法核实的执行仅限制接管/启动 |
| 历史start或缺marker/fresh epoch | 即使无当前拥有执行，也不能证明fresh，prepare写witness并要求本次登记后的真实重启 | 历史记录不是进程仍运行的证明。优先查询受信旧代进程/Job并确认停止，已证明则自动恢复；仅旧版无可靠证据时采用重启兜底。无需因此禁止服务器新配对 |
| 旧journal/outbox/session或身份核对fence未完成 | 普通就地换token的canReplaceIdentity门拒绝 | 同一旧身份原地更换不能混结果，门需保留；独立新身份隔离旧结果/任务即可建立连接，不要求旧业务全部结案。旧任务保留原device，不迁移、不伪造ACK、不自动重放 |
| 保护的witness/boot/session/resource摘要与归档anchor不一致 | 资源/归档不放行，可能重登记 | 保留交接和归档真实性要求；持久显示当前缺哪个证明与下一动作，实际相关资源变化才作废。管理刷新不能删提示；不能用UI缓存假装已重启 |
| 目录链接/系统helper/CIM检查不可用/档案过大 | 当前统一失败或多轮准备长等待，可能不给有效配对路径 | 支持经过规范化与绑定验证的合法系统配置路径，具体受控文件仍拒越界/替换；缺helper给修复安装动作。容量与有界处理保留，但大量历史不应使新连接不可用 |
| 双重恢复/安装/退出并发、10/120/300秒等期限 | busy挡重入；Main超时不取消Host会留后台检查 | 互斥保留但不得残留永久busy；补整轮deadline和实际子检查取消/收尾，配对未发送需明确展示。检查超时不得假称资源已安全或自动重用OTP |
| 网页selected/BOSS插件ready | 不是pair前置；影响业务路由/执行就绪 | 不应新增为新配对限制，配对后分别给选设备、装插件/登录的步骤 |

推荐阶段：连接基础检查（可写、凭据保护、地址等）→旧资源准备/步骤状态明确显示→适时输入新码并发送一次pair→加密持久保存新身份并验证服务器心跳→确认旧执行资源停止后激活新身份与启动。若旧执行暂不能收尾，允许先在独立受保护暂存身份中完成配对与验证，页面说明“新设备已配对，旧执行仍需停止后才能接新任务”；不覆盖旧home、不启动新claim，故既不能假连也不让旧历史堵住新身份创建。是否采用独立暂存及共享H3 DTO扩展须先登记共同接口/唯一写入者/兼容方案，再高风险开发及独立验证。

上述是评估与拟议调整，不是r8已有能力。当前源码/包不变；当前已证主要死循环为旧控制资料缺失+旧启动历史触发资源前置，后续检查覆盖重启步骤。本次审计没有修改服务端标记或清旧任务；本次提交仅记录调查证据、待修清单与任务状态，后续逐项修复。

### 用户收敛决议：失效连接重新注册与旧设备注销（2026-10-13，待实施）

用户明确选择简化产品流程：客户端用于替用户执行工作，不要求用户查找密钥、操作备份或理解身份恢复。此前清单中关于备份恢复原身份的讨论仅为技术可能性，当前没有产品备份恢复入口，不作为可交付处理步骤；本节取代将原身份找回作为故障处理目标的建议。

- 正常升级保留有效连接；凭据与配置仍完整时自动继续使用，不要求用户选择恢复模式。已有可验证的配对落盘事务可在内部自动完成，不暴露为用户备份操作。
- 配对资料缺失、损坏、不可解密或设备已撤销时，统一引导重新配对、注册新设备。旧管理资料隔离保存，不成为新注册前置条件；不开发无资料情况下找回原设备编号的功能。
- 用户只需企业服务地址、设备名称和网页生成的新配对码；服务器在可信用户授权后向客户端直接签发新凭据，客户端安全保存。用户无需获取、查看、复制服务器或本机的密钥。
- 新设备注册、凭据本机提交与真实连接验证完成后，再处理指定旧设备的撤销；任一步失败都不能显示整体完成或提前破坏仍可用的旧连接。旧资料隔离与执行资源交接仍须真实检查，旧任务结果不迁移、不重放、不伪造结案。
- 当前服务端支持登录用户撤销自己所属设备，但普通配对码没有替换旧设备的绑定，Runtime也没有可信的撤销旧设备授权。因此自动注销是待开发能力：应由网页“更换此设备”生成绑定目标旧设备的替换授权/配对码，服务器校验租户、用户及目标设备；客户端完成注册后由对应协议处理撤销。不能仅凭客户端传入device_id、名称或机器指纹授予撤销权限。
- 本机资料全失时，设备名/机器指纹仅可帮助展示候选，不能当旧设备归属或替换目标的权威。有明确授权目标时自动处理；无法确定时由已登录网页用户选定旧设备。不撤销同账号其他电脑，不因旧设备暂时无法确定或清理失败阻断新注册与连接。
- 撤销意味着旧凭据失效并取消选定，不删除旧设备关联的任务、执行事实或审计记录；旧执行尚在处理时，记录真实中断/未确认状态，不把撤销等同业务取消完成。新身份启动仍须完成旧资源交接。

后续逐项修复以“重新注册可成功、旧设备可准确注销、用户无需处理密钥与备份”为验收目标。新增替换授权、撤销时序及H3投影须先完成共同契约版本/唯一写入者/兼容检查；本轮仅更新方向，未修改产品代码、未提交、未部署或操作实际设备。

### Phase13 开发共同检查点（2026-10-13）

本批高风险开发主控负责Runtime core注册事务/Main/preload/renderer及共同文档；windows_process_supervisor负责服务端持久绑定、迁移与Web入口；runtime_test独立验证，runtime_review独立审查。四条路径冻结：普通旧客户端配对不变；替换票据仅protocol2可消费；新身份注册及加密持久保存先于旧资源门；同一受保护registration UUID对应唯一归档epoch，旧执行停止证明仍决定激活而非是否发送pair。

注册事务位于固定home旁独立受保护目录，持有该目录OS lease；先预检DPAPI和可写落盘并记录requested（无配对码），再发一次HTTP。响应丢失或落盘失败保留未确认事务，只有用户明确提交新码才开新事务，不自动重发；paired保存受保护的新凭据，不领取任务。状态检查只读持久进度，不扫描旧历史，不将过去心跳冒称当前在线。continue_registration不带配对码，同epoch资源核验/归档/安装/心跳后完成服务端指定替换；active阶段注销失败可重试，不重配对、不重归档。旧结果、插件/任务记录保留且不迁移、不重放。

兼容收口：新注册使用显式register_new，仅新可选registrationStatus能力存在时发送；旧new_device在新Main里消费码/Host/文件动作前明确CLIENT_UPDATE_REQUIRED，无悬空stage，要求完整更新。不同于此前将旧mode直接改成stage的初稿，该门经独立CR指出后修正。archive_id跨未完成事务的显式supersede持续继承，发布新requested前保留原权威state，仅原子替换成功才切换。55秒资源检查超时给重启登记出口；真实不同kernelboot+受保护同home/digest witness+双legacy检查+稳定snapshot证明旧资源停止时，跳过逐条旧ledger解密，但不改变业务结案。

H3仍version1，新增可选registrationStatus本机只读能力；仅存在该端口时启用continue_registration，旧preload不显示入口，旧renderer不接收额外DTO字段。注册未激活期间阻止本产品自动start/普通start/pair，保留stop/退出；旧CLI仍由资源门和原home lease约束，不冒称已停。HTTP拒绝重定向，凭据不跨服务根发送。服务端pairing_protocol2与replacement_pending可选，旧普通码接口兼容；complete受新token鉴权且请求不含oldID，关系由服务端绑定；竞争替换必须避免两个赢家。新设备不自动改selected，用户网页选定不会被迟到注销覆盖。

验收重点：实际r8双缺失/18历史混合场景pair恰一次并受保护保存；持久待办经检查/重连/重开不丢；无旧资源证明不激活/不注销；跨租户/用户、旧客户端、响应丢失、落盘失败、并发、重复complete均不误撤其他设备。最终独立测试/CR、构建及隔离实际启动证据完成后登记，不提交或部署服务器，发行包另按真实验证交付。

### Phase13 实现及验证登记（2026-10-13）

新注册位于独立同父目录，使用既有CurrentUser DPAPI保护整个记录和真实OS lease；新配对请求先于旧management/config/resource核验。requested不保存配对码，响应丢失不自动重发；paired记录持久保存后由不带配对码的continue_registration继续。旧资料归档采用同一个受保护archive_id，反复明确重新注册仍继承未完成归档链；写入失败保留原权威状态，不能因此解除启动门。激活后先核对config/credentials摘要和真实心跳，再调用服务端绑定的complete，失败保留已保存的新连接，不重复配对或归档。

服务端新增登录用户所属旧设备替换码，protocol2才允许消费；配对码消费、新设备和替换关系同事务。complete请求不接受旧ID或归属字段，鉴权新token并读取服务器关系；用户范围事务锁与旧设备持久winner保证并发仅一个胜者。重复完成幂等，其他胜者不被撤销，目标不存在给明确网页处理步骤。旧selected撤销后不自动选定新设备；旧任务、binding、grant、结果和审计均不迁移、不重放、不结案。部署迁移与Web入口需要一同发布，不将本机测试当agent2已升级。

本批UI0.0.6、Host0.2.18、Core0.2.17，BOSS仍沿用已验证0.3.1插件，不混入其他任务的BOSS开发改动。说明已更新新注册两阶段、指定旧设备替换、未知响应、保存失败、重启资源核验及服务器配套要求。

源码验证：Runtime最终资源25项、新增终态409两项、Main/Supervisor13项、前端Runtime65项后追加能力门页面12项定向验证均通过；契约7项、8schema/63fixture和consumer15项通过。较早Runtime注册/资源29项在最终资源及409增量前完成，增量由上述相关验证补齐，不把旧快照冒称完整最终重跑。Desktop/Host/Core/Web构建通过；包内13模块、6renderer资源与最终dist逐字节一致，独立CR绑定26产品源码、11测试和4安全依赖，无遗留P0/P1/P2。

独立验证：真实HTTP/CurrentUser DPAPI六场景及compiled Main/真实Host两场景通过；最终资源时限/跨boot快速证明/两个409四组补充通过；后端隔离nonce PostgreSQL33项、附加真实路由/事务四项及Web17项通过。旧renderer/newMain拒绝在任何Host/文件/HTTP前完成，旧配对码未消费；实际不可变r8renderer/preload接收固定升级回复，非旧EXE直接替换Main联调。跨boot使用真实DPAPI和显式模拟kernel boot，未执行本机真实重启。

安装器为`AID-Work-Runtime-0.0.6-win-x64-acceptance-unsigned.exe`，126397790字节，SHA256 `a2ff37f732ec00099ff000c1dcf242665b2937a8f91f31e46bb4759a77659558`，NotSigned；Host TGZ SHA256 `9486449109f179805b43e53ed4c6039b0963d74e9f317dce72f5bb280eb22bd1`。实际包隔离smoke退出0并释放Host lease。证据目录`clients/agent-desktop/build/runtime-recovery-phase13-20261013`；报告明确区分源验证、包绑定与实际UI验证。

实际EXE混合旧记录两组已通过：每组pair恰一次、加密保存、检查/重连/重开仍有完成与重启步骤、不提前归档或注销、正常退出及lease释放。两组跳过真实自启写入，但组合脚本随后safe阶段失败，未保存其最终全局Run/RunOnce摘要，不能把源隔离门等同两组独立前后对比；后续narrow及同home补查有直接摘要对比。安全新home已完成真实UI配对、持久保存、旧Host退出和归档、新配置安装、心跳、指定替换complete、启动在线；停止操作succeeded0且marker已删除。首次停止后测试额外getState查询发生未保留具体错误的IPC拒绝，不能宣称不存在；独立角色在同一已激活home补查启停/退出，最终结果另列。期间导航等待和调试端口冲突属于已确定fixture错误，后者不能解释首次未知IPC拒绝。客户完整安装/UAC/真实Windows重启、agent2新协议及BOSS业务未验收；本批未提交、未部署。

补查结果：复用同一已激活home、原配置和凭据逐字节不变，不再次pair或complete；实际UI启动显示运行中/在线、H1状态确认、UI停止显示已停止并删除marker、正常退出Main0且Host lease释放、真实Run/RunOnce摘要不变、0claim/0业务调用。有效补查的IPC拒绝为0，仍不能据此证明首次未知查询异常不存在。独立综合报告须以有范围限制的通过登记，不冒称全场景无异常；本轮只完成开发与隔离验证，候选安装器不作为客户环境已通过验收的承诺，新组合ZIP尚未交付。

最终独立报告`clients/agent-desktop/build/runtime-recovery-phase13-20261013/independent-validation.json`，结果`PASS_SCOPED_WITH_UNRESOLVED_TRANSIENT`，SHA256 `9847c8a4c9df966eb71c60d592d2ca73ee1cfdcbbfa76f41249a2d3b78cde813`。报告绑定最终安装器/EXE、26源摘要、13包模块和6renderer资产；记录实际UI范围、首次未知异常、fixture修正及精确清理owned调试manager。结束时本测试自有产品进程为0。未生成或交付新的客户ZIP。

### 客户试用包交付前剩余清单（2026-10-13）

用户要求先提交当前代码，再列出将内部候选转为客户可用版本的剩余工作。以下是发布门，不代表已经实施；客户不承担研发排查或补充日志的工作，Windows安装/重启与服务端联调均由内部验证完成。目标仍为已明确授权的未签名试用ZIP，不将正式代码签名证书作为本批前置。

| 顺序 | 工作 | 放行条件 |
|---|---|---|
| 1 | 定位首激活后停止操作的异常，保留完整脱敏错误和生命周期时序；区分测试脚本与产品，有产品问题则修复及回归 | 原异常有可核验的归因；相关停止/重连/恢复时序可预测，UI不永久busy、退出不被假执行状态卡住，不能以重试成功销账 |
| 2 | 在独立Windows测试环境运行实际安装器，覆盖全新安装、有效旧连接覆盖、客户旧故障等价资料覆盖、正在执行时升级、卸载与重装；覆盖实际提供的安装模式和支持系统 | 无调试/跳过安装路径；旧实例自动协调、等待有界并给可完成动作；有效配置保留，损坏配置可重新注册；正常退出和卸载确实结束本产品进程，不影响其他程序 |
| 3 | 真正Windows重启验证登录自启、续装及混合旧资料的登记重启→完成连接配置 | 自动启动尊重用户选择；显式停止不被恢复成业务执行；新凭据跨重启保留，无需重复输入已消费码；旧资源真停止后可激活，提示不循环。模拟boot和当前Run不变检查不能替代此项 |
| 4 | 用户明确部署授权后发布agent2数据库迁移、API和Web，再用内部账号做完整新配对与指定旧设备替换 | 客户端请求实际到agent2并加密保存，真实心跳成功；网页选定新设备，指定旧设备准确撤销，其他设备和旧任务保留；旧客户端普通配对仍可用，网络失败及重复完成按预期处理 |
| 5 | 最终包内BOSS插件实际安装、登录、就绪、能力上报和打开BOSS业务链路 | 客户机不需Node/npm；服务器看到真实可用能力，插件未就绪有明确步骤；由内部测试环境验证打开和读取，不额外发送招聘消息。固定已验收插件版本，不混入另一任务未验证改动 |
| 6 | 逐项核对客户提示与真实失败状态，避免显示已连接却未完成、错误步骤消失或重新配对死循环 | 配对未发送/响应未确认/已保存待完成可区分；网络、权限、凭据保护、资源待停止和服务端能力缺失都有可操作下一步；处理后实际重新检查成功，不要求客户查密钥、恢复备份或例行提交日志 |
| 7 | 从最终提交重新构建并封装未签名ZIP，执行最终包关键启动/连接/退出检查、版本与逐文件摘要核对，更新简短配置说明 | Installer/Host/Core/插件对应已验证版本；ZIP包含安装器、插件、HTML/MD配置与故障处理说明、校验清单；报告无未解决的交付阻断，明确未签名及已验证系统/安装范围 |

第1项优先于继续扩大客户安装；第2和第3项共用内部Windows环境，第4项必须获得当场明确部署授权。已通过且源码/依赖/环境未变化的定向测试和CR可以复用，不机械重跑整库；产品修复、合并或最终重包后重跑受影响检查。

### Phase13 提交核对（2026-10-13）

用户当场授权提交。确认master上游为origin/master，fetch后快进至`b981e64d`；远程网页/通知/微信等17个文件与本任务产品改动无重叠，未stash或覆盖其他任务改动。26产品源码、11审查测试及4安全依赖摘要与独立CR仍一致，既有Desktop/Host启动与定向验证可复用；远程Web变更后补跑前端build通过，263源边界及1124包模块发行检查通过。只暂存本轮Runtime/共享core、设备替换API/迁移/Web、测试及对应文档，ideas仅暂存本任务索引行。内部安装器保持原摘要，不将当前旧候选冒称为最终提交重新构建的客户包；此次提交不部署服务器。

### Phase14 串行执行记录（2026-10-13）

当前代码已提交并推送`4192da65`。用户授权依次完成客户交付门，未授权服务器部署或后续自动提交。本轮复用项目开发流程；有产品修复时由主控开发、独立测试及独立CR，纯测试定位/文档登记不虚构产品修复。

| 项目 | 状态 | 完成记录 |
|---|---|---|
| 1 停止相关异常归因 | ✅ 完成（2026-10-13） | 实际首激活完整复现Error: button disabled，未发送stop；约0.1秒后UI自然ready，实际点击停止/退出/lease通过，产品未改 |
| 2 真实安装/升级/卸载 | 🔧 进行中 | 用户明确暂缓内部安装验收，由已同意协助的客户试装；Sandbox需重启但不继续操作 |
| 3 真实重启/登录自启/恢复 | 🔧 进行中 | 复用已验启动选择/恢复逻辑，整理现场步骤；真实Windows启动效果仍待现场 |
| 4 agent2配套部署与联调 | 🔧 进行中 | 用户明确自行手动部署；配套4192da65已推送，真实联调待部署后，助手不部署 |
| 5 BOSS插件与能力业务链路 | ✅ 完成（2026-10-13） | 最终0.3.5实际UI导入/ready/HB能力和Device→Runtime→MCP只读调用ACK通过；真实agent2业务待现场 |
| 6 故障提示与处理闭环 | ✅ 完成（2026-10-13） | 独立新增4个保护记录损坏+1实际UI场景通过；pair各1次、503/401步骤保留，正常退出/lease及启动项通过 |
| 7 最终提交构建与客户ZIP | ✅ 完成（2026-10-13） | r9未签名候选ZIP/摘要/CRC/最终启动与独立导入/CR通过；安装/真实boot/agent2明确现场待验 |

第1项证据更正：原`registration-final-ui-safe.mjs`第34行1091列精确指向`await active.click('停止执行')`，原栈仅evalJS和顶层帧，没有getState轮询回调帧。click helper会在按钮不存在或disabled时主动抛错，旧evalJS把具体exceptionDetails压成RENDERER_EXCEPTION。保留原safe报告explicit-start-online显示界面运行中但连接中、设备状态未完整读取，不能将手工H1在线判断等同UI stop已可点击。失败后的finally会执行正常退出并请求stop，因此原safe的stop succeeded/marker删除不能单独证明原UI stop点击成功；后来samehome启停有独立实际点击证明，保持有效。归因仍以本轮完整捕获为准，不通过删历史失败或重试成功放行。

第1项独立验证完成：一轮原始首注册→重开→首次激活→start强H1轮询，精确复现`Error: button disabled at <anonymous>:1:151`，完整exceptionDetails指向click helper、发生在b.click之前；当时只有start成功记录，没有stop。UI自然刷新约0.1秒后变为在线且停止按钮启用，再实际点击stop，H1/操作成功、marker删除，所有getState无IPC异常；两次Main0/两次真实lease释放、pair1/complete1、Run/RunOnce前后摘要相同、结束owned产品进程0。fixture改为等真实按钮ready并保留完整异常，不以后台state先到冒称UI已ready；没有产品源码修改或第二轮重复。报告`clients/agent-desktop/build/runtime-release-readiness-20261013/item1/independent-validation.json`，`PASS_ORIGINAL_EXCEPTION_ATTRIBUTED`，SHA256 `a69f2a8d3fd83adb32f5ce3c9fb3c4803d206433f10adb34822e311791d2f994`，最终EXE/asar仍对应原r9。此段关闭此前误分类的未归因异常，不代表后续安装/重启/客户业务已验收。

第2项准备：当前开发机Windows11专业版10.0.26200、64位、约32GB内存，Hyper-V相关服务运行；当前token非管理员，Get-VM被Windows权限拒绝，Sandbox特性状态2（未启用），无Sandbox可执行文件。已检查Downloads/Public Documents/repos下现成ISO/虚拟磁盘，无可复用验收镜像。用户明确授权启用Windows Sandbox，准备脚本`.tmp/enable-runtime-validation-sandbox.ps1`仅启用系统特性、记录结果且使用-NoRestart；通过管理员授权启动，绝不自动重启当前开发机。此操作只准备本机测试环境，不部署agent2，不触碰客户配置；真实安装尚未开始。

启用回读结果`ENABLED_RESTART_REQUIRED`：feature由Disabled变Enabled，RestartNeeded=true，automaticRestart=false。当前开发机没有自动重启；已询问用户保存工作后自行重启或明确授权重启，等待决定，不绕过系统重启条件。guest材料已备妥：原r9安装器a2ff37...、从不可变r8 ZIP提取的原安装器087bcec...、实际打包Node22.23.3（SHA256 `9c9245166b4a8e182e0b797da9c20136117ff24368eaff1fec8343a123c8db0e`）、稳定BOSS0.3.1插件；均逐文件校验，仅映射输入目录只读和报告目录可写，未映射仓库或客户配置。`.wsb` XML校验通过，Networking Disable、6GB；guest bootstrap/driver由有界角色准备，但guest未启动，没有安装器执行证据，不将准备结果标成通过。此环境目前仅Win11，silent真实安装亦不能冒称MUI交互或真实UAC跨SID已验收。

重启后续接入口：`clients/agent-desktop/build/runtime-release-readiness-20261013/item2/runtime-install-validation.wsb`。root已补manifest的guestOnly及driver/bootstrap摘要，6份输入复读校验通过；driver Node22语法与bootstrap WindowsPowerShell5.1 AST通过，只对固定WDAGUtilityAccount来宾执行。脚本依次覆盖currentuser/allusers的全新安装、有效r8覆盖、运行中升级、readonly故障覆盖、运行/readonly卸载和保留资料重装，每个安装器180秒、全套40分钟上界；live-progress/report写映射输出。旧故障样本18操作为synthetic，0claim/0业务；3010记录等待真实重启且停止，不冒充成功。准备报告`item2/preparation-validation.json`和交接说明`item2/preparation-handoff.md`均明确尚未执行；尚缺交互MUI/真实UAC切换证据需后续另行验证。用户重启开发机或明确授权后先重新检查系统特性和可执行文件，再启动该入口；不跳到第3项，不自动部署或提交。

用户调整顺序（2026-10-13）：r8已在原故障客户机覆盖成功，客户明确同意协助后续试装；用户要求暂跳第2项并先完成第3–7项。主控不重启当前开发机，不启动Sandbox；准备材料保留但不算安装通过。第3项可复用源码/模拟验证并给现场步骤，真实登录自启与跨boot恢复仍不可标为实测。第4项已另询当场agent2部署授权，未授权前只读核验及准备。第5项固定已提交ef5ad8d0/BOSS0.3.4隔离快照，避免混入其他任务正在修改的0.3.5。

部署决定（2026-10-13）：用户明确agent2由其手动部署，并授权本轮相关改动提交；主控不运行更新脚本或重建服务。管理员发布与现场验收说明加入组合包，明确提交4192da65/插件冻结ef5ad8d0、数据库/API/Web配套、内部账号普通注册及指定替换验收；服务器尚未发布的能力不能标PASS。

第5项最终版本调整：准备期间另一BOSS任务完成独立40项、额外8反例、CR和连续3次真机只读会员识别，并提交推送b8124834/0.3.5。为满足最新CLI要求，本轮最后固定该已验证提交；不追后续工作区变化。0.3.4包及实际UI导入/就绪/能力/退出证据保留prior，最终0.3.5按新签名根重新包内核验及独立导入，不将旧插件证据直接当新版导入成功。

第6项独立故障闭环：`item6/failure-matrix-independent-validation.json`结果PASS_WITH_EXPLICIT_ACCEPTANCE_GAPS，26源码与原CR相同。真实CurrentUser DPAPI+loopback四组invalid blob/解保护后坏JSON/foreign home/oversized，显式新码各pair一次、旧bytes备份、encrypted stage保存；实际旧r9 EXE混合home（缺config/secret+18旧op+badstage）首次可导航，新pair1、真实protected重启见证登记；503/401检查、纯管理重连、重开保留requires_reboot/action，不重复pair/complete/claim。旧facts不改且不归档，两次Main0/真实leasefree，Run/RunOnce不变，owned产品进程0。最终重包Main/renderer asar仍f90945...，Host/Core13模块摘要相同；权限或系统DPAPI自身故障仍需修复对应系统条件，不绕过保护。安装器、真实Windows boot、agent2与业务验收未测，独立报告明确限定。

## r9 候选组合ZIP（2026-10-13）

- ZIP：`clients/release/AID-Work-Runtime-UI-BOSS-20261013-trial-r9.zip`，131212947字节、26文件，CRC及逐文件SHA256复读通过；未覆盖r8或以前的ZIP。
- ZIP SHA256：`bc9a8ec9cfd2d8d023251876cad82aa7a325cc3c4e952df655274ed8b9f165a7`。
- UI0.0.6、Host0.2.18/Core0.2.17（代码4192da65）、BOSS0.3.5（冻结b8124834）、固定Node22.23.3 win-x64 ABI127。安装器NotSigned，SHA256 `b953ef67ee808183bf84b1dd9f167c60c1f78832fd4bc7b23f5c8771c7e9cb9e`；插件SHA256 `9cf4ca8ac93ba79b44b517f6370df6c64173dda89563f6e3fda2b2324561914b`。
- 最终Main/renderer asar与item1/item6原r9逐字节同为f90945...，13个Host/Core关键模块及26源/11测试/4依赖绑定通过；追加的新public trial root与签名者一致，旧两根逐字段保留。新安装器包验、实际EXE启动/正常退出/真实Host lease通过，最终独立发行审查无新阻断。
- 最终BOSS0.3.5独立实际UI导入（仅OS选文件边界由fixture返回固定路径）、Main私有IPC→Host真实签名/安装/probe；UI版本0.3.5、enabled/ready为true。现Chrome9222只读doctor后，UI启动真实loopback HB上报boss能力；Device claim一次boss_list_jobs→Runtime→签名MCP→started1/progress6/resultACK1，success/codeOK/effectnone，仅记录count2和shape，不保存岗位内容。未选岗、筛选或发送，仅只读工具展开职位下拉；stop成功、marker删除、Main0/leasefree、Run/RunOnce不变，owned进程0。
- 包内客户简明HTML/MD、完整配置迁移及错误处理表、管理员发布/现场步骤、版本/摘要和分项报告。phase13原未归因报告原样保留，与item1归因修正及最终validation-report同时提供，不删除失败历史。
- 本次可交自愿客户试用的未签名候选，不列正式发布或完整现场验收PASS。用户明确暂缓内部安装/开发机重启；真实登录自启/跨boot恢复尚待现场。agent2只读最后HEAD仍b981e64d，用户自行部署后才能验证替换和真实业务；助手没有部署或修改客户设备。普通新配对兼容旧服务端，未发布替换功能不得承诺可用。
