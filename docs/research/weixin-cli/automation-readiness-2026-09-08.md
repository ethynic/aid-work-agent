# weixin-cli 场景编排与无人值守就绪度调研

日期：2026-09-08。范围：当前仓库 `clients/weixin-cli`（用户所称 wexin CLI）、后台调度、Local Tool Runtime、微信营销第一场景。本文为源码核查，不代表当前真机验收通过。

关联：[产品与架构设计](../../design/weixin/weixin-marketing-automation-design.md) · [技术实现与开发计划](../../plans/weixin/plan-weixin-marketing-automation.md)。

## 1. 结论

现有 CLI 是可复用的 Windows 微信执行基础，但尚不能直接承诺无人值守定时群发送。应新增微信营销业务编排层，复用现有后台进程、数据库和本地工具通道；先加固目标身份、写动作状态与恢复语义，再接时间触发和图片发送。

固定内容在发布时冻结，执行时直接调用确定性流程。模型仅参与自然语言配置解析及当前 CLI 必需的视觉定位/校验；“不重新调用聊天 Agent”不等于“完全不消耗模型”。

## 2. 核实方法与本次环境

- 逐项阅读 TypeScript operation、MCP schema、PowerShell driver、target ref、Runtime claim/result 和 scheduler executor 实现。
- 当前工作目录位于 macOS；发现 `/Applications/WeChat.app`；PATH 未发现 `aid-weixin` 或 `prlctl`，本工程没有现成 `dist/src/cli/index.js` 和本地 TypeScript 依赖。没有发现已配置的 Windows 执行入口。这不能证明用户没有其他 Windows 设备。
- 当前 CLI 的 `probeEnvironment` 只接受 `process.platform === win32`，业务 driver 使用 `powershell.exe`、Win32 窗口和 Windows 微信；微信 Mac 版已打开也不能满足该前提。
- 本次未执行 CLI 真机发送，未发送文字、图片或链接，未创建真实定时任务，未运行离线测试；不将历史报告的通过数作为本轮结果。
- 用户已授权必要时向「哈尼」群发送一段测试文字或图片或链接；该授权不是向其他群发送、连续压力发送或启用永久营销任务的授权。实际测试仍须在运行设备上唯一定位此群。

## 3. 当前能力矩阵

| 能力 | 源码现状 | 场景影响 |
|---|---|---|
| CLI / MCP | `aid-weixin`，package 版本 0.1.0；operation 共用实现，MCP stdio | 可由程序调用，无须 shell 串命令 |
| 环境探测 | `weixin_probe`；doctor 检查平台、SESSIONNAME、PowerShell、进程和目录 | 进程存在不代表登录、账号正确或未锁屏；doctor 还会创建并删除目录探针文件 |
| 搜索好友/群 | `weixin_chat_search(query,type,limit)`，最多 20 项，视觉结果带 ref | 可用于选群；非永久群 ID，类型带视觉推断性质 |
| 文字发送 | `weixin_message_send(target_ref,text)`，1 个目标、1 条、单行、JS length ≤500 | 中文基本字符通常按 1 计；部分 emoji 占 2 个 UTF-16 代码单元；前后端须同一验证规则 |
| 链接 | 没有独立链接发送工具；URL 可作为 text | 仅承诺文本网址；原生标题/封面卡片没有实现，也不能保证自动展开 |
| 图片/文件 | 当前 toolDefs 和 messageSend 都没有此参数 | 必须新增图片导入、发送和验证能力；不能把图片路径当成发图 |
| 聊天读取 | `weixin_history_read`，max_pages 1..10，内联最多 200 条 | 会打开聊天并清未读；不适合作为无副作用监听器 |
| 未读列表 | `weixin_unread_list` 读当前会话列表 | 不是完整消息流，无稳定消息 ID、持久游标或实时推送保证 |
| 搜一搜/文章/关注 | 旧设计列有路线图；当前 toolDefs 未注册 | 本场景不能把路线图当已上线能力 |
| 云端接入 | `src/local_tools/catalog.py` 与 Runtime 内置 manifest 当前是 BOSS | 微信尚未完成该通道注册和 Provider 选择，不能只新增一个服务端工具名 |

## 4. 无人值守关键缺口（事实与推断分开）

### 4.1 目标引用不等于稳定身份

事实：`src/platform/targetRef.ts` payload 为 `{v:1,name,type,exp}`，本机 HMAC，TTL 300 秒；没有账号、进程、会话 epoch 或目标指纹。payload 是 base64url 编码，可解码出名称，不是加密 opaque handle。本地 key 以 base64 文件保存，Windows 下 mode 不能替代完整 ACL/DPAPI 保障。

事实：`messageSend.ts` 验证 ref 后只将 `target.name` 交给驱动。`drivers/ps1/_common.ps1::Open-WeixinChat` 使用视觉 best match，并以标题 `-like '*目标名称*'` 核对；未要求唯一 exact group，也没有证明同名目标消歧。

推断：即使搜索时用户点选了候选，发送时也可能按名称重新选择不同候选；不能把短期 ref 存一个月后直接发送，也不能靠重新搜索名称解决所有身份问题。

