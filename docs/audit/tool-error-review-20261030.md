# 双环境 tool_error 收集与处理清单（最近 5 天，2026-10-25 至 10-30）

> 收集时间：2026-10-30。范围：生产（243 `aid_work_logs`）、测试（254 `aid_work_logs2`）。
> 来源：追踪库 `obs_spans`（`span_type='span' AND status='failed'`）+ `obs_traces` 关联终态。日志库含 `tool_error` 标签的 trace 即由这些失败 span 标记（`trace_collector.py`）。
> 说明：254 `aid_work_logs3`（agent3）与 logs2 失败数据完全相同（test2dev 单向同步所致），不重复计数。
> 上期清单：[log-error-review-20261026.md](log-error-review-20261026.md)（主日志 ERROR/WARNING 维度，本表是工具失败维度）。

## 1. 总量概览

| 环境 | 失败 span | 涉及 trace | trace 终态分布 |
|------|----------|-----------|---------------|
| 生产 243 | 93 | 34 | 32 completed（自愈）/ 1 failed / 1 finalizing |
| 测试 254（logs2） | ~114（其中 skill_execute 77） | 40 | 35 completed / 3 failed / 2 cancelled |

**最终失败 trace（4 个，全部同一根因：撞执行上限 ITERATION_LIMIT）**：

| trace | 环境 | 数字员工 | tokens | 积分 | 轮次 |
|-------|------|---------|--------|------|------|
| tr_runner_10cce9cd | 生产 | building-supply-chain | 635 万 | 78.83 | 60 |
| tr_runner_fa9f94ae | 测试 | ai-lead-gen | 562 万 | 64.52 | 50 |
| tr_runner_5d8e000a | 测试 | ai-lead-gen | 100 万 | 25.46 | 20 |
| analysis_f626be48 | 测试 | （数据分析） | 11.6 万 | 0 | 9 |

另有 1 个 finalizing（recruiting-operator，EXECUTION_INTERRUPTED，10-29）对应已知的 finalizing 孤儿问题（修复 5b8504aa 待部署）。

## 2. 忽略项（偶发/可自愈/不影响最终结果）

| 失败源 | 数量（生/测） | 判定理由 |
|--------|-------------|---------|
| 数据分析内部步骤 `calculate` / `query` / `aggregate` / `describe` / `to_chart` / `to_table` / `load_output` | 55 / 24 | LLM 写代码迭代重试的固有形态（列名猜错、语法错、类型错），失败后重试成功；所属 trace 全部 completed。仅 `load_output` 产物不可复用属正常护栏 |
| boss_* RPA 人机协作恢复型 | 24 / 19 | `NOT_LOGGED_IN`（引导用户登录）、`SCRIPT_NEEDS_FILL`（需用户补充话术）、`DEVICE_UNAVAILABLE`（Runtime 离线引导重连）均为设计内恢复语义；trace 大多 completed |
| boss_* UI_CHANGED 视觉锚点失败 | 6 / 8 | RPA 界面变动自动重试/换锚点，个别需人工；量小不构成模式 |
| excel_process 参数误用（needs_data / 文件不存在 / xlsx→pdf 不支持） | 8 / 0 | LLM 首次调用缺参后被错误信息纠正重试 |
| knowledge_base_search 栏目未授权 | 1 / 0 | 错误信息自带授权栏目清单，LLM 换栏目重试即成 |
| use_skill not allowed（pdf/word） | 2 / 0 | 白名单语义正确拦截，仅 2 次 |
| skill 指南版本不匹配 | 0 / 1 | 重调 use_skill 即恢复 |
| domain_check / 邮件发送类技能业务性失败 | 少量 | 业务数据问题（联系人不存在的 campaign 等），非系统缺陷 |

## 3. 待处理项（反复发生、耗费 token、与最终失败相关）

### T1（P0，已有修复待部署）ITERATION_LIMIT 最终失败烧 token

4 个最终失败 trace 全部以「本轮已达到执行上限」结束，合计 **约 169 积分成本、1300 万+ token**。共同模式：`tool:skill_execute` 反复失败烧轮次直至上限（生产 building-supply-chain 那单还叠加了 LLM 生成代码 TypeError 崩溃）。

根因与修复见 [ITERATION_LIMIT 事故三项修复](../../docs/plans/)（7385c1bb 待部署：cp 批量交付 / 技能串联省轮次 / trace 投影 error_code）。**本期数据进一步确认部署优先级**。T2 是其中被掩盖的放大器。

