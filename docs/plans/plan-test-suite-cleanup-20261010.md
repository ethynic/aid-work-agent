# 测试有效性审计与清理

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 全仓测试清单与当前实现核对 | ✅ 完成（2026-10-10） | 同步 025bc6c8 后重新核对 720 个后端测试文件及前端、客户端入口 |
| Phase 2 | 删除无效测试、修正仍有价值的失真测试 | ✅ 完成（2026-10-10） | 删除 5 个遗留文件和重复占位用例；迁移当前行为测试、修复夹具与默认收集 |
| Phase 3 | 定向回归、独立测试和 CodeReview | ✅ 完成（2026-10-10） | 后端 240、前端 86、BOSS 20 通过；全量收集通过，独立 CR 无 P1/P2；未提交或部署 |
| Phase 4 | 二轮复查剩余重复、自证及过时测试 | ✅ 完成（2026-10-10） | 删除 26 用例；独立 177 passed/7 平台 skip；recap 泄漏复验通过，CR 无未关闭 P1/P2 |
| Phase 5 | 第三轮低价值和过度设计测试治理 | ✅ 完成（2026-10-10） | 删除 38 弱用例、合并净减 21；独立后端 690/前端 76 通过，类型检查通过，CR 无 P1/P2 |

## 范围与判定

审计 `tests/`、`frontend/`、`clients/` 和 `experiments/` 的测试入口与实现关系。按用户要求先同步最新代码：`master` 从 `12c358ca` 快进 107 个提交至 `025bc6c8`，与 `origin/master` 一致。同步前逐文件备份本任务修改，只恢复本任务已修改路径，合并后按最新实现重新应用；没有全量 stash、覆盖用户改动或自动提交。

最新基线共 720 个 `tests/**/test_*.py` 文件；静态测试函数计数为 unit 8082、integration 1127、e2e 6。第一轮后 718 个文件，分别为 8086、1114、6；二轮后文件数不变，unit 下调到 8067；三轮后 unit 为 8016，integration/e2e 不变（不含 fixture，参数化后 pytest 收集数不同）。检查同名覆盖、语法、失效 src 导入及完全相同 AST 函数体。前端和客户端按既有测试入口、组件交互、协议、安全及恢复契约核对。

不以文件年代、历史阶段命名、mock、静态检查、无直接 assert、本机缺依赖或外部服务跳过作为单独删除理由。fixture、带断言的 helper、不同参数化数据、不同渠道实现与真实安全边界均保留。风险按跨模块测试治理处理，开发、独立测试、独立 CodeReview 分开执行。

## 删除清单及覆盖去向

| 对象 | 依据 | 保留覆盖 |
|------|------|---------|
| integration/test_scenario_14_15.py、test_scenario_16.py、test_scenario_17.py | 无 pytest 用例和断言，收集阶段初始化 PG，手工 LLM 分析/打印报告 | unit/tools/test_analysis_agent.py、test_data_analyzer.py |
| integration/test_credentials_endpoints.py | `src.db.remote_credential`、RemoteCredentialDB 已不存在，SMB/FTP 凭据功能无当前实现 | 现有 EmailCredentialDB 与 archive credential codec 独立保留；不声称替代已移除功能 |
| integration/test_customer_endpoints.py | import 路径、函数名与返回结构均失效，旧 fixture 创建实际用户/会话 | 改为 unit/skills/test_trade_customer_manager.py 调用当前脚本；仅 mock 外部 DB 边界 |
| unit/test_knowledge_api_download.py 跨租户拒绝重复项 | 同 patch、同输入、同断言 | 保留 foreign_tenant_doc_with_tenant_context |
| unit/test_llm_failover.py 熔断未超时重复项 | 同 fixture、同输入、同断言 | 保留 open_state_blocks_attempts |
| unit/tools/test_sms_skill_cli.py bypass 非六位重复项 | 同 patch、同 fixture、同 QBTEST 输入 | 保留 verify_bypass |
| unit/services/x_to_image/test_browser_pool.py 截图占位类 | 只有无条件 skip，未实现测试 | 真实截图测试继续由 real_browser 套件提供 |
| unit/api/test_subagent_extra.py 存在性占位及两项实现文本检查 | 占位不验证行为；新版 extra_md 已由 PromptSources 负责 | 同文件实际 PromptSources 参数、strip、miss 与异常行为 |
| unit/test_mid_term/test_agent_integration_real.py 导入存在性检查 | 仅 hasattr / iscoroutinefunction | 同文件压缩后重载历史及 test_compress_session.py；保留上游已改成行为验证的调用顺序用例 |
| unit/tools/browser/test_phase3_human_control.py 两项包装用例 | 仅再次调用已独立收集的 engine acceptance 测试，无新增断言或浏览器接线 | 保留原文件 unknown external effects 等待、兄弟任务暂停恢复的三个行为用例 |
| frontend JsonNode 两项按钮存在性检查 | 后续点击复制用例已查找同一按钮、触发点击并校验内容 | Object/String 实际复制及阻止冒泡测试 |
| BOSS detail-greet-executor.test.ts 视口常量断言 | 仅验证 fixture 常量，无执行器行为 | 保留 20 个执行器行为用例 |

