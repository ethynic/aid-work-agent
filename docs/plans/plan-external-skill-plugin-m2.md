# 外部 Skill 插件机制 M2（执行路由 + skill-runner Provider）开发计划

> 关联调研：[external-skill-plugin-integration-research.md](../research/external-skill-plugin-integration-research.md)（§4.0 总体 / §4.2 B 层路由 / §4.3 C 层设备执行 / §5 分期 / §7 开放问题）
> 前置：[M1 知识层开发计划](plan-external-skill-plugin-m1.md)（已落地：目录链 / 审批 hash 门 / 同名拒绝 / 插件执行全量拦截——M2 在其上增量扩展，不改已验收语义）
> ideas 条目：系统功能分区 20261006-1204
> 修订：2026-10-06 按评审意见修订——① providers.test.ts 计数断言 3→4 纳入文件清单与「不改断言」例外声明；② §3.6 增 entries ∩ mutable = ∅ 双端强制（封堵入口脚本免 hash 锁定的篡改分发）；③ §3.1 增 stdin/files/content fail-closed 处置（不静默丢弃）；④ §3.7 增文案 substring 约束表（保 M1 既有断言绿）；⑤ §4.2 点名版本门实现钩子（覆写 _dispatch_and_wait）与审批条目二次读取 fail-closed 语义；⑥ 验收清单注明 write_tools 全归写与第 3/7 条牵制。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 云端 catalog skill-runner 条目 + 审批清单扩展（entries/exec_hash）+ 配置 | ✅ 完成（2026-10-06） | 云端测试门通过；catalog 条目、审批 entries/exec_hash/mutable 扩展、device_execution 配置落地（catalog.py / skill_plugin_gate.py / approve_skill_plugin.py / settings.py / config.yaml + 对应单测） |
| Phase 2 | 云端执行路由（放行判定 + 命令解析 + dispatch + 文案更新） | ✅ 完成（2026-10-06） | 云端测试门通过；skill_runner_proxy.py（evaluate/parse/dispatch）新增、skill_executor.py 路由 + 文案、use_skill_tool / skill_execute_tool 更新 + 对应单测 |
| Phase 3 | 设备端 skill-runner handler（manifest/目录发现/hash 镜像/命令门禁/执行/回传）+ 接线 | ✅ 完成（2026-10-06） | 设备端 typecheck 通过，node 单测达成（darwin 宿主验证上限）；skillRunner.ts 新增 + manifest/capabilities/routing/cli 接线 + 对应单测；darwin 存量红实测 8 例（计划记 7 例，同族运行间波动），M2 前后失败集完全一致、无新增 |
| Phase 4 | 独立测试 + CodeReview + 文档收尾 + 真机验收清单交接 | ✅ 完成（2026-10-06） | 独立测试+评审 9 条发现（P0/P1 1 条），修复 3、拒绝 0；修复循环内评审复核 3 轮未判通过，**主控者终态复核关闭**：P1（共享 hash 向量防漂移闭环）经确定性核验成立——两侧 fixture 已统一为同一份 demo-skill（含排除集样本）、共享同一组常量（a7565713…/5c53b754…），云端 103 passed + 设备端 27 pass/0 fail 对同组常量断言全绿，sha256 语义下即证 fixture 字节同源且双端算法一致，任一端改算法该端必红、另一端即对账来源（评审发现描述的是修复前状态）；终态全量：云端 12 文件门范围 239 passed + 设备端受影响 4 文件 60 pass/0 fail + typecheck 0 错误。文档收尾完成；真机验收未做——宿主无 Windows 环境，§7 清单置顶提醒、移交用户执行 |

**收尾备注（2026-10-06，如实记录）**：

- 改动文件与 §5 清单一一对应：云端 15（12 修改 + 3 新增）、设备端 9（7 修改 + 2 新增）。
- 云端存量红：`tests/unit/tools/` 目录 9 例（Redis 已禁用内存降级语义差异 / deploy 脚本契约 / 阿里 API mock 形态 / ppt 路径校验），与本次改动零 import 交集，基线即红、非本轮引入。
- 并行改动说明：本会话内 `clients/agent-tool-runtime/src/{cli,config,invocationRunner}.ts` 出现 M 状态，系同一工作流设备端实现工程师兄弟 agent 的并行改动（§5「云端与设备端可并行开发」），收尾 agent 对 `clients/` 仅有只读 grep、零写入，收尾核对时已确认。
- 文件清单偏差（已获授权）：`providers.test.ts:155` 注册表计数断言 3→4 不在分派任务清单内，因与「追加 manifest 必致该断言红」冲突，经 escalate 裁定 (a) 授权修改——仅此一行，未动该文件其他内容。
- 环境事实：宿主 darwin 无 Windows 真机——验证上限为 typecheck + node 单测（均已达成），无 npm ci/typecheck/测试阻塞；真机项（DESKTOP_NOT_INTERACTIVE / 命名管道跨进程互斥 / `taskkill /T /F` 进程树终止 / 真实 Python 解释器执行 jingpian 样本）按 §7 清单移交用户执行。
- darwin 存量红实测 8 例而非计划记录的 7 例（HEAD 对照实证，同一测试族运行间波动），M2 前后失败集完全一致，无新增。

## 1. 背景与范围

M1 已落地知识层：插件 skill 经审批（内容 hash 锁定）+ allowed 白名单后对 Agent 可见，`skill_execute` 对插件来源全量拦截、对声明 `execution: device` 的 skill 返回设备链路占位。M2 打通**执行层**：声明设备执行的已审批插件 skill，经既有 invocation 队列下发到用户 Windows 设备，由 agent-tool-runtime 新增的 skill-runner 通用执行器执行并回传 stdout 结果。

架构定位（调研 §1/§4.2）：skill-runner 是**通用执行器 Provider**，不是封闭 schema 的第一方 CLI——Provider 规范中「禁止 command/raw_argv 等任意执行参数」的约束由**设备侧命令门禁 + 审批白名单 + hash 对账**补偿（见 §3.6），这是本计划安全设计的核心。

### 1.1 M2 范围（硬边界，超出即砍）

**云端**：

1. `src/local_tools/catalog.py` 追加 skill-runner Provider（provider_id `ai.aidwork.skill-runner`，tools=`["skill_script_run"]`），纯追加，不动 boss/weixin/wecom 三条目；
2. `SkillExecutor.execute_skill_command` 放行「已审批插件 + execution=device」并路由 invocation 入队——复用 `LocalToolProxyTool` 的设备闸门 / 计费预检 / 进度轮询 / 取消 / 超时语义与 `LocalInvocationService.enqueue`；
3. 审批清单条目扩展 `entries`（设备执行入口白名单）等字段，向后兼容：**无 entries = 拒绝设备执行**（fail-closed）；
4. `use_skill` / `skill_execute` 的 M1 占位文案更新为真实提示；
5. 配置项（settings + config.yaml）。

**设备端（TypeScript，clients/agent-tool-runtime）**：

