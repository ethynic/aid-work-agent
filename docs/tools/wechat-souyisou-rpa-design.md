# 微信搜一搜 RPA 命令行工具设计

## 2026-07-29：会话关闭判定补充

微信插件在 `Ctrl+W` 后可能只隐藏窗口而暂不销毁 HWND。清理成功必须同时满足：

- 关闭前插件 HWND 的进程路径、类名和标题通过可信身份校验；
- 关闭后插件 HWND 已不存在，或 `IsWindowVisible=false`；
- 普通微信主 HWND 仍存在、可见、身份可信且已恢复前台。

插件仍可见、目标身份错误或主窗口未恢复均返回 `SESSION_CLEANUP_FAILED`。隐藏的旧 HWND 不再被误判为
清理失败，但下一次查询仍必须走完整打开搜一搜流程。

> 日期：2026-07-27
> 状态：🔧 第一版 CLI 开发中
> 关联调研：[微信桌面版 RPA 自动化技术方案调研](../research/wechat-desktop-rpa-technical-research.md)

## 1. 目标与结论

实现一个运行在 Windows 已登录桌面会话中的微信搜一搜 RPA 命令行工具。完整业务入口接收协会名称，自动完成：

1. 复用现有 Agent 搜索协会官网，并从官网确认秘书长姓名；
2. 将协会名称和秘书长姓名交给 Windows CLI；
3. CLI 激活普通微信，搜索“协会名称 + 姓名 + 联系人”；
4. 读取第一页结果文字，先执行整页证据判断，并用于恢复验证与加密审计；
5. 逐条打开最多 10 个结果并判断详情正文；
6. 任一阶段命中后立即停止；10 条均未命中则记录证据并返回未找到；
7. 使用 `Ctrl+W` 关闭详情，保留原结果页和滚动位置；
8. 输出结构化结果，供人工、脚本或 aid-work-agent 调用。

第一期选择 **PowerShell 7/Windows PowerShell 5.1 兼容的无界面 CLI**，不开发桌面 GUI。原因：

- 所有关键 Win32、键盘、鼠标、剪贴板链路均已用 PowerShell 真机验证；
- 无需引入 Python、OCR 服务或浏览器调试端口；
- 可直接复用企业微信个人号 RPA 的前台锁、按键释放、JSON 协议经验；
- 后续如需常驻运行，可用 .NET 8 Console Host 或计划任务包装，不影响核心脚本协议。

## 2. 已验证事实

测试环境：微信 4.1.11.24、Windows 11。

| 能力 | 结论 | 真机证据 |
|------|------|----------|
| 唯一识别普通微信 | 通过 | `Weixin.exe` 路径 + `MainWindowHandle` |
| 激活主窗口 | 通过 | `AttachThreadInput` + 完整前台切换 |
| 打开搜一搜 | 通过 | `Ctrl+F → Down → Enter` |
| 搜一搜窗口身份 | 通过 | `WeChatAppEx.exe` + RadiumWMPF 路径 + 类名 + 标题联合验证 |
| 输入关键词并搜索 | 通过 | 剪贴板粘贴 + Enter |
| 读取结果页 | 通过 | `Ctrl+A → Ctrl+C` 得到 2507 字、84 行、至少 10 条结果 |
| 打开详情并读取正文 | 通过 | 点击结果后复制得到 390 字、26 行正文 |
| 关闭详情 | 人工确认 | 使用 `Ctrl+W` 返回结果页，优于 `Alt+Left` |
| 结果页滚动 | 通过 | 右侧内容区发送 6 次滚轮事件，截图哈希和滚动条位置变化 |
| UI Automation | 不可用 | 搜一搜窗口仅暴露 2 个后代节点，无标题、列表项或 InvokePattern |
| Windows OCR | 不作为主链路 | 当前机器旧探针编译失败；页面文本已可由剪贴板无损读取 |

## 3. 范围

### 3.1 第一期包含

