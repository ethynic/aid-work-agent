# C0：端侧会话任务基线与 Provider 契约冻结（fake 路线）

> 日期：2026-09-12 · 阶段：C0（[开发计划](../../../docs/plans/desktop-automation/plan-edge-session-task.md) §3 / [设计](../../design/desktop-automation/edge-session-task-design.md) 含 §13 澄清）
>
> 启动决定（设计 §13.1）：**C0 fake 契约基线 → C1–C4 fake 开发**。真机条件与授权未确认，本报告所有真机项登记 BLOCKED；不把 fake 结果写成真机通过。

## 1. 环境与硬件现状

测量机（当前开发机，**不自动视为已授权测试设备**）：

| 项 | 值 |
|---|---|
| CPU / 内存 | 13th Gen Intel Core i9-13905H / 31.7 GB |
| OS | Windows 11 专业版 Insider Preview 10.0.26220 |
| GPU | RTX 4060 Laptop + Iris Xe（OCR 走 onnxruntime CPU，未用 GPU） |
| OCR 栈 | repo `venv`：onnxruntime 1.20.1 + RapidOCR（rapidocr_onnxruntime）+ Pillow 12.1.1 |
| 微信客户端 | 最近已知 4.1.11.24（M0 冻结记录 2026-08-11，**当前未核验**，真机 BLOCKED） |

模型位置：经服务端 LLM 网关（Qwen/ZhipuAI 等，见 AGENTS.md）；端侧无供应商密钥、无直连授权。**C0 未做模型延迟实测**（deferred：C3 fake 决策 worker 注入可配置延迟，C5 按同条件真测）。

## 2. 已测性能数据（fake/离线，原始数据）

### 2.1 OCR 引擎冷/热（合成截图 2100×1400，不碰真实微信）

**可复现材料（C0 评审修订 P2-4）**：脚本 [scripts/ocr_engine_bench.py](../../../clients/weixin-cli/scripts/ocr_engine_bench.py)，参数固定（`--cold 3 --hot 30`，图 2100×1400、18 个会话条目块，确定性生成）；分位数算法 p50=statistics.median、p95/max=nearest-rank（sorted[ceil(0.95n)−1]），逐样本数据见下。运行方式：仓库根 `venv/Scripts/python.exe clients/weixin-cli/scripts/ocr_engine_bench.py --cold 3 --hot 30`。C5 用同脚本同参数对照。

冷启动（新进程，分阶段，3 次）：

| # | import_ms | init_ms（构造 RapidOCR） | first_ocr_ms | total_ms |
|---|---|---|---|---|
| 1 | 708.0 | 433.8 | 2783.3 | 3925.0 |
| 2 | 657.3 | 394.5 | 2756.9 | 3808.8 |
| 3 | 626.2 | 394.5 | 2698.8 | 3719.5 |

热路径（同进程复用 engine，无预热，n=30）：p50=1017.6 ms，p95=1622.6 ms，max=2832.5 ms。逐样本（ms）：
2832.5, 1130.8, 1066.4, 1071.2, 1205.3, 1173.9, 1622.6, 1228.9, 1112.1, 1015.8, 1005.5, 1017.8, 1028.2, 1017.5, 939.4, 977.6, 1017.0, 1017.1, 1049.6, 963.6, 951.9, 966.6, 1025.1, 1068.8, 931.3, 972.4, 946.2, 902.4, 1093.8, 906.8（首样本 2832.5 为预热开销，如实保留）。

结论（引擎级，真机端到端仍 BLOCKED）：

1. 现状 `unread-list.ps1` 每次调用 spawn Python + 构造 RapidOCR：**进程冷启动合计 ~3.7–3.9s/次，其中 import ≈0.6–0.7s、引擎构造 ≈0.4s、首次 OCR ≈2.7–2.8s**——设计 §6「必须常驻 OCR 引擎」有原始数据支撑（首次 OCR 高于热路径 p50，含模型首跑预热）。常驻化后单次观察的引擎地板 ≈1s（全窗口图）。
2. 合成图与真实截图的检测框数量不同，绝对值仅供 fake 重放基线用；C5 真机同条件复测后替换。
3. 待验证假设（C2）：先裁剪目标区域再 OCR 可低于全图 ~1s；未验证前不承诺。