### T2（P1）skill_execute 脚本文件缺失 -- staging 与路径拼接缺陷

ai-lead-gen 会话中 LLM 执行 `lead_db.py`、`sites_*.txt` 报 `FileNotFoundError`（测试 5+ 次，两个大失败 trace 中贡献 12 次失败烧轮次）。原报告的两个缺陷核实后状态：

1. **绝对路径双重拼接**：已由 5a329a49（2026-10-27）修复，agent2（254）容器已部署；**生产 243 容器尚未部署**。
2. **依赖文件未 staging**：深入排查（trace tr_runner_2eb58fcd 实证）残余缺陷为 **workdir 参数被承诺但静默忽略**——`SkillExecuteInput.workdir` 在 tool 层读取后从未传给 executor，LLM 用 write 工具把数据文件写入租户 conversation 目录、再传 `workdir=<conversation>` + 相对文件名时，实际在一次性临时目录执行报 FileNotFoundError，改绝对路径自愈白烧一轮。多步技能（web-search-pro batch 等）反复触发，是 ITERATION_LIMIT 大失败 trace 的放大器。

修复（2026-10-30，待部署）：
- tool 层 `_validate_workdir`（fail-closed）：仅放行当前租户存储目录（`is_tenant_owned_file`）与 `storage/tmp`，无租户上下文仅放行 tmp；拒绝时返回引导文案不执行。
- executor `execute_skill_command` 新增 `workdir` 参数：提供时在该目录执行且**不 cleanup**（绝不 rmtree 租户目录），支持多步技能跨调用复用文件；缺省行为不变。
- 顺手收口 `add_file` files 键名逃逸（拒绝绝对路径与 `..` 段，CodeReview P2 建议）。
- 单测 43 绿（`tests/unit/test_skill_executor_path.py` 含 symlink/逃逸/跨调用复用边界）+ 独立测试与独立 CodeReview 通过（CodeReview 另有 3 项 P2 观察项：cwd 依赖 `configured_storage_root` 未来分叉、设备路由分支静默忽略 workdir、workdir 指向文件时文案不友好）。

### T3（P1）web-search-pro 通道无结果/风控

skill_execute 中 15+ 次失败为搜索引擎通道全灭：`baidu 命中风控验证码`、`so/baidu_m/sogou 第4次结果不相关`。与已知结论一致（百度风控为行为级动态；151 tinyproxy 代理代码待提交部署，254 .env 须配代理）。

**2026-10-30 复查（已收敛）**：151 代理链路全通（151 tinyproxy / 反向隧道 254:127.0.0.1:18080 / socat forwarder 192.168.48.1:18080 / agent2、agent3 容器 env 均已配 `WEBSEARCH_BAIDU_PROXY`），容器内实测 baidu 通道返回真实结果无验证码。数据佐证：agent2 容器 10-30 14:45 带 env 重启后，baidu 风控失败不再出现（10-28~10-30 白天的失败均发生在容器 env 注入前，走 254 直连被标 IP）。**剩余为不同性质失败**：so/bing「第4次结果不相关（疑似分词漂移）」是 SERP 相关性清洗门槛对泛查询（如「有限公司 联系我们 运营总监」）严格，非风控，观察项不改动。

### T4（P2）http_api 环境变量未配置即调用

`client_token` 占位符解析失败（生产 2 次/1 trace）。错误信息已指引用户去管理后台配置，但 LLM 在未配置状态下可能反复尝试。建议：会话首次报此错后向用户明确说明一次即可，避免 LLM 循环重试（观察项，量小暂不改动）。

### T5（观察）测试环境自愈型失败的 token 代价

测试环境 35 个 completed trace（含失败 span）平均 126 万 token / 合计 586 积分，其中数据分析迭代失败是主要烧点。功能上自愈、用户无感，但单会话成本高；待 T1 部署与上下文压缩可观测性改造（自适应预算）落地后一并复查经济性。

## 4. 处理进度

- [ ] T1 部署 7385c1bb 并验证 ITERATION_LIMIT 收敛
- [ ] T2 代码已修（workdir 生效 + 绝对路径拼接 5a329a49）；待生产 243 部署 5a329a49 + 本次修复，并用 ai-lead-gen 真实会话回归
- [x] T3 已复查（2026-10-30）：baidu 风控经 151 代理已收敛（容器 env 注入后零复发）；so/bing 分词漂移类为清洗门槛观察项
- [ ] T4 观察不改动
- [ ] T5 T1/上下文压缩落地后复查单会话成本
