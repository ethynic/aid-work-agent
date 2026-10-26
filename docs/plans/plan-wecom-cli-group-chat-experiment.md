# 开发计划：wecom CLI 群聊实验与输入链路修复

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|---|---|---|---|
| 基线 | 当前工作树CLI构建、账号/群定位与增量读取 | ✅ 完成（2026-10-23） | 本地库读取正常；无私有账号ID或群消息正文入库 |
| 修复 | 搜索/编辑区定位与输入链路最小修复 | ✅ 完成（2026-10-26） | 完整搜索、发布后凭证、个人标记兼容及OLE空读恢复闭环 |
| 验证 | 独立测试、独立CR及主控真机验收 | ✅ 完成（2026-10-26） | 最终独立策略/空读23项、三PS入口及CR通过；只搜索连续两轮与原文恢复通过；历史发送见下文 |
| 实验 | 持续轮询、@本人必回、其他消息择机回复 | ✅ 完成（2026-10-23） | 用户要求暂停后发送结束招呼并退出；累计590轮、8条确认发送，无pending |
| 搜索收尾 | 完整搜索与剪贴板恢复（2026-10-26） | ✅ 完成（2026-10-26） | 最终当前源码双轮CLI搜索/全文复制/原文及个人状态/草稿/清理核对通过；未提交、部署或恢复监听 |
| 发送对账 | W4严格核验与重启后只读恢复 | ✅ 完成（2026-10-26） | 独测TS49/恢复7/Python78通过，CR无遗留；仅CLI，无新真实发送验收 |
| MCP/Runtime对账 | W4新增只读工具接线 | ✅ 完成（2026-10-26） | PG63/TS32通过，单测173通过另2旧BOSS失败；CR无遗留 |

## 2026-10-26 MCP与Runtime发送对账接线

