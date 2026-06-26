# 开发计划 —— 企业微信个人账号 RPA Enter 方案 C# 迁移

> 关联：
> - 设计文档：[docs/system/wecom-personal-rpa-enter-fix-design.md](../docs/system/wecom-personal-rpa-enter-fix-design.md)
> - 主计划：[plans/plan-wecom-personal-rpa.md](./plan-wecom-personal-rpa.md)
> - 调试脚本：[clients/wecom-personal-rpa/scripts/debug-navigate.ps1](../clients/wecom-personal-rpa/scripts/debug-navigate.ps1)
> - 调试状态：[clients/wecom-personal-rpa/docs/status-2026-06-26.md](../clients/wecom-personal-rpa/docs/status-2026-06-26.md)
>
> 登记位置：[docs/ideas.md](../docs/ideas.md) #29
>
> 创建日期：2026-06-26
> 状态：🔧 开发中（计划已制定，待执行）

---

## 0. 总览

把 PS 调试脚本 `debug-navigate.ps1` 验证过的 Enter 方案迁移到 C# 客户端 `Client.Automation`，分 5 个阶段：

| 阶段 | 内容 | 工时估 | 验收 |
|------|------|--------|------|
| 1 | ConversationNavigator 重写为 Enter 方案 | 2h | 单元测试通过 + 真机回归（搜索 → 进入会话）3/3 |
| 2 | SendMessageService 补全发文本 + 发图片 | 1.5h | 单元测试通过 + 真机回归（发文本 + 发截图）3/3 |
| 3 | 配置项 + DPI awareness 注入 | 1h | 配置文件加载测试通过 |
| 4 | Layer 2 降级（Qwen3-VL bbox 偏移修正）保留 + 测试 | 1h | 单元测试通过 |
| 5 | 端到端真机回归 + 文档更新 | 1.5h | 7/7 步全流程通过 + ideas.md 状态更新 |

**总工时**：约 7 小时

**依赖**：无（独立改造，不依赖其他进行中的工作）

---

## 阶段 1：ConversationNavigator 重写为 Enter 方案

### 1.1 目标

把 `clients/wecom-personal-rpa/src/Client.Automation/WeCom/ConversationNavigator.cs` 从「Qwen3-VL 视觉定位」改为「固定坐标 + Enter」。

### 1.2 任务清单

- [ ] **1.2.1** 新增 `AutomationConfig` 配置类
  - 字段：`SearchBoxBboxBase`（int[4]，默认 [330, 34, 430, 66]）
  - 字段：`SearchBoxBaseWidth`（int，默认 1936）
  - 字段：`WaitAfterClickSearchMs`、`WaitAfterTypeKeywordMs`、`WaitAfterEnterMs`（默认 400/1500/2500）
  - 文件：`Client.Automation/Configuration/AutomationConfig.cs`

- [ ] **1.2.2** 重写 `ConversationNavigator.Navigate`
  - 移除：`_visionLocator.LocateAsync("input", "搜索")` 调用
  - 新增：按 `AutomationConfig.SearchBoxBboxBase` + `windowWidth / SearchBoxBaseWidth` scale 计算搜索框 bbox
  - 移除：`_visionLocator.LocateAsync("list_item", keyword)` 调用
  - 新增：调 `Win32Input.PressKey(VK_RETURN)` + 等 `WaitAfterEnterMs`
  - 保留：`_inputExecutor.ClickElement` / `Win32Input.SelectAllAndDelete` / `_inputExecutor.TypeText`

- [ ] **1.2.3** 修改构造函数依赖
  - 移除：`IVisionLocator visionLocator` 参数（保留但标 `[Optional]`，Layer 2 降级用）
  - 新增：`AutomationConfig automationConfig` 参数

- [ ] **1.2.4** 写单元测试 `ConversationNavigatorTests`
  - 测试用例：
    - `Navigate_ValidKeyword_ReturnsSuccess`
    - `Navigate_WindowNotReady_ReturnsError`
    - `Navigate_DpiScale_计算正确 bbox`（验证 1284 宽度时 bbox=[219, 23, 285, 44]）
  - Mock：`InputExecutor` / `Win32Input` / 窗口 origin 解析

