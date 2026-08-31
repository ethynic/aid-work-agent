# 工具 effect / 幂等安全清单

> 版本：v1.0（2026-08-19）
> 盘点基线：2026-08-19，`src/core/agent.py` 4284 行
> 用途：独立的工具副作用、幂等性和盲重试风险审查资料。每个工具均逐一打开源码核实，非按名猜测。
>
> 本清单是独立安全审计资料，不创建业务对象、不自动授权重试；代码变化后必须重新核对。[运行时安全加固设计](../agent-runtime-safety-hardening-design.md)

---

## 1. 分类规则（口径冻结）

| effect | 判定标准 |
|---|---|
| `none` | 纯读，不改变任何外部状态（检索、读文件、只读 API、纯 LLM 生成、仅返回字典） |
| `applied` | 有外部副作用：写文件 / 发请求改远端状态 / 改 DB / 触发 RPA（含本机 UI 写操作） |
| `unknown` | 无法静态确认副作用是否已发生（网络中断后、或副作用取决于运行时行为） |

补充口径：
- 幂等键列「无」= 该工具没有任何显式幂等机制（无 client 请求 ID、无去重键、无 ON CONFLICT）。「近似幂等」指重复调用结果可接受但无键保护。
- `applied(本机UI)` = RPA 改本机桌面应用 UI 状态，无平台外部副作用（工具描述自证「无外部副作用」），重试安全。
- 重试策略为静态判断的**建议**，运行时以实际错误位置为准。

## 2. 注册表枚举与覆盖证明

工具注册共 4 个入口，清单 50 行 100% 覆盖：

| 注册点 | 数量 | 依据 |
|---|---|---|
| `Agent._register_builtin_tools`（agent.py:421-543，主注册表，全部实例无条件注册） | 28 | agent.py:436-517 逐行 register 调用（历史上 27 个，已增至 28） |
| 虚拟工具（构造但不入 tool_registry，agent loop 特殊分支处理，agent.py:519-541） | 5 | agent.py:519 注释「不放入 tool_registry，由 agent loop 特殊处理」 |
| 本地代理工具（`_register_local_proxy_tools` agent.py:596-617，子智能体 allowed 交集时按名注册） | 15 | proxy_tool.py:968 `LOCAL_PROXY_TOOL_CLASSES` |
| 旧注册表（`src/core/executor.py` 独立 ToolRegistry，规划遗留） | 2 | executor.py:206-207 |

注意：`src/tools/browser/browser_tool.py`（browser_open/click 等 7 个）与 `tools_semantic/tools_find/tools_path` 是 BrowserAutomationTool 的内部子动作，**不独立注册**；`asr/speech_to_text` 已注释停用（agent.py:507-509）；均不入清单。

## 3. 主注册表 28 工具（agent.py `_register_builtin_tools`）

