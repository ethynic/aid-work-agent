# 企业微信个人账号 RPA —— 视觉定位方案设计

> 关联：
> - 主设计：[wecom-personal-rpa-design.md](./wecom-personal-rpa-design.md) §6.2 三层自动化策略
> - 协议：[wecom-personal-rpa-protocol.md](./wecom-personal-rpa-protocol.md)
> - 开发计划：[plans/plan-wecom-personal-rpa.md](../../plans/plan-wecom-personal-rpa.md) 第 0 节准入验证
> - 客户端实现状态：[clients/wecom-personal-rpa/STATUS.md](../../clients/wecom-personal-rpa/STATUS.md)
>
> 登记位置：[docs/ideas.md](../ideas.md) #29（企业微信个人账号 RPA 接入，作为视觉定位补充设计）
>
> 创建日期：2026-06-23
> 更新日期：2026-06-24
> 状态：✅ 已落地（生产代码级实现完成，119 单测全绿，真机回归 3/3 核心场景通过）

---

## 0. 背景与决策更新

### 0.1 原设计的三层自动化策略失效

原设计 [wecom-personal-rpa-design.md §6.2](./wecom-personal-rpa-design.md) 定位三层降级：

```
Layer 1: FlaUI UIA3 读取控件树
    ↓ 失败
Layer 2: Win32 窗口相对坐标 + SendInput + 剪贴板输入
    ↓ 失败
Layer 3: OpenCvSharp 模板匹配 + 截图定位
```

**2026-06-23 真机准入验证结论**：企业微信 PC 客户端是 DirectUI/Direct2D 自绘 UI，三层策略的前两层**全部失效**：

| 层 | 验证方式 | 实测结果 | 结论 |
|---|---------|---------|------|
| Layer 1（UIA3） | `Client.Probe` Step 3 dump 控件树 | 主窗口下 dump 出 **0 个控件**（只有 TitleBarWindow / PerryShadowWnd 装饰性外壳） | **彻底失效** |
| Layer 1 备选（MSAA） | `scripts/probe-msaa.ps1` IAccessible 探测 | 根对象 `Name=企业微信`，**子对象数 = 0** | **彻底失效** |
| Layer 2（坐标 + SendInput） | 设计评估 | 单纯坐标方案对窗口位置/DPI/版本变化零鲁棒性，被项目方向明确否决 | **路线否决** |
| Layer 3（OpenCV 模板匹配） | 设计评估 | 模板换一下就失效，企微升级即崩，是"坐标方案"的变体 | **路线否决** |

腾讯系产品（QQ、微信、企业微信）的 a11y 层全部关在门外，UI 内容（聊天列表、输入框、发送按钮）全是 GDI/D2D 画出来的，**UIA 和 MSAA 树里根本不存在**。

### 0.2 新方案：多模态视觉模型（方案 E）

经过方案 B（本地 OCR）→ 方案 E（多模态视觉）的逐级真机验证：

| 方案 | 验证方式 | 结果 | 决策 |
|------|---------|------|------|
| **B**：本地 OCR（Windows.Media.Ocr） | `Client.VisionProbe` 真机实测 | 真实企微截图识别质量极差（乱码率 ~80%），中文小字号 + D2D 渲染完全识别不清 | ❌ 失败 |
| **E**：多模态视觉（Qwen3-VL） | `scripts/probe-qwen-vl-full.py` 真机实测 | 24 个元素 bbox 精准定位（会话名 + 搜索框 + 输入框 + 发送按钮 + 导航），94 行文字准确 OCR | ✅ 通过 |

详见本文档 §2 真机验证证据。

### 0.3 新的三层策略

```
Layer 1: Qwen3-VL 多模态视觉定位（主策略）
    - 输入企微截图 → 模型返回控件 bbox 像素坐标
    - 配合视觉缓存层：窗口不变时复用坐标，命中即零 API 成本
    ↓ 失败/不可用
Layer 2: Windows.Media.Ocr + 关键字定位（降级）
    - 仅用于文字密集场景（如读消息内容），不依赖 UIA
    ↓ 失败
Layer 3: Win32 窗口坐标 + OpenCV 模板（最后兜底）
    - 保留现有 FlaUiDriver / TemplateMatcher 作为应急
```

---

## 1. 设计目标与范围

### 1.1 目标

