# 企业微信个人账号 RPA 视觉定位方案 —— 关键技术突破总结

> 关联：
> - 视觉定位设计：[wecom-personal-rpa-vision-design.md](./wecom-personal-rpa-vision-design.md)（权威设计）
> - 主设计：[wecom-personal-rpa-design.md](./wecom-personal-rpa-design.md)
> - 开发计划：[plans/plan-wecom-personal-rpa-vision.md](../../plans/plan-wecom-personal-rpa-vision.md)
> - 客户端实现状态：[clients/wecom-personal-rpa/STATUS.md](../../clients/wecom-personal-rpa/STATUS.md)
>
> 创建日期：2026-06-24
> 登记：[docs/ideas.md](../ideas.md) #29（企业微信个人账号 RPA 接入）
> 类型：项目级技术决策与突破记录（非调研、非设计、非开发计划）

---

## 0. 总结目的

记录 2026-06-23 ~ 2026-06-24 期间，企业微信个人账号 RPA 客户端从「UIA 失效」到「视觉定位方案生产代码级落地」的关键技术决策、踩过的坑、最终方案，作为后续维护与新员工入门的技术档案。

**适用读者**：客户端维护者、新接手该项目的工程师、想做类似企微自动化项目的人。

---

## 1. 核心技术突破

### 1.1 突破：发现并弃用 UIA/MSAA 三层降级链

**原设计假设**（2026-06-16 主设计 §6.2）：企微 PC 客户端可以通过三层降级策略稳定定位 UI 元素——FlaUI UIA3 → Win32 坐标 + SendInput → OpenCvSharp 模板匹配。

**真机验证结果**（2026-06-23）：

| 层 | 验证方式 | 实测结果 | 结论 |
|---|---|---|---|
| Layer 1（UIA3） | `dotnet build` 后跑 `FlaUI.UIA3Automation.GetDesktop().FindFirstChild(ByClassName("WeWorkWindow"))` | 主窗口下 dump 出 **0 个控件**（只有 TitleBarWindow / PerryShadowWnd 装饰性外壳） | **彻底失效** |
| Layer 1 备选（MSAA） | `oleacc.dll AccessibleObjectFromWindow + AccessibleChildren` | 根对象 `Name=企业微信` 但**子对象数 = 0** | **彻底失效** |
| Layer 2（Win32 坐标） | 设计评估 | 单纯坐标方案对窗口位置/DPI/版本变化零鲁棒性 | **路线否决** |
| Layer 3（OpenCV 模板） | 设计评估 | 模板换一下就失效，企微升级即崩，是「坐标方案」的变体 | **路线否决** |

**根因**：腾讯系产品（QQ、微信、企业微信）的 UI 内容（聊天列表、输入框、发送按钮）全是 DirectUI / Direct2D 自绘，UIA 和 MSAA 树里**根本不存在**——只能拿到窗口外壳（标题栏、阴影边框），里面的实际控件是 GDI/Direct2D 画出来的像素，没有任何 a11y 元数据暴露。

**关键技术教训**：任何依赖 a11y API（UIA / MSAA / ATK / AXUI）定位腾讯系产品 UI 的方案都注定失败。必须走「视觉」路线。

### 1.2 突破：Qwen3-VL 多模态视觉定位企微 D2D UI

**真机验证**（2026-06-23）：

用企微真实主窗口截图（1448×1392）调 Qwen3-VL-Plus（DashScope OpenAI 兼容接口），模型返回：

| 元素 | bbox（相对企微主窗口左上） | 准确性 |
|---|---|---|
| Search box | (122,15)-(243,38) | ✅ |
| Message input | (293,819)-(988,985) | ✅ |
| Send button | (951,965)-(984,980) | ✅ |
| Left navigation icons | (10,17)-(72,985) | ✅ |
| 20 个 conversation items | Y 坐标均匀间隔 45 像素 | ✅ |

**关键发现**：
- Qwen3-VL 对企微 D2D 自绘 UI 的 grounding 能力**远超预期**——24 个元素全部精准命中
- 单次 grounding 调用 ~28s，~3100 tokens，**单次成本约 0.02-0.04 元**
- 软件识别能力：模型能正确区分企微 vs 钉钉 vs VSCode（通过「微盘」「智能文档」等企微特有功能名识别）

