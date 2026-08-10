# 协会信息收集客户端 — 开发计划

> **交付目标**：2026-08-10（下周一）交付给客户。
> **设计依据**：[association-client-design.md](association-client-design.md)
> **开发流程**：严格遵循 `.claude/rules/dev_workflow.md` 三智能体流程（开发→测试→CodeReview）。

---

## 开发顺序总览

```
Phase 0: 风险验证（Day 1）           ← 必须先过，否则后续全部阻塞
    │
    ├─ 0.1 PyInstaller + Playwright 打包验证（最高风险）
    ├─ 0.2 Electron spawn CLI 子进程验证
    └─ 0.3 微信 RPA 在 CLI exe 内运行验证
    │
Phase 1: 服务端 API（Day 1-2）       ← 核心计费链路
    │
    ├─ 1.1 数据表 + DB 访问层
    ├─ 1.2 鉴权中间件
    ├─ 1.3 LLM/OCR/WebSearch 代理端点（含计费×5）
    ├─ 1.4 激活码管理端点
    └─ 1.5 单测
    │
Phase 2: 客户端 CLI（Day 2-3）       ← 业务逻辑载体
    │
    ├─ 2.1 ProxyLLMGateway / ProxyWebSearchTool
    ├─ 2.2 CLI 入口（activate/credits/collect）
    ├─ 2.3 NDJSON 进度输出
    ├─ 2.4 PowerShell 脚本改造（judge/ocr 走 HTTP）
    └─ 2.5 PyInstaller 打包
    │
Phase 3: Electron 客户端（Day 3-4）  ← GUI 壳
    │
    ├─ 3.1 工程脚手架（复用 agent-desktop 模式）
    ├─ 3.2 激活页
    ├─ 3.3 收集主页（输入+进度+日志）
    ├─ 3.4 结果展示页
    └─ 3.5 积分页
    │
Phase 4: 联调与打包（Day 4-5）       ← 端到端验证
    │
    ├─ 4.1 三端联调（Electron + CLI + 服务端）
    ├─ 4.2 electron-builder 打包完整安装包
    └─ 4.3 干净机器验收
    │
Phase 5: 交付准备（Day 5-6）
    │
    ├─ 5.1 生成客户激活码 + 租户充值
    ├─ 5.2 编写交付操作手册
    └─ 5.3 最终验收
```

**时间预估**：Phase 0-3 约 4 人天（核心），Phase 4-5 约 2 人天（验收交付），合计 6 人天。下周一可交付。

---

## Phase 0：风险验证（Day 1，最高优先）

> **目的**：尽早暴露打包和子进程调用的技术风险。Phase 0 任一项失败，必须立即调整方案，不得进入后续 Phase。

### 0.1 PyInstaller 打包 + Playwright 预装方案验证

**已定方案（不再讨论）**：Playwright Chromium **不打包进 exe**，由客户在电脑上预装。CLI 启动时检测 `%LOCALAPPDATA%\ms-playwright\chromium-*`，缺失则 fail-loud 提示。

**验证步骤**：
1. 创建最小化 Python 脚本，用 PyInstaller 打包（**不含浏览器二进制**）：
   ```python
   # spike_playwright.py
   import asyncio
   from playwright.async_api import async_playwright

   async def main():
       async with async_playwright() as p:
           browser = await p.chromium.launch(headless=False)
           page = await browser.new_page()
           await page.goto("https://www.baidu.com")
           title = await page.title()
           print(f"title={title}")
           await browser.close()

   asyncio.run(main())
   ```
2. `pyinstaller --onefile spike_playwright.py`（只打 Python 包，不带浏览器）
3. 预装 Playwright Chromium：`pip install playwright && playwright install chromium`
4. 运行 exe，验证 Chromium 能从用户目录找到浏览器并启动
5. 验证 `playwright_check.py` 检测逻辑：删除 ms-playwright 目录后运行 exe，确认 fail-loud 提示正确
6. 验证 `install-playwright.cmd` 能引导完成预装

**决策分支**：
- ✅ 成功 → 按预装方案推进，交付手册写明步骤
- ❌ PyInstaller 打包 playwright 包本身失败 → 排查 hiddenimports（`collect_all('playwright')`）
- ❌ exe 找不到预装的浏览器 → 检查 `PLAYWRIGHT_BROWSERS_PATH` 环境变量兼容性

**产出**：验证报告 + 可用的 `build.spec` 模板 + `install-playwright.cmd` 脚本。

