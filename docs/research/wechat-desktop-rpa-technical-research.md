# 微信桌面版 RPA 自动化技术方案调研

> 日期：2026-07-27
> 关联：[微信桌面版搜一搜 RPA — POC 计划](./wechat-desktop-souyisou-rpa-poc.md)
> 目的：在 POC 文档（影刀路线）失败后，重新调研「自研 RPA 自动化微信桌面版搜一搜」的最佳技术路线，并通过 PowerShell 真机验证关键假设。
> 结论先行：**已放弃影刀录制回放，主方案定为「图像识别 + OCR + 键鼠模拟」**（Python 技术栈），物理链路已真机验证通过。
>
> **2026-07-27 后续更新**：进一步真机实验已证明搜一搜结果页和详情页可通过 `Ctrl+A → Ctrl+C` 无损读取文字，详情可用 `Ctrl+W` 关闭，结果区可直接发送滚轮事件。因此正式实现已调整为 **PowerShell CLI + 剪贴板 Reader + 视觉卡片定位**；OCR 降为坐标映射兜底，Python 不再是第一期必需依赖。最新决策以[正式设计](../tools/wechat-souyisou-rpa-design.md)为准，本文保留为选型演进记录。

---

## 0. 背景与动机

原 POC 计划押宝「影刀 RPA 录制回放 + OCR」。真机实测：**影刀录制回放不成功**。根因需要重新定位，并重新选型一条不依赖影刀、不依赖控件树的自研路线。

本调研在用户当前运行微信（已登录、主窗口可见）的机器上，用 PowerShell 脚本做了 4 轮真机探测，回答以下问题：

1. 微信 4.x 到底是什么架构？（CEF？Qt？原生？）
2. 为什么所有 UIA 工具（影刀/Inspect/FlaUI）都抓不到控件？
3. 搜一搜结果页是什么渲染的？
4. 图像/OCR 路线物理上可行吗？（窗口定位 → 截图 → 可读画面）

---

## 1. 真机探测结论（PowerShell 实测）

测试环境：微信 **4.1.11.24**（`C:\Program Files\Tencent\Weixin\Weixin.exe`），Windows 11。

### 1.1 进程架构

| 进程 | 角色 | 关键发现 |
|------|------|---------|
| `Weixin.exe`（5 个实例） | 主进程 | 版本 4.1.11.24，**主窗口类名 `Qt51514QWindowIcon`** |
| `WeChatAppEx.exe`（xwechat/RadiumWMPF） | CEF/Chromium 渲染进程 | 路径 `AppData\Roaming\Tencent\xwechat\xplugin\plugins\RadiumWMPF\20005\extracted\runtime\WeChatAppEx.exe`，多个 `--type=renderer` 子进程 |
| `WeChatAppEx.exe`（WXWork/wmpf_Applet） | 企业微信小程序运行时 | 与本调研无关（企业微信残留） |

**架构定性**：微信 4.x = **Qt 5.15.14 宿主窗口 + 自研渲染层 MMUI + CEF（WeChatAppEx）内嵌网页**。混合架构。

> 注意：`WeChatAppEx` 是腾讯「RadiumWMPF」框架封装的 Chromium，用于渲染所有网页/H5/小程序内容。**搜一搜结果页就是 CEF 网页**。

### 1.2 UIA 控件树探测（关键）

对主窗口 `Qt51514QWindowIcon` 走 `System.Windows.Automation` 控件视图树（深度 5），结果只有 **3 个节点**：

```
[Window] cls='Qt51514QWindowIcon' name='微信'
  [Pane]  cls='Qt51514QWindowIcon' name='Weixin'
    [Pane] cls='MMUIRenderSubWindowHW' name='MMUIRenderSubWindowHW'
```

**没有任何按钮/输入框/列表项/文本节点。** 这就是「微信 4.0 UI 树消失」的真相：

- Qt 只提供顶层窗口壳，内部所有 UI 都由 **MMUI（微信自研 GPU 渲染层）** 自绘——本质上和游戏画面一样，是一张被持续重绘的位图。
- UIA / Inspect / FlaUI / pywinauto / 影刀控件抓取**全部只能看到壳**，拿不到任何子控件。
- 这与 3.x 时代「原生控件 + UIA 可达」完全不同，是架构级断代。**不可逆，无法靠工具配置绕过。**

### 1.3 CEF 远程调试端口探测

遍历所有 `WeChatAppEx.exe` 命令行：

- **没有任何进程带 `--remote-debugging-port` 参数**
- **没有任何 WeChatAppEx/Weixin 进程监听 TCP 端口**（9222/9333 等常见端口全部关闭）