1. `providers.ts` 追加 skill-runner 镜像 manifest（纯追加）；
2. 新增 skill-runner handler：设备 skill 目录发现（设备配置目录）、capabilities 上报已安装 skills 清单（name/hash）、命令门禁 fail-closed（只允许白名单 entry、解释器+argv 直传禁 shell 拼接、路径圈定 skill 目录内、参数数量/长度上限）、桌面锁（与三 CLI 同互斥域）、stdout 结果经既有 v1 result 链路回传；
3. node 单测（`npm run build:main && node scripts/run-tests.mjs`）。

**验证样本**：jingpian-house-finder v2.0.0（调研 §2），`uicache.py status`、`flow.py seq verify` 走通即算闭环（调研 §5）。

### 1.2 明确不做（超出即砍）

- 截图/产物回传与多模态工具结果（M3）；管理端 UI/租户市场/按 skill 细分计费/设备自动分发（M4）；
- Gunicorn 多进程实测（多进程缓存一致性沿 M1 签名轮询语义，不新增验证面）；
- Windows 灯机真机联调——本计划只交付真机验收清单（§7），由用户执行；
- 不改 M1 已验收语义：`compute_skill_dir_hash` 排除集、`scan_plugin_dir` 判定、`read_approvals` 校验规则、`resource_cache`/`skill_session`/`tenant_skill_cache`、`is_plugin_skill`/`get_execution_decl` 均不动（只增量调用）；
- 不新增 LLM 可见工具（`skill_execute` 保持单入口，路由对 LLM 透明，调研 §4.2）；
- 不改既有三 CLI 的 manifest 与校验逻辑；`providers.test.ts` 的注册表计数断言 `TRUSTED_MANIFESTS.length === 3` 同步为 4（事实 17，纯基数同步，三 CLI 工具白名单/互斥断言不动）；不做 weixin-cli 与 skill 的微信会话业务级协调（调研 §7.5，桌面锁互斥已保证不并发）。

## 2. 现状关键事实（已核对）

| # | 事实 | 位置 |
|---|---|---|
| 1 | `LocalToolProxyTool.execute` 链：设备闸门（selected+active+在线≤30s+capabilities 覆盖 provider）→ 计费预检（价>0 才查余额）→ `LocalInvocationService.enqueue`（行 `provider_key` 做行级 claim 过滤）→ 轮询 events/state 至终态 → 终态映射；`catalog=False` 不进自动目录、`LOCAL_PROXY_TOOL_NAMES` 决定注册面 | `src/local_tools/proxy_tool.py:232-401`、`src/local_tools/service.py:24-71` |
| 2 | 设备闸门按 tool_name 查 catalog：`device_ready_error` → `get_provider_keys_for_device`（providers 数组/manifests/旧 provider_id 三路解析）+ `is_tool_allowed` | `src/local_tools/device_policy.py:8-24`、`src/local_tools/catalog.py:118-169` |
| 3 | claim 行级过滤：行 `provider_key` 非 NULL 仅派给 capabilities 覆盖该 key 的设备；api.py claim 已从 capabilities 解析 provider_keys | `src/local_tools/repository.py:444-446`、`src/local_tools/api.py:239-256` |
| 4 | 计费：`write_result` succeeded 首落终态同事务按 `tool_credit_price(tool_name)` 计费（价目表未列 → default_credit_price，现配 0）；proxy 侧预检同源取价 | `src/local_tools/repository.py:620-660`、`src/local_tools/pricing.py:20-27` |
| 5 | M1 执行拦截：`_check_execution_gate` 判定顺序 device 声明占位 → 插件来源拦截；两个入口（`execute_skill_command`:593、`execute_skill_script`:701）共用；拦截先于 `_process_command` 路径替换与子进程创建 | `src/core/skill_executor.py:496-547` |
| 6 | 放行判定所需能力已就绪：`registry._plugin_hashes`（当前审批集）、`is_plugin_skill`（目录身份 fail-closed）、`get_execution_decl`（返回 execution/device_requirements/entry） | `src/core/skill_registry.py:320-382` |
| 7 | 审批清单条目为任意 dict 透传（read_approvals 只强校验 computedHash/skillMdHash，额外字段原样返回）——M2 增字段**无需改 read_approvals** | `src/core/skill_plugin_gate.py:83-108` |
| 8 | 审批 CLI `--approve` 写入 computedHash/skillMdHash/note/source_dir；原子写 | `scripts/approve_skill_plugin.py:84-105` |
| 9 | skills.check（`SkillSession.check`）是 use_skill 版本一致性门，非执行门；`ControlToolAdapter` 对 skill_execute 只做 check + 参数注入后直调工具——**controls.py 无需改动** | `src/services/agent_runner/runtime/skill_session.py:14-42`、`controls.py:35-42` |
| 10 | 设备侧 invocation 路由：`providerKey = inv.provider ?? 'boss-recruiting'`；未安装 → PROVIDER_NOT_AVAILABLE；manifest 白名单 → TOOL_NOT_ALLOWED；v2 门禁 → PROTOCOL_NOT_SUPPORTED；全部 tool call 持同一 `desktopLockName(desktopResourceKey)` 桌面锁；write_tools 触发锁屏检测与崩溃 EXECUTION_UNKNOWN | `clients/agent-tool-runtime/src/invocationRunner.ts:284-333,360-375,416-553` |
| 11 | Provider 能力上报：`deviceCapabilities()` 按 `resolveProviderEntries`（config.providers entry）过滤 TRUSTED_MANIFESTS 键 → providers 数组 + provider_manifests 摘要 + capabilities 工具名清单；键序契约 boss 恒首位 | `clients/agent-tool-runtime/src/config.ts:146-196` |
| 12 | manifest digest 按 Provider 独立计算（provider_id+排序 tools+execution_target），追加 Provider 不改三既有 digest | `clients/agent-tool-runtime/src/providers.ts:156-165,188-198` |
| 13 | 设备 ProviderSet 以 MCP entry 为准（`has(key)`=有 entry）；skill-runner 是进程内 handler，不走 MCP stdio | `clients/agent-tool-runtime/src/providerManager.ts:228-270`、`cli.ts:146-166` |
| 14 | 桌面锁为机器作用域命名锁（win32 命名管道 / 非 win32 退化为 unix socket），Runtime 多实例互斥 | `clients/agent-tool-runtime/src/desktopLock.ts` |
| 15 | 设备端测试：`npm run build:main`（tsc）→ `node scripts/run-tests.mjs`（node:test 递归 dist/tests）；无 yaml 依赖（package.json 仅 MCP SDK/zod/boss） | `clients/agent-tool-runtime/scripts/run-tests.mjs`、`package.json` |
| 16 | 云端测试入口 `bash scripts/dev_test.sh`（容器 aid-agent-api 优先）；compose 已挂载 `./skills:/app/skills`（第一方插件位） | `scripts/dev_test.sh`、`docker-compose.agent-runner.yml` |
| 17 | 设备端 `providers.test.ts` 存在注册表计数断言 `assert.equal(Object.keys(TRUSTED_MANIFESTS).length, 3)`——追加 skill-runner manifest 后**必红**，需同步 3→4（纯基数同步，三 CLI 工具白名单/互斥断言不变） | `clients/agent-tool-runtime/tests/providers.test.ts:155` |
| 18 | `skill_execute_tool` 每次调用**恒构造 stdin_content**（无 content 时也注入 tenant/subagent 身份 JSON，:237-243）；`files` 写入容器工作目录后经 `_process_command` 做 `${filename}` 占位替换（该替换只在容器路径生效）——设备路由必须显式处置这两类输入，否则读 stdin 的脚本挂到超时、附文件命令把占位符字面量传进 argv | `src/tools/skill/skill_execute_tool.py:237-243`、`src/core/skill_executor.py:956-960` |
| 19 | `registry._plugin_hashes` 值仅为 `(computedHash, skillMdHash)` 二元组——entries/exec_hash 不在其中，放行判定的审批数据须另行读取清单 | `src/core/skill_plugin_gate.py:251` |
| 20 | 基类 `LocalToolProxyTool.execute()` 在 `_find_ready_device`（设备闸门）与 `_dispatch_and_wait`（enqueue）之间**无插入钩子**——版本门第 1 层只能经覆写 `_dispatch_and_wait` 实现 | `src/local_tools/proxy_tool.py:253-273` |

