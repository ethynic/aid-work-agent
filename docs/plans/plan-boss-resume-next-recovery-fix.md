# BOSS 续筛评分、恢复与故障反馈修复

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 续筛模型归属和恢复证明 | ✅ 完成（2026-10-14） | 真实 Worker 续筛 3 项通过：正常、评分后恢复、扣费后恢复 |
| Phase 2 | 中断展示、阶段进度与 Trace | ✅ 完成（2026-10-14） | Web build、隔离浏览器宽窄屏、独立 Trace Worker/PG 5 项通过 |
| Phase 3 | Provider 回执兼容和安全诊断 | ✅ 完成（2026-10-14） | Core 9 项、Runtime 35 项通过；原始坏回执触发原因仍未知 |
| Phase 4 | 独立测试与 CodeReview | ✅ 完成（2026-10-14） | 独立定向验收通过，CR 无未解决 P0/P1/P2；源码提交 49556694，未部署 |
| Phase 5 | 客户端 r10 打包交付 | ✅ 完成（2026-10-14） | 标准验包、实际 NSIS 内容核对、独立隔离启动通过；未执行现场安装 |

## 事实与范围

2026-10-14 agent2 的 `tr_runner_c3477033de4d4118968c98bd9316cb0b` 在本地读取三份简历后，云端 `resume_recognition_covered` 调用被 `COVERED_USAGE_OWNER_REQUIRED` 拦截；14:26 的恢复又被 `LOCAL_PHASE_INTENT_CHANGED` 拒绝。前一轮 Batch 正常完成。另一个会话的 `tr_runner_71d638da69674df8ad203ca0c79a2bf6` 因 Provider 回执不可解析进入效果未知等待，未展示说明。

修复包括 Next 在评分、模型来源、领域恢复的完整证明；系统派生参数与模型原始输入分开核对；中断/核验等待始终显示可理解状态；工具阶段完成不冒充整个任务完成；Trace 保留安全错误码并结束未完成工具 span；拒绝恢复不得改变旧输出时间；Provider 回执兼容多文本块但拒绝歧义、错误包装和畸形结果。

## 共同契约检查点

- 遵守 `AID-RUNNER-DESKTOP-RUNTIME` v2.0，现有 Runner API v1 与 MCP 返回结构不升级、不新增业务入口。
- 本批唯一整合者为主控；续筛证明子任务仅写 Runner 证明模块与相关测试，Provider 子任务仅写共享 Host 解析模块与相关测试；主控负责状态展示、Trace 和文档。
- 兼容旧 checkpoint：有明确核验说明则保留；缺少说明由展示层提供保守提示。既有状态语义不变，不把 interrupted 当 completed/failed。
- 保持 C08/C09 原身份、设备和意图验证；保持 C11 效果未知不自动重放。任何外部副作用不以恢复请求作为新授权。
- 不修改公共 schema、Device API、binding、全局包或 lockfile；如确需修改，先补版本和生产消费兼容检查。

## 验证要求

1. 使用真实证明组件验证 Next 正常评分、续筛中断恢复、重复结果幂等；拒绝跨租户/会话/设备及伪造父引用、排重名单、截图和策略。
2. 前端验证 interrupted/verification 文案、阶段进度、拒绝恢复后的时间稳定；运行 Web build。
3. Trace 验证错误码不含异常敏感内容、未完成工具 span 不伪报完成、后续成功恢复不残留失败状态。
4. Host 验证合法 structuredContent/文本结果、错误包装、多块歧义及不泄露 Provider 原始内容。
5. 用户未授权部署；agent2 原故障任务不修改、不恢复，不发送真实招呼。真机发布验收待明确部署授权后执行。

## 验证记录

- 开发者：续筛证明/历史/识别定向单测 54 passed。Trace 与 Worker 初轮 24 passed；后续四种未完成 Span 状态已纳入独立重跑。
- 独立验证：证明与 Trace 单测 49 passed；真实 Worker + 隔离 PostgreSQL 5 passed（终态事务失败→重试成功清旧错误，四种未完成状态真实落库）。
- 独立 Next 集成：专属临时 Runner/pgvector 容器，外网禁用，模型与设备仅测试 IO，`test_resume_next_owner_flow.py` 3 passed / 0 failed / 0 skipped，148.92s，退出码 0。覆盖正常续筛、`resume.evaluate` 提交后强制中断恢复、`resume.fee` 提交后强制中断恢复；验证真实模型授权、截图证明、入库和计费，不重复派发、识别或扣费。
- 独立前端：工具函数和实际组件挂载 16 passed；等待状态中文标签调整后对应 4 项复验通过。开发者既有 Batch 真实读取、评分、识别费和入库场景 1 passed。
- Provider：Core build；解析/续筛能力 9 passed；Runtime build:main；真实 stdio、大回执、崩溃、v2 写路径及契约 35 passed。独立 SDK 回执 6 passed。
- 主控：Web build 最终状态通过；隔离 API 数据下真实 Chrome 检查 1280px/390px 中断正文为空提示及旧 verification 无 question 提示，无真实业务请求。临时验收脚本与截图存于忽略目录。
- CR：修复 finalization-only 重试成功仍残留错误、未完成 span 默认成功；同步 TraceDetail 状态颜色/中文标签。保留历史失败 span，成功最终态清错误摘要。
- 环境边界：Windows 单测使用项目 venv；完整 Runner 集成在本机 WSL 容器中，临时 pgvector 测试服务和随机隔离数据库，不使用容器原远程 DATABASE_URL，不修改业务部署。
- 测试环境时区统一为 `Asia/Shanghai`，避免临时库默认 UTC 与既有无时区设备心跳字段造成离线误判。
- 相邻旧用例 `test_finite_original_recognition_permit_and_untrusted_hints_have_distinct_prices[covered-detail]` 失败：其 probe 没有真实原始设备调用/策略证明，当前 HEAD 原有校验已拒绝该输入；本次不降低生产校验来适配旧 fixture。混跑还出现 pytest 失败报告进程崩溃，不把该次无最终摘要的输出作为通过证据；新增 Next 场景单独验收。
- 解释器异常排除：最终 Next 证据来自专属测试容器和私有 `PYTHONPYCACHEPREFIX`，使用 `--assert=plain --tb=line` 保留业务断言；没有修改业务依赖或重启既有容器。所有本任务测试容器已清理，agent2 原任务未改动。
- 提交前同步：2026-10-14 fetch 后快进到 `ef66f7d2`，远程增量为 LLM 上限参数修复；合并后 Gateway 参数、Next 证明及 Trace 定向回归 32 passed。

