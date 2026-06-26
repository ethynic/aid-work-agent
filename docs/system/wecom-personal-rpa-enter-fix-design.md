# 企业微信个人账号 RPA —— Enter 方案设计（视觉定位实战修正）

> 关联：
> - 主设计：[wecom-personal-rpa-design.md](./wecom-personal-rpa-design.md) §6.2 三层自动化策略
> - 视觉定位设计：[wecom-personal-rpa-vision-design.md](./wecom-personal-rpa-vision-design.md)（已过时，被本文档修正）
> - 协议：[wecom-personal-rpa-protocol.md](./wecom-personal-rpa-protocol.md)
> - 开发计划：[plans/plan-wecom-personal-rpa-enter-fix.md](../../plans/plan-wecom-personal-rpa-enter-fix.md)
> - 调试脚本：[clients/wecom-personal-rpa/scripts/debug-navigate.ps1](../../clients/wecom-personal-rpa/scripts/debug-navigate.ps1)
> - 调试状态记录：[clients/wecom-personal-rpa/docs/status-2026-06-26.md](../../clients/wecom-personal-rpa/docs/status-2026-06-26.md)
>
> 登记位置：[docs/ideas.md](../ideas.md) #29（企业微信个人账号 RPA 接入，作为视觉定位实战修正）
>
> 创建日期：2026-06-26
> 状态：🔧 部分完成（PS 调试脚本验证完毕，C# 迁移待执行）

---

## 0. 背景与决策更新

### 0.1 视觉定位方案的真机实战失败

[wecom-personal-rpa-vision-design.md](./wecom-personal-rpa-vision-design.md) 定位方案是「Qwen3-VL 视觉定位 + 视觉缓存」，C# 客户端 `Client.Automation/Vision/QwenVisionLocator.cs` 已实现。

**2026-06-26 真机端到端调试结论**：Qwen3-VL 视觉定位在企微 5.0.8 真实 UI 上**不可用**，三个核心问题：

| 问题 | 实测现象 | 影响 |
|------|---------|------|
| **bbox 不稳定** | 同一张截图调用 qwen3-vl-plus 多次，返回的元素数量从 26 到 122 不等，bbox 坐标每次都不同 | 无法用于精确定位 |
| **bbox 偏左上** | 模型返回的 bbox（如"陆伟"=[187,72,213,86]）实际位置比真实位置偏左上约 (+40, +8)，且 bbox 宽度只覆盖文字本身而非整个 UI 元素 | 点击中心点击不到目标元素 |
| **"自报家门"误识别** | 模型返回 `conversation_name=文件传输助手` 但实际 bbox 在别的会话上 | 进入错误会话（详见 status-2026-06-26.md §现象） |

PaddleOCR layout-parsing 作为视觉定位降级方案也**不可用**：

| 问题 | 实测现象 | 影响 |
|------|---------|------|
| **不识别 UI 输入框 placeholder** | 企微顶部搜索框的"搜索"灰色 placeholder 完全识别不到，layout_det_res 把整个搜索框区域当空白忽略 | 无法定位搜索框 |
| **漏识下拉项** | 搜索下拉里的"联系人"分组下的项（特别是排在最前面的目标联系人）经常漏识，markdown.text 里完全没有该关键字 | 无法定位目标联系人 |

### 0.2 新方案：固定坐标 + Enter 键（方案 F）

经过 2026-06-26 全天调试，确定了一套**绕开视觉定位**的方案，在 PS 调试脚本里完整验证：

```
搜索联系人 → 进入会话 → 发消息 → 发图片
```

完整 7 步流程，3 个真人用户实测通过（陆伟、孙晨、芮秀），跨多种窗口尺寸/DPI 配置稳定。

**核心思想**：放弃视觉模型对动态 UI 的定位能力，改用：
1. **固定坐标**：搜索框位置在企微版本内不变，按窗口尺寸 scale 换算即可定位
2. **Enter 键**：企微搜索下拉第一个结果默认高亮，搜索框直接按 Enter 进入第一个高亮项

