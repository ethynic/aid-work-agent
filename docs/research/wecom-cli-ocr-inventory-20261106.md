# WeCom CLI OCR 使用点清单（2026-11-06）

## 范围与状态

基于 WeCom CLI 0.2.8 源码逐个检查实际 OCR 调用；按讨论目的整理为 **9 组原使用场景**。`localdb-login.ps1` 仅在注释中提到 OCR，实际只枚举窗口，不调用 OCR。`wecom_read_sessions`、`wecom_session_directory`、群目录/群轮询等稳定本地库读取没有 OCR 路径。2026-11-06 W21 源码调整后，第 5、6 项 operation 已停止调用 OCR 驱动；W22 源码调整后，第 2 项搜索框准备/清理不再调用搜索框状态 OCR；W24 源码调整后，第 1 项文本发送全链路不再调用 OCR。独立搜索/选择、媒体发送和添加客户仍可能使用 OCR。W24 独立测试与 CR 已通过，本机自会话双点标定已成功并落盘；真实目标搜索与文本发送尚未验收。

| 序号 | 业务入口 | OCR 当前作用 | 已有非 OCR 通道 / 失败影响 | 源码 |
|---|---|---|---|---|
| 1 | 文本发送 `wecom_message_send` | **W24 源码已移除发送链 OCR**：自会话 GLM 双点标定并复制标题核对，搜索默认结果直接 Enter，目标标题复制核对，后验只查本地库 | 本机自会话标定 exit 0，schema 2 双点缓存与返回值一致；输入点未独立验证焦点。有效缓存无需 GLM/OCR；真实目标搜索与文本发送待验收 | [message-send.ps1](../../clients/wecom-cli/drivers/ps1/message-send.ps1)、[messageSend.ts](../../clients/wecom-cli/src/operations/messageSend.ts) |
| 2 | 搜索框状态（共享） | **W22 源码已移除该门禁**：Ctrl+F 成功后固定 Ctrl+A/Delete 清空；搜索词输入后完整剪贴板回读，不再 OCR 判空、残留或清空结果 | Ctrl+F 失败即中止且不清聊天草稿；全文复制不匹配即中止。合成测试与独立 CR 已通过，待真机验收 | [_common.ps1](../../clients/wecom-cli/drivers/ps1/_common.ps1)、[chat-search.ps1](../../clients/wecom-cli/drivers/ps1/chat-search.ps1)、[add-customer.ps1](../../clients/wecom-cli/drivers/ps1/add-customer.ps1) |
| 3 | 搜索会话 `wecom_chat_search` | 识别搜索结果名称、分区、位置，必要时让 Jev 选最佳项 | 已有本地库稳定会话 ID 和显示名，但 UI 搜索结果的具体点击位置仍从视觉获得；误读可能找不到候选 | [chat-search.ps1](../../clients/wecom-cli/drivers/ps1/chat-search.ps1) |
| 4 | 进入会话 `wecom_chat_select` | 点击前重读搜索结果项和坐标；点击后读标题确认已进入目标 | 本地库能确认目标 ID/名称，当前没有已验证的“正在显示哪个会话 ID”来源；误读会拒绝进入或把标题验证报错 | [chat-select.ps1](../../clients/wecom-cli/drivers/ps1/chat-select.ps1) |
| 5 | 读取会话 `wecom_read_session` 的旧 UI 路径 | **已退役**：旧 `target_name/ref` 只用本地库，失败报原码；显式 UI 报不支持 | 稳定 `session`/日期读取原已是本地库；旧无账号 ref 拒绝，不靠 UI 兜底 | [readSession.ts](../../clients/wecom-cli/src/operations/readSession.ts) |
| 6 | 未读列表 `wecom_unread_list` 与监控 UI 兜底 | **已退役**：只用本地库；监控失败轮保留水位 | doctor 未设或 UI 兼容态也尝试受实际版本矩阵约束的本地库；失败明确报错 | [unreadList.ts](../../clients/wecom-cli/src/operations/unreadList.ts)、[watchPoll.ts](../../clients/wecom-cli/src/operations/watchPoll.ts) |
| 7 | 发图片、发文件 | 发送前标题、输入区/预览、发送后气泡或列表结果；含 Jev 可选判断 | 当前仍是 UI/OCR 发送链，未接入文本的本地库发送回执；误读可能拒发或得到不确定结果 | [send-image.ps1](../../clients/wecom-cli/drivers/ps1/send-image.ps1)、[send-file.ps1](../../clients/wecom-cli/drivers/ps1/send-file.ps1) |
| 8 | 添加客户 | 搜索框、候选、确认对话框及结果/失败原因 | 这是 UI 写操作，现有本地库通道不承担添加客户；OCR 影响候选选择和结果判断 | [add-customer.ps1](../../clients/wecom-cli/drivers/ps1/add-customer.ps1) |
| 9 | 登录探测 `wecom_probe` | **源码已移除 OCR 调用**；窗口枚举判断登录态，二维码截图供扫码，`qr_status_hint` 固定为 `unknown` | 窗口信息不能证明二维码是否过期或受限；现行探测不再依赖 OCR，待真机复验 | [probe.ps1](../../clients/wecom-cli/drivers/ps1/probe.ps1)、[localdb-login.ps1](../../clients/wecom-cli/drivers/ps1/localdb-login.ps1) |

## 与本次故障直接相关

- 17:02 的文本发送：服务端收到旧 OCR 后验 `0/3`、`EXECUTION_UNKNOWN`；0.2.8 发送状态门与稳定会话读取准入不一致。W20 未提交改动已修稳定 `session` 不回退 OCR 后验，但还未独立 CodeReview、打包和真机验证。
- 17:10、17:11 的文本发送：发送前标题被读为目标群名后多一个 `+`，误判非目标并转搜索，最终 `UI_CHANGED/effect=none`，没有输入或发送。单纯忽略独立 `+` token 的候选修复可能误认真实名称含 ` +` 的群，已撤回；此处仍待研究。
- 对当前会话身份的只读探测：本机企业微信主窗口未通过 Windows UI Automation/Win32 子窗口文本暴露会话标题；本地 `session.db` 的会话表未见显式“当前打开会话 ID”列。`status`、`flag` 和键值表是否记录活动会话，尚未完成切换 A→B→A 对照，不能当成已验证替代方案。不扫描进程内存。

## 讨论顺序

先讨论第 1 项文本发送的“发送前确认目标会话”及“发送后确认结果”，再讨论会话搜索与进入，随后处理读取/未读、媒体、添加客户和登录提示。每项选择方案前区分 **目标身份**、**UI 实际选中目标** 和 **发送是否落库** 三个不同事实；本地库能证明前者与后者，不能仅凭目标会话存在证明 UI 当前显示的就是它。