| 工具名 | 类别 | effect | 幂等键 | 重试策略 | 依据（file:line 一句话） |
|---|---|---|---|---|---|
| email_send | 业务 | applied | 无 | 禁止自动重试：SMTP 中断后无法确认是否已发出，重发重复打扰收件人 | src/tools/email/email_tool.py:199 `server.sendmail` 真实发信 |
| email_read | 业务 | none | — | 可安全重试 | src/tools/email/email_tool.py:425 UID fetch 用 `BODY.PEEK[]`，不标已读 |
| email_list_folders | 业务 | none | — | 可安全重试 | src/tools/email/email_tool.py:596 起 IMAP list/status 只读 |
| paddleocr_doc_parsing | 文档 | none | — | 可安全重试 | src/tools/ocr/ocr_tool.py:367 远端解析 API 只读返回 markdown |
| web_search | 检索 | none | — | 可安全重试（advanced 超时已内置降级 basic） | src/tools/search/search_tool.py:149 Tavily `client.search` 只读 |
| browser_automation | 网络 | applied | 无 | 禁止盲目重试：任务可能已提交表单/下单 | src/tools/browser/automation_tool.py:186 `orchestrator.execute` 驱动真实浏览器 RPA |
| read | 文件 | none | — | 可安全重试 | src/tools/file/read_tool.py:110 只读文件内容 |
| write | 文件 | applied | 无 | overwrite 重写近似幂等；append 重放会重复追加 | src/tools/file/write_tool.py:502-505 `write_text`/append 写文件 |
| edit | 文件 | applied | 无 | 禁止自动重试：重放旧编辑找不到旧串或二次修改 | src/tools/file/edit_tool.py:358-363 临时文件+rename 原子写回 |
| cp | 文件 | applied | 无 | 重试产生重复文件与重复下载注册 | src/tools/file/cp_tool.py:324 `shutil.copy2` + :331 `_register_download` 注册下载 |
| grep | 文件 | none | — | 可安全重试 | src/tools/file/grep_tool.py:97 只读搜索（rg 参数不经 shell） |
| content_generate | 业务 | none | — | 可安全重试（重生成内容不同，但无副作用） | src/tools/llm/content_generate_tool.py:91 纯 `llm.chat` 生成文本 |
| http_api | 网络 | unknown | 无 | 禁止自动重试：超时后无法确认远端是否已执行 | src/tools/network/http_api.py:163 任意 method（POST/PUT/DELETE）外呼远端 |
| create_scheduled_task | 系统 | applied | 无 | 禁止自动重试：会重复建任务；dry_run 也先真实执行一次 prompt | src/tools/scheduler/scheduled_task_tool.py:179 dry_run 真实试执行 + :203 `ScheduledTaskDB.create` 写库 |
| manage_scheduled_task | 系统 | applied | 无 | pause/resume 重复调用近似幂等；cancel 重试报不存在 | src/tools/scheduler/scheduled_task_tool.py:292/299/306 pause/resume/cancel 改 DB 状态（list/view_logs 只读） |
| knowledge_base_search | 检索 | none | — | 可安全重试 | src/tools/knowledge/knowledge_base_tool.py:72 向量检索只读 |
| attraction_search | 检索 | none | — | 可安全重试 | src/tools/knowledge/attraction_search_tool.py:68 向量检索只读 |
| hotel_search | 检索 | none | — | 可安全重试 | src/tools/knowledge/hotel_search_tool.py:120 ILIKE+向量检索只读 |
| word_process | 文档 | applied | 无 | 重试生成新文件（无害重复文件） | src/tools/word/word_process_tool.py:156 生成 docx 输出文件 |
| excel_process | 文档 | applied | 无 | 同 word_process | src/tools/excel/excel_process_tool.py:210 生成 xlsx 输出文件 |
| pdf_process | 文档 | applied | 无 | 同 word_process | src/tools/pdf/pdf_process_tool.py:164 生成 pdf 输出文件 |
| x_to_image | 文档 | applied | 无 | 同 word_process（临时 PNG） | src/tools/image/x_to_image_tool.py:67 渲染 PNG 临时文件 |
| ppt_process | 文档 | applied | 无 | 同 word_process | src/tools/ppt/ppt_process_tool.py:130 生成 pptx 输出文件 |
| submit_video_task | 业务 | applied | 无 | 禁止自动重试：重复提交消耗视频生成额度（draft_only=True 只出草稿除外） | src/tools/video/submit_video_task_tool.py:107 `handle_user_message_via_chat` 提交视频生成 API |
| transfer_to_human | 渠道 | applied | 无 | 重复调用被 service_state=3 会话状态挡住，但无键保护 | src/tools/transfer_to_human.py:111 `adapter.transfer_to_human` 转接 API + 会话元数据/channel_messages 写入 |
| ai_call | 业务 | none（Mock） | — | —（无真实副作用） | src/tools/phone/ai_call_tool.py:78-89 Mock 返回；接真实外呼 API 后**必须改 applied** |
| analyze_data | 业务 | applied | 无 | 重试产生重复图表文件（查询本身只读） | src/tools/data_analysis/smart_analysis_tool.py:78 起 AnalysisAgent 产出图表 artifact（analysis_agent.py:376 `_handle_to_chart`） |
| upload_data_file | 业务 | applied | 无 | 重试可能重复注册同表 | src/tools/data_analysis/upload_data_tool.py:96 `save_schema_to_knowledge` 写 documents/chunks |

## 4. 虚拟工具 5 个（不入 tool_registry，agent loop 特殊分支）

