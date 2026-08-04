# 浏览器 RPA（纯键鼠路线）深度调查报告

> 调研日期：2026-07-30
> 状态：调研完成
> 目的：①沉淀「针对特殊防爬网页的纯键鼠 RPA 操作逻辑」通用方法论；②评估将 Boss 直聘客户端从「原生 CDP（不启用 Runtime 域）+ 截图 OCR」路线，迁移/补充为「纯键鼠 + 剪贴板/OCR」RPA 路线的可行性与取舍。
> 关联：
> - [BOSS 直聘智能招聘 Agent 可行性调研](boss-recruiting-agent-research.md)
> - [BOSS 简历筛选助手原生 CDP 设计](../design/recruiting/boss-resume-assistant-native-cdp-design.md)
> - [微信桌面版 RPA 自动化技术方案调研](wechat-desktop-rpa-technical-research.md)
> - [微信搜一搜 RPA 设计](../tools/wechat-souyisou-rpa-design.md)

---

## 0. 结论先行

1. **纯键鼠 RPA 在反爬的「浏览器层」（环境 + 事件）确实优于 CDP/stealth**：系统级真实输入（`SendInput`/`keybd_event`）在**网页 JS 层面**呈现为 `Event.isTrusted === true`，网页 JS 无法将其与真人操作区分；且不携带 `navigator.webdriver`、CDP `Runtime.enable` 等自动化指纹。微信搜一搜 RPA 的成功已在本项目真机验证了这条路线可跑通。**但它不解决网络层（TLS/IP）和行为层（轨迹/频控），见 §2.4。**

2. **「Ctrl+A → Ctrl+C 复制详情正文」对 Boss 的可行性，目前只有理论推断、未经真机验证，强烈建议先做 5 分钟实验**（见 §3.3 的验证方案）。已知事实是：详情核心正文是 Canvas 渲染（DOMSnapshot 实测 `DIV → CANVAS`、27 节点、无正文文本节点），理论上剪贴板复制不到 Canvas 位图里的字。**但详情页还存在非 Canvas 的 DOM 区域**（DOMSnapshot 抓到"页面标题等 6 项"nodeValues，意味着顶部基础信息、右侧经历概览、按钮文案可能是普通 DOM）——这些区域 `Ctrl+C` 可能复制得到，对"初筛够不够用"需要实测确认。不要用理论判断代替这个低成本实验。

3. **两条路线的本质权衡**：现有原生 CDP 方案的"复杂"源于它要规避一个脆弱的经验观察——「Boss 详情刷新的最小触发条件恰好是 `Runtime.enable`」。这个约束一旦被平台更新打破（刷新触发条件变了），整个自研 CDP 链路就崩。纯键鼠 RPA 的战略价值在于**不依赖这个脆弱约束、且与微信 RPA 统一技术栈**；代价是**定位鲁棒性下降、需窗口前台、占用键鼠**。完整决策矩阵见 §6.1。

4. **推荐：先验证复制可行性（§3.3），再在 §6.1 决策矩阵中据实选择**。无论选哪条路，都应先把微信搜一搜 RPA 已沉淀的「六件套」（§4）抽象为通用底座。

---

## 1. 两条技术路线的本质区别

### 1.1 DOM/CDP 嗅探路线（Playwright / Puppeteer / Selenium / 原生 CDP）

| 子形态 | 原理 | 指纹特征 |
|--------|------|----------|
| Selenium / chromedriver | WebDriver 协议驱动 | `navigator.webdriver=true`、`cdc_` 变量、UA 含 `HeadlessChrome` |
| Playwright / Puppeteer | CDP + 自有连接协议 | 创建 Page 时**隐式启用 `Runtime` 域**，触发 execution context 事件 |
| 原生 CDP（本项目 Boss 现状） | 裸 WebSocket + 手选 method | **可选**是否启用 Runtime，本项目刻意不启用 |
| 浏览器扩展（`chrome.debugger`） | 扩展内发 CDP 命令 | 仍走 CDP，`debugger` 权限可见 |

**共同点**：都通过 CDP/WebDriver 与浏览器**进程内**通信。自动化痕迹大部分发生在浏览器进程内部，网页 JS（或风控 SDK）可从多层面探测。

### 1.2 纯键鼠 RPA 路线（系统级真实输入）

| 工具/层次 | 输入注入方式 | 浏览器可检测性 |
|------------|--------------|----------------|
| `pyautogui` / `SendKeys` | 应用层模拟，部分软件可拦截 | 边缘 |
| `pywinauto` / `keybd_event` | Win32 API，OS 输入队列 | **网页 JS 层不可区分**（`isTrusted=true`） |
| `SendInput` / AutoHotkey / pynput | OS 输入子系统 | **网页 JS 层不可区分** |
| **影刀「驱动级点击」** | 底层键鼠驱动，直接写 Windows 输入子系统 | **网页 JS 层不可区分**，且可作用于后台/被遮挡窗口 |

