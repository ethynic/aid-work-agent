# 外部 Skill 插件机制 M1（知识层）开发计划

> 关联调研：[external-skill-plugin-integration-research.md](../research/external-skill-plugin-integration-research.md)（§4.1 A 层注册 / §5 分期 / §7 开放问题）
> ideas 条目：20261006-0958（系统功能分区）
> 修订：2026-10-06 按评审意见修订——M1 执行边界升级为「插件来源 skill 一律拦截云端执行」（原「M1 不执行」前提不成立，见 §3.1），拦截提前至 Phase 2；修正调用方事实、租户边界表述、缓存保证表述、既有测试改造标注，补测试缺口。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 审批门核心模块（hash/清单/同名拒绝）+ SkillLoader include/run_init | 🔧 进行中 | 代码完成（skill_plugin_gate.py + approve_skill_plugin.py + SkillLoader 参数），定向单测通过；独立测试/CR 发现已处置（结论见 Phase 5）；测试门未验证（环境 EACCES） |
| Phase 2 | **插件执行全量拦截（先行）** + 注册链接入（load_from_sources + resource_cache 签名刷新 + 配置 + 审批 CLI） | 🔧 进行中 | 拦截先于可见性合入（同一变更内，enabled 默认 false 消除窗口）；MCP 全局单例不接入 gate（空注册表 fail-closed，原因记录于 _check_execution_gate docstring）；定向单测通过；独立测试/CR 发现已处置（结论见 Phase 5）；测试门未验证（环境 EACCES） |
| Phase 3 | 租户合并链适配（tenant_skill_cache + skill_session，含既有测试 mock 面迁移） | 🔧 进行中 | mock 面已迁移到 gate；阻断级评审意见（同名租户 skill 被 _plugin_hashes 误伤）按「目录身份判定」方案消化并有回归断言；CR 第 1 轮修复：`_loaders` 起底改用 `_base_loaders` 快照（消除跨租户 loader 残留串读）、`is_plugin_skill` 增 `skill.dir` 目录身份 fail-closed 兜底 + skill_session 注回前剔除 stale 插件（revoke TTL 半状态执行门不失效），新增 3 回归用例；定向单测通过；独立测试/CR 发现已处置（结论见 Phase 5）；测试门未验证（环境 EACCES） |
| Phase 4 | metadata.execution 声明 + device 占位文案 + use_skill 提示 | 🔧 进行中 | get_execution_decl + 两类占位文案 + use_skill 尾部提示，定向单测通过；独立测试/CR 发现已处置（结论见 Phase 5）；测试门未验证（环境 EACCES） |
| Phase 5 | 独立测试 + CodeReview + 文档收尾 | ✅ 完成（2026-10-06） | 独立测试与评审共 10 条发现，P0/P1 3 条全部修复（拒绝 0）；测试门 EACCES 已解决（`bash` 前缀直跑）并在 Phase 6 补验通过；文档收尾完成（本进度区 + ideas 索引行 + 调研 §5 计划链接） |
| Phase 6 | 第二轮整改：CR P2 处置 + 测试门补验 + 33f9dba1 遗留修复 | ✅ 完成（2026-10-06） | ① CR P2/nit 处置：use_skill 篡改插件专门文案（与 not found 区分）、registry 目录链切换清空插件残留、resource_cache 两缓存改「触发签名标记」（cached_subagent_registry 补直接测试）、审批 CLI 7 用例、f9 TOCTOU 接受说明；use_skill 边界提示与通用指引冲突修复（有提示时跳过 guidance_suffix）。② 测试门补验：15 个单测文件 257 passed + 集成验收 26 passed。③ 33f9dba1 遗留（独立验证 P1，HEAD 纯净导出证实非 M1 引入）：62+6 例红测试改写/移植到新接缝（engine/ToolCatalog/ProfileResolver，新增 _agent_runtime_seam.py harness）、controls.py 控制工具 `_no_truncate` 豁免断链修复、tools.py before_tools 补 effective_enabled 门、context_assembler.py 续跑路径 UnboundLocalError 崩溃修复 |

## 1. 背景与范围

调研结论：外部 skill（AgentSkills 标准 SKILL.md 格式）与现有 SkillLoader 完全兼容，M1 只做**知识层**——插件 skill 放进目录链并对 Agent 可见（经审批），执行层路由属 M2。

### 1.1 M1 范围（硬边界）

1. **插件 skill 目录链**：registry 加载从单一 `src/skills` 扩展为多目录：`src/skills`（基础）→ 仓库根 `skills/`（第一方插件位，compose 已挂载未消费）→ `storage/skills/plugins/`（运维动态安装位），低→高优先级；租户目录链（最高优先级）不动。
2. **审批/hash 门**：插件目录中的 skill 默认不可见；需进入审批清单（含内容 hash 锁定，参考仓库根 `skills-lock.json` 的 computedHash 思路）。审批存储形态见 §3.2。
3. **同名冲突**：插件 skill 与内置 skill 同名时拒绝注册并告警（内置优先）。落地调研 §7.1 开放问题的建议方案。
4. **metadata.execution 声明**：从 SKILL.md frontmatter 的 `metadata` 字典读取（`Skill.metadata` 已有，`src/core/skill_loader.py:76`，不新增 dataclass 字段），提供读取 helper。
5. **执行边界（M1 安全基线）**：插件 skill 在 M1 **只读不可执行**——`execute_skill_command` 对所有插件来源 skill 一律拦截（不论是否声明 execution，理由见 §3.1）；对声明 `execution: device` 的 skill（任意来源）返回设备链路占位提示（M2）。设备执行链路本身属 M2，不实现。

