# Runtime 干净安装与自动连接切换

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 安装代际隔离、旧配置选择性迁移、旧进程停止 | ✅ 完成（2026-10-14） | 安装模块7项、真实脚本5项；坏指针/凭据/暂存状态不挡新目录，兼容插件重新验签 |
| Phase 2 | 注册协议3、幂等配对、授权替换与自动选定 | ✅ 完成（2026-10-14） | 开发定向391项；独立79项及修订后8项；旧授权在途claim/permit/renew行锁竞态通过 |
| Phase 3 | 自动激活与简化配对页面 | ✅ 完成（2026-10-14） | 独立DPAPI/loopback/真实Host9项；页面40项，自动后台恢复后前台重新握手 |
| Phase 4 | 独立测试、CodeReview、最终安装包验证 | ✅ 完成（2026-10-14） | 最终EXE实际连续安装10/10、包内Host9/9、自动恢复GUI与载荷比对通过；CR无P0/P1，r11交付 |
| Phase 5 | 安装前运行检测与确认/取消 | ✅ 完成（2026-10-14） | 独立增量5场景通过；实际NSIS确认/取消及r12实际安装/静默取消/载荷比对通过，CR无P0/P1 |
| Phase 6 | 无测试环境的正常交互安装退出修复 | ✅ 完成（2026-10-14） | r12正常本地向导退出2复现；r13正常用户非静默完整安装到完成页并退出0，注册版本0.0.9；独立回归/CR通过 |
| Phase 7 | 存量数据库迁移水位补偿 | ✅ 完成（2026-10-14） | 31单测/独立4迁移+3配对及CR通过；agent2结构已恢复且接口无码400正常，正式库待发布验收 |

## 提交交接（2026-10-14）

用户已在本次提交之后自行发布agent2；后续只读排查发现注册DDL被存量迁移水位跳过，当前修复进度见Phase7及文末。下文未部署记录属于当时状态，不冒称助手已执行服务器发布。

用户本次明确授权提交代码，按仓库规则将本批客户端、服务端API、数据库迁移、回归测试、契约和故障复盘提交并推送到当前`master`的`origin/master`。下文“未提交”是候选包生成时的历史状态，以本节交接和Git记录为准。服务器仍未部署，提交不代表获得部署授权。

发布配套服务端须执行`deploy/db_update.yaml`中`2026-10-14 17:00:00`增量并更新API；仅安装r13不能让旧服务器获得协议3。部署后需验证新配对一次完成、丢响应幂等恢复、撤销已验证旧身份及选中新设备。旧客户端协议1/2保留兼容，其他机器及跨账号旧身份不误撤销。r13验收ZIP及验证摘要保持不变，构建时未提交的源代码纳入本次提交。

提交前主控再次通过统一入口加载API及执行设备替换/仓储定向34项（34 passed），复用最终r13构建、正常完整交互安装和独立审查的同代码状态证据；没有部署服务器。

## 已批准目标与边界

新版从干净运行状态启动。有效旧配置自动迁移；无效旧配置进入干净配对页。新配对由一个操作完成凭据保存、旧身份撤销、新身份选定及启动。旧缓存、管理密钥、启动标记、journal/outbox/session和资源账本不进入新代际。旧业务事实保留服务端，不迁移、不重放。旧文件清理失败不阻断新版。仅处理当前用户可验证属于本产品的进程和目录，不批量结束Node/浏览器，不撤销其他电脑。

新配对阻断点只允许当前真实系统条件（权限、网络、安装文件、凭据保护）并给出具体步骤；不得用历史状态要求手动重启、结案或清理。未授权服务器部署、提交、客户机操作。

2026-10-14追加授权：安装遇到正在运行的旧客户端，必须先提示退出才能升级；确定后才自动停止并安装，取消则不安装。安装器Detect复用同一作用域/SID/PID检查且只读；静默安装不能取得本次确认，遇到运行实例返回取消。初次检测空闲但进程随后启动时，Stop的RequireIdle在任何变更前拒绝，不绕过确认。仅调整安装入口，卸载及已授权的新配对/启动代际切换不改；NSIS/PowerShell由主控唯一写入，wire/Device协议不变，独立测试与CR只读产品代码。

## 共同接口实施前登记

共同契约增量2.1，保留H1候选0.3。H3产品运行目录增加安装代际；可选注册端口继续兼容旧renderer，但新renderer不再要求手动continue。Device pairing_protocol=3增量：客户端先用既有随机凭据生成及CurrentUser DPAPI保存请求ID、设备token和请求意图，再发起配对；服务端按请求ID+token摘要验证幂等身份，返回同一device_id，禁止重复创建设备或重放撤销。

