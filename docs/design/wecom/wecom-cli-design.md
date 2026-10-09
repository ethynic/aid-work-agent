# wecom-cli：第一方企业微信操作 CLI / MCP Provider 设计

> 状态：✅ 已实现并持续维护（2026-10-09 按当前代码整合撰写）
>
> 上位规范：[第一方 CLI / MCP Provider 架构与开发规范](../../system/first-party-cli-mcp-provider-standard.md)
>
> 姊妹实现：[weixin-cli 设计](../weixin/weixin-cli-design.md)（操作模式与契约来源）
>
> 本文档是 wecom-cli 的唯一现行架构设计文档，整合自已删除的真机探测与方案设计
> （2026-08-29）、RPA 取代差距分析与实验设计（2026-09-28）两份过程文档及 README
> 里程碑记录（过程明细见 git 历史；工程内 `clients/wecom-cli/README.md` 保留详细
> 真机实测叙事）。

## 1. 定位

`wecom-cli` 是项目第三个第一方 CLI：独立、可分发的 Windows MCP Provider
（provider_id `ai.aidwork.wecom`，可执行 `aid-wecom`，stdio transport，`local_required`，
平台 `win32-x64`），操作已登录的企业微信 Windows 客户端（WXWork.exe，5.0.9 系实测）。

与旧 RPA 的关系：**取代 `clients/wecom-personal-rpa` 的 UI 执行职责**——约 5600 行 C#
工程中真正操作企微的只有 ~770 行 PowerShell；wecom-cli 以 TypeScript Provider + 精简
驱动承接全部 UI 能力，C# 壳计划瘦身为调用本 CLI 的协议壳（对接尚未落地，见 §10）。
RPA 已于 2026-09-29 标记废弃（6d48163e：README/STATUS 横幅 + 前端渠道入口移除 +
激活码管理越权收口）。服务端会话归档栈（`src/channels/wecom_personal_rpa/archive/`）
不属于本 CLI 职责。

## 2. 工程形态与命令面

- Node.js ≥22 + TypeScript（ESM）；依赖仅 `@modelcontextprotocol/sdk` + `zod`；
  `npm run build / typecheck`，`npm test` = build + node:test 递归（全 mock）。
- CLI 动词 14 个：`probe / search / select / send / send-image / send-file / unread /
  read-session / watch / add-customer / mcp / doctor / version / help`。
- MCP 工具 10 个（`src/mcp/toolDefs.ts` + `src/operations/registry.ts`，CLI 与 MCP 共用
  同一 operation）：

| tool | 作用 | 写语义 |
|------|------|--------|
| `wecom_probe` | 平台/交互会话/PowerShell/WXWork 进程/主窗口/登录态三态（online/need_login/offline，need_login 附二维码 PNG base64） | 只读 |
| `wecom_chat_search` | 搜索联系人/群，返回 best/items + 带坐标 target_ref | 只读 |
| `wecom_chat_select` | 点击搜索结果进会话 | 半写（清角标） |
| `wecom_read_session` | 读会话消息，双通道路由（§5） | 半写（清角标） |
| `wecom_watch_poll` | 新消息跟踪单轮，水位 diff 只返增量 | 半写 |
| `wecom_unread_list` | 未读会话快照 | 只读 |
| `wecom_message_send` | 文本 ≤2000 字（多行走剪贴板） | 写 |
| `wecom_send_image` | png/jpg/jpeg/bmp/gif ≤20MB | 写 |
| `wecom_send_file` | 任意扩展 ≤100MB，文件名去空白 ≥3 字符 | 写 |
| `wecom_add_customer` | 按手机号检索并发添加邀请（confirm 显式 true；M12 网络查找直达路线） | 写 |

- 智能分发（M11a）：send/send-image/send-file/read-session 支持 `target_ref`/
  `target_name` 二选一；驱动返回 `navigate_required=true` 时 TS 层（`src/operations/
  navigate.ts`）编排内部 chatSearch+chatSelect 后二次调用；`resolveTargetByName`
  走 Jev best 身份校验 → 唯一匹配回退 → 歧义 fail-closed（`TARGET_AMBIGUOUS`）。

## 3. 架构分层与操作契约

```text
CLI 动词 / MCP stdio ──> operations（10 个，统一 OperationResult，永不 reject）
                          │  effect ∈ none|applied|partial|unknown，20+ 稳定错误码
                          ▼
        platform（powershell 驱动执行器 / targetRef HMAC / watchState 水位 / serverProxy / namedMutex）
                          │  spawn powershell，stdout 末行 DRIVER_JSON: {ok,data|code,message}
                          ▼
        drivers/ps1（_common.ps1 底座 + 9 动作驱动，Win32 P/Invoke）+ drivers/py（RapidOCR）
```

