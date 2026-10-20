# Runner单任务取消隔离修复计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 设计边界与原缺陷回归复现 | ✅ 完成（2026-10-15） | 新12项修前4失败8通过；失败精确覆盖原取消传播缺陷 |
| Phase 2 | 常驻/once任务回收修复与开发自测 | ✅ 完成（2026-10-15） | 唯一源码worker.run修复；新12通过，组合153通过/1存量schema夹具失败 |
| Phase 3 | 独立测试与独立CodeReview | ✅ 完成（2026-10-15） | 独立核心与真实execute探针51通过零跳过；组合153通过/1同存量失败；CR无P0/P1/P2 |
| Phase 4 | 主控整合与隔离启动检查 | ✅ 完成（2026-10-15） | 导入/配置/CLI帮助/编译/diff检查通过；源码SHA一致，后续获提交授权，未部署 |

关联：[架构设计§5.5](../system/agent-application-architecture-design.md#55-执行任务取消与worker生命周期隔离2026-10-15)、[原故障](../incidents/wecom-kf-excel-read-runner-interrupted-20261015.md)。

## 授权与基线

- 用户2026-10-15要求先提交上一轮Excel/metadata修复，再在master修复单任务取消退出；新修复不提交部署。
- 上一轮已提交a99a08e1，Codeup origin/master推送成功。配置的GitHub镜像两次TLS/连接失败，未冒充镜像同步成功。
- 当前复用master工作目录C:/Users/PC/.codex/worktrees/llm-failover-master/aid-work-agent；另有BOSS功能并发改动，只修改本任务指定文件，不暂存它们。
- 高风险验证角色：主控开发、自测；独立测试者；独立CodeReview。无需迁移、网络调用或修改生产服务。

## 实现与验收

1. 已完成Task若cancelled，仅回收；非取消Task读取result并继续传播意外异常。--once等待空集合时保持返回0，有任务取消时正常返回1。取消也计入max_tasks，后续新任务可领取。
2. heartbeat仍中断原control并取消owner；execute仍完成已有收尾再重抛，不吞取消继续执行模型/工具，不补发/重放业务。
3. 测试用真实RunnerWorker.run/_heartbeat，内存仓储/执行体作为IO边界；并发双任务中一项因LeaseLost取消，另一项可完成且worker可领取第三项。串行槽复用、取消收尾屏障、once、空任务、普通异常、外部cancel与显式stop均需验证。
4. 运行同模块既有worker执行与渠道取消回归；补模块导入及CLI --help隔离启动检查。Python统一经scripts/dev_test.sh；业务DB指向不可连接本机测试地址，日志DB清空，Redis禁用，不能运行共享库清理。
5. 独立测试/CR完成后主控检查最终源码hash、diff、文档索引。未获新提交/部署授权，不进行Git提交或服务器变更。

## 验证记录

### 环境与自测

- Windows宿主Python3.12.6/openpyxl3.1.5/pytest9.0.2，Git Bash运行统一入口；DATABASE_URL=postgresql://test_disabled@127.0.0.1:1/test_disabled、LOGS_DATABASE_URL为空、REDIS_ENABLED=false、PYTHONUTF8=1。未连接共享业务库、服务器或LLM。
- 修复前：scripts/dev_test.sh tests/unit/services/agent_runner/test_worker_task_isolation.py -p no:cacheprovider -q --tb=short，4 failed/8 passed，exit1；分别为串行/并发心跳取消、取消后续接单、once取消。
- 修复后：上述新文件加test_worker_execution.py、tests/unit/test_agent_engine_acceptance.py、tests/unit/channels/test_channel_runner_service_independent.py，153 passed/1 failed/0 skipped（12.78s），exit1。新12项均通过，不把组合称为全绿。

### 存量失败核验

唯一失败为test_fresh_runner_schema_and_original_channel_do_not_require_retired_native_pipeline：通知schema检查读取version，内存夹具只返回name，KeyError出自src/notifications/repository.py:126。本次未改repository、通知模块或该测试。

主控用忽略目录tmp/runner_baseline_plugin.py，在独立pytest进程将run函数恢复为已提交a99a08e1的源码AST，仍经scripts/dev_test.sh运行该单例，同样1 failed/exit1。该测试路径本身也不调用worker.run，因此确认是已有夹具问题，未扩展修复无关代码。

### 独立测试与CR

- 独立测试重新运行完整四文件组合：153 passed/1 failed/0 skipped（11.64s），exit1，同一schema夹具失败。
- 独立核心回归及自有tmp/test_runner_independent_probe.py：51 passed/0 failed/0 skipped（2.55s），exit0；含12新用例、既有worker执行/Engine回归与2项额外探针。
- 额外探针使用真实run/execute/_heartbeat及真实drain，仓储/runtime/trace持久化为内存IO边界：LeaseLost取消owner后，drain未完成时run不返回、trace不flush、guard保留；release后依序drain、duration写被LeaseLost拒绝、interrupt、trace、jobs清空/guard删除、factory.close，once返回1，无stage/finalize/回复。另验max_tasks=0不领取任务。探针初次配置类型不符失败，仅修正自有探针后通过。
- 独立CR：P0/P1/P2=0。常驻/once仅隔离已取消且完成的子任务，主协程取消与停机完整保留，非取消异常继续暴露。
- 独立测试/CR开始结束与主控最终worker.py SHA256均为c5acf19e8a67452288fd17f6e0030d57b025553a4ba61dd9b64368d923499b12。

### 主控最终检查与交付限制

实际导入RunnerWorker/AgentRunnerConfig、默认心跳租约约束检查、python -m src.services.agent_runner.worker --help、py_compile和指定文件git diff --check均通过。CLI帮助是导入/命令入口检查，未启动真实持久化服务。

本修复仅改worker调度回收，无DB/schema、费用或设备协议变更；旧attempt仍停止执行，不通过“吞取消继续调用”伪装恢复。业务源码与测试均保留为未暂存、未提交状态，未部署。真实DB/生产端到端未在本轮执行；完整回归的存量schema失败单独保留，不冒充已修复。

最终检查期间另一项BOSS任务提交50a0ba35，master的HEAD因此前进；本任务未执行新的commit。主控核对该提交文件列表不含worker修复，HEAD中worker仍为原SHA c74ecea5aa0c54ded128db5033f2e5d5f82db8fe0cac72c7ccb0cf05531bfa2b，工作区才是已独验的新SHA，暂存区为空。未将HEAD前进误报为本任务已提交。

## 后续提交授权与复核（2026-10-15）

用户随后明确要求“提交代码吧”，授权提交并按仓库规范推送本修复；部署仍未授权。提交前确认master/upstream=origin/master，git fetch origin后双方一致，HEAD为56e97343。其间新增提交仅涉及Runtime打包/交付及文档，本次worker源码SHA仍与独立测试/CR完全一致，因此复用既有51项核心验证、完整组合的存量失败归因与独立CR；主控再次执行CLI帮助、py_compile及diff检查通过。只暂存本任务六个文件，其他Runtime包与交付文件不纳入提交。