- 普通微信桌面版，单个已登录账号；
- 综合搜索结果；
- 输入已确认的协会名称和秘书长姓名；
- 自动组合查询词“`{association_name} {person_name} 联系人`”；
- 读取结果页可复制文字；
- 前 N 条结果逐条进入详情并读取正文，默认 N=10；
- 调用现有 LLM Provider 做目标人手机号结构化判断；
- 命中即停止、最多 10 条、未命中证据归档；
- 结果去重、滚动、状态校验、超时与失败恢复；
- 单次进程互斥；
- dry-run、诊断截图、机器可读 JSON；
- 本地命令行调用。

### 3.2 第一期不包含

- GUI、托盘、安装器和自动更新；
- 登录、扫码和账号管理；
- 发送微信消息；
- 搜索频控策略绕过；
- 多微信实例并行；
- Session 0 Windows Service 直接控制桌面；
- 注入、Hook、修改微信启动参数或开启 CEF 调试端口；
- 自动绕过验证码、风险提示或安全校验。

第一期 CLI 不负责自行浏览官网。官网和秘书长姓名由 aid-work-agent 现有网页/Agent 能力解决；CLI 接受 `association_name`、`person_name` 以及可选的 `official_site`。这样 RPA 可独立测试，且不会在 Windows 端重复实现 Agent。

## 4. 总体架构

```text
CLI 参数 / stdin JSON
        │
        ▼
Command Router
        │
        ├── probe        环境和窗口诊断
        ├── open         打开搜一搜
        ├── search       搜索并读取结果页
        └── collect      搜索 + 前 N 条详情采集
        │
        ▼
SouyisouFlow（状态机）
        │
        ├── WindowGuard      窗口枚举、身份校验、前台锁
        ├── InputDriver      键盘、鼠标、滚轮，保证按键释放
        ├── ClipboardReader  结果页/详情页文本读取
        ├── ScreenProbe      截图、哈希、稳定性判断
        ├── UIAResultSelector 语义筛选结果节点和物理 ClickablePoint
        ├── ResultParser     标题/摘要/来源/日期/联系方式解析
        └── Recovery         Ctrl+W、重开搜一搜、失败截图
        │
        ▼
Evidence Judge
        ├── LLM Gateway      复用 Qwen/Zhipu/DeepSeek Provider 与 failover
        ├── Deterministic    姓名邻近、手机号正则和来源约束
        └── Evidence Store   加密保存列表/详情/链接/模型判断
```

完整业务编排分两层：

```text
AssociationContactWorkflow（服务端/Agent）
  ├── OfficialSiteResolver：搜索并确认协会官网
  ├── SecretaryResolver：从官网证据确认秘书长姓名
  └── 调用 Windows CLI：association_name + person_name + official_site

Windows CLI
  ├── 微信搜一搜 RPA
  ├── 证据采集
  └── LLM/规则判断
```

核心脚本放在：

```text
clients/wechat-souyisou-rpa/
├── scripts/
│   ├── wechat-souyisou.ps1
│   ├── lib/
│   │   ├── window.ps1
│   │   ├── input.ps1
│   │   ├── clipboard.ps1
│   │   ├── screen.ps1
│   │   ├── cards.ps1
│   │   ├── parser.ps1
│   │   └── flow.ps1
│   └── tests/
└── README.md
```

早期真机实验代码不进入生产目录；已验证的窗口识别、激活和输入约束直接由
`clients/wechat-souyisou-rpa/` 的正式实现及自动测试维护。

## 5. 命令行协议

### 5.1 命令

```powershell
# 环境诊断，不发送按键
.\wechat-souyisou.ps1 probe

# 打开搜一搜
.\wechat-souyisou.ps1 open -Execute

# 搜索并读取结果列表
.\wechat-souyisou.ps1 search -Keyword "协会 联系人" -Execute

# 搜索并采集前 10 条详情
.\wechat-souyisou.ps1 collect `
  -AssociationName "中国游艺设备游乐园协会" `
  -PersonName "王承展" `
  -Limit 10 `
  -Execute
```

