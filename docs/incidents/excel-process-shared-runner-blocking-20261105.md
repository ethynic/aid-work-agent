# Excel 工具假异步冻结共享 Runner 事件循环（2026-11-05 TPG80509）

## 排查结论

生产 243 的单 worker（`aid-runner-worker`）在执行宏陶陶瓷租户一名企微客服客户的「给我TPG80509的VR设计图」请求时，模型调用 `excel_process` 读取知识库文件 `kb_5edd6ced8830.xlsx`（宏盟荟产品VR图表格.xlsx，4 个 Sheet 的 used range 全部膨胀到约 104.8 万行）。`async def` handler 直接调用 openpyxl 同步逐格遍历（假异步），冻结事件循环约 50 分钟，14:12 起 5 个租户的全部智能体无响应；人工重启容器后恢复。

本次报告基于 2026-11-06 对生产库（`aid_work_agent` / `aid_work_logs`）与日志的只读复核，补充确认了触发方（租户/用户/文件）与受影响执行面。根因分析与代码修复见前一轮调查结论（本文件第 4 节），修复状态以 `docs/ideas.md` 对应条目为准。

## 触发方明细（本轮补充核验）

| 项目 | 值 |
|------|------|
| 租户 | `tenant_29304e2196e8`（宏陶陶瓷） |
| 用户 | `user_ba5589de3687`，昵称「宏陶瓷砖山西区-杨春进13827788792」，来源 wecom_kf（企微客服外部客户 `wmMsuPRQAAwh6UNsHiuccbpleAynR0dQ`） |
| 会话 | `tenant_29304e2196e8_wecom_kf_wkMsuPRQAAWNdUgexIMQleVx70sfA-0g_wmMsuPRQAAwh6UNsHiuccbpleAynR0dQ_sales-assistant` |
| 智能体 | `sales-assistant`（企微客服渠道） |
| 用户消息 | 「给我TPG80509的VR设计图」（TPG80509 为产品型号，华清凝·冰灰 800×800mm） |
| Runner 执行 | `runner_0bc6233d77a24357a7a7ae3b276fedee`，14:12:38 受理，attempt 1 冻结；重启恢复后 attempt 2 于 15:05:52 记为 cancelled |
| Excel 文件 | `storage/tenants/29304e2196e8/knowledge/kb_5edd6ced8830.xlsx`，documents 表 id=3682，title=`宏盟荟产品VR图表格.xlsx`，source_type=`k_25efc88fd802`，2026-09-23 上传 |
| 冻结的工具调用 | `excel_process(file_paths=[kb_5edd6ced8830.xlsx], instruction="查找包含 TPG80509 或 80509 的所有行…")`，checkpoint 中留有完整调用参数 |

## 核验范围和证据

- 生产容器：`aid-agent-api`、`aid-runner-worker`（注：worker 容器 11-05 16:34 被重建，其 stdout 中 14:12 窗口的日志已丢失，时间线以数据库记录为准）。
- 业务库 `aid_work_agent`（`agent_runners` / `channel_messages` / `users` / `tenants` / `documents`）；追踪库 `aid_work_logs`（`obs_traces` / `obs_spans`）。
- 时间均为北京时间（2026-11-05）。

| 时间 | 已核验事实 |
|------|------------|
| 14:08:14–14:12:22 | 宏陶租户 wecom_kf sales-assistant 连续 12 个 runner 执行正常完成（obs_traces / obs_spans 均有完整记录，含多次 knowledge_base_search） |
| 14:12:22.29 | 最后一个正常 llm_call span（tr_runner_c375735006424fd7a0e61cb41e50ed26）结束，该 trace 14:12:22.6 正常收尾 |
| 14:12:22 之后 | `obs_spans` 全天再无任何新增（14:12:20–14:40 查询 0 行）——worker 事件循环冻结后 span 无法落库 |
| 14:12:38.886 | `runner_0bc6233d77a24357a7a7ae3b276fedee` 受理（用户消息「给我TPG80509的VR设计图」）；checkpoint 显示 kb_search（query="TPG80509 产品 规格 工艺"）已完成后，模型发起 `excel_process` read `kb_5edd6ced8830.xlsx` |
| 14:12–15:02 | worker 冻结约 50 分钟，所有智能体无响应；该执行无 obs_traces 行（trace 行与 span 均在终结时落库，冻结中的执行不落盘） |
| 15:03–15:05 | 人工重启容器恢复；冻结窗口内受理的排队执行陆续处理，冻结执行 attempt 2 于 15:05:52 记为 cancelled |
| 15:06 后 | 恢复正常：15:06 起新 trace/span 正常落库；同日 17:41 主日志出现该 Excel 的正常下载记录 |