普通配对只允许撤销客户端以原token证明且与配对码同tenant/user的原身份；替换码以服务端票据指定目标为准。旧凭据不可读时不凭机器名称/指纹撤销其他设备。协议3在同事务登记新设备、撤销已验证旧设备、选定新设备；相同账号其他设备只清selected而不撤销。不把旧调用认定成功，不将旧调用迁移到新身份。

注册幂等字段和具体schema由服务端开发角色交付后主控核对并消费；不静默降级到不支持自动切换的服务器。未知响应使用已保护的请求凭据恢复，OTP不得写日志或持久保存。客户端配置持久化错误可重试同一次注册，不能消费第二个码。

已实现wire：POST `/api/local-tools/runtime/pair` 的 `pairing_protocol:3,registration_id,registration_token` 必填，首次code必填，恢复可省略；name/platform/runtime_version/capabilities/machine_fingerprint/previous_device_token构成不可变意图，摘要不含OTP。响应`success:true,pairing_protocol:3,registration_completed:true,device_id,device_token,replacement_completed`；同请求不重选、不复活被撤销的设备。`POST /runtime/activate {}`仅设备token鉴权自身，用于旧协议2暂存身份自动收尾；旧协议2替换关系仍经原complete接口完成。

用户切换企业账号时，跨tenant/user原token仅忽略撤销证明，可信新票据仍可新注册，绝不撤销原账号其他设备。显式换服务地址时保留旧保护回执、接受新注册，不让不可达旧服务器阻断新意图。配对前先停止本机已拥有的Host，再分配空连接目录；权限确实不允许停止时显示系统条件错误。服务端设备FOR SHARE先于任务锁，撤销事务完成后旧设备不能新claim/续租/取得写许可，包括session task领取链。

H3可选`automaticConnection:true`只由新版独立Runtime preload提供；新renderer在旧preload上保留旧模式。新界面无完成注册/历史重启按钮；网络恢复自动续接后只读重连管理页，不重发start或业务调用。普通重新打开不冒充新安装，默认迁移启动条件为fresh&&migrated。

唯一写入者：安装角色负责productConfiguration/新runtimeInstallation模块/installer脚本及对应测试；服务端角色负责src/local_tools、数据库迁移与服务端测试；主控负责Main/H3注册组合、Runtime注册/API client、renderer、计划与契约。独立测试与CR分别只读核验，修复由各文件写入者统一完成。

服务端开发角色结束后，主控接管repository跨账号旧证明修订与对应集成测试；扩展服务端角色唯一维护`src/session_tasks/service.py`的领取/续租设备门。本批没有提交或部署。清理是作用域验证后的best effort，失败不影响新版；尚无持久后台清理重试，不将其标成已实现。

## 修复与验证记录

独立验证找出PS5.1 File.Replace空参数导致第二次重装必失败，已用NullString修复并验证十次脚本Finalize。CR修复跨账号旧证明阻断、不可达旧server阻断新server、paired/installed已撤销身份、旧registration-lock路径污染、旧协议2恢复成功仍残留CONNECTING、坏stagedtoken回退仍带replacement标志等问题。安装探针现在真实请求getState并拒绝blocked，检验插件管理、DPAPI及ASAR实际HTML字节。最终EXE页面运行另验，不能用探针替代。

独立CR实现/DDL/打包/页面26文件合成SHA256：`4a9af08089273ec2847ceadeb7ac1b59f32400c25f4ab88703ce153bec73028f`。开发Runtime兼容27项及新增注册2项、Web/Runtime renderer构建通过。独立测试脚本`clients/agent-desktop/scripts/tests/runtime-clean-independent.test.mjs`使用真实Windows DPAPI与loopback，实际owned Node Host通过原IPC启动/强制停止，其他Host为明确fixture；最终安装包driver另外记录。

## 验收

损坏config/credentials/management-secret/operations/marker/journal、残留旧CLI、新旧GUI重复启动、删除拒绝、网络401/503、配对响应丢失、保存失败/中途退出、替换目标跨租户/并发、连续安装升级、坏插件隔离。真实Windows DPAPI与实际子进程、最终安装包启动和旧进程接管必须有证据；未做真实UAC/boot/客户机/agent2须据实登记。

## 最终发行验证与交付（2026-10-14）

