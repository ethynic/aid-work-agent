# wecom-cli 本地库数据通道（local-db）开发计划

> 设计依据：[wecom-cli 本地库数据通道设计](../design/wecom/wecom-cli-localdb-design.md)（2026-10-14 评审 v5）
> 实验参考：`clients/wecom-cli/experiments/local-db-decrypt/`（算法与密钥扫描可吸收；WAL 重建逻辑按设计 §3.3 重做，不可照搬）
> 开发顺序原则（评审 v2）：先闭合可信快照、密钥复用、账号和会话定位三个正确性/绑定问题，交付 P1 历史读取；再做待回复检测与发送核验；独立采集进程等优化依实测决定。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| P0 | 前置调查（schema / 打包 / 登录证据） | ✅ 完成（2026-10-14，子智能体真机执行） | 水位基元=message_id；send_time=秒；无发送状态字段；群解析经 conversation_table 可行；WAL 旧代尾帧实观命中（4/6 帧）；DPAPI 单文件移植 + pycryptodome 打包仅 1 条 lock 条目 |
| P1a | 组件 C1-C9 + doctor 深检（正确性优先序） | 🔧 进行中 | 组件已实现；本轮身份修复独立测试与 CR 通过（Node 419/419、Python 97 通过/1 跳过）；1.14 真实退出登录、同 PID 切号、锁屏待用户配合 |
| P1b | read-session 本地库通道组合 + 真机读取冒烟 | 📋 待开发 | |
| P2 | 待回复检测 + 发送本地库核验 | 📋 待开发 | |
| P3 | content_type 扩展 + validate + runbook 文档 | 📋 待开发 | |

### P1a 验收记录（2026-10-15）

> **本轮身份修复（2026-10-15，独立测试与 CR 通过）**：CEF 降为候选，删除进程时间/容差/日志身份准入；复用操作主窗口 PID，完整查询其 File 类型磁盘句柄，以唯一普通本地数字账号目录下的 `Data/message.db` 确认目录。无/多账号、权限或枚举失败明确未确认，旧日志不背书；直接证据不受日志缺失/冲突、mtime 或在线时长限制。跨文件候选统一按行事件时间排序。矩阵按主窗口 PID 实际版本准入，真正扫描时才核验其余 PID 同版本，不限制找钥只能在主窗口。静态只读已核对真实主窗 PID 与唯一消息库；开发自测 typecheck/build 通过、定向 Node 83/83、Python 新增 2/2（真实合成 WXWork.exe 同 PID 句柄切换/多账号/释放 + 映像拒绝）。独立验证：`npm test` exit 0，34 文件 419/419 通过、0 跳过（本轮唯一全量 Node 回归）；定向 Node 83/83；Python 全量 98 项、97 通过/1 个非 Windows 分支跳过，exit 0；typecheck/build exit 0；PS1 parser 通过。额外独立合成 WXWork.exe 真实 active-account CLI 4/4（唯一 111→多账号拒绝→同 PID 唯一 222→零句柄拒绝），重复 5 次查询前后测试进程句柄数相同；定向版本探测返回辅助映像实际 AssemblyFileVersion 5.0.11.6029/source=process。主控独立 CR 通过，并亲自通过 typecheck、dist doctor/datadir import 和 PS1 BOM 检查，全程仅评审验证、未修改实现。真实退出登录、同 PID 切号、锁屏仍待用户配合，归 1.14。以下第六/七轮记录为历史验证，原日志身份证据已由本轮替代。