要求：长期绑定设备＋已验证微信账号＋群身份依据；每次发送前刷新短期引用并复验。UI 无法提供可靠区分依据时，应拒绝无人值守激活，允许用户为群设置可区分名称后重新绑定；不能虚构稳定微信内部群 ID。

### 4.2 “最后一条文字相等”不能证明本轮发送成功

事实：`message-send.ps1` 回车后截图，视觉返回 `sent_ok` 与 `last_message`，未显式要求本轮新增、自方气泡、发送失败图标消失、输入框已清空。每日固定内容正好会重复。

推断：昨天或上一次的相同消息可能被误认为本次成功。需要发送前后的消息区域增量证据；不能偷偷给用户正文添加追踪码来规避问题。图片不能以字节 hash 与微信缩略图直接比较，因为微信可能压缩重编码。

### 4.3 写动作异常有多个层次，必须端到端保留 unknown

事实：messageSend 对超时归 `EXECUTION_UNKNOWN`、取消归 unknown；但 PowerShell 非零退出或缺少 DRIVER_JSON 会归 `INTERNAL_ERROR`，没有完整提交边界记录。Runtime 的取消/关闭分支在缺少 settled 结果时可回 `effect=none`，关闭分支带 retryable=true。云端 `write_result` 主要按 success 与 code 分类，不能假定任意 `effect=unknown` 都会归 unknown。

事实：Runtime 结果回传在网络故障时重试，但当前没有可见的持久结果 outbox；进程退出可丢结果。旧租约过期后云端会归 unknown，迟到 result 对终态行直接返回原状态。

要求：driver、Provider、Runtime、DB、业务执行器统一“提交前 / 提交可能开始 / 已验证”证据；提交后结果不明不重发，只补传回执、对账或人工核查。数据库幂等不能保证微信 exactly-once。

### 4.4 现有定时任务入口不适合直接套用

事实：`src/tools/scheduler/scheduled_task_tool.py::CreateScheduledTaskTool` 创建前调用 `ScheduledTaskExecutor.dry_run()`，而 dry_run 内会真实调用 `agent.process_message_sync`；不是无副作用预览。正式 execute 也执行自然语言任务，异常有线程重试逻辑。

要求：本场景使用自己的配置、预检、发布和执行服务；preview 不发送，试发是独立显式动作。不让通用 create_scheduled_task 承接微信后台自动发送，也不复用其整轮异常重试。

### 4.5 复用点与真实改造量

- `src/background_runner.py` 已有独立后台进程；`src/scheduler/manager.py` 已有 APScheduler 与周期系统任务，可加业务 tick。
- `src/local_tools/repository.py` 已有租户设备、queued/claimed/running、claim token、租约、取消与结果记录；复用传输，补充业务关联与写前授权。
- `clients/agent-tool-runtime` 的 config、ProviderManager、manifestVerifier、deviceCapabilities 均有 BOSS 定制，不是现成的通用多 Provider Host。
- 当前调度器 Redis 获取锁异常会继续启动；营销触发唯一性必须依靠 DB 事务与唯一键，不能只依赖该进程锁或 APScheduler max_instances。
- 当前日志/driver 参数可包含群名、正文及视觉回读文字；截图使用 TEMP 固定文件名。营销接入须落实受控临时目录、日志脱敏、stdin 输入、最小截图范围和留存策略。

## 5. Mac 支持的选择

建议首期复用已存在的 Windows 路线：后台配置页面可从 Mac 打开，实际执行在登录微信的 Windows 交互桌面上。若必须直接操作这台 Mac，需要在相同 operation 契约下新增 macOS driver，并分别验证辅助功能权限、选群、中文/emoji 输入、图片剪贴板和写后证据；不能仅改平台判断。Mac 支持是独立实施分支，不计入 Windows MVP 已有能力。

## 6. 外部资料核验

- APScheduler 3.x 提供 date、interval、cron；coalescing/misfire/max_instances 是调度策略，不能替代业务发送去重。[官方用户指南](https://apscheduler.readthedocs.io/en/3.x/userguide.html)
- PostgreSQL `FOR UPDATE SKIP LOCKED` 适用于多消费者队列式领取；本方案仅用于短事务抢占，不跨 RPA 操作持有行锁。[官方 SELECT 文档](https://www.postgresql.org/docs/current/sql-select.html)
- MCP 的 readOnlyHint/idempotentHint 是提示，不能作为真实副作用和重试授权的唯一依据。[官方 schema](https://modelcontextprotocol.io/specification/2025-11-25/schema)

本文未调查微信协议合规结论或宣称任何官方营销接口能力；对象是仓库自有桌面 CLI。线上可用性最终由目标设备、微信版本和实测证据决定。

## 7. 下一步验证顺序

1. Windows 设备具备 Node/发布包、登录微信、可交互桌面、视觉代理授权；确认 Runtime 和视觉代理属于同一租户。
2. 只读 doctor/probe，唯一识别账号和「哈尼」群；出现重名或无法判断即停。
3. 明确一次试发正文，刷新 ref，只发一次，人工核对与机器证据对照；unknown 不自动重发。
4. 单独开发图片 probe 后再做图片试发；时间调度、故障注入与稳定性矩阵优先使用 fake driver，真实多次发送另用专用测试群和明确范围。
5. 达成技术方案验收矩阵后，才开启无人值守；本次文档完成不代表上述步骤完成。