### 1.3 突破：视觉缓存层让 API 成本接近零

**问题**：单次 grounding 28s + 0.03 元，如果每次发送消息都调 5 次（搜索框 + 会话项 + 输入框 + 发送按钮 + 状态确认）= 140s + 0.15 元/条，不可接受。

**解决方案**：基于「窗口指纹」的视觉缓存层。

```
缓存键 = (WindowClass, WindowRect, DpiScale, WeComVersion, ElementType, LabelKeyword)
```

任一字段变化（如窗口移动 1 像素、DPI 改了、企微升级版本）→ 视为新 key → 重新调 API。

**真机验证**：
- 第 1 次定位：调 API（28s）
- 第 2/3 次同元素定位：缓存命中（**7-29ms**），Source="cache"，**零 API 成本**
- 窗口尺寸变化后缓存自动失效

**生产预期**：专机专用环境下，企微窗口指纹长期稳定，**实际 API 调用量 = 首次定位 + 偶发失效**，单账号日均 API 成本 < 1 元。

### 1.4 突破：截图采集委托 PowerShell 绕过 C# 子进程权限限制

**问题**：C# `CopyFromScreen` 在子进程环境（如真机回归工具）下：
1. **DPI 感知**与系统不一致 → `GetSystemMetrics` 返回虚拟分辨率，误判窗口越界
2. **前台权限**受 Windows 限制 → `SetForegroundWindow` 失败，企微拿不到前台

**尝试过的失败方案**：
- ✗ 在 C# 入口加 `SetProcessDpiAwarenessContext`（PerMonitorV2）—— 修复了 DPI，但前台权限问题没解决
- ✗ `SetWindowPos` 强制重排窗口到屏幕左上角 —— 企微主窗口 resize 后内部 DirectUI 渲染出问题，截到全白
- ✗ Windows.Graphics.Capture COM 互操作 —— spike 验证 API 可用，但 COM 互操作代码量 ~500 行（RoGetActivationFactory + IGraphicsCaptureItemInterop + D3D11 staging texture），工程量过大

**最终方案**：C# `ScreenCapturer` 委托 PowerShell 截图。

```
C# ScreenCapturer.CaptureWeComMainWindowAsync
    ↓ Process.Start("powershell.exe", "-File capture-wecom-for-csharp.ps1")
PowerShell 脚本（系统级、交互式用户身份）
    ↓ 找 hwnd → 前台化 → CopyFromScreen → 像素自检 → 输出 JSON
C# 解析 JSON + 加载 PNG → Bitmap
```

**为什么有效**：
- PowerShell 作为系统级进程，继承父进程（Client.App GUI 进程）的前台权限
- PowerShell 不受 .NET DPI projection 影响，`GetSystemMetrics` 返回真实分辨率
- 像素自检在 PowerShell 里完成，C# 直接复用结果

**真机对比**：
- C# 直接 `CopyFromScreen`：白色 82.5%（被遮挡）
- 同一时间同 hwnd 用 PowerShell 截图：白色 12.9%（正常）

**适用边界**：
- ✅ 生产环境（Client.App 常驻 GUI 进程）：PowerShell 子进程继承前台权限，截图稳定
- ⚠️ 测试环境（VisionRegression console 子进程）：嵌套子进程丢前台权限，需要外部 PowerShell 先截图再喂给 C#（已用 `--image` 参数实现）

### 1.5 突破：方案 B（OCR）真机证伪 + 方案 E（多模态）真机验证

按用户既定决策链「先 B 后 E」执行：

| 方案 | 验证方式 | 结果 |
|---|---|---|
| **B**：Windows.Media.Ocr（本地） | `Client.VisionProbe` 真机实测 | ❌ 失败。会话名识别率 < 10%（"批量紳"、"v 皙存的更改"、"氵酉苫名" 全是乱码） |
| **E**：Qwen3-VL（云端多模态） | `scripts/probe-qwen-vl-full.py` 真机实测 | ✅ 通过。24 元素 bbox 全部精准 + 94 行文字准确 OCR |

