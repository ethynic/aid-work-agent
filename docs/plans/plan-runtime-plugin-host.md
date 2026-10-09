# Runtime 执行环境、插件宿主与可视化客户端开发计划

## 开发进度

> **当前仅实施第一部分 A1～A4。本轮代码交付已完成并停止；第二部分 B1/B2 暂不开发，只保留设计、契约、schema 和 fixture。不能因“继续开发”“按计划开发”自动启动第二部分，必须由用户明确要求。**
>
> 更新：2026-10-09。唯一架构：[Runtime 设计](../system/runtime-plugin-host-architecture-design.md)；共同契约 v2.0，wire 候选0.3未冻结。本计划整合旧 M1/M2 及 Runtime M0.4，不再维护独立阶段文档。

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| 设计/共享格式 | 当前架构、契约、schema/fixture | ✅ 完成（2026-10-09） | 架构v2.0、格式候选0.3；共同wire冻结另列 |
| 第一部分 A1 | 共用核心、管理端、CLI与受管入口 | ✅ 完成（2026-10-09） | 实际Host/DPAPI/停止/兼容验证；H1共同冻结待确认 |
| 第一部分 A2 | 三第一方离线包与安装管理 | ✅ 完成（2026-10-09） | 隔离签名实包、真实安装/重启/MCP验证；正式发行输入另列 |
| 第一部分 A3 | Runtime UI与公共Electron壳 | ✅ 完成（2026-10-09） | 实际窗口/IPC/监管、握手竞态修复、独立52项验证 |
| 第一部分 A4 | Windows发行与真实业务验收 | 🔧 进行中 | 隔离安装器已交付；NSIS/原生选框/真实配对业务及正式发行未完成 |
| 旧 M1/M2 | 服务端目录知识层与legacy设备脚本路由 | ✅ 完成（2026-10-06） | 已有代码并入兼容基线，样本Windows业务验收仍未完成，见第9节 |
| 第二部分 B1 | 第三方Skill导入、环境与AI分析 | **暂不开发** | 未开放实现；不是当前待开发队列 |
| 第二部分 B2 | 第三方Skill动态登记、执行与样本验收 | **暂不开发** | 未开放实现；保留格式/资源/结果约束 |

## 1. 范围与停止条件

第一部分为独立 Runtime UI、BOSS/weixin/wecom 按需离线安装管理及原云端执行兼容，成功安装默认启用，就绪由真实环境决定。安装器不预装业务插件，客户机无需 npm/Git/系统 Python。

不并入完整 Desktop 对话、通用文件/Shell/PTC、在线市场或第三方MCP分发。第三方包在安装/依赖构建/源码上传前拒绝，不显示无效占位入口。保留 legacy skill-runner 不等于实现新的第三方安装机制。

本轮完成代码和隔离验收交付，停止自动实施。A4 剩余人工项据实保留，不因提交推送登记已验收。后续部署须用户明确当场指定目标；提交不是部署授权。

## 2. 成功标准与剩余门

已验证空核心、配对管理协议、三包单独/组合安装、只读就绪诊断、启停/卸载、重启恢复、去重、实际child退出、旧执行/结果兼容及固定Node发行资源。

仍需人工确认 NSIS 安装、原生选择框、现有服务真实配对、业务软件登录、授权只读云端任务和干净客户机。正式发行还需正式trust root/发布签名、Windows证书、内部Python安全和CRT许可资格。

H1 producer/真实H3 consumer已有证据，整体wire仍候选0.3；H2新任务设备/workspace/许可与H4新登记/资源/产物未冻结。这些不是已上线接口，缺输入不启用对应能力。

## 3. 实施阶段

### 第一部分：Runtime UI与第一方CLI插件