时间线证据等级说明：「attempt 1 冻结于 excel_process read」由 checkpoint 中留存的工具调用参数、14:12:22 后 span 断流、以及前一轮调查（含膨胀探测确认 4 个 Sheet used range 约 104.8 万行）共同支撑；无 worker stdout 直接栈证据（容器重建丢失），不能声称保留了冻结时的现场栈。

## 受影响面（本轮补充核验）

冻结窗口（14:12:22–15:06）内 `agent_runners` 共受理 13 个执行，跨 5 个租户、12 个用户（含宏陶陶瓷等多个租户的 web 与渠道会话），全部积压至重启后处理：11 个 completed、1 个 cancelled（即冻结执行 attempt 2）、1 个 waiting。排队执行的最长积压约 50 分钟（14:12:38 受理、重启后才处理）。

冻结期间渠道侧用户表现为长时间无回复；未发现重启后遗留的异常终态。

## 根因层次（沿用前一轮调查结论）

1. **直接根因**：`excel_process_tool.py` 的 `async def` handler 直接调用 openpyxl 重 CPU 同步读取（假异步），违反 [backend_dev.md](../../.claude/rules/backend_dev.md) 假异步规范，阻塞 worker 事件循环。
2. **结构根因**：`src/tools/excel/excel_reader.py` 与 `src/tools/file/excel_reader.py` 双实现并存。10-15 read 工具已修过同类假异步（a99a08e1，见 [wecom-kf-excel-read-runner-interrupted-20261015.md](wecom-kf-excel-read-runner-interrupted-20261015.md)），excel_process 漏修。
3. **深层根因**：worker 的全部防御机制（租约续期、心跳、并发控制、超时回收）都依赖「事件循环不被阻塞」这一隐含前提。一处假异步即可击穿全部防线，且无看门狗兜底，唯一破局手段是人工重启。

## 修复状态

- **P0 修复（已完成开发，待部署）**：commit `2f15048e` 已推送。excel_process 8 个 handler 全量 `asyncio.to_thread` 包裹；Excel 解析归一到 `file/excel_reader.py`，新增膨胀探测 `_is_inflated_workbook` + read_only 流式路径 `_read_document_stream`；行为变化为 excel_process read 的无效 sheet_name 改为报错（不再静默回退）。270 个相关测试通过。登记见 [ideas.md](../ideas.md) 20261105-1540 条目。
- **P1 看门狗（待用户裁决）**：仅完成设计稿 [runner-worker-watchdog-design.md](../system/runner-worker-watchdog-design.md)，含事件循环阻塞看门狗线程、队列水位巡检、独立飞书告警通道三个方案；登记见 [ideas.md](../ideas.md) 20261105-1550 条目，实现方式待用户裁决，不得擅自实施。

## 经验教训

1. 同一问题在双实现并存时只修了一处（read 修了、excel_process 漏修）——同类同步 IO 入口修复必须全量排查，不能只修出事的那一个。
2. span / trace 均在终结时落库，冻结中的执行在可观测性上是盲区；队列积压只能从 `agent_runners` 受理行侧观察（这正是看门狗设计中「不能依赖 worker 自身报告冻结」的依据）。
3. 容器重建会丢失 worker stdout，关键故障窗口的现场证据随之丢失；重大事故排查前应先保存 `docker logs` 再重启容器。

## 关联文档

- [微信客服读取异常宽表导致 Runner 中断（2026-10-15）](wecom-kf-excel-read-runner-interrupted-20261015.md) —— 同类前案（read 工具、222 秒阻塞）
- [Runner Worker 看门狗与队列堆积告警设计](../system/runner-worker-watchdog-design.md) —— P1 防线设计稿
- [ideas.md](../ideas.md) 20261105-1540 / 20261105-1550 条目 —— 修复与看门狗登记