### 1.2 明确不做（超出即砍）

- `local_tools/catalog.py`、`clients/agent-tool-runtime`、invocation 队列、任何设备侧改动（全部属 M2）；
- 插件 skill 的任何云端命令执行开放（M1 全拦，开放路径= M2 执行路由 + 设备侧门禁）；
- 管理端安装/审批 UI、租户市场、计费归类、设备侧 skill 自动分发、**租户级审批维度**（属 M4）；
- 不改 `SkillRegistry.load_from_directories` 既有覆盖语义与既有测试断言（多目录覆盖行为 `tests/unit/test_skill_registry.py:103` 保持不变）。

## 2. 现状关键事实（已核对）

| 事实 | 位置 |
|---|---|
| 进程级缓存只加载 `src/skills` 单目录，key=allowed，无刷新；**缓存实例被跨执行共享** | `src/services/agent_runner/runtime/resource_cache.py:28-42` |
| 同文件已有「签名刷新 + copy-on-write 换新」范例（subagent DB overlay，**其原子性前提是实例不可变——skill registry 不满足**，见 §3.6） | `resource_cache.py:77-99` |
| `load_from_directories` 已支持多目录低→高覆盖合并，但 `_loader` 兜底指向**最后一个存在的目录** | `src/core/skill_registry.py:97-146`（`_loader` 赋值 :143） |
| `get_content` 的 fallback 链：`_loaders` miss → `_loader` → **last-resort 直接拼 `skill.body`**（body 为 SKILL.md 正文全文） | `skill_registry.py:236-248`；body 来自 frontmatter split 全文 `src/core/skill_loader.py:196` |
| `match_by_file` / `reload` 同样依赖 `_loader` fallback | `skill_registry.py:302-311`、`:383-394` |
| **skill 执行链无沙盒**：`_execute_command` 直接 shell 执行（代码注释自认「直接执行，不使用沙盒」） | `src/core/skill_executor.py:578-584` |
| **`_process_command` 把命令中 `scripts/<name>`、`./scripts/<name>` 替换为该 skill 目录脚本的绝对路径**——插件手册只需引导 LLM 写出相对路径即可命中插件脚本 | `src/core/skill_executor.py:855-875` |
| `use_skill` 工具描述明确引导「手册要求执行脚本/命令 → 调用 skill_execute」（手册即执行指令） | `src/tools/skill/use_skill_tool.py:22`、`src/core/skill_registry.py:356-367` |
| `execute_skill_command` **真实调用方仅两处**：skill_execute_tool、MCP executor（模块 docstring 中的第三处是使用示例，非真实代码） | `src/tools/skill/skill_execute_tool.py:278`、`src/mcp/executor.py:162`；示例 `src/core/skill_executor.py:2-26` |
| `execute_skill_script` 是**第二执行入口，当前无任何调用方**（全仓 grep 确认），后续接线即绕过入口拦截 | `src/core/skill_executor.py:594` |
| `skill_session` 租户合并链：base 目录硬编码 `src/skills`，且依赖 `registry._loader` 重建 `_loaders`；**加载后原地改写进程级缓存共享实例的 `_skills`/`_loaders`** | `src/services/agent_runner/runtime/skill_session.py:61`、`:67`、`:88-89` |
| `tenant_skill_cache` 合并 = 单 base 目录 + 租户目录，TTL 300s | `src/saas/services/tenant_skill_cache.py:75-110` |
| **既有 `test_tenant_skill_cache.py` 全部 4 用例靠 monkeypatch `src.saas.services.tenant_skill_cache.SkillLoader` mock 基础加载**——base 改走 gate 后该 mock 失效 | `tests/unit/test_tenant_skill_cache.py:26-33` |
| SkillLoader 构造即扫描并**自动执行 skill 的 init_script**（importlib 任意代码） | `src/core/skill_loader.py:335-363`、`_init_skill_tables` :365-395 |
| SkillLoader 扫描跳过 `.` 开头子目录，按 frontmatter name 注册 | `src/core/skill_loader.py:341-359` |
| metadata 读取 helper 先例（`get_user_feedback`，读 `skill.metadata` 字典） | `src/core/skill_registry.py:198-215` |
| `SkillsConfig` 仅有 master_agent/subagent 两子模型；config.yaml 的 `skills.directories` 键**无 settings 字段、无消费方** | `src/config/settings.py:337-340`、`configs/config.yaml:226-231` |
| **allowed 语义：空列表 = 允许所有 skills**（master 与 subagent 同理）；生产 master_agent.allowed 现配 8 个、subagent.default_allowed 2 个——是名称白名单，**无租户维度** | `configs/config.yaml:238-252`、`src/services/agent_runner/runtime/profile.py:43`（`or None`） |
| compose 四服务已挂载 `./skills:/app/skills`（代码未消费）；storage 挂载 api/worker/runner 共享 | `docker-compose.agent-runner.yml:59,113,170,225`、`docker-compose.prod.yml:59,72` |
| 仓库根 `skills/`、`storage/skills/` 目录当前均不存在 | 本地 `ls` 确认（2026-10-06） |
| 全局 `skill_registry` 单例被 MCP server/tools 消费，`create_skill_executor` 惰性 `load_from_directory` 兜底 | `src/core/skill_registry.py:407`、`src/core/skill_executor.py:1051-1056`、`src/mcp/server.py:28`、`src/mcp/tools.py:24` |
| storage 根解析：`AGENT_RUNNER_STORAGE_ROOT` 环境变量优先，默认仓库根 storage | `src/core/storage.py:42-44` |