> 收尾执行：1.13 启动清扫接线（doctor 步骤 1 前调用 sweepArtifacts，清扫失败只记日志不失败）完成后全量实跑。真机环境：Windows，企业微信 5.0.12.6018，登录态（WXWork 5 进程）。
> 全量验证：`npm run typecheck` 通过；`npm test` **387/387 全绿**（localdb TS 用例 162 条，其中 doctor 31 条含本次新增启动清扫接线 2 条）；`npm run test:py` **91 条 OK**（1 条为非 win32 平台门禁跳过；含 keyscan 合成进程端到端命中）。
> **第六轮评审修复后复验（2026-10-15）**：8 项修复（身份绑定/EPIPE/清扫存活规则/快照重试 WAL 重判/取消等 close/状态保存失败提示/SCHEMA_UNSUPPORTED 真实协议/测试环境密闭）经独立测试用评审者复现手法逐条复核 8/8 PASS；无 SESSIONNAME 环境多轮全量零失败；终检 typecheck 通过、**npm test 401/401、test:py 96（1 平台跳过）**。
> **登录驱动已知限制（三态验收须覆盖）**：localdb-login.ps1 把托盘隐藏、无可识别主窗口归为 offline——后台/最小化运行场景会被判未登录。该限制须在退出登录、同 PID 切号、锁屏三项验收中一并确认实际表现，不能因移除交互会话门禁就认定已支持后台。
> **第七轮评审修复后复验（2026-10-15）**：3 项遗留（历史 CEF 行背书身份/owner 发布空窗误删/保存失败自称已启用）经独立测试用评审者复现手法逐条复核 3/3 PASS；终检 typecheck 通过、**npm test 410/410**。identity 语义现为「最新绑定事件 + 文件 mtime ≤24h 时效窗」（历史一致行不再背书，最新事件冲突即拒绝）；workdir 改两段发布（暂存名对清扫不可见，rename 原子发布，崩溃泄漏由 pid 死活兜底清扫）；保存失败时报告不含「已启用」表述。
> **第七轮二次意见修复后复验（2026-10-15，身份证据根因）**：identity 语义再重写为「**锚定当前进程实例**」——version.ts 探测全部 WXWork 进程取最早 CreationDate 为实例锚点（失败→null 保守拒绝），datadir 只认**行级时间戳 ≥ 锚点−2min 容差**的会话内最后一条带 account_id 绑定行（无账号行不回退历史绑定），**24h TTL 与全部 mtime 时效逻辑删除**（无关追加/陈旧 mtime 双向均不影响判定，grep 零残留）；doctor 步骤 3 锚点缺失 → process-anchor-unavailable 保守拒绝。评审者复现场景（旧绑定 + 新上下文缺失）翻转为 false 并归因 no-binding-in-current-session；真机冒烟：锚点=主进程创建时刻、绑定行晚 4 秒（浏览器进程初始化写入）、identity_confirmed=true。独立验证 32/32 PASS；终检 typecheck 通过、**npm test 420/420、test:py 96（1 平台跳过）**。

**逐任务状态（1.1–1.14）**

| # | 状态 | 验证证据（一句） |
|---|------|------|
| 1.1 | ✅ | version/running/matrix 三组件 + 矩阵进打包产物（copy-matrix.mjs + dist 优先）；真机冒烟 5.0.12.6018 → unknown 禁止硬扫；三轮评审修复后 253/253 全绿 |
| 1.2 | ✅ | 直接句柄证据优先，CEF 按事件时序只选候选；旧日志无准入权；本轮独立测试与 CR 通过，三态归 1.14 |
| 1.3 | ✅ | fingerprint.ts 库头魔数+明文页头双匹配（页参数漂移可检）+ 可选 exe 常量指纹（默认关闭）；15 用例；真机指纹匹配由批次 4b doctor 步骤 5 实证 |
| 1.4 | ✅ | TS 捕获编排（H0/H1 头比较+基线 stat+边界语义）11 用例 + py 冻结点 1 场景表 22 用例（坏链/撕裂截断/未提交尾帧/错钥等，失败场景全部断言不产生半解密产物）；**场景表全过，生产捕获规则冻结** |
| 1.5 | ✅ | py decrypt（页 1 先知快速失败→逐页解密→有效前缀+提交边界重建 WAL、明文校验和重算→integrity_check）；独立参考实现 ref_encryptor.py（sqlite3mc cipher_wxaes128.c 规格）生成固定向量，6 用例消费独立向量非自往返 |
| 1.6 | ✅ | keyscan 三层验证 13 用例（独立向量先知/对齐预筛+回退纯函数/合成进程端到端）；**接线验证点真机实证（2026-10-15）**：持钥进程恰为 CIM 首选 PID 51788（命中 2.72s/11.26MB，两轮稳定），非持钥 UI 进程 34844 回退全量 153.4s 未命中——doctor 以 detectRunning 全列表候选正确覆盖密钥位置 |
| 1.7 | ✅ | keystore.ts DPAPI 存储/绑定三元组/失效语义 7 用例 + scripts/smoke-dpapi.mjs 真机冒烟（手动）；批次 4b doctor 全链 keystore 复用命中 |
| 1.8 | ✅ | py query 只读 SQL 四任务，契约按 0.1 结论冻结（水位=last_message_id 行、cursor 只认返回集）；合成明文库 50 用例（迟到同步/同刻排序/排除表双向语义/同名消歧） |
| 1.9 | ✅ | C9a extract_content 严格解析+递归兜底标 trusted=false（py 场景表）+ C9b messages.ts 契约归一（P0 最小类型集、非文本占位）+ 游标编解码，12 用例（对 py 输出不信任的显式边界） |
| 1.10 | ✅ | 登录探针复用操作主窗面积选择与并列歧义，返回 window_pid；独立测试与 CR 通过；真实登录态只读核对，三态仍留 1.14 |
| 1.11 | ✅ | 接入主窗 PID 的 active-account 与定向版本核验；未确认不得进入密钥/快照；缓存复用不探测其他候选；独立测试与 CR 通过 |
| 1.12 | ✅ | log.ts 五类事件 JSONL/轮转/整行脱敏 11 用例；真机 localdb.log 实测 mode_decided/keyscan_result 记录正常 |
| 1.13 | ✅ | cleanup.ts sweepArtifacts 9 用例（owner 存活/陈旧挂名/删除失败收集不中断）+ doctor 启动清扫接线 2 条（收尾批次）；真机 artifacts/localdb 全程无残留；取消路径「先终止 py 子进程再删目录」仍留组合任务接线 |
| 1.14 | 🔧 部分 | 自动化项完成：全量三链全绿 + doctor 真机 + keyscan 直测×2（见上/下）；未登录/登录/锁屏三态 + 登录证据三态（进程在已登出、同 PID 切号、锁屏）+ 锁屏本地库只读可用性实证待用户配合 |