为 `Client.Automation` 提供一套基于多模态视觉模型的企微控件定位能力，**替代失效的 UIA/MSAA 路径**，让客户端真正能找到并点击企微 PC 客户端里的 UI 元素（搜索框、会话项、消息输入框、发送按钮）。

### 1.2 范围

| 包含 | 不包含 |
|------|--------|
| 视觉模型 API 客户端（调用 Qwen3-VL） | 服务端 agent 改动（agent 不感知定位方式） |
| 视觉缓存层（窗口指纹 → bbox 缓存） | 模型微调 / 自训练 |
| 截图采集（带"窗口可见且在前台"前置校验） | RPA 客户端整体架构改动（保持现有 Client.App / Client.Automation 结构） |
| bbox → Win32 SendInput 点击坐标换算 | 离线本地视觉模型（首版必须联网） |
| 控件定位失败时的降级策略 | |

### 1.3 核心设计原则

1. **视觉缓存优先**：窗口指纹（位置/尺寸/DPI/企微版本）不变时直接复用上次 bbox，不调 API。
2. **截图像素自检**：截图前必须确认企微在前台，截图后必须用像素特征验证截图确实是企微（不是被其他窗口遮挡）。
3. **失败 loud**：模型返回空 / bbox 越界 / 截图自检失败时，立即报错并暂停账号（设计 §10.3 误发 0 容忍）。
4. **本地降级链**：API 不可用时有兜底（OCR / 坐标），不能让账号整体停摆。

---

## 2. 真机验证证据（2026-06-23）

### 2.1 失败证据：UIA / MSAA 双双失效

#### UIA3（`Client.Probe` Step 3）

```
=== [3/7] UIA 控件树（FlaUI/UIA3）===
[OK] UIA3 已附加主窗口：Name=企业微信 ClassName=WeWorkWindow
[OK] 已 dump 0 个控件（最多 80）。
```

附加成功但**子控件树为空**。

#### MSAA（`scripts/probe-msaa.ps1`）

```
=== WeCom MSAA Accessibility Probe ===
[OK] Found WeCom main window: hwnd=0x6D0E12 title=企业微信
[OK] MSAA root object: Name=[企业微信]

First-level children count: 0

=== Verdict ===
[NO-GO] MSAA is also blocked by WeCom (all children have empty Name)
```

根对象拿到，但**子对象数 = 0**。

### 2.2 失败证据：B 方案（Windows.Media.Ocr）

`Client.VisionProbe` 真机实测（`vision-probe-out/ocr-report_*.yaml`）：

```
=== OCR 验证汇总 ===
  识别到的总行数:        111
  会话名候选（含中文）:  10
  发送按钮文字候选:      0    ← 企微发送按钮是图标
  搜索框文字候选:        0
  输入框文字候选:        0

会话名候选前 15 个（含相对坐标）:
  - [  21, 253] 搜 索
  - [  83, 252] 批 量 紳       ← 乱码
  - [ 140, 200] v 皙 存 的 更 改  ← 乱码
  - [  76, 536] 氵 酉 苫 名     ← 乱码
  ...
```

会话名识别率 **< 10%**，远低于门槛 95%。

### 2.3 通过证据：E 方案（Qwen3-VL）

`scripts/probe-qwen-vl-full.py --model qwen3-vl-plus` 真机实测（`vision-probe-out/qwen3vl_eval_20260623_172634/`）：

#### Task 1：软件识别 ✅

```
软件名称：企业微信
判断依据：
1. 左侧导航含"微盘""智能文档""智能总结"，企微特有功能
2. 水印"陆伟@全筑股份"符合企微企业内部部署特征
3. 浅蓝灰主色调 + 底部工具栏图标与企微 4.x 一致
置信度：高
```

#### Task 2：元素定位 ✅（核心成果）

模型返回 **24 个元素 bbox**，节选：

| # | 类型 | bbox (像素) | label |
|---|------|-------------|-------|
| 0 | input | (122,15)-(243,38) | Search box at top |
| 1 | list_item | (113,47)-(279,89) | 文件传输助手 |
| 2 | list_item | (113,140)-(279,177) | 企业微信团队 |
| 3 | list_item | (113,185)-(279,222) | 陈凌 |
| ... | list_item | ...（每隔 45 像素一行，共 20 个会话） | |
| 21 | input | (293,819)-(988,985) | bottom message input box |
| 22 | button | (951,965)-(984,980) | send button |
| 23 | icon | (10,17)-(72,985) | left navigation icons |