## 保留并修正

- LLM 告警发送/禁用测试：增加发送次数、消息参数与未发送断言，防止异常吞掉后虚假通过。
- TextRenderer 转义：调用真实 render 并捕获浏览器边界接收的 HTML，避免在测试内部先转义造成自证。
- Agent extra_md：以最新 PromptSources 实际行为替换旧 Agent/resolver 实现字符串锁定。
- ContextAssembler：补齐附件边界的实际二元返回值，保留上游压缩先于历史重载的行为断言并更新名称。
- SMS CLI：隔离已 mock 下游逻辑前的数据库池初始化，避免单测额外尝试连接 PG。
- E2E LLM 凭证检查：按 gateway 当前 provider 和启用的备用链配置判定，移除旧 API_KEYS 注入和重复门禁；只处理 e2e 项，新增离线门禁回归。
- 搜索 E2E：使用当前 max_results 参数并检查实际结果结构。
- agentIcons 分布：固定样本取代随机样本，保留大样本分布检查。
- 前端冒烟用例名称与实际弹窗标题断言一致，不冒称覆盖平台管理员列头。
- integration conftest 移除指向已不存在手工脚本的 collect_ignore。
- root conftest：collect-only 不再执行测试租户数据库清理；正常测试仍保留前后清理。
- weixin_marketing 增加包初始化文件：消除其与 session_tasks 的 test_lifecycle 同名导入冲突，保留两套生命周期测试。
- Runtime 4 个测试文件加入 Vitest 默认 desktop 收集，原有 52 个行为用例随普通 npm test 执行。
- Office 预览：入库四个有效 OOXML 小夹具（约 144 KiB），改为文件相对路径；生成器默认只生成小夹具，超大手工样例显式 opt-in；真实 PPT 依赖预热 hook 单独允许 30 秒，普通用例超时与断言不变。
- 当前 customer_manager 增加 29 个离线用例，验证真实函数、字段映射、user_id SQL 条件及参数、提交、空结果、筛选、限额和六条异常路径。只 mock 数据库边界，不声称验证数据库权限或约束。

## 第一轮验证与限制

使用项目 venv 的 Python 3.12、Git Bash 的 `scripts/dev_test.sh` 统一入口，指定 `DATABASE_URL=postgresql://127.0.0.1:1/test_suite_cleanup?connect_timeout=1` 和 `PYTHONUTF8=1`，防止连接共享数据库。补装 requirements-test 已声明的 psutil；前端按上游 lock 执行 npm ci。没有修改生产 `src/`、依赖清单或 lock。

| 验证 | 命令与范围 | 实际结果 |
|------|------------|---------|
| 主控定向回归 | `./scripts/dev_test.sh`：Agent 集成、human control、engine acceptance、knowledge download、SMS、browser pool、compress session；附 `-p no:cacheprovider -q --tb=no --disable-warnings` | 130 passed，退出 0；10 个既有 warning |
| 独立后端测试 | 同入口：subagent extra、LLM failover、TextRenderer、E2E collection gate、customer_manager | 110 passed，退出 0；4 个既有 Pydantic warning |
| 独立前端测试 | 普通 `npm test --`：JsonNode、agentIcons、Office gaps、Runtime 四文件；另执行 Office main 文件 | 64 + 22 passed，0 skipped；包含 Runtime 52 个及真实 Excel/PPT 解析渲染用例 |
| 独立 BOSS 验证 | `npm run build:main`、`npm run typecheck`、`node --test dist/tests/detail-greet-executor.test.js`，禁用自动 Chrome | 编译、类型检查退出 0；20 passed |
| 全量后端收集 | `./scripts/dev_test.sh --collect-only -p no:cacheprovider -q --tb=no --disable-warnings` | 9988/10095 tests collected，107 deselected，退出 0 |
| 收集无副作用 | 独立提取实际 sessionstart/finish hook，以 mock 清理函数执行两种分支 | collectonly 不调用清理；正常运行保留两次调用 |
| 独立 CodeReview | 删除覆盖去向、DB mock 边界、门禁、Runtime 收集、OOXML 夹具与最终 hook/path 差异 | 客户 SQL 条件断言问题已修复；最终无 P1/P2 或必要覆盖缺失 |
| 差异检查 | `git diff --check`、生产代码路径核对 | 通过；生产代码无修改 |