**关键事实**：`Event.isTrusted` 是浏览器区分真实/合成事件的**唯一标准机制**，且只读。JS 合成事件（`dispatchEvent`）→ `isTrusted=false`；而 OS 级注入（`SendInput`/`keybd_event`）走与真实硬件相同的输入管线 → `isTrusted=true`。Chromium 官方确认"操作系统不会以浏览器**JS** 可检测的方式标记合成输入事件"。**这是纯键鼠路线反爬优势的物理基础。**

> ⚠️ 边界限定："`isTrusted` 不可区分"针对的是**网页 JS / 浏览器进程内部**。理论上**内核级反作弊**（游戏反外挂那种 ring-0 检测）可探测 `SendInput`，但 Boss 这类普通商业网页风控不在此层级，本报告后续"`不可区分`"均指网页 JS 层。

**定位方式与点击方式正交**（来自影刀/火山引擎文档，值得沉淀）：
- **模拟点击（前台）**：`mouse_event`/`SendInput` 驱动真实光标，需窗口前台、占用鼠标。
- **驱动级点击（系统级）**：底层驱动直接写输入子系统，可后台/被遮挡操作，最隐蔽。
- **JS 点击**：CDP 在 DOM 层 `.click()`，不经鼠标，**易触发反爬**。
- 定位可以是「DOM/XPath（需 CDP）」「图像识别」「坐标」之一。**纯键鼠路线 = 图像/坐标定位 + 模拟/驱动级点击**。

---

## 2. 反爬风控识别原理

### 2.1 风控的多层一致性校验

现代风控不依赖单一信号，而是**网络层 × 浏览器层 × 行为层**的一致性校验，任一层"穿帮"即触发：

| 层 | 检测维度 | CDP/stealth 能否对抗 | 纯键鼠能否对抗 |
|----|----------|---------------------|----------------|
| **网络层** | TLS/JA3(JA4) 指纹、HTTP/2 指纹、IP 信誉、请求频次 | ❌ JS 补丁完全不触及 | ❌（与输入方式无关，需代理/住宅 IP） |
| **浏览器层（环境）** | `navigator.webdriver`、CDP `Runtime.enable`、Canvas/WebGL/Audio 指纹、`plugins`/`languages` | ⚠️ stealth 补丁只能改 JS 表层，且补丁自身引入副作用 | ✅ 不注入任何 JS，环境零污染 |
| **浏览器层（事件）** | `Event.isTrusted` | ❌ JS 合成事件 `isTrusted=false` | ✅ OS 注入 `isTrusted=true` |
| **行为层** | 鼠标轨迹（Fitts 定律/贝塞尔/抖动）、按键 dwell/flight time、滚动模式 | ❌ "浏览器伪装无法让瞬移点击看起来正常" | ⚠️ 事件可信但轨迹/节奏仍需拟人 |

### 2.2 stealth 补丁为什么必然失效（重要沉淀）

`puppeteer-extra-plugin-stealth` 这类纯 JS 方案覆盖了 `navigator.webdriver`、`chrome.runtime`、`plugins`、WebGL 等，但**仍被检测**，根因是**分层不匹配**：

1. **只覆盖 JS 表层，不覆盖驱动层**：`Runtime.enable` 域启用产生的对象序列化事件（风控通过 hook `console.debug`、读 `Error.stack` 探测），**只在自动化客户端启用了 Runtime 域时才发生**，init script 无法修复"由自动化驱动本身产生的信号"。
2. **补丁自身是可检测的**：用 Proxy 改 `navigator.languages`，其 `toString()` 行为可能与原生不一致；伪造的指纹拼不出"内部一致的设备画像"。
3. **网络层、行为层完全不可触及**。

新一代框架（nodriver 等）的思路正是**从架构上消除特征**（严格限制 CDP 只用于非敏感域，甚至改用 OS 级真实输入），而非在 JS 层打补丁。

### 2.3 与本项目 Boss 实验的关系（一个观察，非定论）

本项目真机单变量实验（`boss-recruiting-agent-research.md` §8.6、设计文档 §2.1）精确锁定：**Boss 详情刷新的最小触发条件是 `Runtime.enable`**，而 WebSocket 连接、`Target.*`、`Page.enable`、`DOMSnapshot`、`Page.captureScreenshot`、`Input.dispatchMouseEvent` 都不触发。

