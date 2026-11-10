# WeCom CLI OCR 使用点清单（2026-11-06）

## 范围与状态

本表按 2026-11-06 的九组原使用场景追溯。**W27 当前源码与隔离验包结论**：添加客户 CLI/MCP/operation 已退役，旧搜索/读取/未读 OCR 驱动、OCR Python 文件及共享 OCR helper 已删除；当前企微隔离签名包不含 OCR 资产或 add-customer 编译残留。本地库改用包内独立 `localdb-python`，合成加密 SQLite 快照已由包内驱动解密并读回。微信 Provider 的 OCR 路径保持。W26 仍无真实 101/102 样本或媒体真机验收；W27 独立 TEST/CR 已通过，尚未正式发行或部署。

| 序号 | 业务入口 | OCR 当前作用 | 已有非 OCR 通道 / 失败影响 | 源码 |
|---|---|---|---|---|
| 1 | 文本发送 `wecom_message_send` | **W24 源码已移除发送链 OCR**：自会话 GLM 双点标定并复制标题核对，搜索默认结果直接 Enter，目标标题复制核对，后验只查本地库 | 本机双点标定与缓存复用单聊发送 exit 0，本地库命中唯一出站记录；其他 OCR 入口仍待处理 | [message-send.ps1](../../clients/wecom-cli/drivers/ps1/message-send.ps1)、[messageSend.ts](../../clients/wecom-cli/src/operations/messageSend.ts) |
| 2 | 搜索框状态（共享） | **W22 源码已移除该门禁**：Ctrl+F 成功后固定 Ctrl+A/Delete 清空；搜索词输入后完整剪贴板回读，不再 OCR 判空、残留或清空结果 | Ctrl+F 失败即中止且不清聊天草稿；全文复制不匹配即中止。合成测试与独立 CR 已通过，待真机验收 | [_common.ps1](../../clients/wecom-cli/drivers/ps1/_common.ps1)（旧 OCR helper 已删除） |
| 3 | 搜索会话 `wecom_chat_search` | **W25 源码已移除 OCR**：本地库群名、联系人名精确优先/包含匹配，唯一目标才签发账号绑定 ref；无命中给近似名称，多命中给候选 | 搜索本身不操作 UI；旧 `chat-search.ps1` 已删除 | [chatSearch.ts](../../clients/wecom-cli/src/operations/chatSearch.ts) |
| 4 | 进入会话 `wecom_chat_select` | **W25 源码已移除 OCR**：复查唯一名称，Ctrl+F 输入全名 Enter，复制缓存点标题全等核对 | 进入会话仍会切换视图并清未读角标；标题不符拒绝报告成功 | [chat-select.ps1](../../clients/wecom-cli/drivers/ps1/chat-select.ps1) |
| 5 | 读取会话 `wecom_read_session` 的旧 UI 路径 | **已退役**：旧 `target_name/ref` 只用本地库，失败报原码；显式 UI 报不支持 | 稳定 `session`/日期读取原已是本地库；旧无账号 ref 拒绝，不靠 UI 兜底 | [readSession.ts](../../clients/wecom-cli/src/operations/readSession.ts) |
| 6 | 未读列表 `wecom_unread_list` 与监控 UI 兜底 | **已退役**：只用本地库；监控失败轮保留水位 | doctor 未设或 UI 兼容态也尝试受实际版本矩阵约束的本地库；失败明确报错 | [unreadList.ts](../../clients/wecom-cli/src/operations/unreadList.ts)、[watchPoll.ts](../../clients/wecom-cli/src/operations/watchPoll.ts) |
| 7 | 发图片、发文件 | **W26 源码已移除发送链 OCR/Jev**：双点缓存、标题复制、剪贴板粘贴、Enter 后本地库核验 | 图片唯一 101 只证明客户端有图片记录，不证明原字节；文件唯一同名 102 证明客户端有同名文件记录。无真实媒体库样本与真机验收 | [send-image.ps1](../../clients/wecom-cli/drivers/ps1/send-image.ps1)、[send-file.ps1](../../clients/wecom-cli/drivers/ps1/send-file.ps1) |
| 8 | 添加客户 | **W27 入口和驱动已退役** | 新 CLI/MCP 不再提供添加客户；旧已交付包不受本轮影响 | 已删除 add-customer CLI/operation/PS/Python |
| 9 | 登录探测 `wecom_probe` | **源码已移除 OCR 调用**；窗口枚举判断登录态，二维码截图供扫码，`qr_status_hint` 固定为 `unknown` | 窗口信息不能证明二维码是否过期或受限；现行探测不再依赖 OCR，待真机复验 | [probe.ps1](../../clients/wecom-cli/drivers/ps1/probe.ps1)、[localdb-login.ps1](../../clients/wecom-cli/drivers/ps1/localdb-login.ps1) |

## W27 发行资产边界

- 新企微运行路径没有 OCR 调用；`drivers/py` 仅保留 `wecom_localdb.py` 与 `python_bootstrap.py`。独立 `localdb-python` 包含 Python、pycryptodome、tzdata 与 app-local CRT，签包和离线验包验证 AES/SQLite/IANA。
- 当前工作区隔离测试包内 4,640 个文件，`localdb-python/` 1,032 个文件；未发现 OCR 模型/库、已删 PS 驱动或旧 `dist/src/operations/addCustomer.js`。合成 8,192 字节加密 SQLite 经包内驱动解密后查询成功。
- `weixin` 仍使用原 `ocr-python` 和 OCR 业务能力；旧客户机签名包原样保留。

## 与本次故障直接相关

- 17:02 的文本发送：服务端收到旧 OCR 后验 `0/3`、`EXECUTION_UNKNOWN`；0.2.8 发送状态门与稳定会话读取准入不一致。W20 未提交改动已修稳定 `session` 不回退 OCR 后验，但还未独立 CodeReview、打包和真机验证。
- 17:10、17:11 的文本发送：发送前标题被读为目标群名后多一个 `+`，误判非目标并转搜索，最终 `UI_CHANGED/effect=none`，没有输入或发送。单纯忽略独立 `+` token 的候选修复可能误认真实名称含 ` +` 的群，已撤回；此处仍待研究。
- 对当前会话身份的只读探测：本机企业微信主窗口未通过 Windows UI Automation/Win32 子窗口文本暴露会话标题；本地 `session.db` 的会话表未见显式“当前打开会话 ID”列。`status`、`flag` 和键值表是否记录活动会话，尚未完成切换 A→B→A 对照，不能当成已验证替代方案。不扫描进程内存。

## 讨论顺序

先讨论第 1 项文本发送的“发送前确认目标会话”及“发送后确认结果”，再讨论会话搜索与进入，随后处理读取/未读、媒体、添加客户和登录提示。每项选择方案前区分 **目标身份**、**UI 实际选中目标** 和 **发送是否落库** 三个不同事实；本地库能证明前者与后者，不能仅凭目标会话存在证明 UI 当前显示的就是它。