**真机实测（2026-10-15，登录态，只读边界遵守）**

- **doctor --localdb**（当前矩阵仅 5.0.11.6029）：步骤 1-3 绿（PID 51788 / 版本 5.0.12.6018 / 账号 1688854831832237 目录命中），止于步骤 4 `ui(compat)`「未知版本禁止硬扫」——本机 5.0.12.6018 不在矩阵属**正确行为**；退出码 0，全程 ~1.8s；localdb-state.json 正确落盘 {mode:ui, kind:compat}。未注入临时矩阵条目（该验证已于批次 4b 完成）。
- **keyscan 真机直测**（绕过 doctor，venv python 直驱，--db 只读库页 1 4KB）：
  - 单 PID 34844（`--switch-account` UI 进程，非持钥）：对齐预筛+全量步长 1 回退共 153.4s 未命中——补 §9.5 回退段成本数据；
  - 全候选 5 PID × 2 次：均命中 PID 51788（`/upgraderun` 进程，恰为 doctor 步骤 1 CIM 首选），aes128，2722ms / 2732ms（波动 0.4%），扫描 11,255,808 字节——命中稳定；key_hex 未记录（stdout 即看即弃，不入任何日志）。
  - 5.0.12.6018 兼容性证据（批次 4b 临时注入实测 + 本机日志在案）：指纹匹配 + 密钥页 1 验证 + 104 页整库及 WAL 全解密 integrity=ok + 查询冒烟通过，doctor 全链 mode=db。
- **残留与状态**：`artifacts/localdb/` 全程为空（启动清扫无对象 + 工作目录 finally 清理 + keyscan 不产生产物）；原始库文件 mtime 未变化（只读保证成立）；localdb-state.json 与 logs/localdb.log 内容符合预期（无 sweep_failed；keyscan 直驱不经 TS 桥、不写日志属预期）。

**冻结点状态**

| 冻结点 | 状态 |
|---|---|
| 1 可信快照生产规则 | ✅ 已冻结（批次场景表全过：正常含旧代尾帧截断/坏校验和链/撕裂截断/未提交尾帧/错钥等，失败场景均不返回半解密产物、不推进水位） |
| 2 P0 结论（水位基元/发送核验语义） | ✅ 已冻结（2026-10-14，见「P0 结论摘要」） |
| 3 P1b 错误分流表 | 📋 待 P1b 任务 2.1 开发前评审冻结 |
| 4 P2 关注集合 schema | 📋 待 P2 任务 3.1 开发前评审冻结 |
| 5 登录判定规则 | 🔧 1.10 原语已交付（四值判定 + 登录态真机冒烟），**三态验证待用户配合后冻结** |

**需用户配合项清单**