第一轮执行的后端 240、前端 86、BOSS 20 个用例共 346 个，另有全量收集验证；未执行全部 9988 个后端用例，也未生成全仓覆盖率报告。DOCX 渲染器为明确 mock，只验证组件调用和缩放；不声称验证真实 Word 排版。未调用真实 LLM、邮件、搜索、Chrome/Chromium、PostgreSQL 或部署环境，真实 SQL 约束和多租户权限仍由现有集成测试承担。删除根据实现与重复证据决定，未以数量减少作为目标；不改产品行为，未提交或部署。

## 二轮复查（2026-10-10）

在第一轮修改基础上再次进行静态扫描及独立实现核对。共删除 26 个用例：后端 19、前端 5、客户端 2；另把 CDP 占位用例替换为两个真实行为用例。不同渠道、不同外部边界和有实际职责的结构守卫继续保留，不把相同 AST 文本直接当作可删除依据。

| 对象 | 删除依据与保留覆盖 |
|------|--------------------|
| services/test_recap_runner.py 旧派发用例（1） | 无条件要求 create_task 且未控制 Redis；当前实现优先入队。保留 test_recap_runner.py 显式入队成功与降级用例 |
| test_compression_result_contract.py 字段存在项（1） | trigger_reason 已包含在同文件完整字段集合断言中；类型和默认值另有用例 |
| test_token_cache_update.py / test_token_cache_mutation.py（3） | 两个零行 False 和一个普通 float 转 int 路径，被 mutation 文件的 rowcount 0/1 类型与值矩阵、float 转换完整覆盖；保留 channel、数值字符串与大值用例 |
| test_agent_image_event_loop.py（6） | 非 dict / 空 dict、placement 缺失 / 空串 / 空列表 / 原地修改被 agent_events 同输入更强断言覆盖，函数 re-export 身份守卫保留；组合提取与特殊无效项仍保留 |
| test_agent_integration.py / test_agent_reload_memory.py（3） | 默认来源、record 异常降级、摘要读取异常与已保留真实身份解析和更强历史组装用例重复 |
| archive/test_native_sdk.py SDK 文件存在项（1） | 缺文件 skip、存在即通过，没有失败路径；实际加载签名和缺失错误路径保留 |
| x_to_image/test_browser_pool.py（3） | bool、重复不抛和方法存在弱项，被同实例缓存一致性、新实例缓存、真实方法签名及 context manager 调用覆盖 |
| x_to_image/test_service.py 未启动浏览器项（1） | 原 shoot 抛 AssertionError 会被 convert 吞掉，无法验证未调用；将 spy.assert_not_called 合并到保留的真实不支持类型结果用例 |
| Office truncateSheetRange（1） | 同 range(0,999)/1000 重复；保留同时断言不截断、总行数、引用保持的用例 |
| rendererSetup / contractHarness 两个文件（3） | 只测试自建 DOM 和本地 String.trim，无产品消费者；真实 DesktopApp、登录页、Shell 挂载测试保留 |
| shell.test.ts JSON 视口项（1） | 仅把无人消费的 JSON 与固定字面量比较，未验证布局；保留实际组件最小视口导航、状态与焦点断言 |
| ChromeLauncher 端口已通项（1） | 被同文件同参数的所有依赖均未触碰用例完整覆盖 |
| 微信 replay 全局 ID 唯一项（1） | 先 Set 去重再比较 Set 大小与数组长度恒真；保留生产 schema、观察验证、分类及错误水位负例。重放允许再次出现历史消息，不新增不正确的跨步唯一限制 |

二轮同时修正：

