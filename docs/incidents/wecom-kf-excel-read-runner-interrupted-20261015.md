# 微信客服读取异常宽表导致 Runner 中断（2026-10-15）

## 排查结论

生产 `agent` 的 trace `tr_runner_efe14dd37c7547fe8dfffd8821d454ea` 在读取知识库 Excel 时阻塞约 222 秒，随后 Runner 从 running 进入 interrupted，渠道适配器取消任务并发送“抱歉，处理您的消息时遇到了问题，请稍后重试。”。

直接触发因素是 Excel 中延伸到 XFC 列的空白/样式单元格与同步全范围读取实现。结合线上有效配置（租约 30 秒、心跳 10 秒）、同步执行路径和中断时序，高置信判断为事件循环被阻塞后无法续租，引发任务取消；心跳异常分支没有记录原始异常，日志未直接保存该次 `LeaseLost`，不能把它写成已捕获的异常事实。

初始排查仅查询生产日志、数据库和文件元数据；2026-10-15用户随后明确授权补迁与代码修复，正式库240份资源已完成metadata补迁，Excel代码在master工作目录修复，未重跑用户业务请求或部署代码。执行与验证见[开发计划](../plans/plan-excel-read-performance.md)。

## 核验范围和证据

- 生产容器：`aid-agent-api`、`aid-runner-worker`；业务库 `aid_work_agent`，追踪库 `aid_work_logs`。
- 渠道/智能体：`wecom_kf` / `travel-consultant`。
- Runner：`runner_efe14dd37c7547fe8dfffd8821d454ea`。
- 主日志：`/var/www/agent/log/agent/aid-work-agent_20261015.log`；worker 标准输出由 `docker logs` 查询。
- 时间均为北京时间（2026-10-15）。

| 时间 | 已核验事实 |
|------|------------|
| 14:30:23 | 微信 sync_msg 成功，语音附件下载、SILK 解码成功 |
| 14:30:24 | ASR 成功，文本为“帮我加上天空之桥。” |
| 14:30:26 | Runner attempt 1 开始执行 |
| 14:30:26–14:30:32 | 两次 deepseek-flash 调用和两次知识库搜索均 completed |
| 14:30:32.816 | 开始 `tool:read`，目标 `kb_cb53a5e9aa06.xlsx` |
| 14:34:14.663 | worker 输出“成功读取Excel文档”，工具随后报告成功 |
| 14:34:14.924 | Runner 事件记录 interrupted；read span 最终为 interrupted，duration_ms=222134 |
| 14:34:15 | worker 输出 `asyncio.exceptions.CancelledError`，栈位于 worker.run → execute → iter_with_verbose_feedback → queue.get；随后退出 |
| 14:34:16 | worker 重新启动；Runner attempt 2 收尾取消，14:34:16.962 记录 cancelled 终态 |
| 14:34:17 | 渠道队列报 `CHANNEL_EXECUTION_INTERRUPTED`，错误提示发送成功 |

`obs_traces` 最终 status=cancelled、output 为空。该行 duration_ms=6043 不代表端到端耗时：read span 单独耗时 222134ms，渠道记录耗时 233199ms。不能仅凭 trace 汇总耗时认定处理只用了六秒。

## Excel 和代码事实

通过 ZIP/XML 只读检查文件结构，未在生产再次调用 ExcelReader：

- 文件大小 14,140 字节，单工作表 XML 大小 13,838 字节。
- 工作表 dimension 为 `A1:XFC28`，包含 16,383 列、28 行，矩形范围共 458,724 格。
- XML 实际存储 299 个单元格，其中含值或 inline string 的单元格仅 164 个；最后存储格为 XFC28，按 XML 顺序最后有值格为 J27。
- 不能将该结果解释为用户实际有 16,383 列业务数据；少量空白/样式格扩张了读取范围。

已核对生产源码：

1. `src/tools/file/read_tool.py` 的 `async def execute()` 直接调用同步 `_read_excel_document()`，没有转移到线程执行。
2. `src/tools/file/excel_reader.py` 全范围提取 rows，之后再次全范围计算列宽并格式化；循环反复访问 `sheet.max_row` / `sheet.max_column`，扩大范围会显著放大计算量。
3. `src/services/agent_runner/worker.py` 的心跳协程与工具执行共用事件循环；心跳失败后 `control.interrupt()` 并取消 owner task。
4. `execution_repository.py` 的 heartbeat 拒绝续期已过期的租约。生产实际 settings 为 lease_seconds=30、heartbeat_seconds=10，远短于本次同步读取耗时。
5. `src/channels/runner_agent.py` 观察到 interrupted/paused 时调用 `_cancel_and_wait()`，抛 `CHANNEL_EXECUTION_INTERRUPTED`；`src/channels/session.py` 的 error 分支发送截图中的兜底文案。