1. **登录判定三态验证**（冻结点 5 前置，需用户在真机配合操作）：
   - ① 退出登录但进程仍在：验证 probe 判定（need_login/offline）+ datadir 身份证据仍可读时本地库路径正确拒绝；
   - ② 同 PID 切换账号：验证 keystore binding 失效重扫 + datadir 定位切换到新账号目录；
   - ③ 锁屏三态：登录态探测在锁屏下的表现 + **锁屏下本地库只读可用性实证**（设计 §9.6 理论收益验证）。
2. **矩阵是否加入 5.0.12.6018 条目**：证据已齐（指纹匹配 + keyscan 命中 + 全链解密 integrity ok；批次 4b 临时注入 doctor 全链绿灯），等用户拍板后写入 version-matrix.json 并登记 verified_date，随后重跑 doctor --localdb 复核 mode=db。
3. **提交与分支决定**：当前 `feature/ask-user-question-card` 分支承载全部 localdb 未提交改动，远程 master 已前移；需决定提交拆分与远程整合策略（本收尾任务不提交不 push）。

**已知限制与 P2 备案汇总**（随批次发现累积，长期有效）

| 限制 | 说明 | 处置备案 |
|---|---|---|
| PowerShell 5.1 OEM 代码页解析 | 含中文的 ps1 驱动必须 UTF-8 with BOM 保存，否则 Windows PowerShell 5.1 按 GBK 解析报错（localdb-login.ps1 已带 BOM） | 长期注意：编辑工具链保持 BOM |
| defaultExecFile 等义复制 | running.ts/version.ts 各复制一份 environment.ts 未导出的 defaultExecFile（5s 只读命令超时同语义） | P2 重构：导出共享 helper |
| 矩阵 dist 优先的源码态陈旧性 | loadMatrix 先读 dist 副本（发行布局），改 src 矩阵后未重建时 dist 陈旧 | copy-matrix 已挂 build；维护者须知 |
| cross_check 语义混同 | Config.cfg 不可解析时与「无直记键」不可区分（同落 'no-id-key'），身份契约属设计侧问题 | 设计评审/P2 |
| CEF existsSync 网络路径阻塞 | CEF 命中根上的同步 existsSync 遇死网络路径可能阻塞（本机实测步骤 3 仅 13ms，未复现） | P2：候选根探测异步化 |
| keyscan payload 未区分对齐/回退段 | 返回值只有 scanned_bytes/elapsed_ms，命中无法归因对齐预筛还是全量回退段（§9.5 命中率归因不完整；本机 2.7s 命中量级支持对齐段有效，但无字段证实） | P2：payload 增加段级计数 |
| 实验代码 decrypt_wal 缺陷 | experiments 的 decrypt_wal 未重算明文校验和（与 docstring 不符），正确逻辑在 decrypt_full；工程化已按 §3.3 重做 | 已记录（P0 结论）：实验分支不可照搬 |

## P0：前置调查（真机，产出结论回填设计 §4/§9；结论+证据是后续冻结点依据）

| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| 0.1 | message_table schema 调查 | ✅ 完成（2026-10-14） | `message_id INTEGER PRIMARY KEY AUTOINCREMENT`（=rowid），18 列，sqlite_sequence 存在；196 行 max=197 空洞 1（无害，cursor 按 ≤水位）；message_id 全唯一，与 rowid 序逆序对 0/19110；**send_time=秒**（1.7e9）；同秒 7 组 15 条（sequence 列同秒递增可决胜）；server_id 全局唯一 |
| 0.2 | 迟到同步实证 | ✅ 完成（2026-10-14，库内证据） | 同会话 rowid 序 send_time 回退仅 2 例且均为系统/控制类消息（ct=1017/38/31），陆伟会话 0 例；迟到插入获得更大 message_id → **rowid/message_id 水位天然覆盖迟到同步**；手机端实时落库延迟未测，留待 P1b 联调 |
| 0.3 | 打包与移植依赖验证 | ✅ 完成（2026-10-14） | pyCryptodome 4.0.0 已入本机 venv（子代理安装）；打包态 = `ocr-assets.lock.json` 加 1 条 wheel 条目 + 重跑 prepare-ocr.py（+7.3MB，无门禁冲突）；DPAPI 移植 = 拷 weixin-cli `src/security/dpapi.ts` 单文件（PowerShell ProtectedData，零 npm 依赖，~1.5-2h 含测试） |
| 0.4 | 群会话解析调查 | ✅ 完成（2026-10-14） | session.db `conversation_table`（72 行）含 name/room_info_pb，R: 群名→id 实测唯一命中（name 无唯一索引，需防重）；S: 经 user_table；Y: 应用号 2/17 会话无法解析显示名，显式降级 |
| 0.5 | WAL 旧代尾帧实观 + 登录证据手段 | ✅ 完成（2026-10-14，候选证据调查） | 真实 WAL 6 帧中 4 帧旧代尾帧（旧 salt=新 salt−1），有效前缀 2 帧含 1 提交帧——评审合成场景真实命中，**必须显式按代过滤**；登录证据候选：Config.cfg 登录键（kPollCorpMessageSyncKey 活跃游标/LastStorageBackupTimeKey 当日）+ 文件 mtime + 进程存在，窗口标题无账号信息——**均为辅助信息非硬门禁**（页 1 核验≠当前已登录，评审 v5），判定规则待 1.10 原语 + 三态验证冻结 |