- Agent 结构守卫按当前 facade 425 行下调旧 4026 行上限，恢复防结构倒退作用。
- CDP 用例真正调用生产 getTargets → attachToRecommendPage → attachToTarget，验证跳过非 page / 非 BOSS 目标、请求参数和 session；无目标时拒绝且不 attach。仅 mock fetch/socket 协议边界。
- Chrome 超时测试按生产 sleep 参数推进 Node test context 的虚拟 Date.now，保留超时、派发次数和轮询断言。主控实测从约 15000 ms 降到 1.52 ms；独立实测 1.70 ms，结束自动恢复时钟。
- recap 创建任务 mock 显式关闭未调度协程，并逐例隔离 runner._background_tasks 和 background_runner._recap_bg_tasks，避免协程 warning 和 fake task 污染后续 shutdown。
- x_to_image 真实 Chromium 集成模块补 real_browser 标记，七个有效管线用例默认分离，显式选择仍能完整收集；未删除真实截图覆盖。
- 清除删除后无引用的 helper/import，并校正旧 Phase 1/3、Agent 与数据库接口范围注释。

| 二轮验证 | 实际结果 |
|----------|---------|
| 主控 14 个后端文件 | 131 passed、7 skipped，退出 0；修改 recap mock 后相关 34 个复跑通过 |
| 独立同范围最终后端验证 | 131 passed、7 native SDK 平台 skipped，退出 0；8 个既有 warning；使用与第一轮相同隔离环境和入口 |
| 独立 frontend 普通入口 | Office 工具、shell、DesktopApp、loginPage 四文件，26 passed；默认 web/desktop 项目及真实组件挂载正常 |
| 独立 BOSS | typecheck 通过、复用最终 build:main；ChromeLauncher/CDP 两文件 16 passed，0 skipped |
| 独立 Weixin | build:main、typecheck 通过；session-observer-replay 4 passed，0 skipped |
| 最终全量收集 | 9962/10076 tests collected，114 deselected，退出 0；七个 real_browser 集成用例 opt-in collect 全部保留，退出 0 |
| 最终 recap 泄漏复验 | 两文件 34 passed，RuntimeWarning / PytestUnraisableExceptionWarning 提升 error，退出 0；两个实际隔离 fixture 在假模块边界的 12 项隔离/恢复断言通过 |
| 独立 CR | consumer 任务集合未隔离的 P2 已修复并复核关闭，最终无未关闭 P1/P2；26 项删除数量与覆盖去向已独立核对 |
| 最终差异检查 | git diff --check 与修改 Python 文件语法检查通过；生产代码无修改 |

独立二轮共 177 passed、7 skipped（额外 recap 复跑不重复计数）。Windows 下 Linux 原生 .so 的七个用例未执行，保留测试并如实报告；没有验证真实浏览器、输入、外部 API 或数据库，也未运行全仓用例或重新生成全仓覆盖率。第一轮无受影响变化的结果复用，不叠加数量或反复跑相同检查。

## 三轮低价值与过度拆分复查

本轮先核对远程新增提交，`master` 从 `025bc6c8` 安全快进至 `61fbc597`，仅涉及部署配置与更新脚本，与本任务已有修改无交集；没有执行部署脚本。本轮修改前逐文件快照保存到系统临时目录，独立审查据此区分本轮与前两轮的差异。

删除 38 个低价值用例（后端 28、前端 9、Runtime 1）；迁移有效断言、合并重复构造后再减少 21 个收集用例。后端静态函数减少 51，因结果默认值与异常检查参数化保留两组输入，实际收集净减 49。前端删除两个仅导入的文件，后端测试文件总数仍为 718。没有更改生产实现、依赖清单或锁文件。