| 阶段 | 已交付实现 | 保留限制 |
|---|---|---|
| A1 | Node core exports/build、管理producer、薄CLI/bootstrap、lease、配对事务、既有Device执行、drain、结案证明 | 历史无可信证明/unknown继续拒绝身份切换；无任意renderer业务调用 |
| A2 | envelope/payload签名校验、原子store、受控选包冻结、持久operation、升级/停用/卸载、三自包含包 | 仅批准第一方；旧受信工具子集不扩大；正式发行另需批准输入 |
| A3 | 配对/连接/插件页，preload/main/监管/私有IPC，产品身份与固定Node，托盘与退出门 | 完整Desktop对话不在本批；外置无受控通道只报冲突 |
| A4 | Windows隔离acceptance安装器、完整资源验包、实际包EXE启动/退出 | 人工与正式发行门见第2节；不承诺已完成业务真机验收 |

### 第二部分：第三方skill（暂不开发）

**B1/B2 不属于当前开发清单。不得以通用化、预留接口或第一部分依赖为由提前编写安装器、AI分析器、新云端登记/执行链或UI入口。**

B1 保留唯一技能根、安全解包、原包/sidecar、环境/可变状态、SKILL.md优先与必要源码云端分析、实际入口/code_map验证。

B2 保留设备私有契约视图、调用版本固定、资源/取消/unknown/原结果ACK、新产物/图片进入模型及WorkBuddy实包验收。现有fixture不能代替真实样本执行。只有用户明确启动第二部分才更新状态和实施。

## 4. 开发与验证流程

非平凡代码按项目开发流程独立测试和CR；纯文档整合核对代码、约束、索引与链接，不机械重复业务测试。只运行受影响检查，不扩大权限消除基线失败。

共同格式变更先登记唯一写入者、版本/兼容方案与共同检查点，再验证真实producer/consumer。不能把candidate通过或临时tgz构建成功当正式发行完成。

## 5. 文档与索引

只维护一个Runtime架构和本计划。共同契约、schema/fixture、Provider标准、Desktop设计及构建手册按各自职责保留。

旧M1/M2调研设计/计划与M0.4实施规格已整合删除，索引不再有独立M1/M2待办。领域sessionTasks、微信/BOSS自动化不删除，其Host依赖指向当前架构。进行中总条目保持“部分完成”，原因是A4及共同/正式发行门仍未关闭。

## 6. Desktop / Runtime 共同边界登记

| 检查点 | 主导/唯一写入范围 | 当前状态 |
|---|---|---|
| H1管理port/wire | Runtime：contracts/runtime-host/v1、core管理producer；Desktop消费验证 | 候选0.3真实两端已验证，未共同登记冻结 |
| H2设备/workspace/许可 | Desktop主导服务端与共享输入；Runtime消费核验adapter | 未冻结，不自行补协议 |
| H3公共壳/实例监管 | Desktop：main/preload、选择/监管、package/lock、Node/产品构建和全局入口 | 首期隔离Runtime产品已交付，人工项待验收 |
| H4插件描述/结果/资源 | Runtime主导，与Device/Runner/Desktop共同验证 | 描述/输出格式已交付；新登记、资源及产物链未完成 |
| Runtime实现 | core、Host/CLI/bootstrap、三包构建与必要CLI路径、runtime feature | 本批已提交；不由Desktop重建第二套core |

## 7. 当前接口与依赖

