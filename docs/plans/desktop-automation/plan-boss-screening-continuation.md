# BOSS 简历数量选择与连续筛选

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 续筛契约、CLI 翻页与排除 | ✅ 完成（2026-10-11） | 独立新工具，最多30屏，无进展停止，CLI定向101项通过 |
| Phase 2 | 云端可信引用与重复派发防护 | ✅ 完成（2026-10-11） | 可信历史、设备固定、同事务去重，恢复复用 |
| Phase 3 | 能力及 schema 驱动的编排，移除场景提示词 | ✅ 完成（2026-10-11） | Python72、CLI105通过；独立补验及3项P2复核通过 |
| 验证 | 自测、独立测试与CodeReview | ✅ 完成（2026-10-11） | 后端110、CLI定向101、core33通过；独立PG14、CR通过 |
| 真机验收 | Windows真实推荐页滚动与鼠标 | 📋 待开发 | 本次未执行提交/部署，待用户指定真机验收环境 |

## 关联遗留项（2026-10-11 自已注销计划转入）

以下三项随 plan-boss-cli-vip-filter-perf.md、plan-boss-detail-greet.md 两份过程计划注销（功能均已交付）转入本计划，随 Windows 真机验收一并核验或加固：

1. VIP 账号筛选面板滚动验证：非 VIP 账号已真机验证（严格 clip 边界判定 + 最多 3 次滚动）；VIP 账号弹窗滚动适配待有 VIP 账号时验证。
2. open-detail 姓名校验竞态：canvas 先现、头部姓名晚一拍，6 次中 3 次误报 WRONG_PAGE，应改为轮询重试。
3. goto URL 直跳留 SPA 半水化态：点击失灵，同 URL 重新 Page.navigate 可治愈，待加固。

## 用户行为

用户需求和上下文决定职位、要求及总数量，工具默认值不代替业务目标。3是MCP单批容量，不是任务总量。agent通过工具schema发现页面职位切换、完整筛选替换、首批读取、连续排除读取等能力并组合调用；不在运行时提示词枚举自然语言场景或固定调用流程。缺项由通用澄清原则处理。

成功入库数为目标，失败如实报告；列表无进展/搜索上限明确停止。续筛排除已处理者（包含失败尝试），不能反复识别收费。新一轮与明确要求重评不同，不自动扩大打招呼授权。

## 业务契约v1与兼容

当前BOSS主控为本批唯一写入者。新增`boss_resume_next`独立工具名，防止旧Provider静默忽略排除参数后重复读取。Runner/Device/Host管理wire、binding、许可与跨调用资源占用均不改。

- 模型入参：`continue_from`（上一批invocation UUID，必填）、`limit`（1～3，默认3）。
- 云端从原invocation读取累计排除；校验tenant/user/session/device、工具名、成功终态。派发参数在原domain phase固化，恢复复用，不新增数据库表。
- Provider入参：`exclude_names`（1～999项，累计到1000停止）、`expected_job_name`（必填）、`limit`。MCP最多3；CLI最多10。
- CLI：`resume-next --exclude-names 甲,乙 --job 职位 --limit 3`。推荐页URL/职位检查后跳过排除者，当前屏处理完再滚列表，最多30屏；不确定是否到底时明确搜索边界，不宣称读完全部。
- batch新增输出`processed_names/stop_reason/job_name`；云端保留`continuation_id/stop_reason`，包括失败及空批结果。原batch参数兼容，旧客户机明确拒绝新续筛工具，不降级；缺少新停止原因或职位元数据的旧批次结果不可直接续筛。
- 按姓名排除是现有DOM身份的保守限制，同名候选人可能被跳过，不承诺稳定候选人ID。匿名失败行只在当前屏去重。
- 不在本批修复无关详情页Runtime注册与评分覆盖差异。

## 验证

连续批次不重复、重叠卡片/失败者排除、滚动加载、搜索上限、空批、取消/关闭失败停止；跨租户/用户/会话/设备引用拒绝，旧Provider拒绝，恢复不重复识别/扣费。CLI/MCP/schema/云端注册一致性及原batch回归；高风险独立测试与CR。

## 实施补充

- 父invocation在创建下一批的同一事务中 FOR UPDATE；已经存在后续批次时拒绝过期引用，保留 Runner 自有 dedupe。明确读前失败的code白名单、effect=none且无读取数据（仅run_id允许）才允许原引用再用；未知结果必须阻断。
- 共用 ProviderManager 仅对新续筛工具查询并缓存 listTools 的必需参数能力；不支持则返回 TOOL_NOT_ALLOWED/effect=none，不实际调用，重启清缓存。主控独占该文件及门禁测试，未修改管理接口。
- 招聘智能体 max_iterations=100；50份至少17批需留准备轮数。实际单轮容量受工具调用预算约束，不通过场景提示词规定200份硬目标；累计姓名达到1000即停止续筛。
- 页面职位变化有硬校验；实际筛选档位目前不做DOM指纹绑定。连续筛选依赖用户未在执行间手动更改页面条件，其他操作/人工改条件后需明确新一轮。不同条件或职位的新轮次可能与历史名单重叠，未实现跨轮稳定候选人身份去重。

