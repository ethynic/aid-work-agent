# M0：旧 PowerShell 冻结协议样本

> 日期：2026-08-11
>
> 阶段：M0，对应 [开发计划](../../../plans/weixin/plan-weixin-cli.md) §2「冻结旧 ps1 stdin/stdout、错误码、artifact 和 cleanup 行为样本」
>
> 冻结源：`clients/wechat-souyisou-rpa/scripts/wechat-souyisou.ps1` + `wechat-souyisou-lib.ps1`（与协会客户端同名文件字节一致）
>
> 用途：M2 独立 driver 的行为等价回归基线。weixin-cli driver 不必逐字段兼容本协议（会去除协会业务字段），但**安全语义**（错误码分级、cleanup 契约、脱敏、单行 JSON）必须等价。

## 1. 命令面

```text
wechat-souyisou.ps1 -Command probe|open|search|collect
  [-AssociationName <str>] [-PersonName <str>]
  [-InputJson <json> | -ReadStdin]
  [-Execute]                       # 缺省 dry-run，不操作微信
  [-Limit 1..10 =3]
  [-WaitMilliseconds 500..30000 =2500]
  [-SearchReadyTimeoutMilliseconds 10000..60000 =15000]
  [-VerifyInputOnly]               # 仅 search/collect + -Execute；验证输入但不提交
  [-JudgeCommand <exe>] [-UseProjectLlm] [-CliExe <exe>]
  [-OcrCommand <exe>] [-DisableOcr]
  [-ArtifactDirectory <path> = %LOCALAPPDATA%\AidWorkAgent\wechat-souyisou-rpa\artifacts]
```

### stdin JSON（`-ReadStdin`，UTF-8）

```json
{
  "command": "search",
  "association_name": "中国游艺设备游乐园协会",
  "person_name": "王承展",
  "verify_input_only": false,
  "limit": 3,
  "search_ready_timeout_milliseconds": 15000
}
```

- `limit` 超界被钳到 10；`search_ready_timeout_milliseconds` 越界或非数字 → `INVALID_SEARCH_READY_TIMEOUT`
- 未知 command → `INVALID_COMMAND`；`verify_input_only` 组合非法 → `INVALID_INPUT_PROBE_MODE`

## 2. stdout 样本（始终单行 JSON；退出码 0=成功/业务终态，1=失败）

dry-run：

```json
{"ok":true,"executed":false,"mode":"dry_run","command":"search","query_present":true,"limit":3}
```

probe：

```json
{"ok":true,"executed":true,"command":"probe","window_found":true,"hwnd":131334}
```

open（按命令语义不自动关闭）：

```json
{"ok":true,"executed":true,"command":"open"}
```

VerifyInputOnly：

```json
{"ok":true,"executed":true,"input_verified":true,"submitted":false,"session_closed":true}
```

search 成功：

```json
{"ok":true,"executed":true,"status":"captured","source":"result_page_unbounded","artifact_ref":"C:\\...\\<guid>.dpapi","link_count":2,"session_closed":true}
```

collect 业务终态（`status ∈ found|not_found|inconclusive`，均 `ok:true`）：

```json
{"ok":true,"executed":true,"status":"inconclusive","checked":4,"failures":1,"artifact_ref":"C:\\...\\<guid>.dpapi","session_closed":true}
```

失败（`exit 1`；`message` 已经过手机号脱敏且 ≤160 字符）：

```json
{"ok":false,"executed":true,"error_code":"FOREGROUND_LOST","message":"...","session_closed":false,"artifact_ref":"C:\\...\\<guid>.dpapi","stage":"click","result_status":null}
```

关键不变量：

- 会创建临时插件窗口的终态必带 `session_closed`；cleanup 失败 → `SESSION_CLEANUP_FAILED`（`ok:false`），调用方必须停止批处理
- stdout 永不包含手机号、剪贴板原文、完整进程路径；手机号一律以 `1**********` 脱敏
- 业务 `inconclusive` 也是 `ok:true` + 加密 artifact，不是进程失败

## 3. 稳定错误码全集（按类别）