### 1.3 验收

- [ ] 所有单元测试通过
- [ ] 真机回归脚本跑 3 次"搜索联系人 → 进入会话"，3/3 成功（陆伟/孙晨/芮秀各 1 次）

---

## 阶段 2：SendMessageService 补全

### 2.1 目标

补全 `SendMessageService.cs` 的发文本消息（封装）和发截图能力。

### 2.2 任务清单

- [ ] **2.2.1** 新增 `SendTextAsync(string text)` 方法
  - 复用现有 `_inputExecutor.TypeText(text, pressEnterAfter: true)` 逻辑
  - 加等待时间配置化
  - 返回 `SendResult`

- [ ] **2.2.2** 新增 `SendScreenshotAsync(IntPtr windowHandle)` 方法
  - 实现：
    1. 用 `Win32.GetWindowRect` 取窗口位置
    2. 用 `Bitmap + Graphics.CopyFromScreen` 截图
    3. 用 `Clipboard.SetImage` 放到剪贴板
    4. 发 `Ctrl+V`（`Win32Input` 已有）
    5. 等 `WaitAfterPasteImageMs`
    6. 发 `Enter`
  - 加剪贴板重试（最多 3 次，每次间隔 100ms）

- [ ] **2.2.3** 写单元测试 `SendMessageServiceTests`
  - 测试用例：
    - `SendTextAsync_ValidText_CallsTypeTextWithEnter`
    - `SendScreenshotAsync_ValidWindow_CapturesAndPastes`
    - `SendScreenshotAsync_ClipboardConflict_RetriesUpTo3Times`

### 2.3 验收

- [ ] 所有单元测试通过
- [ ] 真机回归：进入会话后发"你好啊！" → 发截图，连续 3 次成功

---

## 阶段 3：配置项 + DPI awareness 注入

### 3.1 目标

让 C# 客户端启动时 DPI-aware，并加载 `automation` 配置段。

### 3.2 任务清单

- [ ] **3.2.1** 修改 `Client.App/Program.cs`
  - 在 `Add-Type`/Win32 初始化**之前**调 `SetProcessDpiAwarenessContext(new IntPtr(-4))`
  - 失败时 fallback 到 `SetProcessDPIAware()`
  - 加日志记录设置结果

- [ ] **3.2.2** 扩展 `client.example.yaml`
  - 新增 `automation` 段（参考设计文档 §4.3）

- [ ] **3.2.3** 修改 `ClientOptionsLoader`（或类似配置加载类）
  - 解析 `automation` 段到 `AutomationConfig` 对象
  - 注入到 DI 容器供 `ConversationNavigator` 使用

- [ ] **3.2.4** 更新操作手册 `操作手册.md`
  - 新增"automation 段配置说明"
  - 说明 DPI awareness 是自动设置的，不需要用户配置

### 3.3 验收

- [ ] 客户端启动后日志包含 "DPI awareness: PER_MONITOR_AWARE_V2"
- [ ] 配置项可被 ConversationNavigator 正确读取
- [ ] 操作手册更新

---

## 阶段 4：Layer 2 降级保留

### 4.1 目标

保留 `QwenVisionLocator` 作为 Layer 2 降级（Layer 1 Enter 方案失败时使用），但加 bbox 偏移修正。

### 4.2 任务清单

- [ ] **4.2.1** 修改 `QwenVisionLocator.MatchElement`
  - 在返回 hit 之前对 bbox 加偏移 (+40, +8)（基于真机实测）
  - 加注释说明偏移来源

- [ ] **4.2.2** 修改 `ConversationNavigator.Navigate`
  - Layer 1 失败时（如 MessageWatcher 检测到会话标题不匹配），fallback 调 `_visionLocator.LocateAsync`
  - 加日志记录降级触发