最终安装器 `AID-Work-Runtime-0.0.7-win-x64-acceptance-unsigned.exe` 为126312751字节，SHA256 `13e1165ff0fccf054be60a212cd4e70dbd83101c18828499152bb85c3f40bc99`；UI0.0.7、Host0.2.19、Core0.2.18、Node22.23.3，BOSS沿用签名插件0.3.5。Host包SHA256 `18c07d8304c9688b485687fa52b4027b06016460f4a4e9d5f0971bd890543511`，构建输入SHA256 `e84f545b3b81f3806f5228d511273e99cf75fc99d4313a1fd046bb2def79b44b`。最终资源校验3695文件通过，不在验证后重新构建安装器。

独立角色实际运行同一个NSIS安装器连续覆盖十次，10/10通过；每次创建新安装UUID和空运行目录。重复污染配置、凭据、密钥、启动标记、journal/outbox，并在第五次破坏安装指针，第六次仍恢复。使用严格nonce目录和注册表命名空间，普通HKCU/HKLM Run、RunOnce及产品安装/卸载键逐次内存摘要比对未变；历史摘要字符串未落盘，报告另附最终只读快照相同摘要，不将它冒称十次的历史字符串。

最终包内固定Node/Host、真实DPAPI、HTTP loopback及实际直属Host替换9/9通过。实际Electron进程验证已提交服务器但丢失配对响应、随后503、恢复服务后自动续配对和启动；前台自动重新握手，显示运行中/在线，单一配对表单及三个字段，无REGISTRATION_PENDING/完成注册/重启按钮。原生smoke退出0，直属Host残留0；1187×739视口无横向溢出。另实际安装并比对EXE/ASAR/Node/Host/Core关键文件字节，与GUI测试的win-unpacked完全一致。

证据在组合包“验证记录”及本地 `clients/agent-desktop/build/independent-validation/`：`runtime-clean-independent-report.json`、`final-installer.tap`、`final-host-loopback.tap`、`gui-native-final.tap`、`final-gui.json`、`final-gui-dom.json`、`final-installed-payload.json`。三个截图/CDP工具失败原始日志一并保留；最终GUI用真实DOM和布局检查通过，但隐藏Electron截图阻塞CDP，未完成像素级视觉检查，不把混合失败日志宣称全绿。可复现driver为 `scripts/tests/runtime-clean-independent.test.mjs` 和 `scripts/tests/runtime-clean-package-independent.test.mjs`，命令及源文件摘要见独立报告。

交付路径：`clients/release/AID-Work-Runtime-UI-BOSS-20261014-trial-r11.zip`，130888986字节、20文件，内含安装器、BOSS插件、客户/管理员说明、manifest、摘要与原始验证记录。ZIP SHA256 `90bac3bd7f32d04eb9477714800bc2ba80d7d5fcd07276c5d7384c211f523d45`，同名`.sha256`及CRC/每文件摘要验证通过；包内计划快照在生成ZIP摘要前固定。新配对须先发布本批服务端API及`deploy/db_update.yaml`中`2026-10-14 17:00:00`迁移，客户端不能替代服务器升级。

开发与隔离发行验证已完成，未提交、未部署。未验收真实UAC提权、Windows开机登录、客户电脑、agent2连通及真实BOSS业务；未实现持久后台删除重试。现场验收不阻断开发归档，也不冒称已完成生产发布。

## r12安装确认增量交付（2026-10-14）

用户追加要求：正在运行的旧客户端必须先确认退出，确定才自动停止并安装，取消保持旧运行、暂不安装。只调整NSIS/PowerShell安装入口；卸载、新配对和Runtime启动代际切换保持既有语义。UI发行号0.0.8，Host0.2.19/Core0.2.18/BOSS0.3.5不变。

Detect复用既有产品/SID/PID筛选而不写文件、自启或停止进程。弹窗说明确定会中断本机任务，默认按钮为取消；静默运行实例默认取消，返回1。空闲路径Stop使用RequireIdle二次检查，发现新实例在任何清理前拒绝；确定路径复验PID身份并等待实际退出，然后才继续安装。

独立测试实跑Windows PowerShell/CIM和编译后的原始NSIS hook：Detect有/无进程只读及RequireIdle竞态、真实默认取消与显式取消保持CLI/子进程/旧文件/自启、确定仅停止已验证Runtime树并继续、无运行直接继续及静默取消，四个场景通过。实际最终r12安装器完成空闲安装，然后在旧Runtime运行时静默取消，返回1且进程/文件/自启/安装载荷不变；关键EXE/ASAR/Host/Core与构建副本摘要一致，第五场景通过。首次取消文案检查因PS5 stdout编码产生工具断言失败，仅测试driver固定UTF8后受影响场景及最终包2/2通过，不改产品，也不宣称首轮混合日志全绿。