- 驱动错误码白名单映射；默认超时 300s（read-session 600s）；业务失败 ok=false 且退出码 0。
- 并发控制：进程内单飞 + 跨进程命名管道互斥 `\\.\pipe\AidWorkAgent.AidWecom.<scope>`，
  占用返回 `BUSY` 结构化结果。
- `target_ref`：HMAC 签名短期句柄（5 分钟 TTL），绑定目标指纹；写动作不接受模糊裸名。
- manifest schema digest 按上位规范 sha256 序列化（`src/mcp/manifest.ts`）。

## 4. Win32 交互机制（真机裁定的事实）

- **一切交互只走 PostMessage**：企微 5.0.9 丢弃 SendInput/keybd_event 注入（键盘+鼠标，
  2026-08-30 复核），代码层禁用；组合键（Ctrl+F/Ctrl+A/Ctrl+V）经 AttachThreadInput
  attachstate 发送。
- **截图**：`PrintWindow(hwnd, hdc, 2)`（PW_RENDERFULLCONTENT）——前台/后台/被遮挡/
  最小化均出完整画面（9 点采样全黑/白回退 CopyFromScreen），CLI 可完全后台运行。
- **输入规则**：无修饰 ASCII 走 WM_KEYDOWN（带 scan code）；Shift 修饰字符/中文走
  WM_CHAR；两条路径不可混发（Qt 会双倍字符）；posted 修饰键无效（Qt 读真实键盘状态，
  Ctrl+V 落成字面 `v`）→ 多行文本/图片/文件经剪贴板粘贴（Clipboard.SetImage/
  SetFileDropList，覆盖不恢复）。
- **滚动**：PostMessage WM_MOUSEWHEEL（delta<<16）。
- **窗口拓扑**：主外壳 `WeWorkWindow` + 每功能页子 HWND（类名 `WXworkWindow - 企业微信-
  <页名>` 编码页名）；**2026-09-29 客户端更新后子窗口消失**，页面校验改 OCR 动态定位
  （导航项 OCR 找"通讯录"等）；弹窗 `SearchExternalsWnd`（添加客户 400x292）/
  `InputReasonWnd`（发送邀请）类名稳定。PostMessage 点击必须投递到拥有目标坐标的
  HWND（WindowFromPoint 判定），hwnd 动态变化每次重新解析。
- **感知与决策**：RapidOCR（仓库根 venv，未装 → `CONFIG_MISSING`）读文字；未读角标
  用红色像素 blob 检测 + 5x 放大 OCR；决策层 Jev（TypeSafe System One 类型化决策，
  `Invoke-WeComJev`，缺 key/失败逐级降级到规则链）。视觉模型坐标定位（weixin-cli 的
  KimiVision 链路）未引入本 CLI。
- **已知启发式弱点**：消息 side（左右）判定仍为启发式，代码注释自认不可靠（§10 遗留）。

## 5. read_session 双通道路由（M10）

`serverUrlConfigured(env)` 检查 `AID_WECOM_SERVER_URL`：

- **未配置 → 本地 OCR 通道**：驱动 `-ParseMode ocr`（channel="ocr"，免费）。
- **已配置 → 服务端模型通道**：驱动 `-ParseMode none` 只截图 → TS 读 page-*.png 转
  base64（反转旧→新，单图 ≤5MB）→ `parseSessionHistory` 上传
  `POST {AID_WECOM_SERVER_URL}/api/client/v1/session-history`（channel="model"，
  透传 model_usage/billing）。
- **失败分类**（`serverProxy.ts` ProxyError.kind）：`unavailable`（网络/超时/5xx/422/
  截图缺失超限）→ 二次调驱动 OCR 兜底并记 fallback_reason；`config`（401/缺 token）与
  `insufficient_credit`（402）→ 不降级直报。
- **watch 轮询内的 read-session 不走计费通道**（缺省 ocr，维持纯本地）。

服务端实现 `src/api/client_routes.py:603`：GLM-5.3-Flash 多模态并行分页
（asyncio.gather）+ 页间重叠去重合并；计费 `_session_history_credit_cost` =
`ceil(token 成本(元) × credit_multiplier × 100)/100`（Decimal 防浮点），不足最低值按
最低收（口径"成本×100 积分、最低 1 积分/次"），余额不足 402 阻断。