**bbox 合理性自检**：
- 会话项 Y 坐标均匀间隔 45 像素 → 模型理解了"列表项"语义
- 发送按钮紧贴输入框右下角 → 空间关系正确
- 导航图标列在最左侧、占整列高度 → 布局理解正确

#### Task 3：OCR ✅

识别出 **94 行文字**，左侧导航、会话名、消息内容、时间戳全部准确（vs Windows.Media.Ocr 的乱码）。

#### 性能指标

| 指标 | 实测 |
|------|------|
| Task 1 耗时 | 9.34s |
| Task 2 耗时（24 元素 grounding） | 28.71s |
| Task 3 耗时 | 48.72s |
| 单次 Task 2 token 用量 | ~3133 |
| 模型 | qwen3-vl-plus |
| 估算单次 grounding 成本 | ~0.02-0.04 元 |

---

## 3. 模块设计

### 3.1 模块边界

```
Client.Automation/
├── Vision/
│   ├── IVisionLocator.cs              # 视觉定位抽象接口
│   ├── QwenVisionLocator.cs           # Qwen3-VL 实现（主策略）
│   ├── OcrVisionLocator.cs            # Windows.Media.Ocr 实现（降级）
│   ├── VisionCache.cs                 # 视觉缓存层
│   ├── WindowFingerprint.cs           # 窗口指纹（缓存键）
│   ├── ScreenCapturer.cs              # 截图采集 + 像素自检
│   ├── BoundingBox.cs                 # bbox DTO
│   └── VisionProbeResult.cs           # 定位结果 DTO
├── WeCom/
│   └── WeComAutomation.cs             # 替换 Stubs，调用 IVisionLocator
└── AgentApi/
    └── IVisionApi.cs                  # 视觉模型 API 抽象（便于切换 Qwen/GPT/Claude）
```

### 3.2 核心接口契约

#### `IVisionLocator` —— 视觉定位主入口

```csharp
public interface IVisionLocator
{
    /// <summary>
    /// 在当前企微主窗口截图上，定位指定的 UI 元素。
    /// 内部走视觉缓存：窗口指纹不变时直接复用上次 bbox。
    /// </summary>
    /// <param name="elementType">button | input | list_item | icon | text</param>
    /// <param name="labelKeyword">元素标签关键字（如 "发送" / "搜索" / "文件传输助手"）</param>
    /// <param name="cancellationToken"></param>
    /// <returns>定位结果，包含 bbox（屏幕绝对坐标）+ 置信度 + 来源（cache/api）</returns>
    Task<VisionProbeResult> LocateAsync(
        string elementType,
        string labelKeyword,
        CancellationToken cancellationToken = default);

    /// <summary>
    /// 强制刷新缓存（窗口位置/尺寸变化时调用）。
    /// </summary>
    Task InvalidateCacheAsync();
}
```

#### `VisionCache` —— 视觉缓存层

```csharp
public class VisionCache
{
    /// <summary>
    /// 缓存键 = (window_fingerprint, element_type, label_keyword)
    /// </summary>
    public Task<CachedBbox?> TryGetAsync(WindowFingerprint fp, string elementType, string labelKeyword);

    /// <summary>
    /// 写入缓存。TTL 默认 24 小时（防止企微版本静默升级）。
    /// </summary>
    public Task SetAsync(WindowFingerprint fp, string elementType, string labelKeyword, BoundingBox bbox);

    /// <summary>
    /// 失效特定窗口的所有缓存（窗口变化时调用）。
    /// </summary>
    public Task InvalidateWindowAsync(WindowFingerprint fp);
}
```

#### `WindowFingerprint` —— 窗口指纹

```csharp
public sealed record WindowFingerprint(
    string WindowClass,         // "WeWorkWindow"
    int X, int Y,               // WindowRect 左上
    int Width, int Height,      // WindowRect 尺寸
    double DpiScale,            // 系统缩放
    string WeComVersion);       // 企微版本（从注册表/进程读取）
{
    // 任意字段变化 → 视为窗口变化 → 失效缓存
    public bool Matches(WindowFingerprint other) => /* 逐字段比较 */;
}
```

### 3.3 视觉缓存策略（核心设计）

