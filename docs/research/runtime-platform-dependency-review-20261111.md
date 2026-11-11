# Runtime 1.0.0 外部状态依赖审查

2026-11-11，基于 `codex/runtime-recovery` 的 `6a0d6d05` 静态检查启动、配对、连接修复、安装、进程停稳、任务调度及诊断。用户要求 Runtime 的状态由自身记录、插件和服务端协议闭合。下列现状均按代码路径核对，去除方案纳入 1.0.0。

| 依赖 | 当前代码与实际影响 | 1.0.0 处理 |
| --- | --- | --- |
| Windows 启动项 GUID 和会话编号 | 新全局 Job 的停稳已走直接证据。`connectionRecoveryResources.ts` 的旧目录修复、登记重启恢复和首次目录记录仍读取 `boot-id`、`session-id`。空目录初始化会调用 `recordFreshConnectionEpoch`。旧启动项标识仍能影响这些路径。[连接资源核验](../../clients/agent-tool-runtime/src/connectionRecoveryResources.ts) · [Host 初始化](../../clients/agent-tool-runtime/src/runtimeHost.ts) | 目录由 Runtime 的安装记录、已持有的目录租约和启动记录判定。旧执行停止由已登记的受管进程及退出回执证明。退役按 Windows 开机或会话变化放行的重启登记路径。 |
| 全机进程枚举 | `assertNoLegacyRuntimeProcess` 用 WMI 扫描同 Windows 用户的 `node.exe` 命令行，无法识别其实际 home 和版本时仍会拒绝 Host 初始化。安装器的 Detect/Stop 也先做全机进程枚举，再按安装路径和产品记录匹配，枚举失败会中断安装检查。[旧 CLI 扫描](../../clients/agent-tool-runtime/src/legacyProcessCheck.ts) · [安装器检查](../../clients/agent-desktop/scripts/runtime-clean-install.ps1) | 日常启动使用本 Runtime 的目录租约及受管进程记录。升级用已知安装实例的管理入口、记录的进程身份或退出回执核对旧端。把必须对旧无台账版本进行的交接限在升级操作中，普通上线与旧端交接分开。 |
| 机器注册表标识和电脑名 | `machineFingerprint` 用 `MachineGuid` 和 hostname 生成指纹。配对上报该值，服务端停止、领取和历史等待准入把指纹相同的设备关联。电脑改名可能改变匹配，旧设备记录也可能影响当前设备作业。[指纹生成](../../clients/shared/local-tool-host-core/src/legacy/config.ts) · [配对请求](../../clients/agent-tool-runtime/src/cleanRegistration.ts) · [服务端准入](../../src/local_tools/repository.py) | 配对和回连以服务端发放的设备 ID、凭据和配对回执为依据。设备换绑使用已核实的原设备凭据或替换票据。服务端按已绑定的设备和作业判断，配对与准入退役机器指纹。 |
| 电脑名、用户名和会话环境变量构造锁 | 桌面锁名来自 hostname、用户名和可选 `AIDWORK_DESKTOP_SESSION_ID`。Host 目录租约也把 Windows 用户名拼入键。相同 Runtime 或作业的互斥域受外部名称影响。旧桌面锁可以由多个进程共同竞争。[桌面锁](../../clients/shared/local-tool-host-core/src/legacy/desktopLock.ts) · [Host 目录租约](../../clients/shared/local-tool-host-core/src/instanceLease.ts) | Host 只按自己的安装目录身份互斥，使用已有目录租约及权限保护。同一 Runtime 的作业由串行队列与并行声明调度。旧桌面锁覆盖的跨实例输入互斥按下文安全切换要求验证。 |
| Windows 桌面是否可交互 | 旧执行器在调用部分写工具前使用 `OpenInputDesktop`，检查失败会直接返回 `DESKTOP_NOT_INTERACTIVE`。通用层因此替插件决定业务是否能做。[桌面探测](../../clients/shared/local-tool-host-core/src/legacy/desktopCheck.ts) · [执行前门禁](../../clients/shared/local-tool-host-core/src/legacy/invocationRunner.ts) | 由插件报告自己所需的桌面、窗口、登录或应用条件。实际作业按插件返回的错误和进度展示。Runtime 连接与调度沿统一协议运行。 |
| Chrome 与固定工具的资源特例 | 原生辅助器仍包含旧 `--preserve-chrome` 分支，旧退出回执可以产生 `chrome` 限制。`RuntimeHost` 与服务端按企微及具体工具名开放少量作业。旧微信会话桥接还直接调用特定观察及发送工具。[原生监督](../../clients/agent-tool-runtime/native/RuntimeProcessSupervisor.cs) · [Runtime 准入](../../clients/agent-tool-runtime/src/runtimeHost.ts) · [微信会话桥接](../../clients/agent-tool-runtime/src/sessionTasks/nameBridge.ts) | 新版只监督 Runtime 绑定到作业的受管进程。浏览器或业务软件的状态由相应插件处理。退役新版执行路径中的浏览器名称、插件名、工具名和微信会话特例。以已知活动作业和串行声明约束执行。 |
| Windows 电源事件及启动、会话诊断 | 现有内置导出单独读取电源日志及标记匹配，属于诊断。另有源码中的旧延后升级脚本读取 `boot-id`，当前安装器入口使用 `runtime-clean-install.ps1`。[诊断导出](../../clients/agent-desktop/electron/runtimeDiagnosticEvidence.ts) · [旧升级脚本](../../clients/agent-desktop/scripts/runtime-legacy-upgrade.ps1) · [现行安装器入口](../../clients/agent-desktop/scripts/runtime-installer.nsh) | 1.0.0 诊断按 Runtime 启动记录、原设备心跳、受管执行事实、作业编号及插件回执说明故障。Windows 日志作为人工取证资料。登录或唤醒事件用于通知 Runtime 重新查看自身状态。 |