### P0 结论摘要（冻结依据，2026-10-14）

1. **水位基元冻结：`message_id`**（AUTOINCREMENT 主键=入库序；rowid 0 逆序为同字段单调性验证，非写入策略独立证明）。覆盖范围限定：**迟到消息以新记录插入取得更大 message_id 时被覆盖**（不覆盖原地更新，全同步路径行为待 P1b 手机端联调）；cursor 在 message_id 空间（绑定账号/会话/格式版本），**不用 send_time（秒级且系统消息可迟到回退）也不用 sequence 做游标**；同秒展示排序以 server_id/sequence 决胜。
2. **发送核验范围冻结：无发送状态字段**（flag 与方向无关，方向=sender_id 比对；send_failed_unnotified 表存在但恒空）。核验命中=本地已有记录，映射：db 命中 → success/applied + `data.verification='local-record'`；OCR 命中 → applied + `'ocr-bubble'`；均无 → EXECUTION_UNKNOWN。**success/applied 语义=客户端动作已记录并经本地核验，不得被消费侧解释为对方已收到/已读**。
3. **快照协议：普通样本已验证，专项验证待完成**：H0/H1 头比较可行且通过（复制窗口 6ms）；真实 WAL 4/6 帧为旧代尾帧 → §3.3 有效前缀过滤必须显式实现（decrypt_full 靠 SQLite 自行忽略属侥幸语义）；**并发 checkpoint/reset/截断/撕裂场景按 1.4 场景表专项验证后才冻结生产规则（含失败场景不返回不可信结果、不推进水位）**。
4. **DPAPI 移植与打包路径明确**（0.3），依赖声明落点（venv README 惯例 vs requirements）在 1.5/1.6 开发时冻结。
5. **登录判定未证明**：P0 仅完成候选证据调查，Config.cfg/mtime/游标均为辅助信息；判定规则待 1.10 登录探测原语交付 + 三态验证（已登录→退出但进程在、同 PID 切号、锁屏）后冻结。

### 性能实测：单人聊天历史端到端（2026-10-15，真机 5.0.12.6018，3 轮取中位）

| 阶段 | 耗时 |
|---|---|
| 快照复制（db+wal，1.28MB） | 0.88ms |
| 页 1 密钥核验 | 4.41ms |
| 整库解密 + WAL 明文校验和重建（104 页） | 7.11ms |
| 打开 + WAL 回放 + 定位陆伟会话 | 13.50ms |
| 提取该会话全部消息（11 条，含 protobuf 文本） | 0.18ms |
| **端到端合计（缓存密钥复用，0 扫描）** | **27.26ms** |

- 提取吞吐：**4.47ms/千条**（~16µs/条）；外推 1000 条 ≈ 4.5ms、10000 条 ≈ 45ms（仅解析环节）。
- 解密成本随**整库页数**线性增长（~68µs/页），与会话数无关——大库时期单次调用成本仍为亚秒级（万页 ≈ 0.7s）。
- 冷路径（现场扫描）本轮未复测，历史值 266s @5.0.11。
- **5.0.12.6018 兼容性证据**：库头指纹匹配 + 昨日密钥页 1 验证通过 + 104 页整库及全部 WAL 帧解密成功 + integrity=ok——现有 wxaes128 方案在升级后实际有效，升级未改加密方案与主密钥。按 runbook 尚差 doctor 深检/动态冒烟正式流程，矩阵加条目前补齐。
- 实验代码缺陷记录：`decrypt_wxaes128.decrypt_wal` 函数体未重算明文校验和（与 docstring 不符，SQLite 会拒绝回放），正确逻辑在 decrypt_full——工程化按 §3.3 重做，实验分支不可照搬。
- 环境事实：message.db 内无 user_table（在 user.db），会话定位走 conversation_id LIKE 路线（已计入 13.5ms）。