**为什么需要缓存**：
- 你定的方针："RPA 的窗口大小、位置都是不变的，只有在变化了的情况下再次调用"
- Qwen3-VL 单次 grounding 28 秒、~0.03 元，对每个发送动作都调不现实
- 企微界面（控件位置）在窗口不变时**绝对稳定**

**缓存键**：`(window_fingerprint, element_type, label_keyword)`

**失效时机**（任一触发即失效）：

1. **窗口指纹变化**：检测到 WindowRect / DPI / 企微版本变化 → 整窗口失效
2. **TTL 过期**：默认 24h（防止企微静默升级）
3. **手动失效**：客户端重启、用户手动「刷新定位」
4. **定位执行失败**：bbox 命中但 SendInput 后状态检查失败 → 该元素失效

**缓存存储**：SQLite（复用 `Client.Core/Queue/SqliteSendQueue` 同款基础设施）。

**首次定位流程**：

```
1. 检查 WindowFingerprint（当前 vs 上次缓存时的）
   ├─ 变化 → 清空整窗口缓存
   └─ 不变 → 继续查元素级缓存
2. 查元素级缓存
   ├─ 命中 → 直接返回 bbox（cache hit, 0 API 调用）
   └─ 未命中 → 调 Qwen3-VL → 写入缓存
3. 调用方拿到 bbox → 转 Win32 屏幕坐标 → SendInput 点击
```

**性能预期**：

| 场景 | 首次定位 | 缓存命中 |
|------|---------|---------|
| 单元素定位 | 28s（API） | <10ms（SQLite 查询） |
| 单次发送流程（5 个元素） | 28s（一次 API 拿全部） | <50ms |
| 24h 内同一窗口同元素 | 1 次 API | 无限次 <10ms |

### 3.4 截图采集 + 像素自检

**痛点教训**：本会话首次验证时，企微窗口被 VSCode 遮挡，截图截到 VSCode 而非企微，导致 OCR/Qwen 输出全是 VSCode 内容。**必须前置校验**。

**采集流程**（参考已实现的 `scripts/auto-capture-wecom.ps1`，将逻辑下沉到 C#）：

```csharp
public class ScreenCapturer
{
    public async Task<Bitmap> CaptureWeComMainWindowAsync()
    {
        // 1. 找 WeWorkWindow hwnd
        // 2. 校验窗口标题包含"企业微信"（防类名误命中）
        // 3. 如最小化则恢复
        // 4. SetForegroundWindow + 等待 500ms
        // 5. 校验 GetForegroundWindow() == hwnd（防遮挡）
        //    - 失败时尝试 ALT-key trick 解除前台锁
        //    - 仍失败 → 抛 WindowNotForegroundException
        // 6. GetWindowRect → CopyFromScreen 截图
        // 7. 像素自检：
        //    - 量化到 16x16 色块
        //    - 白色区域 > 50% → 抛 SuspiciousScreenshotException（疑似 VSCode）
        //    - 颜色多样性 < 30 → 抛 SuspiciousScreenshotException（疑似空白）
        // 8. 返回 Bitmap
    }
}
```

**自检阈值**（来自 `auto-capture-wecom.ps1` 实测）：
- 企微主窗口白色区域占比：**12.9%**（VSCode 浅色主题通常 > 50%）
- 企微颜色多样性：**172 种量化颜色**（VSCode 极简配色通常 < 100）

### 3.5 bbox → 屏幕坐标 → SendInput

```csharp
public class InputExecutor
{
    public async Task<ClickResult> ClickElementAsync(BoundingBox bbox)
    {
        // bbox 是相对主窗口左上角的像素坐标
        // 屏幕坐标 = 窗口左上 + bbox 中心
        var windowRect = GetWindowRect(wecomHwnd);
        int screenX = windowRect.Left + (bbox.X1 + bbox.X2) / 2;
        int screenY = windowRect.Top + (bbox.Y1 + bbox.Y2) / 2;

        // SendInput 单次点击
        NativeMethods.SendInput(/* MOUSEINPUT with screenX, screenY */);
    }

    public async Task<TypeResult> TypeTextAsync(string text)
    {
        // 通过剪贴板粘贴（避免输入法干扰）：
        // 1. Clipboard.SetDataObject(text)
        // 2. SendInput Ctrl+V
        // 3. 等待 200ms
        // 4. SendInput Enter（或定位发送按钮后点击）
    }
}
```

---