**预估**：半天（风险大幅降低，原"打包浏览器二进制"风险已消除）。

### 0.2 Electron spawn CLI 子进程验证

**风险**：Electron 主进程 spawn PyInstaller exe，stdout NDJSON 解析是否正常。

**验证步骤**：
1. 创建测试 CLI（`test-cli.exe`），每隔 1 秒输出一行 JSON：
   ```jsonl
   {"event":"progress","step":"test","status":"running","progress":50}
   {"event":"complete","total_consumed":0}
   ```
2. Electron 主进程 spawn 它，流式解析 stdout
3. 验证事件能正确转发到 renderer

**预估**：2 小时。

### 0.3 微信 RPA 在 CLI exe 内运行验证

**风险**：PyInstaller exe 内 spawn PowerShell 脚本，脚本路径、Python 解释器路径是否正确。

**验证步骤**：
1. 将 `wechat-souyisou-rpa/scripts/*.ps1` 复制到 CLI 工程
2. PyInstaller 打包时带入 scripts
3. 运行 exe，验证能 spawn `powershell.exe -File wechat-souyisou.ps1 -Command probe`
4. 验证 `probe` 命令能正常探测微信窗口

**预估**：半天。

---

## Phase 1：服务端 API（Day 1-2）

> **前置**：Phase 0.1 无需通过即可开始 Phase 1（服务端改造独立于打包）。

### 1.1 数据表 + DB 访问层

**任务**：
- 在 `deploy/db_update.sql` 追加 3 张表的 DDL（`client_activation_codes`、`client_bindings`、`client_usage_logs`），见设计文档 §2.1
- 新建 `src/db/client_binding_db.py`：
  - `ClientActivationCodeDB`：`create()` / `get_by_code()` / `mark_used()` / `list()` / `disable()`
  - `ClientBindingDB`：`create()` / `get_by_token()` / `get_by_id()` / `update_status()` / `rotate_token()` / `list()`
- 新建 `src/db/client_usage_db.py`：
  - `ClientUsageLogDB`：`create()`（同事务扣减 tenant 余额）/ `get_tenant_usage()` / `get_binding_usage()`

**验收标准**：
- 表创建成功，字段类型与设计文档一致
- DB 访问层方法有单测覆盖
- 扣减余额的原子性（SELECT FOR UPDATE 或单 UPDATE 语句）

**预估**：半天。

### 1.2 鉴权中间件

**任务**：
- 新建 `src/api/client_auth.py`，实现 `verify_client_token(access_token)` 函数（设计文档 §2.3）
- Redis 缓存（key: `client_token:{access_token}`，TTL 300s）
- 返回 `ClientBinding` 对象（含 tenant 信息）

**验收标准**：
- 无效 token 返回 None
- 禁用的 binding 返回 None
- 租户非 active 返回 None
- Redis 缓存命中率可观测

**预估**：2 小时。

### 1.3 LLM/OCR/WebSearch 代理端点（核心）

**任务**：
- 新建 `src/api/client_routes.py`，实现 6 个端点（设计文档 §2.2）
- 新建 `src/api/client_llm_proxy.py`，实现 LLM 代理 + 计费逻辑（设计文档 §2.4）
- 余额检查（≤0 返回 402）
- ×5 系数扣费（同事务 INSERT log + UPDATE balance）
- 在 `src/main.py` 注册路由

**验收标准**：
- `POST /api/client/v1/llm/chat` 能正确调用 llm_gateway 并返回结果
- 计费准确：`credit_cost = ceil(raw_credit × 5 × 100) / 100`
- 余额不足时返回 402
- OCR / WebSearch 不扣费但记录调用
- 单测覆盖计费公式（含 ×5 边界值）

**预估**：1 天。

### 1.4 激活码管理端点 + 后台界面

**任务**：
- 新建 `src/saas/api/client_activation_mgmt.py`（设计文档 §2.5）
- 生成激活码：`AC-` + 12位去混淆字符
- bcrypt 哈希
- 仅 platform_admin 可访问
- **★ 新建前端组件 `frontend/src/components/tenant/ClientActivationManager.vue`**（设计文档 §2.5.1）
  - 在「租户管理 - 编辑租户」弹窗内挂载，与「数字员工授权」「API 配置」平级
  - 「+ 生成激活码」按钮 → 弹窗输入 client_name/expires_at → 调 API → 显示激活码明文（仅一次）
  - 激活码列表表格（激活码/客户端名/状态/激活次数/操作）
  - 「禁用」「吊销绑定」操作按钮

