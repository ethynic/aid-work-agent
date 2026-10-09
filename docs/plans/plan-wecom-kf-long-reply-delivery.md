# 微信客服长答复 MD 交付开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| 方案调研 | 代码链路与渠道职责核对 | ✅ 完成（2026-10-09） | 已确认标准 MD、默认纯文本前缀与仅正文图片长图策略 |
| Phase 1 | 渠道交付准备与文件登记 | ✅ 完成（2026-10-09） | 标准 MD、可信下载登记与历史投影；真实存储/Redis wrapper 组合验证通过 |
| Phase 2 | 原文前缀/模型摘要、长图、原生 MD 及预算 | ✅ 完成（2026-10-09） | 默认零模型调用、500 字符与现有字节双约束；可选摘要计费及取消结算、交付期间 lease 续持 |
| Phase 3 | 独立测试与 CodeReview | ✅ 完成（2026-10-09） | 独立定向/组合 113 passed，入口失主补修独立 4 passed，最终 CR 通过；回归跳过 7 项有明确环境条件 |
| Phase 4 | 真机验收 | 📋 待开发 | 未部署；实际部署必须另有用户当场明确指示 |

关联：[调研](../research/wecom-kf-long-reply-delivery-research.md)、[现有渠道设计 §十四](../channel/wecom_kf/wecom_kf_design.md#十四2026-10-09-长答复交付调整)、[原配额方案](../channel/wecom_kf/reply_quota_control_plan.md)。

## 实施原则

- 不改原任务 Agent 提示词、模型循环、工具装配、Runner wire schema、Device API 或 Runtime Host；渠道默认确定性前缀，仅 llm 模式新增无工具的正文摘要调用。
- 本计划是渠道既有长答复策略的调整；不新增渠道专用接单、持久任务或投递服务。
- 完整答复指现有 `sanitize_llm_markdown` 处理后的最终可见正文，不含 reasoning、工具消息或隐藏提示词。
- 摘要触发沿现有长度判断：`len(markdown_to_plain_text(text).encode('utf-8')) > adapter._max_bytes`，默认阈值 2048 字节，保留配置覆盖。500 字符不是触发阈值；表格本身也不触发模型总结。
- 超过现有阈值时默认“纯文本原文前缀 + 标准 MD”，明确切换 llm 后才使用模型摘要；只有正文真实图片才考虑“长图 + MD”，文字与长图正常二选一。未超阈值的普通回复直发；短表格通过 MD 与固定附件提示展示，不调用摘要模型、不转图。
- 生成后的摘要/原文前缀连同省略号及附件提示，Unicode 字符数不超过 500；不把此输出约束施加为原答复是否需要总结的判断。固定说明只作前缀不可用时的最后兜底。
- 摘要模型调用须实际计价扣费；输出是直接回答同一用户的精简回复，保持原答复语言、称谓和语气，不转为第三方文档摘要。
- 增加渠道策略 `summary_mode=prefix|llm`，默认 prefix，先观察算法效果，后续可明确切换为 llm；prefix 不可用时固定提示，不自动调用模型。不新增后台配置页面；两种方式沿同一超长触发、文件和发送路径。

## Phase 1：交付准备

1. 在 KF 渠道模块沿现有字节判断决定是否超长：`len(markdown_to_plain_text(text).encode('utf-8')) > adapter._max_bytes`（默认 2048）。未超阈值不因超过 500 字或含表格调用摘要模型；短表格仅做 MD 文件及固定提示交付。图片策略独立判定，排除代码示例与独立 ImageRef。
2. 为 `ChannelSessionManager.process_and_persist` 增加可选的交付准备入口，仅 KF 路由传入；位置为成功结果净化后、最终 assistant metadata 与 batch 构造前。其缺省路径保持现状。
3. 区分历史全文、规范化 MD 与发送文字：完整正文按 CommonMark 基础及 GFM 表格的常用子集归一化后写成 UTF-8 .md，注册 text/markdown；保留表格、URL、代码和全部业务内容，不让模型重写全文。校验真实表格渲染、内容完整性及未误包整篇代码围栏，不能只改扩展名。先验证已有 Markdown 库与 tables 扩展的相关规范样例；不要求支持所有 GFM 扩展。
4. 文件存入可信租户 conversation 目录并登记已有 file_id/下载元数据；若复用 cp 的底层注册能力，只提取必要原语并保持 cp 行为，不从渠道直接执行工具。
5. 自动 MD 加入当前 assistant 的 downloadableFiles；区分它与既有业务 MD，设置仅渠道内部使用的用途/优先级，避免发送所有 MD 都改成新策略。MD 内部图片/文件引用只通过既有授权资源映射处理，不泄露路径或留下冒充可用的 file_id 链接。
6. 不在共享 adapter 上保存本轮 reply/预算；所有数据按 owner 传递。取消/合并 follower、转人工抑制与持久化失败路径沿原队列语义处理，准备产生的未引用文件按既有清理机制处理。

## Phase 2：发送与预算

1. 自动生成 MD 优先使用原生 `upload_media(file) → send_msg(file)`；上传支持业务文件名和正确 MIME，保持其他调用方兼容。
2. 仅现有长度判断超阈值且未选择长图的正文选择文字呈现：默认 prefix 直接运行算法，不能先调用模型，算法不可用时用固定提示。明确切换 llm 后调用统一 LLM 网关 `chat_no_thinking`，保持原回答语言/称谓/语气，直接回应问题，最终纯文本加提示不超过 500 字符；模型失败、超时、超限或不合规时转原文前缀。完整规则见设计 §14.4/14.4.1。
3. 只有正文真实图片才考虑复用长图渲染，不额外生成摘要；表格、代码中的图片示例、独立 ImageRef 不触发长图。图片不可读、渲染/上传明确失败可以按 summary_mode 转文字，发送未知不自动补发。必要时拆分现有长图方法的准备/发送结果，保留原调用方兼容。
4. KF owner budget 在 verbose 关闭时也存在；verbose 预留呈现消息和 MD 两次。长图末尾嵌入查看 MD 的提示，不另占文字消息；额外独立图片及其他文件使用剩余额度。
5. 摘要用量由渠道记录并实际收费，与 ASR 共用既有记录原语；设置实际摘要模型/provider、输入/输出及缓存 token，修订 `skip_save` 条件，使新增摘要参与既有计价、chat_records 落库和租户余额扣减。只计算本地摘要/ASR，不重复计算 Runner 原任务；按实际模型既有单价及取整规则结算。记录关联到可信 tenant/user/session/owner，失败、取消、摘要未被采用或消息未送达但已有模型 usage 的调用仍计入实际消耗；未调用模型不产生摘要费用。验证单价存在、账务实际写入与扣费；计费异常如实登记，不把 token 日志当成已收费，不盲目重放未知扣费。
6. 上传明确失败但本地文件注册成功时，说明文件暂无法直接发送，并以既有下载入口降级；发送结果未知不自动补发。生成/注册失败用有界失败说明，不宣称附件存在。
7. 提示使用“请查看附件”，不提前声称“已送达”；记录 API 接受、拒绝、未知与部分失败事实，沿既有返回值影响 send_ok/recap，不新增全局消息账本。

8. 实现无模型前缀算法：源正文按顺序转换为无表格纯文本（表格展开为字段和值），保留段落/列表；先预留省略号和实际附件提示字符，预算内取正文开头，优先靠后的完整段落/句子/列表边界，无边界则按 Unicode 字符截取。只有真正截断才加 `…`，最后追加“完整回复请查看发送的文件《详细答复.md》。”并校验整条不超过 500 字符；空正文/转换失败才用固定提示。保留历史全文与 MD，不从失败模型摘要截取。

## Phase 3：验证矩阵

| 意图 | 用例与预期 |
|---|---|
| 摘要触发沿现有阈值 | 默认 2048 字节不总结、2049 字节才进入超长处理；配置 max_bytes 覆盖仍生效；501 个 ASCII 字符、600 汉字（1800 字节）及 2030 字节原回复均不因摘要输出限制而总结 |
| 表格不触发总结 | 短表格交付 MD 与固定提示，不调用摘要/不转图；超长表格默认前缀，只有显式 llm 模式才调用摘要；仅独立图片不生成多余 MD |
| 摘要字符上限 | 中英 emoji、换行、列表及附件提示均计入；生成后摘要的 500/501 字符边界单独验证，不用于原回复触发判断；发送侧沿用现有平台字节限制 |
| 真正 Markdown 文件 | UTF-8、.md、text/markdown；GFM 样例表格解析为真实表格节点/HTML 表格，包含转义竖线、对齐及代码段示例；不将整篇包为代码块 |
| 全文不丢失 | 无需归一化的正文逐字核对；归一化表格逐单元格核对，链接与代码内容一致，不删除溢出列/行或以摘要替代全文 |
| 策略二选一 | 超过现有阈值的普通长文及表格按 summary_mode 呈现且不渲染长图；正文真实图片才考虑长图，代码图片示例排除；独立图片仍发送 |
| 模型摘要兜底 | 显式 llm 模式下失败/超时/空输出/格式不符/超限转原文前缀，而非直接固定提示；MD 不变，无无限重试 |
| 前缀算法确定性 | 输出确为源正文顺序前缀投影；500 字符包含省略号、空白及附件提示；断句/段落/列表，无边界长句、emoji、URL/小数点、开头表格、全表格、代码示例均覆盖；截断有省略号，未截断不伪造省略 |
| 默认前缀与显式切换 | 未配置时采用 prefix，摘要模型零调用/零费用；前缀失败也不调用模型，使用固定提示；显式 llm 正常调用，已产生 usage 再回退仍计费；两种模式均保留完整 MD，文件不存在不宣称可查看 |
| 回答风格保持一致 | 代表性中文/其他语言、正式/自然客服答复，摘要沿用称谓、人称、语气，直接回答问题而非评论原文；数字、限制和不确定性不被改义；提示契约测试结合代表性输出人工检查，不把模型语义质量当确定性保证 |
| 附件有历史 | 持久化的正文保留全文，metadata 含自动 MD，下载可读；其他渠道默认准备入口行为不变 |
| 副作用边界正确 | 转人工、合并 follower、取消与被取代结果不交付旧 MD；记录/发送失败不触发成功 recap |
| 预算保护完整交付 | verbose 开关、摘要两条、长图含附件提示的实际条数；MD 优先于多图多文件，预算不足明确部分失败 |
| 新增费用准确 | 纯文本摘要不被 skip_save 漏记；校验实际输入/输出/缓存 token、实际模型计价、chat_records 与余额变化，不重复算 Runner；失败/未采用但有 usage 的摘要仍计费，无模型调用则无摘要费；单价缺失、账务失败及未知扣费不假报结算成功 |
| 文件及身份安全 | 可信 tenant 路径、其他租户/不存在/过期引用拒绝；业务显示名正确，无路径暴露 |
| 失败诚实可控 | 保存/注册/上传失败、API 拒绝、发送超时；无假成功和未知后盲目补发 |
| 注册幂等与兼容 | 同一交付准备重入不重复附件；既有 cp 文件注册行为及文件发送测试通过 |

按 `.claude/rules/dev_workflow.md` 评估风险并执行独立测试/CR；仅运行受影响验证与必要启动检查，保留 skipped/未覆盖说明。文档阶段不启动三智能体开发流程。

## Phase 4：真机验收与交接

核对微信官方最新 text/file 限制，检查目标主 API/Runner 共用文件存储与 Redis；用户明确部署授权后才能部署。真实微信客户端验收文件名、中文编码、可打开性、全文、图片和平台错误事件。未执行时明确登记未验收，不能以 mock 测试替代。

开发已完成，设计与进度已同步，条目移至 ideas_finished；本轮未提交、推送或部署。Phase 4 仍待实际验收。

## 本次实施记录（2026-10-09）

- KF 路由成功结果进入 `prepare_reply`；会话持久化前追加完整 MD 和 `channelDelivery` 投影，历史正文继续保留原始完整可见答复。其他渠道不传准备回调。
- 新的 `reply_format.py` 使用既有 Markdown 库进行结构校验和纯文本投影；表格超出可判定列数时明确文件准备失败，不静默丢列。图片示例代码不进入长图判断；文件只含可交付 URL，未授权/不可映射图片保留 alt 与不可显示说明。
- 默认渠道配置 `summary_mode=prefix`；以后在原 KF 渠道 config JSON 设置 `summary_mode: llm` 可显式切换。该配置由现有 ChannelFactory 透传到 adapter，不新增全局 Settings/YAML 节点或后台页面。
- 可选模型调用 `chat_no_thinking`，在现有 physical call observer 记录实际 provider/model/usage，启用渠道 record 落库；未调用不记摘要费，已消费但不采用的返回仍计费。模型和微信客户端风格/文件表现尚无真实接口验收。
- MD 先完成上传准备，客户可见顺序仍为“文字/长图 → 原生 MD → 剩余图片/文件”；verbose 为最终交付预留 2。发送传输异常返回未知且不自动重试，不以接口接受等同最终送达。
- 自测命令：宿主 venv + Git Bash `./scripts/dev_test.sh tests/unit/channels/test_wecom_kf_reply_delivery.py tests/unit/channels/test_wecom_kf_reply_format.py tests/unit/channels/test_wecom_kf_adapter.py tests/unit/channels/test_wecom_kf_reply_budget.py tests/unit/channels/test_session_manager_persist.py tests/unit/channels/test_wecom_kf_waiting_indicator.py tests/unit/tools/test_cp_tool.py tests/unit/tools/test_cp_tool_work_outcome.py -p no:cacheprovider -q --tb=short`，192 passed / 3 skipped（Windows 对应 POSIX 源路径用例），5 条既有 Pydantic 弃用警告。

### 独立验证及修复

- CR 初审发现 1 项 P1（准备期 ownership 过期）和 4 项 P2（取消漏结算、缺价调用、短代码图片误判、摘要投影为空），独立测试另发现自定义 1024 字节下预览缺失。统一修复后源码复核关闭全部问题；Redis 原子续期不复活旧锁，不清后继 owner。
- 独立定向与组合复验：`./scripts/dev_test.sh tests/integration/test_wecom_kf_reply_delivery.py tests/unit/channels/test_wecom_kf_reply_delivery.py tests/unit/channels/test_wecom_kf_reply_format.py tests/unit/channels/test_session_manager_persist.py -p no:cacheprovider -q --tb=short`，113 passed / 0 failed / 0 skipped。含 13 项常驻组合用例：真实路由、实际 LLMGateway 物理调用、独立 CommonMark 表格解析、存储与 Redis wrapper、失主/取消/过期锁以及已消费摘要取消结算。
- 扩大回归在上述自测命令后追加 `tests/unit/test_session_queue.py tests/unit/test_redis_client.py tests/integration/test_wecom_kf_reply_delivery.py`：285 passed / 7 skipped，唯一失败为集成测试价格替身误用字段，独立复验已用真实字段纠正并通过。7 项跳过为 3 项 Windows 不适用 POSIX 路径及 4 项需要真实 Redis 服务；不得将它们称作已通过。
- route 集成用例实际导入配置、渠道路由与 Runner 契约，外部 DB/API/Runner 边界替身隔离；未启动真实业务服务。真实微信 MD 可打开性、外部摘要风格、真实扣费余额及 Redis Lua 服务仍待授权部署后的验收。

- 最终入口资源收尾补修：首次 ownership 校验失败也取消并等待独立 verbose worker、关闭 state，不准备、不落库、不发 final；开发受影响回归 `test_session_manager_persist.py + test_verbose_dispatcher.py + tests/integration/test_wecom_kf_reply_delivery.py` 68 passed，独立仅复验新增入口用例与 3 项 finalization 用例 4 passed / 0 failed / 0 skipped / 10 deselected。最终独立 CR 通过，全部已报告问题关闭。
- `requirements-test.txt` 显式登记 `markdown-it-py>=4.0.0`，独立 CommonMark 表格测试不依赖宿主机偶然已有的传递依赖。

- 用户授权提交后已 fetch 两个远程，合并 Codeup 单项部署说明更新，无源码冲突。主控提交前关键导入与回归：`./scripts/dev_test.sh tests/integration/test_wecom_kf_reply_delivery.py tests/unit/channels/test_session_manager_persist.py -p no:cacheprovider -q --tb=short`，38 passed / 0 failed / 0 skipped；未部署 agent2。