| 类别 | 错误码 |
|---|---|
| 输入 | `INVALID_COMMAND` / `INVALID_INPUT` / `INVALID_LIMIT` / `INVALID_INPUT_PROBE_MODE` / `INVALID_SEARCH_READY_TIMEOUT` |
| 单飞 | `RPA_BUSY`（命名 Mutex `Local\AidWorkAgent.WechatSouyisouRpa`，`WaitOne(0)` 非阻塞抢占） |
| 环境/DPI | `DPI_AWARENESS_FAILED` / `DPI_AWARENESS_INVALID` / `WINDOW_DPI_INVALID` / `UIA_CLICK_POINT_INVALID` |
| 窗口 | `WX_WINDOW_NOT_FOUND` / `WX_WINDOW_AMBIGUOUS` / `WX_ACTIVATION_FAILED` / `SOUYISOU_WINDOW_UNTRUSTED` / `WINDOW_RECT_FAILED` |
| 前台/安全 | `FOREGROUND_LOST` / `INPUT_FOCUS_LOST` / `PREEXISTING_PLUGIN_REJECTED` / `ALLOW_PREEXISTING_INVALID` |
| 输入验证 | `SEARCH_INPUT_READBACK_MISMATCH` / `SEARCH_INPUT_FOCUS_FAILED` / `SEARCHBOX_NOT_FOUND` / `SEARCHBOX_VALUE_PATTERN_UNAVAILABLE` / `UIA_ROOT_UNAVAILABLE` / `UIA_TARGET_TERMS_INVALID` |
| 输入设备 | `KEY_RELEASE_FAILED:*` / `MOUSE_POSITION_FAILED` / `MOUSE_RELEASE_FAILED:*` |
| 剪贴板 | `CLIPBOARD_CAPTURE_FAILED` / `CLIPBOARD_RESTORE_FAILED` |
| 恢复 | `RECOVERY_FAILED` / `SESSION_LIST_WINDOW_MISSING` |
| cleanup 细分 | `PLUGIN_IDENTITY_INVALID` / `PLUGIN_CLOSE_REJECTED` / `PLUGIN_CLOSE_TIMEOUT` / `MAIN_WINDOW_MISSING` / `MAIN_WINDOW_UNTRUSTED` / `MAIN_ACTIVATION_FAILED` / `MAIN_FOREGROUND_NOT_RESTORED` → 顶层 `SESSION_CLEANUP_FAILED` |
| 超时 | `WECHAT_WORK_TIMEOUT`（9min 业务预算 + 1min cleanup 预留） |
| 外部判定/OCR | `JUDGE_START_FAILED` / `JUDGE_TIMEOUT` / `JUDGE_PROCESS_FAILED` / `JUDGE_OUTPUT_EMPTY` / `OCR_FAILED` / `OCR_ATTRIBUTION_FAILED` |
| artifact | `ARTIFACT_PROTECTION_FAILED:*` |
| 临时文件 | `TEMP_CLEANUP_FAILED` |
| 兜底 | `RPA_FAILED`（任何非 `^[A-Z][A-Z0-9_]+$` 的异常消息被压平为此码） |
| 调用方层（Python provider，非 ps1） | `WECHAT_HANDOFF_FAILED` |

约定：抛 `throw 'SOME_CODE'` 形式的纯大写码会被原样透传；其余异常一律压平为兜底码，防止内部信息泄漏到 stdout。

## 4. artifact 行为样本

- 路径：`{ArtifactDirectory}\{guid:N}.dpapi`，内容为 DPAPI CurrentUser 加密的 UTF-8 JSON；`artifact_ref` 返回完整路径，`artifact_id` 为 guid
- 三种 `kind`：
  - `result_page_unbounded`：结果页整页文本 + CF_HTML links + `input_verified` + `input_method`；明确「无边界」供审计区分
  - `collect_result`：`status/checked/failures/list_artifact_id/records[]/list_judge_status/found_result/llm_usages`
  - `failure`：`error_code/stage/cleanup_error_code/detail_may_be_open/input_verified/readback_matched/foreground{hwnd,process_basename,class_name}/window_geometry/viewport_hash/click_diagnostics`
- 脱敏边界：failure artifact 不含完整进程路径、query 原文、剪贴板文本、OCR 文本（VerifyInputOnly 路径）；普通日志（`%TEMP%\wechat_diag.log`）当前**会**记 person/assoc——weixin-cli 版本须按设计 §8 改为只记 hash
- cleanup 失败不覆盖既有 artifact；读取用 `read-artifact.ps1`（DPAPI 解密打印）

## 5. cleanup 行为样本（`Close-WeixinPluginSession` 语义）

1. 插件 HWND 仍存在：先复核完整身份（路径+类名+标题+非主窗口），身份不符 → `PLUGIN_IDENTITY_INVALID`
2. 可见插件：`SendMessageTimeout(WM_CLOSE, 2000ms)`，拒绝 → `PLUGIN_CLOSE_REJECTED`；随后 ≤50×100ms 轮询，`IsWindow=false` 算关闭、变隐藏转第 3 步、超时 → `PLUGIN_CLOSE_TIMEOUT`
3. 隐藏插件：只校验身份，**不重新激活**
4. 主窗口必须存在且可见（`MAIN_WINDOW_MISSING`）、身份可信（`MAIN_WINDOW_UNTRUSTED`）、可激活（`MAIN_ACTIVATION_FAILED`）、前台恢复（`MAIN_FOREGROUND_NOT_RESTORED`）
5. 每会话 cleanup 恰好一次（`sessionCleanupAttempted` 守卫）；失败后 finally 不重发关闭键
6. 多 owned HWND 时先关 detail 后关 list（list 排序最后）

## 6. 会话交接（调用方协议）

批量查询由外层 Python provider 管理：上一条 `ok=true 且 session_closed=true` 后，下一条启动前发一次 `Alt+Tab` + 等 1000ms；首条/上一条未启动/清理不确定时不发送；交接失败 `WECHAT_HANDOFF_FAILED` 并停止批次。详见 `docs/tools/wechat-rpa-session-handoff.md`。weixin-cli 的对应语义由 operation 层重新设计，不逐字继承。