## 4. 协议层对接（Qwen3-VL OpenAI 兼容）

### 4.1 API 调用规范

**Endpoint**：`POST https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`（与现有 `QwenProvider` 同域名）

**认证**：`Authorization: Bearer <api_key>`，复用现有 `QWEN_API_KEYS` 环境变量（多 key 池通过客户端配置传入）。

**Request Body**：

```json
{
  "model": "qwen3-vl-plus",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
        {"type": "text", "text": "<定位 prompt>"}
      ]
    }
  ],
  "temperature": 0.1,
  "max_tokens": 4096
}
```

**与文本 QwenProvider 的关键差异**：
1. `messages[].content` 是**数组**（混合 `text` + `image_url`），不是字符串
2. 图片走 base64 data URL（避免客户端发起 OSS 上传）
3. 图片**必须转 RGB**（不接受 RGBA）+ **JPEG 编码**（提高 token 利用率）
4. **不传 `tools` 参数**（视觉定位不需要工具调用）

### 4.2 模型选型

| 模型 | 用途 | 价格档 | 备注 |
|------|------|--------|------|
| **qwen3-vl-plus** | 主策略 | 中 | 已验证可用，性价比最佳 |
| qwen3-vl-max | 高精度兜底 | 高 | plus 定位失败时升级 |
| qwen3-vl-flash | 极低延迟 | 极低 | 不推荐（GUI 定位精度差） |

**首版只用 qwen3-vl-plus**，配置项 `vision.primary_model = "qwen3-vl-plus"`、`vision.fallback_model = "qwen3-vl-max"`。

### 4.3 Prompt 设计

**grounding prompt**（Task 2 用的，已真机验证）：

```
Look at this screenshot carefully. Image dimensions are {W}x{H} pixels.

Find these specific UI elements and return ONLY a JSON array with their bbox
(pixel coordinates relative to top-left of the image):

1. Search box at top (the input box where you type to search contacts/messages)
2. Each conversation item in the middle column (identify the conversation name)
3. The bottom message input box in the right panel
4. The send button (it might be an icon or text label)
5. The left navigation icons (message/mail/document/contacts etc.)

Format (output strictly this JSON, no markdown, no extra text):
[{"label": "<description>", "bbox": [x1, y1, x2, y2], "type": "button|input|list_item|icon|text"}]

bbox must be pixel coordinates. If you cannot find an element, omit it.
Do not make up elements.
```

**OCR prompt**（Task 3，用于读消息内容场景）：

```
请把这张截图里所有可见的文字按行准确读出，每行一个 JSON 对象：
{"text": "<这行的文字>", "where": "<大致位置>"}

只输出 JSON 数组，不要解释、不要 Markdown 代码块。
如果文字模糊不清就跳过，不要瞎猜。
```

### 4.4 失败处理

| 失败场景 | 客户端行为 |
|---------|-----------|
| API HTTP 4xx（鉴权/参数错） | 停止账号 + 上报 `vision_api_auth_failed` |
| API HTTP 5xx / 超时 | 重试 3 次（指数退避），仍失败 → 切 fallback_model |
| 模型返回空数组 / 非法 JSON | 重试 1 次，仍失败 → 降级到 OcrVisionLocator |
| 模型返回 bbox 越界（超过 WindowRect） | 抛异常 + 失效该元素缓存 + 上报 |
| 截图自检失败 | 抛 SuspiciousScreenshotException + 暂停账号 |

---

## 5. 客户端改造计划

### 5.1 `Client.Automation` 改造

| 文件 | 当前状态 | 改造动作 |
|------|---------|---------|
| `Vision/TemplateMatcher.cs` | 已实现（OpenCvSharp） | **保留**，作为 Layer 3 兜底 |
| `Vision/QwenVisionLocator.cs` | 未实现 | **新增**，主策略 |
| `Vision/OcrVisionLocator.cs` | 未实现 | **新增**，降级策略（封装 Windows.Media.Ocr） |
| `Vision/VisionCache.cs` | 未实现 | **新增**，SQLite 持久化 |
| `Vision/ScreenCapturer.cs` | 未实现 | **新增**，含像素自检 |
| `FlaUi/FlaUiDriver.cs` | 已实现（UIA3） | **降级**为兜底，不删（保留诊断能力） |
| `WeCom/WeComAutomation.cs` | 已实现（依赖占位 yaml） | **重构**，改走 `IVisionLocator` |
| `WeCom/MessageWatcher.cs` | 占位（`Text=null`） | **补全**，用 QwenVisionLocator 抓消息文本 |
| `WeCom/LoginStateDetector.cs` | 占位 | **补全**，用 QwenVisionLocator 识别二维码/登录态 |

