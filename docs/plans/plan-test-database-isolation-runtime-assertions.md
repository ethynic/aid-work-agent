# 开发计划：测试入口隔离与 Runtime 陈旧断言

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| 设计 | 默认无DB、显式本机隔离库及清理证明 | ✅ 完成（2026-10-26） | 明确旧入口风险、网络前拒绝与真实SQL验收；不改产品连接策略 |
| 实现 | Python测试入口/驱动guard与Runtime三项断言 | ✅ 完成（2026-10-26） | Python6文件冻结；无DB25+28subtests、私有PG28+28subtests自测通过，Runtime22通过 |
| TEST | 独立子进程安全反证及真实私有PG回归 | ✅ 完成（2026-10-26） | Runtime22、无DB21+29subtests、私有PG20与独立入口/故障反证通过 |
| CR | 独立审查并修复到通过 | ✅ 完成（2026-10-26） | 创建资格时点P2已红绿闭环，最终无未解决P0/P1/P2 |
| 本地常驻测试 PG | Compose 一键运行真实 SQL 测试，明确本地业务库联调边界 | ✅ 完成（2026-11-17） | WSL2 真实 SQL 17 项通过，独立验证冷启动、复用、工作区挂载及失败清理 |
| 本地开发环境复用 | 首次建库、Python 自测、前端依赖缓存和智能体入口 | ✅ 完成（2026-11-17） | WSL2 验证 API 和 Runner 启动、三类自测、Web 构建和依赖复用 |
| 指定工作区挂载 | 从目标 worktree 自测或在原 Compose 项目切换应用源码 | ✅ 完成（2026-11-17） | WSL2 三类自测、API / Runner 切换及恢复通过，PG / Redis 未重建 |

设计：[测试数据库隔离](../design/devtools/test-database-isolation-design.md)。工作树C:/repos/aid-work-agent-wecom，分支feature/ask-user-question-card，基线349a10ab。原clients/wecom-cli/package-lock.json三peer生成改动保留。用户仅授权第1项修复，无提交、部署、客户端安装或恢复监听授权。

## 实施与责任

采用项目dev-workflow高风险流程。Python开发角色唯一维护tests/conftest.py、scripts/dev_test_isolated_db.py、必要共享纯测试guard模块及其Python测试，必要时scripts/dev_test.sh的安全参数转发；主控维护clients/agent-tool-runtime/tests/providers.test.ts及文档，生产Runtime白名单/digest算法不改。稳定后TEST与CR只读独立验证，发现问题回到原开发角色修复，禁止共同写文件。

先登记设计→Python与TS分域开发、自测→冻结→独立TEST/CR→必要修复与受影响复验→更新本计划和索引。完整设计及验收条件见设计文档；不扩整个单测框架、模型网络策略、scheduler或业务模块。所有真实PG使用任务专属本机helper和随机aid_test库；在guard尚未生效时只能纯unittest/网络禁止的子进程或显式任务PG入口自测，不运行默认共享库入口。

## 结果与边界

Runtime断言已实现：生产manifest与摘要函数未改；预期BOSS25含只读probe、Provider精确5项，当前摘要另固定，4个历史摘要通过仅投影后增probe继续校验。主控Runtime/core/native构建及providers/productTrust/capabilities22项通过，独立测试同22项通过；独立CR源审及providers7项通过。CR首次误用未安装tsx启动失败，改仓库已构建JS后通过，不计产品失败、不新增依赖。

Python6文件已冻结：bootstrap纯unittest9通过；新无DB正式dev_test.sh入口25通过、28subtests通过，2个既有BOSS注册差异精确deselected；本任务私有PG随机库28通过、28subtests通过（新实际SQL2、原service storage17、guard9）。覆盖真实conftest/实际SkillLoader内置6个DB初始化与池的底层tripwire0，不用禁Python socket冒充libpq无连接；无DB环境未读业务.env。Python聚合`8a4062ebbb4df63f6abeb27a0653c15be3800f456016b89e86f9cfbf01e06145`，正式独测/CR进行中。

Docker未运行真实daemon，已用GitBash+fakeDocker验证源hash、仅换行兼容、实际代码变化拒绝、零pytest/零Redishelper及环境转发。初次启动误选Windows WSL bash，无stublog，改明确GitBash后通过，不算产品故障。不向旧容器拷贝文件、重启或部署。私有PG沿既有EDB缓存，bootstrap只在任务Temp wrapper精确省略3条不相关vector扩展/表/索引DDL，其余原SQL照常，pgvector未验。

W9的历史不确定批次不因此变成已证明安全；本轮不读取业务库核查，不修改部署环境或发布包。旧2个Python BOSS注册差异不在本次3项Runtime断言修复范围，不借此改生产工具权限。

## 最终独立结果（2026-10-26）

Runtime独立providers/productTrust/capabilities22通过、0失败/跳过；源审及providers7项复核通过，生产Runtime/core/src无差异。当前摘要固定ef0b8cf5...d16644，4个历史摘要原值保留，原3项Runtime陈旧断言均转绿，不以修改生产白名单掩盖失败。

