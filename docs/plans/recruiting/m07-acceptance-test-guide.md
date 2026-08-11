# M0.7 验收测试指南：云端 Web Agent 调本地 BOSS CLI

> 适用对象：第一次接触本项目的验收人员，按步骤照做即可。
>
> 验收目标（计划 M0.7）：**云端 Web Agent → PostgreSQL invocation → 本机 Runtime → MCP stdio → BOSS CLI → 本机已登录 Chrome** 全链路跑通，且 Codex/WorkBuddy 能独立直连 BOSS MCP。
>
> 关联：[开发计划](plan-recruiting-cli-agent-integration.md) / [设计文档](../../design/recruiting/recruiting-cli-agent-integration-design.md)
>
> ⚠️ 三条铁律（全程适用）：
> 1. **用受控 BOSS 测试账号**（M0.0 已固定的那个），写动作会真实发招呼/标记候选人，有单次测试额度，不要用正式招聘账号。
> 2. **操作执行期间手离开鼠标键盘**，不要遮挡/最小化 BOSS 的 Chrome 窗口，不要锁屏。
> 3. 出问题先看本文「§6 排查表」，不要自行重试写动作（effect=unknown 的动作绝不重试）。

---

## 1. 验收环境总览

| 角色 | 在哪 | 本次用什么 |
|---|---|---|
| 云端服务端 | 测试环境 agent2（`https://agent2.aidingyi.cn`） | 已部署 master 最新代码 |
| 数据库 | 测试库 `aid_work_agent2` | 需手动建 4 张表（§2.1） |
| 本机执行节点 | 你的 Windows 电脑 | Node 22 + 两个客户端 + 日常 Chrome |
| BOSS 账号 | 本机 Chrome 里登录 | **受控测试账号** |

> 下文「服务器侧」命令在服务器上执行（SSH 上去）；「本机」命令在你 Windows 电脑的 PowerShell / cmd 里执行。标注清楚。

---

## 2. 环境准备（一次性，约 20 分钟）

### 2.1 服务器侧：agent2 部署 + 建表

```bash
# 1. 更新测试环境代码（SSH 到服务器）
cd /var/www/agent2 && ./deploy/agent2_update.sh

# 2. 确认更新到了本次提交（应显示 36ea6df 或更新）
git log --oneline -1

# 3. 建 4 张表（本次唯一的 DB 变更；幂等，重复执行无害）
docker exec -i aid-postgres psql -U aid_user2 -d aid_work_agent2 <<'SQL'
CREATE TABLE IF NOT EXISTS local_tool_devices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    name TEXT,
    platform TEXT,
    runtime_version TEXT,
    token_hash TEXT UNIQUE NOT NULL,
    machine_fingerprint_hash TEXT,
    capabilities_json JSONB,
    manifest_digest TEXT,
    selected BOOLEAN DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'active',
    last_seen_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_lt_devices_tenant_user ON local_tool_devices(tenant_id, user_id);

CREATE TABLE IF NOT EXISTS local_tool_pairing_tickets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    code_hash TEXT UNIQUE NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS local_tool_invocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    device_id UUID NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json JSONB NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',
    effect TEXT,
    claim_token_hash TEXT,
    lease_expires_at TIMESTAMP,
    result_json JSONB,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    claimed_at TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_lt_inv_device_state ON local_tool_invocations(device_id, state);
CREATE INDEX IF NOT EXISTS idx_lt_inv_tenant_user ON local_tool_invocations(tenant_id, user_id);

CREATE TABLE IF NOT EXISTS local_tool_events (
    id BIGSERIAL PRIMARY KEY,
    invocation_id UUID NOT NULL,
    tenant_id TEXT NOT NULL,
    seq INT NOT NULL,
    stage TEXT,
    current INT,
    total INT,
    message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (invocation_id, seq)
);
SQL

# 4. 验证建表成功（应返回 4 行）
docker exec -i aid-postgres psql -U aid_user2 -d aid_work_agent2 -c \
  "SELECT tablename FROM pg_tables WHERE tablename LIKE 'local_tool%';"

# 5. 验证新 API 已上线（应返回 JSON，含 "success":false 和未登录类提示即正常——说明路由存在）
curl -s https://agent2.aidingyi.cn/api/local-tools/devices | head -c 200
```

✅ 通过标准：步骤 4 返回 4 行表名；步骤 5 返回 JSON 而非 404 HTML。