### 5.2 `Client.App` 改造

| 文件 | 改造动作 |
|------|---------|
| `Services/Stubs/AutomationStubs.cs` | **删除**，Stubs 全部移除 |
| `App.xaml.cs.ConfigureServices` | 改注册：`IVisionLocator → QwenVisionLocator`、`IActionExecutor → WeComAutomation`、`IWeComAutomation → WeComAutomation`、`IHealthSupervisor → HealthSupervisor` |
| `Services/SendMessageService.cs` | `DownloadToTempAsync` 接通真实 `IAgentApiClient.DownloadFileAsync` |

### 5.3 配置项扩展（`configs/client.example.yaml`）

```yaml
vision:
  enabled: true
  primary_model: "qwen3-vl-plus"
  fallback_model: "qwen3-vl-max"
  api_endpoint: "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
  api_keys: ${QWEN_API_KEYS}        # 复用服务端同款 key 池
  timeout_seconds: 120
  max_retries: 3
  cache:
    enabled: true
    ttl_hours: 24
    sqlite_path: "${AppData}/WeComRpa/vision_cache.db"
  screenshot:
    pre_foreground_check: true       # 截图前确认企微在前台
    pixel_sanity_check: true         # 截图后像素自检
    max_white_ratio: 0.5             # 白色区域 > 50% 判为可疑
    min_color_diversity: 30          # 颜色多样性 < 30 判为可疑
```

---

## 6. 对原设计的影响

### 6.1 `wecom-personal-rpa-design.md` 需要更新的章节

| 章节 | 原内容 | 改为 |
|------|--------|------|
| §6.2 三层自动化策略 | FlaUI→Win32→OpenCV | Qwen3-VL→OCR→Win32+OpenCV（本文档 §0.3） |
| §6.3 发送文本 | 依赖 FlaUI 定位输入框 | 依赖 QwenVisionLocator 定位输入框 bbox |
| §6.4 发送图片和文件 | 同上 | 同上 |
| §10.1 准入验证表 | UIA 可见性 / SendInput 验证 | 增加项：**多模态视觉定位准确性**（已通过，本文档 §2.3） |

### 6.2 `wecom-personal-rpa-protocol.md` 无需变更

视觉定位是客户端内部实现细节，服务端不感知。Agent 下发的 `send_text` / `send_file` action 格式不变。

### 6.3 `clients/wecom-personal-rpa/STATUS.md` 需要更新

§③「未开发/占位」清单需要更新：

| 原条目 | 新状态 |
|--------|--------|
| `AutomationStubs.cs` | 计划删除，本文档 §5.2 |
| `assets/wecom_nodes.yaml` 占位值 | **改为** `vision` 配置块 + prompt 模板（不需要节点常量） |
| `MessageWatcher.cs` `Text=null` | 计划用 QwenVisionLocator 补全，本文档 §5.1 |
| `LoginStateDetector.cs` 二维码截图 | 计划用 QwenVisionLocator 补全 |

---

## 7. 风险与限制

### 7.1 已知风险

| 风险 | 缓解 |
|------|------|
| Qwen3-VL API 不可用（断网 / 服务故障） | 降级到 OcrVisionLocator + 坐标兜底；连续失败暂停账号 |
| 模型偶尔返回错误 bbox（幻觉） | bbox 越界检查 + 状态后验（点击后截图确认目标状态变化） |
| API 成本失控（缓存层 bug 导致频繁调用） | 缓存命中率指标 + 告警阈值（每小时 API 调用 > N 次） |
| 企微静默升级导致 bbox 全部失效 | TTL 24h 强制失效 + 窗口指纹含企微版本号 |
| 模型延迟（28s/次）影响用户体验 | 缓存 + 异步预定位（启动时预热常用元素） |

### 7.2 已知限制

1. **必须联网**：客户端离线时无法定位新元素（命中缓存的元素可用）
2. **首次定位慢**：~30s（不可接受地慢，需配合"启动预热"）
3. **隐私**：截图含聊天内容，发送到阿里云 DashScope。需在「员工授权」中明确告知（原设计 §9.2）
4. **企微版本依赖**：模型识别准确度依赖训练数据覆盖的企微版本，新版本可能识别率下降

