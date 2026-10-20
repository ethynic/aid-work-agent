# Excel 读取慢修复与正式库 KB metadata 补迁计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 核查原迁移方案、正式库预检与备份 | ✅ 完成（2026-10-15） | 240文档全部可迁移；metadata类型、chunk唯一性、镜像键安全均正常；原数据0600备份 |
| Phase 2 | 正式库 execute、verify、幂等与目标资料抽检 | ✅ 完成（2026-10-15） | 240迁移/240一致/零冲突零超限零并发；重跑零变更；旧metadata字段与chunks逐文档不变 |
| Phase 3 | Excel 维度扫描与异步入口修复、自测 | ✅ 完成（2026-10-15） | master两源码修复；92单元通过，4项无DB集成实跑通过；完整宽表约1秒 |
| Phase 4 | 独立测试、独立CR、主控整合验收 | ✅ 完成（2026-10-15） | 独立96通过零跳过；真实解析心跳27次；CR无P0/P1/P2；主控导入/编译/diff检查通过；未提交部署 |

设计：[Excel修复](../tools/excel-read-performance-design.md)；迁移沿用[原设计](../system/knowledge-base/unified-kb-resource-table-metadata-design.md)与[原计划Phase 5](plan-unified-kb-resource-metadata.md)；[故障分析](../incidents/wecom-kf-excel-read-runner-interrupted-20261015.md)。

## 实施与成功标准

1. 正式服为129.211.65.243的agent环境、业务库aid_work_agent；不能将脚本--env标签当成选库开关。确认线上脚本与master同SHA、实际DATABASE_URL脱敏身份、240文档数据范围、metadata对象与chunk唯一性。
2. 先备份原metadata和相关chunks并限制备份文件权限；默认--execute迁移，不带--force/--drop-chunks。要求迁移240、冲突/并发/超限均0；verify一致240、mismatch/no_source均0；重跑dry-run零写入；目标1949项目表经统一工具元数据转换可见；旧chunk文本/计数保持一致。
3. ExcelReader缓存循环边界，ReadTool Excel入口线程执行，workbook退出释放；用真实工作簿和事件循环探针验证性能、完整性、并发调度与异常行为。
4. 代码风险按高风险流程执行开发、独立测试与独立CodeReview。运行定向测试及关键导入检查；不连接共享业务库运行pytest，不重放用户业务，不调用真实LLM或对外投递。

## 进度证据

- 原目录当前为feature/ask-user-question-card且有其他任务改动；master由C:/Users/PC/.codex/worktrees/llm-failover-master/aid-work-agent占用且起始干净，复用此目录，不切换/暂存/覆盖原目录改动。
- 原迁移脚本SHA256：39d546aa4da9775c0a344919de95b3591059bf0c293fe1ab88d94c9d31e939d0，生产与master一致。
- 正式库脱敏连接：172.17.0.9:10864/aid_work_agent；全部240份尚无raw_payload，坏metadata=0，重复chunk位次=0。线上PUT已双写metadata/chunks。
- 数据补迁属于本次明确授权；代码部署和旧chunk清场不在本轮执行范围。

### 正式库执行记录（2026-10-15）

- 实际使用生产现有脚本：--env prod --execute → --verify → --dry-run，全部exit0；逐项统计240/240，conflict/concurrent/oversized/mismatch/no_source均0，幂等复查待迁移0。
- 备份：生产容器/app/log/temp/travel_metadata_prod_20261015_before.json，权限0600，SHA256=a4684d732c226a8e7a64a9dec7bbf055bba3e9f9c09e9e1d781fd0174319e807；保留原metadata原文、身份、total_chunks和chunk文本。没有执行--force或--drop-chunks。
- 报告：/app/log/temp/travel_metadata_migrate_prod.log。
- 独立于脚本统计的逐文档断言：候选集合/租户/source_type不变；原顶层metadata、chunk文本、total_chunks均不变；verify_document全部ok。
- doc1949（天空之桥）：生产实际_parse_doc_metadata函数从源码AST提取执行，raw_payload.project_table可见930字且包含桥梁科普馆，ticket_table键存在。该检查只验证元数据转换，未声称执行了完整向量检索或微信端到端对话。