基线回归（本计划撰写时已运行，容器内 pytest）：`bash scripts/dev_test.sh tests/unit/test_skill_registry.py tests/unit/test_skill_loader.py tests/unit/test_tenant_skill_cache.py tests/unit/test_skill_executor_env.py -p no:cacheprovider -q` → **26 passed**。

## 3. 设计决策

### 3.1 执行边界（M1 安全基线）：插件来源一律拦截云端执行

**为什么「M1 只做知识层、不执行」不成立**：M1 的效果就是把插件手册喂给 LLM，而：

1. `use_skill` 工具描述明确引导「手册要求执行脚本/命令 → 调用 skill_execute」（`use_skill_tool.py:22`、`skill_registry.py:356-367`）——手册即执行指令；
2. `execute_skill_command` → `_execute_command` 直接 shell 执行、无沙盒（`skill_executor.py:578-584`）；
3. `_process_command` 会把命令中的 `scripts/<name>` 自动替换为**该 skill 目录脚本的绝对路径**（`skill_executor.py:855-875`）——插件手册只需写出相对路径即可让 LLM 命中插件自带脚本。

`execution: device` 声明是自愿的：不声明 device 的恶意插件手册即可指挥 LLM 在云端容器执行任意命令/其脚本。因此：

**M1 执行策略**：`execute_skill_command` 入口（取到 skill 后，`skill_executor.py:510` 附近）按判定顺序拦截：

1. `registry.get_execution_decl(skill_name)` 声明 `execution: device`（任意来源）→ 返回设备链路占位（§3.9 文案）；
2. 否则若 skill 为**插件来源**（`registry._plugin_hashes` 命中，§3.5）→ 返回「外部插件执行未开放」拦截（§3.9 文案）；
3. 其余（内置且未声明 device）→ 现状行为不变。

判定发生在 `_process_command` 路径替换与子进程创建**之前**，插件脚本不会被替换/执行。该拦截在 Phase 2 **先于可见性放行落地**（§4）：先合拦截、后合「插件可见」，配合 `enabled` 默认 false，消除「插件可见但拦截未上」的窗口。`execute_skill_script` 第二入口（`skill_executor.py:594`，当前无调用方）同样在入口处走同一判定函数，防止后续接线绕过。

`run_init=False`（下条）降级为纵深防御的一层（挡 importlib 支路），**不构成充分保证**——主执行通道的关闭只由本条入口拦截负责。

### 3.2 新模块 `src/core/skill_plugin_gate.py`：目录链构建 + 审批门（唯一可信来源）

审批门必须同时被 runner 进程缓存（`resource_cache`）与租户合并链（`tenant_skill_cache`）复用，防止租户链绕过审批，因此落 `src/core/` 而非 runner 私有目录。职责与接口（实现可微调，语义锁定）：

```python
# 清单读写（写方为 CLI，必须临时文件 + os.replace 原子 rename，见下）
read_approvals(approvals_file) -> Dict[str, dict]
    # 返回 {skill_name: {"computedHash": str, "skillMdHash": str, ...}}
    # 文件缺失 → {}；JSON 损坏 → {} + logger.warning（fail-closed：全部插件不可见）
scan_plugin_dir(plugin_dir, approvals, builtin_names) -> PluginScan
    # 扫描插件目录一级子目录（轻量解析 SKILL.md frontmatter name，无代码执行）；
    # 逐个判定：① name 在审批清单 ② compute_skill_dir_hash 与清单一致 ③ name 不与内置同名
    # 通过 → include 集合；未通过 → 剔除 + logger.warning（注明原因）
compute_skill_dir_hash(skill_dir) -> str
    # 排除 __pycache__/、*.pyc、.DS_Store、shots/（截图产物）、log/ 后，
    # 按「相对路径排序 + 每文件 sha256」聚合成目录 sha256；skillMdHash 单独记录 SKILL.md 文件 sha256
build_base_registry_sources(builtin_dir, settings) -> BaseSources
    # 目录链构建：内置 SkillLoader（init 照旧）+ 各插件目录 SkillLoader(include=通过集, run_init=False)
    # 返回 (loaders: Dict[name, SkillLoader], plugin_hashes: Dict[name, (dir_hash, md_hash)], warnings)
plugin_signature(settings) -> tuple
    # 轻量缓存签名：(审批清单 mtime+size, 各插件目录一级子目录 (name, mtime) 有序元组)
    # enabled=False 时恒为 ()（缓存退化为现状永久缓存）
```

