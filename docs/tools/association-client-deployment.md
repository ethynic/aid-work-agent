# 协会客户端（协会信息收集助手）部署手册

> 适用版本：`AssociationClient-1.0.0-win-x64.exe`
> 关联设计文档：[association-client-design.md](../../docs/tools/association-client-design.md)
> 目标读者：负责给客户上门部署的运营/实施人员。
> 生产服务端地址：`https://agent.aidingyi.cn`（注意：`agent2.aidingyi.cn` 是测试环境，**不要给客户用**）

---

## 0. ⚠️ 上线前必读（两个已知坑，必须先处理）

周一上门前，请务必确认下面两件事，否则客户当场会卡住：

### 坑 1：GUI 激活页的「服务端地址」默认值是测试环境（已修复，但旧 exe 仍带坑）

客户端激活界面有一个「服务端地址」输入框。**源码默认值原先是** `https://agent2.aidingyi.cn`（测试环境），生产应是 `https://agent.aidingyi.cn`（没有 `2`）。

- 代码位置：`clients/association-client/src/index.html:32`
- **已于 2026-08-09 修正**：`index.html:32` 默认值已改为 `https://agent.aidingyi.cn`（生产）。用修正后的代码**重新打包**出的 exe，客户看到的就是正确地址，无需手改。

⚠️ **关键**：修正前打的旧 exe（例如 2026-08-09 08:14 那版）仍带测试地址。客户用生产激活码在这种旧 exe 上激活会直接失败（测试服务器找不到该激活码 → 404）。**交付前必须用修正后的代码重新打包**（见第 0.3 节），或在现场盯着客户把输入框里的 `agent2` 改成 `agent`。

> CLI 本身的默认值一直是对的（`clients/association-client-cli/runtime/config.py:78` 默认 `https://agent.aidingyi.cn`），问题只在 GUI 这一层。

### 坑 1.5：重新打包的正确命令（只改了 GUI，不用重打 CLI）

本次只改了 Electron 渲染层 `index.html`，CLI exe（`association-cli.exe`）没动，所以**不用跑 PyInstaller**，只重打 GUI 安装包即可（几分钟）：

```bash
cd clients/association-client
npm run build              # tsc + copy-renderer.mjs：把改过的 src/ 重新生成 dist/
node scripts/package-win.mjs   # electron-builder 重新出 release/AssociationClient-1.0.0-win-x64.exe
```

或直接 `npm run package:win`（等价于 typecheck + build + package-win.mjs）。打完确认 `release/AssociationClient-1.0.0-win-x64.exe` 的修改时间已更新即可。

### 坑 2：后台没有「生成激活码」的网页界面

目前**激活码只能通过后台 API 生成，管理后台网页里没有这个入口**（设计文档里规划过 `ClientActivationManager.vue`，但还没开发）。所以运营必须在上门**之前**，用 `curl` / Postman 调接口把激活码先生成出来，带到现场。详见第 5 节。

---

## 1. 产品架构（先搞清楚客户装的是什么）

这是一个**两层结构**的桌面应用：

```
AssociationClient-1.0.0-win-x64.exe（NSIS 安装包，约 218MB）
  ├─ Electron GUI「协会信息收集助手」（用户双击的入口）
  └─ resources/cli/association-cli.exe（PyInstaller 打的命令行，约 120MB，业务逻辑全在这）
       └─ Python 解释器已打包进 exe 内，客户机器无需装 Python
```

- 客户**只双击「协会信息收集助手」快捷方式**，不碰命令行。
- GUI 启动后，内部 spawn `association-cli.exe` 跑采集，解析它的进度日志显示在界面上。
- 所有「重活」（文心问答、官网抓取、手机号 RPA 取证、OCR、LLM 证据判断）都在 CLI 内完成，**算力走服务端**，客户机器只是个执行终端。

安装包是标准 NSIS 安装程序：非一键安装、按用户安装（不要管理员）、可自选安装目录、自动创建桌面 + 开始菜单快捷方式。

---

## 2. 客户端依赖环境

### 2.1 硬性依赖（系统层，安装包不含）

