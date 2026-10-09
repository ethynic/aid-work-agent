# Runtime Host v1：schema 与 fixture

状态：候选交付0.3（2026-10-09），补齐插件列表水位与实际版本展示；**未共同冻结、未接入生产**。架构基线为[共同契约v2.0](../../../docs/system/runner-desktop-runtime-integration-contract.md)。Desktop对候选0.1/0.2的历史审阅不替代本批重新消费验证。

本批只覆盖管理、插件描述/清单与工具输出内容。H2设备binding、workspace grant、claim/permit与长占用wire仍由既定工作流交付，不替代已有Device API结果/ACK。`.schema.json`是格式来源，无新生产依赖、类型生成框架或第二套任务Agent loop。

## 当前实施范围

2026-10-09用户限定当前第一部分仅Runtime UI＋我们自己的CLI插件安装管理。首期plugins.import实现只接纳支持的第一方离线包；第三方skill返回明确不支持，不执行安装/依赖构建/源码上传。skill的documents/entries/code_map、手册优先AI流程和fixture仍保留，作为第二部分契约，暂不开发。保留这些格式不等于首期可安装skill，UI不显示可用的第三方导入入口。

范围和验收以[Runtime计划](../../../docs/plans/plan-runtime-plugin-host.md#第一部分runtime-ui与第一方cli插件)为准；0.3不开放新的业务能力或第三方安装。

## 候选0.2迁移到0.3

plugins.list成功result由数组改为`{instance_id, revision, plugins}`，实例与revision必填；插件项增加可选非空version，用于展示实际版本。旧数组明确拒绝，消费方须显式迁移并重新验证。release_id继续是不可猜版本的发布身份；未知version省略，UI显示“版本未知”。不新增版本授权或第二套清单。

Host/plugin可见状态变化共用Host revision，每次提交增加水位；describe/getState/plugins.list/state_changed使用同实例域。消费方共享最高水位，实例变化清两类快照，旧连接回调隔离，本地查询序号防止同revision的迟到列表覆盖后发查询。序号不传wire；真实adapter结束失效查询并维持dirty/补查，不能将丢弃旧回复当刷新完成。

0.2未冻结未上线，0.3在同目录替代它；api_major/schema_version仍为1，架构契约仍v2.0。除列表result和可选version外，本批不改管理请求、H4插件描述/清单/输出、Device结果/ACK或H2协议。七项实施决议及实际包/信任/H3门见[Runtime设计第12节](../../../docs/system/runtime-plugin-host-architecture-design.md#12-第一部分开发前实施决议)。

## 候选0.1迁移

用户改变了插件授权与回复格式，因此架构升v2.0，wire候选升0.2。0.1尚未上线/冻结，0.2在同目录替代它；目录v1、api_major/schema_version=1仍是首个待冻结wire版本，不等于架构版本。旧二选一result/error对象、字符串错误码、工具success、插件registration审批字段不再接受。消费方须显式迁移或编写旧格式adapter并重新验证，不把本次变更当成可忽略的新字段。已有生产Provider/Device协议维持原语义，格式转换在实际adapter接线时完成。

本批变更：安装即插件授权，成功安装默认启用；AI先读SKILL.md，不足才补传必要源码；skill增加入口description、可选output_schema及最小code_map；管理回复/operation和工具内容统一数字code/error。普通手册skill允许空入口，避免虚构执行能力。

## 文件入口

| 文件 | 用途 |
|---|---|
| [management-request](schemas/management-request.schema.json) / [management-response](schemas/management-response.schema.json) | 管理请求、固定回复与管理operation |
| [host-state](schemas/host-state.schema.json) / [host-event](schemas/host-event.schema.json) | 实例状态与刷新通知 |
| [plugin-contract](schemas/plugin-contract.schema.json) | 原始手册/skill入口/代码地图，或MCP工具描述 |
| [plugin-inventory](schemas/plugin-inventory.schema.json) | 安装、版本、启用与就绪清单 |
| [tool-output](schemas/tool-output.schema.json) | 执行结果内容、效果、完整性及产物引用 |

[management.json](fixtures/management.json)覆盖空插件、安装就绪、缺依赖、错误与停止核对；[plugins.json](fixtures/plugins.json)覆盖skill/MCP、手册型skill、空清单、同名异身份及停用；[outputs.json](fixtures/outputs.json)覆盖截图、unknown和截断；[invalid.json](fixtures/invalid.json)覆盖越界路径、非法混用、旧形状及错误码矛盾。设备、摘要和配对码均为测试占位值，不代表真实安装或授权。

## H1：管理消息

请求固定`request_id / method / params`。request_id关联当前连接回复；变更操作另带request_key，Host持久查回同一operation，同键不同意图拒绝。传输重发可换request_id，但沿用request_key，不重复安装或执行依赖。查询不带request_key。

方法：`describe / getState / start / stop / pair / plugins.list / plugins.import / plugins.enable / plugins.disable / plugins.uninstall / operations.get`。变更返回operation，客户端查询状态。无invokeTool、spawn、任意shell或通用文件读取；grants方法等待H2类型。

管理回复固定如下字段：

```json
{"request_id":"read-1","method":"plugins.list","code":0,"error":"","result":{"instance_id":"host-1","revision":0,"plugins":[]}}
```

code为非负整数；成功0/error为空/result按方法返回；失败非零/error非空/result=null。未知非零码也展示脱敏error，不崩溃。读取请求成功不表示被查询operation成功。operation固定operation_id/operation/status/code/error，failed须非零码和原因，其他状态为0/空error；可选message提供处理中诊断。operation须匹配原管理方法，operations.get可查任一种。

status为`running / succeeded / failed / reconciling`；reconciling仍需核对旧进程，不能显示安全停止。operation_id不冒充runner_id/invocation_id。

### 错误码

| code | 含义 |
|---|---|
| 0 | 成功 |
| 1 | INVALID_REQUEST：请求不合法 |
| 2 | API_VERSION_UNSUPPORTED：管理版本不支持 |
| 3 | FEATURE_UNSUPPORTED：功能不支持 |
| 4 | INSTANCE_CONFLICT：实例冲突 |
| 5 | NOT_PAIRED：未配对 |
| 6 | DEVICE_REVOKED：设备接入已撤销 |
| 7 | SELECTION_EXPIRED：选包引用失效 |
| 8 | PACKAGE_INVALID：包不合法 |
| 9 | ENVIRONMENT_NOT_READY：环境未就绪 |
| 10 | MANAGEMENT_REQUEST_CONFLICT：同键不同意图 |
| 11 | RECONCILIATION_REQUIRED：需要核对 |
| 100 | EXECUTION_UNKNOWN：工具业务效果待核对（输出内容） |

新增实际错误再登记非零数字，不预设大量业务码。管理错误码不创造业务重试许可；已有Provider/Device外层码不在此改名。

describe返回api_major=1、host_version、features、instance_id、revision。features本批为first_party_plugins/events，未知值忽略；缺first_party_plugins禁用第一方插件管理，缺events用getState刷新。未知major提示升级，不回退旧D1；UI软件版本不用于协议协商。

Host状态包含instance_id/revision/supervisor/state/connection，可选device及blocked_reason。supervisor为cli/desktop/runtime_app；state为stopped/starting/running/draining/reconciling/blocked；connection为unpaired/connecting/online/offline/revoked。运行与联网分开，online不代表桌面可交互。

state_changed只含实例与revision，是重新查询通知，不是日志/任务事件账本。revision同实例单调非负，重启换instance_id。消费方水位取有效describe、已见通知及接受的getState/plugins.list快照最大值；同实例旧describe不降低水位。两种查询低于已见水位时保留dirty/补查直到追上；一个查询推进水位后另一个快照也可能需要刷新。新实例清两类旧快照/待查询并重置水位，旧实例迟到回复不回退当前状态。同revision列表的先发查询不能覆盖后发结果，使用本地查询序号处理。

重连清待响应请求，回调绑定本地connection generation，旧连接回复/通知丢弃；generation不传wire。真实adapter结束失效Promise、处理超时/取消并补查；fake不提供完整Promise调度或已接线证明。断订阅/关页不停止Host。

pair只经同机可信通道接收server/device_name/pairing_code；Host保管token，响应/状态不回显凭证和配对码，不持久或记录完整pair参数。已有身份替换先排空在途/未知占用和旧ACK；失败保留原身份。plugins.import接收main/platform选择器产生的selection_ref，不接受renderer裸路径或模型制造的引用。Host先查询持久request_key/method/意图，再核验新引用归属/期限及包；已消费/过期引用的原请求重发返回原operation，同键新意图拒绝。首次受理固定安全暂存包摘要与文件身份，配对敏感意图使用OS保护的稳定去重secret做HMAC摘要，详见设计12.5。用户选包安装即本机授权，无第二次插件批准。schema不能替代可信通道、文件检查和凭证保管。

## H4：插件描述与清单

plugin_id是稳定身份，display_name只展示，同名不合并；release_id固定具体发布，installation_id用于本机管理。plugins.list可选version为核验的provider_version或可靠legacy版本，不从release_id推算。致命包/兼容失败不提交；安装成功默认enabled=true，可恢复的应用/登录未就绪显示ready=false/reason，升级保留enabled值。停用阻止新调用。无registration审批字段。服务端按认证身份、既有设备使用关系、格式及版本接纳，校验不成为另一项插件授权；同步失败保留诊断，不能假称Agent已拿到描述。

kind=skill包含documents（必须含原始SKILL.md）、entries和code_map。entry含name/description/args_schema，可选output_schema；现有argv适配由args_schema约束，不上传executable/cwd/env或入口脚本绝对路径。实际入口映射留本机。手册型可空entries/code_map，不发明可执行入口。

code_map每项只含包内相对path/description，用于理解文件用途，不能代替entries。Host核对实际文件，未知用途标注“用途待确认”；文档/地图路径拒绝绝对路径、反斜杠、父目录/点路径。重复路径/入口和实际文件存在由接纳/Host实现核验，不用复杂schema表达系统身份策略。

安装分析先读SKILL.md；足够就生成描述，不上传源码。有缺口才补相关源码，确需分析依赖再补必要文件。用户已允许必要源码用于云端模型分析；原始手册与AI生成内容分开，源码不默认保存为永久documents。AI产出经Host可验证检查，未确认入口不暴露；这是安装分析，不是新本地任务Agent loop。分析器/源码发送逻辑仍未实现。

kind=mcp含tools（name/description/input_schema，可选output_schema），不混用skill的documents/entries/code_map。描述使用实际CLI注册信息。

content_digest/contract_digest是SHA-256小写hex，分别识别不可变包内容及描述版本；摘要算法/规范化将在H4实际接线统一，不能据占位fixture承诺完整性或安全审计。platforms列出兼容平台；resources列出desktop/workspace/network需求，按Host核验登记信息仲裁。未知脚本保守按可能占用桌面处理，脚本自报“纯计算”不免锁，不增加用户插件批准。

嵌入args/input/output schema由实际登记校验器检查，要求自包含，不解析网络外部ref；外层schema仅校验对象形状。身份/权限/摘要真实性不由脚本自报取得。

inventory包含schema_version=1、inventory_revision和installations；记录installation/plugin/release身份、contract_digest、revision、enabled/ready和可选reason。空清单合法，不传token、tenant或本机绝对路径。设备绑定由认证上下文取得，版本/停用等执行前复查。

## 工具输出与原协议

输出内容必填code/error/effect/complete；0/空error表示调用成功，非零/非空error说明失败。没有success。text/data/artifacts按需提供；错误仍可带已产生的结果或产物，不能因失败抹掉效果。

effect沿已有none/applied/partial/unknown：读取可为none，变更完成applied，部分完成partial，无法确认unknown。complete只表示输出完整性，与业务效果和code独立。partial仅用于实际支持的既有适配，v2仍按原合法effect/phase核对；不信任任意脚本的效果声明，不增加safe_to_retry。

输出内容不含claim_token/permit/request_id/phase/ACK。实际adapter转换到已有Device结果内容/外层协议，不能把0.2对象原样冒充已兼容的旧Provider输出。unknown不自动重放，ACK前只补传原结果。

artifact_ref是受权登记的引用，device_id/invocation_id保留归属，name/mime_type用于展示；无本机路径、任意URL或打开命令。消费方复核目标设备与调用再走受权内容接口；B的文件不能作为A本机路径打开，下载是显式复制。图片须实际进入Runner图片输入。schema验证形状不能证明产物接口权限已实现。

## 校验与Desktop接收

仓库根执行：

```powershell
.\venv\Scripts\python.exe -X utf8 contracts/runtime-host/v1/scripts/check.py
node --test contracts/runtime-host/v1/tests/consumer.test.mjs
```

Python使用Draft2020-12和本地registry，不网络解析；[consumer-fake.mjs](fixtures/consumer-fake.mjs)只供消费规则测试，不导入生产或执行插件。Desktop读取本README、schema和fixture实现实际main/preload adapter，重新验证候选0.3；服务端接纳/Host producer/H2/H3/资源与图片联调仍未完成，不能据样例登记wire已冻结。

状态与交接见[Runtime计划](../../../docs/plans/plan-runtime-plugin-host.md#8-首期实施约束)。Desktop候选0.2的[历史审阅](../../../docs/plans/plan-desktop-agent-client.md#92-用户复核后候选02交接2026-10-09)保留，本批接收结论由其另行登记。