- [ ] **4.2.3** 写单元测试
  - 测试用例：`Navigate_Layer1Fails_FallsBackToLayer2`

### 4.3 验收

- [ ] 单元测试通过
- [ ] Layer 2 降级在真机上能 work（手动构造 Layer 1 失败场景）

---

## 阶段 5：端到端真机回归 + 文档更新

### 5.1 目标

完整跑一遍 7 步流程真机回归，确认 C# 实现和 PS 脚本行为一致；更新所有相关文档。

### 5.2 任务清单

- [ ] **5.2.1** 写真机回归脚本 `scripts/run-enter-fix-regression.ps1`
  - 接受 `-Keyword`、`-Message` 参数
  - 串起 7 步流程
  - 每步打印 PASS/FAIL
  - 失败时 dump 当前截图 + 日志

- [ ] **5.2.2** 跑真机回归 10 次
  - 测试联系人：陆伟、孙晨、芮秀 + 7 个其他联系人
  - 验收：成功率 ≥ 90%

- [ ] **5.2.3** 更新 `clients/wecom-personal-rpa/STATUS.md`
  - 新增 Enter 方案章节
  - 标注旧的 Qwen3-VL 视觉定位方案为"Layer 2 降级"

- [ ] **5.2.4** 更新 `clients/wecom-personal-rpa/docs/status-2026-06-26.md`
  - 标记所有阻塞问题已解决
  - 新增"最终方案"章节链接到设计文档

- [ ] **5.2.5** 更新 `docs/ideas.md` #29
  - 状态保持 🔧 部分完成
  - 补充"客户端 Enter 方案已落地（2026-06-26）"
  - 关联设计/计划文档链接

- [ ] **5.2.6** 标记视觉定位设计文档为"已修正"
  - `docs/system/wecom-personal-rpa-vision-design.md` 顶部加"已被 [wecom-personal-rpa-enter-fix-design.md](./wecom-personal-rpa-enter-fix-design.md) 修正"banner

### 5.3 验收

- [ ] 10 次真机回归成功率 ≥ 90%
- [ ] 所有文档更新完成
- [ ] ideas.md 状态正确

---

## 风险与依赖

### 风险

| 风险 | 阶段 | 缓解 |
|------|------|------|
| 企微升级导致 Enter 方案失效 | 5.2.2 | 加 MessageWatcher 验证 + Layer 2 降级 |
| C# DPI awareness 设置失败 | 3.2.1 | fallback 到 `SetProcessDPIAware` + 日志警告 |
| 剪贴板冲突导致图片发送失败 | 2.2.2 | 重试 3 次 |
| 真机回归失败率 > 10% | 5.2.2 | 暂停发布，回到 PS 脚本排查 |

### 依赖

- 无外部依赖（不依赖其他进行中的工作）
- 测试需要：企微 5.0.8+、Windows 10/11、DPI 100% 或 150%

---

## 不在本计划范围内的事

| 事项 | 原因 | 后续 |
|------|------|------|
| 多账号并发发送 | 当前单账号 | 下一阶段设计 |
| 服务端协议改动 | Enter 方案对服务端透明 | 不涉及 |
| 群聊支持 | 群聊场景需要单独设计 | 后续迭代 |
| 文件发送（非截图） | 同剪贴板粘贴原理 | 后续迭代 |

---

## 附录：阶段间依赖关系

```
阶段 1 (ConversationNavigator)
   ↓
阶段 2 (SendMessageService)  ← 可与阶段 1 并行
   ↓
阶段 3 (配置项 + DPI)         ← 依赖阶段 1+2 的代码
   ↓
阶段 4 (Layer 2 降级)         ← 依赖阶段 1 的 Navigate 重构
   ↓
阶段 5 (真机回归 + 文档)       ← 依赖所有前序阶段完成
```

阶段 1 和阶段 2 可以并行做，但都依赖阶段 3 的配置项；阶段 4 依赖阶段 1 的 Navigate 重构完成；阶段 5 必须最后做。
