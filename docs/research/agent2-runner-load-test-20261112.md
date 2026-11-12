# agent2 Runner 轻重负载分离压测报告（2026-11-12）

> 验证对象：[Runner 容量与轻重负载分离设计](../system/runner-capacity-and-load-separation-design.md) 在 agent2 测试环境的部署效果。
> 部署配置：2 个轻槽 × 槽内并发 4 = 8 个轻并发执行体；3 个通用槽 × 1 并发 = 3 个通用执行体。
> 压测方法：复用 2026-11-06 agent3 压测脚本，适配 agent2 后为 `scripts/agent2_runner_load.py`（提交入库）。
> 证据目录：`docs/research/evidence/agent2-load-*.json`（6 轮采样）。

## 一、结论摘要

| 设计验收项（§5/§6） | 结果 |
|---|---|
| 轻任务排队秒级领取（p95≤30s） | ✅ 全轮 max accept 1.26s |
| A 方案轻池槽内并发（单容器多执行体） | ✅ 实证单轻容器最多 3 个执行体并发 |
| heavy 不落轻池 | ✅ 全部轮次 heavy/light worker 集合无交集 |
| 运行时重判（light→heavy 翻转就地继续） | ✅ 3 个 Excel 任务翻转，按设计不迁移 |
| 看门狗防事件循环停跳（§6.2） | ✅ 真实卡死场景下 15s 阈值触发、回收 attempt、容器自动重启恢复 |
| 轻容器 cgroup 内存 2g（§6.3） | ❌ **未落实，仍为 1GiB**，Excel 任务内存顶死（failcnt 273k） |
| DB 连接 ≤100 | ✅ 峰值约 60 |
| P0② 心跳锁误判再现 | ✅ 未再现（今晚窗口 0 次 LockNotAvailable/interrupted 误判） |

**两个新发现的问题**（详见第五节）：① 轻容器 1GiB + excel_process 长同步处理叠加，触发事件循环停跳与看门狗中断（设计内防护链工作正常，但暴露假异步缺陷与内存门禁缺失）；② travel-consultant 因技能白名单含 paddleocr-doc-parsing 入队即判 heavy，纯文本问答占用重池。

## 二、环境与轮次

- 服务器 124.222.3.254（agent2 测试栈），`docker ps`：light-4 / light-5（cgroup 1GiB）、general-1/2/3（1.5GiB）、monitor（256MiB）、aid-agent-api2、aid-postgres（4GiB）。
- 判型配置：`heavy_tools = excel_process / paddleocr_doc_parsing / image_render / analyze_data / upload_data_file`；`heavy_skills = interior-render / paddleocr-doc-parsing`。
- 测试租户（tenant_type=test）：`tenant_d6c6ed2aadd6`（数据分析）、`tenant_1dc997a1806b`（问答/旅行）。
- 用例：main_short / main_reasoning / main_long（main，入队恒 light）、travel_short（travel-consultant，入队 heavy）、data_trend / data_quality（data-analysis，入队 heavy）、excel_range_full / excel_range_chunks（main + 1M 行 Excel 附件，入队 light、运行时重判）。

轮次：

| 轮 | 时间 | 用例×并发 | 证据文件 |
|---|---|---|---|
| 冒烟 | 19:07 | main_short + travel_short ×2 | agent2-load-smoke-20261112.json |
| 9 轻并发 | 19:08 | main_short/reasoning/long/travel ×9 | agent2-load-light9-20261112.json |
| 8 同型 | 19:10 | main_reasoning ×8 | agent2-load-light8-same-20261112.json |
| 8 长文本 | 19:12 | main_long ×8 | agent2-load-light8-long-20261112.json |
| 数据准备 | 19:13 | data_trend ×1 | agent2-load-dataprep-20261112.json |
| 重池占满混合 | 19:16 | 4 heavy + 3 light ×7 | agent2-load-mixed-heavysat-20261112.json |
| Excel 重判专项 | 19:24 | excel_range_full×2 + excel_range_chunks + main_long ×4 | （见 §6.3，运行中断归因见 §5.1） |

## 三、各轮结果

### 3.1 排队与耗时

| 轮 | n | accept max | elapsed 范围 | 状态 |
|---|---|---|---|---|
| 冒烟 | 2 | 0.76s | 9.8~22.2s | 全部 completed |
| 9 轻并发 | 9 | 1.01s | 7.2~34.7s | 全部 completed |
| 8 同型 | 8 | 0.71s | 6.8~10.1s | 全部 completed |
| 8 长文本 | 8 | 0.66s | 9.8~22.3s | 全部 completed |
| 数据准备 | 1 | 0.20s | 165.1s | completed |
| 重池占满 | 7 | 0.47s | 12.7~360.1s | 全部 completed |