**验收标准**：
- 生成的激活码格式正确
- 激活后 used_count 递增，达到 max_uses 则 status='used'
- 过期激活码不可使用
- 后台「编辑租户」页面可见激活码管理区块，能生成/查看/禁用激活码

**预估**：1 天（后端半天 + 前端半天）。

### 1.5 Phase 1 单测

**任务**：
- `tests/unit/api/test_client_routes.py`：各端点的 happy path + error path
- `tests/unit/api/test_client_llm_proxy.py`：计费公式验证（×5）、余额阻断、并发扣减
- `tests/unit/db/test_client_binding_db.py`：激活码生命周期、绑定 CRUD

**验收标准**：
- 三智能体流程通过（开发→测试→CodeReview）
- 核心计费逻辑 100% 覆盖

**预估**：半天。

---

## Phase 2：客户端 CLI（Day 2-3）

### 2.1 ProxyLLMGateway / ProxyWebSearchTool

**任务**：
- 新建 `clients/association-client-cli/runtime/proxy_gateway.py`（设计文档 §3.3）
- 实现 `ProxyLLMGateway`（接口与 `LLMGateway` 一致）
- 实现 `ProxyWebSearchTool`
- httpx 调用，3 次重试（指数退避）
- `NoCreditError` 异常（402 时抛出）

**验收标准**：
- 能成功调用服务端 `/api/client/v1/llm/chat`
- 402 时抛出 `NoCreditError`
- token usage 和 billing 事件正确透传

**预估**：半天。

### 2.2 CLI 入口

**任务**：
- 新建 `clients/association-client-cli/main.py`（设计文档 §3.2）
- 实现 3 个命令：`activate` / `credits` / `collect`
- 配置文件读写（`%APPDATA%\association-client\cli-config.json`）
- machine_id 采集

**验收标准**：
- `activate` 能完成激活并保存配置
- `credits` 能查询余额
- `collect` 能启动完整流水线

**预估**：半天。

### 2.3 NDJSON 进度输出

**任务**：
- 新建 `clients/association-client-cli/runtime/progress_reporter.py`
- 实现 6 种事件类型的输出函数（start/progress/billing/log/error/complete）
- 手机号脱敏函数（`1xx****xxxx`）
- 与 `AssociationBatchEnricher` 的 `progress_reporter` 回调对接

**验收标准**：
- stdout 输出的是合法 NDJSON（每行一个 JSON）
- 事件字段与设计文档 §9.1 一致
- 手机号在所有输出中脱敏

**预估**：半天。

### 2.4 PowerShell 脚本改造

**任务**：
- 复制 `clients/wechat-souyisou-rpa/scripts/*.ps1` 到 `clients/association-client-cli/scripts/`
- 改造 `llm_judge.py`：gateway 默认改为从环境变量构造 `ProxyLLMGateway`（设计文档 §3.4.1 方案A）
- 改造 `ocr_adapter.py`：HTTP 调用服务端 `/api/client/v1/ocr/parse`（设计文档 §3.4.2）
- 验证 PowerShell 仍能 spawn `python llm_judge.py`（PyInstaller exe 内的 Python）

**关键改动点**：
```python
# llm_judge.py 改造
async def run_judge(payload, gateway=None):
    if gateway is None:
        server_url = os.environ.get("ASSOCIATION_CLIENT_SERVER_URL")
        access_token = os.environ.get("ASSOCIATION_CLIENT_ACCESS_TOKEN")
        if server_url and access_token:
            from runtime.proxy_gateway import ProxyLLMGateway
            gateway = ProxyLLMGateway(server_url, access_token)
        else:
            # 降级：直连 llm_gateway（开发调试用）
            from src.llm.gateway import llm_gateway
            gateway = llm_gateway
    # ... 原逻辑不变 ...
```

**验收标准**：
- judge 调用走服务端代理，服务端能记录计费
- OCR 调用走服务端代理
- 微信 RPA 完整流程（probe→open→search→collect）可运行

**预估**：1 天（含真机调试）。

### 2.5 PyInstaller 打包

**任务**：
- 编写 `clients/association-client-cli/build.spec`（设计文档 §3.7）
- 打包 `association-cli.exe`
- 验证 exe 能独立运行（activate/credits/collect）
- 带入 PowerShell 脚本、Python 依赖