## P1a：提供者层组件 + doctor 深检（正确性优先序）

| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| 1.1 | C1/C2/C4：安装检测、运行检测、矩阵加载 | ✅ 完成（2026-10-14/15，三智能体流程 + 两轮代码评审修复） | version/running/matrix + version-matrix.json + 单测；真机冒烟：进程 executable 实测 5.0.12.6018（已自动升级，非矩阵版本 → unknown 禁止硬扫，设计预期成立）；首轮 CR 修复原型键穿透；**外部评审修复轮**：矩阵进打包产物（copy-matrix.mjs + dist 优先加载 + 发行布局冒烟）、版本来源区分（进程/安装目录，防残留版本冒充准入）、矩阵/状态契约收紧（schema 门槛 + validated 必需字段 + mode/kind 枚举）、tasklist 探测状态 probe 字段、PID 严格解析；复审修 version 探针载荷断言脱钉；**修订版评审修复轮**：validated 字段类型/值校验（非空串 + page_size 正整数，6 复现值全降级）、ui 状态 kind 必填契约（类型收窄 + 非法组合 → null）、防冒充用例前提钉死（完整合法条目 + 前置 validated 断言）、发行布局素材改用 dist 产物（脚本失效测试变红实测）；终检 typecheck 通过、253/253 全绿 |
| 1.2 | C3：数据目录定位 + 账号↔目录交叉核验 | ✅ 完成（2026-10-15，本轮独立测试与 CR 通过） | 主窗口 PID 的完整磁盘句柄枚举确认唯一普通本地 `Data/message.db`；历史 CEF/Config.cfg/mtime 不作确认依据，CEF 候选按事件时间统一排序；开发自测 datadir 25 项通过，真实切号/登出转移仍待 1.14 |
| 1.3 | C5：指纹预检 | ✅ 完成（2026-10-15） | fingerprint.ts：库头魔数+明文页头双匹配（页参数漂移可检）+ 可选 exe 常量指纹（默认关闭，runbook/P3 显式启用）；15 用例；真机指纹匹配由批次 4b doctor 步骤 5 实证 |
| 1.4 | **可信快照协议**（py + TS 编排，设计 §3.3） | ✅ 完成（2026-10-15） | snapshot.ts 捕获编排（H0/H1 头比较+基线 stat+失败显式 reason）11 用例 + py snapshot-verify/decrypt 场景表 22 用例（坏链/撕裂截断/未提交尾帧/错钥等，失败场景全部断言不产生半解密产物）；**冻结点 1 场景表全过，生产捕获规则已冻结** |
| 1.5 | C7：解密 + 独立来源固定测试向量 | ✅ 完成（2026-10-15） | py decrypt：页 1 先知快速失败→逐页解密→有效前缀+提交边界重建 WAL（明文校验和重算，实验 decrypt_wal 缺陷不照搬）→integrity_check；独立参考实现 ref_encryptor.py 按 sqlite3mc cipher_wxaes128.c 规格生成固定向量，test_independent_vectors 6 用例消费独立向量（非自往返，含错钥快速失败与 fixture 自检） |
| 1.6 | C6：keyscan（去 psutil、对齐预筛+回退） | ✅ 完成（2026-10-15） | test_keyscan 13 用例三层（独立向量先知/对齐预筛+回退纯函数/合成进程端到端命中）；**接线验证点真机实证（收尾批次）**：持钥进程恰为 CIM 首选 PID（对齐命中 2.72s/11.26MB 两轮稳定），非持钥 UI 进程回退全量 153.4s 未命中——doctor 以 detectRunning 全列表为候选正确覆盖实际密钥位置 |
| 1.7 | C6：DPAPI keystore + 生命周期（设计 §3.4） | ✅ 完成（2026-10-15） | keystore.ts：DPAPI(CurrentUser) 存储 {binding 三元组, key_blob, saved_at}，binding 不匹配顺带清除；**仅页 1 核验明确失败才删密钥**（doctor 编排侧）；7 用例 + scripts/smoke-dpapi.mjs 真机冒烟（手动）；批次 4b doctor 全链 keystore 复用命中 |
| 1.8 | C8：读库查询（conversations/messages/last-per-conv/resolve） | ✅ 完成（2026-10-15） | py query 只读 SQL（uri ro），契约按 0.1 结论冻结（水位=last_message_id 行、cursor 只认返回集）；合成明文库 50 用例（含迟到同步、同刻排序、排除表双向语义、同名消歧、来源库缺失降级） |
| 1.9 | C9a/C9b：内容提取（带 trusted 标注）+ 契约归一 | ✅ 完成（2026-10-15） | C9a extract_content 严格解析+递归兜底标 trusted=false（py 场景表）；C9b messages.ts 契约归一（P0 最小类型集、非文本占位 kind 省略）+ 游标编解码 v1（encodeURIComponent 转义、绑定 account/conv 校验），12 用例（对 py 输出不信任的显式边界） |
| 1.10 | 轻量登录探测原语（从 probe 抽取登录判定核心，不带交互桌面门禁） | ✅ 完成（2026-10-15，本轮独立测试与 CR 通过） | 原有只读登录谓词保持，主窗口 PID 按操作定位面积最大规则选择，最大并列不给 PID；无新增交互门禁，托盘隐藏限制与三态验收仍未关闭 |
| 1.11 | doctor 深检（随组件增量点亮，设计 §5） | ✅ 完成（2026-10-15，本轮独立测试与 CR 通过） | 步骤 3 查询主窗 PID 唯一账号库；步骤 4 定向探测同一 PID 的实际版本；需要扫描时才验证额外候选同版本。任一身份失败禁止密钥与快照，开发自测 doctor/login/version/datadir 共 83 项通过 |
| 1.12 | 结构化日志 logs/localdb.log + redaction 接入 | ✅ 完成（2026-10-15） | log.ts logLocaldbEvent（五类事件 JSONL/ISO UTC/未知 type 拒写）+ >5MB 轮转保留 .1 一个历史（轮转失败不阻断写入）+ 整行 redactSensitive；redaction.ts 扩展 32-hex 密钥样式串兜底（设计 §8，先于手机号规则防 hex 内数字段被改写）；deps 注入 fs/clock/dir；11 用例 |
| 1.13 | 遗留产物清扫（owner 存活校验）+ 删除失败报告 | ✅ 完成（2026-10-15） | cleanup.ts sweepArtifacts：owner.json 缺失/坏 JSON/pid 非法或死亡 → 删；存活未超时 → 跳过；存活但 started_at >24h → 删（陈旧挂名）；存活但时间不可判 → 保守跳过；删除失败收集 {dir,error} 不抛不中断；只动 base 直接子目录；9 用例；取消路径的「先终止 py 子进程再删目录」顺序由组合任务按 driver.ts kill 语义接线；**收尾批次接线：doctor 启动清扫已接（步骤 1 前调用，失败只记 db_fallback(cleanup/sweep_failed) 日志不阻断），2 用例** |
| 1.14 | P1a 验收：doctor 全链真机绿灯 | 🔧 部分（2026-10-15，自动化项完成） | 全量三链实跑全绿（typecheck / npm test 387/387 / test:py 91 含 1 平台跳过）+ doctor 真机止于步骤 4 ui(compat)（5.0.12.6018 不在矩阵，正确行为）+ keyscan 直测×2 稳定命中 ~2.7s（详见「P1a 验收记录（2026-10-15）」）；未登录/登录/锁屏三态 + 登录证据三态（进程在已登出、同 PID 切号、锁屏）+ 锁屏下本地库只读可用性实证待用户配合 |