### 0.3 新的方案分层

```
Layer 1: 固定坐标 + Enter 键（主策略，已验证）
    - 搜索框/侧边栏图标等"位置固定"的元素 → 按窗口尺寸 scale 的固定坐标
    - 搜索结果进入会话 → 直接按 Enter（利用企微默认高亮第一项的 UI 行为）
    ↓ 失败/不可用
Layer 2: Qwen3-VL 视觉定位（降级，仅用于动态/复杂场景）
    - 仅当 Layer 1 失效时启用，用于"消息输入框"、"发送按钮"等位置依赖会话状态的元素
    - 必须配合 bbox 偏移修正（+40, +8）
    ↓ 失败
Layer 3: 人工上报 NeedsReview（最终兜底）
    - 上报"会话定位失败"事件给服务端，由用户人工绑定/确认
```

---

## 1. 设计目标与范围

### 1.1 目标

把 PS 调试脚本 `debug-navigate.ps1` 验证过的方案迁移到 C# 客户端 `Client.Automation`，让客户端能稳定完成「搜索联系人 → 进入会话 → 发送文本/图片」全流程，替代当前不可用的 Qwen3-VL 视觉定位路径。

### 1.2 范围

| 包含 | 不包含 |
|------|--------|
| `ConversationNavigator.cs` 重写为 Enter 方案 | 服务端 agent 改动（agent 不感知定位方式） |
| 搜索框固定坐标配置 + DPI scale 自动换算 | 视觉模型 API 客户端改动（保留作为 Layer 2 降级） |
| `SendMessageService.cs` 补全发送文本 + 发送图片 | 视觉缓存层（Layer 1 不需要缓存，每步都是直接计算） |
| Win32 Enter 键 + 剪贴板图片粘贴 | 多账号并发发送（后续迭代） |
| 真机回归测试脚本 | Linux/macOS 支持（项目仅 Windows） |

### 1.3 验收标准

| # | 标准 | 验证方式 |
|---|------|---------|
| 1 | 真机连续 10 次"搜索联系人 → 进入会话"成功率 ≥ 90% | 真机回归脚本 |
| 2 | 单次定位耗时 < 5s（不调外部 API，纯本地计算） | 性能测试 |
| 3 | 真机连续 10 次"发文本消息"成功率 ≥ 90% | 真机回归脚本 |
| 4 | 真机连续 10 次"发截图"成功率 ≥ 90% | 真机回归脚本 |
| 5 | 跨窗口尺寸/DPI 配置稳定（已在 1284×1392/1936×2088/1481×1245 三种尺寸验证） | 真机回归脚本 |

---

## 2. PS 调试脚本验证结果

### 2.1 完整流程（7 步）

```
Step 1: screenshot       截图企微主窗口（DPI-aware）
Step 2: locate_search    搜索框固定坐标（按窗口尺寸 scale）
Step 3: click_search     点击搜索框（DPI-aware SetCursorPos）
Step 4: type_keyword     清空搜索框（Ctrl+A Delete）+ 输入关键词（剪贴板 Ctrl+V）
Step 5: locate_result    按 Enter 进入第一个搜索结果（不调视觉模型）
Step 6: send_text        输入消息 + Enter 发送
Step 7: send_screenshot  截当前窗口到剪贴板 + Ctrl+V + Enter 发送图片
```

### 2.2 真机测试结果

| 测试 | 联系人 | 窗口尺寸 | DPI | 结果 |
|------|--------|---------|-----|------|
| 1 | 陆伟 | 1284×1392 @ (1276, 0) | 100% | ✅ 文本+截图都发送成功 |
| 2 | 孙晨 | 1284×1392 @ (1276, 0) | 100% | ✅ 文本+截图都发送成功 |
| 3 | 芮秀 | 1481×1245 @ (1317, 42) | 100% | ✅ 文本+截图都发送成功 |
| 4 | 文件传输助手（早期） | 1936×2088 @ (1903, 0) | 150% | ✅ 进入了会话（截图方案早期版本） |