**基线（本计划撰写时已运行）**：

- 云端：`bash scripts/dev_test.sh tests/unit/test_skill_execute_plugin_guard.py tests/unit/test_skill_plugin_gate.py tests/unit/test_skill_registry_load_sources.py tests/unit/test_resource_cache_plugin_refresh.py tests/unit/test_tenant_skill_cache.py tests/unit/test_skill_registry.py tests/unit/test_skill_loader.py tests/unit/local_tools/test_catalog_providers.py tests/unit/local_tools/test_proxy_tool.py -p no:cacheprovider -q` → **134 passed**。
- 设备端：`npm ci --prefix clients/agent-tool-runtime`（工作区原未安装，本次计划撰写时装齐）→ `npm run build:main` → `node scripts/run-tests.mjs` → **186 tests：176 pass / 7 fail / 3 skipped**。7 例失败均为宿主 darwin 平台限制的存量红：DPAPI 仅支持 Windows 1 例（`pair.test.js`，错误串「DPAPI 仅支持 Windows（当前平台 darwin）」）+ 桌面锁跨进程互斥 6 例（desktopLock/desktopArbitration/processCleanup/v2WritePath，macOS unix socket 语义，`desktopLock.ts:11` 自述「非 win32 仅开发/测试可能跑到」）。**与 M2 无关，M2 前后均存在；Windows 真机为准。**
- 环境事实：宿主 node v22；Python 测试必须 `bash` 前缀。

## 3. 设计决策（六项裁定）

### 3.1 D1：invocation payload schema

tool_name=`skill_script_run`，provider_key=`skill-runner`，`arguments_json` 即 payload（设备侧 `inv.arguments` 直接消费）：

```json
{
  "skill": "jingpian-house-finder",
  "version": "2.0.0",
  "entry": "scripts/flow.py",
  "args": ["seq", "verify"],
  "exec_hash": "<sha256hex，审批时锁定的设备对账 hash（§3.6）>",
  "timeout_seconds": 900
}
```

- `version` 仅用于日志/展示，**不参与对账判定**（对账唯一依据 exec_hash，防版本字符串伪造）；
- `timeout_seconds` 由云端配置下发，设备取 `min(下发值, 设备硬顶 1800s)`，双端各自强制；
- `entry` 必须是 skill 目录内相对路径（正斜杠），设备侧 resolve 后必须落在 skill 目录内。

**命令解析（云端，入队前）**：`shlex.split(command)` → 剥离可选解释器前缀（首 token 匹配 `python`/`python3`/`py` 时丢弃——设备端解释器由设备配置决定，云端不指定）→ 首 token 为 entry（去 `./` 前缀归一化）→ **entry 必须 ∈ 审批条目 `entries` 白名单**（§3.6，审批时快照，非当前磁盘声明）→ 其余 token 依序为 args。任何一步失败 → 拦截并返回 `INVALID_DEVICE_COMMAND`，文案列出该 skill 的全部合法 entries（LLM 可自纠）。解析成功也意味着**原始命令字符串不再下传**——设备收到的是结构化 entry/args，绝不转发 shell 命令串。

参数上限（云端入队前 + 设备执行前双端强制）：args ≤ 16 个、每个 ≤ 500 字符、总长 ≤ 4000 字符。

**stdin / files / content 的处置（fail-closed，不留静默丢弃）**：

- `files` 参数：设备路由**拒绝非空 files**（`INVALID_DEVICE_INPUT`：「设备执行的技能不支持 files 文件参数，请将所需内容直接写入命令参数」）——`${filename}` 占位替换只在容器工作目录语义下生效（事实 18），透传只会把字面量塞进 argv；判定放 executor 路由分支（`execute_skill_command` 的 files 形参，同时覆盖 MCP 路径）；
- `content` 参数（用户文本，经 tool 层合并进 stdin_content）：`skill_execute_tool` 在技能为 device 路由（`evaluate_device_execution` 判定）且显式传入 content 时**提前拒绝**（同 `INVALID_DEVICE_INPUT`）——executor 侧无法区分「用户 content」与「服务端注入的身份 JSON」（二者都已是 stdin bytes）；
- `stdin_content`（服务端恒注入的 tenant/subagent 身份 JSON，事实 18）：设备路由**不透传、直接丢弃**（如实声明：内部管道数据，设备侧脚本无消费方；AID_* 身份环境变量注入属 M3+ 不引入）；设备 spawn `stdin: 'ignore'`——依赖 stdin 的脚本**立即收到 EOF 快速失败**，而不是挂到 900s 超时。

### 3.2 D2：云端版本门（capabilities 与审批 hash 的校验时机与失败文案）

双层校验，均 fail-closed：

1. **下发前（云端，设备闸门之后、enqueue 之前）**：从选定设备 `capabilities_json.skills`（设备心跳上报的已安装清单，§3.4）查找 `name == skill 名` 的条目：
   - 无该 name → 返回 `SKILL_NOT_INSTALLED`：「技能 '{name}' 尚未安装到选定设备。请在设备端安装该技能（Runtime skills 目录）后重试，或向用户说明需要先在电脑上安装」——不建 invocation，设备不出工；
   - 有 name 但 `hash != 审批 exec_hash` → 返回 `SKILL_VERSION_MISMATCH`：「设备上的技能 '{name}' 内容与云端审批版本不一致。请更新设备端技能后重试；若设备为新版本需云端重新审批」——不建 invocation。
2. **执行前（设备，claim 之后、spawn 之前）**：设备重算本地 skill 目录 hash（mtime/size 快照缓存，变更才重算），与 payload `exec_hash` 比对；不一致 → 终态 `SKILL_VERSION_MISMATCH`（effect=none，retryable=true），不执行。兜底心跳 5s 间隔内 capabilities 陈旧窗口。

skills 清单上报带上限：≤50 条（超出截断 + 日志告警），防 capabilities 膨胀。

### 3.3 D3：skill-runner 的计费归类