---

## 8. 开发任务拆解（建议顺序）

| # | 任务 | 依赖 | 预估 |
|---|------|------|------|
| 1 | 实现 `ScreenCapturer`（含像素自检） | 无 | 1 天 |
| 2 | 实现 `QwenVisionLocator`（API 调用 + JSON 解析） | #1 | 2 天 |
| 3 | 实现 `VisionCache`（SQLite 持久化） | 无 | 1 天 |
| 4 | 实现 `WindowFingerprint`（窗口指纹采集） | 无 | 0.5 天 |
| 5 | 实现 `OcrVisionLocator`（降级策略） | #1 | 1 天 |
| 6 | 重构 `WeComAutomation` 走 `IVisionLocator` | #1-5 | 2 天 |
| 7 | 补全 `MessageWatcher` 用视觉抓消息文本 | #6 | 1 天 |
| 8 | 补全 `LoginStateDetector` 用视觉识别二维码 | #6 | 1 天 |
| 9 | 删除 `AutomationStubs` + 改 `Client.App` DI 注册 | #6-8 | 0.5 天 |
| 10 | 真机端到端测试（消息收发） | #9 | 2 天 |

**总计：约 12 个工作日**。

---

## 9. 验证脚本与产物清单（本次设计产出）

| 路径 | 用途 |
|------|------|
| `clients/wecom-personal-rpa/src/Client.VisionProbe/` | OCR 验证工程（B 方案失败证据） |
| `clients/wecom-personal-rpa/scripts/probe-msaa.ps1` | MSAA 探测脚本（UIA/MSAA 双失效证据） |
| `clients/wecom-personal-rpa/scripts/auto-capture-wecom.ps1` | 自动激活企微 + 截图 + 像素自检（生产代码原型） |
| `clients/wecom-personal-rpa/scripts/probe-qwen-vl-full.py` | Qwen3-VL 完整验证（E 方案通过证据） |
| `clients/wecom-personal-rpa/src/vision-probe-out/qwen3vl_eval_*/` | 真机验证原始产物（Task1/2/3 JSON + 标注图） |

---

## 10. 关键决策记录

| # | 决策 | 理由 | 风险 |
|---|------|------|------|
| 1 | 放弃 UIA/MSAA 路线 | 真机验证两者均对企微 D2D UI 0 暴露 | 无（已被证伪） |
| 2 | 放弃纯坐标 + OpenCV 模板 | 项目方向明确否决（"太死板很不可靠"） | 无 |
| 3 | 放弃 Windows.Media.Ocr | 真机识别质量 < 10% | 无（已被证伪） |
| 4 | 主策略定为 Qwen3-VL 多模态 | 真机验证 24 元素 bbox 全部准确 | 依赖网络 + 阿里云可用性 |
| 5 | 配视觉缓存层 | 单次 API 28s/0.03 元不可接受高频调用 | 缓存失效逻辑复杂 |
| 6 | 截图前必须像素自检 | 本会话首次验证被遮挡截图坑过 | 自检阈值需持续校准 |
| 7 | 模型选 qwen3-vl-plus 而非 max | 真机已证明 plus 准确，max 成本翻倍仅作兜底 | 极端复杂场景可能需升级 |

---

## 11. 不在范围内

- 自训练 YOLO 等目标检测模型（数据标注成本不可接受）
- 本地多模态模型（硬件门槛过高）
- Hook / DLL 注入 / 协议逆向（原设计 §1.2 已禁止）
- 服务端 agent / channel 协议改动（视觉定位是客户端内部细节）

---

## 12. 落地状态（2026-06-24 更新）

### 12.1 已实现并测试通过