开发自测安装脚本7/7通过，补充Detect/RequireIdle断言后受影响脚本和NSIS编译2/2通过；renderer/TypeScript构建及最终3695资源校验通过。本轮独立CR无新增P0/P1，NSIS SHA256 `b2382b4e72ae1ef80f382f3635ac4217764d681cc6e2a6b9e9583a983229bbe0`，PowerShell SHA256 `c98cbe28ad162c491cde1e3281ce6c75d2c53a094d1499631b43122a2b11f11f`。

最终安装器 `AID-Work-Runtime-0.0.8-win-x64-acceptance-unsigned.exe`，126313928字节，SHA256 `d2b50a6ac05e86127d9acd48980eb9bbc8de66d5ffbd24c90d8690ad2403cd97`，buildInput SHA256 `642130aef1167ce725007e94232374987f774764917d363cbf4d384a1593967f`。独立报告和原始TAP位于 `clients/agent-desktop/build/upgrade-confirmation-independent/`；可复现driver为 `clients/agent-desktop/scripts/tests/runtime-upgrade-confirmation-independent.test.mjs`。

交付 `clients/release/AID-Work-Runtime-UI-BOSS-20261014-trial-r12.zip`，130892571字节、20文件，SHA256 `516c6d67f4898623504ba4005e2cad86fbae28af75cfdb56d3eccf89b242a125`，CRC与每文件摘要验证通过，同名`.sha256`和交付manifest保存摘要，正式独立报告冻结后与归档字节比对通过。包内计划快照先于最终ZIP摘要固定。历史r11证据单独标注，未把十次重装或GUI测试次数冒称r12重跑；本轮未改的Host/Core/renderer功能复用历史证据。安装确认实际弹窗用原NSIS hook独立执行验证，完整最终安装器另外验证空闲安装和静默取消，未冒称真实UAC提升场景已通过。配套服务端发布要求及未提交/未部署/客户机/boot/BOSS业务边界不变。

## r13正常环境安装退出复现与修复（2026-10-14）

用户报告安装进度未结束窗口关闭。主控按要求本地自动实装原r12，正常Windows用户环境、不带/S、不设置fixture变量，操作真实安装按钮；约8秒后退出2，无完成页、无安装登记。根因与测试漏项见[故障复盘](../incidents/runtime-installer-missing-fixture-env-incident.md)。读取可选测试变量后立即ClearErrors，防止缺失状态泄漏到普通安装调用结果；不改变后续真实失败返回或非法fixture拒绝。

r13/UI0.0.9实际完整安装再次使用正常Windows CurrentUser、无fixture、不静默；到达真正完成页面（按钮“完成”、正文“已安装在你的系统”），取消运行复选框后点击完成，进程退出0。正常HKCU安装/卸载记录登记0.0.9，默认安装到`%LOCALAPPDATA%/Programs/aid-agent-desktop`，安装stamp与正常`%APPDATA%/aidwork-runtime-app-acceptance/installation-v2/current.json`一致。本机保留实际安装结果，不自动运行新客户端或业务。

独立回归包含缺测试变量的原macro错误标志旧/新对照、正常完整guard继续、真实PS路径链接错误仍拒绝；完整guard的旧版最小stub对照未复现r12整包退出，明确保留这一结果，不把最小响应器当客户全链路。真正正常环境整包成功由上述实际向导和最终文件/登记比对证明。原生完成页控件、进度历史、退出码及图片位于`clients/agent-desktop/build/runtime-interactive-repro/`，图片PrintWindow未完整画出所有静态文本，只作为控件截图辅助，不宣称它单独完成全页面像素验收。

最终安装器`AID-Work-Runtime-0.0.9-win-x64-acceptance-unsigned.exe`，126313948字节，SHA256 `2d6e68fdb05f0e797b2e69834c51fe43e1f4f1084483cde6401d82467a1dd4df`，buildInput `f3b5b52f2e49fd8a0feef33d6bb2ef258c48619e84e877a8bce50e9e230ebba5`。最终3695资源校验/renderer/TypeScript构建通过；本轮独立CR无新问题，NSIS SHA256 `acb5db748fd5b7a0bc4187a4e5af27f143892ee2a5c52a414f4998215832a305`。

修复包为`clients/release/AID-Work-Runtime-UI-BOSS-20261014-trial-r13.zip`，130897112字节、19文件，SHA256 `184854e11dfb0fa12127a2baeb0a84ff07cd98ac6ec379810c716bdb3ce171a0`，CRC及所有文件摘要通过，包内计划快照先于ZIP摘要固定。独立正式报告`build/runtime-interactive-repro/independent/normal-environment-report.json`已冻结：安装载荷11项与构建副本逐字节一致，安装位置/版本/stamp/current代际匹配，缺测试变量的原宏旧/新对照及真实PS失败拒绝通过。

