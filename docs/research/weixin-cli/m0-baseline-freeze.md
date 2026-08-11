# M0：weixin-cli 现状冻结与基线记录

> 日期：2026-08-11
>
> 阶段：M0（现状冻结与实验规范），对应 [开发计划](../../plans/weixin/plan-weixin-cli.md) §2
>
> 设计：[weixin-cli 设计](../../design/weixin/weixin-cli-design.md)
>
> 性质：只读调研产物，不修改任何被冻结目录。

## 1. 冻结对象与版本基线

| 对象 | 路径 | 冻结内容 |
|---|---|---|
| 旧微信搜一搜 RPA | `clients/wechat-souyisou-rpa/` | 脚本、离线测试、真机记录（见 §2、§3） |
| 协会客户端微信脚本（只读参考） | `clients/association-client-cli/scripts/` | 与旧 RPA 的比对结论（见 §4） |
| BOSS 参考 Provider | `clients/boss-resume-assistant/` | TypeScript Provider 骨架模式（M1 复用） |
| 共享契约测试 | `clients/shared/mcp-conformance/` | `suite.mjs` / `suite.d.ts` / README |

冻结时间点环境基线（来自既有文档与真机记录）：

- 微信 Windows 客户端：`4.1.11.24`（`Weixin.exe`），插件进程 `WeChatAppEx.exe`，插件路径段 `\Tencent\xwechat\xplugin\plugins\RadiumWMPF\`
- 操作系统：Windows 11，交互桌面会话，未锁屏
- 主窗口类名 `Qt51514QWindowIcon`、标题「微信」；插件窗口类名 `Chrome_WidgetWin_0`、标题「微信」
- PowerShell 5.1 兼容（.NET Framework 4.x，无 `[double]::IsFinite`）

## 2. 离线测试基线（不操作真实微信）

`clients/wechat-souyisou-rpa/tests/`：

| 测试 | 规模 | 覆盖 |
|---|---|---|
| `test-static.ps1` | 998 行 | 两脚本 AST 解析零错误；只读变量保护；手机号正则；Judge schema/证据/幻觉拒绝；确定性 judge 多人共现排除；外部 Judge UTF-8 无 BOM stdin/stdout；DPI 物理坐标合同；cleanup 语义 |
| `test-uia-result-targets.ps1` | 152 行 | UIA 候选筛选合同（双语义命中、尺寸窗口、主列区域、去重） |
| `test-window-session.ps1` | 273 行 | 窗口会话状态机（owned HWND、返回列表、自然关闭、cleanup 一次） |
| `test_llm_judge.py` / `test_ocr_adapter.py` | — | Python judge / OCR adapter 单测 |
| `utf8-stdin-helper.py` | — | UTF-8 stdin 严格校验辅助 |

运行入口：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\test-static.ps1`。

**M0 结论**：离线基线可复现，后续 `weixin-cli` driver 的「行为等价回归」以上述测试为蓝本改造（复制改造，旧测试保持不变）。

## 3. 真机基线（历史记录，摘自 dev-plan）

来源：`docs/tools/wechat-souyisou-rpa-dev-plan.md` §15/§16。

已真机验证（2026-07-27）：

- 窗口识别、激活（AttachThreadInput 三段式）、搜一搜打开（`Ctrl+F → Down → Enter`）、UIA ValuePattern 输入 + 回读、结果页就绪轮询（500ms×连续 2 次，默认 15s 超时）、剪贴板整页复制、CF_HTML 链接提取
- UIA 双语义候选 + 物理 `ClickablePoint` 详情定位；`InvokePattern.Invoke()` 优先、鼠标点击回退
- 详情闭环：截图哈希变化 → 详情文本特征 → `Ctrl+W` 返回结果页恢复
- 图片/PDF 详情中央区域（x=0.26..0.74）滚动截图 OCR 命中（`Limit=1 -UseProjectLlm`，`checked=1, failures=0, status=found`）
- 精确 HWND cleanup + 主窗口前台恢复；150% DPI 与双屏负坐标已有离线合同测试