## P1b：read-session 本地库通道组合

| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| 2.1 | read-session operation 接入本地库通道（channel 值域 + fallback_reason + 安全网预检） | ☐ 未开始 | 按 §2 状态转移表与**错误分流表**实现（分流表为冻结点 3：本任务前评审冻结）；取消/参数/游标/账号/目标歧义不兜底；SNAPSHOT_UNSTABLE retryable 映射 |
| 2.2 | 游标增量（limit=100 默认 + cursor 绑定） | ☐ 未开始 | 按 0.1 冻结的水位基元实现；limit 只推进到已返回消息；CURSOR_UNSUPPORTED 结构化失败 |
| 2.3 | target_name/target_ref → conversation_id 解析 + 同名消歧 + ref 账号绑定 | ☐ 未开始 | TARGET_AMBIGUOUS 附候选；新签发 ref 携带账号；**legacy ref 在 DB 路径一律 TARGET_REF_STALE 不执行读取**（评审 v4 #35）；既有 UI 路径保留 legacy 兼容 |
| 2.4 | mode=unset / ui(compat/transient) / db 行为接线 + hint | ☐ 未开始 | unset 不阻断 UI 能力 + 引导 hint；ui(transient) 负缓存到期单次探测与运行时提升；契约增量（msg_id/parse_trusted/cursor）；**评审 v6 追加：ui(transient) 运行时提升 db 前必须要求 identity_confirmed=true（reason=identity-unconfirmed 期间禁止提升）** |
| 2.5 | 真机读取冒烟 + 整链实测 | ☐ 未开始 | 与 UI 通道结果比对（同会话同消息）；耗时/CPU/磁盘实测（回填设计 §9）。**前置基线已测（2026-10-15，见「性能实测」节）：热路径端到端 27.26ms（11 条会话），P1b 验收时对比工程化实现与 UI 通道** |