| 依赖 | 是否自带 | 说明 |
|------|---------|------|
| **Windows 10 / 11 x64** | — | 安装包只出 x64；不支持 32 位 / ARM |
| **Windows PowerShell 5.1** | Win10/11 自带 | 微信 RPA 和机器指纹采集都调 `powershell.exe`（不是 PowerShell 7 / `pwsh`），系统自带即可 |
| **.NET Framework 4.x** | Win10/11 自带 | 微信 RPA 用 UIAutomation + WPF 程序集，依赖 .NET Framework，系统自带 |
| **Visual C++ 运行库（VC++ Redistributable）** | 需确认 | Electron/Chromium DLL 的常规依赖，多数 Win 机器已具备；若启动报缺 DLL，补装 VC++ 2015-2022 x64 |
| **网络能访问 `https://agent.aidingyi.cn`** | — | 激活、LLM 代理、OCR 代理都要联网到生产服务端；客户内网若有白名单需放行 |

### 2.2 业务依赖（采集功能要用的外部软件，安装包不含）

| 依赖 | 用途 | 关键要求 |
|------|------|---------|
| **个人微信 PC 版 4.0+** | 搜一搜 RPA 取证手机号 | 必须是**个人微信**（`...\Tencent\Weixin\Weixin.exe`），**不是企业微信**；旧版 `Tencent\WeChat\WeChat.exe` 路径会被拒绝；**必须已登录**，停在聊天列表页 |
| **Google Chrome**（**推荐，无需 Python**）<br>**或** Playwright Chromium | 文心批量问答 + 协会官网抓取，都复用同一个常驻 Chrome（CDP 端口 9222） | 见 2.3，二选一 |

### 2.3 浏览器二选一（重要，决定要不要装 Python）

CLI 启动采集时会启动一个开了调试端口的 Chrome（端口 9222），文心问答和官网抓取都连这个浏览器。**这个 Chrome 来自两个来源之一**（代码：`runtime/wenxin_browser.py:_chrome_candidates`）：

- **方案 A（推荐，零 Python）—— 装 Google Chrome**：系统装了 Chrome 即可，CLI 优先用系统 Chrome。**客户机器不需要装 Python。**
- **方案 B（备选，需要 Python 3.11+）—— 运行 `install-playwright.cmd`**：脚本会 `pip install playwright` + `playwright install chromium`（下载约 150MB）。**这条路线必须先装 Python 3.11+**，因为 `pip`/`playwright` 命令依赖系统 Python。

> 结论：**为了不折腾，让客户机器装一个 Google Chrome 即可**，别走 Playwright 路线。Playwright 的浏览器只是「系统没装 Chrome」时的兜底。
>
> 注意：`runtime/playwright_check.py` 里的 `ensure_playwright_chromium()` 是**死代码，采集流程并不会调用它**；实际调用的是 `ensure_wenxin_browser()`，后者同时接受系统 Chrome 和 Playwright Chromium。所以装 Chrome 完全够用。

### 2.4 不需要的依赖（别给客户多装）

| 不需要 | 原因 |
|--------|------|
| Python 3.11+ | CLI exe 内已打包解释器；只有走 Playwright 方案装浏览器时才需要系统 Python |
| Tesseract / traineddata | 协会客户端不用本地 OCR；OCR 走服务端 PaddleOCR 代理（`/api/client/v1/ocr/parse`）。`boss-resume-assistant` 里的 `chi_sim.traineddata` 是**另一个产品**的，与本客户端无关 |
| 本地大模型 API Key | 文心/DeepSeek 调用都走服务端代理，用租户积分计费，客户机器无需任何 key |

---

## 3. 客户端安装与配置（客户机器上的操作步骤）

> 建议顺序：先装系统依赖 → 再装微信/Chrome 并登录 → 最后装客户端并激活。

### 步骤 1：安装主程序

1. 双击 `AssociationClient-1.0.0-win-x64.exe`。
2. 按提示选安装目录（默认即可）、下一步直到完成。
3. 桌面 / 开始菜单会出现「协会信息收集助手」快捷方式。

### 步骤 2：安装并登录外部软件

1. **安装 Google Chrome**（推荐）。从官网下载安装即可，不用登录 Google 账号。
2. **安装个人微信 PC 版 4.0+**，用客户的微信扫码登录，**停在聊天列表页**（不要停在某个聊天会话里，也不要停在搜一搜页）。

> 若客户机器确实没有 Chrome 且无法联网下载 Chrome，才走方案 B：装 Python 3.11+ → 在安装目录找 `install-playwright.cmd` 双击运行（下载约 150MB Chromium）。正常情况用方案 A。

### 步骤 3：启动客户端并激活