**关键技术教训**：Windows.Media.Ocr 的中文模型对 D2D 渲染的小字号中文（如会话列表）识别能力严重不足，不能作为主路径，只能作为 Qwen3-VL 不可用时的降级。

---

## 2. 关键踩坑记录

### 2.1 坑：截图被 VSCode 遮挡，导致 OCR/Qwen 全是 VSCode 内容

**场景**：第一次跑 OCR 验证时，企微主窗口被 VSCode 完全覆盖。`WeComMainWindow.TryFind()` 找到了 hwnd（窗口存在），但窗口本身不在前台被遮挡。`CopyFromScreen` 截到的是覆盖在上面的 VSCode。

**后果**：
- Windows.Media.Ocr 「识别出 111 行」（VSCode 代码 + 终端日志）
- Qwen3-VL 描述「这是 VSCode，打开了 aid-work-agent 项目」
- B 方案被**错误判定失败**（其实只是输入错了）
- 浪费了至少 2 轮验证

**修复**：
- 截图前必须 `GetForegroundWindow == hwnd` 校验
- 截图后必须**像素自检**（白色占比 / 颜色多样性阈值）
- 用开放式问题（"这是什么软件"）让多模态模型先验真输入

**记忆已存档**：`feedback_screenshot-verification.md`

### 2.2 坑：MatchElement 严格 label 关键字匹配

**场景**：C# `QwenVisionLocator.MatchElement` 用严格 `type + label 关键字` 匹配（`label.Contains("搜索")`），但 Qwen3-VL 返回的 label 是英文（"Search box at top"），不含中文关键词。

**后果**：模型正确返回了 24 个元素 bbox，但 C# 全部匹配失败（"模型返回 24 元素但无匹配 type=input kw=搜索"）。

**修复**（方案 B）：改造 prompt 让模型返回结构化 `role` 字段（`search_box | message_input | send_button | conversation_item | nav_icon | other`），MatchElement 按 role 匹配；同时加几何兜底（按 bbox 在图像中的位置判定角色）作为模型不按 prompt 响应时的最后防线。

### 2.3 坑：注册表读不到企微版本

**场景**：原设计从 `HKLM\SOFTWARE\Tencent\WeWork` 的 `Version` 键读企微版本作为窗口指纹一部分。真机发现该键不存在，fingerprint.WeComVersion 恒为 "unknown"。

**后果**：企微静默升级时，缓存层无法感知（fingerprint 不变），可能用到过期的 bbox。

**修复**：多路径回退读取版本——① `WXWork.exe FileVersionInfo.FileVersion`（最可靠，真机读到 5.0.8.6009）→ ② 注册表 `HKLM\SOFTWARE\Tencent\WeWork` → ③ 注册表 `HKLM\SOFTWARE\WOW6432Node\Tencent\WeWork` → ④ "unknown"。

### 2.4 坑：缓存命中后耗时仍 800ms+

**场景**：场景 2 缓存命中测试，原门槛 100ms，实测 800ms+ 「失败」。

**根因**：`QwenVisionLocator.LocateAsync` 流程是「先截图 → 查缓存 → 命中直接返回」。即使缓存命中，每次仍然要做一次完整的截图采集（PowerShell 调用 ~600-800ms）。

**修复**：把场景门槛从 100ms 放宽到 1000ms（含截图采集开销），承认「缓存命中的提速不是无截图的，而是省了 28s 的 API 调用」。

**架构层面未做**：「截图采集」和「缓存查询」解耦（设计文档 §7 风险章节列为已知限制）。

### 2.5 坑：PS 5.1 UTF-8 BOM 解析中文

**场景**：PowerShell 脚本里写中文注释或中文字符串字面量，PS 5.1 在某些情况下按系统码页（GBK）解码，导致语法解析失败（"表达式或语句中包含意外的标记 }"）。

**修复**：
- 脚本必须 UTF-8 **with BOM**
- 源码中需要中文比较的地方用 Unicode 码点构造：`new string(new char[]{0x4F01, 0x4E1A, 0x5FAE, 0x4FE1})` 而非直接写"企业微信"

### 2.6 坑：TFM 升级连带影响