- 台账科目 tool_name 即 `skill_script_run`（单一科目，M2 不按 skill 细分）；沿用两条既有计费链路，**零代码新增**：proxy 侧 `_tool_credit_price()` 预检（价>0 才做余额阻断）与 `repository.write_result` succeeded 首落终态同事务计费，取价同源 `tool_credit_price("skill_script_run")`；
- M2 价目：`configs/config.yaml` `boss_tool_billing.tool_credit_prices` 显式示例 `skill_script_run: 0`（默认免费，`default_credit_price` 现为 0）——调价只改配置不改代码；
- 按 skill 计量/差异化定价（复用 `arguments` 里的 skill 名对账）归 M4 生态治理。

### 3.4 D4：设备 python 解释器配置形态

- `RuntimeConfig` 新增 `skills?: { dir?: string; python?: string }`（config.json，本机管理员配置，**禁止云端下发**——对齐 Provider entry 的本地信任原则）：
  - `python`：受管 Python 解释器绝对路径（如 workbuddy venv 的 `python.exe`）；**未配置或文件不存在 → 不上报 skill-runner 能力**（providers 数组无 `skill-runner`，行级 claim 过滤天然不派发，能力真实性对齐 weixin v2Send 先例）；
  - `dir`：skill 目录，默认 `<runtimeHome>/skills`（`%APPDATA%/aidwork-tool-runtime/skills`），可指到 workbuddy 目录或其 junction 链；
- M2 无自动安装/拉包：用户手动放置（调研 §7.2 开放问题的首期结论）；`requirements.txt` 依赖安装属人工步骤，写入 skills/README 运维说明；
- venv/解释器探测不做（探测逻辑复杂且易误配，配置显式路径即可）。

### 3.5 D5：M1「插件全拦」的精确放行条件

`execute_skill_command` 内 `_check_execution_gate` 的 M2 后继判定（`execute_skill_script` 第二入口**维持 M1 全拦**，无调用方，防绕过）：

```
1. decl = registry.get_execution_decl(name)；decl.execution != "device" →
   维持 M1 规则：插件来源（is_plugin_skill）→ 拦截（文案更新，§3.7）；内置 → server 现状不变。
2. decl.execution == "device" 时，以下五项全部满足才放行路由：
   a. registry.is_plugin_skill(name) 为真（目录身份判定）；
   b. name ∈ registry._plugin_hashes（当前审批集；租户 TTL 半状态窗口 fail-closed）；
   c. settings.skills.plugins.device_execution.enabled（M2 开关，默认 false）；
   d. 审批条目含非空 entries + exec_hash（旧清单条目无 entries → 拒绝，即向后兼容 fail-closed）；
   e. 命令解析成功（entry ∈ 审批 entries、参数上限内，§3.1）。
3. 任一不满足 → 拦截，文案按首个失败项区分：
   - a 不满足（内置声明 device）→ BUILTIN_DEVICE_UNSUPPORTED：内置 skill 无审批 hash，无法设备对账，不支持设备执行；
   - b/d 不满足 → PLUGIN_NOT_EXECUTABLE：插件未在当前审批集或审批不含设备执行入口，需（重新）审批；
   - c 不满足 → DEVICE_EXECUTION_DISABLED：设备执行链路未开启（管理员配置）；
   - e 不满足 → INVALID_DEVICE_COMMAND（附合法 entries 清单）。
```

- 判定与路由仍发生在 `_process_command` 路径替换与本地子进程创建**之前**（沿 M1 顺序保证，插件脚本不会被替换进容器路径）；
- `controls.py` / `skills.check`（版本一致性门）**零改动**：放行只发生在 executor 路由层；
- MCP executor 路径（`src/mcp/executor.py:162`）经同一函数自动获得相同行为（MCP 全局单例不加载插件 → 插件在该路径不可见，M1 结论保持）；
- use_skill 手册尾部的执行提示与上述判定共用一个决策函数（`evaluate_device_execution(registry, name)`，见 §4.2），保证「提示可执行 ⇔ 实际可执行」不漂移。

### 3.6 D6：设备端目录与审批 entries 的对账策略

**审批条目扩展**（`--approve` 时写入，read_approvals 已透传任意字段、无需改动）：

```json
{
  "computedHash": "…", "skillMdHash": "…", "note": "…", "source_dir": "…",
  "entries": ["scripts/flow.py", "scripts/uicache.py"],
  "exec_hash": "sha256…",
  "version": "2.0.0",
  "mutable": ["references/ui-cache.json"]
}
```

- `entries`：设备执行入口白名单，审批时从插件 SKILL.md `metadata.entry` 快照（每项须为目录内相对路径且文件存在；execution=device 但无 entry 声明 → CLI 报错拒绝审批该技能的设备执行项）。**当前磁盘 `get_execution_decl().entry` 与审批快照不一致时以审批为准并 warning**（信任锚是审批，不是可变磁盘）；
- `exec_hash`：**新增** `compute_skill_exec_hash(skill_dir, mutable_paths)`——与 `compute_skill_dir_hash` 同算法（排序相对路径 + 每文件 sha256 + `\x00` 分隔聚合）、同内置排除集（`__pycache__`/`shots`/`log` 目录、`*.pyc`、`.DS_Store`），**额外排除 `metadata.mutable` 声明的自学习可变路径**。解决样本 skill 运行时写回 `references/ui-cache.json` 导致 hash 永久漂移的问题（调研 §2「自学习」行）；M1 `computedHash`/`skillMdHash` 语义与排除集**一字不动**（知识层完整性不受 mutable 影响）；
- `mutable` 校验：不得含 SKILL.md、不得逃出 skill 目录（拒绝 `..`/绝对路径）、≤8 条、**不得与 entries 相交（entries ∩ mutable = ∅，双端强制）**——SKILL.md 恒参与 hash 且不可被声明为 mutable，**篡改 SKILL.md 扩大 mutable 集必然改变 hash 而被拒**；**入口脚本是被执行代码，必须受 exec_hash 锁定**：若允许 entry 入 mutable，仅替换 entry 脚本内容、其余文件（含 SKILL.md）不变的篡改分发目录可通过设备对账（设备只校验 exec_hash，computedHash 属云端知识层校验、设备不校验）执行任意代码——审批 CLI（--approve 时 entries/mutable 相交即报错拒绝）与设备侧门禁（对照本地解析的 metadata.entry 与 metadata.mutable，相交即 SKILL_GATE_REJECTED 终态 effect=none）双端拒绝该形态；
- 设备端 TS 镜像同一算法（含 mutable 排除），两侧测试共享固定样例目录 + 期望 hash 向量防漂移（§6）。

**设备侧对账（执行前，fail-closed）**：payload `exec_hash` ↔ 设备重算本地目录 hash（mutable 集从**本地** SKILL.md 解析——合法性论证见上，本地 SKILL.md 被 hash 覆盖验证，篡改即失配）。入口白名单同样从**已通过 hash 验证的本地 SKILL.md** `metadata.entry` 解析（不信任 payload 里的 entry 自身之外的任何清单），payload.entry 必须 ∈ 该集。