1. 双击「协会信息收集助手」。
2. 首次启动进入激活页，填三项：
   - **激活码**：填运营预先生成的 `AC-XXXXXXXXXXXX`（见第 5 节）。
   - **服务端地址**：⚠️ **务必把默认的 `https://agent2.aidingyi.cn` 改成 `https://agent.aidingyi.cn`**（删掉 `2`）。这是第 0 节强调的坑 1。
   - **客户端名称（可选）**：建议填一个能区分的，如「中国黄金协会-前台电脑」，方便后台识别。
3. 点「激活」。成功后会显示积分余额，并进入采集页。

> 激活成功后，凭证会写在本机两个位置（见第 8 节）。换电脑需要重新激活（见 FAQ）。

### 步骤 4：开始采集（使用注意）

1. 在输入框填协会名称（每行一个，或上传 CSV/Excel）。
2. 点「开始收集」。采集会自动跑完「文心问答 → 官网抓取 → 微信搜一搜取证 → LLM 证据判断 → 导出 Excel」全流程。
3. **采集期间必须做到**（这是硬性要求，违反会中断）：
   - **全程不要动鼠标和键盘**，把机器当作「正在干活，勿扰」。微信搜一搜 RPA 要求严格的前台焦点，任何其他窗口抢焦点都会触发 `FOREGROUND_LOST` 中断整批。
   - **微信窗口保持前台、不要最小化**。
   - **不要锁屏、不要断开远程桌面**（RDP 断开会丢前台焦点）。
   - 显示缩放 150% / 双屏都支持；不要设低于 100% 或极端缩放。
4. 完成后下载生成的 `.xlsx`。

---

## 4. 后台为租户设置激活码（运营操作，上门前完成）

> 全程需要**平台管理员（platform_admin）**账号。激活码生成**没有网页界面**，必须调 API。

### 4.1 整体流程

```
登录平台管理员（拿 token）
   → 确认/创建租户（拿到 tenant_id）
   → 给租户充值积分（credit_balance 必须 > 0，否则采集会报 NO_CREDIT）
   → 生成激活码（拿到 AC-XXXX，仅此一次重点保存）
   → 把激活码带给客户上门
```

### 4.2 取得管理员 token

所有后台管理接口都用 `Authorization: Bearer <token>` 鉴权（代码：`src/saas/api/tenant_auth.py:get_current_admin`）。最稳的拿 token 方式：用平台管理员账号登录管理后台网页，F12 打开 DevTools → Network → 随便点一个管理请求 → 复制请求头里的 `Authorization: Bearer xxx`。下面 curl 里统一用 `$TOKEN` 表示。

> 也可以调 `POST /api/saas/auth/password_login` 登录拿 token，但响应字段以实际返回为准，建议直接从网页 DevTools 复制，避免字段名歧义。

### 4.3 一步到位的 curl 序列

把下面变量改成你的值，依次执行（Windows PowerShell 里 `$TOKEN` 等变量先 `$TOKEN = "xxx"` 赋值；或用 Postman）。

```bash
# ---- 变量 ----
TOKEN="<平台管理员的 Bearer token>"
SERVER="https://agent.aidingyi.cn"
TENANT_ID="<目标租户的 tenant_id>"   # 若还没有，先走 4.4 创建

# ① 确认租户存在 + 看 tenant_id
curl -s "$SERVER/api/saas/tenants/list_tenants" \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool

# ② 给租户充值积分（platform_admin only；amount_yuan 元，credits 积分）
#    客户端 LLM 调用按倍率消耗租户积分（倍率见 src/db/client_binding_db.py，默认 5x，
#    上线前请以代码实际值为准）。建议先充一笔够用的。
curl -s -X POST "$SERVER/api/saas/billing/recharges/" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"tenant_id\":\"$TENANT_ID\",\"amount_yuan\":500,\"credits\":5000,\"remark\":\"协会客户端上线预充值\"}"

# ③ 生成激活码（明文 code 仅在本次返回，务必保存）
curl -s -X POST "$SERVER/api/saas/client-activations" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"tenant_id\":\"$TENANT_ID\",\"client_name\":\"客户-前台电脑\",\"max_uses\":1}"
#    返回示例：{"id":12,"code":"AC-AB3K9X7QMN2P","tenant_id":"...","status":"unused","max_uses":1,...}
```

> 接口定义：创建租户 `POST /api/saas/tenants/`、充值 `POST /api/saas/billing/recharges/`（`src/saas/api/billing_recharges.py`）、生成激活码 `POST /api/saas/client-activations`（`src/saas/api/client_activation_mgmt.py:47`）。