## 20份示例与命令对应

用户选择职位2、20份 → 读取云端职位要求 → 推荐页/页面精确职位切换 → 校准筛选档位并设置。模型首次调用 `boss_resume_batch(limit=3)`；之后每次用上一批 `data.continuation_id` 调用 `boss_resume_next`。全成功时共7批：3+3+3+3+3+3+2；不是反复调7次batch。失败者排除，按实际成功数补目标，停止原因优先于凑数。

模型通过本机Runtime调用MCP工具（常驻 `boss-cli mcp --stdio`），不需要每批另启动shell CLI。人工CLI等价入口为 `boss-cli resume-batch --limit 3`，续筛为 `boss-cli resume-next --exclude-names "甲先生,乙先生,丙先生" --job "PHP开发" --limit 3`；人工调用需自行维护累计排除名单，云端调用由可信历史生成。

任务数量、条件调整及是否新建一轮由上下文决定，不以用户措辞匹配硬编码分支。`boss_filter`实际为完整替换：先清空再设置，省略维度也被清空；需要保留其他要求时，agent组合完整参数。schema已同步这一事实，避免误把它当局部更新。当前不同轮次不做全局身份去重。

## 最终验证记录（2026-10-11）

- 主控：BOSS CLI和共用Runtime的typecheck/build通过；CLI定向101项通过；后端相关110项通过；共用core最终33项通过。日志分别在 `/private/tmp/boss-screening-cli-targeted.log`、`boss-screening-python-tests-final.log`、`boss-screening-core-tests-final.log`。
- CLI全量实际429项：422通过、7失败。失败均由未修改的 `WinMouseClicker.defaultScriptPath` 使用Windows反斜杠在macOS无法找到ps1造成（包括MCP派生失败），未将全量声称为全绿。日志 `/private/tmp/boss-screening-cli-tests-final.log`。未改无关跨平台逻辑。
- 独立测试：host Python64/64、CLI批次/operation/manifest60/60、共用core33/33。真实临时PostgreSQL验证14/14，覆盖并发仅插一条、Runner dedupe重绑、明确失败恢复、未知失败阻断及null/标量数据；探测 `/private/tmp/boss-independent-sql-probe.py`，日志 `boss-independent-sql-probe.log`、`boss-independent-unit-final-test.log`、`boss-independent-cli-final-test.log`、`boss-independent-core-final-test.log`。
- Runner完整集成未执行完成：本机隔离PG缺pgvector扩展，初始化被阻断。独立测试初次自动入口误选Docker，不计验证成绩；精确中断该测试，脚本finally清理随机库路径执行，父/子测试进程均已退出。后续验证使用明确host解释器和任务PG，本机随机数据库0，任务PG已停止且目录删除。
- 独立CR及增量复核通过。已修复失败cursor占用、SQL NULL误放行、JSON null标量删除、旧CLI未知工具、50份循环上限等问题。遗留实际档位fingerprint及跨轮身份去重边界见实施补充；Windows真实DOM重排/列表滚动待验收。上述开发验证阶段未执行Git提交或部署；后续提交按用户单独授权执行。

## Schema 驱动的能力契约（2026-10-11 增量）

- agent接收的是云端Python工具的description/input_schema，本机MCP另有独立schema；两层均说明limit的单批含义、首批没有历史排除、续批累计排除、页面状态操作及能力边界。
- CLI结果 `data.continuation={available,reason,tool,arguments,processed_count,deduplication,filters_verified}`。可续筛时arguments携带累计exclude_names及expected_job_name；调用者按业务剩余量选择limit。云端结果保留同一能力结构，arguments替换为可信continue_from引用，排除名单不由模型控制。
- `available`仅表示允许继续查找，不保证存在更多候选人。无进展、扫描上限、详情未关闭或姓名累计到1000均不能直接续筛；云端对已停止引用在派发前拒绝。
- 删除新增的数量/继续/改条件/换职位场景分支，并删除旧的一键筛选固定流程；保留通用目标澄清、实际结果汇报和外部写动作授权。没有把这些自然语言分支搬进工具description。

- 增量CR修复：学历为多选；能力标志与派发门禁使用同一判定，旧结果缺上下文不宣告可续筛；CLI/MCP接受1～999项排除，reader累计1000停止，云端同边界。

### 增量最终验证

- 主控最终：Python相关72/72，CLI typecheck/build通过，reader/operation/manifest/CLI参数105/105。日志 `/private/tmp/boss-schema-python-final.log`、`/private/tmp/boss-schema-cli-final.log`。
- 独立初轮：Python65/65、CLI82/82；最终修复补验Python36/36、CLI3/3，跨层5/5，实际MCP zod数量及排除名单边界通过。日志 `/private/tmp/boss-schema-independent-final-python.log`、`boss-schema-independent-final-cli.log`、`boss-schema-independent-final-cross-layer.log`、`boss-schema-independent-final-schema.log`。
- 独立CR发现的学历语义、能力/门禁判定、1000人边界三项P2均修复并复核关闭，无新增P0/P1。复用原SQL/runtime验证，不重复未变化模块；没有调用真实模型或Windows页面，该验证记录形成时未提交、未部署。