## P2：待回复检测 + 发送本地库核验

| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| 3.1 | unread_list 本地库通道（待回复 = 最后一条**可参与回复判定**的消息发送方为对方） | ☐ 未开始 | **系统/控制消息最小过滤**（已确认类型不参与最后发言方计算：不制造也不清除待回复；验证两向语义）；judgment_basis 标注；UI 兜底保留（快照语义）；**关注集合 schema 冻结点 4**：实现前评审冻结（关注身份=建立时一次性解析为 {account, conversation_id}；未传=全部/空数组=INVALID_ARGUMENT/单目标失败部分返回；**新增关注初始化=一次待回复快照检查**，归属调用方流程） |
| 3.2 | watch_poll 本地库通道 + 关注集合（水位增量独立于待回复判定） | ☐ 未开始 | 轮询只认已解析会话身份（不复用短期 ref/不每轮 search）；返回全部新消息 + 最后发言方辅助字段；失败项不推进其水位；不可信快照不推进水位；**DB 不可用时报不可用保留水位，不自动落 UI**；消费侧规则（恢复后核对最新发言方/已回复状态）随本任务交付并与 Runner 消费流程对齐；群会话按 0.4 结论；CLI 参数/MCP schema/旧字段兼容 |
| 3.3 | **watch 持锁修正**：按轮获取/释放既有互斥（CLI 与 MCP 轮询路径锁入口一并核对对齐） | ☐ 未开始 | 解除"监控进程堵死 send/search"（watch.ts 实证全程持锁）；**集成验证：watch 运行期间完成一次 send**；"DB 绕过 UI 锁"延后（共享状态读改写需串行协调，另立专项），不引入读写锁/多采集器 |
| 3.4 | 发送核验接线调整（PS 发送/核验分离 + TS 编排） | ☐ 未开始 | 发送前基线记录（规避秒精度）→ 发送后匹配新记录；db 核验优先 → OCR 兜底；仅可信正文参与；时间单位按 0.1 结论；PS 超时/取消不重入发送阶段；仅入队证据时结构化体现未确认（不靠文案）；**success/applied 语义=客户端动作已记录并经本地核验，不得解释为对方已收到**；双不足保持 EXECUTION_UNKNOWN 不自动重发 |
| 3.5 | toolDefs 描述更新（待回复检测为主要用途 + 通道语义） | ☐ 未开始 | unread/watch/read-session/message-send 四处 |

## P3：扩展与维护工具

| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| 4.1 | content_type 全量语义表（实测样本归纳） | ☐ 未开始 | 从本机各库采样标注；占位升级为结构化表示 |
| 4.2 | validate 动词（静态/常量/动态三级复检） | ☐ 未开始 | 复用 doctor 深检管线；维护者版本复检入口 |
| 4.3 | 新版本接入 runbook 文档 | ☐ 未开始 | 逆向流程操作手册（EVIDENCE + iterations 决策树沉淀） |
| 4.4 | 独立采集进程等优化评估 | ☐ 未开始 | 依 P1b/P2 实测数据决定是否立项 |

## 已知限制（长期有效）

- 仅限本机当前登录账号自己的数据；未登录无法提取密钥
- 版本强绑定：任何升级都可能使方案失效——以矩阵/doctor 显式呈现，禁止静默硬扫
- v1 仅文本消息库内解析；非文本占位