排队目标 p95≤30s 远超达标。8 个任务同发时 8 个执行体容量下基本零排队（accept 均 <0.8s）。

### 3.2 槽内并发实证（A 方案核心验收）

运行中采样（worker_id 尾 12 位 → 容器映射：44782295cc41=light-4、72a305b1=light-5、2c455f4b=general-1、cc6ae1c9=general-2、10de409f=general-3）：

- **light8-long 轮**：light-4 同容器同时服务 3 个执行体（aa8efd66/ab221e81/defa9d51），light-5 同容器 2 个 —— 槽内并发 4 生效。
- **light9 轮**：9 个轻任务同时入队，被 5 个 worker 领取（light-4×1、light-5×2、general-1×1、general-2×1、general-3×1）——轻任务可被空闲通用池协同领取，符合设计的非对称双池语义（限制只在 heavy 不落轻池）。
- **重池占满轮**：4 个 heavy 占据 general-1/2/3（general-2 上 2 个先后接力），3 个轻任务全部由 light-5 同容器 3 执行体并发承接；heavy 与 light 的 worker 集合交集为空。
- 1 个 heavy（data_quality）排队 122.53s 等槽后由释放的通用槽领取——重任务排队可容忍语义符合预期。

### 3.3 判型核验

- main 系用例全部入队 light、travel_short/data_trend/data_quality 入队 heavy，与 `classify_profile` 预期一致。
- **travel-consultant 入队即 heavy**（§5.2）：其技能白名单含 paddleocr-doc-parsing（heavy 技能），纯文本旅行问答也占用重池。本轮 travel_short 22~26s 完成，暂未造成重池挤压，但在重池业务高峰会挤占 Excel/OCR 类真重任务。建议复核该 SUBAGENT.md 技能白名单（如确认 OCR 仅偶发，可考虑把技能移出白名单改按需 use_skill，或接受判重代价并登记）。
- **运行时重判 3 例**：excel_range_full ×2 与 excel_range_chunks 在 light 池被领取后调用 excel_process，`save_checkpoint` 事务内 load_class light→heavy 翻转。2 个 excel_range_full 在通用池释放后正常完成（36~40s）；excel_range_chunks 按设计**就地继续**（不迁移到通用池），最终在轻容器上触发事件循环停跳（§5.1）。误判降级路径本身工作正常，代价由 §5.1 两项缺陷放大。

## 四、资源水位

- **DB 连接**：峰值约 60（≤100 达标），监测期内无连接池等待告警。
- **通用池内存**：excel_range_full 在 general 槽内存峰值约 700MiB / 1.5GiB，安全。
- **轻容器内存（异常）**：excel_range_chunks 在 light-4 执行时容器内存 301MiB → 1024MiB 顶死 cgroup 上限，`memory.events` failcnt=273,028、oom_kill=0（靠页缓存回收硬扛），worker 进程 RSS 1.01GiB，宿主可用内存一度仅 913MiB。设计 §6.3 要求 A 方案下轻容器 cgroup 升 2g，**部署时未落实**。

## 五、发现的问题

### 5.1 P0：轻容器 1GiB + excel_process 长同步处理 → 事件循环停跳、看门狗中断（本轮最重要发现）

**时间线**（runner 1d347501，excel_range_chunks，1M 行 Excel 分块读取）：

1. 19:24:13 main 入队判 light → 轻池 light-4 领取；
2. 19:24:54 调 excel_process，「分块读取」意图被规则路由劫持成 to_md，运行时重判 heavy（DB 已翻转）但按设计就地继续；
3. 19:24:59 起 MarkItDown 整簿转换（CPU 密集 + 全量加载）阻塞事件循环，**停跳约 8.6 分钟**（期间容器日志完全静默）；
4. 内存同步顶死 1GiB cgroup（failcnt 273k）；
5. 19:33:35 LoopWatchdog 检测到停跳超 15s 阈值，reclaim 1 个 attempt → 状态 interrupted → supervisor 抛 `AGENT_RUNNER_SLOT_CONTAINER_RESTART_REQUIRED` → 容器自动重启；
6. 19:33:41 light-4 重启完成，healthy 恢复，其余容器无感。