Windows 提供进程、文件权限、凭据保护、进程组和本机通信等执行能力。自身进程的真实退出、文件写入结果与凭据读取结果属于 Runtime 可以直接观察的事实。设备在线由原身份的服务端回执确认，业务结果由插件回执确认。受管进程退出证明确认执行停止，缺少业务回执时报告中断。旧执行确有冲突时，只影响关联作业与对应执行资格。

## 安全切换与插件接管

去除的是旧判断依据。每项依赖在替代路径通过对应验证后退役。

| 原有保护 | 接管方及切换要求 |
| --- | --- |
| 避免旧实例和新版同时执行 | Runtime 与安装器使用已知实例的退出回执、受管进程和目录租约完成交接。旧实例没有可用的管理口或可信进程记录时，在升级说明中写明退出该旧实例的步骤。无法确认的执行保留原状态，缺少进程记录或跨会话查不到旧本地 Job 均不足以确认停稳。保留原设备身份及回执。 |
| 避免同设备重复领取和换绑时旧作业冲突 | Runtime 以同一目录独占及自身串行队列保护本机。服务端沿原凭据或授权替换票据识别真实的换绑关系，按绑定的作业完成交接。验证原设备升级、同目录双启动，以及已确认换绑的旧作业归属和回执补传后，再退役按机器指纹和系统名称判定互斥。 |
| 避免不同入口同时操作同一套输入 | 旧锁把经过 `withDesktopLock` 的调用整体串行，持同一个锁的调用等待。BOSS 的对外命令走串行队列。企微按命令确认是否独立使用输入、窗口和其他资源，独立命令按声明并行。跨实例互斥只核对已支持的多个入口确实争用同一输入的场景。[旧调用持锁](../../clients/shared/local-tool-host-core/src/legacy/invocationRunner.ts) |
| 操作前确认浏览器、BOSS 页面和可用输入条件 | BOSS CLI 复用自身连接与页面检查，由需要桌面输入的命令检查实际输入条件。异常通过插件回执报告。用户自行打开的浏览器归用户，Runtime 只停止对应的受管执行。[BOSS CLI 设计](../design/recruiting/boss-resume-assistant-native-cdp-design.md#100-业务命令设计范围2026-11-11待开发) |
| 操作前确认企微账号、窗口和本地库 | WeCom CLI 按命令检查实际所需条件。桌面命令核对窗口、登录和目标，本地库读取核对数据库与账号。具备独立后台执行条件的命令由企微声明可并行，插件报告失败及已知进度。[WeCom CLI 设计](../design/wecom/wecom-cli-design.md#100-重构范围2026-11-11待开发) |
| 浏览器名称特例、旧微信会话桥接、系统事件诊断 | 旧浏览器名称和工具特例由受管作业及插件声明的通用规则取代。旧自动业务核对随对应入口退役。微信路径按首轮范围退役。内置诊断使用自身运行记录与协议回执，系统事件供人工对照。 |

源码可确认 BOSS 有真实光标和键盘输入，企微的点击与按键主要通过窗口消息投递。企微文本发送仍使用系统剪贴板，并可能触发窗口恢复。按屏幕坐标找窗口与屏幕截图回退也存在于企微驱动中。具体哪些企微窗口命令可与 BOSS 同跑，以完整命令的验收为准。[BOSS 真实输入](../../clients/boss-resume-assistant/scripts/BossPhysicalInput.cs) · [企微窗口原语](../../clients/wecom-cli/drivers/ps1/_common.ps1) · [企微文本发送](../../clients/wecom-cli/drivers/ps1/message-send.ps1)

升级保留有效配对身份和原作业、回执。首次安装、普通重启、升级和连接修复均应验证在启动项、会话编号、机器指纹及 Windows 事件日志不可用时，仍可按 Runtime 自身证据完成状态判断。另用两个对照验证实际停稳和隔离范围，一项是已登记的受管旧进程仍在，另一项是系统中有与本 Runtime 无关的 Node 或浏览器进程。插件未登录或桌面不可用由插件报告，原设备持续心跳，独立后台作业继续执行。实施和验证见[统一计划](../plans/plan-runtime-plugin-host.md#1612-运行状态依赖整改100待开发)。