**关键观察**：窗口尺寸变化（1284→1481）和位置变化（1276→1317）下方案仍然稳定 — 固定坐标 scale 换算 + Enter 方案是 robust 的。

### 2.3 失败方案的实测对比

| 方案 | 失败原因 | 实测证据 |
|------|---------|---------|
| Qwen3-VL 视觉定位搜索框 | bbox 偏左上 + 不稳定 | 同图调用 5 次，bbox 在 (154,21) ~ (190,72) 之间漂移，且都比真实位置偏左上 |
| Qwen3-VL 视觉定位下拉项 | bbox 偏左上约 (+40, +8) | 模型给"陆伟" bbox=[187,72,213,86]，实际位置在 (230, 80) 附近 |
| PaddleOCR layout-parsing 识别搜索框 | 不识别 UI 输入框 placeholder | markdown.text 里完全没有"搜索"字样 |
| PaddleOCR layout-parsing 识别下拉项 | 漏识"联系人"分组下的目标联系人 | 搜索"陆伟"时 markdown 里完全没有"陆伟"，但下拉视觉上清楚显示 |

---

## 3. 核心技术决策

### 3.1 搜索框定位：固定坐标 + DPI scale 自动换算

**基准坐标**（基于真机校准，参考基准 1936×2088 DPI=150%）：
```
search_box_bbox = [330, 34, 430, 66]   // 中心 (380, 50)
```

**scale 换算**：
```csharp
double scale = (double)windowWidth / 1936.0;
var searchBox = new BoundingBox(
    (int)Math.Round(330 * scale),
    (int)Math.Round(34 * scale),
    (int)Math.Round(430 * scale),
    (int)Math.Round(66 * scale)
);
```

**为什么有效**：
- 企微 5.0.8 的侧边栏宽度（左侧导航 ~100px）是固定的，不随窗口尺寸变化
- 搜索框在侧边栏右侧的固定位置，相对窗口左上角的偏移与窗口尺寸成比例
- DPI 变化时窗口整体缩放，相对位置不变

**已验证的尺寸范围**：1284×1392（DPI 100%）~ 1936×2088（DPI 150%）

**配置项**（写入 `client.example.yaml`）：
```yaml
automation:
  search_box_bbox_base: [330, 34, 430, 66]   # 基准 bbox（DPI=150% 1936×2088 下校准）
  search_box_base_width: 1936                 # 基准窗口宽度
```

### 3.2 进入会话：Enter 键

**实现**：
```csharp
// Step 5: 输入关键词后直接按 Enter，企微搜索下拉第一个结果默认高亮
SendInput.PressKey(VK_RETURN);  // 0x0D
Thread.Sleep(2500);  // 等企微切换会话视图 + 渲染输入框
```

**为什么有效**：
- 企微 5.0.8 搜索下拉打开后，第一个结果（通常是"联系人"分类下的精确匹配）默认高亮
- 搜索框聚焦时按 Enter，企微直接进入高亮项的会话
- 跳过了视觉定位下拉项的所有难题（OCR 漏识、bbox 偏移）

**风险**：
- 如果企微搜索结果顺序变化（如把"聊天记录"放前面），Enter 会进错会话
- 当前 5.0.8 版本工作正常；未来企微升级需要重新验证

**降级方案**：如果 Enter 后进入的会话名字不对（通过 MessageWatcher 检测会话标题验证），fallback 到 Layer 2 视觉定位 + bbox 偏移修正。

### 3.3 DPI Awareness

**问题**：C# 进程默认不是 per-monitor DPI-aware，`SetCursorPos` 用虚拟坐标系（基于 96 DPI），但企微窗口在物理坐标系，导致点击位置错位。