### 3.7 M1 占位文案更新（use_skill / skill_execute）

- **放行路由达成**（§3.5 五项全满足）：`use_skill` 手册尾部提示改为设备执行说明（非「请勿重试」）：「技能 '{name}' 将通过本机设备执行：调用 skill_execute 后任务会下发到你配对的电脑（Runtime 需在线、已安装同版本技能）执行；执行期间会操作该电脑桌面（键鼠/窗口），请提醒用户不要同时使用鼠标键盘」。`skill_execute` 直接走设备链路，进度经 invocation events 落库可观测；
- **未达成**：保留拦截语义、更新措辞——删除「规划 M2/即将支持」类过时表述，按 §3.5 第 3 条的区分码给出真实原因（未审批入口/未开启/内置不支持/命令不合法），均保留「请勿重试 skill_execute」防试错；
- `_PLUGIN_EXECUTION_BLOCKED_MESSAGE`（未声明 device 的插件）：更新为「外部插件仅支持声明设备执行（metadata.execution=device）且经审批含入口白名单的技能；该技能未声明设备执行，云端命令执行不对外开放」——语义不变（仍拦截），仅去除 M2 字样。

**文案 substring 约束（保持 M1 既有断言绿，实现时逐条对照）**——`tests/unit/test_skill_execute_plugin_guard.py` 对拦截文案有 substring 断言，新文案必须继续包含：

| 场景 | 必含 substring（断言位置） |
|---|---|
| `execute_skill_script` 第二入口拦截、未声明 device 插件拦截 | 「外部 Skill 插件」「请勿重试」（`:141` 附近；§3.7 第三条文案因此仍须出现「外部 Skill 插件」字样，不能只写「外部插件」） |
| builtin 声明 device 的拦截（BUILTIN_DEVICE_UNSUPPORTED） | 「设备执行」「metadata.execution=device」「请勿重试」（`:147-153`） |
| 插件+device 未放行（PLUGIN_NOT_EXECUTABLE / DEVICE_EXECUTION_DISABLED 等）的 skill_execute 拦截与 use_skill 提示 | 「设备执行」+ skill_execute 侧「请勿重试」/ use_skill 侧「请勿调用 skill_execute」（`:206-211`）；插件未声明 device 的 use_skill 提示仍含「外部 Skill 插件」「请勿调用 skill_execute」 |
| 放行路由达成的 use_skill 尾注（§3.7 第一条） | 不受上述约束（新断言：含「设备执行」且**不含**「请勿」字样） |

§6 测试计划已含「M1 既有断言语义保持」回归项——文案实现与该约束表冲突时以约束表为准（既有断言优先于措辞偏好）。

## 4. 实施设计

### 4.1 云端 catalog + 审批扩展（Phase 1）

- `catalog.py` `TRUSTED_PROVIDERS` 追加：

```python
"skill-runner": {
    "provider_id": "ai.aidwork.skill-runner",
    "min_provider_version": "1.0.0",
    "execution_target": "local_required",
    "tools": ["skill_script_run"],
},
```

纯 Dict 追加：三既有条目、`get_provider_keys_for_device` 解析、`is_tool_allowed` 逻辑零改动（weixin/wecom 先例，`catalog.py:52-92`）。

- `skill_plugin_gate.py` 新增（不改既有函数）：`compute_skill_exec_hash(skill_dir, mutable_paths)`、`read_declared_entries_and_mutable(skill_md_path)`（yaml 解析 metadata.entry/metadata.mutable + 校验规则 §3.6）；
- `approve_skill_plugin.py` `--approve` 增写 `entries/exec_hash/version/mutable` 并打印供审批人复核；`--scan` 展示这些字段；存量条目（无 entries）显示「[设备执行未审批]」；
- settings：`SkillPluginsConfig` 增嵌套模型：

```python
class SkillDeviceExecutionConfig(BaseModel):
    enabled: bool = False            # M2 设备执行开关，默认关
    timeout_seconds: int = 900       # 单次执行超时（proxy 轮询超时 + payload 下发值）
    max_args: int = 16               # args 数量上限
    max_arg_chars: int = 500         # 单 arg 字符上限
    max_stdout_chars: int = 200_000  # 工具结果 stdout 截断上限
```

`config.yaml` `skills.plugins.device_execution` 段（含注释）+ `boss_tool_billing.tool_credit_prices` 增 `skill_script_run: 0` 示例。

### 4.2 云端执行路由（Phase 2）

新模块 `src/local_tools/skill_runner_proxy.py`：

1. `SKILL_RUNNER_PROVIDER_KEY = "skill-runner"`、`SKILL_RUNNER_TOOL_NAME = "skill_script_run"` 常量；
2. `evaluate_device_execution(registry, skill_name) -> Decision`：§3.5 判定 a–d（不含命令解析 e），返回枚举 + 原因码 + 审批条目引用；use_skill 与 executor 共用。**审批条目数据源（实现者注意）**：`registry._plugin_hashes` 值仅为 `(computedHash, skillMdHash)` 二元组（事实 19），entries/exec_hash/mutable 不在其中——Decision 的审批条目在**每次调用时实时 `read_approvals(resolve_approvals_file())` 二次读取**（几 KB JSON，开销可忽略）；二次读取的 fail-closed 语义：清单缺失/损坏（read_approvals 返回 `{}`）、条目缺 entries/exec_hash → 一律判 PLUGIN_NOT_EXECUTABLE 拒绝（与 §3.5 b 项的 registry 判定方向一致，两处独立 fail-closed，不互相兜底放行）；
3. `parse_device_skill_command(command, allowed_entries, limits) -> (entry, args) | ParseError`：§3.1 解析规则（shlex，纯函数，独立可测）；
4. `class SkillScriptRunDispatchTool(LocalToolProxyTool)`：**内部派发器，不注册**（不加 `LOCAL_PROXY_TOOL_CLASSES`，基类 `catalog=False` 天然不进自动目录；SUBAGENT 白名单按名称交集注册的机制不受影响——名称不在任何白名单）：
   - `name="skill_script_run"`、`provider_key="skill-runner"`、`invocation_provider_key="skill-runner"`（行级 claim 过滤只派 skill-runner 设备）、`heal_eligible=False`（弹层自愈是 BOSS 页面编排）、`unsupported_provider_message` 覆写为 skill-runner 引导文案；
   - 实例级 `timeout_seconds` 按 settings 配置注入；
   - 复用基类 `execute()` 主体（闸门/预检/轮询/终态映射）；**版本门第 1 层经覆写 `_dispatch_and_wait` 插入**——基类在 `_find_ready_device` 与 enqueue 之间无钩子（事实 20），覆写后在 enqueue 前校验 `device["capabilities_json"]["skills"]`（§3.2 第 1 层，失败直接返回错误 dict、不建 invocation），随后按基类同语义 enqueue/轮询；价 0 预检跳过、TIMEOUT 时 request_cancel、EXECUTION_UNKNOWN 文案等全部继承；
   - `dispatch(skill, entry, args, exec_hash, *, tenant_id, user_id, session_id, context, progress_queue=None) -> ExecutionResult`：组装 kwargs 调 `execute()`，把 proxy 结果 dict 映射回 `ExecutionResult`（success→stdout=result.data.stdout 截断、stderr、exit_code、duration；失败→error=code+message），供 skill_execute_tool 现有响应构造零改动消费。