这与 §2.2 描述的"Runtime 域是 stealth 检测核心点"在**现象上吻合**，但需注意：Boss 详情刷新的**确切机制未公开**（页面观察到的现象是详情 iframe 被拆除重建，但平台侧到底是用它做反爬探测、还是单纯的工程副作用，无法定论）。无论机制如何，事实结论是确定的——**纯键鼠路线完全不走 CDP 协议层，因此天然不触发这个刷新**。

### 2.4 纯键鼠路线仍需单独解决的问题

纯键鼠只攻破了"浏览器层"，**不解决**：
- **行为层**：直线鼠标、匀速/过快按键、无停顿滚动仍会被行为风控抓。微信 RPA 已用"专用号 + 5-10s 间隔 + 随机抖动 + 每日上限"缓解（见 `wechat-souyisou-rpa-design.md`）。
- **账号/IP 层**：请求速率、单账号频次、数据中心 IP，与输入方式无关，照样触发。
- **定位鲁棒性**：坐标受 DPI/分辨率/窗口大小/缩放影响（见 §5）。

---

## 3. 「Ctrl+A → Ctrl+C 全选复制」采集的可行性（关键决策依据，含验证方案）

这一节直接决定 Boss 详情页能否用"复制"代替"OCR"。

### 3.1 复制的本质

`Ctrl+C` 时，浏览器对当前 DOM **Selection**（Selection API）序列化，**同时**写入剪贴板多种格式：
- `text/plain`：纯文本渲染
- `text/html`：富文本 HTML 源码
- 偶尔 `image/png`

复制内容来自**当前可被选中的可见 DOM 文本节点**。微信搜一搜 RPA 正是利用这一点：结果页/详情页都是普通 CEF 网页（DOM 文本节点），`Ctrl+A → Ctrl+C` 后从剪贴板 `UnicodeText`/`Html` 读取，**无损且零 OCR 成本**（`wechat-souyisou.ps1:233-245`）。

### 3.2 复制的硬边界（理论）

| 内容类型 | 能否复制 | 原因 |
|----------|----------|------|
| **Canvas 绘制的文字** | ❌ **不能** | Canvas 对网页是位图，无文本节点，复制只得图片或无内容 |
| 图片里的字（raster） | ❌ 不能 | 同上，是像素 |
| **closed** Shadow DOM 文本 | ❌ 不能 | 设计上"never allow access"，`getSelection()` 无法到达 |
| **跨域 iframe 内** | ❌ 不能 | 同源策略阻止 |
| **同域 iframe 内（关键，Boss 适用）** | ⚠️ **selection 不跨 iframe 边界一起选** | 父页 `Ctrl+A` 选不到 iframe 里的内容，需先点进 iframe |
| 字体反爬（自定义字体替换字形） | ⚠️ 能复制但**乱码** | DOM 里是被字体替换后的码位 |
| **open** Shadow DOM 文本 | ✅ 能（部分） | selection 可穿透 open shadow root |
| 普通 DOM 文本 | ✅ 能 | 标准行为 |

### 3.3 对 Boss 直聘：理论判定 + 5 分钟真机验证方案

> ⚠️ **重要：以下"Canvas 无法复制"是理论判定，未经 Boss 真机复制验证。用户明确说"用全选复制的形式试试"，这个实验成本极低、信息量大，必须先做。**

**已知事实**（`boss-recruiting-agent-research.md` §8.6，DOMSnapshot 实测）：
- 详情 `/web/frame/c-resume` 的 body 核心是 `DIV → CANVAS`，整个 document **27 个节点**，普通 nodeValue "仅含页面标题等 6 项"，转换后 Markdown 正文只有"BOSS直聘"。
- 正文由约 2.9 MB 的 `wasm_canvas_bg-1.0.2-5097.wasm` 解密后，用 `CanvasRenderingContext2D.fillText` 绘制。

**理论判定（待验证）**：
- 详情页**核心正文**（自我介绍、工作业绩、工作内容长文本）在 Canvas 上 → `Ctrl+C` **大概率复制不到**。
- **但** DOMSnapshot 抓到的"页面标题等 6 项"nodeValues 提示：详情页**可能存在非 Canvas 的普通 DOM 区域**——顶部基础信息、右侧经历概览、各类按钮文案。这些区域 `Ctrl+C` **可能复制得到**。
- **关键问题**：这些"可能可复制的 DOM 碎片"加起来，对「初筛」是否够用？这直接决定复制路线在 Boss 的价值。**必须实测，不能靠推断。**

**5 分钟真机验证步骤**（成本极低，建议立即做）：

