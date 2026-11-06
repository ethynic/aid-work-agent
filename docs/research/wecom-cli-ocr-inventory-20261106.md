# WeCom CLI OCR 使用点清单（2026-11-06）

## 范围与状态

基于 WeCom CLI 0.2.8 源码逐个检查实际 OCR 调用；按讨论目的整理为 **9 组原使用场景**。`localdb-login.ps1` 仅在注释中提到 OCR，实际只枚举窗口，不调用 OCR。`wecom_read_sessions`、`wecom_session_directory`、群目录/群轮询等稳定本地库读取没有 OCR 路径。W21/W22/W24/W25 分别去除读取、搜索框清理、文本发送、会话搜索与进入的 OCR。2026-11-07 W26 源码将图片/文件发送改为双点缓存、剪贴板粘贴和本地库 101/102 唯一记录核验，第 7 项不再运行 OCR/Jev；尚无真实 101/102 样本或媒体真机验收。旧 `chat-search.ps1` 脚本仍在仓库但 operation 不派发。当前仍运行 OCR 的业务入口仅添加客户。W25 独立测试、三例本机真机验收及 CR 通过，未部署；W26 尚待独立 TEST/CR。

| 序号 | 业务入口 | OCR 当前作用 | 已有非 OCR 通道 / 失败影响 | 源码 |
|---|---|---|---|---|
| 1 | 文本发送 `wecom_message_send` | **W24 源码已移除发送链 OCR**：自会话 GLM 双点标定并复制标题核对，搜索默认结果直接 Enter，目标标题复制核对，后验只查本地库 | 本机双点标定与缓存复用单聊发送 exit 0，本地库命中唯一出站记录；其他 OCR 入口仍待处理 | [message-send.ps1](../../clients/wecom-cli/drivers/ps1/message-send.ps1)、[messageSend.ts](../../clients/wecom-cli/src/operations/messageSend.ts) |
| 2 | 搜索框状态（共享） | **W22 源码已移除该门禁**：Ctrl+F 成功后固定 Ctrl+A/Delete 清空；搜索词输入后完整剪贴板回读，不再 OCR 判空、残留或清空结果 | Ctrl+F 失败即中止且不清聊天草稿；全文复制不匹配即中止。合成测试与独立 CR 已通过，待真机验收 | [_common.ps1](../../clients/wecom-cli/drivers/ps1/_common.ps1)、[chat-search.ps1](../../clients/wecom-cli/drivers/ps1/chat-search.ps1)、[add-customer.ps1](../../clients/wecom-cli/drivers/ps1/add-customer.ps1) |
| 3 | 搜索会话 `wecom_chat_search` | **W25 源码已移除 OCR**：本地库群名、联系人名精确优先/包含匹配，唯一目标才签发账号绑定 ref；无命中给近似名称，多命中给候选 | 搜索本身不操作 UI；旧 `chat-search.ps1` 已不由 operation 派发，待清理 | [chatSearch.ts](../../clients/wecom-cli/src/operations/chatSearch.ts) |
| 4 | 进入会话 `wecom_chat_select` | **W25 源码已移除 OCR**：复查唯一名称，Ctrl+F 输入全名 Enter，复制缓存点标题全等核对 | 进入会话仍会切换视图并清未读角标；标题不符拒绝报告成功 | [chat-select.ps1](../../clients/wecom-cli/drivers/ps1/chat-select.ps1) |
| 5 | 读取会话 `wecom_read_session` 的旧 UI 路径 | **已退役**：旧 `target_name/ref` 只用本地库，失败报原码；显式 UI 报不支持 | 稳定 `session`/日期读取原已是本地库；旧无账号 ref 拒绝，不靠 UI 兜底 | [readSession.ts](../../clients/wecom-cli/src/operations/readSession.ts) |
| 6 | 未读列表 `wecom_unread_list` 与监控 UI 兜底 | **已退役**：只用本地库；监控失败轮保留水位 | doctor 未设或 UI 兼容态也尝试受实际版本矩阵约束的本地库；失败明确报错 | [unreadList.ts](../../clients/wecom-cli/src/operations/unreadList.ts)、[watchPoll.ts](../../clients/wecom-cli/src/operations/watchPoll.ts) |
| 7 | 发图片、发文件 | **W26 源码已移除发送链 OCR/Jev**：双点缓存、标题复制、剪贴板粘贴、Enter 后本地库核验 | 图片唯一 101 只证明客户端有图片记录，不证明原字节；文件唯一同名 102 证明客户端有同名文件记录。无真实媒体库样本与真机验收 | [send-image.ps1](../../clients/wecom-cli/drivers/ps1/send-image.ps1)、[send-file.ps1](../../clients/wecom-cli/drivers/ps1/send-file.ps1) |
| 8 | 添加客户 | 搜索框、候选、确认对话框及结果/失败原因 | 这是 UI 写操作，现有本地库通道不承担添加客户；OCR 影响候选选择和结果判断 | [add-customer.ps1](../../clients/wecom-cli/drivers/ps1/add-customer.ps1) |
| 9 | 登录探测 `wecom_probe` | **源码已移除 OCR 调用**；窗口枚举判断登录态，二维码截图供扫码，`qr_status_hint` 固定为 `unknown` | 窗口信息不能证明二维码是否过期或受限；现行探测不再依赖 OCR，待真机复验 | [probe.ps1](../../clients/wecom-cli/drivers/ps1/probe.ps1)、[localdb-login.ps1](../../clients/wecom-cli/drivers/ps1/localdb-login.ps1) |