为避免中文命令行转义问题，调用方优先通过 stdin 传 UTF-8 JSON：

```json
{
  "action": "collect",
  "association_name": "中国游艺设备游乐园协会",
  "person_name": "王承展",
  "official_site": "https://example.org",
  "limit": 10,
  "request_id": "uuid"
}
```

### 5.2 输出

stdout 只输出一行最终 JSON；诊断信息写 stderr 或日志。

```json
{
  "ok": true,
  "request_id": "uuid",
  "status": "found",
  "association_name": "...",
  "person_name": "...",
  "details_checked": 3,
  "matched_source": "detail",
  "artifact_ref": "dpapi:wechat-souyisou:uuid",
  "diagnostics": {
    "scroll_count": 2,
    "duration_ms": 18342
  }
}
```

联系人、电话和正文属于敏感业务数据：

- 生产模式不在日志和 stdout 输出明文；
- 结果写入 DPAPI 加密本地文件，stdout 仅返回 `artifact_ref`；
- 由受信调用方读取、解密并通过 HTTPS/HMAC 上传；
- 调试日志中的手机号统一脱敏为 `185****7486`；
- 失败截图按短 TTL 保存，默认 24 小时清理。

状态枚举：

- `found`：有足够证据证明手机号属于目标人；
- `not_found`：实际打开的最多 10 条详情均未命中；
- `inconclusive`：页面/模型/恢复异常，不能等同于未找到；
- `blocked`：登录、验证码或安全提示需要人工处理。

## 6. 状态机

```text
Idle
 → MainWindowLocated
 → MainWindowForeground
 → SouyisouOpened
 → KeywordSubmitted
 → ResultStable
 → ResultTextCopied
 → ResultJudged
 → CardLocated
 → DetailOpened
 → DetailTextCopied
 → DetailJudged
 → DetailClosed
 → ResultRestored
 → Scrolled（需要更多时）
 → Completed
```

每次输入前后都验证当前状态对应的可信窗口：

- `Ctrl+F/Down/Enter` 前：必须是目标 `Weixin.exe` 主 HWND；
- 搜一搜阶段：必须是可信 `WeChatAppEx`；
- 详情关闭后：必须回到同一可信搜一搜窗口；
- 任何时刻切换到其他应用：立即停止后续输入，输出失败。

## 7. 核心流程

### 7.1 打开并搜索

1. 枚举可见顶层窗口；
2. 严格匹配 `Tencent\Weixin\Weixin.exe`，且 HWND 等于 `MainWindowHandle`；
3. 用 `AttachThreadInput` 激活主窗口；
4. 发送 `Ctrl+F → Down → Enter`；
5. 等待可信 `WeChatAppEx` 成为前台；
6. `Ctrl+A` 清空输入，剪贴板粘贴关键词，Enter；
7. 每 500ms 通过 `Ctrl+A → Ctrl+C` 读取候选页面，连续两次满足结果页固定栏目证据
   才能进入取证；默认上限 15 秒且不得低于 10 秒；
8. 超时返回 `SEARCH_RESULTS_TIMEOUT`，不封存加载页，由 `finally` 清理会话。

### 7.2 读取结果列表

结果页读取主路径不是 OCR：

1. 发送 `Ctrl+A → Ctrl+C`；
2. 读取 Unicode 剪贴板文本；
3. 校验文本不是空、不是仅等于关键词，且包含结果页固定栏目；
4. 解析标题、摘要、来源和日期；
5. 保存原始文本，解析失败时仍可追溯。

复制时同时读取剪贴板 `UnicodeText` 和 `HTML Format`：

- `HTML Format` 中存在 `<a href>` 时提取标题与公众号文章链接；
- 微信未提供 HTML/链接时不猜测 URL，仅保存文本；
- 原始 HTML 作为敏感证据加密保存，不写日志。

