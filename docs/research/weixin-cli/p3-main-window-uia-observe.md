# Probe p3-main-window-uia-observe：正常主窗口「会话列表 / 对话窗口」UIA 可见性

> 日期：2026-08-11
>
> 风险等级：**P1（导航）** — 仅激活窗口 + UIA 枚举 + 截图；零输入、零点击、零剪贴板改动、零会话切换。定义见 [probe-risk-and-whitelist.md](./probe-risk-and-whitelist.md)
>
> 关联：`clients/weixin-cli/experiments/probes/p3-main-window-uia-observe/probe.json`、`run.ps1`
>
> 前置：[p1-chat-search-group.md](./p1-chat-search-group.md) §5（搜索态 Qt UIA 不可用结论）

## 1. 假设

微信 4.x 正常主窗口（非搜索态）的「会话列表」与「当前会话对话窗口」两个核心区域，UIA 仍不暴露内部元素——与 p1 搜索态结论一致：Qt 把整个界面画成单个 `MMUIRenderSubWindowHW` 渲染窗口，UIA 只看到外壳。机器可验证终态：TreeWalker 深度枚举主窗口 UIA 树，若节点 `BoundingRectangle` 无法对应截图上两个区域的视觉边界，则元素定位必须走外部视觉识别。

## 2. 环境

| 项 | 值 |
|---|---|
| 操作系统 | Microsoft Windows 11 专业版 |
| 微信版本 | 4.1.12.26（`C:\Program Files\Tencent\Weixin\Weixin.exe`） |
| 主屏分辨率 | 2560 × 1440 |
| 主窗口类名 / 标题 | `Qt51514QWindowIcon` / `微信` |
| 主窗口 HWND | 9377842 |
| 主窗口 rect（物理屏幕坐标） | (289, 260, 1261, 1233)，972 × 973 |
| DPI 感知 | 线程固定 Per-Monitor V2（`SetThreadDpiAwarenessContext(-4)`） |

## 3. 步骤与证据

代码：`clients/weixin-cli/experiments/probes/p3-main-window-uia-observe/`
- `probe.json` — probe 元数据（假设、允许/禁止动作、成功标准、cleanup）
- `probe-lib.ps1` — Win32/UIA 底座（一次性复制自 p1，在 weixin-cli 内独立维护）
- `run.ps1` — 主脚本

执行流程：

1. **定位并激活主窗口**。`EnumWindows` + 三条件筛唯一主窗口（`Weixin.exe` 路径 + `MainWindowHwnd == Hwnd` + `Visible`），`Invoke-WeixinActivation` 三连激活，`Test-WeixinMainIdentity` 校验 class=`Qt51514QWindowIcon` + title=`微信`。
   - 证据：hwnd=9377842，身份校验通过。

2. **截图主窗口**。`GetWindowRect` → `CopyFromScreen` 截主窗口区域 PNG（非全屏），落 `%TEMP%\weixin-probe-p3-main.png`（含真实会话名/消息内容，不进仓库，不外传）。
   - 证据：972×973 PNG 生成成功。

3. **枚举微信所有相关可见 HWND**。遍历所有可见顶层窗口，按 `Weixin.exe` 路径过滤。
   - 证据：**仅 1 个**（主窗口本身）。无搜索覆盖层、无其它子窗口（正常态本应如此，对比 p1 搜索态有 2 个：主窗口 + `Qt51514QWindowToolSaveBits`）。

4. **TreeWalker 深度枚举**（`ControlViewWalker` 深度优先，上限 15000 节点/HWND，远未触及）。对每个节点记录 control_type / name / automation_id / class_name / BoundingRectangle / is_offscreen / 四种 Pattern（Invoke / Value / Scroll / Text）。

5. **统计与落盘**。每个 HWND 的完整树落 `%TEMP%\weixin-probe-p3-tree-{hwnd}.json`（含真实内容，不进仓库），控制台输出摘要。

## 4. 结果

### 4.1 主窗口 UIA 摘要

| 指标 | 值 |
|---|---|
| Weixin 相关可见 HWND 数 | **1** |
| 主窗口 UIA 总节点数 | **3** |
| 有名节点 | 3 |
| 可编辑（ValuePattern） | **0** |
| 可点击（InvokePattern） | **0** |
| 可滚动（ScrollPattern） | **0** |
| 主窗口 rect 内可见(>5px)节点 | **2** |

### 4.2 主窗口内仅有的 2 个可见节点