## 交付边界

- 代码开发和上述定向验收完成，不等同于全仓测试全部通过；旧 owner fixture 失败单独保留，未计入通过数。
- 原 Provider 坏回执未留存，无法证明当时具体触发原因；本次完成既有合法格式兼容、错误包装拒绝及不含原文的安全诊断。
- 用户已授权提交、推送与所需客户端打包，打包记录随后补充；agent2 由用户手动发布，本任务不部署服务端。需要更新后端 API、Runner API/Worker、网页前端，以及包含共享 Host 修复的新 Runtime 客户端；BOSS 插件无本次源码变更。

## 发布交接（2026-10-14）

- 修复源码：`495566943b923cc3e5176d11b50657773f0cfd6b`，已推送 `origin/master`。
- agent2 需要发布：更新 Python 后端、Runner API/Worker 和构建网页前端；仅更新客户端不能修复云端评分归属与恢复校验。此次没有新增数据库迁移。
- 用户在服务器手动执行既有命令：`cd /var/www/agent2 && ./deploy/agent2_update.sh`。该脚本 fetch 后 `git reset --hard origin/master`，部署执行时最新 master（并非固定到 49556694），构建 Web，并同时重建主服务与 Runner overlay；执行前确认服务器没有需要保留的未提交改动，按现有发布流程选择维护窗口、核对在途任务。助手未执行该命令。
- 新客户端用于本次 Provider 回执兼容与诊断；原 BOSS 0.3.5 插件不需要重新开发或替换。r10 组合包与旧 r9 分开保留，通过源码提交和 SHA256 区分，未执行真实用户安装/配对/招呼。
- 安装方式：手动执行 r10 安装器，沿用原用户和安装目录；现有 NSIS 同版本也执行旧版本卸载及新文件安装，保留应用数据，并经过 Runtime drain/延期安装保护。版本号相同不能用来判断是否已安装修复，应核对 r10 包与哈希；当前未启用自动更新。

### r10 打包事实

- 输入源码：`495566943b923cc3e5176d11b50657773f0cfd6b`；UI 0.0.6 / Host 0.2.18 / Core 0.2.17 / BOSS 0.3.5 / Node 22.23.3（Windows x64）。
- 安装器：`AID-Work-Runtime-0.0.6-win-x64-acceptance-unsigned.exe`，126399391 字节，SHA256 `1828ab4e8f5ef2d0d5e78ef343aaa31aad6ebcdf61e5dd8dea344ae2b6fafb9c`；Windows 签名状态 `NotSigned`，属于试用验收包。
- Host tgz SHA256：`4b965115e95b75cc0092d22c99ea0bb82f9a8cdbb3f6798e08917c2553788fab`。标准 `verify-runtime-package.mjs` 通过（3692 个资源文件）；安装器和 manifest 输出到独立 r10 目录，未覆盖 r9 ZIP。
- 组合包：`clients/release/AID-Work-Runtime-UI-BOSS-20261014-trial-r10.zip`，131167515 字节、20 文件，SHA256 `9cf5d9bdbe16e3170a1f925cede87d9a73a1b33993249eaf59c0cf93703bcfb0`。包含新安装器、原签名 BOSS 插件、客户/管理员说明及本次独立验证报告，旧 r9 ZIP 保留。
- 独立验包：从 NSIS 本体只读解出的 3770 个文件与冻结的 `win-unpacked` 路径、大小、SHA256 全部匹配；实际包内 Core 解析行为及源码哈希匹配，BOSS 签名和 3606 个载荷文件匹配现有信任根；实际打包 EXE 隔离 APPDATA 启动/握手/退出通过，Host 锁释放。未真实安装、配对或部署，不把历史 r9 验收记录作为 r10 新结果。
- 最终 ZIP 独立复核通过：CRC、20 个成员的清单路径/大小/哈希及 `SHA256SUMS` 全部匹配，3 份独立报告原样纳入；交付审查无阻断问题。临时解包目录清理被自动审批策略拒绝（未返回具体原因），保留在忽略目录，没有绕过限制。
