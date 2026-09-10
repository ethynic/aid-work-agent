# BOSS 批量读简历 0 份入库且日志无线索事故复盘

- **日期**：2026-09-10（事故发现）/ 2026-09-10（整改完成）
- **环境**：正式环境（agent），客户「国腾」租户设备「曹老师-公司笔记本」（device_id=53575923-5285-4061-8126-675127d559f0，agent-tool-runtime / boss-resume-assistant 0.2.11，均为当时最新）
- **现象**：客户使用 BOSS 直聘筛选简历，智能体执行 `boss_resume_batch` 后页面上简历打开了，但简历库没有任何新增。云端 trace 只有 `RESUME_PAYLOAD_INVALID：CLI 批量结果缺少 resumes 数组或为空`（data=null），客户端诊断包 runtime.log 只有两行 `tool=boss_resume_batch success=true code=OK effect=none`，两侧都看不出失败原因。

## 证据链

1. **客户端契约特性**：`ResumeBatchReader.readBatch` 单卡失败只记 `data.failures` 后继续，全部失败仍返回 `success=true code=OK`（bossResumeBatch.ts），与云端 `BossResumeBatchTool` 的空 resumes 校验（proxy_tool.py）语义冲突。
2. **传输链路无损**：providerManager 透传 structuredContent、invocationRunner 原样转发 `data`、服务端 `write_result` 原样落 JSONB——不存在链路丢数据。
3. **原始表取证**（`local_tool_invocations.result_json->data`，trace 里被工具层置 None 的部分）：当天两次批量 `attempted=5、成功 0、失败 5`，同一批 5 人两次全部失败——4×「点击卡片后简历详情未打开（未出现简历画布）」+ 1×「姓名交叉校验未通过」（能走到姓名校验说明 OCR 本身工作，**OCR 不是主因**）。
4. **历史回溯**：该设备 2026-09-02 已有同模式记录（3 次批量均「成功 1 份失败 4 个」，失败文案相同），成功率 ~20%；`boss_greet`（同款卡片定位点击、打开聊天窗口）一直正常——**慢性问题，非当日回归**，坏的只是「点卡片 → 打开在线简历画布」这一步。

## 根因判断（2026-09-10 二次修订：两个独立 bug）

### Bug B（版本相关）：0.2.11「打开成功却不保存」= 姓名交叉校验正确拦截张冠李戴

数据库实锤：0.2.9 保存的「任玮鹤」简历，OCR 头部明确是**王亦菲**（『王 亦 菲 刚 刚 活 跃 22 岁 大 专…』）；09-02 的「田女士」实为**董云慧**的简历。即：**点击配对姓名与实际打开的详情经常不是同一个人**，旧版（无 P0 交叉校验）把错名数据照存入库；0.2.11 的 P0 交叉校验（卡片姓名必须在 OCR 文本头部命中）拒绝错名入库——不是新版变差，是防护生效。错位点击 + 旧版错名入库 = 客户简历库已被污染（该租户 6 条记录至少 3 条错名）。

### Bug A（版本无关，机器相关）：卡片点击错位——绝对像素校准不适应客户机布局

- 点击几何 `CARD_CLICK_X=600 / CARD_CLICK_DY=70`（按钮 y 固定 +70、主体列固定 x=600）自 2026-08-17 引入后从未改过（git 证实），0.2.9 与 0.2.11 完全相同——错位不是 0.2.11 引入。
- 该值按开发机视口 1249x1277 校准。客户笔记本分辨率/窗口尺寸/浏览器缩放不同 → 卡片行高不同 → y+70 落进行间空隙（点击毫无反应）或错误行（开错人的详情）。开发机是参考布局所以从未复现。
- 佐证：`boss_greet` 点击的是「打招呼」按钮自身中心（坐标跟 DOM 走），在客户机上一直正常；批量读简历点的是固定偏移点，一直只有 ~1/5-1/3 能打开。
- 这是与 2026-09-01 修复的「姓名列绝对像素 x<360」完全同类的坑（0736d220），当时只改了姓名配对，点击点没改。

### Bug A 修复（2026-09-10，已真机验证）

点击策略改为：**只点击配对姓名节点的中心点**（`pairCardNameWithPoint` 返回点位，坐标跟 DOM 走，任何分辨率/缩放/窗口尺寸下恒在卡片行内）。绝对像素主体列点（600/y+70）**整体删除，无兜底**——错误的校准没有兜底价值；姓名配对失败的卡片没有可点点位，不点击直接记 failure 跳过（P0 反正不入库，盲点白读还占 ~30 秒真实鼠标滚动）。每次点击的点位落 `[boss-batch]` 日志，批量开始记录视口尺寸+按钮行距（与参考机 184px 的漂移一眼可见）。

**真机验证（2026-09-10 15:18-15:20，开发机）**：doctor 预检 7/7 卡片配对成功（边海洋、赵杰、李晓宝、何苏、蒋震、邓长江、刘亮）；`resume-batch --limit 3` 用新点击代码 **3/3 全部打开并读取成功，零失败**——边海洋（拼接图 433KB）、赵杰（OCR 639 字，头部「赵杰3日内活跃」）、李晓宝（OCR 7493 字，头部「李晓宝刚刚活跃」），姓名与简历内容逐一人卡相符，交叉校验全过，拼接图按姓名落盘。

## 整改（2026-09-10，已交付）

目标：**同类问题只看客户端 runtime.log + 云端 trace 即可定位，不再翻原始表**；链路上每个节点失败都有日志可指认。