| 工具名 | 类别 | effect | 幂等键 | 重试策略 | 依据（file:line 一句话） |
|---|---|---|---|---|---|
| create_plan | 系统 | none | — | 可安全重试 | src/tools/plan/create_plan_tool.py:90 计划存 Redis 会话态 TTL 1h（src/core/plan_manager.py:56-60），无业务外部状态 |
| use_skill | 系统 | none | — | 可安全重试 | src/tools/skill/use_skill_tool.py:32 只读加载 SKILL.md 正文 |
| skill_execute | 系统 | unknown | 无 | 禁止自动重试 | src/tools/skill/skill_execute_tool.py:220 `execute_skill_command` 执行任意技能脚本（subprocess），副作用取决于脚本 |
| clarify | 系统 | none | — | 可安全重试 | src/tools/agent/clarify_tool.py:44 仅返回澄清问题字典 |
| delegate_to_subagent | 系统 | unknown | 无 | 禁止自动重试 | src/tools/agent/delegate_tool.py:131 委派子智能体，效果=子智能体实际调用工具的并集，静态不可知 |

## 5. 本地代理工具 15 个（boss_*，子智能体按名交集条件注册）

基类 `LocalToolProxyTool`（src/local_tools/proxy_tool.py:43）统一走 invocation 租约：云端建 invocation（:78-81）→ 本机 Runtime 执行 → 终态带回 runtime 上报的 `effect` 字段（:167/:200-201）；`unknown/expired` 终态强制提示「实际效果未知，禁止重试，请提示用户人工检查」（:36、:186-193）。**这是全仓唯一已落地的 effect 语义，Phase 2 Evidence Ledger 的现成先例。**

| 工具名 | 类别 | effect | 幂等键 | 重试策略 | 依据（file:line 一句话） |
|---|---|---|---|---|---|
| boss_filter | 业务 | applied(本机UI) | invocation 租约 + runtime effect 上报 | 重试安全（仅页面筛选） | src/local_tools/proxy_tool.py:239 「仅改变页面筛选，无外部副作用」 |
| boss_clear_filter | 业务 | applied(本机UI) | 同上 | 重试安全 | src/local_tools/proxy_tool.py:251 同款描述 |
| boss_filter_options | 业务 | none | 同上 | 可安全重试 | src/local_tools/proxy_tool.py:266-270 只读探查筛选档位，读完自动收起 |
| boss_goto | 业务 | applied(本机UI) | 同上 | 重试安全 | src/local_tools/proxy_tool.py:279 页面切换「无外部副作用」 |
| boss_greet | 业务 | applied | 同上 | 禁止盲目重试：可能向同一候选人重复打招呼 | src/local_tools/proxy_tool.py:314-321 「外部可见写动作」，单次 ≤3 人授权上限 |
| boss_accept_resume | 业务 | applied | 同上 | 禁止盲目重试 | src/local_tools/proxy_tool.py:332 「外部可见写动作」，单次固定 1 份 |
| boss_reject_current | 业务 | applied | 同上 | 禁止盲目重试（可能误标下一个候选人） | src/local_tools/proxy_tool.py:340 标记当前候选人不合适 |
| boss_interview_demo | 业务 | applied(本机UI) | 同上 | 重试安全（只填不发送） | src/local_tools/proxy_tool.py:353 「只填写不发送，不属于外部写动作」 |
| boss_list_jobs | 业务 | none | 同上 | 可安全重试 | src/local_tools/proxy_tool.py:363-368 只读列出页面职位 |
| boss_select_job | 业务 | applied(本机UI) | 同上 | 重试安全（幂等切换，切错会校验报错） | src/local_tools/proxy_tool.py:386-391 页面写动作，精确名匹配+切换后校验 |
| boss_jobs_list | 业务 | none | —（纯云端查询，不建 invocation） | 可安全重试 | src/local_tools/proxy_tool.py:445-453 云端职位库只读 SELECT |
| boss_resume_detail | 业务 | applied | 同上 | 谨慎重试：重读简历会重复落库新记录 | src/local_tools/proxy_tool.py:551-615 OCR 读取后 `create_resume_record_from_tool_result` 入库+自动评分 |
| boss_resume_batch | 业务 | applied | 同上 | 谨慎重试（批量版同上，≤3 份） | src/local_tools/proxy_tool.py:618-643 批量读取逐份落库 |
| boss_send_to | 业务 | applied | 同上 | 禁止盲目重试：可能重复发送消息（dry_run=true 只输入不发送；话术模式 SCRIPT_NEEDS_FILL 分支只读） | src/local_tools/proxy_tool.py:838-913 「外部写动作」，搜索找人输入并发送 |
| boss_send_current | 业务 | applied | 同上 | 禁止盲目重试 | src/local_tools/proxy_tool.py:916-965 向当前会话发送消息 |