## Excel 的业务用途和调用来源（补充核验）

该文件是既有景点资源资料，并非行程导出的中间文件。生产 `documents` 记录 id=1949，title=`景点：天空之桥研学基地`，source_type=`attraction_resource`，file_type=`xlsx`，created_at/updated_at 均为 2026-06-05 16:05:30；保存路径即本次 read 参数。数据库没有保留可确认的原始上传文件名，不以存储用随机文件名猜测原名。

该景点的旧版知识片段包含“超级桥梁”“桥见贵州”“贵州桥语”“桥模DIY”“桥梁科普馆”等课程/服务、适用群体、时长、报价信息。这些是编排行程的输入资料，与最终以 Markdown 调用 `word_process` 输出 Word 的流程用途不同。

本 trace 的实际调用顺序：

1. 用户语音要求“帮我加上天空之桥”。
2. 模型调用 `knowledge_base_search(query="天空之桥 平塘特大桥 景点", top_k=10)`。
3. 同轮调用 `knowledge_base_search(query="平塘 天空之桥 桥梁科普馆 参观", top_k=10)`。
4. 两个搜索结果均返回 id=1949 的标题、xlsx 路径和景点信息摘要，匹配 score=1.0；但该文档的 `doc_metadata` 均为 `{}`，没有项目明细。
5. 下一次模型调用之后执行 `read(file_path="storage/tenants/ea24cd1a1097/knowledge/kb_cb53a5e9aa06.xlsx")`，继而发生上述阻塞和中断。

两次模型调用的 trace output 为空，不能声称保存了模型为何选 read 的文字解释；根据路径来源和工具序列，可以确认路径来自搜索返回结果，高置信推断读取目的是补查该景点资料。没有证据表明模型把“导出 Word”误当成“生成 Excel”，本 trace 没有调用 `word_process`。

另有数据契约缺口：生产智能体指令要求从 `doc_metadata.raw_payload.project_table` 选择项目；这个旧文档 metadata 仅有 region/category_cn/category/source_sheet，没有 raw_payload，实际项目表仍在 chunks 的 chunk_index=2。生产提示词同时规定 project_table 缺失时项目列写“—”，并未要求读取原始 Excel。因此 read 是本次模型额外选择的资料补查路径，不是生成行程或导出 Word 的必需步骤。不能用“提示词强制要求读 Excel”解释本次调用。

`word_process(context="完整详细行程 Markdown 原文")` 是客户确认详细行程之后的导出步骤；本轮请求仍是新增景点、调整行程，尚未走到该步骤。

## 为什么工具读资料没有降级，反而使 worker 退出（2026-10-15追加调查）

本节按用户要求只调查，未修改Runner、工具或渠道业务源码；上一轮Excel修复仍保持原SHA，代码未部署。本轮生产操作仅只读查询源码hash、容器状态与历史日志。

### 结论：业务错误与执行权中断进入了不同通道

普通工具异常由ToolExecutor.execute的except Exception转成错误结果，Engine将tool结果加入messages，模型可以继续解释或换资料。本次Excel没有返回普通读取错误：14:34:14.663已打印文档读取成功，14:34:14.680执行器也返回成功，但此前长时间阻塞使执行权/心跳链路失效，随后任务被取消，模型未获得继续生成答复的机会。

不能把“暂时无法读取资料”直接等同于“景点没有资料”：两次搜索已取得景点摘要，原文件和项目表也实际存在。健康任务下可以基于已有信息回答并说明暂时无法读取详细资料；失去租约的原attempt则必须停止后续模型、工具与持久化操作，不能为了补一句话继续使用失效执行权。

### 已核对的传播链

1. worker._heartbeat（282行起）发现续租异常，执行control.interrupt()、owner_task.cancel()。该分支捕获所有非CancelledError的BaseException，但没有记录原始异常类型；本次最初为何失效仍以“长阻塞导致租约失效”的高置信判断表述，不能伪造已保存的LeaseLost日志。
2. worker.execute（520行起）捕获CancelledError，尝试保存/中断收尾后重新raise。该取消属于执行生命周期中断，既不被工具层except Exception捕获，也没有被转换成给模型的业务错误。
3. worker.run（691–692行）对完成子任务直接await task，CancelledError随之传播至调度循环。外层except asyncio.CancelledError（701行起）只在self.stopping已经置位时接受取消，否则继续raise。因此单任务内部取消会被当作worker主协程异常。
4. run的finally调用self.stop()并取消/等待剩余任务，关闭资源；run_worker的finally关闭连接池，异常最终到达asyncio.run/main，进程退出。
5. 生产容器restart_policy为unless-stopped，因此进程退出后自动重启。目标窗日志14:34:15的Traceback明确在worker.run第692行await task处透出CancelledError；14:34:16重新出现初始化日志。查询时RestartCount=2是容器累计次数，不代表本trace重启了两次。
6. 渠道适配层观察到interrupted后_cancel_and_wait，并抛CHANNEL_EXECUTION_INTERRUPTED；session error分支只能发送固定兜底消息，未再进入模型回复流程。