### 2.2 本机侧：Windows 执行节点

**前置检查**（都满足才继续）：
- [ ] Windows 10/11，当前是交互登录会话（不是远程桌面最小化状态）
- [ ] 已安装 Node.js 22+（`node -v` 验证）
- [ ] 日常使用的 Chrome（里面已登录**受控 BOSS 测试账号**）

```powershell
# 1. 拉最新代码（本机仓库）
cd C:\repos\aid-work-agent
git pull

# 2. 构建 BOSS CLI
cd clients\boss-resume-assistant
npm install
npm run build

# 3. 构建 Local Tool Runtime
cd ..\agent-tool-runtime
npm install
npm run build

# 4. 冒烟：BOSS CLI 版本信息（应输出 JSON，含 provider_id 和 schema_digest）
node ..\boss-resume-assistant\dist\src\cli\index.js version --json

# 5. 冒烟：Runtime 环境自检（此时未配对，报「配置不存在」等是预期的；
#    重点看 DPAPI、桌面检测、boss CLI 入口三项是否为 OK）
node dist\src\cli.js doctor
```

**启动带调试端口的 Chrome**（每次验收前都要做）：

```powershell
# 1. 先彻底关闭所有 Chrome 窗口（任务栏右键退出，确认托盘也没有）
# 2. 用你日常登录 BOSS 的那个 Chrome 带调试端口启动（路径按你机器实际调整）
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222

# 3. 在打开的 Chrome 里访问 https://www.zhipin.com 确认是登录状态，打开「推荐牛人」页
```

```powershell
# 4. 验证 BOSS CLI 能 attach（应输出逐项检查结果全部 ✅）
cd C:\repos\aid-work-agent\clients\boss-resume-assistant
node dist\src\cli\index.js doctor
```

✅ 通过标准：两个构建都 0 错误；`version --json` 输出 JSON；BOSS CLI `doctor` 全部 ✅（如果提示未登录/未找到 BOSS 页面，回 Chrome 确认登录态和页面）。

---

## 3. 验收用例（核心链路）

> 每个用例包含：操作步骤 → 预期结果 → 通过标准。按顺序执行，前面过了再做后面。
>
> Web 端入口：浏览器打开 `https://agent2.aidingyi.cn`，登录你的测试租户账号。

### T1 Web 配对流程

| 步骤 | 操作 | 预期 |
|---|---|---|
| 1 | 左侧菜单栏**底部点用户名** → 弹出菜单选「本地工具」 | 打开本地工具页，有三步引导说明 |
| 2 | 点「生成配对码」 | 显示 8 位配对码 + 5 分钟倒计时（**只显示这一次，先复制好**） |
| 3 | 本机 PowerShell 执行（把 `AB12CD34` 换成真实配对码）：<br>`cd C:\repos\aid-work-agent\clients\agent-tool-runtime`<br>`node dist\src\cli.js pair --code AB12CD34 --server https://agent2.aidingyi.cn --name 我的测试机` | 输出配对成功、设备 ID |
| 4 | 回 Web 页刷新设备列表 | 看到「我的测试机」，状态列显示（此时还未 start，可能显示离线） |
| 5 | 本机执行 `node dist\src\cli.js start` | 开始心跳循环，终端持续输出心跳日志，**这个窗口保持开着** |
| 6 | 回 Web 页刷新，点该设备的「选定」 | 状态变「在线」+「使用中」badge |

✅ 通过标准：设备列表显示在线 + 使用中；Runtime 终端无报错持续心跳。

**配对失败排查**：配对码 5 分钟过期/一次性——过期就重新生成；`pair` 报 401/400 多半是码错或过期。

### T2 链路一：筛选并打招呼（核心写动作）

前置：Chrome 在「推荐牛人」页，Runtime 在线已选定，手离开鼠标。

| 步骤 | 操作 | 预期 |
|---|---|---|
| 1 | Web 打开招聘操作智能体对话：`https://agent2.aidingyi.cn/t/<你的租户ID>/chat/recruiting-operator`（租户 ID 在浏览器地址栏 /t/ 后面那段） | 进入对话页 |
| 2 | 发送：**「筛选 5 年以上经验、本科以上学历的候选人」** | 页面出现进度提示（正在执行…）；本机 Chrome 可见筛选面板被自动操作；最终回复筛选成功（筛选·N） |
| 3 | 接着发送：**「给前 3 个人打招呼」** | 进度实时更新（⏳ 1/3、2/3、3/3）；本机 Chrome 可见逐个点击打招呼；最终回复成功 3 人 |
| 4 | 在本机 Chrome 人工核对 | 列表前 3 张卡片变成「已打招呼」状态 |

