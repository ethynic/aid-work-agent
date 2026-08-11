# Probe 报告：p1-chat-search-group（好友/群检索 UIA 结构 + 点击进入）

> Probe ID：`p1-chat-search-group`
>
> 阶段：M3 probe，对应 [设计文档 §7.3 首批 probe 第 4 项](../../../design/weixin/weixin-cli-design.md) 与 [开发计划](../../../plans/weixin/plan-weixin-cli.md)
>
> 风险等级：**P1（导航）** — 激活窗口、打开搜索、输入但不提交或只读提交、点击进入会话；不发送消息、不读历史、不关闭用户窗口。
>
> 关联规范：[probe-risk-and-whitelist.md](./probe-risk-and-whitelist.md)
>
> 日期：2026-08-11

## 1. 假设

微信主窗口 `Ctrl+F` 全局搜索可通过自动化驱动：激活主窗口 → `Ctrl+F` → 逐字输入查询 → 在"群聊"分类找到匹配项 → 点击进入目标会话。全程存在机器可验证的终态（搜索面板关闭 + 会话内容区切换）。

## 2. 环境

| 项 | 值 |
|---|---|
| 操作系统 | Microsoft Windows 11 专业版 |
| 微信版本 | 4.1.12.26（`C:\Program Files\Tencent\Weixin\Weixin.exe`） |
| 主屏分辨率 | 2560 × 1440 |
| 主窗口类名 / 标题 | `Qt51514QWindowIcon` / `微信` |
| 搜索面板（覆盖层）类名 | `Qt51514QWindowToolSaveBits`（标题 `Weixin`，独立 HWND） |
| DPI 感知 | 线程固定 Per-Monitor V2（`SetThreadDpiAwarenessContext(-4)`） |
| 测试查询 | 联系人C短词（初探）、联系人B、联系人A、测试群X（定位方案验证）；真实姓名明文不入库，下文以代号指代 |
| 视觉定位模型 | Kimi `kimi-k3`（Moonshot，`https://api.moonshot.cn/v1/chat/completions`），推理模型，temperature 只允许 1/省略，答案取 `content`（空则取 `reasoning_content`） |

## 3. 步骤与证据

代码：`clients/weixin-cli/experiments/probes/p1-chat-search-group/`
- `probe.json` — probe 元数据（假设、允许/禁止动作、成功标准、cleanup）
- `probe-lib.ps1` — Win32/UIA 底座（一次性复制自协会客户端已验证实现，在 weixin-cli 内独立维护）
- `run.ps1` — 主脚本

执行流程：

1. **定位并激活主窗口**。`EnumWindows` + `IsWindowVisible` 枚举顶层窗口，按 `ProcessPath` 后缀 `\Weixin.exe` + `MainWindowHwnd == Hwnd` + `Visible` 三条件筛唯一主窗口。`Invoke-WeixinActivation` 用 `AttachThreadInput` + `BringWindowToTop` + `SetForegroundWindow` 三连，重试 3 次直到 `GetForegroundWindow() == mainHwnd`。
   - 证据：`hwnd=9377842`，激活后前台身份校验通过（`Qt51514QWindowIcon` / `微信`）。

2. **`Ctrl+F` 打开搜索**。`Invoke-SafeKeyChord` 按下前查前台守卫，异常路径逐键 `KeyUp`。
   - 用户确认：搜索面板打开后搜索框必然聚焦，无需额外定位。

3. **清空搜索框残留**。`Ctrl+A` → `Delete`，防止上次实验未关面板时旧查询干扰。

4. **逐字输入测试词（联系人C 短词）**。**关键发现**：
   - ❌ `SendInput` + `KEYEVENTF_UNICODE`（`Send-WeixinUnicodeChar`）：微信 4.x Qt 搜索框**不接收**该方式注入的中文字符，输入被静默丢弃（首次实验截图搜索框为空）。
   - ✅ **剪贴板逐字粘贴**（`Send-WeixinPasteText`）：每个字符 `SetText` 到剪贴板 → `Ctrl+V`。每个字符间隔 350ms 等结果刷新。实验结束后恢复原剪贴板内容（仅当原内容为文本时）。
   - 证据：第二次截图搜索框显示输入词，下方列出匹配结果。