`skill_executor.py` 改动（`execute_skill_command`，取到 skill 后）：

```
decision = evaluate_device_execution(self.skill_registry, skill_name)
if decision.executable:                       # §3.5 a–d 满足
    if files: return 拦截(INVALID_DEVICE_INPUT)          # §3.1 files fail-closed
    parsed = parse_device_skill_command(command, decision.entries, limits)
    if not parsed.ok: return 拦截(INVALID_DEVICE_COMMAND, 附 entries)
    return await skill_runner_proxy.dispatch(...)   # 设备链路，不再走本地子进程（stdin_content 不透传）
# 其余：decision.blocked_* → §3.7 文案拦截；decision.server → 现状路径不动
```

`_check_execution_gate` 重构为内部调用 `evaluate_device_execution` + 文案常量更新（`execute_skill_script` 仍走全拦分支）。`use_skill_tool.py`：boundary_hints 按 decision 更新（§3.7，含 substring 约束表）。`skill_execute_tool.py`：调用 executor 前对 device 路由技能且显式 `content` 参数时返回 `INVALID_DEVICE_INPUT`（§3.1，executor 侧无法区分用户 content 与注入身份 JSON，判定须在 tool 层完成）。

### 4.3 设备端（Phase 3）

1. **`providers.ts`**：`TRUSTED_MANIFESTS` 追加 `'skill-runner'`：`tools: ['skill_script_run']`、`execution_target: 'local_required'`、`protocol_version: 1`、`shared_lock_capable: false`（独占桌面锁 = 与三 CLI 同互斥域，即期望语义）、`write_tools: Set(['skill_script_run'])`（RPA 键鼠注入按写对待：锁屏前置 DESKTOP_NOT_INTERACTIVE、崩溃 EXECUTION_UNKNOWN）。三既有 manifest 与 digest 不动（事实 12）；
2. **`config.ts`**：`RuntimeConfig.skills?: { dir?: string; python?: string }`；`deviceCapabilities()`：`skills.python` 配置且存在时——providers 数组并入 `'skill-runner'`（保持 boss 首位、其余字典序的键序契约）、provider_manifests 增其摘要、新增顶层 `skills: [{name, hash}]` 清单（目录发现 + mtime/size 快照缓存 hash，≤50 条）；
3. **新 `skillRunner.ts`**（进程内 handler，接口对齐 `ProviderManager.callTool`：`callTool(name, args, {onProgress, signal})` + 单飞 + busy 抛 ProviderBusyError）：
   - **目录发现**：扫描 skills dir 一级子目录的 SKILL.md，轻量 frontmatter 子集解析（name/version/metadata.entry/metadata.mutable；**自研 ≤100 行解析器，解析不了即 fail-closed 拒执行**，不引入 yaml 依赖；支持平铺键 + 一层 metadata 嵌套 + flow/block 字符串列表）；
   - **hash 镜像**：`skillDirHash(dir, mutable)` 同 §3.6 算法（node:crypto sha256，posix 相对路径排序）；
   - **命令门禁**（`skill_script_run` callTool 入口，逐项 fail-closed）：payload schema 校验 → skill 已安装 → exec_hash 对账（§3.2 第 2 层）→ entry ∈ 本地已验证 SKILL.md metadata.entry → **entry ∉ 本地 metadata.mutable（entries ∩ mutable = ∅，§3.6 安全闭环）** → entry resolve 后在 skill 目录内 → args 数量/长度上限 → timeout 截顶；任一失败返回结构化 `{success:false, code:<稳定码>, message:<中文>, effect:'none', retryable:<按码>}`；
   - **执行**：`spawn(python, [entryAbs, ...args], { cwd: skillDir, shell: false, windowsHide: true, stdin: 'ignore' })` argv 直传（**绝不 shell 拼接**，Provider 规范 §9「禁止 shell 拼接」；`stdin: 'ignore'` 使读 stdin 的脚本立即 EOF 快速失败，§3.1）；env 继承 + `PYTHONIOENCODING=utf-8`/`PYTHONUTF8=1`；stdout/stderr 流式累计（200KB/64KB 截断保尾部）；AbortSignal → 进程树终止（win32 `taskkill /PID /T /F`，posix `process.kill(-pid)`，spawn `detached` 起进程组）；超时同路径；
   - **结果映射**：exit 0 → `{success:true, effect:'applied', data:{stdout, exit_code, duration_ms}}`；非零/超时 → `{success:false, effect:'unknown', retryable:false}`（RPA 可能已部分操作桌面，unknown + 禁自动重试，对齐 Provider 规范 §5.3）；脚本可经 stdout 尾部 JSON `{"effect":"none"|"applied"}` 覆盖 effect（合法枚举才生效）；进度：脚本 stdout 按行转发 `onProgress`（轻量：每行 message，无 current/total）；
4. **`invocationRunner.ts`**：Provider 解析处（`:285-299`）增 skill-runner 分支——`providerKey === 'skill-runner'` 时以 `deps.skillRunner` 代替 `deps.providers.get(...)`，缺 handler → PROVIDER_NOT_AVAILABLE（其余 manifest 白名单/桌面锁/锁屏检测/终态映射链路全部复用，零复制）；
5. **`cli.ts`**：`config.skills?.python` 存在时构造 `SkillRunnerHandler` 注入 `runnerDeps.skillRunner`（pollLoop 与 session-engine 两处 runInvocation deps 同步注入，保持 deps 形状一致）；启动日志打印 skills 目录与解释器。

### 4.4 不改动清单（防越界）

`src/core/skill_registry.py`、`src/services/agent_runner/runtime/{resource_cache,skill_session,context_assembler,tools,controls}.py`、`src/saas/services/tenant_skill_cache.py`、`src/local_tools/{proxy_tool,service,repository,device_policy,api}.py`、`clients/agent-tool-runtime/src/{providerManager,desktopLock,pollLoop,manifestVerifier,resultOutbox,writeAuthorize,journal}.ts`、三 CLI 的 manifest 与工具白名单/互斥测试断言、M1 全部测试既有断言（扩展新增用例不改既有断言）。**唯一例外（如实声明）**：`providers.test.ts:155` 注册表计数断言 3→4（事实 17，新增 Provider 的基数同步，非语义改动）。

## 5. 文件清单

**云端（修改）**：