Python正式无DB独立pytest21通过、29subtests通过；官方bootstrap9方法在初冻通过，最终同模块含新增故障子场景已由正式pytest复验。独立原生入口6场景与连接矩阵20子场景通过：无DSN/真实复制路径的匿名恶意.env、显式不安全目标/kwargs合并、晚写PG重定向、日志逃逸、实际Pool与真实SkillLoader合成init均在C/libpq之前被拒；合成init的entered标记证明真实importlib执行，底层connect/SQL仍0。合法参数矩阵只有2项到达假底层，不连接业务库。

真实本机私有PG20项通过：新runner/业务与trace同精确随机库、正常结束及失败的owned finally DROP；实际root sessionfinish删除自有canary tenant/关联record，在DROP前原生SQL核对0行，DROP后核对库不存在；注入另一个自有私有库时仅允许current_database证明，tenant SELECT/DELETE均0、canary保留。CREATE碰撞不删除已有库，canary37保持。

独立CR发现并修复一项P2：CREATE execute成功后cursor退出异常时，原created标记在with外导致漏删自建库。仅将created=True移至execute成功后仍在with内，不重试未知CREATE/不扫库。正式无DBaffected回归9通过、29subtests通过，官方纯unittest9通过；初次新增断言误把schema注释CREATE DATABASE计为第二次，改仅统计真正管理DDL，未改行为凑绿。独测精确还原并核freeze1 runner SHA后用真实PG注入close故障，旧0DROP且残库（finally人工清理）、新精准1DROP且无残库；CREATE自身失败仍0DROP，管理连接均关闭。独立CR同源2个Mock红绿场景通过。

Shell独立4场景通过：LF/CRLF一致接受、代码内容变化拒绝且无pytest/helper、normal/isolated分别保留带空格参数并透传exit37、管理DSN只走环境键而非argv；实际GitBash宿主无DB入口6项通过。真实Docker daemon未启动，这些是fakeDocker进程边界测试；不将其称作实际容器运行验收。pgvector三DDL省略边界保持。

最终12文件freeze2聚合927004888c66bcfc7512ef3eaa17747d10810592e611243533aed81b81333b5e全匹配；Python6文件聚合549ca55507a475933dbf931152607011531b21d2f25a454e013871c3e9ce2182；独立完整CR无未解决P0/P1/P2。最终仅追加验收文档/移动索引，源码不再变化。主控核Python AST/Bash语法及guard只读source-hashes入口，原package-lock三peer哈希不变；HEAD349a10ab，无暂存、提交、推送、部署、客户端安装或群聊监听恢复。

已核0 aid_test数据库/连接及本批自有Python/Node/Bash进程，主控按任务meta/root/data/pid精确关闭61295 helper，端口0监听，未动其他服务；测试二进制缓存保留。修复已完成开发，实际Docker环境及未来发行验收沿原后续计划执行。

独立完整命令、源码哈希、故障红绿与计数重叠说明：`%TEMP%/test-db-safety-independent-acfe7563/independent-w9-final-evidence.json`。各批方法/子测试与单独探针可能重叠，不另相加成虚构总数。

## 本地常驻测试 PG 验收（2026-11-17）

在 WSL2 执行 `docker compose -f docker-compose.test-db.yml run --rm -T sql tests/integration/agent_runner_service/test_storage.py -p no:cacheprovider -q`，17 项通过。纯 SQL 入口禁用 Redis，消除读取本地配置后等待 Redis 的重试。工况验证确认本地 API 实际连接 `aid_wecom_local`。专用测试实例处于健康状态，复用时无需修改应用工作区或连接配置。

独立验证使用另一 Compose 项目确认空环境自动建卷并启动、网络只有回环接口、当前工作区挂载正确。实测一项 PostgreSQL 队列并发测试通过。pytest 返回预期错误码 4 后，PG 实例未重建，且无残留临时库。独立只读评审无阻断。其他平台、Docker 引擎重启以及进程被强制终止后的清理未实测。

## 本地开发环境复用验收（2026-11-17）

在基于 `origin/master` 的独立工作树中，用已有应用镜像启动本地数据覆盖。PostgreSQL 的 vector 扩展、Redis PING、API `/health` 和 Runner API、worker 容器健康检查通过。手册记录默认视频供应商的启动配置，可用非凭据的占位值核验本地启动。

在 WSL2 按手册执行 Compose 命令，Python 11 项、真实 PostgreSQL 17 项和 Web 8 项通过，Web build 及产物检查通过。前端首次安装依赖，后续测试、构建和独立 Vite 检查命中同一卷，未重复安装。独立验收 Vite HTTP 200、源码挂载与旧 Compose 兼容。另运行的 `routes.test.ts` 有 1 项既有快照差异，当前改动与基线的前端源码相同。Docker Desktop 和原生 Linux 未实机验证。

## 指定工作区挂载验收（2026-11-17）

在 WSL2 用 `AID_LOCAL_SOURCE_DIR` 指向未含新 Compose 入口的 `6bc4aebf` worktree。Python 11 项、真实 SQL 队列测试 1 项和 Web 8 项通过。独立验证一次性容器中的测试文件哈希与目标工作区一致，并确认 Runner 的配置和 Skills 路径随目标切换。

同一验证项目里按手册切换并恢复 API、Runner API、worker 和 monitor。两次实际容器挂载均匹配预期，使用原环境 `.env`，四个服务健康。PostgreSQL 与 Redis 容器 ID 全程一致。Docker Desktop 及 API、Runner 分属不同项目时的实际切换未实机验证。