未完成 / 未验收（weixin-cli 不得宣称继承这些能力）：

- 完整真机矩阵（100%/125%/150% DPI × 单/双屏 × 深/浅色 × 窗口状态）未跑完
- 连续前 10 条详情闭环、异常恢复矩阵未验收
- artifact TTL 未实现；30 次 × ≥98% 成功率指标从未正式跑过
- `collect` 的 LLM judge 准确率未做 Provider 矩阵验收；确定性 judge 存在漏判
- 搜一搜「文章」分类切换不可用（`Ctrl+Tab` 在新版微信失效，已确认）

## 4. 协会客户端 vs 旧 RPA 只读比对

比对方法：`diff` 同名文件逐字节比较（2026-08-11）。

| 文件 | 结果 |
|---|---|
| `wechat-souyisou.ps1` | **字节一致** |
| `wechat-souyisou-lib.ps1` | **字节一致** |
| `extract-mobile.ps1` | **字节一致** |
| `read-artifact.ps1` | **字节一致** |
| `llm_judge.py` | 有差异（协会侧面向文心/协会业务适配） |
| `ocr_adapter.py` | 有差异（同上） |
| `wenxin_collect.py` | 仅协会侧存在（业务编排层，不属于微信自动化内核） |

**结论**：

1. PowerShell 自动化内核（窗口/输入/剪贴板/UIA/DPAPI/cleanup）两侧完全同源，M2 一次性复制只需以 `clients/wechat-souyisou-rpa/scripts/` 为源即可得到与协会客户端一致的自动化能力；
2. Python 层（LLM judge、OCR、文心编排）属于协会业务语义，按设计 §9 不进入 `weixin-cli`；
3. 协会客户端无任何需要 weixin-cli 兼容的私有协议分叉。

## 5. 可复用算法清单（M2 复制范围）

复制到 `clients/weixin-cli/automation/powershell/` 后需**去除协会业务命名**（`AssociationName`/`PersonName`/`联系人` 后缀、手机号判定、LLM judge），保留以下通用机制：

| 机制 | 位置（lib.ps1 函数） |
|---|---|
| 主窗口/插件窗口身份联合校验（路径+类名+标题） | `Select-WeixinMainWindow` / `Test-WeixinForegroundIdentity` / `Test-WeixinMainIdentity` |
| 前台守卫与恢复 | `Get-TrustedForegroundIdentity`（主脚本内）/ `Test-OrRestoreTrustedForeground` / `Invoke-WeixinActivation` |
| 组合键逐键 KeyUp / 安全鼠标点击 | `Invoke-SafeKeyChord` / `Invoke-SafeMouseClick` |
| 搜一搜打开与可信验证 | `Invoke-LimitedTrustedOpen` + `Ctrl+F/Down/Enter` |
| UIA ValuePattern 输入 + 规范化回读 | `Invoke-WeixinSouyisouSetValueAndReadback` / `Invoke-VerifiedWeixinUaSearchSubmission` / `Normalize-WeixinSearchInputText` |
| 结果页就绪轮询与结果页证据判定 | `Test-SearchResultReady` / `Test-ResultPageEvidence` |
| UIA 候选枚举/筛选/去重 | `Get-WeixinUiaResultDescriptors`（主脚本内）/ `Select-WeixinUiaResultTargets` |
| InvokePattern 点击 + 鼠标回退 | `Find-WeixinCardElement` + 点击段（主脚本内） |
| Per-Monitor V2 DPI 物理坐标 | `ConvertTo-WeixinPhysicalClickPoint` / `Get-BitmapSha256` |
| 窗口会话状态机与精确 cleanup | `New-WeixinWindowSession` / `Add-WeixinWindowSessionForeground` / `Invoke-WeixinWindowSessionReturnToList` / `Close-WeixinWindowSession` / `Close-WeixinPluginSession` / `Complete-WeixinPluginSession` |
| 详情闭环辅助 | `Test-WeixinDetailOpened` / `Test-WeixinReadableDetailCopy` / `Wait-WeixinDetailSettled` |
| DPAPI artifact 存取 | `Protect-EvidenceArtifact` / `Unprotect-EvidenceArtifact` |
| 日志/错误脱敏 | `Get-RedactedSummary` |
| 工作预算（9min 业务 + 1min 清理） | `Assert-WeixinWorkBudget` |
| 跨进程命名 Mutex 单飞 | 主脚本 `Local\AidWorkAgent.WechatSouyisouRpa` |
| 外部进程单行 JSON 调用（UTF-8 无 BOM） | `New-ExternalJudge` / `ConvertTo-WindowsCommandLineArgument`（可作为 driver 子进程范式参考） |