**验收标准**：
- exe 能在干净 Windows 上运行（无需预装 Python）
- 内含的 PowerShell 脚本路径正确
- Playwright 可用（依赖 Phase 0.1 结论）

**预估**：半天。

---

## Phase 3：Electron 客户端（Day 3-4）

### 3.1 工程脚手架

**任务**：
- 新建 `clients/association-client/` 工程
- 参考 `clients/agent-desktop/` 的 electron 主进程、preload、security 模式
- `package.json` / `tsconfig.json` / Vite 配置
- IPC 通道定义（设计文档 §4.5）

**预估**：半天。

### 3.2 激活页（ActivationView.vue）

**任务**：
- 激活码输入框（格式校验 `AC-XXXXXXXXXXXX`）
- 服务端地址输入框（预填默认）
- 激活按钮 → 调用 `cli.activate()` → 保存配置 → 跳转主页
- 错误提示（激活码无效/已使用/已过期）

**预估**：2 小时。

### 3.3 收集主页（CollectView.vue）

**任务**：
- 协会名输入（textarea，逗号/换行分隔）
- 文件上传（CSV/Excel，调 CLI 解析）
- 输出路径选择
- "开始收集"按钮 → 调用 `cli.collect()`
- 进度区域：实时解析 NDJSON 事件，渲染时间线（设计文档 §4.4.2）
- 日志区域：实时流式显示
- "停止"按钮 → `cli.kill()`
- 积分余额卡片（实时更新）
- 余额为 0 时禁用按钮

**预估**：1 天。

### 3.4 结果展示页（ResultView.vue）

**任务**：
- 任务完成后展示汇总（成功/部分/失败计数）
- 下载 Excel 按钮
- 协会结果卡片列表（脱敏）

**预估**：半天。

### 3.5 积分页（CreditsView.vue）

**任务**：
- 余额、今日/本周/总计消耗
- 消耗明细列表（调 `cli credits` 或查本地 SQLite）

**预估**：2 小时。

---

## Phase 4：联调与打包（Day 4-5）

### 4.1 三端联调

**任务**：
- 在开发环境完整跑通：Electron → CLI → 服务端 → LLM
- 验证计费准确性（对比 token 消耗与积分扣减）
- 验证微信 RPA 真机流程（需微信已登录）
- 验证错误处理（余额不足、网络断开、微信未登录）

**验收标准**：
- 单个协会完整收集成功
- 积分正确扣减（×5）
- 进度/日志实时显示
- 结果 Excel 正确生成

**预估**：1 天。

### 4.2 electron-builder 打包

**任务**：
- 编写 `electron-builder.yml`（设计文档 §4.7）
- 将 CLI exe 作为 extraResources 打入
- 生成 NSIS 安装包
- 验证安装后目录结构正确

**验收标准**：
- 安装包大小合理（< 300MB）
- 安装后可直接运行

**预估**：半天。

### 4.3 干净机器验收

**任务**：
- 在未安装开发环境的 Windows 机器上安装客户端
- 输入激活码激活
- 运行完整收集流程
- 验证无缺少依赖、无路径错误

**预估**：半天。

---

## Phase 5：交付准备（Day 5-6）

### 5.1 生成客户激活码 + 租户充值

**任务**：
- 在生产服务端为该客户租户充值积分（平台后台「租户管理」→ 编辑租户 → 充值）
- 在**该租户编辑页面**的「协会客户端激活码」区块点击「+ 生成激活码」，输入客户端名（如"中国黄金协会-张三电脑"）
- 复制生成的激活码（仅显示一次），随安装包交付给客户

### 5.2 编写交付操作手册

**任务**：
- 编写 `docs/tools/association-client-delivery-guide.md`，包含：
  - **系统要求**：Windows 10/11、微信 PC 版（最新版，安装后关闭自动更新）
  - **预装步骤（关键）**：安装 Python 3.11+ → `pip install playwright` → `playwright install chromium`（或运行安装目录下 `install-playwright.cmd`）
  - **关闭微信自动更新**：微信设置 → 关于 → 关闭"自动下载安装包"和"有更新时自动安装"（防止升级到未验证版本）
  - 安装步骤（运行 NSIS 安装包）
  - 激活步骤（输入激活码 + 服务端地址）
  - 使用步骤（含截图）
  - 常见问题排查（Playwright 未安装、微信未登录、积分不足等）
  - 联系方式