5. **结果定位——Qt UIA 路已证实走不通**。微信 4.x 主窗口是 Qt 自渲染，搜索结果项**完全不以 UIA 节点暴露**。P0 观察（`observe-uia.ps1`，查询词为真实联系人A，深度 `TreeWalker` 枚举全部 Weixin HWND）数据：

   | HWND 类名 | 角色 | UIA 总节点 | 有名 | 可编辑(ValuePattern) | 可点击(InvokePattern) |
   |---|---|---|---|---|---|
   | `Qt51514QWindowIcon` | 主窗口 | 4 | 4 | **0** | **0** |
   | `Qt51514QWindowToolSaveBits` | 搜索覆盖层 | 1 | 1 | **0** | **0** |

   - 主窗口 4 个节点全是顶层 Pane 容器（`微信`/`Weixin`/`Weixin`/`MMUIRenderSubWindowHW`），`TreeWalker.ControlViewWalker` 深度遍历下不去——它们没有 UIA 子节点。
   - **连搜索框都不暴露 `ValuePattern`**（可编辑=0），与搜一搜 Chromium 插件窗（`Edit` + `ValuePattern` 可用）完全不同。
   - **没有任何 `InvokePattern` 节点**（可点击=0），结果项无法用 UIA 点击。
   - 结论：微信 4.x Qt 把整个界面画成一个 `MMUIRenderSubWindowHW` 渲染窗口，UIA 只看到外壳。**Qt UIA 定位元素坐标这条路在微信 4.x 上不可用**，定位必须走外部视觉识别（OCR / 视觉模型），由应用层负责。

   > ⚠️ **关于实验一的"视觉定位"**：实验一用会话多模态模型目测截图给出坐标，**首次命中、重跑歪掉**（同一坐标 `(493,628)` 第二次 `OVERLAY_STILL_OPEN`，搜索面板未关闭）。这实证了「会话模型目测坐标不可复现」。视觉识别是应用层的能力，probe 不应把会话模型的能力算作产品能力。本 probe 对"坐标怎么来"零贡献，只验证了"Qt 搜索结果可用物理坐标点击命中"这一事实；可复现的定位链（OCR 返回 bbox → 换算屏幕坐标）需另行建设。

   ### 5.1 视觉定位的完整链路（实验一的做法，不可复现，仅作记录）

   ### 5.1 视觉定位的完整链路（本次实验实际做法）

   本次实验的"视觉定位"是**由人（实验者/多模态模型）直接看截图给出坐标**，**不是 OCR，也不是 UIA**：

   ```
   ① GetWindowRect(mainHwnd)              → 物理屏幕坐标 rect=(left=303, top=291, 972×973)
   ② CopyFromScreen(left, top, 0, 0, 972×973) → 截取「主窗口区域」PNG（不是全屏）
   ③ 人/多模态模型看 PNG，给出目标点击坐标（见 5.2，本次为屏幕绝对坐标 (493, 628)）
   ④ SetCursorPos + mouse_event 点击
   ```

   ### 5.2 坐标系与换算（实现时必须落地）

   三套坐标，关系固定：

   | 坐标类型 | 来源 | 本次值 | 用途 |
   |---|---|---|---|
   | 截图 PNG 内相对坐标 `(px_png)` | OCR 在截图上量出（原点=PNG 左上角=窗口左上角） | —（OCR 未用） | OCR 产物 |
   | 物理屏幕绝对坐标 `(px_screen)` | 多模态模型直接给出 / 或 PNG 相对坐标+窗口偏移换算 | (493, 628) | `SetCursorPos` 入参 |
   | 逻辑坐标（DPI 虚拟化） | — | 不使用 | 禁止使用 |

   **换算公式（取决于视觉系统返回哪种坐标）**：

   ```
   情况 A — 视觉系统返回「PNG 内相对坐标」(px_png，原点是 PNG 左上角 = 窗口左上角)：
     px_screen_x = window_rect.left + px_png_x      # 例：303 + px_png_x
     px_screen_y = window_rect.top  + px_png_y      # 例：291 + px_png_y

   情况 B — 视觉系统返回「物理屏幕绝对坐标」(px_screen，已含窗口偏移)：
     直接用，无需换算
   ```

   > **本次实验属于情况 B**。核对方法：在搜索态 PNG 上用红框标注 `(493,628)`，红框精确落在"群聊"分类下目标结果项的中心——说明我（多模态模型）分析 PNG 时给出的是**屏幕绝对坐标**，`SetCursorPos(493,628)` 直接命中，无换算。
   >
   > ⚠️ **正式实现若改用 OCR，几乎一定是情况 A**（OCR 返回的是文字在截图上的像素行列，原点是 PNG 左上角），**必须 `+window_rect.left / +window_rect.top` 换算成屏幕绝对坐标**。这是两种视觉系统最容易踩的坑：实现时必须在代码里**显式标注坐标类型**（`coordinate_space: 'png_relative' | 'physical_screen'`，probe-lib.ps1 的 `ConvertTo-WeixinPhysicalClickPoint` 已采用此字段），并在传入 `SetCursorPos` 前统一归一化到 `physical_screen`，禁止两种坐标混用。

   **DPI 换算**：**不需要**。前提是线程已固定 Per-Monitor V2（`SetThreadDpiAwarenessContext(-4)`，见 probe-lib.ps1）。此约束下 `GetWindowRect` / `CopyFromScreen` / `SetCursorPos` 三者都用物理像素，PNG 像素 = 物理像素，1:1，无缩放因子。若未固定 DPI 感知，150% 缩放下 Win32 坐标会被虚拟化，PNG 与光标坐标错位。

   **多屏负坐标**：主窗口若位于主屏左侧/上方的副屏，`window_rect.left/top` 可能为负。公式照常成立，不要拒绝负数（probe-lib.ps1 的 `ConvertTo-WeixinPhysicalClickPoint` 已据此设计）。

   ### 5.3 定位方案对比与验证结论（三种视觉方案）

   Qt UIA 不暴露结果项（见 §5），目标定位必须走外部视觉识别。实验中先后试了三种方案：

   | 方案 | 状态 | 验证结果 |
   |---|---|---|
   | **会话模型目测坐标** | ❌ 不可复现 | Claude Code 会话模型读 PNG 自由文本输出坐标，无 prompt/无 bbox/无置信度。同一查询（联系人B）同一坐标首跑命中、重跑歪掉（`OVERLAY_STILL_OPEN`）。这把会话模型能力误当成产品能力，**否定**。 |
   | **PaddleOCR bbox（未完全否定）** | ⚠️ 链路通、字形误识别 | 仓库 `paddleocr_doc_parsing` skill 的 `/layout-parsing` 接口**确实返回坐标**（`prunedResult.parsing_res_list[].block_bbox = [x1,y1,x2,y2]`，PNG 内像素坐标，`prunedResult.width/height` = 截图尺寸作证）。**坐标链路完全成立**。但 OCR **字形识别有误差**：把测试联系人B首字误识为形近字，纯精确匹配 `IndexOf(原查询)` 在搜索结果区匹配不到真正目标，反而误中聊天列表的消息预览。可复现但需模糊匹配兜底 + 区域限定，**未完全否定，作为降级/备选保留**。 |
   | **百度布局 OCR** | ❌ 仓库无实现 | `configs/config.yaml` 和 `settings.py` 有 `provider: baidu` / `baidu_api_key` 字段，但**全仓库无任何调用代码**（无 `aip`/`baidubce` 导入，无 `AipOcr`），是历史残留的空配置。**不可用**。 |
   | **Kimi `kimi-k3` 视觉模型** ✅ | **已验证可行（首选）** | 见 5.3.1。 |

   #### 5.3.1 Kimi k3 视觉方案（已验证，4/4 成功）

   调用：`POST https://api.moonshot.cn/v1/chat/completions`，`model=kimi-k3`，`messages[].content` 为对象数组（`image_url` data URL + `text`），固定 system prompt 约束只返回 JSON。**kimi-k3 是推理模型**，三点必须注意：① `temperature` 只允许 1 或省略（传 0 报 400）；② `max_tokens` 要给足（推理也消耗 token，4096+）；③ 答案在 `choices[0].message.content`，为空则取 `reasoning_content`。PowerShell `Invoke-RestMethod` 对大 base64 body 会误报 400，改用 `curl.exe --data-binary @reqfile` 发送。

   prompt 要求模型返回 `{"x":<int>,"y":<int>,"label":"<文字>","found":<bool>}`，坐标为 PNG 内像素（情况 A，原点截图左上角）。换算：`screen = window_rect.left/top + (x,y)`。

   验证（微信 4.1.12.26 / Win11 / 2560×1440，全链路真机）：

   | 查询词（脱敏代号） | 类型 | 字数 | label 返回 | 屏幕 y | 终态 |
   |---|---|---|---|---|---|
   | 联系人B | 联系人 | 3 | 原查询词 ✅ | 395 | SUCCESS（overlay 关闭） |
   | 联系人B（复测） | 联系人 | 3 | 原查询词 ✅ | 395 | SUCCESS |
   | 联系人A | 联系人 | 3 | 原查询词 ✅ | 395 | SUCCESS |
   | 测试群X | 群聊 | 7 | 原查询词 ✅ | 597 | SUCCESS |

   4/4 label 准确（label 与输入查询词逐字一致，无 OCR 那种字形误识）、4/4 点击进入会话、4/4 终态机器可验证。注意联系人B两次 x 不同（200→238）但都成功——模型每次基于当次截图独立定位，在结果项可点击区域内即有效，比固定坐标可靠。

   **优势**：语义理解而非字形识别（中文姓名、长群名都准）；返回结构化 JSON（label 可校验、found 可判空）；不依赖微信内部结构。**风险**：依赖外部 API（成本/延迟/网络）、推理模型延迟较高、仍需 P3 矩阵（DPI/主题/版本/重名）验证才能进 manifest。

   #### 5.3.2 实现约定（无论用哪个方案）

   1. 识别产物是**结构化数据**（坐标 + label + found），不是自由文本；
   2. 代码显式标注 `coordinate_space: 'png_relative'`，传入 `SetCursorPos` 前统一 `+window_rect` 归一化到 `physical_screen`；
   3. "目标可机器验证"断言：label 与查询词一致 + 点击后 overlay HWND 消失；
   4. P3 稳定性矩阵通过才进 manifest。

   ### 5.4 点击执行

   `SetCursorPos(px_screen_x, px_screen_y)` + `mouse_event(LEFTDOWN/LEFTUP)`，按下前后均查前台守卫，down/up 防撕裂（`finally` 中释放）。