## 6. 已知踩坑清单（复制时必须保留对应防护）

| # | 踩坑 | 既有防护 |
|---|---|---|
| 1 | 新版微信 `Ctrl+Tab` 不能切换搜一搜分类 | 已移除该快捷键，直接用「全部」列表；M3 文章分类需重新 probe |
| 2 | `Alt+Left` 不关闭详情页（只是浏览器后退） | 详情关闭统一 `Ctrl+W` |
| 3 | 剪贴板粘贴输入不可靠、依赖焦点 | 改 UIA `ValuePattern.SetValue` + 回读比对 |
| 4 | `mouse_event` 点击受前台抢占影响 | 优先 `InvokePattern.Invoke()`，鼠标仅回退且前后复验前台 |
| 5 | 150% DPI 坐标虚拟化、双屏负坐标 | 线程声明 Per-Monitor V2，全程物理坐标，不二次换算 |
| 6 | 隐藏插件 HWND 不算关闭 | cleanup 只验证身份、不重新激活；可见插件轮询 ≤5s |
| 7 | cleanup 重复发送关闭键可能误关后台标签 | 每会话 cleanup 恰好一次，失败不在 finally 重试 |
| 8 | 结果页整页复制含第 11 条之后内容 | artifact 标记 `result_page_unbounded`，命中证据仅限已点击详情 |
| 9 | Chromium UIA 节点枚举后失效 | 按 name+ControlType 现场重新定位（`Find-WeixinCardElement`） |
| 10 | 中文代码页/控制台编码污染子进程 stdin | 显式 UTF-8 无 BOM 字节写入重定向流 |
| 11 | 负滚轮 delta 转 UInt32 溢出 | Win32 二进制补码（`ConvertTo-MouseWheelData`） |
| 12 | 详情页为整页布局，OCR 误截左侧结果列表 | 只截中央 `x=0.26..0.74` 区域，最多 2 个滚动视口按哈希去重 |
| 13 | 异常路径组合键残留按下 | `Invoke-SafeKeyChord` finally 逐键 KeyUp |
| 14 | LLM judge 幻觉号码 | PowerShell 二次校验姓名/号码/原文；weixin-cli v0.1 不含 LLM judge |
| 15 | 异常时详情可能仍打开 | failure artifact 记 `detail_may_be_open`；finally 不猜层级，直接关整个插件 HWND |

## 7. M0 验收自检

- [x] 不操作真实微信即可完成本记录（全部来自既有文档与只读 diff）
- [x] 冻结协议样本见 [m0-frozen-protocol-samples.md](./m0-frozen-protocol-samples.md)
- [x] probe 报告模板见 [probe-report-template.md](./probe-report-template.md)
- [x] P0~P3 风险等级与白名单格式见 [probe-risk-and-whitelist.md](./probe-risk-and-whitelist.md)
- [x] 协会禁改路径零改动（`git status` 全仓干净，M0 仅新增 `docs/` 文件）