### 2.2 未测路径（BLOCKED / deferred 登记）

| 路径 | 状态 | 原因 |
|---|---|---|
| PrintWindow 截图、未读发现→深读端到端（真实微信） | BLOCKED | 无授权测试设备/会话（§13.1） |
| 模型决策 p50/p95 | deferred | 无本地直连；C3 fake 注入，C5 真测 |
| write-authorize 许可往返 | deferred | 需 C1 fake server fixture，C2/C3 测 |
| 桌面锁等待/发送/写后验证 | BLOCKED（真机）/ fake 可测（C2） | 同上 |

### 2.3 用户转述数据 → 待测假设（不作为事实）

「2–4 分钟/轮」「5–10 秒检测」「1–3 秒推理」「短句 100%」均为待测假设；C5 性能分解按 §12 的 `peer_message_visible→detected→batch_ready→decision_ready→lock_acquired→authorized→submitted→verified` 逐段出具真机数据后方可下结论。**微信营销 P0 验收（账号/群身份、相同文字新增证据、图片 probe、平台基线）经核对未执行**：`docs/research/weixin-cli/` 内 p1–p3 为 M2/M3 既有操作链（chat_search/message_send/history_read）的真机 probe 报告，不包含观察水位、连续窗口、单聊身份证据；营销 P0 计划中的「哈尼」群单条试发从未执行（2026-08-27 M2 真机闭环的既有发送记录不构成 P0 观察证据），微信 P0 真机门禁仍属未完成。

## 3. `session_observer_v1` 契约冻结

冻结位置（代码即契约，实现于 C2，先冻结后实现）：

- `clients/weixin-cli/src/platform/sessionObserver.ts` — schema + 语义校验 + 协议级分类器
- `clients/weixin-cli/tests/session-observer-contract.test.ts` — 契约测试 + 单步语料（16 用例，含跨会话/绑定版本漂移）
- `clients/weixin-cli/tests/fixtures/sessionObserverReplay.ts` + `tests/session-observer-replay.test.ts` — 有序重放语料与水位链测试（5 用例，含 2 个失败后篡改水位负例）
- 测试命令与环境见 §6（本机 2026-09-12 全套件 115/115；另见设计者环境 106/109 的既有环境差异说明）

要点（与设计 §6 逐条对应；含 **2026-09-12 评审修订**：空基线水位、原文保留、增量语义、协议级分类器）：

| 冻结项 | 内容 |
|---|---|
| 能力名 | `session_observer_v1`（manifest 协商用） |
| 请求 | `{conversation_binding_id, binding_version, account_identity_version, watermark}`；`watermark=null` 建基线；`{last_local_message_id: null, window_fingerprint}` 表示**已建立的空基线**（增量对齐，首条入站不当历史跳过） |
| 结果最小字段 | `observation_id, account_identity_version, conversation_binding_id, binding_version, observed_at, coverage, ordered_messages[], window_fingerprint, gap_reason`；消息含 `sender=peer\|self\|system\|unknown, text, local_message_id, source_evidence_ref` |
| coverage 语义 | `complete_window\|gap\|unavailable`，仅指与上次水位连续对齐的窗口，不承诺全量 |
| 消息增量语义 | 水位非 null 时 `ordered_messages` 只含新于水位的消息（空=无新消息）；建基线时含整个对齐基线窗口（历史锚点，不触发回复） |
| 文本原文 | `text` 校验不变换文本（首尾空白保留，用于对齐/比较/证据引用），仅拒绝纯空白 |
| 不变量 | gap/unavailable 必须空消息且 fingerprint=null；complete_window 必有 fingerprint 且禁 sender=unknown、禁重复 local_message_id；引擎失败=unavailable 不返回空列表冒充 |
| gap 原因集 | `sender_ambiguous, alignment_broken, scroll_discontinuity, duplicate_unalignable, layout_changed, viewport_out_of_range` |
| unavailable 原因集 | `engine_unavailable, ocr_failed, window_missing, viewport_unreachable, account_identity_changed`（两集互斥，新值需升能力版本） |
| 消息 ID | `m-<uuid>` 本地分配；去重靠连续对齐（绑定版本+sender+序列/相邻窗口+重复项序号），禁正文 hash/OCR 行号/未读数/时间戳 |
| 协议级分类器 | `classifySessionObservation(request, observation)`：先校验**三项身份字段**（`conversation_binding_id`/`binding_version`/`account_identity_version`）——任一不符返回 `binding_mismatch`/`identity_drift`，窗口不可信、不推进水位（评审二轮 P2-1）> `gap`/`unavailable` > `baseline`（建基线，historical_count，不触发回复）> `new_messages`（水位推进）/`no_change`（锚点不变、指纹刷新）；失败分支一律原样保留进入时水位 |
| 操作错误码 | 复用既有 ErrorCode 子集（`SESSION_OBSERVER_ERROR_CODES`，17 个，模块导出+编译期锁定）；可恢复环境/引擎失败走 coverage=unavailable 而非错误码 |