6. **终态验证**。
   - 点击后重新枚举顶层窗口，搜索覆盖层 `Qt51514QWindowToolSaveBits`（Weixin 进程）**已消失** → 搜索面板关闭。
   - 点击后截图：会话内容区已切换到目标群聊（用户肉眼确认进入）。
   - `PROBE_RESULT: CLICK_DONE`。

## 4. 结论

| 假设子项 | 结论 |
|---|---|
| 主窗口可唯一识别并激活 | ✅ 成立 |
| `Ctrl+F` 打开搜索 | ✅ 成立（搜索框自动聚焦，无需额外定位） |
| 逐字输入中文查询 | ⚠️ **条件成立** — 必须用剪贴板逐字粘贴，`KEYEVENTF_UNICODE` 无效 |
| 群聊/联系人结果可用 UIA 识别 | ❌ **不成立** — Qt 渲染，UIA 不暴露结果项（见 §5） |
| 结果项可用 Kimi k3 视觉定位 | ✅ **成立（4/4）** — label 准确 + 坐标精准 + 机器可验证（见 §5.3.1） |
| 点击进入会话 | ✅ 成立（PNG 坐标 + window_rect 换算屏幕坐标，物理点击） |
| 进入会话终态可验证 | ✅ 成立（覆盖层 HWND 消失） |