| 清理对象 | 依据及保留覆盖 |
|----------|----------------|
| auth 模块 imports、long_term_memory 无旧文件（2） | 两个实际认证调用、已有缺文件模板测试覆盖相同条件；保留安全与旧记忆迁移用例 |
| 压缩摘要 spy、compressed_message_ids_collected（2） | 前者只看调用次数、不验证顺序；后者未断言任何 ID。同模块实际两轮 prompt 注入与完整 INSERT/commit 流程更强；不将旧弱项冒称消息 ID 覆盖 |
| verbose 状态伪 processor 重试、重复 frozen、重复 Web metadata 三项（5） | 同一个 state 调用两次不等于重跑 processor；保留真正重试场景、配置冻结契约及 web_stream 的完整字段、streamed、去重和异常输入断言 |
| 九个独立工具 name、两个 InputModel 存在/身份（11） | 同文件公开工具 schema 已检查准确名称、必填字段，真实参数校验和执行保留；不锁等价模型的具体类身份 |
| browser/service 单例、注册私有结构与 INFO 文案、重复注册键/渲染类身份（5） | 保留实际 register→render→finalize 分派；默认三种类型名称映射移入普通 service 单测，不依赖 real_browser 门禁；真实截图用例继续 opt-in 保留 |
| 工具发现名称清单、线索工具内部 catalog、报价 generate hasattr（3） | 保留真实实例名与目录键一致、完整黄金清单注册；已有实际 generate 测试补 internal_data 的 items、hotel_stays、人数和内部不含 file_path 的断言 |
| 前端 API auth/session 导入 smoke（4） | 只检查 typeof，session 还容许缺失导出直接跳过；未执行请求。移除它们不代表补齐 auth/session HTTP 行为覆盖，原有缺口仍在；保留 knowledge/localTools 实际 HTTP 与租户头回归 |
| Office 容器/docx 调用弱项、超限 10.1/20.1MB 重复（5） | 保留真实 Excel 表格与切换、docx 缩放/调用参数、上限与上限+1字节、实际点击与错误降级；共享 Word 夹具减少重复读取 |
| Runtime 直接请求 FakeCloud 验证自建幂等（1） | 只验证模拟服务自己写的终态逻辑；保留真实 Runtime claim→started→progress→result，以及后端真实 repository 终态短路和不重复计费 |

合并有效断言而非取消契约：

- Token 更新五个拆分 SQL/事务项并入两个成功行为用例，保留正确表、排除错表、参数化、精确参数和 commit。边界转换、零值、异常和无匹配返回仍保留。
- CompressionResult 三个碎片项并入字段集合、类型和默认值检查，保留 provider/model、trigger_reason 的完整契约。
- x_to_image 模型默认构造与枚举取值合并，保留所有原有字段断言、自定义 extra、可变对象不共享；成功/失败默认结果仍各执行一次。该组净减 13 个收集用例。
- 两种渲染器异常改为同一强测试参数化，RuntimeError/ValueError 均验证返回失败及准确 error，无用例输入损失。

修复两个失真 mock：搜索测试原来接受任意非 None 失败字典，且 patch 未被调用的符号。本轮仅模拟假配置及实际 SDK 导入边界，调用真实 execute/解析，断言成功、results/count/answer 和 SDK 参数。认证 verify_token 已迁服务层，原测试 patch API 模块导致真实数据库访问；改为服务层 DB/cache 边界，并检查真实查询和 token 实参。

| 三轮验证 | 实际结果 |
|----------|---------|
| 主控后端自测 | 初次 654 passed、1 failed；定位认证旧 mock 目标后修复，受影响两个认证用例复跑通过 |
| 独立后端最终回归 | 快照中 27 文件加 web_stream、真实终态与计费三文件：690 passed、12 deselected、退出 0；17 个既有 warning |
| 独立前端相关回归 | Office 面板、下载卡片及相邻组件/API 共 6 文件：76 passed、0 skipped、退出 0；共享夹具微调后面板 20 用例复跑通过，npm run typecheck 通过，不重复计数 |
| 主控 Runtime | 按既有锁文件恢复缺失本机依赖后 build:main 通过；真实协议用例 1 passed、退出 0；全部使用本地 FakeCloud/Provider，无真实桌面输入 |
| 全量收集 | 9916/10027 tests collected，111 deselected，退出 0；未执行全仓或真实外部服务 |
| 独立 CR | 核对本轮 32 文件，关键断言迁移完整，无新增 P1/P2；明确记录前端 API 原有 HTTP 覆盖缺口 |
| 差异与语法 | 27 个本轮 Python 文件语法、git diff --check 通过；生产代码、依赖清单与 staged 路径均无任务改动 |

不将减少用例数量等同于覆盖率提高，也不将符号存在检查称为业务覆盖。没有全仓覆盖率数据或全仓执行耗时对比；真实浏览器、真实数据库与外部 API 未执行。Word 渲染器保留既有 mock 边界，下载点击出现两条 jsdom 未实现原生导航诊断，不代表真实浏览器下载验收。没有执行部署。

## 提交前核对

2026-10-10 用户授权提交。当前分支 `master`，upstream 为 `origin/master`；提交前 `git fetch origin` 成功，双方均为 `61fbc597`，无需再次整合代码。只提交本任务测试、夹具和登记文档，复用对应最终代码状态的上述验证结果；提交后推送到已确认的 upstream，不执行部署。