基线`4c024796`，用户授权继续接入现有发送对账能力，独立测试与CR；不提交/推送、部署或恢复监听。按高风险流程，主控开发MCP/Runtime并自测，后端开发角色限域写Python代理/精确scope及其测试，源码冻结后两个独立角色分别TEST/CR。保留原package-lock生成改动，不操作真实群发送。设计沿[§4.3.2](../design/wecom/wecom-cli-localdb-design.md#432-对账能力mcpruntime接线2026-10-26)。

实施共同检查点（唯一写入者）：主控拥有`clients/wecom-cli/src/mcp/{toolDefs,server}.ts`、共享core `legacy/{providers,providerManager}.ts`、相关TS/MJS测试及所有设计/计划/契约；后端角色拥有`src/local_tools/`必要注册/代理/repository/read-only scope与Python测试、`subagents/recruiting-operator/SUBAGENT.md`；另限域维护`src/services/agent_runner/local_recovery.py`在原严格参数比较前的status来源证明适配，并在overlay_flow.poll_operation同源适配Cloud ID与native receipt等价，原owner/phase严格比较及_known不变。不修改Device/Host/H3公共wire、其他Runner核心、outbox格式或全局package/lockfile。所有额外必要文件先明确责任，不让两个角色并行写同文件。

成功标准：真实stdio MCP发现/校验/调用到原只读operation，Runtime只给wecom路由并保留readonly/effectnone；旧CLI缺新能力明确拒绝，旧6工具不受影响，缓存随子进程清除；原发送unknown的receipt可由可信普通/late结果提取，cloud模型只给send_invocation_id，跨主体/会话/设备和伪造scope零派发；detached hold只允许这次精确只读查询且原hold/unknown不改；查询unknown不等于发送confirmed，默认工具价非零也不误扣本地工具费；原发送绝不重放。原Runner verification等待不自动解除，不把它扩成新的恢复平台或持续任务验收。

计划：登记新增schema v1与2.9共同边界 → MCP工具及Runtime受信/实际能力门禁 → 后端代理与来源scope/收费 → 匿名合成库或注入边界的生产消费集成测试 → 独立测试/CR并修复到通过 → 更新本节。真实群聊、真实配对/发行包安装和多天运行仍另验，不能用mock冒充；若真实数据只读核对确有必要，只读且不入项目日志。

### MCP/Runtime接线验证过程记录

主控已完成CLI、共享core/native和Runtime构建；依赖按原lockfile执行npm ci --ignore-scripts，未修改package/lockfile。初次core缺tsc/原生包声明由补齐依赖并先构建native解决；测试首次把ProviderSet.shutdownAll误写为shutdown，已修测试并只停止所属MCP测试子进程；旧manifest数组位置断言随新工具插入而失败，改按工具名取对应schema后11项通过。这些失败不计作产品通过证据。

TS freeze2自测：新WeCom兼容/stdio4项+既有续筛/结果解析9项共13通过，Runtime wecom定向4通过，CLI manifest/progress11通过，无跳过。真实stdio验证缺失凭据读取从MCP经Runtime原outbox ACK完整保留unknown/effectnone，未读真实账号或发消息。独立CR在freeze1发现额外必填参数被误接受（P2），已修required恰好receipt_id、可选字段仍可兼容；freeze2子集CR通过，完整后端待最终独测/CR。

后端新增权限触及入队/claim/started，使用本任务新建、loopback随机端口的PostgreSQL17.11 helper及随机aid_test_*库验证真实SQL，不连接.env或业务库。二进制来自[EDB官方下载](https://www.enterprisedb.com/download-postgresql-binaries)，ZIP SHA256为4b8db0930c38f6ef845db919551dedda3b6b845aeb0927b3d79a6e8e9e4537cf，仅开发测试依赖，无系统服务或业务部署；本地bin缓存保留。Windows pg_ctl stdout继承导致bootstrap未返回，但root依据自建data/postmaster.pid恢复所有权元数据并用psycopg2验证同helper，不接管旧helper。SQL验证及完整冻结结果待补。

只读对账的工具费固定0；原Runner _known不放开，cloud send_invocation_id与native receipt_id在resume及poll两处以同一原发送证据等价验证，不能省略取消或重启归属证明。旧BOSS注册面已有两项基线差异（manifest/测试期望漏boss_lookup_candidates、boss_greet_selected），不借本任务恢复无关BOSS能力，独立阶段须保留失败事实。首轮测试临时目录清理曾被自动审批返回blocked by policy而拒绝，未说明更具体原因，保留该匿名目录，不影响源码和测试通过结论。

后端全freeze1独测：147项通过、2项精确既有BOSS注册差异失败；初批相邻PG53项通过，另外4个旧BOSS真实worker用例在预检因当前dashscope SDK缺base_compatible_api_url而setup失败，teardown又遇Windows无os.killpg（1 error）；未将这些旧worker业务记为通过，独测仅按精确PID/父进程/命令清理其自有4个API子进程。原SQL测试随机库均DROP、helper保留给修后独测。此后仅重跑相关status/proof/admission存储范围，不为本任务修改全局SDK安全预检或通用worker夹具。

完整CR发现排队失效P2：原测试仅断言不派发但保留queued，遗漏设备队头会持续重选。最小修复为来源scope失效只结束尚未started的status自身，原send/hold/_known与终态幂等不改；其他未决hold仍阻断。同步补崩溃后消费该server零派发失败的有限私有证明，不把source解析失败当一般恢复许可，不新建调用。额外必填schema兼容P2在TS freeze2已独测/CR闭环；后端随后fullfreeze2复验通过前两点，但独测实际混合end_wait又发现server拒绝status自身被错误当作未完成而phantom hold；fullfreeze3仅对严格服务端no-dispatch证明识别该status自身完成，补持久hold查询所需列，未加全局错误码/未动local_wait策略，最终均独立转绿。

### MCP/Runtime最终结果（2026-10-26）

完成MCP工具及说明、Runtime受信第7项/实际Provider能力门禁、云端原send ID适配与原设备scope。初次调用若当前显式受权设备B与原send设备A不同，拒绝并需选回原设备，不自动换机；已有durable phase按原保存device核对。独立对账不改变原发送unknown/hold或自动解除原Runner verification等待。只有status自身确证未派发的失败可恢复消费已有事实/确证物理完成，不能把它用于原send或新业务授权。

- 构建：主控当前CLI、共享core/native、Runtime构建通过。最终metadata增量仅重跑CLI build及真实stdio断言；core逻辑/Runtime源码与已有构建证据一致，不机械重复全量。MCP初始化前512字符实际断言含Windows、UI登录解锁、单目标、未知不重发、只读查询成功不等于confirmed、真实触达和发送剪贴板覆盖不恢复。
- 独立TS32通过、0失败/跳过：core13、Runtime wecom定向4、CLI manifest/progress11、另4个真实SDK stdio独立场景（原结果ACK丢失仅重投outbox，不重调用Provider；旧CLI/额外required零调用拒绝、可选字段/respawn兼容等）。其中Runtime4按同hash复用，其余28在最终源码重新执行。
- 独立Python单测173通过、2失败、0跳过：`venv/Scripts/python.exe -m pytest tests/unit/local_tools/test_wecom_send_status.py tests/unit/local_tools/test_wecom_proxy_tool.py tests/unit/local_tools/test_proxy_tool.py tests/unit/local_tools/test_local_wait_recovery.py tests/unit/local_tools/test_write_result_billing.py tests/unit/local_tools/test_repository_state.py tests/unit/test_agent_local_tool_registration.py -p no:cacheprovider -q`。两失败精确为本节前述BOSS旧基线，HEAD AST差异与当前一致，未纳入修复；实际Wecom7项装配通过，不称全库全绿。
- 独立真实PG63通过、0失败/错误/跳过：本轮22、相邻proof/admission/store31、独立7及取消/旧hold3。包括原/late来源、跨主体/会话/设备拒绝、三个准入点、只有精确原hold只读旁路、configured默认付费重复result零工具费、失效队头后合法调用推进、拒绝后重启只消费已有失败、借用/客户端marker/坏版本/缺字段拒绝、mixed end_wait与历史held投影。只读查询confirmed/unknown都不改原发送审计/hold。
- PG使用本任务专有helper与原isolated入口随机aid_test库，Temp wrapper只精确省略vector extension/chunks_vec/hnsw3条无关DDL，各断言1，其余原init及业务SQL不变；pgvector未验。原4个BOSS Worker在SDK预检setup失败、Windows teardown错误已单列，不纳入最终63；独测按精确归属PID清其4个API进程。最终随机测试库及连接均0，root已核任务数据目录/PID后停止helper，原bootstrap捕获管道随之以TimeoutExpired退出（设施问题，非pytest结果），没有接管业务DB/旧helper；二进制cache和匿名证据保留。
- 完整独立CR通过，无未解决P0/P1/P2；审查者另执行22项匿名证明/归属/恢复断言通过。首轮CR harness全socket禁连误拦Windows asyncio自管道，先建loop再禁连后绿，未改产品源码。原`_known`函数及`write_result` AST与HEAD一致，无新恢复平台/后台loop/重发队列。

完整29文件freeze3前后hash一致，聚合`0206b5f523b4a11aebec88b89aa095683045e435ad152d4f5e65ddddf5c18be3`（sorted path:sha256，LF，无尾LF），最终仅更新文档验收状态。全命令/证据在`%TEMP%/wecom-status-runtime-independent-58f623d4/backend/independent-full-final-evidence.json`，冻结`%TEMP%/wecom-send-status-full-freeze3.json`。主控复核原unknown门未改、源码hash无漂移、无暂存；HEAD仍`4c024796`，原package-lock三peer生成变化继续保留。无提交、推送、部署、真实群消息或监听恢复。W6持续任务/真实Worker、配对发行、多设备和多天运行验收未因此完成。

### MCP/Runtime接线提交核验（2026-10-26）

用户在最终验收后明确授权提交并推送当前分支，替代上文接线阶段的禁止提交约束；部署、恢复群聊监听仍未授权。提交前fetch origin与github，当前`feature/ask-user-question-card`与两端对应远程分支均无分叉；29文件逐项匹配最终交付清单，原package-lock三peer生成改动保留不提交。复用同源码CLI/core/Runtime构建、独立PG63/TS32、单测173通过另2个HEAD旧BOSS失败及最终CR通过证据；主控追加隔离数据库guard下的注册/启动import/0价检查通过，不重复业务测试或启动真实群聊。未顺带合并新master、不进行发行包安装或部署。

## 2026-10-26 发送确认与对账恢复（W4）

以下保留CLI实现及`4c024796`提交前后的验收记录；当前MCP/Runtime增量进度以上节为准。

本轮基线`6daf84db`，仅在wecom工作树修复发送确认和只读恢复，禁止提交/推送、部署、监听及真实群消息发送。保留既有package-lock生成变化。高风险流程：主控开发、自测 → 独立测试 + 独立CR → 主控整合核验；数据投影由独立开发者限域修改，其余由主控统一整合。沿用[本地库设计§4.3.1](../design/wecom/wecom-cli-localdb-design.md#431-不确定发送的只读对账2026-10-26w4)，不新增Runtime恢复平台。

成功标准：已知多片段可信全文能匹配，未知/提及/截断不误报；第51条之后可达，上限/歧义为unknown；发送前加密凭据落盘，发送后异常及独立进程重启可通过原凭据只读对账，零自动重发；账号/源代际/锚/损坏凭据与时间窗拒绝假确认；结果附脱敏诊断。测试使用匿名合成库及注入发送边界，任何真实库核对仅只读，不把它声称为新真实发送验收。

实施顺序：补严格投影和回归；接发送专用分页核验与DPAPI证据；新增CLI只读send-status；冻结源码后独立测试/CR，修复到通过并同步本节。freeze1主控build与61项定向测试通过，独立Python75项及另3个边界通过；独测另复现2个已知无动作receipt的假confirmed：checkpoint取消零派发、navigate_required后搜索失败，之后出现相同文本可误确认。此轮不通过，统一补本阶段凭据删除、失效失败停止及readonly最终存在性复核，不靠intent文案回避。最初开发测试桩漏raw_last_line编译失败、子进程fixture安装元数据不足导致基线回退已在freeze1前修正；保留失败事实。freeze2独立47项TS和6个业务case通过，但新增真实fsync失败注入复现：wx自建完整密文已写入、flush失败返回前未清理，仍可被后到同文误确认。再次判不通过；修由保存函数统一清write/flush/close失败的owned文件，自清失败的unknown和ID不得被两层包装改成none，freeze3最终独测与CR通过，结果见下。

### W4最终结果（2026-10-26）

- CLI当前源码`npm run build`通过；独立测试隔离LOCALAPPDATA执行`node --test dist/tests/send-receipt.test.js dist/tests/message-send-localdb-verify.test.js dist/tests/message-send-v2.test.js dist/tests/manifest.test.js`，49通过、0失败、0跳过（3/27/13/6）。此前相邻图片/文件发送回归在同一未变源码已通过，不机械重跑。
- 独立匿名业务恢复7项全通过：真实CLI非法/缺失ID；checkpoint取消；导航无动作退役；正常二阶段与派发后取消可多次只读收敛；二阶段保存失败不返回旧ID；真实Windows FileShare.Read句柄阻删时unknown停止；完整真实DPAPI写入后fsync注入失败自清。不是7次真实群发送，也不把与TS重叠的业务意图累加为唯一场景数。
- Python独立执行旧query66、新send-messages9、另3个独立严格解析边界，共78通过、0失败、0跳过。开发者更广相关136通过另记，不冒充独测或重复累加；Python驱动从freeze1至freeze3的hash保持`1bac54ed87b1c9bf79cc19c04f647d6be7bdfd33cac35e30c5dd41cbf33e8fc3`。
- 独立CR最终通过，无未解决P0/P1/P2；两类P2（已知none阶段凭据、owned文件刷盘失败残留）均经过红→修复→独立绿。CR另实际复核新增退役4项、receipt3项及包装1项；其早期外层GBK日志打印失败不算产品失败，也不据此虚报细分测试通过。
- 冻结16文件审查前后hash全部相符；freeze3聚合SHA256为`89b8e2958af67f6a093547e6f749542767036911e457be3871f615b809560865`（sorted path:sha256、LF、无尾LF），最终仅更新验收文档状态。独测证据在本机`%TEMP%/wecom-w4-independent-3948d58e/independent-w4-final-evidence.json`，冻结清单`%TEMP%/wecom-send-w4-freeze3.json`；测试自有锁进程、合成库/凭据已清理，fsync注入finally恢复。

W4 CLI实现完成；不声称服务端送达/已读、业务exactly-once、断电持久性或新的真实群发送验收。子进程exit17证明进程退出后的原凭据只读恢复，不能冒充OS/电源故障。已知none到失效之间的强杀、所有失效IO失败及窗口内人工同账号同文仍按设计保守边界处理；本轮没有新全局账本、后台恢复平台或自动重发。CLI-only的send-status未接MCP/Runtime受信能力，W6集成、原W2/W3/W5/W7/W8继续保留。

本轮未暂存、提交、推送或部署，未恢复群聊监听，也未操作真实UI发送新群消息。HEAD保持`6daf84db`；原package-lock三处peer生成变化hash保持`baa047a299bcc9d514512ffa7f35d1c7db964a0bc66f19c631d611bfff080dda`，Runner/Runtime及前轮搜索/剪贴板代码未动。

### W4提交核验（2026-10-26）

用户在验收完成后明确授权提交并推送当前分支，替代上文修复阶段的禁止提交约束；部署和恢复群聊监听仍未授权。本次仅纳入W4代码、回归及关联文档16个文件，原package-lock三处生成变化保留。提交前fetch origin与github，确认当前分支`feature/ask-user-question-card`与两端对应远程分支均无分叉；源码逐项匹配freeze3，复用主控同源码CLI build、独测TS49/恢复7/Python78及最终CR通过结果，只补充提交记录，不重复业务测试。不引入新的master变更，不进行新UI/发送实验。

## 2026-10-26 继续修复

用户明确要求完成完整搜索和剪贴板恢复，走独立测试与CodeReview，不提交、不部署、不恢复群聊监听。基线为`d03dc07b`，保留原有CLI package-lock生成变化，不混入Runner/Runtime修改。沿用既有搜索和发送器，只修复发布凭证/恢复编排；先以新回归证明旧版Close后版本变化遗留nonce，再对最终工作树独立验证。

本轮成功标准：完整长群名搜索与连续搜索可用；原纯文本/空剪贴板在成功与可处理失败路径均按当前归属安全恢复，外部后到内容不被恢复动作覆盖；富格式、未知布局及系统写入/关闭失败安全中止；保持原草稿保护及unknown不重发。主控真机仅执行搜索、非发送清理与剪贴板核对，账号/群消息/原剪贴板正文不入项目文档。最终结果更新本节及W1，不以旧副本绿灯替代当前真机验收。

本轮新增边界：复制查询后首次读取失败、尚无copySeq时保守不恢复，但原内容可能已被替换；已补错误分类及真实Body回归，未恢复时明确告知用户，确已回收自有nonce时不虚报原值丢失。原测试5ms时限两次偶发首例失败保留记录，测试改为100ms（产品默认1500ms不变），100ms副本连续3轮及当前源通过，不声称直接捕获了两次失败的调度原因。

真机纯文本还附带`EnterpriseDataProtectionId`，用户从纯文本来源重新复制后仍存在。只读[官方EDP接口](https://learn.microsoft.com/en-us/windows/win32/devnotes/edpgetenterpriseidforclipboard-function)实际返回成功及NULL（非企业数据），此前‘任意额外格式一律拒绝’误拒个人文本。最小兼容只接受这一种额外标记且官方接口成功/NULL；实际企业身份、接口失败/缺失及其他HTML/图片/文件/未知格式仍拒绝。不读取、记录或修改企业身份，非NULL返回缓冲按官方要求HeapFree；所有证明仍受现有稳定sequence和原子CAS约束，不支持企业数据降级或通用富格式备份。若记录恢复后保护状态，须由本轮真机只读复核，不凭写入成功推断。

### 本轮最终结果与复现范围

- 实测OLE可能在原生写入后同一sequence、同一owner下首次返回空、下一次却返回nonce。`GetPlain`仅首次null/零格式时延迟25ms重新取对象一次，后续完整格式/EDP校验不变。copy阶段空值不建立copySeq，而是在原有1500ms期限内等非空可信结果；连续空读或读取失败且原文未恢复时明确提示，不盲写，也不自动重新输入查询。
- 最终独立策略/流式空读23场景及三个PS入口全部通过，0失败/跳过。独立CR通过，无未解决P0/P1/P2；实际执行生产GetPlain、Echo、搜索Body及原生policy helper，仅OS边界替换，测试不操作真实UI/剪贴板。先前Close版本、复制后读异常、copy永久空读的红灯均保留，不以旧轮次绿灯代替新边界验证，各分组不累加为唯一用例数。
- 主控CLI build与相关Node/PS定向42项通过（搜索、发送编排、图片/文件发送守卫与三个真实PS测试入口），无真实发送。最终Common SHA256为`05fe0c35fbb015996439d8c04436a0970b0ffe6c77e3ebe725efd0abedfb5795`；policy/空读回归为`03f6749bd2ecd78efb22d6a87c12559ea4fbf8bd1ec926ec8d240f0b245180e3`，Native CAS替换/回滚算法与基线逐字一致。
- 最终工作树真实CLI连续两次完整长群名搜索均`OK`；两次`full_copy_verified=true`、原剪贴板全文Ordinal相等、恢复后官方保护状态为个人。安全清理搜索后仍全文相等、个人状态一致、聊天草稿为空。UI搜索项名称带成员数后缀，验收按该已观察显示项核对；未放宽产品query的完整复制规则，也未执行select/read-history/send或监听命令。
- 最终真机证据保留本机任务Temp的`live-search-result.json`与CLI search artifacts，只有匿名布尔/计数进入项目记录，原剪贴板及群历史正文不落项目文件。最初因HTML/个人系统标记而拒绝、以及验收脚本误把成员数后缀判为不匹配的事实保留为过程失败，不计成产品成功；最终双轮为修正后的独立新调用。
- 空剪贴板、富格式、真实企业身份和系统故障路径由隔离模型验证；本轮真实环境只验证当前已标定布局的个人纯文本，不声称任意DPI或受保护企业数据可用。原被覆盖的历史剪贴板原文未保留，不能补称找回；进程强杀/系统无法写入也不保证finally，仍明确失败。没有新增持久备份、队列、监听服务或恢复状态机。

在`clients/wecom-cli`目录可复现无UI回归：

```powershell
npm run build
node --test dist/tests/input-locator.test.js dist/tests/powershell-input.independent.test.js dist/tests/chat-search-v2.test.js dist/tests/message-send-v2.test.js dist/tests/send-image.test.js dist/tests/send-file.test.js
```

本轮未暂存、提交、推送或部署；HEAD仍为`d03dc07b`。原package-lock生成变化保留不动，Runner及其他任务代码未改。W1已完成，W2扩展布局验收及W3–W8的产品编排/长期联动等后续项目仍保留，不因CLI本次修复而宣称完成。

### 本轮提交核验（2026-10-26）

用户在验收完成后明确授权提交并推送当前分支，替代上文修复阶段的禁止提交约束；部署与恢复群聊监听仍未授权。本次只纳入完整搜索/剪贴板恢复代码、回归及关联文档共8个文件，原package-lock生成变化继续保留不提交。提交前fetch确认upstream为`origin/feature/ask-user-question-card`且无分叉；Common与policy回归hash分别匹配上文最终验收版本，复用同源码下的CLI构建、42项定向测试、独立测试/CR及最终真机双轮结果。没有新增UI实验、群消息发送或监听进程。

## 后续处理清单（2026-10-23 提交检查点）

用户要求先记录问题、后续处理并提交当前代码。本次保存开发检查点，不恢复群聊监听、不部署，也不将未完成验收记为可正式上线。

| 编号 | 待处理内容 | 当前证据与后续验收标准 | 状态 |
|---|---|---|---|
| W1 | 完整群名搜索与剪贴板恢复 | 已应用旧补丁并补齐个人标记、OLE空读及未恢复提示；最终独测23项与CR通过，当前源码真实双轮完整搜索、原文恢复、个人状态、草稿和清理核对通过 | 已完成（2026-10-26） |
| W2 | 搜索与编辑区定位稳定性 | 工具栏误选已修；OCR横向裁切、图标先于正文、正文合并×已有回归。继续验证窗口尺寸、DPI及焦点变化，布局不能确认时须安全中止、保留人工草稿 | 待扩展验收 |
| W3 | 人工同账号发言与自动出站消息区分 | 临时脚本已修正self全过滤；产品监控编排须以已确认出站ID区分，验证人工发言不漏、自动回复不自触发，不直接复制临时脚本的近期原文去重策略作为永久协议 | 待产品接入 |
| W4 | 不确定发送的核验与幂等 | 本轮一次unknown经原群可信全文只读核对，未重复发送；严格投影/分页、加密凭据与CLI只读重启对账完成；独测TS49/恢复7/Python78及CR通过，unknown不重发 | CLI/MCP/Runtime接线已完成 |
| W5 | 普通群友闲聊不插话 | @本人和非@承接已实测；纯群友闲聊未遇真实样本。补第三人互聊、话题切换及多条@合并处理样本 | 待补验收 |
| W6 | 真正Runner与Runtime持续任务联动 | 原实验是开发助手决策+临时脚本轮询；发送对账MCP/Runtime接线已开发验收，真正持续Runner、多天运行/发布重启/停止在途/重复唤醒/累计用量与状态增长仍须集成验收 | 待集成验收 |
| W7 | 多任务共用企业微信设备 | 本轮只串行操作一个群。另验多会话任务与单任务多群，复用既有设备队列/锁，保证搜索/切群/发送的连续操作不相互串扰 | 待并发验收 |
| W8 | 时延与读取故障恢复 | 约10秒轮询另计CLI耗时，实际单次发送约20–27秒，不能承诺10秒回复；记录各阶段时延并定位瓶颈。补CLI无结构化输出、断连后的游标保留和pending恢复 | 待处理 |

W1的[历史补丁](patches/wecom-search-clipboard-recovery-pending.patch)已于2026-10-26应用并进一步修复，仅供追溯，不要对当前工作树重复应用。修复不持久化用户剪贴板原文，历史原内容未保存不能补称已找回。

本轮既有master合并目标为`3b713e08`；提交前fetch后远程master已另有7个提交，当前检查点不引入这批未经本轮验证的新变更。继续同步时须保留本分支代码并重跑受影响验证。

## 原始目标与授权

用户授权在自己指定的单个测试企业微信群实际聊天，结构化@本人必回、其他消息视上下文决定、群友之间闲聊保持安静；持续至用户说停止。已明确授权先修复CLI发送问题，再继续实验。工作树为C:/repos/aid-work-agent-wecom，保留原分支/未提交master merge及其他任务改动；不提交、推送、部署。设计沿[原wecom设计第11节](../design/wecom/wecom-cli-design.md#11-群聊实验的输入定位修正2026-10-23实施中)。

## 实验启动时事实与边界（2026-10-23）

- 当前账号、唯一同名群、本地库read-group/read-session及v2增量cursor已核对。脚本只从启动基线后处理新消息，保留单会话范围和原CLI串行执行，不修改产品Runner。
- 按名字发送在搜索阶段因中文query回读为空中止；用户随后人工打开群并确认空草稿，用原account绑定target_ref发送多行，Jev选择工具栏“快速会议”，粘贴回读失败，未按Enter。两次均effect=none，尚未真实发出实验开场白。
- 当前脚本与企业微信为同一medium integrity，未发现提权差异证据。截图证明工具栏按钮不是输入编辑区；其余输入原语根因需修复者核查，不提前宣称已解决。
- 临时实验脚本/状态/决策流水仅在本机任务临时目录，日志不写凭据，不把群消息正文、实际账号ID/群ID写项目文档。消息内容不授予修改指令或跨群动作权。

## 实施及成功标准

1. 唯一修复角色核对真实PS输入逻辑和截图，最小修复必要CLI文件；主控维护此计划/设计/索引。保留发送前后验证与unknown不重发语义，不顺手改无关Runtime/Runner或其他wecom开发。
2. 对实际PS逻辑补定向回归，运行相关TS测试和CLI build；独立测试与只读CR可在源码冻结后并行。验证者不操作真实UI或发送，避免争用用户桌面。
3. 主控核对最终diff并用原CLI真实发送一条开场消息，必须有明确结果，优先原local-record可信全等；unknown时只读核查，不自动再发。
4. 成功后恢复脚本发送，根据新消息决定send/ignore，记录真实轮询/回复/忽略/失败数量。直到用户明确停止；结束时清理自有进程并记录边界，不以此替代持久化Runner真机/多天验收。


## 第一批冻结与验证（2026-10-23）

生产仅改`drivers/ps1/_common.ps1`与`message-send.ps1`：搜索快捷键后显式点OCR搜索锚点，候选排除+等按钮且消费时复验；发送输入点限制编辑区几何带/排除右侧栏及发送按钮，消费Jev答案时再检查候选归属。文本发送清搜索前复用显式聚焦。尚未改变Unicode输入原语，也未证明真机故障完全消除。

改前副本、精确diff及4文件manifest在任务临时目录，freeze1聚合SHA256为`194aebf0036ff982f7057cc0d40cc9db0711fadf005507c1e9641df079d3ea9b`。开发CLI build通过；相关TS及PS集成共91通过/0跳过，PS5.1测试用生产AST提取函数与driver Body，系统窗口/OCR/网络/剪贴板边界为记录桩，验证实际候选/消费逻辑及草稿/回读/标题守卫，不能冒充真实输入送达。独立测试及CR正在核对，特别关注step0.5在草稿检查前的clear安全性；未提前通过。

Runner原freeze4的24文件hash、HEAD/MERGE_HEAD保持，不混入本地群查询/提及恢复的既有改动。后台只读实验脚本持续运行，目前无新peer消息、无成功发送。


第一批CR未通过：真实PS5.1 AST复现无可靠搜索锚点时仍接受任意crop token，发送step0.5在draft判定前无条件Clear。示例只有左侧角标token且PlaceholderPos/ContentPos为空，Jev选择该token，调用序列为CtrlF→click→clear→draft错误；开发测试把Clear整体桩住，未证明内部编辑焦点已切换。复现未操作真实UI或用户草稿。修复回原唯一写入者：空搜索无动作，未知状态失败；非空仅可信搜索专属×点击后回读，不再依靠CtrlA/Delete清当前焦点；Focus候选必须对应可靠PlaceholderPos/ContentPos且通过几何/消费门。补执行真实Clear函数的独立回归后再审，不以第一批91通过替代安全闭环。


独立测试对freeze1精确副本执行实际PS原语/driver决策，17场景中16通过、1失败；失败就是无锚点Jev token仍导致真实CtrlA/Delete清理。其余包含实际Unicode消息序列、chord状态恢复、回读完整/缺字、编辑点/草稿/剪贴板上限、title、unknown单Enter与VerifyOnly零输入。未触及真实UI。

第二批安全收敛：现有截图搜索为空，没有非空query右端×的可靠实测证据；不得猜按钮坐标。先实现empty无动作、unknown/has_content缺可靠专用清理证据时失败且零破坏性快捷键，可靠搜索锚点严格验证。该临时限制不等于完整非空清理已恢复；独测CR通过后，主控先用已开群/空搜索验证原ref发送，再单独执行搜索只读交互采集非空×现场，按证据补齐，未完成前不记CLI整体修复已验收。


## 第二批冻结与限定安全通过（2026-10-23）

freeze2四文件聚合SHA256为`f35c3a2ffaddcda1e3723ddb23ff880fd08de9a1818e0c9ef98a0ff6941d6b09`；生产只有两个原PS驱动。开发91项及build通过，独立PS20场景通过、独立build通过。CR通过本批安全复核，允许主控仅以空搜索/已开原群/account绑定ref作一次live验证；原P1无可靠锚点清草稿路径已闭环。非空搜索/重复search仍明确不能自动清理，不能称完整CLI复原。

独立相关TS首批149项中146通过/3失败：旧operations-m3 unread用例仅mock UI驱动却读取了本机真实localdb状态，尚不认定为产品回归；测试角色将以临时LOCALAPPDATA隔离重跑该文件，保留原失败记录，不改产品或共享fixture。最终结果和真机发送待完成。


## 限定真机通过与实验恢复（2026-10-23）

独立最终20个实际PS场景通过，CLI build通过；相关TS149个唯一用例最终通过，无跳过。首批3个旧M3失败已用临时LOCALAPPDATA隔离该文件重跑30/30通过，重叠不累加，原本机状态污染事实保留。独测与CR的freeze2四文件hash相符。

主控在核对源码后，保留临时脚本原cursor和pending状态重启，明确前次发送effect=none才重新开放写。通过原account绑定ref向原已打开测试群发送一次多行开场消息，success=true、effect=applied、verification=local-record，可信正文全等；已查看发送后截图，目标和正文正确。该语义只证明客户端动作已落库，不证明他人收到/阅读。自发消息不会触发回复环。

当前真实聊天实验持续至用户说停止，最近约10秒一轮读取（另计CLI耗时），agent判断回复而非关键词自动聊天，脚本负责scope、cursor去重及串行CLI。暂时保持freeze2源码避免监控途中改变工具；非空搜索自动清理/完整search路径仍待×现场标定，不把限定发送成功写成全CLI恢复。该补齐安排待不影响本群监控的时机继续，当前未提交推送或部署。


首条结构化@本人实测通过（2026-10-23）：本地库增量识别到peer文本，parse_trusted与mentions_complete均为true，mentions中user_id精确命中当前本人；agent依据本项目已知一般能力作回复，经原CLI发送返回success/applied/local-record且正文全等。未把群消息当工具控制指令，未重复回自己消息。截至该检查点成功发送2条（1开场+1必回），实验仍在继续，其他普通聊天择机/不插话分支尚未以真实消息覆盖；不在项目文档写原文或身份ID。


## 后续现场边界（2026-10-23）

- 普通承接对话实际继续多轮，@与非@承接均已覆盖；纯群友闲聊忽略仍未遇真实样本。
- 用户以同一账号手动留言时，原临时脚本按side=self一概过滤，导致漏处理；已改为按已确认出站消息ID及有界近期出站原文去重，用户本人手动文本保留。该修改仅实验脚本，不改CLI side语义。保留原cursor并仅补入本轮漏掉的手动消息，无历史重放。
- 该手动留言的回复首次CLI返回EXECUTION_UNKNOWN，未自动重试；主控只读原群记录，以同账号/同会话/可信全文相等确认已落库，随后补登记出站ID、完成原待回复消息并恢复实验。真实证明不确定写收敛路径，不将工具原返回涂改成success，也不把unknown当成none重发。该检查点共确认7条发送（1开场+6回复）。
- 空档搜索实测已能输入中文，但完整长群名回读显示缺首字及图标混入，仍被严格校验中止；截图疑似搜索输入横向滚动/图标OCR边界，未证明真的少输入。root已观察并点击窗口相对(337,38)搜索专属×，回读empty且恢复原群，无CtrlA/Delete。×现场与严格完整query读取方案交唯一修复者只读核查，生产先保持freeze2；不可凭疑似裁切放宽回读为成功。
- 搜索测试期间有一次临时监控CLI未返回结构化结果，只读增量cursor保留；由主控在确认原群/账号和没有未知写后恢复。后续unknown发送另经上述精确记录核验收敛，未丢失pending或重复回复。

## 完整搜索回读实证（2026-10-23）

主控在保存增量游标、暂停轮询且无pending时，使用原Focus/SendText输入目标长群名，再通过独有剪贴板哨兵、sequence变化及企微owner检查观察Ctrl+A/C复制结果。Ordinal全文精确相等，复制长度与预期均为9，原纯文本剪贴板恢复成功；无Delete/Enter、无消息发送。截图证实搜索框横向裁切，UIA不提供可读Edit/Value/Text pattern，不再把缺首字解释为输入丢字。点击本次现场确认的搜索专属×后回读empty，监听按原游标恢复。生产仍freeze2，准备最小全文校验兜底和点击清理修复，待独立验证；不放宽文本匹配，不将诊断成功冒充完整search验收。

搜索修复proposal3仅在Temp副本冻结，6文件hash为`7a5d1fbc7c43cf1c50cee0091dca8a2dd4ac86338f2a4ea0a487a3c88820ee15`，未应用生产。原OCR不等才触发精确复制兜底；清理按真实内容锚点、唯一同行右邻+及原窗口几何保护点击搜索专属点，回读empty，拒绝未知布局而不使用Delete。CR确认一项P2：系统EmptyClipboard后Set失败有原内容风险，但仅诊断reason可见、调用方给出普通query不匹配错误，需明确用户可见恢复失败；交唯一修复者在新副本修复，原proposal3保留供独测。不得先标通过或把OS记录桩测试当真实剪贴板验证。群聊监控持续运行在原freeze2。

proposal4已通过独测及CR后精确应用6文件，聚合SHA256为`51b5152d0f665c48d7194ea2634c4ed800e7edbde9f98e4c128f811ff51e0dc6`。P2已闭环：原生同锁单次原文补救，WRITE/CLOSE/恢复读取/owner关闭异常均否决匹配，调用方明确告知剪贴板可能未恢复。独测9个真实C#算法故障注入、26个实际PS格式/复制/搜索Body场景及TS入口2套件通过，无失败跳过；原20个未改变的发送边界结果复用，不累加重叠。C#的OS边界为managed替代，未冒称真实OS竞争验证。应用前后六hash、Runner24冻结hash与HEAD/MERGE_HEAD不变，主控CLI build通过。

完整search真机验收仍失败：原OCR裁切后复制helper被search_layout_changed保护提前中止（无复制、无消息发送），随后主控只读重读同布局为有效，尚需区分OCR波动与PS5执行上下文。未放宽保护或标完整通过。主控点击已观察搜索×后确认empty，监听按原cursor恢复，pending为0；唯一修复者继续只读诊断，生产proposal4保持冻结。

## 真机剪贴板发布与恢复缺陷（2026-10-23）

proposal5已通过窄修独测/CR后精确应用，聚合`a19be208eff8da4902e72db3bd4a2846c1612a0ee4f9ba5ba2b06cfbd5e8a66b`；独立11个实际producer/搜索流程、26个相邻安全场景通过，同失败帧在PS5.1/7重放旧版拒绝、新版通过。真实search已越过布局门，但在clipboard_changed处中止；生产Clear函数真实点击清搜索后回读empty，原游标监听恢复。此时仍不能记完整search通过。

主控只读检查发现剪贴板留下本次校验marker，已明确告知用户恢复逻辑有遗漏。系统只读probe排除当前格式、原文/终止符、内存长度不符；后续受控probe真实证明：native CAS成功写入后，锁内返回sequence为296，Close后实际为299，内容与本次nonce、owner均一致；用关闭后的稳定seq恢复成功（第二次301→304），仅恢复probe开始时的旧marker，不声称找回更早未保留的原剪贴板。原managed故障模型只在Set时递增序号，遗漏Close发布行为，原CR也漏此恢复边界；本轮不可复用为缺陷已闭环证据。

后续最小修复只调整PS编排：锁内write stamp不能当发布凭证；写完后稳定seq前后、全文nonce和本次hidden HWND共同确认归属，finally也可在未取得receipt时按同样证据回收自己的nonce，再交原CAS原子恢复。保护恢复阶段不覆盖后到外部写，不声称用户并行CtrlC完全不受影响。不得为硬杀进程/系统故障将用户剪贴板原文落磁盘；硬中断不保证finally，该更早原内容未保留则不能猜测补回。修复仍在Temp副本准备，生产proposal5保持，监控继续，未提交部署。

## 用户结束实验（2026-10-23）

用户明确要求先在群内说明实验暂停、期待后续正式上线，再停止跟踪。主控按原CLI串行排队一次结束招呼及stop，发送success/applied、local-record精确核验，随后立即退出；没有再轮询或补发，进程exit0。最终脚本累计590次成功轮询、8条确认发送（1开场+6对话回复+1结束招呼），ignored0、pending0、blockedfalse；纯群友闲聊忽略分支未遇真实样本，不能记实测覆盖。原unknown对话回复经只读记录核验收敛且未重发。状态与日志保留本机Temp，没有创建额外自动唤醒任务；不重启监控，后续继续需用户另行指示。

搜索修复proposal6已在Temp冻结，聚合`f3e9d184405221a8db10bed391bd229d9b729ed02b56f10545841fffeb1aea4a`，仅PS发布凭证和恢复编排及对应回归改变，Native不变。正式CR通过无未解决P0/P1/P2；独测已完成新增恢复16场景绿、旧proposal5对应Close发布回归红，以及实际Native dirty-Close模型1场景通过，独测已按结束实验要求收尾，最终六hash一致，无测试占用；未追加完整相邻回归，旧9/26不作为本次新恢复缺陷闭环证据。当前工作树仍proposal5，proposal6尚未应用或真机验收，完整search问题未称解决；结束跟踪后不再自行运行新的UI搜索实验。此前遗留marker对应更早剪贴板原文未保存，无法声称找回；后续受控probe只还原probe起始值。

索引保留部分完成，实验阶段已结束而完整CLI搜索修复尚未验收。没有提交、推送或部署。

## 本次提交核验（2026-10-23）

用户本轮明确授权提交当前检查点，替代前文历史阶段的禁止提交约束；部署仍未授权，群聊监听保持停止。本次代码保留proposal5，proposal6仅以待应用补丁和W1验收项入库，不新增真实UI实验。

提交前已fetch并确认当前分支upstream为origin/feature/ask-user-question-card。独立范围核对通过：既有暂存447项等于先前合并基线，新增改动均归入Runner/WeCom/agent2及关联文档；不把仅三处peer标记变化的CLI package-lock生成噪声纳入提交。Runner最终24文件hash逐项与freeze4一致，复用计划中的既有独测/CR证据。

主控本次检查：CLI build通过；定向Node/PS/群查询测试11通过、0失败、0跳过（无真实UI发送）；Web build及产物检查通过；62个变更Python文件语法编译通过；agent2脚本bash语法检查通过；待应用补丁git apply --check通过。没有为此次提交重启测试数据库或真实服务，不声称重新运行全部历史测试或完成正式集成验收。

暂存后范围核验：相对既有合并基线的123条新增/更新路径与本任务明确清单完全一致。默认git diff --cached --check唯一提示是从master原样合入的济南部署文档EOF空行，未顺手修改；本任务范围无新增空白错误。仅CLI package-lock三处peer生成噪声留在工作区，未暂存。