✅ 通过标准：筛选徽章计数正确；恰好 3 人被打招呼（**数量必须精确等于 3**）；Web 端全程有进度提示；从发话到开始本机操作的附加延迟 ≤1~2 秒。

**授权规则抽查**（设计 §14，顺带验证）：
- 发「给大家打招呼」（数量模糊）→ 智能体应**反问你**打几个，而不是直接执行
- 发「给 10 个人打招呼」→ 应被拒绝并告知单次最多 3 人

### T3 链路二：接收简历 + 标记不合适

前置：受控账号的沟通页有至少一个「对方想发送附件简历」的会话（提前用另一个 BOSS 账号给测试号发简历申请，或利用既有会话）。

| 步骤 | 操作 | 预期 |
|---|---|---|
| 1 | 同一对话发送：**「去沟通页，接收 1 份简历」** | 自动跳沟通页 → 找到简历请求 → 点同意 → 预览后关闭；回复成功接收 1 份 |
| 2 | 发送：**「把当前候选人标记为不合适」** | 当前会话右侧面板点「不合适」，弹确认层自动确定；回复已标记 |
| 3 | 本机 Chrome 人工核对 | 该会话候选人显示「不合适」标记 |

✅ 通过标准：简历真实接收（附件可打开）；恰好 1 人被标记。

### T4 链路三：约面试演示（只填不发送）

| 步骤 | 操作 | 预期 |
|---|---|---|
| 1 | 沟通页随便打开一个会话，然后发送：**「给当前候选人填一下约面试，备注写『带身份证和简历』，时间选明天，但不要发送」** | 自动打开约面试表单 → 逐字填备注 → 选明天日期 → **点取消关闭** |
| 2 | 本机人工核对 | 候选人**没有**收到任何面试邀请（表单是取消的） |

✅ 通过标准：全程未发送；回复中明确说明「已取消未发送」。

### T5 进度与断线恢复

| 步骤 | 操作 | 预期 |
|---|---|---|
| 1 | T2 步骤 3 执行中，观察 Web 页面 | 进度条/进度文案实时刷新（不需要手动刷新页面） |
| 2 | 执行一次打招呼时，**中途拔掉网线 10 秒再插回** | Runtime 终端显示断线退避重连；本地动作继续完成；网络恢复后 Web 端拿到最终结果 |

✅ 通过标准：断线后结果不丢失、不重复执行。

### T6 失败场景矩阵（逐个演一遍）

| # | 场景 | 制造方法 | 预期行为 |
|---|---|---|---|
| F1 | Runtime 离线 | 关掉 Runtime 终端窗口后发「打招呼 1 人」 | 回复明确引导：设备离线，请启动本机 Runtime（**不是**报技术错误） |
| F2 | Chrome 未启动调试端口 | 关掉 Chrome 后发指令 | 回复提示 Chrome 不可用/需带调试端口启动（CHROME_UNAVAILABLE） |
| F3 | 未登录 BOSS | Chrome 里退出 BOSS 登录后发指令 | 回复提示未登录（NOT_LOGGED_IN） |
| F4 | 锁屏 | Win+L 锁屏后发「打招呼 1 人」，解锁再看 | 写动作被拒绝执行，回复提示解锁电脑后重试（DESKTOP_NOT_INTERACTIVE） |
| F5 | 付费墙 | 用无开聊权益的职位打招呼 | 停止并说明该职位无开聊权益（PAYWALL），**不自动扣费不自动重试** |
| F6 | 任务冲突 | 一个打招呼执行中，再发一个 | 第二个收到 BUSY 类提示，不抢鼠标 |
| F7 | 页面不对 | Chrome 停在非 BOSS 页面发「打招呼」 | 提示请先打开对应页面（WRONG_PAGE），不乱点 |

✅ 通过标准：每个场景都是**停止 + 中文可懂的原因说明**，没有任何一个场景出现"静默重试"或"乱点页面"。

### T7 解绑