### 5.3 最终验收

**任务**：
- 按交付操作手册在模拟客户环境完整跑一遍
- 确认所有功能正常
- 确认积分计费准确

---

## 里程碑检查点

| 检查点 | 时间 | 验收标准 | 阻塞后续 |
|--------|------|----------|----------|
| Phase 0 完成 | Day 1 下午 | PyInstaller 打包+Playwright预装方案可行、Electron spawn 可行、微信RPA 可运行 | ✅ 全部阻塞 |
| Phase 1 完成 | Day 2 | 服务端 API 可用、计费准确、后台激活码管理界面可用 | 阻塞 Phase 2 联调 |
| Phase 2 完成 | Day 3 | CLI 可独立运行、打包成 exe | 阻塞 Phase 3 |
| Phase 3 完成 | Day 4 | Electron GUI 可用 | 阻塞 Phase 4 |
| Phase 4 完成 | Day 5 | 端到端联调通过、安装包生成 | 阻塞 Phase 5 |
| Phase 5 完成 | Day 6 | 干净机器验收通过、激活码已生成、手册已编写 | — |
| **交付** | **Day 6（周日）** | **可交付给客户** | — |

---

## 人员分工建议

| 角色 | 负责范围 |
|------|----------|
| 后端开发 | Phase 1（服务端 API）+ Phase 2.4（PowerShell 改造） |
| 全栈开发 | Phase 2（CLI）+ Phase 3（Electron）+ Phase 4（联调） |
| 测试 | Phase 0.3（微信真机）+ Phase 4.3（干净机器验收） |

> 若只有 1 人开发，建议优先 Phase 0 → Phase 1 → Phase 2 → Phase 3 精简版（只做激活+收集+进度，结果/积分页简化）→ Phase 4。Phase 5 手册可后补。

---

## 风险与应急方案

| 风险 | 概率 | 应急方案 |
|------|------|----------|
| 客户未预装 Playwright Chromium | 中 | 交付手册前置写明预装步骤；客户端首次启动引导运行 `install-playwright.cmd`；CLI 启动 fail-loud 检测 |
| 微信自动更新导致 RPA 失效 | 中 | 交付手册强制要求安装后关闭自动更新；RPA 返回 inconclusive 不崩溃 |
| 交付时间不足 | 中 | Phase 3 简化（只做核心收集流程，积分/结果页延后）；Phase 5 手册简化 |
| PyInstaller 依赖遗漏 | 中 | 干净机器验收时发现，补充 hiddenimports |
| 服务端部署延迟 | 低 | Phase 1 可在开发环境验收，生产部署可并行 |

---

## 增补：可观测性（遥测 + 本地日志 + 诊断包 + 后台查看）— ✅ 已完成（2026-08-10）

客户端可观测性补全。设计见 [association-client-design.md §10](association-client-design.md)。

**已完成工作**：
- **CLI 本地完整日志**：`runtime/run_log.py`（`_emit` tee → `%LOCALAPPDATA%\AidWorkAgent\association-client\logs\app.log`，5MB 滚动）。
- **CLI 遥测上报**：`runtime/telemetry.py`（start/log/error/complete 缓冲→`/api/client/v1/logs`，吞异常；finally 兜底 flush）；`main.py` 注入 session_id + 修 L187 旁路。
- **GUI 导出诊断包**：`electron/main.ts` `client:system:exportDiagnostics`（脱敏 + PowerShell Compress-Archive，零依赖）+ `preload.cts` + 顶栏按钮 + `app.js` guiLog。
- **后台查看**：`ClientUsageLogDB.list/recent_errors` + `/api/saas/client-usage-logs/list|recent-errors` + 前端 `/portal/client-logs`（ClientUsageLogs.vue）+ `client_usage_logs(status,created_at)` 索引。

**验证**：本地真机 6 协会 run——遥测落 agent2 `client_usage_logs`（run_start/run_complete 可见）、`app.log` 62 事件完整、`wechat_diag.log` 含 INCONCLUSIVE 重试、导出包 access_token 已脱敏。未补正式单测（以真机 e2e 验证为准）。

**部署节奏**：后台部分（API+页面+索引）独立先行上线；客户端部分（CLI+GUI）本地 exe 验证通过后提交，需重打 `association-cli.exe`（PyInstaller）+ 安装包。

---

*开发计划结束。设计文档见 [association-client-design.md](association-client-design.md)。*