### 3.1 fake 重放语料（已冻结于测试）

**单步契约语料**（`tests/session-observer-contract.test.ts`）：基线（水位空）、增量对齐、**相同文本×10**（10 个不同 ID，禁正文去重）、拆行重建（重建后单条）、突发 3 条同观察（Runtime 侧按静默 2s/最长 10s 合批）、sender 不明→gap、引擎不可用→unavailable、视口外→gap、原文保留（`"  keep spaces  "` 不被 trim）、空基线→首条入站→重启恢复链路、身份漂移优先于 coverage。

**有序重放语料**（C0 评审修订 P2-2/P2-3，`tests/fixtures/sessionObserverReplay.ts` + `tests/session-observer-replay.test.ts`）：8 场景 12 步，每步含请求（前后水位）、响应（观察结果或错误码）与预期协议处置。场景显式声明 `initialWatermark`，失败分支（error/gap/unavailable/漂移/串扰）**原样保留进入时水位**；测试逐场景校验水位链一致性（rebaseline 显式豁免）与分类器处置，并含「失败后篡改请求水位必须被链路检查拒绝」的两个负例（评审二轮 P2-2）：

| 场景 | 预期处置（flags 供 C2 对账） |
|---|---|
| 空基线→首条入站→重启恢复 | baseline/new_messages/no_change；首条不被当历史，重放不重复接纳 |
| 人工回复（用户接管） | new_messages + `manual_intervention`（Runtime 对照发送账本 → human_required） |
| 人工已读（角标不可信） | new_messages + `badge_cleared_irrelevant`（水位与角标无关） |
| 当前打开会话 | new_messages + `currently_open_deep_read`（不依赖角标） |
| 离线→重启断层→显式重建基线 | unavailable → gap（阻断）→ baseline + `no_backlog_reply`（积压不自动回复） |
| 焦点抢占中断观察 | error `FOREGROUND_LOST` + `watermark_not_advanced` |
| 账号身份漂移 | identity_drift + `task_blocked/rebind_required` |
| 跨会话观察串扰 | binding_mismatch（请求会话 A 返回会话 B）+ `task_blocked/protocol_violation`；绑定版本漂移用例见契约测试 |

真机待补（BLOCKED，真实性不因语料存在而成立）：真实微信下的同场景重放（锁屏/真实断线/真实焦点抢占/真实身份漂移），C5 用本语料结构补真机样本。

## 4. 身份与覆盖矩阵（当前状态）

| 能力 | 现状证据 | 结论 |
|---|---|---|
| 账号身份验证 | M0 冻结的主/插件窗口身份联合校验（路径+类名+标题）；probe 报告进程/登录态 | 窗口身份有离线证据；**账号级身份版本机制不存在**，C1 按 §13.3 pending 骨架建 |
| 群聊目标唯一性 | chat_search 唯一候选 + target_ref（HMAC 5min）；群绑定机制在 weixin_marketing | 代码能力存在；长期绑定→observer 复验链路未建（C1/C2） |
| 单聊目标唯一性 | 同名好友无消歧机制 | **无证据，能力保持关闭**；真机验证前不开启单聊自动发送 |
| 连续窗口对齐/稳定消息 ID | 不存在（unread_list 无水位） | 契约已冻结（§3）；实现与真机证据属 C2/C5 |
| 写后新增气泡证据 | message_send 有截图校验（v1）+ 底座 v2 effect/phase 链 | v2 链有代码；真机证据 BLOCKED |