## 剩余依赖的层次

- **仍会运行 OCR 的业务入口**：第 8 项添加客户，使用 `chat_ocr.py` 与 `add_customer_result.py`；W26 媒体发送入口不再调用共享 `Invoke-WeComChatOcr`。
- **已退役但源码仍含 OCR**：`drivers/ps1/chat-search.ps1`、`read-session.ps1` 与 `unread-list.ps1` 仍直接调用 OCR，当前同名 TS operation 已不再派发它们；`_common.ps1` 还保留部分旧 OCR 搜索框助手。删除这些文件或函数前须核对发行包与手动调用入口。
- **发行包依赖**：`clients/runtime-plugin-packaging/package.mjs` 仍为 `wecom` 设置 `ocr: true`，拷入 `ocr-python` 并执行 RapidOCR 启动检查。`wecom-cli/src/platform/localdb/driver.ts` 在发行态也使用该目录下的 `python.exe` 运行本地库解密和查询。因此文本发送虽不调用 OCR 识别，现有发行包仍依赖这份带 OCR 的 Python 资产；移除它之前需给本地库提供可用的 Python 运行环境，再调整打包检查。

## 与本次故障直接相关

- 17:02 的文本发送：服务端收到旧 OCR 后验 `0/3`、`EXECUTION_UNKNOWN`；0.2.8 发送状态门与稳定会话读取准入不一致。W20 未提交改动已修稳定 `session` 不回退 OCR 后验，但还未独立 CodeReview、打包和真机验证。
- 17:10、17:11 的文本发送：发送前标题被读为目标群名后多一个 `+`，误判非目标并转搜索，最终 `UI_CHANGED/effect=none`，没有输入或发送。单纯忽略独立 `+` token 的候选修复可能误认真实名称含 ` +` 的群，已撤回；此处仍待研究。
- 对当前会话身份的只读探测：本机企业微信主窗口未通过 Windows UI Automation/Win32 子窗口文本暴露会话标题；本地 `session.db` 的会话表未见显式“当前打开会话 ID”列。`status`、`flag` 和键值表是否记录活动会话，尚未完成切换 A→B→A 对照，不能当成已验证替代方案。不扫描进程内存。

## 讨论顺序

先讨论第 1 项文本发送的“发送前确认目标会话”及“发送后确认结果”，再讨论会话搜索与进入，随后处理读取/未读、媒体、添加客户和登录提示。每项选择方案前区分 **目标身份**、**UI 实际选中目标** 和 **发送是否落库** 三个不同事实；本地库能证明前者与后者，不能仅凭目标会话存在证明 UI 当前显示的就是它。