### 4.4 如果还没有租户

租户可以在管理后台「租户管理」网页里建（有界面），也可以调 API：

```bash
curl -s -X POST "$SERVER/api/saas/tenants/" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"company_name\":\"中国黄金协会\"}"
```

返回里的 `tenant_id` 就是后续充值、生成激活码要用的值。

### 4.5 激活码字段说明

| 字段 | 说明 |
|------|------|
| `code` | 形如 `AC-XXXXXXXXXXXX`（去混淆字母表，无 0/O/1/I/l）。**创建时明文返回，请立刻记下来** |
| `tenant_id` | 绑定的租户，**创建后不可改**；一个租户可生成多个激活码 |
| `max_uses` | 可激活次数，默认 `1`。默认值下激活一次就失效 |
| `expires_at` | 失效时间（ISO 8601），不传 = 永不过期 |
| `status` | `unused`（未用）→ `used`（已用）→ 可被 `disabled` |

**激活码丢了能找回吗？** 能。当前 MVP 明文存储，调 `GET /api/saas/client-activations/list?tenant_id=xxx` 会返回 `code` 字段（`client_activation_mgmt.py:103`）。所以万一忘了，登录后台查列表即可——但也因此，激活码要当敏感信息保管。

---

## 5. 服务端地址绑定（`https://agent.aidingyi.cn`）—— 解答疑问

**问：绑定到 `https://agent.aidingyi.cn` 是不是首次安装时配置？**

**答：不是「安装时」，而是「首次激活时」在 GUI 里填一次，之后自动记住。** 具体机制：

1. 客户首次启动客户端 → 激活页有三个输入框，其中「服务端地址」就是在这里填 `https://agent.aidingyi.cn`。
2. 点激活后，地址会**保存到本机配置文件**（`%APPDATA%\association-client\cli-config.json` 的 `server_url` 字段，以及 Electron 的 `client-config.json`），之后每次启动自动读取，客户不用再填。
3. **安装包本身不固化服务端地址，也没有安装期的配置步骤**——地址完全是运行时在激活页填入的。

⚠️ **最大的坑**：激活页输入框的默认值是测试地址 `https://agent2.aidingyi.cn`（见第 0 节坑 1）。**客户必须手动把 `2` 删掉**，或我们在打包前改掉这个默认值。上门时务必当面确认这一步。

**已激活后想改服务端地址怎么办？** 客户端目前没有「切换服务器」的界面按钮。两个办法：
- 简单：删掉配置文件后重新走激活流程（见第 7 节路径，删 `%APPDATA%\association-client\cli-config.json` 和 Electron 的 `client-config.json`，重启客户端会回到激活页）。
- 注意：改了服务器就要用**对应服务器生成的激活码**重新激活（生产码不能在测试服务器激活，反之亦然）。

---

## 6. 日常运维（补充）

| 场景 | 操作 |
|------|------|
| 查某租户的积分余额 | 后台「充值/计费」页，或 `GET /api/saas/billing/balance`（带 `X-Tenant-Id`） |
| 看激活码列表 / 找回 code | `GET /api/saas/client-activations/list?tenant_id=xxx` |
| 看哪些客户端已激活（绑定） | `GET /api/saas/client-bindings/list` |
| 把某台客户端踢下线 | `POST /api/saas/client-bindings/{binding_id}/disable` |
| 轮换（泄露的）access token | `POST /api/saas/client-bindings/{binding_id}/rotate-token`（新 token 仅返回一次） |
| 吊销某激活码关联的所有绑定 | `POST /api/saas/client-activations/{code_id}/revoke` |
| 禁用一个还没用的激活码 | `DELETE /api/saas/client-activations/{code_id}` |
| 客户要换电脑 | 见 FAQ「换电脑」 |

> 计费提醒：客户端每次 LLM 调用都消耗**绑定的那个租户**的积分（按倍率，默认 5x，以 `src/db/client_binding_db.py` 实际为准）。租户积分耗尽后采集会报 `NO_CREDIT` 中断。所以**上门前务必确认租户已充值且余额充足**，并在长期使用中关注余额。

---

## 7. 客户机器上的数据与文件位置（排障用）