不再向客户建议安装r11/r12未修复包。r13未增加持久安装日志，不能将此次实际复现记录冒称客户机常驻日志；服务端配套发布、未提交/未部署、真实UAC、客户机、开机登录和业务边界仍不变。

## Phase7：agent2存量迁移水位补偿（2026-10-14）

用户发布后，视频显示暂时连接故障，实际服务器协议3配对持续500。只读确认agent2代码为a805eb24，容器有原17:00迁移且未设置DB_UPDATE_SKIP；数据库水位已到18:00，三个registration字段和部分唯一索引缺失。21:52启动日志显示无新增块。根因和测试漏项见[复盘](../incidents/runtime-registration-migration-watermark-incident.md)。

主控唯一写入补偿YAML及迁移单测/文档，独立角色唯一写入隔离PG迁移集成测试；Runtime core、Device pairing wire及客户端不变，共同契约仍2.1。本批追加22:45同四条幂等DDL，保留17:00历史，不回拨/删除水位，不篡改设备身份、selected或业务任务。真实部署前执行字段/索引检查并确认当前水位，新修复尚未提交/部署。

服务器恢复方案已准备：仅以事务执行这四条补偿DDL（`ADD COLUMN IF NOT EXISTS`×3、部分唯一索引×1），设置10秒lock_timeout，失败回滚；不重启容器、不写配对凭据或设备任务。可由新22:45块正常更新流程追踪，或明确授权后先执行对应DDL恢复，再由正常新版本迁移记录进度；不得把已有水位退回17:00。需要用户当场批准agent2数据库修复才可执行，当前只读事实不算发布授权。

数据库恢复后，若原首次注册从未提交且码已过期，网页生成新码、在现有r13再次配对即可；不用重新安装客户端。本次先修复升级结构，不把客户端500泛化为网络提示的问题标成已解决。

最终验证：统一入口迁移单测31 passed；独立使用开发PG服务上的严格nonce数据库/自有schema，真实执行器验证4个迁移场景及3个既有协议3配对场景通过。原17-only跳过缺列故障已复现；完整和部分结构修复、NULL/非NULL部分唯一索引、旧身份/selected/任务字段保留、重放幂等及丢响应恢复通过。测试从真实YAML仅抽17/22:45块，避免未来追加其他迁移造成无关失败；维护性修订后重跑受影响3例通过。CR无未关闭P0/P1/P2。

独立报告`clients/agent-desktop/build/registration-migration-independent/registration-migration-report.json`已冻结；YAML SHA256 `9fe29950f1e27a39bf2cd8cd64e367bcb6fa540a77ce59939fad3df2697f89c8`，新集成测试SHA256 `3076eafcc4d06f8abb71eada0dce3b14f83c879c192463089910ac518ab3a21b`。可审阅补偿事务在`clients/agent-desktop/build/runtime-connection-video/registration-repair.sql`。本次本地开发完成，不代表agent2实际数据库已经恢复；补偿未提交、服务器仍未执行，待用户当场授权后现场验证配对。

### 现场复查与正式发布准备（2026-10-14 22:58–23:01）

用户当场授权连接agent2数据库补表。助手执行前复查发现补偿已由其他执行过程生效：数据库迁移记录为22:45，三列类型UUID/TEXT/TEXT均可空，唯一索引定义含`WHERE registration_id IS NOT NULL`。因此未重复执行DDL；不将已存在的修复冒称助手完成。无配对码且使用全新随机请求的无业务创建探针返回HTTP400/PAIRING_CODE_REQUIRED，证明当前注册查询已可正常执行；没有创建设备、没有打印凭据。

22:30起没有新建设备，一张配对票据未使用且已过期；首次注册没有提交成功，客户端持久事务不保存OTP，故该用户需要网页生成新码并在现有r13点击一次确认配对。之后连接/选定/启动仍自动完成，已成功注册但丢响应的情况继续可自动恢复；不能承诺这次未知首次注册仅等待就会完成。

正式环境仅做只读结构检查，未部署或写入：当前迁移记录为`2026-10-13 17:00:00`且注册列缺失，原14日17:00及新22:45两块均晚于它，正式发布时不会遇本次18:00跳过问题。重复幂等已由真实PG验证。为满足本任务正式发布准备，将补偿迁移、回归测试和复盘纳入此前用户授权的Runtime代码提交并推送；正式业务配对验收仍须实际发布后完成，未把元数据检查当整套正式验收。