结论：CEF 的 DevTools 协议（CDP）默认关闭。要走 CDP 路线，必须**带 `--remote-debugging-port=NNNN` 参数重启微信**，且腾讯随时可能在更新里封掉这个入口（见风险章）。

### 1.4 截图链路验证（图像路线前提，✅ 通过）

用 Win32 `EnumWindows` + `GetWindowRect` + `Graphics.CopyFromScreen` 实测：

- 主进程 PID 13320 共有 **4 个 `Qt51514` 窗口**（含隐藏辅助窗口）
- 按可见性 + 面积过滤，定位到真正的微信主窗口：**1008×968 @ (130,296)**
- 截图保存为 PNG，**画面完整、文字锐利、对比度高（暗黑模式），完全适合 OCR**

> ⚠️ 坑：直接 `EnumWindows` 返回第一个匹配 `Qt51514` 的窗口，会拿到一个 66×98 的隐藏迷你窗口。必须**枚举全部 + 按可见 + 面积最大**筛选。

视觉分析确认：截图能清晰看到左侧导航栏、聊天列表、搜索框（左上角，约占 x:5%-30% / y:3%-8%）、聊天内容区。**搜索框是稳定的视觉锚点。**

---

## 2. 技术路线对比

| 路线 | 原理 | 可行性 | 准确率 | 风控/稳定性 | 工作量 |
|------|------|--------|--------|------------|--------|
| **A. UIA 控件树**（影刀录制回放原方案） | 抓取控件属性操作 | ❌ **不可行** | — | — | — |
| **B. 图像 + OCR + 键鼠** ⭐主方案 | 截图→模板匹配/OCR 定位→坐标点击→OCR 读结果 | ✅ 已验证物理链路 | 中高（OCR 手机号需正则校验） | 低（纯像素，微信无感知） | 中 |
| **C. CEF 远程调试（CDP）** | 带 `--remote-debugging-port` 重启微信，用 DevTools 协议读 DOM | ✅ 技术可行 | **最高**（直接读 DOM 文本） | 中高（每次重启、可能被腾讯封入口、版本更新失效） | 中 |
| **D. 内存/Hook 注入** | 逆向 MMUI，hook 渲染管线 | ❌ 不现实 | — | 极高（封号 + 法律风险） | 极高 |
| **E. 真机 + ADB/模拟器** | 安卓模拟器跑微信 | ❌ POC 已否决 | — | 极高（封号率 ~27%） | 高 |

### 选型决策：路线 B（图像 + OCR + 键鼠）为主

理由：

1. **唯一不依赖微信内部结构**的方案。MMUI 自绘 + CEF 网页都不影响——像素就是像素，微信怎么改版，只要界面长得差不多就能跑。
2. **零侵入、零风控特征**。不重启、不改启动参数、不开调试端口、不注入。微信侧无法把这套操作和正常人工操作区分开。
3. **物理链路已真机验证通过**（§1.4）。
4. 与 POC 文档唯一判据一致：能从结果里稳定提取手机号即算通过。

**路线 C（CDP）作为高准确率备选**保留，仅在 B 的 OCR 手机号误码不可接受时，再评估「重启微信开调试端口」的代价。

---

## 3. 主方案架构设计（路线 B）

### 3.1 技术栈

| 层 | 选型 | 理由 |
|----|------|------|
| 语言 | **Python 3.11+** | 生态最全（OCR/CV/自动化），与本项目后端一致，便于集成 |
| 窗口控制 | `pygetwindow` + `pywin32`（Win32 API） | 枚举窗口、激活、取矩形 |
| 键鼠模拟 | `pyautogui` 或 `pynput` | 点击、输入、回车；pyautogui 带 FAILSAFE |
| 图像定位 | **OpenCV (`cv2.matchTemplate`)** | 模板匹配，定位搜索框/Tab/按钮等固定图标 |
| OCR | **PaddleOCR**（中文最优）或项目已有 paddleocr 工具 | 中文识别准确率最高；本项目已装 |
| 截图 | `mss`（高速）或 `PIL.ImageGrab` | 全屏/区域截取 |
| 编排 | 纯 Python + `asyncio`/`time.sleep` 节流 | 拟人节奏，避免风控 |

> 复用提示：本项目 `src/tools/ocr/` 已有 paddleocr 能力，`src/tools/browser/` 有图像/视觉相关积累，可参考。

### 3.2 流程编排（单次搜索）