| 路径 | 内容 | 用途 |
|------|------|------|
| `%APPDATA%\association-client\cli-config.json` | 激活凭证（binding_id / access_token / tenant_id / server_url / machine_id） | CLI 凭证，明文 JSON。删掉=注销 |
| `%APPDATA%\协会信息收集助手\client-config.json` | GUI 侧凭证副本，access_token 经 Windows DPAPI 加密 | Electron 凭证 |
| `%APPDATA%\association-client\wenxin-chrome-profile\` | 文心/官网用的独立 Chrome 用户目录 | 与客户日常 Chrome 隔离 |
| `%LOCALAPPDATA%\AidWorkAgent\wechat-souyisou-rpa\artifacts\` | 微信 RPA 取证结果（DPAPI 加密的 `.dpapi`） | 手机号证据留存 |
| `%TEMP%\wechat_diag.log` | 微信 RPA 诊断日志（已脱敏） | 排查搜一搜问题先看这里 |
| 安装目录 `resources\cli\association-cli.exe` | 打包好的 CLI | 可单独命令行调用排障 |

机器指纹 `machine_id` = `sha256(主板序列号|CPU ID|磁盘序列号)[:32]`（Windows 用 PowerShell WMI 采集）。**当前 MVP 只采集不强制校验**，换硬件不会导致不可用；token 理论上可跨机器（如需硬件强绑定是 Phase 2 工作）。

---

## 8. 客户端出问题：排查与日志收集

> 现状：客户端**已自动上报遥测**（`start`/`log`/`error`/`complete` → 服务端 `/api/client/v1/logs`），并在本机写完整日志、GUI 一键导出诊断包。排查顺序：**先看后台遥测（不打扰客户）→ 不够再让客户导诊断包**。

### 8.1 排查顺序

1. **后台遥测（首选，不打扰客户）**：平台管理后台「客户端运行日志」(`/portal/client-logs`)，按租户/状态/阶段/时间筛，或点「🔍 近24h错误」。能看到每个 run 的 `run_start`/`run_complete`（含成功/失败计数、消耗积分）+ ERROR/WARNING 事件。
2. **客户本机完整日志**：`%LOCALAPPDATA%\AidWorkAgent\association-client\logs\app.log`（每次 run 的全事件，含文心/官网/微信每一步，跨次保留、5MB 滚动）。
3. **微信 RPA 诊断**：`%TEMP%\wechat_diag.log`（搜一搜 INCONCLUSIVE/重试/judge 异常；跨所有 run 追加，按协会名+时间筛）。
4. **导出诊断包**：客户端顶栏「📦 诊断包」一键打包（见 8.3）。

### 8.2 各日志覆盖范围

| 来源 | 位置 | 覆盖范围 | 持久 |
|------|------|---------|------|
| **本地完整日志** | `%LOCALAPPDATA%\AidWorkAgent\association-client\logs\app.log` | 全部事件：文心/官网/微信每步 progress + start/complete + log/error | ✅ 持久，5MB 滚动 |
| **服务端遥测** | 后台 `/portal/client-logs`（表 `client_usage_logs`） | start / log / error / complete（**不上报 progress**） | ✅ 服务端持久 |
| **微信诊断日志** | `%TEMP%\wechat_diag.log` | 搜一搜 INCONCLUSIVE/重试/list_judge/judge 异常 | ✅ 追加，跨 run 共用 |
| GUI 日志弹窗 | 「📋 查看日志」按钮 | 同 app.log 的事件流（内存） | ❌ 内存，关软件丢 |
| 微信取证产物 | `%LOCALAPPDATA%\AidWorkAgent\wechat-souyisou-rpa\artifacts\*.dpapi` | 每条取证结构化结果 | DPAPI 加密，远程无法解密 |

> ⚠️ **可观测性边界**：服务端遥测是 **run 级**（哪批、几个成功/失败、消耗多少）+ 错误事件；**per-协会 per-步骤的细节**（如「中国XX协会会长手机号未找到」、微信 INCONCLUSIVE 重试）只在**本地 app.log / wechat_diag.log** 里，不上报服务端（progress 不上报，控量）。所以运营在后台能发现「某客户这批 5/6 partial」，但要查「为什么 partial」仍需客户的诊断包。

### 8.3 导出诊断包（一键，发给客服）

客户端顶栏「📦 诊断包」按钮 → 选保存位置 → 生成 zip，内含：
- `app.log`（完整运行日志）、`wechat_diag.log`（微信诊断）、`gui-log.txt`（GUI 内存日志）
- `cli-config.json` / `client-config.json`（**access_token 已脱敏成 `***`**）
- `artifacts/`（微信取证产物，单文件≤2MB、总≤10MB 裁剪）、`system_info.txt`（系统/版本信息）

打完自动打开所在文件夹，客户把 zip 发给客服即可，无需手动找文件。

### 8.4 运营侧自检（不依赖客户）

- **客户端运行日志**：后台 `/portal/client-logs`（API `GET /api/saas/client-usage-logs/list`、`/recent-errors`，platform_admin 鉴权）。
- **激活/绑定状态**：`GET /api/saas/client-bindings/list`。
- **积分消耗**：`GET /api/saas/billing/balance`（带 `X-Tenant-Id`）。
- **服务端 LLM 代理 trace**：客户端 LLM 请求走 `/api/client/v1/llm/chat`，凭 binding_id + 时间在日志库 `obs_spans` 查。

---

## 9. 常见问题 FAQ

**Q：采集跑到一半停了，日志里有 `FOREGROUND_LOST`？**
A：微信搜一搜 RPA 要求严格前台焦点。采集期间不要动鼠标键盘、不要切窗口、不要锁屏、不要断 RDP。微信窗口要保持前台、不能最小化。重新开始这批即可。

**Q：文心偶尔报验证码（captcha）？**
A：属于正常风控。客户端会自动降级到 DeepSeek 兜底，不影响整体流程；复用同一个常驻 Chrome 会话能显著降低验证码频率。

**Q：客户要换电脑怎么办？**
A：激活码默认 `max_uses=1`，激活一次就失效。换电脑需要**后台为同一租户重新生成一个激活码**（第 4.3 步 ③），在新电脑上重新激活。旧机器若要停用，可调 `POST /api/saas/client-bindings/{binding_id}/disable` 踢下线。

**Q：客户激活时报「积分不足 / NO_CREDIT」？**
A：租户没充值或已耗尽。后台给该租户充值（第 4.3 步 ②）后重试。客户端的 LLM 调用是按倍率扣租户积分的。

**Q：搜索某些协会的详情页结果为 `inconclusive`？**
A：当前版本 OCR（用于微信结果里图片/PDF 详情页）**已临时屏蔽**（`wechat-souyisou.ps1` 中 `$ocrEnabled = $false`，因服务端 PaddleOCR 云 API 未配置）。这类图片类详情页暂时给不出手机号，属于已知限制。

**Q：激活页连不上服务器？**
A：① 确认服务端地址填的是 `https://agent.aidingyi.cn`（不是 agent2）；② 客户内网是否需要放行该域名；③ 服务端是否正常运行（`/api/client/v1/*` 端点是否可达）。