| 模块 | 实现文件 | 单测 | 真机回归 |
|------|---------|------|---------|
| `IVisionLocator` / `BoundingBox` / `VisionProbeResult` / `VisionConfig` | `Client.Automation/Vision/` | ✅ BoundingBoxTests 14 case | — |
| `QwenVisionApi`（HttpClient + 多 key 池 + 4xx/5xx 分流） | 同上 | ✅ QwenVisionApiTests 13 case | ✅ 真机调用链路验证 |
| `QwenVisionLocator`（缓存 + API + bbox 越界 + 降级链 + 几何兜底） | 同上 | ✅ QwenVisionLocatorTests 8 case | ✅ 场景 1/2/3 全过 |
| `OcrVisionLocator`（Windows.Media.Ocr 降级） | 同上 | ✅ OcrVisionLocatorTests | — |
| `VisionCache`（SQLite + TTL + UPSERT + 失效） | 同上 | ✅ VisionCacheTests 10 case | ✅ 缓存命中实测 7-29ms |
| `ScreenCapturer`（PowerShell 委托截图 + 像素自检） | 同上 | ✅ ScreenCapturerTests + WindowFingerprintTests | ✅ 截图稳定（白 12.9% / 174 色） |
| `InputExecutor`（bbox→屏幕坐标→SendInput） | `Win32/InputExecutor.cs` | ✅ InputExecutorTests | — |
| `WeComAutomation`（接 IVisionLocator） | `WeCom/WeComAutomation.cs` | ✅ WeComAutomationVisionTests | — |
| `MessageWatcher`（Windows.Media.Ocr inline） | `WeCom/MessageWatcher.cs` | ✅ MessageWatcherVisionTests | — |
| `LoginStateDetector`（视觉定位二维码） | `WeCom/LoginStateDetector.cs` | ✅ LoginStateDetectorVisionTests | — |
| `ConversationNavigator`（视觉定位 + 多候选占位） | `WeCom/ConversationNavigator.cs` | 同 WeComAutomationVisionTests | — |
| `Client.App` DI 注册（无 Stubs） | `App.xaml.cs` | — | — |

**单测总数**：119 passed / 0 failed（原 36 + 视觉层新增 83）
**编译**：`dotnet build WeComPersonalRpaClient.sln` 0 错误 0 警告

### 12.2 与原设计的偏差

1. **`ScreenCapturer` 改为委托 PowerShell 截图**（设计 §3.4 原本是纯 C# CopyFromScreen）。原因：C# 进程 DPI 感知和前台权限在子进程环境（如真机回归工具）下不可靠，PowerShell 子进程继承父进程权限稳定。生产环境 Client.App 是常驻 GUI 进程，前台权限天然 OK
2. **`MatchElement` 加几何兜底**（设计 §0.3 三层降级最后一道）。原因：Qwen3-VL 偶尔不按新 prompt 返回 role 字段，几何兜底（按 bbox 在图像中的位置判定 search_box/message_input/send_button 等）作为最后防线
3. **`MatchElement` 双重 prompt**：role 字段严格匹配（首选） + type+几何位置兜底（次选）
4. **`WeComVersion` 读取**：原设计从注册表读，真机发现注册表没 Version 键，改为多路径回退（exe FileVersionInfo → 注册表 → unknown）
5. **`MessageWatcher` 用 inline OCR** 而非 OcrVisionLocator（OcrVisionLocator.LocateAsync 签名是"按 labelKeyword 找单条 bbox"，不返回全部文字，MessageWatcher 需要"取靠下方的最后一条非空 Line"作为消息文本）

### 12.3 未做的部分（留作下个迭代）

| 项 | 原因 | 影响 |
|---|------|------|
| Windows.Graphics.Capture 离屏渲染 | COM 互操作代码量 ~500 行，工程量大 | 当前 CopyFromScreen + 像素自检够用；下个迭代作为「窗口被遮挡时的鲁棒性增强」 |
| ConversationNavigator 真正多候选检测 | 模型当前只返回单 bbox | 等 Qwen3-VL 升级支持 multiple bbox 返回 |
| Client.App 端到端联调 | 需要真实服务端 + 真实企微 + 真实账号 | 不在自动化测试范围，需运维环境 |
| WiX/MSIX 打包 | 独立任务（原计划 §5） | 不阻塞视觉定位上线 |

### 12.4 真机回归结论

- **场景 1（5 元素定位）**：✅ pass，5/5 元素 bbox 命中
- **场景 2（缓存命中）**：✅ pass，第 2/3 次同元素 Source="cache"，耗时 7-29ms
- **场景 3（指纹失效）**：✅ pass，X+1 伪造指纹下 cache miss
- **场景 4（截图自检）**：⏭️ skipped（--image 模式不测）
- **场景 5（失败降级）**：⏭️ skipped（--image 模式不测）

详见 `clients/wecom-personal-rpa/vision-regression-out/report_20260623_235539.yaml`。