```
[定位微信主窗口] → [激活置顶] → [点搜索框] → [输入关键词] → [回车]
  → [等待结果加载（轮询画面稳定）] → [切「文章」Tab（模板匹配）]
  → [滚动加载更多（可选）] → [对结果区截图] → [PaddleOCR 识别]
  → [正则 1[3-9]\d{9} 抽手机号] → [去重 + 校验] → [落库]
```

每个协会跑一遍，**间隔 5-10 秒拟人**（POC 文档既定）。

### 3.3 关键技术点与解法

| 难点 | 解法 |
|------|------|
| 定位主窗口（有多个 Qt 窗口） | 枚举全部 `Qt51514QWindowIcon` + `IsWindowVisible` + 面积最大，§1.4 已验证 |
| 搜索框位置漂移 | **模板匹配**而非固定坐标：预存搜索框/放大镜图标小图，`matchTemplate` 定位；窗口缩放用相对坐标 |
| 结果加载完成判断 | 连续两帧截图哈希差 < 阈值 → 稳定；或 OCR 到「没有更多了」类文案 |
| OCR 手机号误码（8↔0、漏位） | 正则 `1[3-9]\d{9}` 严格 11 位；多截几次交叉验证；本地号段库二次过滤 |
| 结果区分页/滚动 | `pyautogui.scroll` 或 PageDown，每滚一次重 OCR，去重合并 |
| 微信失焦/被遮挡 | 操作前 `SetForegroundWindow`；FAILSAFE 鼠标移角落急停 |
| 暗黑/浅色主题 | PaddleOCR 对两种主题都能识别；模板匹配准备两套模板或做反色预处理 |

### 3.4 抽象层（便于后续接 CDP）

```
WeChatRPA（编排层）
  ├── Locator        # 定位策略：CVLocator(模板匹配) / CDPLocator(DOM) 可替换
  ├── Action         # 操作：Click / Type / Enter / Scroll / Screenshot
  ├── Reader         # 读取：OCRReader / CDPReader
  └── Flow: souyisou_search(keyword) -> [PhoneRecord]
```

Locator/Reader 抽象成接口，**图像路线是默认实现，CDP 是未来可替换实现**，编排层不变。

---

## 4. 风险与缓解

| 风险 | 等级 | 缓解 |
|------|------|------|
| OCR 手机号误码 | 中 | 严格正则 + 多次截取交叉 + 号段库；必要时回退路线 C（CDP）读 DOM |
| 微信改版导致模板失效 | 中 | 模板匹配对位置漂移有容忍度；改版大时重做小图模板（成本低） |
| 频繁操作触发风控 | 中 | 专用号 + 5-10s 间隔 + 随机抖动 + 每日上限 |
| 搜索框定位失败 | 低 | 模板匹配 + 多候选锚点（搜索框/放大镜/「搜索」文案） |
| 操作期间用户动鼠标 | 低 | pyautogui FAILSAFE；建议运行时机器独占 |
| 搜一搜结果本身无手机号 | 高（业务侧） | 非技术问题，按 POC 判据 ≥2/3 命中即算路通 |

---

## 5. 下一步（POC → 自研 RPA）

按 POC 文档的「唯一判据」推进，但把实现从影刀换成自研 Python RPA：

1. **Step 1（半天）**：搭最小闭环——定位窗口 → 截图 → 模板匹配搜索框 → 点击 → 输入「中国黄金协会 陈玉千」→ 回车 → 截结果区 → PaddleOCR → 正则抽手机号。
2. **Step 2**：跑 POC 的 3 个测试协会，统计命中率/误抓率。
3. **Step 3（判据）**：≥2/3 命中且误抓 <20% → 路通，进入正式设计（铺几千家 + agent 集成）；否则评估路线 C（CDP）。

> POC 通过后，自研 RPA 作为独立工具/子智能体技能接入主系统（参考本项目 `src/tools/` 或 `subagents/` 扩展点）。

---

## 6. 探测脚本归档

本次真机验证的 PowerShell 脚本位于 `docs/research/rpa_probe/`：

| 脚本 | 作用 |
|------|------|
| `probe_wechat.ps1` | 枚举微信进程、版本、加载 DLL、窗口类名、CEF 端口 |
| `probe_cef.ps1` | 探测 WeChatAppEx 命令行参数（找 `--remote-debugging-port`）、监听端口 |
| `probe_uia2.ps1` | UIA 控件树探测（证实 MMUI 自绘、UIA 不可达） |
| `probe_capture2.ps1` | 窗口定位 + 截图验证（证实图像路线物理可行） |
| `wechat_main.png` | 截图样本（1008×968，验证画面可读） |

> 这些脚本仅为调研验证用，非生产代码。PowerShell 在此用作「系统级调用探针」，验证结论后，正式 RPA 用 Python 实现。