**解决**：在 `Client.App` 启动早期调用：
```csharp
[DllImport("user32.dll")]
static extern bool SetProcessDpiAwarenessContext(IntPtr value);

// PER_MONITOR_AWARE_V2 = -4
SetProcessDpiAwarenessContext(new IntPtr(-4));
```

**已经在 PS 脚本验证**：`capture-wecom-for-csharp.ps1` 加了 DPI awareness 后，`GetWindowRect` 返回物理坐标（1936×2088 而非虚拟坐标 1291×1392），`SetCursorPos` 点击精准。

### 3.4 文本输入：剪贴板 + Ctrl+V

**问题**：`SendInput` 对中文输入法不友好，`VkKeyScanW` 无法直接发送中文字符。

**解决**：
```csharp
// 1. 把文本放到剪贴板
Clipboard.SetText(text);
// 2. 发 Ctrl+V
SendInput.PressKey(VK_CONTROL);
SendInput.PressKey(VK_V);  // 0x56
SendInput.ReleaseKey(VK_V);
SendInput.ReleaseKey(VK_CONTROL);
```

**已在 PS 脚本验证**：`Type-Text` 函数稳定工作，"陆伟"、"孙晨"、"芮秀"、"这是通过爱定义的rpa发送的一条消息..." 全部正确输入。

### 3.5 图片发送：截屏到剪贴板 + Ctrl+V + Enter

**实现**：
```csharp
// 1. 截当前企微窗口到 Bitmap（用 CopyFromScreen）
using var bmp = new Bitmap(width, height);
using var g = Graphics.FromImage(bmp);
g.CopyFromScreen(windowLeft, windowTop, 0, 0, new Size(width, height));

// 2. 放到剪贴板
Clipboard.SetImage(bmp);

// 3. Ctrl+V（企微弹出图片预览对话框）
SendInput.PressKey(VK_CONTROL);
SendInput.PressKey(VK_V);
SendInput.ReleaseKey(VK_V);
SendInput.ReleaseKey(VK_CONTROL);
Thread.Sleep(1500);  // 等图片预览对话框弹出

// 4. Enter 发送
SendInput.PressKey(VK_RETURN);
```

**已在 PS 脚本验证**：1284×1392、1481×1245 两种尺寸截图都能成功发送。

---

## 4. C# 客户端改造范围

### 4.1 `ConversationNavigator.cs` 重写

**当前实现**（基于 Qwen3-VL 视觉定位）：
```csharp
public ConversationNavigateResult Navigate(string keyword)
{
    var origin = ResolveWindowOrigin();
    var searchProbe = _visionLocator.LocateAsync("input", "搜索").GetAwaiter().GetResult();
    _inputExecutor.ClickElement(searchProbe.Bbox, origin.Value);
    Win32Input.SelectAllAndDelete();
    _inputExecutor.TypeText(keyword);
    var listProbe = _visionLocator.LocateAsync("list_item", keyword).GetAwaiter().GetResult();
    _inputExecutor.ClickElement(listProbe.Bbox, origin.Value);
    // ...
}
```

**重写为 Enter 方案**：
```csharp
public ConversationNavigateResult Navigate(string keyword)
{
    var origin = ResolveWindowOrigin();
    if (origin is null) return ErrorResult("automation_layer_error");

    // Step 2: 计算搜索框固定 bbox（按窗口尺寸 scale）
    double scale = (double)origin.Value.Width / _config.SearchBoxBaseWidth;
    var searchBox = ScaleBbox(_config.SearchBoxBboxBase, scale);

    // Step 3: 点击搜索框
    _inputExecutor.ClickElement(searchBox, origin.Value);
    Thread.Sleep(400);

    // Step 4: 清空 + 输入关键词
    Win32Input.SelectAllAndDelete();
    Thread.Sleep(150);
    _inputExecutor.TypeText(keyword, pressEnterAfter: false);
    Thread.Sleep(1500);

    // Step 5: 按 Enter 进入第一个搜索结果
    Win32Input.PressKey(VK_RETURN);
    Thread.Sleep(2500);

    // 验证：通过 MessageWatcher 检测会话标题是否匹配
    // （如果失败，返回 NeedsReview 让上层 fallback）

    return new ConversationNavigateResult { Success = true, ... };
}
```