| 文件 | 改动 |
|---|---|
| `src/local_tools/catalog.py` | 追加 skill-runner 条目（§4.1） |
| `src/core/skill_plugin_gate.py` | 新增 `compute_skill_exec_hash` + `read_declared_entries_and_mutable`（既有函数不动） |
| `scripts/approve_skill_plugin.py` | --approve/--scan 扩展 entries/exec_hash/version/mutable |
| `src/config/settings.py` | `SkillDeviceExecutionConfig` + 挂载 |
| `configs/config.yaml` | skills.plugins.device_execution 段 + tool_credit_prices 示例 |
| `src/core/skill_executor.py` | 路由分支 + `_check_execution_gate` 接 evaluate + 文案更新 |
| `src/tools/skill/use_skill_tool.py` | boundary_hints 按 decision 更新 |
| `src/tools/skill/skill_execute_tool.py` | device 路由技能 + 显式 content 提前拒绝（§3.1） |
| `tests/unit/local_tools/test_catalog_providers.py` | skill-runner 条目/工具放行用例 |
| `tests/unit/test_approve_skill_plugin_cli.py` | entries/exec_hash 写入与向后兼容用例 |
| `tests/unit/test_skill_plugin_gate.py` | exec hash/mutable 校验用例 |
| `tests/unit/test_skill_execute_plugin_guard.py` | 文案更新回归 + 非放行分支用例 |

**云端（新增）**：

| 文件 | 内容 |
|---|---|
| `src/local_tools/skill_runner_proxy.py` | dispatch 工具 + evaluate + parse（§4.2） |
| `tests/unit/local_tools/test_skill_runner_dispatch.py` | dispatch/版本门/payload 构造（mock repository 队列） |
| `tests/unit/test_skill_device_routing.py` | 放行五条件矩阵、命令解析、use_skill 提示、ExecutionResult 映射 |

**设备端（修改）**：

| 文件 | 改动 |
|---|---|
| `clients/agent-tool-runtime/src/providers.ts` | TRUSTED_MANIFESTS 追加 skill-runner |
| `clients/agent-tool-runtime/src/config.ts` | RuntimeConfig.skills + capabilities 上报 |
| `clients/agent-tool-runtime/src/invocationRunner.ts` | skill-runner provider 分支 |
| `clients/agent-tool-runtime/src/cli.ts` | skillRunner 注入 |
| `clients/agent-tool-runtime/tests/providers.test.ts` | 注册表计数断言 3→4（事实 17 基数同步）+ skill-runner manifest 用例 |
| `clients/agent-tool-runtime/tests/capabilities.test.ts` | skills 清单/能力真实性用例 |
| `clients/agent-tool-runtime/tests/providerRouting.test.ts` | skill-runner 路由/未配置 PROVIDER_NOT_AVAILABLE 用例 |

**设备端（新增）**：

| 文件 | 内容 |
|---|---|
| `clients/agent-tool-runtime/src/skillRunner.ts` | 目录发现/frontmatter 解析/hash 镜像/命令门禁/执行/结果映射（§4.3.3） |
| `clients/agent-tool-runtime/tests/skillRunner.test.ts` | 门禁矩阵、hash 向量（与云端共享样例）、spawn/取消/超时（stub python）、单飞 |

两侧文件所有权不相交；云端与设备端可并行开发（payload schema §3.1 为接口契约先行冻结）。

## 6. 测试计划

云端统一 `bash scripts/dev_test.sh <文件> -p no:cacheprovider -q`；设备端 `cd clients/agent-tool-runtime && npm ci && npm run build:main && node scripts/run-tests.mjs`（关注新增/修改文件用例全绿 + 存量 176 pass 基线不回归；darwin 7 例存量红不计数，Windows 真机为准）。