---

## 10. 上门交付清单（给客户带的东西）

- [ ] `AssociationClient-1.0.0-win-x64.exe` 安装包（U 盘 / 网盘）
- [ ] **已为该客户租户生成好的激活码** `AC-XXXXXXXXXXXX`（上门前在后台生成，记下来）
- [ ] 确认该租户**已充值且积分充足**
- [ ] Google Chrome 安装包（备用，客户机器没装 Chrome 时用）
- [ ] 微信 PC 版安装包（备用）
- [ ] 本手册（重点看第 0 节两个坑 + 第 3.4 节使用注意）

---

## 附：相关代码索引

| 模块 | 文件 |
|------|------|
| 打包脚本 | `clients/association-client/scripts/package-win.mjs`、`clients/association-client/electron-builder.yml` |
| CLI 入口 | `clients/association-client-cli/main.py` |
| 服务端地址解析 | `clients/association-client-cli/runtime/config.py:71` |
| GUI 激活页默认地址（坑 1） | `clients/association-client/src/index.html:32` |
| 浏览器探测（系统 Chrome / Playwright） | `clients/association-client-cli/runtime/wenxin_browser.py:28` |
| 客户端激活接口 | `src/api/client_routes.py:87` |
| 激活码生成 / 管理 API | `src/saas/api/client_activation_mgmt.py` |
| 充值 API | `src/saas/api/billing_recharges.py` |
| 积分计费倍率 | `src/db/client_binding_db.py`（`_client_credit_multiplier`） |
| 客户端 token 校验 | `src/api/client_auth.py:31` |
| 微信诊断日志写入 | `clients/association-client-cli/scripts/wechat-souyisou.ps1`（多处 `Add-Content`）+ `main.py:339` |
| GUI 日志弹窗（内存） | `clients/association-client/src/index.html:88`（查看日志按钮）、`src/app.js:348`（appendLog） |
| 日志上报接口（客户端未接通） | `src/api/client_routes.py:301` |
| 设计文档 | `docs/tools/association-client-design.md` |