```
1. 用户手动登录 Boss，手动打开任一候选人详情浮层
2. 在详情浮层内点击一下（确保焦点在详情，而非外层推荐页）
3. 按 Ctrl+A 全选 → Ctrl+C 复制
4. 打开记事本 Ctrl+V 粘贴，保存为 txt
5. 把这份 txt 给我看，我来判断：
   - 能复制到多少有效信息（姓名/现公司/期望/技能标签/经历摘要？）
   - 是否够做"初筛"（初筛通常只需结构化摘要，不需自我介绍长文）
   - 哪些关键字段缺失（若缺失的恰好是初筛必需项，则复制路线价值有限）
```

> 微信 RPA 的经验可直接复用：`Ctrl+A → Ctrl+C` 后从剪贴板同时取 `UnicodeText`（纯文本）和 `Html`（富文本，保留结构，对解析更有用）两种格式。

**三种可能的实验结果与对应决策**：
- **结果 A**：能复制到 ≥80% 初筛所需字段 → 复制路线**高度可行**，详情页改走"复制优先 + OCR 补缺失项"，大幅降本。
- **结果 B**：能复制到部分字段（如姓名/公司/技能），但经历详情缺失 → **复制 + OCR 混合**：复制拿结构化字段，OCR 补 Canvas 正文。
- **结果 C**：几乎复制不到任何有用内容（全在 Canvas）→ 复制路线在详情页**不可行**，必须 OCR。**但列表页（普通 DOM）仍可复制**。

### 3.4 复制保护绕过（备查）

若网页是普通 DOM 但加了复制保护（`oncopy return false`、`user-select:none`、禁右键），绕过手段：禁用 JS / DevTools 改 CSS `user-select:auto` 并删内联事件 / 浏览器扩展（Allow Copy）/ 拖到可编辑字段 / `Ctrl+S` 存盘取源文。**纯键鼠 RPA 场景下，若遇复制保护，最稳妥是退回 OCR，不与保护脚本对抗。**

---

## 4. 通用方法论沉淀：针对特殊防爬网页的纯键鼠 RPA 操作逻辑

从微信搜一搜 RPA（已跑通）+ Boss 现有 CDP 方案 + 业界（影刀/UiBot/Power Automate）实践，提炼出**可复用的六件套**。这套方法论是本报告最重要的产出，应作为后续所有「难搞的网页/客户端自动化」的起点。

### 4.1 六件套架构

```
┌─────────────────────────────────────────────────────┐
│              RPA 编排层（状态机 + 熔断）              │
│  IDLE → 前置校验 → 执行 → 证据采集 → 判定 → 清理     │
├─────────────────────────────────────────────────────┤
│ 窗口层    │ 输入层     │ 读取层    │ 定位层          │
│ 身份校验  │ 安全按键   │ 剪贴板    │ 视觉(CV/OCR)    │
│ 激活置顶  │ 安全点击   │ (DOM页)   │ 坐标(相对)      │
│ 互斥锁    │ 滚轮       │ 截图OCR   │ 锚点            │
│           │            │ (Canvas页)│                 │
├─────────────────────────────────────────────────────┤
│                 安全层（DPAPI 加密 + 脱敏 + 审计）    │
└─────────────────────────────────────────────────────┘
```

### 4.2 各件套要点（均已在本项目真机验证）