**移除依赖**：`IVisionLocator`（保留作为 Layer 2 降级入口，但 Navigate 主路径不再调用）。

### 4.2 `SendMessageService.cs` 补全

**当前实现**：只有发文本消息，没有发图片。

**新增 SendScreenshotAsync**：
```csharp
public async Task<SendResult> SendScreenshotAsync(IntPtr windowHandle)
{
    // 1. 截图到剪贴板
    var (left, top, width, height) = Win32.GetWindowRect(windowHandle);
    using var bmp = new Bitmap(width, height);
    using var g = Graphics.FromImage(bmp);
    g.CopyFromScreen(left, top, 0, 0, new Size(width, height));
    Clipboard.SetImage(bmp);
    Thread.Sleep(300);

    // 2. Ctrl+V 触发企微图片预览
    Win32Input.PressKey(VK_CONTROL);
    Win32Input.PressKey(VK_V);
    Win32Input.ReleaseKey(VK_V);
    Win32Input.ReleaseKey(VK_CONTROL);
    Thread.Sleep(1500);

    // 3. Enter 发送
    Win32Input.PressKey(VK_RETURN);
    Thread.Sleep(800);

    return SendResult.Success();
}
```

**新增 SendTextAsync**（基于已存在的发送逻辑封装）：
```csharp
public async Task<SendResult> SendTextAsync(string text)
{
    // 复用现有 Type-Text + Enter 逻辑
    _inputExecutor.TypeText(text, pressEnterAfter: true);
    Thread.Sleep(500);
    return SendResult.Success();
}
```

### 4.3 配置项扩展

`client.example.yaml` 新增 `automation` 段：
```yaml
automation:
  # 搜索框固定 bbox（基准：DPI=150% 1936×2088 校准，运行时按窗口尺寸 scale）
  search_box_bbox_base: [330, 34, 430, 66]
  search_box_base_width: 1936
  
  # 各步骤的等待时间（毫秒）
  wait_after_click_search_ms: 400
  wait_after_type_keyword_ms: 1500
  wait_after_enter_ms: 2500
  wait_after_send_text_ms: 500
  wait_after_paste_image_ms: 1500
  wait_after_send_image_ms: 800
```

### 4.4 DPI Awareness 启动注入

`Client.App/Program.cs` 在 `Add-Type`/初始化 Win32 之前：
```csharp
// 必须在第一次调用 user32 API 之前设置
SetProcessDpiAwarenessContext(new IntPtr(-4));  // PER_MONITOR_AWARE_V2
```

或通过 app.manifest 声明（更稳定）：
```xml
<application xmlns="urn:schemas-microsoft-com:asm.v3">
  <windowsSettings>
    <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>
    <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2</dpiAwareness>
  </windowsSettings>
</application>
```

### 4.5 Layer 2 降级：Qwen3-VL + bbox 偏移修正

保留 `QwenVisionLocator`，但在 `MatchElement` 后加 bbox 偏移修正：
```csharp
if (hit is not null)
{
    // bbox 偏移修正：qwen3-vl-plus 实测偏左上约 (+40, +8)
    hit.Bbox = new BoundingBox(
        hit.Bbox.X1 + 40,
        hit.Bbox.Y1 + 8,
        hit.Bbox.X2 + 40,  // 不向右扩展（用户场景测试时未做扩展）
        hit.Bbox.Y2 + 8
    );
}
```

---