**安全关键点 1——init_script 支路**：SkillLoader 构造即执行已注册 skill 的 `init_script`（`skill_loader.py:363`）。插件 skill 是外部内容，即使审批通过也不能在云端容器执行其 Python 脚本。`SkillLoader.__init__` 增加 `include: Optional[Set[str]] = None`（按 frontmatter name 在注册前过滤，未注册即不参与 `_init_skill_tables`）与 `run_init: bool = True`，默认值保持现状行为；插件目录 loader 一律 `run_init=False`。此为纵深防御第一层，主执行通道的关闭由 §3.1 入口拦截负责。

**安全关键点 2——审批清单原子写**：审批 CLI 的 `--approve/--revoke` 写 `plugin-approvals.json` 必须采用「写临时文件 + `os.replace` 原子 rename」。清单参与签名轮询且被 runner 并发读：半写 JSON 会触发「损坏 → {} → 全部插件不可见」的 fail-closed 闪断（安全但造成全量可见性抖动），原子写消除该窗口。

**审批清单无租户维度（如实声明）**：`plugin-approvals.json` 是平台级清单。结合「allowed 空列表 = 允许所有」语义（`config.yaml:238-239`、`profile.py:43` `or None`）与 subagent 同理的配置，**实际租户边界是：一次审批 + 该 skill 名进入（或本就不受）allowed 白名单 = 所有租户的 master/subagent 立即可见可用**。生产现配 master_agent.allowed 8 个名称、subagent.default_allowed 2 个（`config.yaml:243-252`），它们是名称白名单而非租户关卡；不存在第三道「租户绑定」门对**内置/插件基础 skill** 生效（租户绑定只管租户目录自定义 skill）。租户级审批/上架属 M4，M1 文档（skills/README + 本计划）必须向运维明示这一边界。

### 3.3 内容完整性门（对抗审批后篡改）

M1 关闭插件执行通道（§3.1）后，`scripts/` 等资源篡改在 M1 已无执行面（M2 设备侧另有 hash 门）；知识层剩余的篡改影响面是**手册内容本身**（提示注入载体），由两层门覆盖：

- **Layer 1（可见性门，注册表重建时）**：`plugin_signature` 触发重扫时做完整目录 hash 校验（`computedHash`），不符则剔除该 skill。签名为轻量 stat（清单 mtime + 一级子目录 mtime），深层文件修改不改一级子目录 mtime——签名可能不变、缓存不重建。
- **Layer 2（内容门，读手册时）**：`SkillRegistry` 新增内部映射 `_plugin_hashes: Dict[str, Tuple[str, str]]`（由 `load_from_sources` 填充，非 Skill dataclass 字段）；`get_content()` 对命中 `_plugin_hashes` 的 skill：**强制走 loader 路径**（`_loaders` miss 时直接返回 None，不走 `_loader` fallback、也不走 last-resort `skill.body`——body 为 SKILL.md 正文全文，`skill_registry.py:243-247`，放行即泄漏篡改后手册），读盘后校验 SKILL.md sha256 == `skillMdHash`，不符返回 None 并 `logger.warning`（use_skill 侧转为「插件内容与审批时不符，已暂停提供」错误，fail-closed）。
- 注册表缓存里的旧 Skill 对象在窗口期仍指向磁盘路径，Layer 2 封住读出内容；两层叠加后窗口期风险收敛为「描述行（name+description，工具面可见）暂未更新」，可接受并写入风险表。

### 3.4 同名冲突策略

| 冲突 | 行为 |
|---|---|
| 插件 vs 内置（`src/skills`） | **拒绝注册 + logger.warning**（含两侧路径）；在 gate 扫描阶段剔除，内置优先 |
| 插件 vs 插件（`skills/` vs `storage/skills/plugins/`） | 按目录链语义高优先级（storage）覆盖低优先级（skills/）+ warning；沿用 `load_from_directories` 覆盖方向（`skill_registry.py:122-126`） |
| 租户 vs 基础/插件 | 既有租户覆盖语义不动（`tenant_skill_cache.py:90-97`、`skill_session.py:83-85`） |

拒绝逻辑放 gate（目录链构建策略层），不改 `load_from_directories` 的通用覆盖语义，既有测试零改动。

### 3.5 `SkillRegistry.load_from_sources()`：M1 专用加载入口（新方法，不动旧方法）

```python
def load_from_sources(self, builtin_dir: Path, plugin_loaders: List[SkillLoader],
                      plugin_hashes: Dict[str, Tuple[str, str]], allowed=None) -> int
```

