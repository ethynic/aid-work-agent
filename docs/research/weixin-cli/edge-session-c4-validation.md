# C4 会话任务工作台与工具验证

日期：2026-09-15。范围：C4 代码与隔离 fake 验证；C0 真机及 C5 门禁仍未通过，不开放真实观察/发送能力；开发验证阶段未提交代码。

关联：[设计 §13.6](../../design/desktop-automation/edge-session-task-design.md)、[C0–C5 计划](../../plans/desktop-automation/plan-edge-session-task.md)、[进行中索引第78项](../../ideas.md)。

## 交付

- 微信营销模块内新增会话任务列表/详情，保持 developing；Base*、AppHeader、三种完成模式、回复策略、预算/期限、开场白与工作时段、已有绑定选择。
- prepare/publish/manage 经 Catalog 自动发现与 Assembly 装配，身份仅取 ToolExecutionContext。prepare 返回授权表单；确认按钮一次点击签发并发布，工具不能签发确认；工具重放以 confirmation 为稳定业务键返回原回执。
- 属主限定的列表/详情投影与分页时间线：任务阶段、在线、观察时间、回复/轮数/决策、积分、消息证据和底座发送账本。响应 no-store，浏览器不持久化正文。
- 显式 fresh_baseline 恢复：核对版本、水位、绑定、预算、期限、未决模型/发送；旧历史批次不重处理，旧租约失效，新 claim 持久化单次恢复许可及连续输入版本，不重复开场白。
- `session_task_notifications` 迁移/初始化/登记同步；完成、停止、需人工和阻断同事务生成站内通知，按 tenant/task/control_epoch 去重，不外发。
- C4 弹窗启用可选键盘焦点管理；异步代际检查、卸载取消、双击保护、版本冲突提示；窄屏表格在自身容器横向滚动。

## 验证证据

Python 常规验证通过 WSL `bash ./scripts/dev_test.sh … -p no:cacheprovider -q` 使用运行中的 aid-agent-api；已核对挂载 `/mnt/c/repos/aid-work-agent → /app`。数据库测试使用随机隔离租户，测试后清理，不连接真实微信。

| 检查 | 结果 |
|---|---|
| 独立 C4 PostgreSQL `tests/integration/test_session_workbench.py` | 共20项；核心/CR回归19项通过，新增幂等发布回放1项通过 |
| `tests/unit/tools/test_session_task_tools.py` | 14通过；缺身份/伪造确认/参数注入/恢复水位/目录注册 |
| 广泛 `tests/unit/session_tasks` + 工具及当时C4集成 | 首次220通过、2项旧“C1未接入”文案断言失败；更新断言后相关16项通过。未将首次失败隐去或机械重跑全量 |
| Runtime `npm --prefix clients/agent-tool-runtime run build:main` + `node --test dist/tests/session-*.test.js` | build通过，6个测试文件62项通过；含fake Provider子进程、日志与执行恢复 |
| 前端 `npx vitest run web/__tests__/components/sessionTasks web/__tests__/router/routes.test.ts` | 16通过；另既有BaseModal测试2通过 |
| 前端 `npm run build` | 最终代码通过，512模块 |
| 编译与diff | `compileall`、`git diff --check` 通过；仅既有弃用/换行提示 |

浏览器使用本地 Vite 与仅隔离假数据的挂载页，检查1440×1000详情、发布完整授权弹窗、390×844窄屏列表/弹窗。窄屏页面宽390，表格内部滚动宽1100；Tab进入按钮、Escape关闭、焦点返回通过。未执行真实发布。检查中发现手机文字被压成竖排，已设置表格最小宽并复验。

Windows真实Runtime进程＋fake Provider联测：`"C:/Program Files/Git/bin/bash.exe" ./scripts/dev_test.sh tests/integration/test_session_live_runtime.py -p no:cacheprovider -q --tb=short` → **1 passed，48.16s**，覆盖实际路由/DB/Runtime进程/进程kill恢复。默认容器没有Node，最初两次运行因环境缺失失败；切换现有Windows Python3.12/Node通过Git Bash同一dev_test.sh入口后通过。

## 独立角色与问题闭环

独立测试与独立CodeReview分别执行。审查后已修复：

1. 恢复后新claim前，旧epoch迟到批次重新变accepted：改为历史事实通道；回归验证不进入新transcript。
2. 阶段遗漏decision_phase/phase_to、40事件截断和旧assignment覆盖：改为当前assignment按序取最新相关事件；保留recovery_blocked和观察缺口优先级。
3. 前端错误创建返回字段、非法控制按钮、stop原因缺失及旧请求清理竞态。
4. 工具重复发布原先冲突：使用既有事务回执，原请求重复（即使已暂停）不复活任务。

最终只读复核无未关闭的明确P0/P1。

## 限制与后续

- C0/C5真机、性能、安装包和灰度验收仍BLOCKED/待执行；fake不证明实际微信身份/OCR/新增气泡可靠性。
- 首次恢复claim回包丢失或本地元数据持久化失败后，恢复许可不会自动授予另一代。后续领取可能保守阻断；用户需显式暂停→选择新基线恢复。保留此安全停机行为，不因网络超时重复放行旧未决工作。
- V1恢复只提供新基线，历史不补发；unknown、身份/覆盖等blocked不能靠按钮解除。额度/期限仍按原授权，改授权须重新确认。
- 继承的部分HTTP控制/草稿端点仍以expected_version CAS防重复变更；本次创建/发布及工具发布使用事务幂等回执，未宣称所有既有写端点均已统一Idempotency-Key契约。
- 保留工作区原BOSS等无关改动；开发验证阶段未提交、未推送，真实能力未开启。

## 用户授权后的独立提交门禁复核

2026-09-15，按用户要求由新的独立智能体重新评估实际C4代码，结论为可提交入库，无明确阻断P0/P1，无需额外开发修复。独立执行 `./scripts/dev_test.sh tests/integration/test_session_workbench.py tests/unit/tools/test_session_task_tools.py -p no:cacheprovider -q --tb=short`：34 passed，4个既有弃用警告，25.66s（Windows宿主Python＋真实PostgreSQL）。主控最终前端build及模块import/默认关闭断言通过。fetch后master与origin/master一致；仅暂存C4清单，BOSS等原有改动保留。用户已授权通过评估后提交并依仓库规范推送，本次放行不代表C5真机上线。
