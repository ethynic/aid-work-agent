# BOSS 筛选结果与补充推荐边界

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 真机空结果与推荐边界取证 | ✅ 完成（2026-10-13） | CLI Gateway只读截图/DOM确认空态与推荐卡片同属card-list，按钮扫描会混入推荐 |
| Phase 2 | Provider候选人来源门禁与云端停止语义 | ✅ 完成（2026-10-13） | Provider0.3.4源码冻结；批量/续筛/详情/打招呼来源门禁，云端零匹配停止与矛盾回执拒绝 |
| Phase 3 | 独立测试、CR与真机只读验收 | ✅ 完成（2026-10-13） | 云端54及206入口场景、CLI495覆盖与受影响40复验、额外20场景通过；CR通过，真机零输入；用户已授权提交，未部署 |

## 实测与范围

当前推荐牛人页筛选18项，显示“暂无符合牛人，更换筛选范围试试”，下方分隔文字为“暂无符合牛人，为你推荐”，推荐卡片仍带“打招呼”按钮。实测结构：推荐iframe的 `.list-wrap.card-list-wrap > .list-body > .card-list` 首个 `.card-item > .mome-main-ui` 包含 `.tip-inner/.gray` 空态及 `.recommend-mome-ui` 推荐边界，其后同级卡片属于补充推荐。现有按钮扫描没有来源门禁，可能读取、评分或触达不满足条件的人。

本任务修复范围为Provider卡片来源识别、读取/续筛/打开详情/列表打招呼门禁及云端结果解释；不自动放宽筛选，不读取下方推荐，不部署。已读共同集成契约v2.0；复用现有operation终态及data.stop_reason，不修改Runtime core、Device wire或授权。高风险流程：开发、独立测试与独立CR，重点验证错误触达与不必要识别费用。

Provider修复版本0.3.3→0.3.4，输入schema不变。单份详情成功仍返回原简历payload；可靠证据证明详情为补充推荐时返回NO_MATCHING_CANDIDATES/RECOMMENDATIONS_BOUNDARY失败终态，云端直接透传且不进入识别。批量/续筛复用stop_reason正常空结果语义。

## 实施与兼容

- 以实际列表的空态和推荐分隔结构确认边界；排除隐藏iframe、隐藏结构及候选人正文同文案，不能仅全文搜词或按屏幕y划分。滚动后标记离开视口但仍在列表DOM时仍有效。
- 匹配卡片和补充推荐使用共同识别逻辑，点击前新快照重测。无匹配时任何推荐卡片都不可打开、打招呼；正常匹配后推荐分隔以下卡片同样不可处理。
- `stop_reason=no_matching_candidates` 表示本轮筛选明确无匹配；`recommendations_boundary` 表示匹配流已到补充推荐边界。批量/续筛可正常返回零份、零尝试并关闭continuation；云端不将其报为无效简历、不识别、不入库、不按份扣识别费。错误/加载中/单纯视口无卡片继续保留原失败或无进展语义。
- Agent如实告知当前条件无匹配或无更多匹配，需要用户改变条件后才能继续；不得把推荐当匹配、擅自放宽或重复续筛。

## 文件职责与验证

Provider源码及测试由boss_filter_fix负责；云端proxy/domain_flow/resume_continuation、招聘提示词及测试由主控负责；独立测试与CR使用既有独立智能体。真机原始截图/快照仅本地临时取证，不提交真实候选人信息，测试fixture使用合成姓名。

验证覆盖：无结果但推荐存在、正常结果后推荐、隐藏/后台诱饵、同文案正文、滚动后边界、点击前状态变化、旧正常页面、batch/next/OpenDetail/Greet路径，以及legacy/durable云端停止不识别不计费。真机验收只运行只读识别，当前条件保持不变。

## 验证记录

- 缺陷基线：真机当前页明确空态，旧compiled `ResumeBatchReader.locateCards` 却返回3张可见推荐卡片。
- 新Provider真机只读：通过CLI Gateway fresh canonical快照调用实际边界探针、卡片定位、批量/scanMore读取和列表打招呼执行器；所有输入方法替换为调用即失败的禁止输入依赖。可处理卡片0，批量/续筛简历0、尝试0，打招呼0，统一no_matching_candidates；禁止输入调用0，探针约7.7ms。未改变筛选，未打开详情，未发送。
- 云端主控：隔离数据库/Redis环境，新行为与可信续筛最终40项通过。独立最终54项（boundary/continuation/VL）通过，额外206个legacy/durable入口场景通过；矛盾回执包括resumes/failures标量，均不识别/计费/入库，真实匹配+推荐边界仍正常处理并关闭续筛。effect非none时保留不确定性，以EXECUTION_UNKNOWN停止，不把无结果描述为已确认事实。
- CLI独立：typecheck/build退出0；全套495项初跑494通过，唯一旧测试期待“batch默认/next仅DOMRects”与新canonical采集不符。主控仅更新该采集断言，保留UI_CHANGED、effectnone、零点击/滚动与session关闭检查；build及受影响operations-result+recommendation-boundary-operations共40项复验通过，产品源码未变，其余494项复用。额外20个结构/入口/fresh重击场景通过，2万额外DOM节点探针16.8ms。
- 审查期间关闭的问题：真实空态tip可见而推荐divider隐藏/缺失仍零匹配；详情归属只使用.name/name-wrap姓名结构，经历同文案不作证据；同名歧义拒绝；点击被吞后重击前重取来源、姓名点与viewport，详情出现后读取前再核来源；先读成功后页面变空态不否定已有结果。
- 独立CR及增量复核通过，无剩余阻断项。原始截图/快照仅本地临时取证后移除，fixture只保留组件/几何与虚构姓名。正常结果后分隔场景使用合成匹配卡片验证，不能声称该场景已真实页面验收。
- 本轮开发完成；用户2026-10-13已明确授权提交，按仓库规范推送至已确认的origin/master；未部署。Agent2与已安装旧Provider尚未因此更新，新门禁需后续发布才能生效。