## 5. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 企微升级后搜索框位置变化 | 搜索框点击不准 | 配置化 `search_box_bbox_base`，升级后更新配置即可，不需要改代码 |
| 企微升级后 Enter 进入的不是第一个联系人 | 进错会话 | 加 `MessageWatcher` 验证会话标题，不匹配时 fallback Layer 2 |
| 用户调整了企微侧边栏宽度 | 搜索框 X 坐标偏移 | 配置项可调；长期看需要 Layer 2 兜底 |
| 多显示器不同 DPI | 主窗口跨屏时点击错位 | `SetProcessDpiAwarenessContext(PerMonitorV2)` 已解决 |
| 剪贴板被其他程序占用 | Ctrl+V 粘贴失败 | 加剪贴板重试（最多 3 次，每次间隔 100ms） |

---

## 6. 与服务端协议的关系

Enter 方案**完全不改变**客户端↔服务端的消息协议（[wecom-personal-rpa-protocol.md](./wecom-personal-rpa-protocol.md)）。服务端下发的 `send_message` action 仍然是：
```json
{
  "action": "send_message",
  "conversation_keyword": "陆伟",
  "content": "你好啊！",
  "attachments": [...]
}
```

客户端内部用 Enter 方案实现"conversation_keyword → 会话"的定位，对服务端透明。

---

## 7. 不在本设计范围内的事

| 事项 | 原因 |
|------|------|
| 多账号并发发送 | 当前单账号验证完毕，多账号是下一阶段 |
| 消息撤回 / 编辑 | 企微不支持 RPA 撤回，超出范围 |
| 群聊 @ 提及 | 群聊场景需要单独设计 |
| 文件发送（非截图） | 后续迭代，原理同截图（剪贴板 + Ctrl+V） |
| Linux/macOS 支持 | 项目仅 Windows，不考虑 |

---

## 附录 A：调试脚本命令对照

```bash
# Step 1: 截图企微主窗口
pwsh scripts/debug-navigate.ps1 -Step screenshot

# Step 2: 搜索框固定坐标 + 标注图
pwsh scripts/debug-navigate.ps1 -Step locate_search

# Step 3: 点击搜索框
pwsh scripts/debug-navigate.ps1 -Step click_search

# Step 4: 清空 + 输入关键词（默认从 keywords.txt 读，可用 -Keyword 覆盖）
pwsh scripts/debug-navigate.ps1 -Step type_keyword
pwsh scripts/debug-navigate.ps1 -Step type_keyword -Keyword "陆伟"

# Step 5: Enter 进入会话
pwsh scripts/debug-navigate.ps1 -Step locate_result

# Step 6: 输入消息 + Enter 发送
pwsh scripts/debug-navigate.ps1 -Step send_text

# Step 7: 截图 + Ctrl+V + Enter 发送图片
pwsh scripts/debug-navigate.ps1 -Step send_screenshot
```

## 附录 B：调试产物路径

```
clients/wecom-personal-rpa/
├── scripts/
│   ├── debug-navigate.ps1            # 主调试脚本（7 个 Step）
│   ├── capture-wecom-for-csharp.ps1  # 底层截图脚本（DPI-aware）
│   ├── probe-paddleocr.ps1           # PaddleOCR 探针脚本
│   └── prompts/
│       ├── keywords.txt              # 中文字符串字典（避免 PS 5.1 GBK 解析问题）
│       ├── locate_search_box.txt     # Step 2 弃用 prompt（保留作历史）
│       └── locate_search_result_vl.txt  # Step 5 弃用 prompt（保留作历史）
└── debug-out/                         # 调试产物
    ├── debug-state.json              # 跨步骤状态
    ├── cap_<timestamp>/              # 各次截图
    ├── step2_annotated_*.png         # Step 2 标注图
    ├── paddleocr_step*.json          # PaddleOCR 响应（参考）
    └── qwen_step*.json               # qwen 响应（参考）
```