- 内置目录：`SkillLoader(builtin_dir)`（run_init 默认 True，现状行为）；
- 插件 loaders 由 gate 构造好传入（include + run_init=False），按低→高合并，防御性复查「与内置同名拒绝」；
- 填充 `_skills`/`_all_skills`/`_loaders`/`_plugin_hashes`，allowed 过滤逻辑与 `load_from_directories` 一致；三个 `load_from_*` 入口同时维护 `_base_loaders` 快照（base loaders 副本，供 skill_session 起底，见 §3.7），并新增 `is_plugin_dir`（skill 目录是否落在 `resolve_plugin_dirs` 插件根目录链之下）——`is_plugin_skill` 在 loader 与审批 hash 均未命中时按 `skill.dir` 目录身份 fail-closed 兜底（CR 第 1 轮：revoke/hash 不符触发签名链重建后新实例 `_plugin_hashes` 为空，若租户 TTL 半状态把旧合并结果注回 `_skills`，名字键控双 miss 会放行执行，目录身份兜底保证 §3.1 全量拦截在该窗口不失效）；
- **`_loader` 显式置 None（fail-closed）**：多目录链下 `_loader` 的「最后目录」语义（`skill_registry.py:143`）只会造成错位；置 None 后 `get_content` 对插件 skill 的保护由 §3.3 Layer 2 的「强制 loader 路径」承接，`match_by_file` 走 `_loaders` 多 loader 路径（`skill_registry.py:302-307`）不受影响；`reload()` 对本加载链不支持（调用时 warning 并返回当前数量），租户 TTL 窗口内被撤销插件经「`_skills` 旧值 → `_loaders` miss → `_loader` None」路径得到 not found，而非走 body 兜底泄漏手册。
- **不触碰** `load_from_directory`/`load_from_directories`（`skill_registry.py:58-146`）及其调用方。

### 3.6 `resource_cache.cached_skill_registry` 升级为签名刷新（保证范围如实界定）

仿 `cached_subagent_registry`（`resource_cache.py:77-99`）的机制，但**不照搬其保证**：

1. key 仍为 `frozenset(allowed)`；每次调用计算 `plugin_signature()`（几次 stat，微秒级）；
2. 签名不变 → 命中缓存返回；变化 → 锁外重建（gate 扫描 + `load_from_sources`）→ 锁内复核签名 → 原子换新引用；**换新只保证「重建时刻」的快照一致**——与 subagent 缓存不同，skill registry 实例并非不可变：`skill_session._ensure_tenant_skills_loaded` 会**原地改写共享实例的 `_skills`/`_loaders`**（`skill_session.py:88-89`），这是现状已有的跨执行/跨租户 overlay 污染，M1 不修复但必须定义语义：两次重建之间实例可能携带某租户的 overlay；重建换新后新实例**不含** overlay，由下一次租户执行重新叠加。该语义可接受（overlay 内容仍受租户目录+allowed 约束），写入风险表并有并发交错测试（§6）。
3. `skills.plugins.enabled=False` → 签名恒 ()，行为与现状完全一致（永久缓存）；**enabled=True 但插件目录/清单不存在** → 签名同样恒定（空元组内容），行为等同永久缓存，不产生重复重建。
4. 多 worker/多进程各自持有进程缓存靠签名轮询最终一致——审批/安装/撤销后**下一次执行生效**，无需重启或跨进程通知（与 subagent DB overlay 同模式）。
5. **重建会重放内置 init DDL**：签名变化整体重建时 builtin SkillLoader 重新构造，内置 skill 声明的 `init_script` 会重新 importlib + `init_tables()`（`skill_loader.py:365-395`；`src/skills/` 下多个 skill 声明 init_script）。频率低（仅插件/清单变更时）且 `init_tables` 应幂等，可接受——**前提是 init_tables 幂等**，此为已知约束写入风险表，不宣称「缓存掉 init DDL 的收益完全保留」。

### 3.7 租户合并链适配（不得破坏既有语义）

- `tenant_skill_cache._load_and_merge`（`tenant_skill_cache.py:75-110`）：base 部分从「单目录 SkillLoader」改为调用 gate 的 `build_base_registry_sources()`（内置+已审批插件），租户目录仍覆盖其上；TTL 300s 机制不动，插件变更最迟随 TTL 过期进入租户视图。
- `skill_session._ensure_tenant_skills_loaded`：`:61` 硬编码 `base_skills_dir = src/skills` 改为从缓存 registry / gate 获取合并结果；`:67` `base_loader = self.skill_registry._loader` 改为以 **`registry._base_loaders` 快照**（`load_from_*` 全量加载时维护的 base loaders 副本，见 §3.5）起底再叠租户 loader——修正多目录下 `_loader` 指向最后一个目录的隐患（`skill_registry.py:143`）。§3.6 第 2 点定义的「实例被 overlay 原地改写」语义即来自此处，M1 保持该行为不重构。**CR 第 1 轮修订**：不得以 `_loaders` 当前值起底——共享实例被前一次租户执行改写后，其中已含该租户 loader 映射，下一租户起底会带入残留（本租户未覆盖同名时不被冲掉），导致 get_content 经 stale loader 跨租户串读、插件同名场景 `is_plugin_skill=False` 使执行拦截失效；快照缺失时按目录身份过滤（内置目录 ∪ 插件目录）兜底。配套：注回 `_skills` 前剔除「不在当前 `_plugin_hashes` 且 `skill.dir` 落在插件根目录链下」的 stale 条目（revoke/hash 不符后租户 TTL 300s 半状态，配合 §3.5 的 `is_plugin_skill` 目录身份兜底双保险）。
- `_all_skills` 不在租户合并时改写（保持现状：管理后台全集 = 内置 + 已审批插件）。
- **既有测试必然改造（如实标注，非回归）**：`tests/unit/test_tenant_skill_cache.py` 现有 4 个用例全部 monkeypatch `src.saas.services.tenant_skill_cache.SkillLoader`（`:26-33`）mock 基础加载；base 改走 gate（gate 落 `src/core/` 自持 SkillLoader 引用）后该 mock 对 base 不再生效。Phase 3 的验收包含**将 mock 面从 `tenant_skill_cache.SkillLoader` 迁移到 gate 的目录扫描入口**（如 monkeypatch `skill_plugin_gate` 的扫描/加载函数或 `src.core.skill_plugin_gate.SkillLoader`），改造后原 4 用例语义保持；禁止为实现时保绿而让 tenant 链绕开 gate。