剪贴板操作前保存原内容，任务完成后尽力恢复；敏感剪贴板内容在任务结束时清空。
固定栏目就绪信号只解决结果列表。详情页已有截图变化、可信前台和复制文本特征校验，
但慢网详情仍使用可配置等待，后续真机矩阵需补详情正文稳定轮询。

### 7.3 UIA 结果节点定位

从已经完成身份校验且处于前台的 `WeChatAppEx` HWND 创建 UIA 根节点，只枚举可见、支持
`InvokePattern` 且具有 `ClickablePoint` 的 `Button/ListItem`。协会名和人员名只在内存中用于双语义
筛选，排除百科、小程序、顶部分类和右侧栏；Button 优先、从上到下排序，嵌套标题按矩形重叠去重。
正式线程在读取窗口矩形和 UIA 坐标前声明 Per-Monitor DPI Aware V2，直接使用 UIA 物理屏幕坐标，
不按 DPI 二次缩放，也不叠加窗口原点。点击动作前后均复验相同可信前台。

### 7.4 逐条详情采集

对每个 UIA 候选：

1. 记录结果页截图哈希和候选指纹，不记录节点正文；
2. 单击 `ClickablePoint`；
3. 等待画面变化且仍为可信 `WeChatAppEx`；
4. `Ctrl+A → Ctrl+C` 读取详情全文；
5. 校验详情文本长度、标题匹配度和非列表页特征；
6. 解析联系人、手机号、座机、邮箱；
7. 将目标协会、目标姓名、来源标题和正文交给 Evidence Judge；
8. 命中时立即结束，不再点击剩余结果；
9. 未命中时发送 `Ctrl+W` 关闭详情；
10. 校验回到结果列表，滚动位置应保持；
11. 根据内存中的候选指纹去重。

`Ctrl+W` 是标准返回方式；`Alt+Left` 仅作降级路径。

### 7.5 滚动与前 10 条

1. 处理当前视口内未处理 UIA 候选；
2. 鼠标移动到结果内容区中央；
3. 每次向下滚动 3～6 个滚轮刻度；
4. 等待画面稳定；
5. 重新枚举 UIA，并按候选指纹去重；
6. 达到 `limit`、连续两次无新标题或超过最大滚动次数时停止。

滚动不依赖右侧滚动条坐标；只要求鼠标位于结果大框内。

## 8. 目标手机号判断

### 8.1 判断顺序

1. 从文本提取手机号候选：`1[3-9]\d{9}`；
2. 没有手机号时直接判定当前证据未命中，不调用模型；
3. 有手机号时，调用现有 `LLMGateway` 做结构化判断；
4. 模型必须返回目标姓名、手机号、支持句、置信度和结论；
5. 确定性校验模型返回的手机号确实存在于原文，姓名也必须存在；
6. 不满足校验时视为未命中或 `inconclusive`，不能凭模型补全号码。

号码归属仍由 LLM 按自然语言语义判断，不增加固定词距或“同一行即归属”的死板规则。
旧信息可以采用；目标人明确列在联系人/联络人组中且随后给出联系电话时，该号码可作为
组内每个目标联系人的可用联系方式，即使多人共用或同时服务其他人。只有文本明确将号码
排他绑定给另一人，或目标姓名只出现在与该联系方式无关的上下文时才拒绝。多人共现时，
若存在多个候选号码，优先选择在更多独立搜索结果中重复与目标联系人组关联的完整手机号。
命中引用必须是原文中同时包含目标姓名和完整号码的连续片段；PowerShell 的最终检查
只验证姓名、号码和逐字引用真实存在，不重新判断语义归属。

### 8.2 结构化输出

```json
{
  "matched": true,
  "person_name": "王承展",
  "mobile": "18511597486",
  "evidence_quote": "会议联系人：王承展 18511597486",
  "confidence": 0.99,
  "reason": "姓名与手机号在同一联系人语义片段"
}
```

### 8.3 提示约束