| control_type | name | class_name | rect (物理屏幕坐标) | invoke | value | scroll |
|---|---|---|---|---|---|---|
| Window | `微信` | `Qt51514QWindowIcon` | (289, 260) 972×973 | False | False | False |
| Pane | `MMUIRenderSubWindowHW` | `MMUIRenderSubWindowHW` | (297, 260) 956×965 | False | False | False |

第 3 个节点为 `Weixin`（不可见/零尺寸容器），不在主窗口可见区域内。

### 4.3 解读

- 整个微信主窗口在 UIA 里只有 **2 个可见节点**：窗口外壳（`Qt51514QWindowIcon`）+ 渲染画布（`MMUIRenderSubWindowHW`，956×965，几乎覆盖整个客户区）。
- **会话列表区域在 UIA 里完全不存在**：左侧那条会话列表（截图可见的几十个会话项）无任何对应 UIA 节点，全部画在 `MMUIRenderSubWindowHW` 渲染窗口内。
- **对话窗口区域在 UIA 里也完全不存在**：右侧消息流、输入框、发送按钮同样无任何 UIA 节点。
- **零 InvokePattern、零 ValuePattern、零 ScrollPattern**：连会话列表/消息区的滚动都无法用 UIA 驱动（无 ScrollPattern 节点）。

## 5. 结论

| 假设子项 | 结论 |
|---|---|
| 主窗口可唯一识别并激活 | ✅ 成立 |
| 会话列表区域可用 UIA 识别 | ❌ **不成立** — Qt 渲染，UIA 零暴露 |
| 对话窗口区域可用 UIA 识别 | ❌ **不成立** — Qt 渲染，UIA 零暴露 |
| UIA 可驱动滚动会话列表/消息区 | ❌ **不成立** — 零 ScrollPattern 节点 |

**整体：假设成立。** 微信 4.x 正常主窗口的 UIA 接口对自动化完全关闭——无论是会话列表还是对话窗口，UIA 都无法定位、无法点击、无法读取文本、无法滚动。这把 p1 §5 的结论（搜索态 Qt UIA 不可用）**推广到正常主窗口态的两个核心区域**。

对 weixin-cli 设计的直接含义：**「读取会话列表」「读取对话窗口内容」「滚动消息」「点击会话项」这类操作，UIA 路径全部不可用，必须一律走外部视觉识别（截图 + OCR / 视觉模型）+ 物理坐标点击**，与 p1 §5.3.1 已验证的 Kimi k3 视觉方案一致。

## 6. 与 p1 的关系

| 维度 | p1（搜索态） | p3（正常态，本次） |
|---|---|---|
| 场景 | Ctrl+F 打开搜索后 | 主窗口正常显示 |
| 可见 HWND 数 | 2（主窗口 + 搜索覆盖层） | 1（仅主窗口） |
| 主窗口 UIA 总节点 | 4 | 3（更精确枚举，含 Text Pattern 探测） |
| 可点击 / 可编辑 / 可滚动 | 0 / 0 / — | 0 / 0 / 0 |
| 结论 | 搜索结果项 UIA 不暴露 | **会话列表 + 对话窗口 UIA 均不暴露** |

p3 将「Qt UIA 不可用」从「搜索结果项」推广到「主界面全部核心交互区域」，是更强的否定结论。

## 7. 复用与隔离边界

- `probe-lib.ps1` 一次性复制自 p1（p1 又复制自协会客户端），在 weixin-cli 内独立维护。未 import、未链接、未修改协会客户端或 p1 任何文件（设计 §2.1、§9）。
- 实验产物（截图、UIA dump）只落 `%TEMP%`，不进仓库；`experiments/` 不进正式 MCP tool 注册表和签名发布包（设计 §3.3、§10.3）。
- 本 probe **不触发** P2 测试目标白名单（零输入零点击，纯观察）。

## 8. 不提交的产物清单

以下文件在 `%TEMP%`，**不提交到仓库**（含真实会话名/消息内容）：
- `weixin-probe-p3-main.png`（主窗口截图）
- `weixin-probe-p3-tree-9377842.json`（主窗口 UIA 完整树 dump）

## 9. 后续

本次只验证了「UIA 不可用」这一面。会话列表/对话窗口能否被**视觉方案**稳定识别（bbox 定位 + 内容读取），需另起 probe 验证（参考 p1 §5.3.1 Kimi k3 视觉方案，针对主窗口两个区域做只读识别，不点击）。该 probe 同样是 P1 级（只读识别，无写动作）。