格式唯一入口为[contracts/runtime-host/v1/README.md](../../contracts/runtime-host/v1/README.md)，实际行为见[Runtime设计第11～12节](../system/runtime-plugin-host-architecture-design.md#11-共用核心与接口实现)。api_major/schema_version为1，与共同契约v2.0、候选0.3和产品版本分别管理。

列表返回实例/revision/plugins、可选真实version；请求固定code/error；operation与业务任务分开。消费方共享水位、处理换实例/旧连接/同revision乱序，并保留握手期间通知。

管理mutation依request_key持久去重，选包引用与外包快照走私有Main-child callback，不加H1字段。固定Node/信任资源来自H3产品输入。缺官方生产输入不能用测试根补位。

## 8. 首期实施约束

七项规则已落实于[设计第12节](../system/runtime-plugin-host-architecture-design.md#12-第一部分开发前实施决议)：离线签名/依赖、安装与ready分开、升级排空、legacy优先级、持久去重与水位、停止/配对、唯一文件责任。

固定Node22.23.3/ABI127。包检查顺序进行；管理查询/初始化等待120秒，与30秒drain观察及业务许可期限分开，超时不kill、不续权。当前性能证据不覆盖低内存/冷磁盘认证，压力刷新曾达86.6秒，不能宣称所有客户机刷新都很快。

## 9. legacy兼容与未完成样本验收

旧M0.4已经成为core中的Device/MCP/DPAPI/桌面检查/结果链，原“必须捆绑BOSS、5秒强退”等规格不作为现行为。旧pack脚本对新布局先拒绝；新发行走Desktop标准构建链。

M1知识层代码完成，历史证据为257单测及26集成通过；M2云端/设备代码完成，收尾证据为239云端用例、60设备受影响用例及typecheck通过，旧进度“评审未收口”已被其终态复核记录替代。以上是历史记录，本次文档整理不重新宣称跑过。

现代码仍使用服务端目录、审批/hash/entries、device_execution开关与本机skills.dir/python；新v2.0路径不能照搬这些安装/审批要求。旧门槛暂按代码保留，不能删除文档就取消执行检查。

WorkBuddy jingpian样本Windows Python实际执行、可交互桌面/进程树收尾与截图/业务闭环未验收。该遗留随本计划保留，不单列待开发，不重新启动B1/B2，也不拿第一方包验收代替第三方样本。

## 10. 最终交付与验证记录

### 10.1 第一方最终隔离资产

仅使用clients/runtime-plugin-packaging/release/final-acceptance的三包与对应ISOLATED-TEST-TRUST-ROOT.json；旧调试根不能混用。

| 包 | 字节数 | SHA256 |
|---|---:|---|
| boss-0.3.0-dev.aidplugin.zip | 4442404 | `6c1f4578498ff23ab8138b5fb1c51994f4dbd209a7bf1754e43d8726bce73d11` |
| weixin-0.1.0-dev.aidplugin.zip | 117056592 | `c3f17d0460aa596b273c76b24299d74b7cda0a96af3c07c86564f9af7c1285f3` |
| wecom-0.1.0-dev.aidplugin.zip | 117215512 | `35dfd1b23ae2dab194763dbb4dae5ea80a4f9e02c136d32887a5524b68f9a1dc` |

manifest版本0.3.0/0.1.0/0.1.0；完整MCP工具21/8/10，Host子集不扩大。BOSS0.3.0沿用此前已有源输入，不称本批原创升级。

微信/企微内部Python/OCR/模型/CRT随包，使用-I -B避免缓存写入。离库、系统PATH下真实version/doctor及合成图片OCR验证通过，执行前后文件集合/摘要不变；未发消息或点击业务GUI。

### 10.2 独立验证与已知限制

| 范围 | 本批记录 |
|---|---|
| Runtime/core | Runtime251/251、core30/30；固定Node22 trust/proof/session10，无跳过 |
| 独立离库Host | 最终f52b真实Host/DPAPI、三包import、冻结副本/原key过期ref重试、停用启用、两次重启/卸载退出0；另seq32/negative10通过 |
| 真实MCP | 三实际payload SDK initialize/tools/list与签名manifest逐字段一致；关stdin自然exit0，无kill/残留PID |
| session恢复 | 9项proof/真实terminal-fullACK/drain、engine/retention34通过；旧/篡改/部分ACK/unknown拒绝结案 |
| UI | 最新feature52/52与desktop typecheck；握手高水位缓存、reset/旧代/溢出回归，独立CR无遗留P0/P1/P2 |
| 公共壳/最终包 | 实际Electron三包/重启/退出22断言；最终win-unpacked启动/退出12断言、无残留；标准验包3682资源一致 |
| 打包验签修复 | PS7→PS5.1模块路径隔离，独立10/10及CR；NotSigned/Valid策略不变 |

独立CR核对76编译文件与最后f52b Host一致。离库Host末次安装约2.04/4.79/4.57秒、清单5.43～5.57秒；压力及内存限制见第8节，不能当客户机性能保证。

三CLI定向BOSS46、微信相关27、企微相关19、OCR取消5、打包3通过。全量CLI不全绿：HEAD可复现微信2项、企微2项环境/mock基线失败；微信全collector有send竞争，定向12项当前与HEAD均过。提交前Python相关独立28通过/1失败/0跳过，失败为HEAD也有BOSS catalog21/Host18差异；保持权限不扩大。

### 10.3 Windows人工验收交付

| 项目 | 最终记录 |
|---|---|
| 安装器 | [AID-Work-Runtime-0.0.2-win-x64-acceptance-unsigned.exe](../../clients/agent-desktop/build/runtime-release/acceptance/AID-Work-Runtime-0.0.2-win-x64-acceptance-unsigned.exe) |
| 字节数/SHA256 | 126241075 / `68b855010c8f39798a8822df83191e3044bdbb7860d46123345fe1dfbee9d3bd` |
| 构建输入摘要 | `d12b14e1758bdba88ffaff6d79d4e17a68181e3f3d947e21e3f6616bb3e76aff` |
| Host | 0.2.14 / `f52b056be3b729ebfef1cc9786079b34d16eb9092312d19076269bed527fc13f` |
| 发行记录 | [runtime-release-manifest.json](../../clients/agent-desktop/build/runtime-release/acceptance/runtime-release-manifest.json) |
| 人工步骤/三包 | [构建手册12节](../system/desktop-agent-client-build-manual.md#12-独立runtime首期验收包) |
| H3证据 | [Desktop计划9.7](plan-desktop-agent-client.md#97-第一部分h3实施登记2026-10-09) |

独立验收产品cn.aidingyi.agent.runtime.acceptance，Windows签名NotSigned；三插件自身仍验发布者签名。最新installer已包含握手修复，旧安装器/临时Host候选不作为交付。

自动化替代了原生dialog返回值，随后Main文件冻结/IPC/Host验签真实。NSIS安装、OS选框交互、真实服务配对和软件登录/业务仍人工：安装打开→现有服务地址/设备名/一次性码配对→空核心连接→原生选包→登录软件并刷新→停用/重启/卸载→已授权只读云端任务→托盘恢复/安全退出。

### 10.4 提交与线上影响边界

已提交并推送`f1087289`，156本任务文件；未部署。用户明确master推送不会自动部署，手动部署需另授权。服务端src、Web业务/构建配置、微信KF实现及部署配置未纳入本批；Web完整build通过，244源/523模块无Desktop/native依赖。旧pack布局前置拒绝通过独立验证，不删除legacy入口。

源码提交不使线上服务或已装客户机自动切换版本。首期本轮停止；整体索引仍部分完成，A4人工/正式发行及共同wire未冻项保留，B1/B2暂不开发。

## 11. 本次文档收敛

2026-10-09按用户要求，将旧M1/M2任务与Runtime M0.4合入本计划/唯一架构，删除过时独立调研设计与实施计划，替换入链引用。将代码完成与未验收分开登记；原Server审批/hash事实保留，未实现第三方新机制不改状态。不改执行逻辑、共同契约语义或schema版本；用户已明确要求提交本次整理，不部署。

文档核对：43个Runtime相关本地链接/锚点、索引唯一性与B1/B2状态检查通过；删除文件的旧引用已清除。源码/测试/配置仅修注释指针，15个Python文件排除docstring后的AST与HEAD一致，TypeScript去注释和YAML非注释内容在替换前后相同；定向git diff --check通过。纯文档工作未重跑业务测试，工作区其他任务修改保留。