- 只允许依据传入文本，不使用模型记忆或外部知识补号码；
- 同一段出现多人号码时必须正确绑定姓名；
- 只有座机、邮箱或其他人的手机不算命中；
- 证据歧义时返回 `matched=false`；
- 模型异常走 Provider failover；全部失败返回 `inconclusive`，不继续冒充“未找到”。

## 9. 可靠性与失败恢复

- 单机使用命名 Mutex，禁止两个任务同时操作微信；
- 所有组合键在 `finally` 中逐键释放；
- 每一步有独立超时和错误码；
- 前台窗口变化时 fail closed，不继续盲打；
- 详情读取失败时先尝试 `Ctrl+W` 恢复列表，再决定是否中止；
- 连续两条失败即停止，避免错误坐标连点；
- 任务失败保留：阶段、窗口身份、脱敏文本摘要、截图、截图哈希；
- 不自动处理登录失效、验证码、风险提示，由人工接管。

### 9.1 每人独立搜一搜会话

`search` 和 `collect` 必须记录本次打开的插件 HWND。无论业务终态是
`found/not_found/inconclusive/blocked` 或任意异常，释放 Mutex 前都必须：

1. 若详情仍打开，先在精确插件 HWND 上安全发送一次 `Ctrl+W` 返回结果页；
   复制并用本次原始列表证据验证确已恢复，失败时不得继续发送插件关闭快捷键；
2. 重新校验记录的插件 HWND 仍是可信 `WeChatAppEx`；若它已经隐藏，不得重新激活；
3. 仅对仍可见的精确插件 HWND 发送 `Ctrl+W`，最多轮询约5秒等待 HWND 销毁或隐藏；
4. 校验普通微信主 HWND 仍存在且进程可信，并精确激活为前台；
5. 插件 HWND 已销毁，或插件已隐藏且精确主 HWND 恢复前台，成功结果返回
   `session_closed=true`；插件仍可见、仍占前台或其他插件 HWND 占前台均失败。

不能使用模糊标题 `AppActivate`。清理动作使用安全组合键函数，异常路径也必须逐键
`KeyUp`。清理失败升级为 `SESSION_CLEANUP_FAILED`，不得输出原业务成功并继续下一人；
已有 DPAPI 证据 artifact 保持不变，另写 `stage=cleanup` 的无 PII 失败诊断。
每个会话最多执行一次 cleanup 尝试；显式 cleanup 已失败时，外层 `finally` 不重复发送
`Ctrl+W`，只完成剪贴板和 Mutex 收口后上报失败。
分层状态必须分别处理：详情态执行“关详情→验列表→关插件”，列表态只关插件，已经
完成主窗口恢复的终态不再发送任何关闭快捷键。
`probe` 不创建插件会话；`open` 的语义是显式打开供调查，不自动关闭。

建议错误码：

```text
WX_WINDOW_NOT_FOUND
WX_WINDOW_AMBIGUOUS
WX_ACTIVATION_FAILED
SOUYISOU_WINDOW_UNTRUSTED
RESULT_LOAD_TIMEOUT
RESULT_TEXT_EMPTY
UIA_ROOT_UNAVAILABLE
DETAIL_LOAD_TIMEOUT
SESSION_CLEANUP_FAILED
DETAIL_TEXT_INVALID
FOREGROUND_LOST
RECOVERY_FAILED
```

## 10. 运行与集成

### 10.1 本地 CLI

第一期由人工或本地脚本调用，适合快速交付和真机迭代。

### 10.2 常驻 Worker

需要服务端派单时，增加无 GUI 的 .NET 8 Console Host：

- 必须运行在“用户已登录”的交互桌面会话，不能直接跑 Session 0 服务；
- Console Host 轮询服务端任务或保持 WebSocket；
- 收到任务后启动 PowerShell CLI，读取单行 JSON；
- 复用企业微信个人号 RPA 的 HMAC、队列、Supervisor 和审计模式；
- Console Host 不复制自动化逻辑，只负责进程管理和安全通信。

### 10.3 Agent 工具