停止条件评估：OCR 能否可靠判 sender/连续窗口、身份是否唯一——**均无真机证据**，按 §13.1 不进入任何目标类型的自动发送实测；fake 执行器开发继续。

## 5. 模块工作量清单（C1–C5 估算输入）

| 阶段 | 主要模块 | 规模粗估（待 C1 细化） |
|---|---|---|
| C1 | `src/session_tasks/`（表/API/租约/事件/预算/确认）+ `src/weixin_conversation/`（绑定骨架/schema 校验）+ DDL 双轨 + 配置门控 | 最大单阶段：9 张系统表 + 2 张业务表 + 11 个用户端点 + 5 个设备端点 |
| C2 | Runtime `src/sessionTasks/`（JSONL 日志/DPAPI/就绪队列/同步/恢复）+ weixin-cli 常驻 OCR/observer 实现（按 §3 契约） | Runtime 与 Provider 两侧；契约测试已就位 |
| C3 | 决策 worker（DB 槽位/受限输出/完成判定）+ execution_lane/定向 claim + 计费预留结算 | 高并发正确性集中区；必测矩阵 13 项 |
| C4 | 前端 `frontend/web/components/sessionTasks/`（挂 weixin-marketing 子路由）+ prepare/publish/manage 工具 | 页面 + 工具确认链（confirmation_id） |
| C5 | 回归 + 故障矩阵 + 真机/性能 + 安装回滚 | 真机授权为前置 |

复用现状（已核对）：底座 P1–P5 代码（subjects/occurrences/runs/deliveries/outbox/quota、permits/pricing、write-authorize、journal/resultOutbox/dpapi、pollLoop）齐备；Runtime claim 长轮询 20s + 心跳 5s + 退避 1s→30s；v2 写链 = 锁内许可→journal fsync→执行→outbox（invocationRunner.ts 头注与实现一致）。

## 6. C0 验收自检

- [x] OCR 冷/热引擎基线有原始数据（§2.1，分阶段+逐样本，可复现脚本入库）
- [x] fake 重放语料冻结且可回归（§3.1：单步 16 用例 + 有序重放 5 用例，含评审二轮修订场景与负例）
- [x] `session_observer_v1` schema 与错误码冻结为接口/contract test（§3，含空基线水位/原文保留/增量语义/三项身份字段分类器）
- [x] 身份/覆盖矩阵出具，无证据能力全部标记关闭（§4）
- [x] 真机项全部 BLOCKED 登记，未冒充通过（§2.2/§4）
- [x] 未向服务端承诺完整未读全量（coverage 语义冻结）
- [ ] 真机 ≥30 样本与端到端分段耗时：BLOCKED，待用户补齐 §13.1 清单后补测
- [ ] 相同正文新增气泡真机证据：BLOCKED，同上

### 6.1 测试命令与环境（C0 评审补充）

- 命令：`cd clients/weixin-cli && npm test`（先 `build:main` 编译再运行 node:test 套件）。
- 本机（C0 开发机，Win11 10.0.26220 交互会话，i9-13905H）：2026-09-12 评审二轮修订后 **118/118 通过**（97 既有 + 21 新增）。
- 设计者复核环境（2026-09-12 评审轮）：修订前快照 106/109，3 个失败集中在**既有** doctor/probe 对 Windows 交互环境的成功断言（环境相关，非 C0 引入；C0 新增用例 12/12 通过）。交互环境差异会影响 doctor/probe 断言，后续引用套件结果须注明环境。

## 7. C1 放行输入

C0 fake 交付完成：契约冻结 + fake 语料 + 引擎基线。C1 可开始（设计歧义已由 §13 回填）；C1 的 fake server fixture 是 C2 输入。真机补测不阻塞 C1–C4，但 C5 门禁（A9/A10/A11）在真机证据缺席时只能报 BLOCKED。