| 步骤 | 操作 | 预期 |
|---|---|---|
| 1 | Web 设备列表点「解绑」并确认 | 设备从列表消失/变已撤销 |
| 2 | Runtime 终端观察 | 心跳收到 401 后停止并提示设备已撤销 |
| 3 | 本机执行 `node dist\src\cli.js unpair` | 本地凭证清除 |

---

## 4. 外部 Host 验收（Codex / WorkBuddy 直连）

> 目的：证明 BOSS CLI 是独立标准 MCP Provider，**不依赖** aid-work-agent 云端。
> 前置：**关掉 Runtime**（`start` 窗口 Ctrl+C），云端在不在无所谓。

### T8 Codex 直连

```powershell
# 1. 注册本地 MCP Provider（路径按实际调整）
codex mcp add boss-recruiting -- node "C:\repos\aid-work-agent\clients\boss-resume-assistant\dist\src\cli\index.js" mcp --stdio

# 2. 确认工具被发现（应列出 7 个 boss_* 工具）
codex mcp list
```

然后在 Codex 里对话验证：
1. 「用 boss-recruiting 跳到沟通页」→ 本机 Chrome 自动跳转（boss_goto，只读导航）
2. 「给 1 个人打招呼」→ 完成 1 个受控写动作

✅ 通过标准：7 个工具被发现；导航成功；1 个写动作成功且数量精确。

### T9 WorkBuddy 直连

在 WorkBuddy 的 `mcp.json` 添加（路径按实际调整）：

```json
{
  "mcpServers": {
    "boss-recruiting": {
      "type": "stdio",
      "command": "node",
      "args": ["C:/repos/aid-work-agent/clients/boss-resume-assistant/dist/src/cli/index.js", "mcp", "--stdio"]
    }
  }
}
```

验证：工具列表出现 7 个 boss_* 工具 → 调用一次 `boss_goto`（导航）→ 再任选一条受控操作。

✅ 通过标准：工具发现 + 至少 1 条调用成功。**记录 WorkBuddy 的版本号**；如有兼容差异，把现象记下来（版本 + 报错），不要现场改。

---

## 5. 验收记录表（打勾归档）

| 用例 | 结果 | 备注 |
|---|---|---|
| §2.1 服务器建表 + API 上线 | ☐ | |
| §2.2 本机构建 + doctor | ☐ | |
| T1 配对 + 在线 + 选定 | ☐ | |
| T2 筛选 + 打招呼 3 人（数量精确） | ☐ | |
| T2 授权抽查（模糊反问 / 超上限拒绝） | ☐ | |
| T3 接收简历 + 标记不合适 | ☐ | |
| T4 约面试演示未发送 | ☐ | |
| T5 进度实时 + 断线恢复 | ☐ | |
| F1~F7 失败场景全部正确停止 | ☐ | |
| T7 解绑 | ☐ | |
| T8 Codex 直连 | ☐ | 记录版本： |
| T9 WorkBuddy 直连 | ☐ | 记录版本： |

全部通过 = MVP 验收完成，可以把 `docs/ideas.md` 对应条目标记 ✅ 已完成开发。

---

## 6. 排查表

| 现象 | 可能原因 | 处理 |
|---|---|---|
| Web「本地工具」页报错 | 服务器没建 4 张表 | 回 §2.1 步骤 3 |
| 配对码报错 | 超过 5 分钟 / 已用过 | 重新生成（一码一次） |
| 设备一直「离线」 | Runtime 没 start / server 地址不对 | 检查 `start` 窗口日志；`status` 看本地配置 |
| 智能体说设备不可用 | 没点「选定」/ 心跳中断 | 设备列表确认「使用中」+「在线」 |
| 智能体回复但没有本机动作 | Runtime 窗口有报错 | 看 Runtime 终端输出（日志不含敏感信息，可整段保存反馈） |
| 动作做到一半停了 | 页面结构变化 / 风控弹层 | 属于 fail-loud 设计，看回复里的原因；**不要重复发同一指令**，先人工看页面 |
| 回复「实际效果未知」 | 写动作发出但校验失败 | 人工去 BOSS 页面核对真实状态，**禁止让智能体重试** |
| Chrome 完全没被操作 | 调试端口没起 | 关干净 Chrome 重新带 `--remote-debugging-port=9222` 启动 |

**收集反馈时带上**：Runtime 终端最后 20 行、Web 端智能体完整回复、大概时间点。服务端日志在服务器 `docker logs aid-agent-api2`（可选，给开发用）。