### 开发验证记录

- 环境：Windows宿主Python3.12、openpyxl3.1.5、pytest9.0.2；Git Bash运行scripts/dev_test.sh；DATABASE_URL设置本机不可连接测试地址、LOGS_DATABASE_URL清空、REDIS_ENABLED=false，未向共享库运行pytest。
- 自测：tests/unit/tools/test_excel_reader.py、tests/unit/tools/test_read_tool.py、tests/unit/test_migrate_travel_resource_tables.py合计92 passed。首轮另有4项集成因目录级DB fixture跳过；新增本模块无DB fixture后单独实跑tests/integration/test_excel_read_performance.py，4 passed，未将跳过当通过。
- 构造真实A1:XFC28工作簿：完整保留28×16383格、0/False和J27业务值，测试调用耗时约1.01秒；这是本地复现样本，未在正式worker重跑耗时读取。
- 缩小至28×256的原版/修复版cProfile对照：max_row调用258→1、max_column调用29→1，格式化输出逐字一致；证据在忽略目录tmp/excel-read-evidence/dimension-profile.json。首次探针因Windows子进程默认GBK解码失败，显式UTF-8后成功，无业务源码变更。
- 独立CR：P0/P1/P2=0；开始/结束SHA一致，excel_reader.py=867406677ff48e6ca2e91e6be76f54db18e5e381c5348683e4b0f08c92992b1b，read_tool.py=daf7af56b99662959875bf65502fc38de5e8c2576a526bbbe93e134007ab197f。
- 独立测试：同隔离环境下经scripts/dev_test.sh实际运行上述四个文件，96 passed in 4.68s，0失败、0跳过、exit0。源码开始结束SHA与CR一致。
- 独立额外探针：tmp/excel_independent_probe.py，真实XFC28含业务值完整保留、公式缓存42、合并格与空表语义通过；实际ExcelReader读取0.964秒，实际ReadTool.execute读取0.978秒期间心跳推进27次、最大调度间隔0.073秒，未mock解析器，exit0。
- 主控最终验证：在相同隔离环境实际导入两个模块、初始化ReadTool并执行空路径入口，结果正确；两源码py_compile与git diff --check通过。未连接生产运行修复代码，未部署/提交。
- 已用不带Force的精确Remove-Item安全删除本地SSH临时密钥副本；此前Force清理遭策略拒绝的问题已消除。

## 后续只读调查（2026-10-15）

用户追问“读不到资料为何使Runner崩溃”，明确要求先调查不改。已核对线上worker/executor/engine与master同SHA，并以真实run/_heartbeat方法、内存IO替身复现：普通工具错误可继续，心跳触发的单任务CancelledError却穿透worker主循环，finally还会取消其他任务。原Excel两源及Runner源码未变化；详情与证据限制见[复盘新增章节](../incidents/wecom-kf-excel-read-runner-interrupted-20261015.md#为什么工具读资料没有降级反而使-worker-退出2026-10-15追加调查)。这是独立的任务取消隔离缺陷，本计划已完成的Excel修复不代表它已修复；未新增实现或部署。

## 提交前复验（2026-10-15）

用户随后明确授权先提交本计划对应修改，再另修worker任务取消隔离（新修复不提交部署）。提交前fetch确认origin/master领先4项，针对两个索引文件临时保存并在快进同步后完整恢复，未暂存其他BOSS任务修改；当前基线47f25c16。同步后同隔离环境重跑四套定向测试96 passed in 4.85s、0跳过；主控Excel模块实际导入与入口检查通过，已有独立测试/CR的两个源码SHA保持一致。正式服代码未部署。