服务端后续提供 `association_contact_lookup` 工具，唯一必填业务参数为协会名称：

1. Agent 搜索并确认官网；
2. Agent 从官网证据确定秘书长姓名；
3. 创建 Windows RPA 任务；
4. CLI 返回 `found/not_found/inconclusive/blocked` 和 `artifact_ref`；
5. 服务端保存来源、判断过程和审计信息。

Windows CLI 也保留 `association_name + person_name` 的直接入口，便于单独验收 RPA。

第一版 LLM 接入由 `scripts/llm_judge.py` 完成：stdin 接收证据 JSON，调用项目
`src.llm.gateway.llm_gateway.chat`（`temperature=0`），stdout 只返回严格单行 JSON。
PowerShell 执行姓名、手机号和证据原文的最终确定性校验；adapter 或 Provider 全失败时
任务返回 `inconclusive`。

外部 Judge 进程协议的 stdin/stdout 均为 UTF-8。Windows PowerShell 所在的旧 .NET
若不提供 `ProcessStartInfo.StandardInputEncoding`，不得使用默认代码页的
`StandardInput.WriteLine`；实现通过 `StandardInput.BaseStream` 写入 UTF-8 无 BOM
字节，并仅为避免旧重定向 writer 预写 BOM 而短暂设置后恢复
`Console.InputEncoding`。测试必须使用真实 Python 子进程严格解码中文 JSON，不能只做
源码字符串断言。

Provider 首次返回 JSON 解析失败或严格 schema/type/value 校验失败时，adapter 只重试
一次。重试消息沿用原始目标与证据语义，只说明上次格式不合规并重申 exact schema；
不得拼接第一次原始响应。网络、鉴权和 `gateway.chat` 异常不属于格式错误，不重试。
第二次仍不合规则失败并由 PowerShell 记为 `inconclusive`；格式重试不能绕过最终的
姓名、号码、逐字 evidence quote 合同。
首次和重试共享单一 `JUDGE_MAX_TOKENS=2500` 预算常量。预算包含推理型 Provider 的
reasoning token；不得回退到已真机复现会在800 token处 `finish_reason=length`、
最终 `content` 为空的配置。

## 11. 测试策略

### 10.1 无副作用测试

- 路径和窗口身份筛选；
- 企业微信 `WeChatAppEx` 必须被拒绝；
- 前台丢失后不再发送按键；
- 异常时全部 KeyUp；
- 状态机顺序；
- 剪贴板列表/详情解析；
- 手机号、座机、邮箱提取与脱敏；
- UIA 双语义筛选、排序、去重和坐标合同；
- 滚动去重和停止条件；
- 详情命中立即停止；
- 详情第 N 条命中立即停止；
- 10 条无命中返回 `not_found`；
- LLM 返回原文不存在的号码时必须拒绝；
- LLM 全部失败返回 `inconclusive`；
- HTML Format 链接提取与无链接降级；
- stdout 单行 JSON；
- dry-run 不触碰微信。

### 10.2 真机测试

- 暗色/浅色主题；
- 100%、125%、150% DPI；
- 最大化和普通窗口；
- 0、1、10、超过 10 条结果；
- 不同 UIA 结果节点结构、有图/无图结果；
- 详情正常、详情加载失败、外链详情；
- `Ctrl+W` 后滚动位置保持；
- 用户抢焦点、微信最小化、微信退出；
- 中文关键词、空格、特殊字符；
- 10 条连续采集成功率和总耗时。

## 12. 验收标准

- 30 次打开搜一搜成功率 ≥ 98%；
- 30 次结果页复制成功率 ≥ 98%；
- 前 10 条详情打开/读取/关闭闭环成功率 ≥ 95%；
- 详情与列表标题匹配准确率 ≥ 98%；
- 手机号提取误抓率 < 5%，日志无明文泄漏；
- 模型返回的号码 100% 可在对应原始证据中回溯；
- 不使用无边界的整页复制文本直接命中；
- 最多只打开 10 条，命中后无额外点击；
- 未找到任务保留完整加密证据，能区分模型漏判与原文无号码；
- 任何前台丢失场景不向其他应用注入后续按键；
- 单任务异常后可恢复到结果列表或明确停止；
- 全流程无需 GUI 和人工点击，安全提示除外。

