# BOSS 部分筛选项不可读时继续执行

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 正式服对话取证与方案调整 | ✅ 完成（2026-10-15） | SSH只读确认15:02—15:08探查因部分行不可读反复UI_CHANGED；工具未报告具体数量 |
| Phase 2 | Provider部分探查、云端引用校验及招聘指令 | ✅ 完成（2026-10-15） | 部分行/坏年龄隔离；同设备引用允许已确认子集；普通条件带引用也校验实际生效 |
| Phase 3 | 独立测试与CodeReview | ✅ 完成（2026-10-15） | 云端111/111、CLI筛选70/70、入口10例及端云消费6例通过，CR无阻断；全套5个无关失败，未部署 |
| Phase 4 | 提交与新版Runtime/BOSS交付 | 🔧 进行中 | 用户授权先提交推送再打包，UI0.0.12/BOSS0.3.10，交付名称不含trial |

## 问题与行为

正式服最近招聘任务要求筛选2份简历、人工审核后再决定打招呼。`boss_filter_options`连续返回“部分筛选行未完整解析出控件，拒绝报告完整面板”，未返回可用行，因此普通经验/学历/薪资也无法继续。智能体自行推断“两行”、弹窗或加载问题并反复要求用户刷新；回执没有支持这些判断。

用户2026-10-15明确调整规则：页面上匹配不到的条件直接忽略，只执行可匹配条件，继续读取、入库和评分。未用于页面筛选的职位要求仍参与原有评分，不回写职位库。跳过情况简要告知，不要求用户批准忽略或手动设筛选，也不编造行名、数量或原因。全部条件不可匹配时不调用空参数筛选或清除原条件，按当前页面结果继续并如实说明。

## 实现边界与共同检查点

已读取Runner/Desktop/Runtime共同契约v2.0。只修改BOSS Provider探查和云端消费，不修改Runtime core、Device API、binding、授权或wire。Provider探查沿用filter_schema_version=3和原有probe_complete含义，增加可选unreadable_rows（label/reason）描述已发现但无法可靠解析的行；部分可用时success=true、probe_complete=false，零可用仍明确失败。旧云端拒绝部分结果而不误执行；旧完整结果保持兼容。Provider源码唯一写入者provider_fix，云端、提示词、文档唯一写入者主控。

云端仅对同租户、用户、会话、设备、十分钟内的成功探查放行。部分探查只允许已确认的行、控件类型及精确选项；缺失行、范围伪装、未验证年龄或附属第一学历不放行。原工具仍验证具体请求的实际控件、选中状态和结果，未改变EXECUTION_UNKNOWN不得重试、VIP权益或补充推荐来源边界。原“完整探查才可继续”和“不能漏掉条件”规范在本任务范围被用户明确替代。

按跨端消费行为变更执行高风险验证：开发与自测、独立测试、独立CR。测试使用脱敏真实面板fixture和隔离云端mock，不连接生产数据库写入、不触达候选人。仅在master既有工作目录修改，保留其他任务改动；未授权提交或部署。

## 验证与交付

验收目标：同一面板存在不可读行时，已识别普通条件可完成探查与设置；部分探查引用可派发已识别动态条件；未确认的条件不会被点击；招聘提示允许跳过并继续，不虚报全部满足。

- 主控初轮：Git Bash统一`dev_test.sh`，项目已有venv，数据库/Redis指向不可达本机隔离地址；dynamic_filters/vip_entitlements/filter_result_boundary共64通过、0失败/跳过（4条既有Pydantic警告）。普通条件带引用的终态缺口由独立验证发现后已修复并补三例回归，最终独立验证覆盖该修复。
- Provider开发自测：`npm run typecheck`及筛选相关四文件70项通过、0失败/跳过；全套`npm test`为555/560通过，5项失败分别为未修改的MCP清单预期缺两个工具、三个greet fixture缺CSS几何，不计为全绿。日志保留客户端忽略文件`.test-filter-result.log`、`.test-provider-result.log`。
- 主控最终Provider源码状态：`npm run typecheck`和`npm run build:main`均exit0，`git diff --check`通过。未修改包版本；工作目录内其他会话的版本变更不属于本任务。
- 独立测试最终：`scripts/dev_test.sh tests/unit/local_tools/test_boss_dynamic_filters.py tests/unit/local_tools/test_boss_vip_entitlements.py tests/unit/local_tools/test_boss_filter_result_boundary.py tests/unit/local_tools/test_proxy_tool.py tests/unit/local_tools/test_boss_tool_billing.py -p no:cacheprovider -q`：111通过、0失败/跳过；`npm run build:main`通过，`node --test dist/tests/filter*.test.js`：70通过、0失败/跳过。
- 独立实际入口10例通过：真实`BossFilterTool.execute`及`local_operation_scope`分别走legacy/durable，mock外层设备选择、存储/派发/轮询；正确部分引用保留原始意图，未知项/错租户/未来时间/错误控件在创建前拒绝。未运行需要真实PostgreSQL的Runner集成，不声称真实存储验收。
- 独立端云消费6例通过：真实`createBossFilterOptionsOperation`读取脱敏fixture，返回12可读行、性别不可读及`probe_complete=false`，原样交给Python dispatcher；经验/薪资/求职状态/年龄可派发，性别不可派发。真实`createBossFilterOperation`普通薪资回执被云端接受，删除`applied_rows`后转`EXECUTION_UNKNOWN`。临时验证文件已清理。
- 独立CodeReview通过，无本轮引入的P0/P1/P2可行动问题；复核逐行结构隔离、年龄诊断保留、请求定位/选中校验、引用身份/时效与终态证据。索引按开发完成归档，未部署验收。
- 未调用真实招聘页面、模型或候选人；SSH仅只读查看正式服日志和表结构，未更改服务、配置或业务数据，临时密钥副本已清理。正式聊天端到端恢复需后续更新云端代码及客户设备BOSS Provider，本次没有提交、推送、打包发行或部署。

## 2026-10-15 提交与打包授权

用户随后明确要求先提交代码，再打包新版Runtime与BOSS插件，版本不要trial。先提交本修复和必要版本元数据（UI0.0.12、BOSS0.3.10），不纳入其他会话的Runner取消隔离改动；fetch核对origin/master后推送当前master。沿用现有构建与验包安全门；仅去除本次交付文件名的trial标记，不将未签Windows证书的包冒称生产签名发行。Runtime Host/Core无变更，使用当前已提交版本，交付记录同步[组合包计划](../plan-runtime-ui-boss-trial-bundle.md)。本授权不含服务器部署或在用户电脑安装新包。