## 6. 旧注册表 2 个（src/core/executor.py，规划遗留）

| 工具名 | 类别 | effect | 幂等键 | 重试策略 | 依据（file:line 一句话） |
|---|---|---|---|---|---|
| respond | 系统 | none | — | 可安全重试 | src/core/executor.py:134-143 仅返回消息字典 |
| clarify（旧） | 系统 | none | — | 可安全重试 | src/core/executor.py:171-181 仅返回澄清问题字典（与 src/tools/agent/clarify_tool.py 同名不同类） |

## 7. 结论与安全建议

### 7.1 三类分布统计

| 范围 | none | applied | unknown | 合计 |
|---|---|---|---|---|
| 主注册表（§3） | 11（39%） | 16（57%） | 1（4%） | 28 |
| 全部工具（§3-§6） | 19（38%） | 28（56%） | 3（6%） | 50 |

### 7.2 无幂等键的写动作清单

28 个 applied 工具**全部没有显式幂等键**。按风险分层：

**高风险（外部不可逆/不可收回，后续安全加固必须优先覆盖）——11 个：**
`email_send`、`browser_automation`、`transfer_to_human`、`submit_video_task`、`boss_greet`、`boss_accept_resume`、`boss_reject_current`、`boss_send_to`、`boss_send_current`、`create_scheduled_task`（dry_run 先真实执行一次 prompt）、`manage_scheduled_task`（cancel 删除）。

**中风险（落库/注册，重复产生脏数据）——5 个：**
`upload_data_file`（重复注册表）、`boss_resume_detail`、`boss_resume_batch`（重复简历记录）、`analyze_data`（重复图表）、`cp`（重复下载注册）。

**低风险（临时/输出文件或本机 UI 状态，重试只产生无害重复文件/可恢复状态，近似幂等）——12 个：**
`write`（append 例外）、`edit`、`word_process`、`excel_process`、`pdf_process`、`ppt_process`、`x_to_image`、`boss_filter`/`boss_clear_filter`/`boss_goto`/`boss_select_job`/`boss_interview_demo`（本机 UI，重试安全）。

三层合计 11+5+12=28，与 applied 总数一致。

另 unknown 3 个（`http_api`、`skill_execute`、`delegate_to_subagent`）在结果确认前一律按「可能已发生」处理，禁止自动重试。

### 7.3 独立安全建议

1. **按具体工具定义幂等键**：优先覆盖 7.2 高风险 11 个；键的作用域、归一化参数和过期规则必须由对应业务动作定义，不能从已作废的轮级 execution 键继承。
2. **参考 boss_* 链路的 effect 语义**：invocation 的 runtime effect 上报（none/applied/unknown）及 unknown/expired 禁止重试提示是现有先例；是否复用需在具体功能中重新审查。
3. **http_api 补声明**：唯一 unknown 的注册表工具；建议 Phase 2 给 InputModel 加 `declared_effect: none|applied`（GET 默认 none，其余默认 applied），让调用方显式声明而非全局 unknown。
4. **ai_call 接真实 API 时必须同步本清单**（Mock→applied），建议在替换 PR 检查单中固化。
5. **守卫自动化（可选）**：本清单目前靠人工同步；可加结构守卫测试，解析 agent.py 注册调用数与本文档 §3 行数比对，防新增工具漏登记。
6. **skill_execute / delegate_to_subagent 的 unknown 传导**：两者效果取决于脚本/子智能体实际调用；风险判断应检查最终被调用工具，无法确认时保持 unknown。