**① 窗口层（防止误操作其他应用——RPA 第一安全问题）**
- `EnumWindows` + `IsWindowVisible` + 按面积最大筛选主窗口（微信坑：枚举到 66×98 隐藏迷你窗口，必须按可见+面积过滤，`wechat-desktop-rpa-technical-research.md` §1.4）。
- 进程路径强校验：微信要求 `\Tencent\Weixin\Weixin.exe`，插件窗口要求 `\Tencent\xwechat\xplugin\plugins\RadiumWMPF\` + 类名 `Chrome_WidgetWin_0` + 标题"微信"（`wechat-souyisou-lib.ps1:1-6, 28-39`）。Boss 应校验 Chrome 进程路径 + `zhipin.com` 页面。
- 激活用 `AttachThreadInput` 三次重试（`lib:202-233`），失败抛 `WX_ACTIVATION_FAILED`。
- 进程互斥锁 `Local\AidWorkAgent.<AppName>`（`wechat-souyisou.ps1:77`），单机单实例。
- **每次动作前用 `$ForegroundGuard` 校验前台窗口仍可信**，否则抛 `FOREGROUND_LOST`——这是防止"操作途中用户切走窗口导致键鼠打到别的应用"的关键。

**② 输入层（安全按键——保证释放 + 拟人）**
- 虚拟键码表 + `Invoke-SafeKeyChord`（`lib:155-177`）：按下所有键 → 暂停 → **finally 中逆序释放**，保证按键一定被释放（不会卡住 Ctrl）。
- Boss 详情页关闭用 `Escape`（现有 CDP 方案用 `rawKeyDown`/`keyUp`，纯键鼠可直接 `keybd_event`）；微信详情关闭用 `Ctrl+W`。
- 滚轮用 `mouse_event(MOUSEEVENTF_WHEEL)` + `ConvertTo-MouseWheelData`（`lib:727-730`）。
- **拟人节奏**：专用号 + 5-10s 间隔 + 随机抖动 + 每日上限（缓解行为风控，`wechat-souyisou-rpa-design.md`）。
- **输入法干扰规避**：中文输入法会吞掉字母按键。用 `Ctrl+V` 粘贴代替逐字 `keybd_event` 输入（微信方案即如此），或发送前临时切英文输入法。

**③ 读取层（复制 or OCR——按 §3 判据二选一）**
- 普通网页：`Ctrl+A → Ctrl+C` → 剪贴板 `UnicodeText`/`Html`（微信方案，零 OCR 成本）。
- Canvas/图片网页：分段截图 + 拼接 + OCR（Boss 方案，`LongScreenshotStitcher` + Tesseract/PaddleOCR）。
- 读取前校验证据完整性：微信用 `Test-DetailEvidence` 要求含目标人/协会名 + 文档标记；Boss 用相邻图 MSE 判稳定/到底。

**④ 定位层（鲁棒性核心——§5 详述）**
- 优先级：控件树选择器 > 相对坐标+图像锚点 > 纯坐标。
- 纯键鼠场景无 DOM 选择器（这正是放弃 CDP 的代价），走「相对坐标 + 图像锚点」：微信用 `Find-DarkThemeCardBands` 按亮度差检测卡片 band 算点击点 `(x_ratio, y_ratio)`，**用比例而非绝对像素**，对窗口尺寸变化有容忍度。

**⑤ 安全层（敏感信息加密——AGENTS.md 强制要求）**
- DPAPI 加密落盘 `Protect-EvidenceArtifact`（`lib:420-436`）：`ProtectedData.Protect(..., CurrentUser)`。
- 日志脱敏 `Get-RedactedSummary`（`lib:447-452`）：手机号正则替换 `1**********` + 截断 160 字符。
- 审计脱敏：过滤 cookie/security/token/body 等字段，sessionId 哈希（Boss 现有 `audit.mjs:15-23` 已有此模式，可平移）。
- 明文只在 DPAPI 解密层 + 二次证据校验后短暂出现（`extract-mobile.ps1`）。

**⑥ 编排层（状态机 + 失败熔断 + 严格清理）**
- 显式状态机：`IDLE → WAITING_MANUAL_LOGIN → ... → CHECKPOINT`（Boss 设计 §状态机）；微信的 `dry_run` 默认只输出计划，必须 `-Execute` 才操作。
- 失败熔断：连续 N 次失败跳出循环（微信 `Test-ResultPageEvidence` 连续两次失败熔断）。
- **严格清理 finally**：无论成功/异常都关闭详情 → 关插件/页面 → 恢复主窗口前台，失败抛 `SESSION_CLEANUP_FAILED`（`Close-WeixinPluginSession` `lib:41-115`）。
- 人机协同：登录、CAPTCHA、视觉确认由人工完成（Boss 现有"用户手动扫码登录 + 点击准备完成"，微信 dry_run）。

### 4.3 浏览器快捷键武器库（纯键鼠 RPA 操作浏览器的核心手段）

纯键鼠路线操作浏览器，主要靠系统快捷键（这些键发送的是 `isTrusted=true` 的真实事件）：

| 场景 | 快捷键 | 说明 |
|------|--------|------|
| 全选页面文本 | `Ctrl+A` | ⚠️ Boss 详情在 iframe 内，需先点进 iframe 再全选；不跨 iframe 边界 |
| 复制选中 | `Ctrl+C` | 复制 DOM 文本节点，剪贴板 `text/plain` + `text/html` |
| 粘贴 | `Ctrl+V` | 规避输入法干扰的最佳输入方式 |
| 关闭标签页 | `Ctrl+W` | 微信详情关闭用此；Boss 详情是浮层用 `Esc` |
| 关闭浮层/弹窗 | `Escape` | Boss 详情浮层关闭用此 |
| 新标签页 | `Ctrl+T` | 导航用 |
| 切换标签页 | `Ctrl+Tab` / `Ctrl+数字` | 多标签时定位目标标签 |
| 地址栏聚焦 | `Ctrl+L` / `Alt+D` | 直接导航到 URL |
| 页面内查找 | `Ctrl+F` | 微信打开搜一搜入口用此 |
| 页面缩放重置 | `Ctrl+0` | **RPA 必做**：锁定缩放比例，否则坐标定位失效 |
| 滚动 | `Page Down` / `Space` / `↑↓` / 滚轮 | 滚动加载或分段截图 |
| 后退 | `Alt+←` | 从详情返回列表（若 Boss 改成页面跳转而非浮层） |

### 4.4 通用方法论的判据树（快速决策）

```
要自动化一个网页/客户端：
├─ 目标是普通 DOM 文本？ ──是──→ 走 DOM 嗅探（Playwright/CDP），最稳最快
│   └─ 有强反爬 / 自绘 UI？ ──是──→ 走纯键鼠 + Ctrl+A/C 复制（如微信搜一搜）
├─ 目标是 Canvas/图片/closed shadow？
│   └─ 有强反爬？ ──是──→ 走纯键鼠 + 截图 OCR（如 Boss 详情，复制路线需先按 §3.3 验证）
│       └─ CDP 不触发反爬？ ──是──→ 原生 CDP 截图（Boss 现状，省键鼠定位成本）
└─ 目标是标准桌面控件？ ──→ UIA/控件树（pywinauto/Power Automate）
```

---

## 5. RPA 稳定性工程实践

纯键鼠路线最大的工程弱点是**定位鲁棒性**。坐标受 DPI 缩放、分辨率、窗口大小、浏览器 zoom 综合影响。成熟做法按可靠性递增：

| 定位策略 | 可靠性 | 来源/实现 |
|----------|--------|-----------|
| 控件树选择器（UIA/DOM/accessibility） | ★★★★★ | 但自绘 UI/Canvas 不可用 |
| 相对坐标 + 图像锚点（Anchor Base） | ★★★★ | UiPath Anchor Base；微信 `Find-DarkThemeCardBands`（比例坐标） |
| 图像模板匹配（template matching） | ★★★ | OpenCV `matchTemplate`；微信搜索框定位方案 |
| 绝对像素坐标 | ★ | 最脆弱，应避免 |

**Anchor Base 思想（重要沉淀）**：当目标元素本身不易定位时，用**附近稳定元素作锚点**，再用相对偏移定位目标。配合"以锚点为基准的偏移矩形"抓取文本（UiPath Relative Scraping / Set Clipping Region）。本项目微信方案已隐式使用（搜索框作视觉锚点）。

**RPA 场景必须锁定的环境变量**（否则坐标/图像定位必失效）：
- **浏览器缩放 = 100%**（运行前发 `Ctrl+0` 重置）。
- **窗口位置/大小固定**（激活后立即 `MoveWindow` 到固定矩形，微信方案已含窗口矩形获取）。
- **DPI 感知**：脚本进程必须声明 DPI Aware，否则 `GetWindowRect` 返回的是逻辑像素而非物理像素，坐标全错。
- **屏幕分辨率固定**（迁机/换显示器需重新校准坐标，用比例坐标 + 图像锚点可缓解）。

**异常处理模式**（来自 UiPath 等成熟工具，与 §4.2⑥ 呼应）：
- 状态机工作流：状态 + 转换 + 触发器，用 `Check App State` 在转换触发器里检测条件（如验证码出现）路由到分支。
- 重试：Try/Catch + Retry Scope 对不稳定元素查找自动重试 N 次。
- 熔断：重试计数器超阈值即跳出，转清理状态。
- 清理：专门的清理状态，终止前无条件执行。
- HITL：遇 CAPTCHA/人工判断时，暂停流程生成人工任务，提交后自动恢复（UiPath Action Center 模式；Boss 现有"人工门禁"是其简化版）。
- 视觉确认：操作后用图像存在性校验做断言，确认预期状态达成再推进（微信 `Test-SearchResultReady` 连续两次确认才进入取证）。

---

## 6. 对 Boss 直聘的具体建议

### 6.1 决策矩阵：继续原生 CDP vs 转纯键鼠 RPA

| 维度 | 原生 CDP（现状） | 纯键鼠 RPA（新方案） |
|------|------------------|----------------------|
| **反爬对抗** | ✅ 已验证不触发详情刷新（因不启用 Runtime 域） | ✅ 完全不走 CDP，天然不触发；事件层 `isTrusted=true` |
| **抗平台更新** | 🔴 **脆弱**：依赖"刷新触发条件恰好是 Runtime.enable"这个经验观察，平台一旦改变触发逻辑，整个自研 CDP 链路崩 | ✅ 不依赖该约束，平台改 CDP 检测逻辑不影响键鼠 |
| **技术栈统一** | 🔴 独立自研原生 CDP（WebSocket + method 白名单），与微信 RPA（PowerShell）割裂 | ✅ 与微信搜一搜 RPA 同栈（PowerShell + Win32），六件套可复用 |
| **列表定位精度** | ✅ DOMSnapshot 精确拿候选人结构化数据 + HMAC 指纹去重 | ⚠️ 靠图像/比例坐标，精度略低；但列表是普通 DOM，可 `Ctrl+C` 复制补偿 |
| **详情正文提取** | 截图 + OCR（Canvas 硬约束） | 截图 + OCR（同约束）；**若 §3.3 实测复制可行，可改"复制优先+OCR 补"降本** |
| **是否需窗口前台** | ❌ 可后台截图、不占键鼠 | 🔴 需窗口前台、占用键鼠（驱动级点击可缓解但不能完全后台） |
| **工程复杂度** | 高（自研 CDP 客户端、禁用 Playwright Page 抽象、滚轮 MSE 判底） | 中（复用微信六件套，但要做 Boss 专属视觉定位） |
| **可后台运行** | ✅ | ❌ |
| **合规** | 人工辅助、不绕防爬 | 同左（见 §6.4） |

**决策建议（取决于两个问题的答案）**：

- **若 §3.3 复制实验"结果 C"（详情几乎复制不到）+ 当前 CDP 方案稳定够用** → **维持 CDP，不迁移**。纯键鼠作为"平台更新导致 CDP 崩溃"时的应急兜底（§6.2）。
- **若 §3.3 复制实验"结果 A/B"（详情能复制到有价值字段）+ 你重视技术栈统一和抗平台更新** → **值得转纯键鼠 RPA**。复制能拿到结构化数据会大幅降低 OCR 成本和复杂度，且与微信 RPA 共享底座。
- **折中（推荐起点）**：先做 §3.3 实验，同时把微信六件套抽象为通用底座（§6.2 A），两条路都铺好接口，最后据实验结果选择 Boss 适配器实现。

### 6.2 推荐的两步走

**(A) 抽象通用 RPA 底座**（`clients/rpa-core/` 或 `src/tools/rpa_core/`）
把微信搜一搜 RPA 的六件套（§4.2）抽成平台无关的底座，Boss 与微信都作为适配器。收益：
- 复用窗口身份校验、安全按键、DPAPI 加密、状态机熔断——纯键鼠 RPA 最易踩坑、微信已填平的部分。
- 为 §6.1 的"折中"提供前提：两条路共享底座，切换成本最低。

**(B) 在 Boss 客户端增加「纯键鼠兜底通道」开关**
仅当原生 CDP 连接失败 / Runtime 域约束失效（平台更新）时，切换为：系统 Chrome 前台 → `keybd_event`/`mouse_event` 键鼠 → 截图 + OCR（正文）+ 复制/DOMSnapshot（列表，取可用者）。

### 6.3 若转纯键鼠重写 Boss，需要补的关键件 + 已知坑

1. **窗口层**：Chrome 进程路径校验 + `zhipin.com` 标签页识别。
   - ⚠️ **多标签页坑**：Chrome 多个标签共享一个 HWND（标签是内部绘制，不是独立窗口）。纯键鼠无法用 HWND 区分标签，必须靠 `Ctrl+Tab`/`Ctrl+数字` 切到目标标签，或用 UIA 读地址栏 URL 确认。**这是浏览器 RPA 与微信 RPA（插件窗口独立 HWND）的关键差异。**
2. **定位层**：列表卡片点击点用「图像锚点 + 比例坐标」。Boss 列表是浅色主题，需重做亮度差检测（微信 `Find-DarkThemeCardBands` 是暗色主题）或改用固定模板。
3. **读取层**：详情正文**先按 §3.3 实测**；列表用 `Ctrl+A/C` 复制（普通 DOM，但注意在 iframe 内需先点进去）。
   - ⚠️ **iframe 全选坑**：Boss 列表在 `recommendFrame`、详情在 `c-resume`，都是 iframe。父页 `Ctrl+A` 选不进 iframe，必须**先鼠标点进 iframe 区域**再 `Ctrl+A`。
4. **关闭详情**：`Escape` 键（`keybd_event`），现有 CDP `rawKeyDown` 经验可直接平移。
5. **拟人节奏**：专用招聘账号 + 操作间隔随机化 + 每日上限（缓解 Boss 频控/行为风控）。
6. **缩放锁定**：运行前发 `Ctrl+0` 重置浏览器缩放，否则坐标定位失效。

### 6.4 合规边界（不可逾越，两条路线共同适用）

延续 `boss-recruiting-agent-research.md` §4 的既定边界，**纯键鼠路线不改变合规约束**：
- Boss 用户协议将"拟人程序等非正常浏览手段获取数据"定义为非法获取。纯键鼠虽 `isTrusted=true`，**但在法律意义上仍是自动化获取**，不因技术隐蔽而合规化。
- 因此仍应：不逆向私有接口、不绕频控/验证码、不批量多账号、上线前取得平台书面授权或法务确认、默认人工辅助模式、写动作（打招呼/不合适）人工确认。
- 纯键鼠路线的定位应是"在已获授权前提下，用更稳定/更不易误触发风控的方式辅助招聘人员"，**不是"绕过 Boss 防爬"**。

---

## 7. 一句话总结

纯键鼠 RPA 在反爬的「浏览器层」（事件 `isTrusted=true` + 环境零注入）确实优于 CDP/stealth，但**不解决网络层和行为层**；「Ctrl+A → Ctrl+C」对普通 DOM 网页有效，但 **Boss 详情核心正文是 Canvas，复制可行性必须先做 §3.3 的 5 分钟真机实验**（详情页非 Canvas 的 DOM 碎片可能可复制，对初筛是否够用是开放问题）；现有原生 CDP 方案因"不启用 Runtime 域"已规避刷新，但**这个约束脆弱（依赖经验观察）**——是否转纯键鼠，取决于复制实验结果 + 你对"技术栈统一/抗平台更新"的权重，决策矩阵见 §6.1。无论哪条路，先把微信 RPA 六件套抽象为通用底座。

---

## Sources

**反爬检测 / stealth 失效 / isTrusted**
- [Why JS stealth plugins break (CloakBrowser)](https://cloakbrowser.dev/blog/why-js-stealth-plugins-break/)
- [From Puppeteer stealth to Nodriver (Castle.io)](https://blog.castle.io/from-puppeteer-stealth-to-nodriver-how-anti-detect-frameworks-evolved-to-evade-bot-detection/)
- [puppeteer-extra-plugin-stealth (GitHub)](https://github.com/berstend/puppeteer-extra/blob/master/packages/puppeteer-extra-plugin-stealth/readme.md)
- [Event: isTrusted (MDN)](https://developer.mozilla.org/en-US/docs/Web/API/Event/isTrusted)
- [Chromium-dev: can browser detect synthetic input?](https://groups.google.com/a/chromium.org/g/chromium-dev/c/738En-eAgT4)
- [Behavioral Biometrics for Bot Detection (GeeTest)](https://www.geetest.com/en/article/behavioral-biometrics-bot-detection)

**国产/通用 RPA 原理**
- [影刀 RPA 3 种点击模式（火山引擎）](https://developer.volcengine.com/articles/7660756325244108810)
- [桌面应用自动化——控制微信/钉钉终极方案（火山引擎）](https://developer.volcengine.com/articles/7655453335230642921)
- [微信 4.0+ 环境下的 RPA 自动化技术研究报告 (Quicker)](https://getquicker.net/KC/Kb/Article/1171)
- [来也 UiBot 官网介绍](https://laiye.com/news/post/2223.html)
- [云扩 RPA 自动化原理](https://academy.encoo.com/zh-cn/wiki/DevGuide/AutomationPrinciples.md)
- [MS Learn：使用 UI 元素进行自动化 (Power Automate)](https://learn.microsoft.com/zh-cn/power-automate/desktop-flows/ui-elements)

**剪贴板 / Shadow DOM / Boss 反爬**
- [Clipboard.write() (MDN)](https://developer.mozilla.org/en-US/docs/Web/API/Clipboard/write)
- [closed shadow root access (WICG)](https://github.com/WICG/webcomponents/issues/378)
- [Boss 直聘薪资字体反爬机制逆向分析 (影刀社区)](https://www.yingdao.com/community/detaildiscuss?id=944041417883271168)

**RPA 稳定性工程**
- [Anchor Base (UiPath Docs)](https://docs.uipath.com/activities/other/latest/ui-automation/anchor-base)
- [Relative Scraping (UiPath Docs)](https://docs.uipath.com/activities/other/latest/ui-automation/relative-scraping)
- [Human-in-the-Loop with Action Center (UiPath)](https://www.uipath.com/community-blog/tutorials/human-in-the-loop-automation0-for-customer-onboarding-using-action-center)

**纯键鼠 vs DOM/CDP 权衡**
- [浏览器自动化六大技术路线深度对比](https://www.cnblogs.com/haibindev/p/19737374)
- [深入理解浏览器自动化协议：从 CDP 到 BiDi](https://zhuanlan.zhihu.com/p/2025965427466019047)

**本项目内部证据（权威）**
- `docs/research/boss-recruiting-agent-research.md` §8.6（Runtime 域最小触发条件、详情 Canvas 事实）
- `docs/design/recruiting/boss-resume-assistant-native-cdp-design.md` §2（实验事实与架构约束）
- `docs/research/wechat-desktop-rpa-technical-research.md`（微信 4.x UIA 失效、键鼠路线选型）
- `clients/wechat-souyisou-rpa/scripts/wechat-souyisou-lib.ps1`（六件套实现）