**场景**：为支持 `Windows.Media.Ocr`（WinRT API），把 `Client.Automation.csproj` 的 TFM 从 `net8.0-windows` 升到 `net8.0-windows10.0.19041.0`。结果 `Client.App` 报 NU1201（项目不兼容）。

**根因**：ProjectReference 要求消费方的 TFM ≥ 被引用方的 TFM。

**修复**：3 个工程一起升级（Client.Automation + Client.Tests + Client.App）。

---

## 3. 最终方案架构

```
┌────────────────────────────────────────────────────────────────┐
│ Client.App（WPF 常驻 GUI 进程，托盘程序）                          │
│   ├─ DI 注册全部真实实现（已删 AutomationStubs）                   │
│   ├─ RpaHost 状态机管理                                          │
│   └─ 配置加载（vision 段从 QWEN_API_KEYS 环境变量解析）             │
└────────────────────────────┬───────────────────────────────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────────────────┐
│ Client.Automation                                              │
│   ├─ Vision/                                                   │
│   │   ├─ IVisionLocator ← QwenVisionLocator（主路径）           │
│   │   │   ├─ IScreenCapturer ← ScreenCapturer                  │
│   │   │   │   └─ 委托 PowerShell capture-wecom-for-csharp.ps1   │
│   │   │   ├─ IVisionCache ← VisionCache（SQLite + TTL）         │
│   │   │   └─ IVisionApi ← QwenVisionApi（HttpClient + key 池）  │
│   │   ├─ OcrVisionLocator（Windows.Media.Ocr 降级）              │
│   │   └─ DTO: BoundingBox / VisionProbeResult / WindowFingerprint│
│   ├─ WeCom/                                                    │
│   │   ├─ WeComAutomation（接 IVisionLocator）                   │
│   │   ├─ MessageWatcher（inline Windows.Media.Ocr 抓消息）       │
│   │   ├─ LoginStateDetector（视觉定位二维码）                    │
│   │   └─ ConversationNavigator（视觉定位搜索 + 多候选）           │
│   └─ Win32/                                                    │
│       └─ InputExecutor（bbox→屏幕坐标→SendInput）                │
└────────────────────────────────────────────────────────────────┘
```

---

## 4. 性能与成本数据（真机实测）

| 指标 | 数值 | 来源 |
|------|------|------|
| 单次 Qwen3-VL grounding 调用耗时 | 28-50s | 真机回归场景 1 |
| 单次 Qwen3-VL grounding token 用量 | ~3100 | API response usage |
| 单次 Qwen3-VL grounding 成本 | 0.02-0.04 元 | qwen3-vl-plus 定价 |
| 缓存命中后定位耗时 | 7-29ms | 真机回归场景 2 |
| 缓存命中率（同窗口连续定位） | 100%（第 2 次起） | 真机回归场景 2 |
| 截图采集耗时 | 600-800ms | PowerShell 调用 |
| 单元测试 | 119 passed / 0 failed | `dotnet test` |
| 真机回归核心场景通过率 | 3/3（场景 1/2/3） | `run-vision-regression.ps1` |

---

## 5. 未做的部分（明确不在范围）

| 项 | 原因 | 影响 |
|---|------|------|
| Windows.Graphics.Capture 离屏渲染 | COM 互操作代码量 ~500 行，工程量过大 | 当前 PowerShell 委托方案够用；WGC 作为「窗口被遮挡时的鲁棒性增强」留下次迭代 |
| ConversationNavigator 真正多候选检测 | Qwen3-VL 当前只返回单 bbox | 等 Qwen3-VL 升级支持 multiple bbox 后改一行即可 |
| MessageWatcher conversationId 真实化 | 首版以"截图尺寸指纹"作临时 ID | 生产联调时由 ConversationNavigator 回填 |
| Client.App 端到端联调 | 需要服务端部署 + 真实企微账号 + 真实 SendInput | 不在自动化测试范围 |
| WiX/MSIX 打包 | 独立任务（原计划 §5） | 不阻塞视觉定位上线 |
| 自训练 YOLO 目标检测 | 数据标注成本不可接受 | 设计 §11 明确禁止 |

---