**定性**：
- 看门狗防护链**按设计工作**（意外完成了设计 §6.2 看门狗触发的验收测试），中断被正确登记为 interrupted，attempt 未自动重试（interrupted 终态），无脏状态残留。
- 根因一：**to_md 无规模守门 + 规则路由把分块读取意图劫持成 to_md 整簿转换**。「分块读取」上下文命中规则路由的 to_md 短路（params 丢失 range），MarkItDown `convert()` 全量加载 1M 行整簿（约 5M 单元格，openpyxl 全量内存 ≈ cells × ~200B），内存同步顶死 1GiB cgroup。to_md 守门缺失是停跳与内存双问题的共同根因——对照路径 `read` 操作（read_only 流式 + range + 500 行截断）对大文件安全。
- 根因二：**CPU-GIL 下 `asyncio.to_thread` 对纯 Python CPU 密集转换无效**（假异步已在 commit 2f15048e 修复——`_handle_to_md` 已走 to_thread，但 GIL 下转换线程持锁期间事件循环抢不到执行权，to_thread 只能救 IO 阻塞、救不了 CPU 停跳）。根治不在执行层，必须让大文件在进入 MarkItDown 之前 fail-fast。
- 关联缺陷：main profile 入队恒 light 的判型逻辑无法预见 Excel 附件，误判降级路径必然把此类任务落在轻容器——即「运行时重判 + 就地继续」与「大文件缺乏入口守门」的组合在 Excel 场景下是系统性风险，不是偶发。
- 内存上限处置：用户决策**保持容器内存 1g 不动**（`AGENT_RUNNER_WORKER_MEMORY` 配置项已存在），通过 to_md 规模守门在代码层根治，不升 2g。

**建议整改**（按优先级，2026-11-12 当日已实施①②）：
1. ② to_md 整簿转换规模守门：convert 前 read_only 流式探测真实规模，超过 `TO_MD_MAX_CELLS=500_000`（约 100MB）fail-fast，只引导 read + range 分块（已实施，见 excel_to_md.py）；
2. ③ 路由层保留分块读取意图：分块/range/前N行意图跳过规则路由的 to_md 短路，交给 LLM 路由生成 read+range 参数（已实施，见 excel_router.py）；
3. ⑤ excel_process 假异步整改：已完成（commit 2f15048e），对 IO 阻塞有效，但对 CPU-GIL 停跳无效，需以①根治；
4. 评估运行时重判 heavy 的任务是否允许「迁移重试」（重判翻转时若当前为轻容器且尚未产出，考虑取消并以 heavy 身份重新入队）——需设计权衡，与「就地继续避免重复工作」对冲。

### 5.2 中：travel-consultant 入队即 heavy

见 §3.3。占重池但不挤爆，登记为观察项，待业务高峰复核。

### 5.3 低：测试设计教训

excel_range_full 用例首轮（重池占满轮）LLM 走了 upload_data_file（截断 1 万行）+ analyze_data 捷径未触发 excel_process；本轮提示词强化后 2 个 full 用例真正走了 excel_process。**提示词约束力不足时会显著改变任务判型与负载路径**，后续压测用例需把工具路径写死在提示词或改用工具直调。

## 六、成本与验证缺口

- **成本**：两测试租户今日累计 233.72 积分 / 594 万 token（tenant_d6c6ed2aadd6 196.15 + tenant_1dc997a1806b 37.57），约 40 条 chat_records。
- **验证缺口**：
  - P0①（取消后槽位挂死 / NFS stale handle）未做专项测试；
  - 看门狗验收原计划用主动注入测试，本轮由真实卡死场景替代完成（被动触发），主动注入未做；
  - excel-reclass 轮的报告 JSON 因脚本等待中断任务超时未落盘（脚本 timeout 1800s），该轮数据以 DB 事件流 + 容器日志为准；
  - 槽内并发 4 满载（4 执行体全部同时跑满单容器）未精确压到边界（最高实测 3 个同时）。

## 七、证据索引

| 轮次 | 文件 |
|---|---|
| 冒烟 | evidence/agent2-load-smoke-20261112.json |
| 9 轻并发 | evidence/agent2-load-light9-20261112.json |
| 8 同型 | evidence/agent2-load-light8-same-20261112.json |
| 8 长文本 | evidence/agent2-load-light8-long-20261112.json |
| 数据准备 | evidence/agent2-load-dataprep-20261112.json |
| 重池占满 | evidence/agent2-load-mixed-heavysat-20261112.json |
| Excel 重判（DB 证据） | agent_runner_events runner_b2f3071960904fefb7cd8a511d347501；light-4 容器日志 19:24~19:33（watchdog 触发 + 容器重启） |

压测脚本：`scripts/agent2_runner_load.py`。