其中，“单任务CancelledError越过调度边界导致worker退出”已由线上完整栈与隔离复现直接证实；“该次心跳原始异常必然为LeaseLost”没有直接日志，只能根据30秒租约、222秒阻塞和中断时序判断。没有将两者混写成同一证据等级。

### 隔离复现及边界

生产与本机master的三个相关文件SHA完全一致：

- worker.py：c74ecea5aa0c54ded128db5033f2e5d5f82db8fe0cac72c7ccb0cf05531bfa2b。
- tools/executor.py：a80d8a1ca122f8d4158ddbfb505f87f7b5e3b8dc15c20d257ed90eab86be70e3。
- agent_engine/engine.py：cfa57ea503600407ac7feece15d0b2cb20ea1fa66e7c3054f3827970e3bed900。

在忽略目录tmp/runner_cancel_investigation.py构造内存IO边界，调用真实RunnerWorker.run/_heartbeat与ToolExecutor.execute，不启动服务、不使用真实DB/LLM。两场均成功复现预期对照，exit0：

| 注入情形 | 实际结果 |
|---------|----------|
| 工具抛普通ValueError | 执行器返回success=false/error；该任务正常返回，worker继续处理另一任务并完成2项 |
| 心跳仓储抛LeaseLost | 心跳取消job1；worker.run以CancelledError退出，主协程外部cancel请求数为0；finally中job2也被取消并关闭资源 |

证据保存tmp/excel-read-evidence/runner-cancel-investigation.json；开始/结束核对worker、executor、engine及上一轮两份Excel源码SHA均不变。第二场是内存故障注入，证明调度传播与连带影响机制，不声称生产当时必然存在被牵连的其他用户任务。

另做只读故障注入确认：ReadTool的Excel失败包装返回错误字符串，运行时对非dict结果默认success=true，因此会出现“工具执行成功”日志，但错误文字仍会作为tool内容进入模型。这是错误标志表达不一致的次要发现，不能解释本次进程退出；本次线上读取的底层日志本身也确实报告读取成功。

### 后续处理边界（本轮不实现）

本节保留当时的只读调查边界。用户随后授权修复取消隔离，已完成master代码与独立验证，并另行授权提交，当前状态见[修复计划](../plans/plan-runner-task-cancellation-isolation.md)；代码未部署。心跳诊断日志与渠道文案未随本次扩展。

- 单任务内部取消、worker显式退出与存储/租约失败应区分，任务收尾不得意外终止整个调度进程；同时继续遵守失效attempt禁止执行后续IO的约束，不能简单吞掉CancelledError。
- 给心跳失败补充脱敏异常类型/原因码，并区分用户取消、租约失效和服务停机，避免trace最终cancelled掩盖最初中断来源。
- 资料读取失败与真正无资料采用准确业务文案；渠道可提供明确失败原因，不能把基础设施故障转换成“景点无资料”的错误事实。
- 若进入修复，须另补设计/开发计划与独立测试/CR；覆盖双任务隔离、真实关闭、租约失效后零额外模型/工具调用。用户本轮仅授权调查。

## 影响和后续建议

本次失败发生在服务端文件处理阶段。微信接收、语音识别、模型调用、知识库检索和失败提示投递都有成功证据。worker 发生过异常退出与自动重启，不能只视为单条文案问题；本轮未做所有租户影响面统计。

原排查建议范围（当前实施状态以关联开发计划为准）：

1. 在异步工具入口将同步文档解析移到线程，保证 Runner 心跳可以调度。
2. 优化 Excel 有效数据范围识别和遍历，避免远端空白/样式格导致全宽扫描；缓存边界，避免循环中重复计算维度。范围裁剪必须保留实际非空业务数据，不以固定列数静默截断。
3. 独立验证含 XFC28 空白/样式格的小型工作簿：内容完整、处理及时，工具执行期间心跳持续运行。
4. 核查心跳异常到 worker 主循环的 CancelledError 传播，避免单任务中断直接使整个 worker 退出，并补充脱敏异常类型诊断。
5. 核查旧景点数据的项目表到 raw_payload 的兼容读取/迁移，让知识库检索提供完整业务资料，减少模型回读原始 Excel 的动机；本次未修改旧数据。

如进入修复开发，应先按仓库规则补充/更新设计和开发计划并登记；本复盘不代表开发或部署授权。