### 3.8 `metadata.execution` 读取 helper

`SkillRegistry.get_execution_decl(name) -> Optional[Dict[str, Any]]`，仿 `get_user_feedback`（`skill_registry.py:198-215`）：

- 读 `skill.metadata`，返回 `{"execution": "server"|"device", "device_requirements": ..., "entry": [...]}`；
- skill 不存在 / metadata 无 execution 键 → None（调用方按默认 server 处理）；
- execution 值非法（非 server/device）→ warning + 按 server 处理（fail-safe，不炸加载链）。

### 3.9 拦截文案（execute_skill_command 入口，判定顺序见 §3.1）

- **插件来源（未声明 device）**：

  > 技能 '{name}' 来自外部 Skill 插件。外部插件的命令执行链路尚未开放（规划 M2），当前禁止执行。请勿重试 skill_execute 或尝试改写命令形式；可基于已加载手册回答用户咨询，或说明该技能的执行功能即将支持。

- **声明 `execution: device`（任意来源，含插件）**：

  > 技能 '{name}' 声明为设备执行（metadata.execution=device）。设备执行链路尚未开放（规划 M2），当前无法执行该技能的脚本。请勿重试 skill_execute，可向用户说明该技能即将支持、当前暂不可用。

- `use_skill` 加载插件来源 skill 的手册时在返回 content 尾部追加一行同义提示（LLM 读手册即知不可执行，减少无效 tool call）；
- 文案均含「请勿重试」，防 LLM 反复试错；M2 落地后按路由结果替换。

### 3.10 配置（settings + config.yaml 同步）

```python
class SkillPluginsConfig(BaseModel):
    enabled: bool = False                      # 总开关，默认关（M1 合入即安全）
    repo_dir: str = "skills"                   # 仓库根第一方插件位（相对仓库根，容器内 /app/skills）
    storage_subdir: str = "skills/plugins"     # 运维安装位（相对 configured_storage_root()）
    approvals_subpath: str = "skills/plugin-approvals.json"  # 审批清单（相对 storage 根）

class SkillsConfig(BaseModel):
    master_agent: ...
    subagent: ...
    plugins: SkillPluginsConfig = Field(default_factory=SkillPluginsConfig)
```

- storage 相关路径经 `configured_storage_root()`（`src/core/storage.py:42`）解析，兼容容器 `AGENT_RUNNER_STORAGE_ROOT`；
- `configs/config.yaml` skills 节新增 `plugins:` 段（含注释示例）；既有 `skills.directories`（无消费方）追加注释标注「暂未消费，插件目录链由 skills.plugins 定义」，避免双源误导，不在 M1 删除；
- 仓库根新建 `skills/README.md`（说明用途、审批步骤、**§3.2 租户边界声明**、指向本计划），使 compose 挂载位在 git 中可见。

## 4. 阶段划分与验收

| 阶段 | 任务 | 验收 |
|------|------|------|
| **Phase 1 审批门核心** | `skill_plugin_gate.py`（read_approvals / compute_skill_dir_hash / scan_plugin_dir / plugin_signature）；SkillLoader 加 `include`+`run_init` 参数；`scripts/approve_skill_plugin.py`（含原子写） | gate 单测全绿；SkillLoader 默认参数行为与现状一致（既有 test_skill_loader.py 回归通过） |
| **Phase 2 执行拦截先行 + 注册链接入** | **步骤 a（先行合入）：`execute_skill_command` 与 `execute_skill_script` 入口的插件来源全量拦截 + device 占位判定骨架（§3.1）**；步骤 b：`load_from_sources`（含 `_plugin_hashes`、`_loader=None`、get_content 内容门）；resource_cache 签名刷新；settings/config.yaml；`skills/README.md`；核对 MCP 全局单例路径（`skill_executor.py:1051-1056` 惰性加载与 `src/mcp/server.py:28`）——接入同一 gate 或记录不接入原因 | **a 先于 b 合入与验证**（无插件可见时拦截已生效）；enabled=False 行为与现状一致；插件目录放入已审批 skill 后 use_skill 工具面出现；未审批/hash 不符/同名内置均不可见且日志告警；插件 skill 走 skill_execute 与 MCP 两路径均被拦截 |
| **Phase 3 租户链适配** | tenant_skill_cache 多目录 base；skill_session `_loaders` 修正 + base 目录来源改造；**既有 4 用例 mock 面迁移**（§3.7） | 迁移后原用例语义保持 + 新增「内置+插件+租户」三层合并 get_content 定位用例；skill_session overlay 与签名重建交错行为符合 §3.6 第 2 点定义 |
| **Phase 4 execution 声明与占位完善** | `get_execution_decl`；device 占位文案终版；use_skill 手册尾部提示 | device skill（含内置声明 device 的假设场景）返回设备占位；插件未声明 device 返回插件拦截文案；无声明内置 skill 行为不变 |
| **Phase 5 独立验证** | 独立测试智能体（新测试 + 回归）+ 独立 CodeReview 智能体；`.claude/rules/cache_usage.md` 登记 resource_cache 变更（如该文档涵盖进程缓存）；更新本进度区与 ideas 索引行 | 测试/CR 报告落档；文档登记完成 |