## 13. 设计决策

1. **CLI 优先，GUI 延后**：当前瓶颈是自动化稳定性，不是交互界面。
2. **PowerShell 是正式执行层**：关键链路已验证，避免为换语言重做风险。
3. **剪贴板是主 Reader**：正文无损、无需 OCR、速度快。
4. **UIA 只负责定位**：语义节点和物理点击点，不承担正文判断。
5. **详情用 `Ctrl+W` 关闭**：保留结果页和滚动位置。
6. **不启用 CDP、不注入微信**：降低版本和账号风险。
7. **先本地 CLI，后 Console Host**：核心协议稳定后再接服务端派单。
8. **Agent 负责官网与姓名，CLI 负责微信**：职责清晰，已有能力不重复建设。
9. **规则提取 + LLM 绑定判断**：模型只做姓名和号码关系判断，号码必须来自原文。
10. **证据优先**：无论命中与否都可审计；有链接保存链接，无链接保存加密文本。

## 14. 前 10 条边界与图片/PDF 证据

- 搜索结果页 `Ctrl+A/Ctrl+C` 可能包含第 11 条及更后结果；整页命中允许直接结束，
  但必须把来源标记为 `result_page_unbounded`，不得宣称属于“前 10 条详情”。
- 联系人命中必须来自已经按 UIA 屏幕顺序打开且计入 `limit <= 10` 的详情。
- 已确认点击后截图变化、且复制内容不具备结果列表结构时，即使文本为空、没有目标主体
  或没有 PDF/图片标记，也允许对中央详情正文区执行 OCR。
- 详情没有可验证的目标手机号时，只截取中央详情正文区域的最多 3 个滚动视口，交给项目现有
  PaddleOCR 文档解析能力；每次截图前后继续执行可信前台守卫。
- 连续 OCR 视口按截图哈希去重；滚动后哈希相同即停止，至少保留第一张。
- OCR 只作为当前详情的补充证据，OCR 文字仍须通过姓名、手机号原文绑定校验。
- 临时截图使用随机文件名，OCR 完成后立即删除；DPAPI artifact 保存 OCR 文字、截图哈希
  和视口序号，不在 stdout 或普通日志输出手机号。
- OCR 未配置、调用失败、返回结构异常或无法证明仍在当前详情时，当前任务返回
  `inconclusive`，不得把图片/PDF 详情判为 `not_found`。
- 列表 Judge 的 `matched/not_matched/inconclusive` 及固定非敏感 reason code 写入
  DPAPI artifact；即使后续 UIA 枚举没有候选，也能区分模型未命中、协议失败和证据拒绝。
- 点击无变化、详情无效、OCR 失败和重复详情在 DPAPI artifact 中记录
  `stage/reason/text_length/hash` 等非正文诊断字段。

### 14.1 UIA 物理点击点

真机已确认 UIA `ClickablePoint` 能在双屏、微信显示器 150% DPI 下打开正确详情。UIA 坐标与
Per-Monitor V2 的 `SetCursorPos` 都是物理屏幕坐标，因此不执行比例换算；多屏负坐标保持有效。
旧截图卡片分割、窗口比例 locator 和固定相对点均已删除。

同次真机截图确认当前微信详情为整页打开，正文/PDF 位于窗口中央
`x=0.26..0.74`，并非原假设的右侧窄栏；OCR 裁剪及滚轮焦点已同步校准到中央正文区。

可信插件窗口矩形获取失败或尺寸异常返回 `WINDOW_RECT_FAILED`。UIA 根不可用属于能力故障；一次有界
恢复后没有候选或没有新候选则记录安全原因并按现有状态规则结束，不升级为窗口安全故障。