1. 客户端逐卡诊断日志（`[boss-batch]`，stderr → runtime.log）：批量开始/结束摘要（尝试/成功/失败数）、逐卡失败（姓名+原因+画布尺寸现场+点击序列）、逐卡成功（ocr_chars/引擎/段数）。
2. operation 级失败日志（`[boss-op]`）：所有 boss 工具失败原因（code+message 截断 200 字）落 runtime.log，此前只有云端可见。
3. OCR 引擎决策日志（`[boss-ocr]`，每进程一条）：RapidOCR 缺失回退 WinRT 不再无声。
4. 云端 `BossResumeBatchTool` 空 resumes 分支不再把 `data` 置 None：保留 `{attempted, failures 摘要}`（≤5 条、错误文案截断 120 字，纯文本无图片），message 带第一个失败摘要，LLM 与 trace 直接可见。
5. **回传链路日志补盲**：runtime 的 invocation 生命周期行（已领取/已开始执行/终态已回传/回传重试/取消）此前经 cli.ts `onEvent` 只 console.log 到 stdout，服务化启动下文件日志全无——本次诊断包实证该盲区。改走 `logInfo`（stderr+文件双写，前缀格式不变）；终态回传行带 `data_bytes`（证明 data 随终态发出）。
6. **云端落库侧对称留痕**：`/result` 端点写入终态日志带 `tool` + `data_bytes`（与客户端回传行比对，定位丢/裁 data）；`boss_resume_detail`/`boss_resume_batch` 入库成功打印 resume_id/姓名/份数/失败数——「存没存进库」服务端日志直接可答。
7. **点击错位修复**：见根因判断 Bug A 修复——只点姓名节点中心（绝对像素点删除无兜底）+ 点击序列/布局漂移日志；无名卡不点击直接跳过。

## 遗留事项

- **客户机复验**（发新版后）：客户笔记本部署含本次修复的 runtime 后跑一轮批量，确认打开成功率恢复（历史仅 ~1/5-1/3），`[boss-batch]` 的视口/行距/点击序列日志回传核对。
- **客户简历库错名数据清理**：tenant_91c41b1796ad 的 bs_recruiting_operator_resumes 中 id 5、6（任玮鹤←王亦菲内容）、id 1（田女士←董云慧内容）需删除或人工改名；id 2/3/4（求职者）姓名不可信建议一并人工核对。
- 历史批次（0.2.9 及更早）入库的简历记录建议全租户排查：候选姓名与 OCR 文本头部不一致即为错名。
- 产品改进候选：`boss_resume_batch` 对附件简历型候选人给出专属失败文案（引导走 `boss_accept_resume`）；0 份成功时客户端返回 `success=false` 专属错误码（涉及计费语义需单独评估）。
- 观察项：runtime.log 中 runtime 启动行版本号（0.2.8）与实际包版本（0.2.11）不一致、12:42 重启无启动行，agent-tool-runtime 日志埋点待核查（与本次事故无因果）。

## 全链路节点 × 日志对照（整改后）

| 节点 | 客户端 runtime.log | 云端日志/trace |
|------|-------------------|----------------|
| 领取/开始执行 invocation | `[runtime] invocation … 已开始执行 tool=…` | invocation 已创建（enqueue） |
| 卡片定位/点击/画布检测 | `[boss-batch] 卡片[X] 失败：…未出现简历画布（页面 CANVAS 尺寸：…）` | 空 resumes 时 failures 摘要随 data |
| 读取管线（滚动/截图/拼接/OCR） | `[boss-batch] 卡片[X] 失败：读取简历失败：<阶段错误>` | 同上 |
| OCR 引擎选择 | `[boss-ocr] OCR 引擎=rapid/winrt：…` | — |
| 姓名交叉校验 | `[boss-batch] 卡片[X] 失败：姓名交叉校验未通过（ocr_chars=…，引擎=…）` | 同上 |
| operation 失败（任意 boss 工具） | `[boss-op] 失败 run_id=… code=… message=…` | invocation state=failed |
| 工具结果回传 | `[runtime] invocation … 终态已回传 success=… code=… data_bytes=N` | `/result` 写入终态 `data_bytes=N` |
| 云端入库 | — | `boss_resume_batch 入库完成 成功 X/Y 份 resume_ids=[…]` / `boss_resume_detail 入库成功 resume_id=…` |
| 云端入库失败 | — | `第 N 份入库失败 payload 不符契约/数据库或存储异常` |

## 遗留事项

- 现场（客户机器）复测确认附件简历型推断：手动点开失败名单候选人的详情后跑 `resume-detail --candidate-name <姓名> --save-image-to <目录>`，报「未找到简历详情画布」即坐实。
- 产品改进候选：`boss_resume_batch` 对附件简历型候选人给出专属失败文案（引导走 `boss_accept_resume`）；0 份成功时客户端返回 `success=false` 专属错误码（当前与云端契约不一致，涉及计费语义需单独评估）。
- 观察项：runtime.log 中 runtime 启动行版本号（0.2.8）与实际包版本（0.2.11）不一致、12:42 重启无启动行，agent-tool-runtime 日志埋点待核查（与本次事故无因果）。

## 验证

- 客户端 `npm test`：417/417 全绿（含新增画布诊断/日志断言用例）
- runtime `npm test`：96/96 全绿 + typecheck 0 错误（生命周期日志落文件改造）
- 后端 `dev_test.sh`：test_recruiting_resume_tool.py 37/37、test_local_tools_api.py 14/14 全绿
- 独立验证智能体复跑 + diff 审查通过（3 项 P2 已修 2，1 项为既有问题未扩大）