## 5. 文件清单

**修改**：

| 文件 | 改动 |
|---|---|
| `src/core/skill_loader.py` | SkillLoader `include`/`run_init` 参数（默认行为不变） |
| `src/core/skill_registry.py` | `load_from_sources()`（含 `_loader=None`）、`_plugin_hashes`、`get_content` 内容门（插件强制 loader 路径）、`get_execution_decl()` |
| `src/core/skill_executor.py` | `execute_skill_command` / `execute_skill_script` 入口拦截（插件来源全量 + device 占位，判定函数复用） |
| `src/tools/skill/use_skill_tool.py` | 插件/device skill 手册尾部提示 |
| `src/services/agent_runner/runtime/resource_cache.py` | `cached_skill_registry` 签名刷新 + 多目录链 |
| `src/services/agent_runner/runtime/skill_session.py` | base 来源改造 + `_loaders` 重建修正 |
| `src/saas/services/tenant_skill_cache.py` | `_load_and_merge` base 改走 gate |
| `src/config/settings.py` | `SkillPluginsConfig` + SkillsConfig.plugins |
| `configs/config.yaml` | skills.plugins 段 + directories 注释标注 |
| `tests/unit/test_tenant_skill_cache.py` | 既有 4 用例 mock 面迁移（§3.7，语义保持） |

**新增**：

| 文件 | 内容 |
|---|---|
| `src/core/skill_plugin_gate.py` | 审批门 + 目录链构建 + hash + 签名 + 原子写读（§3.1/§3.2） |
| `scripts/approve_skill_plugin.py` | 审批 CLI（--scan/--approve/--revoke，临时文件 + os.replace 原子写） |
| `skills/README.md` | 第一方插件位说明 + 审批步骤 + 租户边界声明 |
| `tests/unit/test_skill_plugin_gate.py` | §6 用例 |
| `tests/unit/test_skill_registry_load_sources.py` | §6 用例 |
| `tests/unit/test_resource_cache_plugin_refresh.py` | §6 用例 |
| `tests/unit/test_skill_execute_plugin_guard.py` | §6 用例（含 device 占位与插件拦截） |

**扩展既有测试**：`tests/unit/test_skill_registry.py`（get_execution_decl 用例）。

## 6. 测试计划

运行命令统一：`./scripts/dev_test.sh <文件> -p no:cacheprovider -q`（容器可用时容器内执行）。

| 文件 | 覆盖点 |
|---|---|
| test_skill_plugin_gate.py（新） | 目录 hash：内容变更敏感、排除项生效、文件遍历顺序无关；清单缺失/损坏 JSON → 空 + fail-closed；CLI 原子写（写过程中并发读不见半写 JSON）；未审批不可见；hash 不符剔除 + warning；与内置同名拒绝 + warning；插件间同名高优先级覆盖 + warning；签名对清单 mtime / 子目录变化敏感；**enabled=True 但插件目录/清单不存在 → 签名恒定、不重复重建（行为等同永久缓存）** |
| test_skill_registry_load_sources.py（新） | load_from_sources 合并顺序与 allowed 过滤；`_all_skills` 含已审批插件；`_plugin_hashes` 填充；**`_loader` 为 None**：get_content 插件 skill 在 `_loaders` miss 时返回 None（不走 body last-resort，`skill_registry.py:243-247` 不泄漏全文）；内容门：篡改 SKILL.md 后返回 None + warning，内容一致时正常返回；插件 loader 不执行 init_script（monkeypatch 断言）；内置 loader 照旧执行 init（现状回归） |
| test_skill_execute_plugin_guard.py（新） | **未声明 device 的插件 skill 走 skill_execute 被拦截**（success=False、文案含「请勿重试」）；**插件 scripts/ 路径替换不生效**（拦截先于 `_process_command`，无子进程创建，可用 monkeypatch 断言 `_execute_command`/`_process_command` 未被调用）；MCP executor 路径（`src/mcp/executor.py:162`）同样拦截；`execute_skill_script` 入口同样拦截；execution=device（含插件与内置假设场景）→ 设备占位文案；插件+device → device 文案优先；server/无声明内置 skill 不拦截；get_execution_decl 非法值 fail-safe；use_skill 插件手册尾部含提示 |
| test_resource_cache_plugin_refresh.py（新） | enabled=False 命中缓存不重扫（现状等价）；插件目录/清单变更触发重建；未变更命中缓存；重建失败保留旧 registry；**重建重放内置 init DDL**（monkeypatch 断言 init_tables 被再次调用，标注幂等前提）；**租户 overlay 与签名重建并发交错**：实例被 skill_session 改写 `_skills`/`_loaders` 后重建换新 → 新实例不含 overlay 且行为正常（§3.6 第 2 点语义断言） |
| test_tenant_skill_cache.py（改造+扩展） | mock 面迁移后原 4 用例语义保持；base 含插件（已审批可见/未审批不可见）；租户覆盖插件同名；allowed 过滤不污染缓存；**--revoke 生效时延**：runner 签名链重建后新实例不可见，tenant TTL 300s 窗口内 use_skill 工具描述仍列出该 skill（`_skills` 旧值，`skill_registry.py:349-352`）但 get_content 返回 not found——断言该半状态符合定义（不引入跨进程失效，文档写明时延语义） |
| test_skill_registry.py（扩展） | get_execution_decl 正常/缺失/非 dict metadata |
| 回归基线 | test_skill_registry.py、test_skill_loader.py、test_skill_executor_env.py 全绿；test_tenant_skill_cache.py 以「迁移后」形态全绿（撰写本计划时原形态 26 passed 已记录） |