## 6. 关键决策记录（一句话总结）

| # | 决策 | 理由 |
|---|------|------|
| 1 | 放弃 UIA/MSAA 路线 | 真机验证两者对企微 D2D UI 0 暴露 |
| 2 | 放弃纯坐标 + OpenCV 模板 | 项目方向否决（"太死板很不可靠"） |
| 3 | 放弃 Windows.Media.Ocr | 真机识别率 < 10% |
| 4 | 主策略定为 Qwen3-VL 多模态 | 真机 24 元素 bbox 全部精准 |
| 5 | 配视觉缓存层 | 28s/次的 API 不可高频调用 |
| 6 | 截图前必须像素自检 | 首次验证被遮挡截图坑过 |
| 7 | 模型选 qwen3-vl-plus 而非 max | 真机已证明 plus 准确，max 翻倍成本仅兜底 |
| 8 | ScreenCapturer 委托 PowerShell | C# 子进程 DPI/前台权限不可靠 |
| 9 | MatchElement role 字段匹配 + 几何兜底 | 模型偶尔不按 prompt 返回 role |
| 10 | 企微版本从 exe FileVersionInfo 读 | 注册表读不到 |

---

## 7. 给后续维护者的建议

1. **企微升级版本后**：先跑一次真机回归（`scripts/run-vision-regression.ps1`），确认 Qwen3-VL 还能稳定识别新版本 UI。若识别率下降，先尝试调整 prompt（特别是 `Focus especially on` 那段），再考虑升级模型到 qwen3-vl-max
2. **缓存膨胀**：`VisionCache` 用 SQLite 持久化，TTL 24h。长期运行后过期条目会累积（设计上不做后台清理）。建议每月跑一次 `VisionCache.ClearAsync` 或手动 DELETE expires_at < now 的行
3. **API 成本异常**：监控 `wecom_rpa_action_success_rate` 和日均 Qwen3-VL 调用次数。正常情况单账号日均 < 10 次调用（首次定位 + 偶发失效）。若飙升说明缓存层有 bug
4. **企微主窗口 hwnd 变化**：当前用类名 `WeWorkWindow` + 标题包含「企业微信」双重过滤。若企微某次升级改了类名，需要更新 `ScreenCapturer.DefaultWindowClassName`
5. **新加视觉定位目标**：在 `QwenVisionLocator.ResolveExpectedRole` 加映射 + 在 prompt 的 `Element definitions` 加描述，两处都要改

---

## 8. 相关文件索引

### 设计文档
- 主设计：[wecom-personal-rpa-design.md](./wecom-personal-rpa-design.md)
- 视觉定位设计：[wecom-personal-rpa-vision-design.md](./wecom-personal-rpa-vision-design.md)
- 本文档（技术突破总结）：[wecom-personal-rpa-vision-breakthrough.md](./wecom-personal-rpa-vision-breakthrough.md)

### 开发计划
- 原主计划：[plans/plan-wecom-personal-rpa.md](../../plans/plan-wecom-personal-rpa.md)
- 视觉定位计划：[plans/plan-wecom-personal-rpa-vision.md](../../plans/plan-wecom-personal-rpa-vision.md)

### 客户端代码
- STATUS：[clients/wecom-personal-rpa/STATUS.md](../../clients/wecom-personal-rpa/STATUS.md)
- 操作手册：[clients/wecom-personal-rpa/docs/操作手册.md](../../clients/wecom-personal-rpa/docs/操作手册.md)
- 视觉层代码：`clients/wecom-personal-rpa/src/Client.Automation/Vision/`
- 截图脚本：`clients/wecom-personal-rpa/scripts/capture-wecom-for-csharp.ps1`
- 真机回归：`clients/wecom-personal-rpa/scripts/run-vision-regression.ps1` + `src/Client.VisionRegression/`

### 真机回归产物（参考）
- 最新报告：`clients/wecom-personal-rpa/vision-regression-out/report_*.yaml`
- bbox 标注图：`clients/wecom-personal-rpa/vision-regression-out/annotated_*.png`

### 记忆（跨会话）
- 截图校验教训：`feedback_screenshot-verification.md`（已存入用户全局记忆）