**整体：P1 导航目标达成，且目标定位已用 Kimi k3 视觉方案达到机器可验证（结构化 JSON 坐标 + label 校验 + overlay 终态）。** 满足设计 §7.3 门禁第 1 条。**下一步是 P3 稳定性矩阵**（DPI/主题/版本/重名），通过后才进 manifest。会话模型目测方案已否定，PaddleOCR 方案未完全否定（坐标链路通，字形误识，作降级），百度 OCR 仓库无实现。

## 5. 后续决策（晋级 P3 / 正式 tool 前必须解决）

1. **稳定性矩阵（P3）**：当前只验证 4 个查询、若干次、1 个 DPI（2560×1440）、1 个微信版本（4.1.12.26）、浅色主题。P3 要求只读 ≥98% 成功率，需扩展到 100%/125%/150% DPI、深色主题、版本升级候选。Kimi 视觉对外部 API 有依赖（网络/成本/延迟），矩阵测试需同时统计 API 成功率。
2. **重名消歧**：本实验查询词均唯一匹配。正式 `weixin_chat_search`（设计 §5.3）要求重名时返回 `TARGET_AMBIGUOUS` 或候选 refs，不自动选第一个。当前 prompt 只返回"最匹配的一个"，需扩展为返回候选列表 + 让调用方/人工确认。
3. **中文逐字输入**：`KEYEVENTF_UNICODE` 对 Qt 搜索框无效，已用剪贴板逐字粘贴（保存/恢复）。正式 tool 需评估剪贴板占用对用户体验的影响，或探索 `SendInput` 的其他中文输入路径（不依赖 IME）。
4. **定位方案降级链**：Kimi 视觉（首选）→ PaddleOCR bbox + 模糊匹配（降级，字形误识需兜底）→ 百度 OCR（**需先实现**，当前仓库只有空配置）。正式 tool 应在 Kimi API 不可用时自动降级，并在 result 里标注用了哪条链。

## 6. 复用与隔离边界

- 本 probe 的 `probe-lib.ps1` 中窗口识别、激活、DPI、安全按键、剪贴板保存/恢复代码**一次性复制自** `clients/association-client-cli/scripts/wechat-souyisou-lib.ps1` 和 `wechat-souyisou.ps1`，在 weixin-cli 内独立维护。未 import、未链接、未修改协会客户端任何文件（设计 §2.1、§9）。
- 实验产物（截图、UIA dump）只落 `%TEMP%`，不进仓库；`experiments/` 不进正式 MCP tool 注册表和签名发布包（设计 §3.3、§10.3）。
- 本 probe **不触发** P2 测试目标白名单（未发送任何消息，纯导航）。

## 7. 不提交的产物清单

以下文件在 `%TEMP%`，**不提交到仓库**：
- `weixin-probe-p1-uia.json`（搜索态 UIA dump）
- `weixin-probe-p1-uia-after-click.json`（点击后 UIA dump，DryRun 路径产生）
- `weixin-probe-p1-search.png`（搜索结果截图，含真实群名）
- `weixin-probe-p1-after-click.png`（点击后截图，含真实会话内容）