测试均用 tmp 目录构造插件/清单，不触碰真实 `storage/`。

## 7. 风险与对策

| # | 风险 | 对策 |
|---|---|---|
| 1 | **插件手册诱导云端任意命令执行**（use_skill 引导 + 无沙盒 shell + `scripts/` 路径自动替换，§3.1 三项事实） | execute_skill_command / execute_skill_script 入口对插件来源**全量拦截**（不看自愿声明），Phase 2 步骤 a 先于可见性合入；run_init=False 仅作 importlib 支路纵深防御 |
| 2 | 审批后内容被篡改（深层文件修改不触发签名变化） | 两层门：重建时目录 hash 全量校验 + get_content 强制 loader 路径并校验 SKILL.md hash（§3.3，含 body last-resort 封堵）；窗口期仅暴露描述行 |
| 3 | **租户合并链回归**：`_loader` 在多目录下指向最后目录（`skill_registry.py:143`），`skill_session.py:67` 依赖它 | Phase 3 专项改造为 `_loaders` 映射起底；test_tenant_skill_cache 既有用例 mock 面迁移 + 新增三层合并用例 |
| 4 | **进程缓存实例非不可变**：skill_session 原地改写共享实例 `_skills`/`_loaders`（`skill_session.py:88-89`，现状已有跨租户 overlay 污染） | M1 不重构，但 §3.6 第 2 点显式定义换新语义（重建时刻快照；新实例不含 overlay，由下次租户执行重建），并有并发交错测试锚定行为 |
| 5 | **多租户边界**：审批清单无租户维度，allowed 空即全放行——一次审批+进白名单 = 全租户立即可见 | §3.2 如实声明 + skills/README 运维提示；租户级审批/上架属 M4；M1 期间审批从紧（note 必填来源与用途） |
| 6 | 多进程缓存一致性：Gunicorn/runner 各进程独立缓存 | 签名轮询最终一致（下一次执行生效），与 subagent overlay 同模式；`--revoke` 的 tenant TTL 300s 半状态已定义并有测试断言（§6） |
| 7 | 重建重放内置 init DDL，依赖 `init_tables` 幂等 | 频率低（仅插件/清单变更），幂等为既有的隐含前提，M1 将其转为显式约束并测试断言；发现非幂等 init 再单独修复 |
| 8 | 管理后台技能选择器将出现已审批插件 skill（`_all_skills` 扩大） | 预期行为；说明写入 ideas 说明与 skills/README，审批 note 可标注来源 |
| 9 | 审批 ≠ 可用：插件 skill 还需进 master_agent.allowed / subagent default_allowed 才对 Agent 生效（但进白名单即全租户，见风险 5） | M1 文档明确运维步骤（审批 → allowed → 生效）与租户边界；管理端流程属 M4 |
| 10 | MCP 全局单例路径（`src/mcp/server.py:28` 等）与新链行为不一致 | Phase 2 核对并接入同一 gate，或记录不接入原因，禁止静默分叉 |
| 11 | `skills.directories`（config.yaml 既有未消费键）与 `skills.plugins` 双源混淆 | directories 加「暂未消费」注释，M1 只认 skills.plugins |
| 12 | 签名 stat 与目录 hash 的性能开销 | 签名仅几次 stat；目录 hash 只在签名变化时重扫或审批 CLI 时计算；插件目录规模小（个位数 skill） |
| 13 | device/插件拦截文案引发 LLM 反复重试 | 文案明确「请勿重试 + 向用户说明」，use_skill 手册尾部前置提示；M2 落地后按路由结果替换 |

## 8. 开发流程级别

按 [.claude/rules/dev_workflow.md](../../.claude/rules/dev_workflow.md) §1 定级：**高风险**——触及核心工具装配链（skill 供给）、**供应链安全门与执行边界（审批/hash/执行拦截）**、进程缓存一致性、租户合并链（跨模块契约）。流程：开发 → 独立测试智能体 + 独立 CodeReview 智能体 → 主控整合验证（Phase 5）。