| 文件 | 覆盖点 |
|---|---|
| test_catalog_providers.py（扩） | skill-runner 条目存在且 tool 放行；三既有条目断言不变；`get_provider_keys_for_device` 对含 skills 数组 capabilities 的解析 |
| test_skill_plugin_gate.py（扩） | `compute_skill_exec_hash`：与 computedHash 在无 mutable 时同值、mutable 排除生效、SKILL.md 不可入 mutable、**entries ∩ mutable 相交拒绝（§3.6 安全闭环）**、逃逸路径/超 8 条拒绝；`read_declared_entries_and_mutable` 解析与非法输入 |
| test_approve_skill_plugin_cli.py（扩） | --approve 写入 entries/exec_hash/version/mutable；**entries/mutable 相交（含入口脚本入 mutable）时拒绝审批**；无 metadata.entry 的 device 技能拒绝设备执行项；--scan 展示；存量无 entries 条目 → 派发侧拒绝（对账在 routing 测试断言） |
| test_skill_execute_plugin_guard.py（扩） | 文案更新回归：未声明 device 插件拦截新文案（无「规划 M2」字样、**含 §3.7 substring 约束表全部必含字样**）；execute_skill_script 仍全拦；M1 既有断言（`:141`/`:147-153`/`:206-211`）原样通过不改 |
| test_skill_runner_dispatch.py（新） | dispatch 组装 payload（§3.1 逐字段）；版本门 SKILL_NOT_INSTALLED/SKILL_VERSION_MISMATCH 不建 invocation（mock enqueue 断言零调用）+ 文案；heal 不触发；timeout→request_cancel；终态映射→ExecutionResult 字段齐全；invocation 行 provider_key='skill-runner' |
| test_skill_device_routing.py（新） | 放行矩阵（a–d × 开关 × 审批形态，含 TTL 半状态 `_plugin_hashes` 缺名 fail-closed）；**审批数据二次读取 fail-closed：清单缺失/损坏/条目缺 entries → PLUGIN_NOT_EXECUTABLE**；命令解析：解释器前缀剥离、./ 归一、引号/多空格、entry 不在白名单（附清单文案）、args 上限；**files 非空拒绝（INVALID_DEVICE_INPUT）/ skill_execute_tool 显式 content 拒绝 / stdin_content 不透传（dispatch 参数断言）**；use_skill 提示：放行→设备说明（无「请勿重试」）、未放行→拦截提示（含 substring 约束）；内置 device 声明→BUILTIN_DEVICE_UNSUPPORTED |
| 设备 skillRunner.test.ts（新） | frontmatter 子集解析（含解析失败 fail-closed）；hash 镜像与云端共享向量一致（固定样例目录，期望 hash 常量两侧同源抄录）；门禁矩阵逐码（未安装/版本不符/entry 越白名单/**entry ∈ mutable 拒绝**/路径逃逸/args 超限/超时截顶）；spawn stub（脚本 echo JSON / 非零退出 / 挂起→超时杀树/**读 stdin 脚本立即 EOF 失败**）；AbortSignal 取消；单飞 busy；effect 默认与 stdout JSON 覆盖 |
| 设备 providers.test.ts（扩） | 注册表计数 3→4；skill-runner manifest 工具白名单/write_tools/protocol 断言；三既有 manifest 断言不变 |
| 设备 capabilities.test.ts（扩） | skills.python 配置→providers 含 skill-runner + skills 清单上报；未配置→不上报；>50 截断；键序契约保持 |
| 设备 providerRouting.test.ts（扩） | invocation provider=skill-runner → skillRunner handler 被调用；deps 缺 handler → PROVIDER_NOT_AVAILABLE；v2 invocation → PROTOCOL_NOT_SUPPORTED；manifest 白名单拒绝非法 tool |
| 回归基线 | §2 基线清单云端 134 passed 复跑全绿；设备端存量 176 pass 不减 |

## 7. 真机验收清单（Windows，用户执行）

> **⚠️ 置顶提醒（2026-10-06）**：真机验收**尚未执行**——开发宿主为 darwin，无 Windows 真机（本环境验证上限为 typecheck + node 单测，均已达成）。以下清单整体移交用户在 Windows 灯机执行；清单走通前 M2 不算闭环（另：评审复核尚有未全部通过项，见「开发进度」Phase 4）。

前置：一台 Windows 设备装 agent-tool-runtime（≥本 M2 版本）+ 配对选定；云端 `skills.plugins.enabled=true`、`device_execution.enabled=true`；jingpian-house-finder v2.0.0 放入云端仓库根 `skills/`。

1. 云端审批：`python scripts/approve_skill_plugin.py --approve jingpian-house-finder --note "…"`，核对输出 entries（scripts/flow.py、scripts/uicache.py 等）与 exec_hash；skill 名进 allowed 白名单；
2. 设备配置：config.json 增 `skills: { "python": "<venv python.exe 绝对路径>", "dir": "<技能目录>" }`；技能目录放入该 skill（或 junction 链 workbuddy 目录）；`python -m pip install -r requirements.txt` 于该 venv；启动 Runtime，心跳日志确认 capabilities 含 skill-runner 与 skills 清单；
3. `use_skill`：手册尾部出现设备执行说明（无「请勿重试」）；`skill_execute` 执行 `python scripts/uicache.py status` → invocation 下发 → 设备执行 → stdout 回传，invocation 行（provider_key=skill-runner）与 events 可查。**注意**：`skill_script_run` 统一归 write_tools（含 status 这类只读探测——通用执行器无法逐脚本区分读写，保守归写），本条须在**桌面已解锁**状态执行；附带验证：传 files/content 参数 → `INVALID_DEVICE_INPUT`；
4. 闭环样本：`scripts/flow.py seq verify` 走通（微信 PC 端已登录、镜片小程序可达）；
5. 桌面互斥：skill 运行期间发起任一 weixin-cli 工具调用 → 排队等待（DESKTOP_RESOURCE_BUSY 或等待后执行）；
6. 版本门：修改设备侧 `scripts/flow.py` 一个字节 → `SKILL_VERSION_MISMATCH`；删除设备侧技能目录 → `SKILL_NOT_INSTALLED`；恢复后（ui-cache 写回场景）exec_hash 不漂移、computedHash 不涉及设备侧；
7. 锁屏执行 → `DESKTOP_NOT_INTERACTIVE`（与第 3 条互为牵制：第 3 条解锁跑通、本条锁屏复测同一命令）；超长任务触发超时 → 进程树被终止、TIMEOUT 结果；
8. 计费：台账 `skill_script_run` 记录 credit_cost=0 占位（价 0 语义）；取消路径：对话中断 → 孤儿 invocation 被 request_cancel（沿 proxy 语义）。

## 8. 风险与对策

| # | 风险 | 对策 |
|---|---|---|
| 1 | **hash 算法双端实现漂移**（Python/TS 各一份，漂移即全量设备执行不可用） | 共享测试向量：固定样例目录 + 期望 hash 常量两侧同源写入测试（§6）；算法极简（排序路径+文件 sha256+分隔符）降低面 |
| 2 | 未声明 mutable 的运行时写回导致 exec_hash 漂移 → 技能永久 mismatch | fail-closed 即拒绝（SKILL_VERSION_MISMATCH 可读文案）；插件作者按 README 声明 metadata.mutable；真机验收第 6 条覆盖 |
| 3 | LLM 生成的命令形态多样，解析失败率未知 | 解析失败返回合法 entries 清单促自纠；手册（use_skill 尾注）引导简单命令形态；样本手册本身即「flow.py seq …」收敛形态（调研 §2） |
| 4 | 桌面锁互斥域扩大：skill 与三 CLI 互相排队 | 预期行为（调研 §4.5 论证：样本操作微信 PC 端与 weixin-cli 同进程，不串行必然互踩）；排队等待 120s 超时 → DESKTOP_RESOURCE_BUSY 可重试 |
| 5 | skill_execute 控制工具路径无 progress 队列，长任务用户无中间反馈 | M2 接受（现状 server 路径同样无进度）；事件已落 DB（trace/invocation 可查）；verbose 集成属后续体验项不扩范围 |
| 6 | 设备 frontmatter 自研解析器兼容面不足 | 解析失败 fail-closed 拒执行（可读码）；审批 CLI 云端 yaml 解析为准入校验，双端解析差异在审批时即可暴露（entries 快照对照） |
| 7 | capabilities.skills 上报体积与心跳开销 | ≤50 条截断；hash 以 mtime/size 快照缓存，仅目录变更重算 |
| 8 | darwin 宿主 7 例存量红测试干扰判读 | 基线已记录（§2）；M2 验收以「新增/修改用例全绿 + 176 基线不减」为准；Windows 真机全量复核 |
| 9 | 审批清单旧条目（M1 时期，无 entries）被误放行 | D5 判定 d 项 + read_approvals 透传后显式检查 entries 非空；无 entries=拒绝执行（计划标题级要求），routing 测试矩阵覆盖 |
| 10 | spawn 进程树残留（脚本拉起子进程后超时/取消） | win32 taskkill /T /F 树杀 + posix 进程组 kill；测试覆盖挂起脚本场景 |
| 11 | 多进程（Gunicorn/runner 各进程）审批清单读取一致性 | 派发时实时 read_approvals（几 KB JSON，开销可忽略）；可见性一致性沿 M1 签名轮询，不新增机制 |
| 12 | node_modules 未安装导致设备端开发阻塞 | 实现者先 `npm ci --prefix clients/agent-tool-runtime`（本次计划撰写时已装齐并跑通基线） |
| 13 | **入口脚本绕过 hash 锁定**：审批的 SKILL.md 声明 mutable 含 entry 脚本 → exec_hash 恰好排除被执行文件，设备只对账 exec_hash，仅换 entry 脚本的篡改分发目录可执行任意代码（评审安全缺口） | §3.6 entries ∩ mutable = ∅ 双端强制（审批 CLI 拒绝审批 + 设备门禁 SKILL_GATE_REJECTED），gate/CLI/skillRunner 三处测试覆盖 |
| 14 | stdin/files/content 静默丢弃引发行为意外（读 stdin 脚本挂 900s、${filename} 字面量进 argv） | §3.1 显式处置：files/显式 content 拒绝（INVALID_DEVICE_INPUT）、stdin_content 不透传 + spawn stdin:'ignore'（EOF 快速失败），routing 测试断言 |

## 9. 开发流程级别

按 [.claude/rules/dev_workflow.md](../../.claude/rules/dev_workflow.md) §1 定级：**高风险**——新增外部代码执行通道（设备侧通用执行器门禁）、触及 skill 执行边界（M1 拦截的精确放宽）与供应链审批链（entries/exec_hash 扩展）。流程：Phase 1–3 开发（云端/设备可并行，payload schema 先冻结）→ 独立测试智能体（新用例 + 双端回归基线）+ 独立 CodeReview 智能体 → 主控整合 + Phase 4 文档收尾与真机验收交接。