## 6. 服务端协作与鉴权

- 唯一服务端接口 `/api/client/v1/session-history`，`Authorization: Bearer
  <AID_WECOM_SERVER_TOKEN>`；超时 150s；单图 ≤5MB、1..10 张。
- 鉴权为 **M10c 直连长期静态 token**：服务端 `POST /api/saas/client-bindings/static`
  签发（token_type='static' 跳过过期检查）；无激活流程、无本地缓存（激活码链路与
  DPAPI server-binding.json 已移除）；401 判 config 直报不重试。
- Runtime 集成（M11b）：`clients/agent-tool-runtime` 的 productTrust 白名单含
  `ai.aidwork.wecom`，Host 将 `AID_WECOM_SERVER_URL/TOKEN` 注入 provider 进程环境
  （runtimeHost.ts）。

## 7. add-customer 路线（M12 重标定后现行）

旧通讯录路线因 2026-09-29 客户端更新失效（④添加按钮 CEF 渲染 + 注入过滤，PostMessage/
mouse_event 均无效）。现行**网络查找直达路线**（全程 PostMessage 零真实点击）：

Ctrl+F 聚焦搜索框 → 清残留 → WM_CHAR 输入手机号（回读 contains 校验）→ 搜索 overlay
出现「网络查找手机号/邮箱：<号>」行（同行拼接校验防点错）→ 点击该行（投 overlay
hwnd）→ `SearchExternalsWnd` 自动填号（校验 + 手输回退）→ Enter 检索 → 结果行
（微信名 + 添加）→ `InputReasonWnd` → 发送 → 终态「已发送申请」轮询。弹窗段 M1 代码
逐行保留。

防御要点：陈旧弹窗在关键点击前关闭并等消失（关不掉 fail-closed，防向旧号码误发，
CR P1 修复）；finally 清搜索框手机号残留（隐私）；OCR 调用 Process 直启（规避 MCP
stdio 白名单 CantActivateDocumentInPipeline）。

## 8. 消息读取与 watch 水位

- read_session：滚动截图 + OCR + 页间重叠去重（移植 weixin-cli 算法，企微气泡布局
  独立调校）；时间分割线整行锚定（前缀匹配会误吞真实消息，CR P1 修复）；消息文本
  归一化三侧一致（py/ps1/TS：去空白 + 全角转半角 + 小写折叠）。
- watch_poll：unread 快照 → 与本地 watchState 水位 diff → 候选会话进会话读增量；
  进会话即清角标（固有副作用），读取成功后 last_unread 归零（否则角标小于旧水位的
  新消息漏报，CR P1 修复）；首读无水位保守全推（设计行为）。

## 9. 测试与交付

- 21 个测试文件、209 个 node:test 用例（全 mock，不触真实企微窗口）：operations
  契约（30+18）、CLI 命令（17）、双通道路由与降级（12+9）、server-proxy 错误分类（12）、
  target_name 直达（12）、send-file/image（10+8）、targetRef（8）、manifest（6）等。
- 真机验证：M2-M12 各里程碑真机实测（README「关键实现事实」含 2026-09-04/09-28/
  09-29 三次客户端更新重标定记录）；M12 时 13 命令全部真机验证通过。
- 交付形态：Runtime 插件 Host 分发（productTrust 白名单 + 环境注入）。

## 10. 演进与遗留

里程碑：M1 probe+add-customer → M2 search+send → M3 unread/read/watch → M4 搜索重构
（attachstate+Jev+坐标句柄）→ M5 select → M6 send 智能分发 → M7 send-image →
M8 send-file → M9 read→read-session 改名+智能分发 → M10a/b/c 双通道+直连静态 token →
M11a/b target_name 直达 + Runtime 注册 → M12 add-customer 网络查找重写（13 命令全通关）
→ E5 probe 登录态二维码。

遗留（未做项，均为增强非阻塞）：

1. **常驻/远程 OCR 迁移**：weixin-cli 的 ocr_server.py 常驻进程方案未引入，仍每次
   拉起 python；
2. **协议壳对接**：wecom-personal-rpa 的 C# 壳未瘦身为调用本 CLI 的壳
   （wecom-ops.ps1 无 aid-wecom 引用）——RPA 废弃后的最终清退步骤；
3. 消息 side 判定仍为启发式（不可靠，待决策模型化）；
4. probe 的 need_login 分支待真机复验（开发机已登录未实测登出场景）；
5. `dist/` 编译产物时间戳落后 src（2026-09-28 后未重打包）；package.json description
   仍写 "M1 骨架"。
