# 开发计划：wecom CLI 群聊实验与输入链路修复

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|---|---|---|---|
| 基线 | 当前工作树CLI构建、账号/群定位与增量读取 | ✅ 完成（2026-10-23） | 本地库读取正常；无私有账号ID或群消息正文入库 |
| 修复 | 搜索/编辑区定位与输入链路最小修复 | ✅ 完成（2026-10-26） | 完整搜索、发布后凭证、个人标记兼容及OLE空读恢复闭环 |
| 验证 | 独立测试、独立CR及主控真机验收 | ✅ 完成（2026-10-26） | 最终独立策略/空读23项、三PS入口及CR通过；只搜索连续两轮与原文恢复通过；历史发送见下文 |
| 实验 | 持续轮询、@本人必回、其他消息择机回复 | ✅ 完成（2026-10-23） | 用户要求暂停后发送结束招呼并退出；累计590轮、8条确认发送，无pending |
| 搜索收尾 | 完整搜索与剪贴板恢复（2026-10-26） | ✅ 完成（2026-10-26） | 最终当前源码双轮CLI搜索/全文复制/原文及个人状态/草稿/清理核对通过；未提交、部署或恢复监听 |
| 发送对账 | W4严格核验与重启后只读恢复 | ✅ 完成（2026-10-26） | 独测TS49/恢复7/Python78通过，CR无遗留；仅CLI，无新真实发送验收 |
| MCP/Runtime对账 | W4新增只读工具接线 | ✅ 完成（2026-10-26） | PG63/TS32通过，单测173通过另2旧BOSS失败；CR无遗留 |
| W6 配置智能体联动 | 配置→持续Runner→Runtime→CLI增量群读与回复 | ✅ 完成（2026-10-26） | 旧分支基线隔离PG76及CR通过；最新master组合验证另列下阶段 |
| 最新Runtime整合 | 同步master的BOSS/Runtime修复并组合回归 | ✅ 完成（2026-10-26） | 1721707f整合验收版获授权提交；PG/Worker100与迁移34通过、CR通过；3旧Runtime断言保留 |
| W10 综合收敛设计 | BOSS经验对照、平台识别及端到端恢复静态评审 | ✅ 完成（2026-10-27） | 确认R1～R12实际缺口，收敛六个业务验收；仅主控静态审查，不冒充独立CR |
| W10 后续实现 | 三工具、身份与发送快路径、持续任务恢复 | ✅ 完成（2026-10-27） | 独立TEST及CR通过，最后别名P1/空页延迟P2闭环；未提交、部署或恢复监听 |
| W11 组合验收包 | Runtime安装器、企微签名插件与隔离升级验证 | ✅ 完成（2026-10-28） | Runtime0.0.31/WeCom0.2.1；发行修复、实际隔离升级及独立TEST/CR通过；未提交/部署/监听 |
| W12 最新master与本地容器 | 保全发行修复、整合新scheduled Runner及隔离本地验证 | ✅ 完成（2026-10-29） | 已整合12b7827c；独测/CR通过，本地登录与main真实模型已验，Runtime配对/企微实聊未验 |
| W13 main企微工具装配 | 修复网页main缺少三工具并复验真实只读调用 | ✅ 完成（2026-10-29） | 装配/内部参数校验已修，独测及CR通过；Web实际DB读取5会话/5消息成功，无发送监听 |
| W14 通用自动装配 | 复用受信目录与设备能力替换main企微特例 | ✅ 完成（2026-10-29） | 0.0.29/0.3.27旧包隔离机制验证及独立复核通过；真实业务UI/完整Worker发布未验 |
| W15 master可发布评估 | 仅升级服务端时的旧BOSS与历史任务兼容 | ✅ 完成（2026-10-30） | 修复前发现历史指纹、招聘profile及迁移水位三处阻塞，后续兼容收尾已闭环 |
| W15 兼容收尾 | 修复升级边界后复评 | ✅ 完成（2026-10-30） | 整合10b579759；独测49项unit/15项PG、CR通过，三项升级阻塞闭环；提交合并见末尾，未部署 |
| W15 提交与合并 | 特性分支推送及master整合 | ✅ 完成（2026-10-30） | 特性分支1b0759cd已双远程推送；master保留两边历史并完成合并核验，未部署监听 |

## W14 通用Runtime业务工具自动装配（2026-10-29）

用户要求取代W13的main企微特例，复用工具目录和设备能力，不加审批、探测、执行门禁。唯一写入者为主控；独立TEST/CR只读。行为设计同步到[工具装配设计](../tools/tool-auto-discovery-design.md)，本计划保留这次本地验收交接。

- 现有`TRUSTED_PROVIDERS`同处声明模型业务面与内部工具，Assembly按目录注册既有实现，不按provider名称分支。main及inherit角色自动取得业务候选；显式allowed/excluded仍限定配置智能体，旧读取别名在同一目录兼容。
- 每次执行/恢复激活，在构建prompt前从可信tenant/user的`list_devices`读取已上报能力，按selected且active设备投影schema与guide；离线/忙碌不抹工具，不加实时probe。安装/卸载在下一执行/恢复激活反映，不另加轮询。无actor不查询设备。能力查询暂时异常仅标记可用性未知，保留已知schema交给原执行链报错，不能使整个任务永久卡死。
- registry保留执行候选，不把可见性变成新鉴权；原权限、claim/permit、冻结调用恢复和unknown不重放不改。动态设备能力不加入配置指纹。既有WeCom域任务pin只覆盖其provider，恢复沿原设备投影，其他provider仍用selected，不新建通用binding。
- 纯云端BOSS职位/通知保持无需设备；内部弹层、恢复观察和skill原语不直接暴露。企微三个业务工具保持现行输入契约；非业务步骤不进模型。

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| W14.1 | 最小设计及原机制对照 | ✅ 完成（2026-10-29） | 复用受信目录、现有能力及域绑定，不新增wire/审批/指纹门禁 |
| W14.2 | 通用装配与角色/能力投影 | ✅ 完成（2026-10-29） | main/inherit及配置元数据复用目录；schema/guide同源，原恢复执行器保留 |
| W14.3 | 独立测试与CodeReview | ✅ 完成（2026-10-29） | 独测247通过/0失败/0跳过，含独立7补充；CR无P0/P1/P2，源码hash一致 |
| W14.4 | 本地Web只读真实验证 | ✅ 完成（2026-10-29） | 新会话自动拿到企微/BOSS业务能力，实际DB读取3会话/3消息、失败0，原waiting保留 |

### W14验证记录及交接

- 主控定向自测87项通过。首次批次51通过/1失败，是既有目录已含runtime-recovery但测试仍断言4个provider的过期断言；按实际5项受信目录修正，本轮没有增删native provider或claim工具。
- 独立TEST在只读工作树、无网络临时容器运行247项，0失败/错误/跳过，含独立7项：旧capabilities格式、并发actor隔离、查询故障不扩张显式profile、隐藏原发送仍走真实Dispatcher.recover(recover=True)、unknown保持及native目录字段不变。原生工具目录/版本逐项对照W14前快照。不是实际发送或多天验收。
- TEST证据位于本机Temp `wecom-w14-test-der77ivb/FINAL-INDEPENDENT-TEST.json`，SHA256 `52267d85e0f2146d89aafcbae808cb34587225aca3aa397cdc87b088d3749df8`；CR位于`wecom-w14-independent-cr-7ls7t19w/review.json`及`review.md`，无未解决P0/P1/P2。主控最终复核12项源/测hash仍匹配TEST。
- API/runner-api/runner-worker三个本地应用已重载且健康；PG/Redis及Windows Runtime未重启、原8000容器未操作。重载前6个completed、1个waiting，无queued/running；旧waiting澄清不自动回答或取消，更不启动监听。
- 用真实actor作用域SQL和现有配对设备capabilities完成装配投影：BOSS24项业务候选、WeCom3项，未装Weixin不显示该provider本地工具；runtime-recovery/skill原语/弹层/企微非业务步骤均隐藏，能力查询未失败。只取元数据，不创建Runner/invocation或导出消息正文。证据Temp `wecom-w14-live-capabilities.json`。
- 网页重载后回登录页，需正常验证码登录；用户于本会话明确选择“我自己登录，稍后通知”。截至本次交接，W14尚未通过网页发起真实模型读取，不以W13成功或后端元数据冒充新版Web验收。用户登录后在新会话发送一次有界只读请求，核实MAIN自动拿到BOSS/企微工具及真实Runner→Runtime读取；不操作BOSS、不发送、不监听、不触发原等待澄清任务。
- 未提交、推送、部署agent2或恢复监听。代码保留原master整合及W11～W13未提交修改；能力刷新限定每次执行/恢复激活，任意第三方原生命令不会凭名字直接变成业务工具。

## W13 main缺少企微三工具（2026-10-29）

用户在本地Web已登录并自行配对Runtime后，要求开发助手操作网页复现“无法读取企微聊天记录”。本轮只读验证，不向企微发消息或创建监听，不提交、发布agent2。配对已实际完成：Runtime UI0.0.31，后台设备active/online/selected；第一次普通提问和随后明确只读要求均未调用CLI，模型实际查询了旧微信业务任务后误判必须开通会话存档。

根因是`assemble_agent_tools`仅在非MASTER且配置显式允许时注册`catalog=False`的本地proxy；main只自动发现普通工具。旧测试甚至断言MASTER不导入local proxy，与[三工具设计](../design/wecom/wecom-cli-design.md#12-持续监控端到端收敛2026-10-27)及用户允许main使用Runtime的要求冲突。本轮选择现行三工具约定，替换该旧断言，不把所有BOSS/Weixin/内部恢复工具全量装到main。

最小方案：MASTER显式注册既有`BUSINESS_TOOLS`三项，其他角色仍沿原allowed/excluded配置；不更改Device授权、设备选择、绑定、claim/permit、发送授权或wire，不新增预探测、配对审批与能力轮询门禁。工具实际执行仍检查已选设备及原权限。主控唯一维护装配、相关测试与文档；独立TEST/CR只读。相关代码自测与独立验证通过后仅重启本地应用进程（不重启PG/Redis/Windows Runtime），再在当前Web会话发一次有界只读请求验证真实链路，失败按真实错误处理，不用开发助手直读CLI代替。

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| W13.1 | Web复现及实际工具定位 | ✅ 完成（2026-10-29） | 网页原回答与新只读请求均缺少企微三工具，Runtime心跳正常 |
| W13.2 | main装配与执行参数修复 | ✅ 完成（2026-10-29） | main显式三项，执行器只校验业务字段但原样传可信上下文；业务未知字段仍严格，纯normalize不改 |
| W13.3 | 独立测试和CodeReview | ✅ 完成（2026-10-29） | 独测55装配/61执行器两批均通过；最终4主文件/7上下文hash和Web证据CR通过，无P0/P1/P2 |
| W13.4 | Web→Runner→Runtime只读复验 | ✅ 完成（2026-10-29） | 实际DB通道5会话/5消息、失败0，wecom3/unknown2；17.49秒、唯一只读invocation，无发送/监听 |

### W13第二个真实调用缺口与最终只读结果

第一处装配修复加载后，模型已能看到并调用`wecom_read_sessions`，但Web暴露第二处实际代码问题：`LocalToolAdapter`为LOCAL_REQUIRED注入`_trusted_tenant_id/_trusted_user_id/_session_id/_progress_queue`，`ToolExecutor.execute`却将它们和业务参数一起传给`extra=forbid`的ReadSessionsInput，拒绝后没有到达Runtime。不能仅凭模型definition测试通过宣称端到端可用；两次参数拒绝都未产生本地invocation或聊天读取。

最终窄修只在`ToolExecutor.execute`中为有InputModel的工具分出非`_`业务字段，validate/missing/errors及类型规范化使用该字典，再与原参数合并传给execute。内部队列等对象身份保持，未知公共字段仍被业务schema拒绝；无InputModel的旧路径不变，全局`normalize_provided_parameters`源码与HEAD逐字相同，冻结调用和恢复证明的纯业务规范没有放宽。没有把内部字段加到LLM schema，也没有为batch关闭extra检查。可信来源仍由原LocalToolAdapter覆盖租户/用户/会话，进度队列独立覆盖；不能把ToolCatalog的definition投影误写成其已预校验全部模型实参。

验证分批登记，不重复累加：主控初批29自测；装配独测55项（真实RuntimeResources/discovery/Assembly/proxy/ToolCatalog）；主控追加6文件85项；执行器独测61项（实际LocalToolAdapter→ToolExecutor→真实WecomReadSessionsTool，只有设备/派发I/O边界mock）。全部0失败/跳过。独立实证包含limit字符串转int、进度对象原样、可信ContextVar、未知公共参数/非法limit在设备查找前拒绝，以及旧BOSS/恢复normalizer相邻回归。第一条sh入口因bash数组语法无法启动，纠正为原bash入口后通过，未按产品失败或通过计数。

仅在本地无queued/running任务时先后两次重载API/Runner API/Worker，PG、Redis、Windows Runtime及配对记录不动，没有重新安装或额外配对。在用户现有Web会话中由main调用一次`wecom_read_sessions(limit=5)`，runner `runner_ca5b54ba015c4e42a181d4e56f3d9cd0` completed，17.491562秒；唯一本地invocation `cea7bdcb-d64b-4d79-a19d-9f4887a1f241` succeeded/OK，channel=local-db、navigated=false。原工具结果核对5会话/5消息、failed0、sender_platform wecom3/unknown2；本批wechat0，不声称已验该类别真人。pending候选1、has_more及history_truncated均true，按5条授权未继续翻页。网页与元数据计数一致，不导出正文、会话名或账号。

证据：本机Temp `wecom-w13-live-read-metadata.json`与`wecom-w13-web-read-success.jpg`；装配独测`wecom-w13-test-hyrrqz8m/FINAL-INDEPENDENT-TEST.json` SHA256 `35980be8ed6d8844c59fa36648988998f8ef279d01ae28bcbb506cdd66e4ea16`，执行器补充`executor/FINAL-INDEPENDENT-TEST.json` SHA256 `2d69b8200aeb8bbb75eb6b29e61c09c9dd9bb17f9ff3a3de97b06fd23153fbf9`。只在本地应用加载修复，未提交或推送、部署agent2、企微发送或启动监听。模型把has_more自然语言解释为“同步范围之外”不严谨，后续说明应使用“当前范围还有后续页”，不以该描述为CLI实现事实，也不为此扩大本轮代码或阻挡首次只读验收。

最终独立CR PASS，无未解决P0/P1/P2；报告本机Temp `wecom-w13-main-cr-toyv8noi/review.json` SHA256 `a8e1aceaf880394d138673f1a4726dd4c85a733ba089e524ae7558187dceb681`，MD `18432b3394cddef830690e028fbeef58e4059c53dca5d9093adb81c68a0f66f9`。审查同时核对真实工具返回与网页摘要一致、本批未涵盖微信来源，以及仅有一次本地只读调用。后续仅文档登记，生产源码冻结不变；不机械重跑已稳定测试或增加无关门禁。

## W12 最新master整合与本地容器验证（2026-10-29）

用户授权先同步最新master并在本地容器准备/验证，不部署agent2，不恢复真实监听或发送，不自动提交。沿用[设计§12](../design/wecom/wecom-cli-design.md#12-持续监控端到端收敛2026-10-27)，不增加另一套Runner、权限流程或工具协议。

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| W12.1 | 工作区保全与主线选择 | ✅ 完成（2026-10-29） | aa6885b4未提交42文件逐一备份核hash；origin/master12b7827c，github/master另有分叉，本轮仅采用origin |
| W12.2 | 合并冲突与迁移水位 | ✅ 完成（2026-10-29） | 7处冲突解除；continuous/scheduled共存，追加13:46:35分支DDL补齐，121个合并Python AST通过 |
| W12.3 | 本地容器与启动验证 | ✅ 完成（2026-10-29） | 新项目API18000、Runner API/Worker、PGvector/Redis及独立卷已运行；Web13000代理，原8000不变 |
| W12.4 | 独立测试与审查 | ✅ 完成（2026-10-29） | 独测206无DB+53完整PG，0失败/跳过；Web28及最终build通过，独立CR无遗留P0/P1/P2 |
| W12.5 | 本地账号与main真实模型 | ✅ 完成（2026-10-29） | 用户指定账号经正常密码/验证码登录；main真实Runner约8秒完成，一笔LLM用量已结算，0本地工具调用 |

本地架构：浏览器→本地API→独立Runner API/Worker→本机Windows Runtime→已安装wecom CLI。API、Runner、PostgreSQL和Redis可运行在WSL Docker；企业微信客户端及其UI/DB依赖留在Windows，不尝试放Linux容器。使用新Compose项目和loopback端口，独立新库/卷及Redis，不借现有8000服务、业务库或其真实账号；先验证启动、schema和合成数据恢复，再由用户授权配置测试智能体、配对和真实聊天验收。没有模型密钥或测试账号时如实标记，不能用合成测试冒充配置智能体实机链路。

本轮成功标准：保留上轮发行修复原字节和交付文件；解除7处冲突；新旧Runner语义共同通过相关定向独测及CR；本地测试环境的实际源码来自wecom工作树，数据库包含分支与master所需结构；能给出本地入口及下一步可执行的测试顺序。旧Runtime0.0.31与插件0.2.1是已验收的客户端固定包，不因服务器master整合静默改写旧包或报告；客户端内容未变可复用，后端整合需重新验证。

### W12本地环境操作与实际边界

配置入口：[deploy/docker-compose.wecom-local.yml](../../deploy/docker-compose.wecom-local.yml)。本机WSL发行版`Ubuntu-22.04`，项目名`aid-wecom-local`，应用镜像复用现有`aid-agent-api:latest`，实际代码挂载`/mnt/c/repos/aid-work-agent-wecom`。代码挂载沿用现有local Compose读写模式，日志/上传/存储独立卷；独立pytest容器另用只读代码及断网/loopback受限网络。原`aid-agent-api`仍挂主工作树、运行8000端口，本轮未替换或重启。

私有配置已保存在Git忽略的`clients/agent-desktop/build/wecom-local/local.env`及`postgres.env`，不能提交、打印展开后的compose config或清理整个目录。前者只含本地新数据库连接、独立服务间令牌及从本机私有配置复用的5项模型配置；后者固定原3项PG参数，不带模型密钥，避免改模型时连带重建PG。未复制业务数据、用户账号、渠道凭据或真实Runtime配对。首次启动阶段未调用真实模型；后续用户指定账号与main真实模型补验见下文，不据此宣称企微业务通过。

本地启动/查看/停止（PowerShell；只影响本项目，`stop`不删除卷）：

```powershell
wsl -d Ubuntu-22.04 --cd /mnt/c/repos/aid-work-agent-wecom --exec docker compose -f deploy/docker-compose.wecom-local.yml up -d
wsl -d Ubuntu-22.04 --cd /mnt/c/repos/aid-work-agent-wecom --exec docker compose -f deploy/docker-compose.wecom-local.yml ps
wsl -d Ubuntu-22.04 --cd /mnt/c/repos/aid-work-agent-wecom --exec docker compose -f deploy/docker-compose.wecom-local.yml stop
```

前端用现有Vite进程，工作目录`C:/repos/aid-work-agent-wecom/frontend`：

```powershell
$env:DEV_API_TARGET = 'http://127.0.0.1:18000'
npm run dev -- --host 127.0.0.1 --port 13000 --strictPort
```

`DEV_API_TARGET`只覆盖开发代理，未设置时仍为原8000；没有改生产API地址或客户端协议。Web入口`http://127.0.0.1:13000/`，主API `http://127.0.0.1:18000`。真实探测：API `/health`与内部Runner API `/health`为200，Web SPA为HTML200，经Web访问未登录设备接口为401；Worker不监听HTTP，禁用其继承镜像的8000健康检查，按进程与真实DB连接记录验证，不能假报HTTP健康。PG与Redis不发布宿主端口；本地库`aid_wecom_local`含pgvector0.8.7，最终迁移水位`2026-10-29 13:46:35`，Runner/定时任务均0。

Docker Hub/既有加速源在WSL daemon拉取时TLS超时，但Windows官方registry可访问；本次用一次性Temp helper从官方Hub取amd64 manifest `sha256:a97d77306ff47cc1d8add4d000cec2207da2e110746318df3137ce53c337ca30`，逐一核对16层压缩摘要与解压diff_id，导入本机后image config ID `sha256:f1447071e6c8449dcf4056aa0994c81d3566ce5ee232de36b04dc25696a116dd`一致。不是在产品中增加拉取代理、镜像管家或修改Docker全局设置；最终完整schema没有删掉向量部分。

启动发现并修复三个本地配置接点：PG healthcheck限定TCP以避初始化socket误报；只读父代码挂载不能为新checkout创建嵌套卷挂载点，沿用原local读写模式；Worker不继承无关HTTP健康探测。主API首次缺模型配置而被已有视频模块导入拒绝，仅补本地真实模型配置，不扩改视频业务或伪造业务ready。

测试设置偏差单列：主控首次用npm exec的prefix但工作目录仍为根目录，导致Vite alias缺失，改从frontend目录原入口重跑28项和build通过；补模型配置时共享env意外重建本项目PG，独立PG第一批连接中断作废，已停稳helper、拆分PG env并重新冷启三应用进程。只重跑受影响PG批；其随机`aid_test_*`残留由原隔离管理入口精确删除，未删其他库。后续PG ID已冻结，不能将此中断算为产品失败或计入通过数。

后续真实业务验收顺序：本地测试租户/账号与main登录/模型现已补验；下一步在隔离Runtime测试配置中连接18000并安装现有0.2.1插件、完成本地配对；先只读校验会话与sender_platform，再由用户明确授权测试发送/恢复监听。Runtime配对、微信/外部企微真人样本及跨日监听仍未完成，不宣称完整售前链路通过。

### W12本地账号与main补验（用户补充授权）

用户提供本地登录凭据并允许使用内置main，故只在`aid_wecom_local`创建专用测试租户与tenant_admin，未复制agent2账号、智能体或数据库，也未写入真实Runtime配对。凭据经不回显输入传入临时验证进程，复用项目既有密码哈希；手机号、密码、令牌及密码hash不登记到文档或仓库。通过正常HTTP验证码和`/api/auth/unified-login`验证成功，没有改认证代码或使用万能凭证。

专用测试租户建立后余额为0，接单被原`NO_CREDIT`机制拒绝；给该独立租户补1000本地测试积分后重试，未改收费或准入规则。唯一受理的短任务走`/api/chat/runners`，显式选择`main`，要求不调用工具仅回复固定连通性文字。真实流程queued→running→completed，约8.15秒；输出为“本地 main 链路正常。”，真实deepseek/deepseek-flash的一笔LLM receipt为observed且applied，结算8.4本地积分，local_tool_invocations为0。不是Mock模型或开发助手代答；此处不涉及企微消息。

脱敏证据保留在本机Temp `wecom-w12-local-account-result.json`、`wecom-w12-main-smoke-result.json`、`wecom-w12-main-model-proof.json`；不保存登录令牌或明文密码。测试租户入口及租户代码已交给用户，账号使用本轮用户提供的凭据。仅本地fixture数据与实际API补验，无产品代码更改，原独测/CR源码冻结保持；没有再次触发部署、重跑不受影响测试、配对Runtime或恢复监听。

### W12最终验证与保全记录

独立TEST：14文件206项无DB，7文件53项完整真实PG（含vector），全部0失败/跳过；前者新断网只读容器经原`dev_test.sh`，后者经原`dev_test_isolated_db.py`在自有PG的loopback管理入口创建随机库。覆盖两个master水位补齐并保用量/积分、真实Worker/子树恢复、continuous可信接续、unknown与累计计费、归档分页及WeCom未知外发/入站恢复。中断批无效单列，不与重跑通过数累加；最终自有`aid_test_*`库0、连接0、运行中的临时pytest容器0。136目标文件加4安全入口共140个hash核对，聚合`0392906537a36eb3fc7006e1d2c6c49cb965b9f1cbfd2545d51b1704a3e9a002`。

独测报告：本机Temp `wecom-w12-independent-test-3y05xbjl/FINAL-INDEPENDENT-TEST.json` SHA256 `088bafc7d1d6634ad906da6a1078458ad8e50c603688ffb28c3c9ad6a7e396d4`，同名MD `0ce10aae5cbed88ac8a81d94b5eca09d776de51163e6e965c51d45d806a7d422`。主控Web28、最终Vite改后build exit0、121合并Python AST及冷启动/HTTP/真实DB证据分别在Temp `wecom-w12-main-evidence.json`与`wecom-w12-local-startup.json`，不冒充登录态或完整实聊。

最终CR PASS，无未解决P0/P1/P2；23个交汇/配置审查文件和独测140文件逐项一致。报告Temp `wecom-master-local-cr-mfdagbmm/review.json` SHA256 `7031986557de488fa720c65a83099aab0c022e51749001f657f0d30033c8a2b9`，MD `463be555402eca048acae690046fce09dff8cbc53ec6093793a74c27f13aac91`。只审本次合并接点及本地配置，不声称重审master全部无关功能或全仓测试通过。

当前分支`feature/ask-user-question-card`仍HEAD `aa6885b4`，MERGE_HEAD `12b7827c`，冲突0；合并结果在index/worktree，未生成merge commit或推送。原42个未提交文件备份于本机Temp `wecom-master-local-20261029-9ds8mpa7`，其中40个clients发行源码/资产原字节均保留，文档仅本轮进度更新。两远程master分叉已登记，未把github独有通知修复混入origin基线。独立本地5服务和Vite保留运行，原8000 API容器ID/启动时间未变；无agent2或其他远程部署、真实Runtime配对/聊天/监听。后续停止仅按上文项目`stop`，不删卷或误停旧服务。

## W10 真机前综合收敛（2026-10-27）

### 本轮实施记录（当前状态，以此节为准）

用户已授权开发、独立测试和CR，替代本节原始设计中的“只改文档”限制；禁止提交、部署、安装发行包及恢复监听。原始设计依据保留在设计§12，以下登记实施事实。

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| W10.1 | 来源实证与三工具契约 | ✅ 完成（2026-10-27） | 微信虚拟corp与本人企业已只读核实；其他corp不猜外部企微，返回unknown；Agent仅读/发/批量读三项 |
| W10.2 | continuous与Runtime恢复 | ✅ 完成（2026-10-27） | 原任务可信接续、任务依赖指纹v3、暂时错误退避、outbox不阻塞心跳；保留原调用及用量事实 |
| W10.3 | 批量读取、身份和发送快路径 | ✅ 完成（2026-10-27） | 稳定session/分页，局部失败不扩范围；未知身份原消息游标可恢复；当前会话匹配不搜索；托盘不算登出，常规清理不用Esc |
| W10.4 | 未知发送与取消恢复 | ✅ 完成（2026-10-27） | 内部只读对账、目标间隔离与轮转；取消保留未知外发hold，原设备资源证明可释放hold但不伪造发送成功 |
| W10.5 | 状态与证据保留 | ✅ 完成（2026-10-27） | 读取总量50条/16KiB；checkpoint仅保有界索引；已闭合journal按7天回收，unknown/未ACK保留 |
| W10.6 | 独立TEST与CodeReview | ✅ 完成（2026-10-27） | 独测136单测/49PG基线及末轮增量通过，CR无遗留P0/P1/P2；真机边界另列 |

**同步master与保全**：HEAD仍`349a10ab`；先无提交整合`5b54688d`，再增量整合远端`835a5010`，MERGE_HEAD已更新为后者，无未解决冲突。第二次整合先保存index/worktree二进制patch及全部未提交/未跟踪文件；普通路径三方应用，ideas两份索引分别合并index与worktree，保留原P6/P7/W9及本轮修改，无stash/reset/提交。两轮本机Temp备份保留。未运行任何更新部署脚本。

**实现与兼容**：Agent公共参数仅`read_session(session,cursor?,limit?)`、`message_send(session,text)`、`read_sessions(sessions?,cursor?,limit?)`，旧MCP和已接受参数继续兼容。原生15项、Runtime受信9项与Agent三项是不同层级。CLI升`0.2.0`，避免同版本不同内容无法升级；schema_digest为`sha256:3cc508ef7b22b0daf2050e5843da644e56165c50bcfb3f479b1835141daa82db`。新Runtime先按旧8项上报，只读协商得到真实新schema后更新9项；旧插件仍可执行旧入参，新session/batch缺能力明确提示升级，协商传输失败下次可重试。复用原Device API、claim/permit、OperationResult和计费，不替换历史签名根。

**恢复实现**：`continuous_trigger`由服务器可信来源接回同一根任务和逻辑会话；暂停/取消/guard优先。v3指纹仅绑定任务实际模型依赖，旧v2只有可验证匹配才迁移。暂时源错误2～32秒退避，保留原wake；死Worker的started模型调用保留为unknown事实，新调用单独结算。现代WeCom调用固定原设备v2，派发前中断能证明尚无main调用并续做；纯DB读取unknown可重读，原audit不改成成功。未知外发只读对账，最多每轮4项并轮转，不重放；独立目标继续。取消按root→admission→device→原invocation顺序取锁，逻辑结束与真实未知外发资源hold分开。

**身份与遗漏恢复**：native按sender_id关联同快照用户资料；微信虚拟corp映射wechat，本人企业映射wecom，缺失或其他企业仍unknown。不能把external关系、昵称、UID前缀或消息中的“@微信”当平台证明。当前真实微信与内部成员来源已核实，尚无外部企微真人样本，不宣称全平台实机覆盖。批量结果保留同事上下文与待判断候选。未知消息附原生精确恢复游标，Runner在原invocation内存私有待处理offset，checkpoint仅存轮转位置；资料补齐后通过同一个read工具取原消息，连同本轮读取原子保存恢复候选，防止消息滑出窗口漏答或崩溃丢候选。保留每条原始baseline，新旧不随重试漂移。新增一个partial index支撑未闭合索引扫描，不新建表、身份服务或调度器。

**测试范围与边界**：开发侧112单测、49真实PG均通过且无跳过，后者包括新Worker再次休眠、原用量/调用保留、首次读取、未知身份、派发前中断、用户取消/资源释放和并发锁。PG使用本任务loopback随机`aid_test_*`库，原安全建库/清理入口不变；本机PG无pgvector，仅在Temp harness排除无关embedding扩展/表/索引，未声称全量embedding schema验证。独立测试发现并修复HTTP响应体中断丢ACK证据、发送核验身份字段投影遗漏等问题，最终结果以W10.6及下段登记为准，不累加重复复测计数。

**最终独立验收**：独立角色按冻结源码验证：后端基线136单测、49真实PG通过；最后named→canonical修复为10业务单测/7真实PG，另45相关单测/15相关PG通过。Runtime56项及真实CLI只读能力协商1项通过。CLI全量基线Python211通过/1正常平台跳过，Node101、真实MCP合成库3及滑出70条后的身份补读结果保留；最终metadata定位增量Node15/Python16/Agent隐藏字段1通过，v7原502会话回归等Python25/Node3通过。各批可能重叠，不相加冒充唯一用例数；未变模块复用原证据，不机械重跑全仓。

最终CR发现的命名目标P1已闭合：仅首次named send派内部`wecom.resolve`纯元数据读取，持久原设备canonical后再发送；即使unknown没有data.session也按稳定目标保留pending。原ToolFact意图保留，新attempt复用原resolve/main，稳定ID发送不额外读取。`resolve_only`仅内部native参数，不暴露Agent，不读正文/不返回cursor，20KB正文不挡定位。另P2过滤启动fence前没有消息的历史上下文候选，500个空会话不再制造约100个空页；不改变批量范围/fence，原空会话的新消息下一次增量可见。未引入aliasmap、新表、新服务或全局冻结。

独立CR最终通过，无未解决P0/P1/P2。CLI v7 45文件聚合`209d305eb00db326a809603858f8db6c7ef9e5a8929a071fdc8b2e095851a6a0`；Runtime18文件聚合`2c36f9fd056f931e45352bc27f749f71503ad47b48c00ace5764aa68d0974733`；Runner最后4文件的独测/审查哈希一致。具体命令、分批结果和源码指纹在本机`%TEMP%/wecom-w10-final-independent-evidence.json`，不提交聊天正文或账号。

**真机衔接**：真实ProviderManager→CLI0.2.0→MCP只读协商已通过，业务调用为0；固定CLI/Runtime源码指纹记录在本机Temp。尚未制作并验证签名组合包安装升级、真实混合群外部企微分类、真实UI快路径发送或多天运行。下一次用户授权真机后用配置智能体走Web→Runner→Runtime→CLI，按V1～V6验证实际延迟、恢复和累计积分；安装/部署、发送/监听分别遵守明确授权。不要用原Temp售前脚本冒充产品Runner链路。

**主控收尾**：最终CLI构建及version报告0.2.0/上述digest通过；103个任务相关变更Python AST通过，本地文档目标存在。CLI45/Runtime18冻结文件逐项一致，原35份未提交文件无丢失，W9隔离入口/测试保持原字节，原lock三处peer标记保留；仅本次版本字段按需更新。确认0个aid_test数据库/连接后停止本任务PG helper，端口已关闭、启动辅助进程exit0。无提交、推送、部署、真实发送或监听；master整合保持未提交merge。

**本版提交授权（2026-10-27）**：用户明确要求提交并推送三工具、身份识别、发送快路径和Runner恢复，替代本节实施阶段的禁止提交/推送要求；仍禁止部署与恢复监听。提交前fetch两远程，origin/master仍835a5010，当前分支两远程均349a10ab、无分叉。提交包含已审查的master整合、P6/P7配套修复和本轮验证依赖的W9隔离入口，保持最终验收源码；原package-lock三处无关peer标记仅留本地，提交只更新CLI版本字段。复用相同代码/依赖/环境的独立TEST、CR、主控构建与AST结果，不重复运行未变化的测试。提交SHA及推送目标以实际Git结果为准。

### 验收范围

以[设计§12.6](../design/wecom/wecom-cli-design.md#126-实施契约与有限验收)的V1～V6为业务验收清单。隔离测试可验证恢复、不重放、计费及有界读取；真实UI、发行安装、外部企微样本和跨日增长另列实机范围。暂时缺失的身份只影响该条，不增加永久平台门禁；确定unknown的外发不自动重发，其他目标不被冻结。

## W11 Runtime与企微插件交付（2026-10-28）

用户授权在wecom工作树生成最新Runtime安装器和wecom CLI插件，完成隔离验包、升级验证并交付安装文件；不部署agent2，不恢复监听。本轮不自动提交。源码基线`aa6885b4`，既有三工具/身份/恢复独立测试和CR结果复用，不重新开发产品业务。

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| W11.1 | 版本、旧包及信任基线 | ✅ 完成（2026-10-28） | 最终UI0.0.31/WeCom0.2.1；Host0.2.24/Core0.2.21/Native0.1.0，旧dev信任单列 |
| W11.2 | 签名插件和Runtime构建 | ✅ 完成（2026-10-28） | 最终0.2.1/0.0.31构建及标准验包通过；含Crypto、缓存过滤及实际深目录启动修复 |
| W11.3 | 隔离验包、库存和NSIS升级 | ✅ 完成（2026-10-28） | 最终真实NSIS30→31、身份/库存及WeCom20→21通过；332字符实际安装路径默认DB/MCP/PS OCR与EXE SMOKE通过 |
| W11.4 | 独立审查、摘要与交付 | ✅ 完成（2026-10-28） | 最终源码/固定产物/安装说明CR通过，无遗留P0/P1/P2；交付目录含报告、SHA256与组合ZIP |

沿用[当前打包手册](../ops/runtime-boss-release-guide.md)和[插件格式/资产规范](../../clients/runtime-plugin-packaging/README.md)，只将Provider换为wecom，不执行BOSS一键脚本或重打BOSS。本轮acceptance安装器仍是Windows NotSigned，插件有Ed25519发布者签名，不冒充production签名。最终UI0.0.31/WeCom0.2.1包含下述真实安装修复；Host0.2.24/Core0.2.21仅发行版本更新。`build-info.json`同时记录源码基线与本轮未提交的修复、版本和公开根变化。

主控唯一处理修复、版本、构建、公开根登记与文档；独立TEST验证实际产物，独立CR只读检查源码/包与证据。初始ignored Python bytecode移入任务Temp，后改为复制阶段排除开发缓存；仅修改发行依赖和启动适配，不改聊天业务。原wecom lock的三处peer标记保留。

验证分开登记：包验签/文件清单/搬迁Node和OCR；真实Host旧库存及合成配对保留；真实NSIS fixture旧安装器升级。基础doctor仅用标准验包器在Temp APPDATA做环境/进程只读检查，不带localdb、不扫密钥、不输入；readiness不是实际业务通过。独立NSIS使用`AIDWORK_RUNTIME_INSTALL_FIXTURE`的现有受限路径和HKCU子树，不动真实Runtime home/快捷方式，也不执行普通卸载器。


### W11发行依赖修复（P1）

独立TEST对首轮包内Python实际调用native解密，发现延迟导入的Crypto未随旧OCR依赖进入发行包；明文SQLite/OCR通过不能覆盖真实加密DB。原插件`7639f3de...71bc2`及原安装器`c3d73639...05afb`已撤回，归档在首批build/withheld-delivery，不再置于clients/release交付目录。

最小修复只涉及原资产链：锁定与开发环境一致的pycryptodome3.23.0官方Windows x64 ABI3 wheel（PyPI官方SHA/URL已独立核对）并保LICENSE.rst；资产准备、企微签包、离线验包均使用嵌入Python执行AES-CBC已知向量/SQLite检查，避免复用旧OCR缓存带病出包。native解密算法、Runner、三工具schema均未改；独测最终仍须包内driver解独立生成的12KiB加密SQLite并比较完整行集。官方SelfTest含公开PEM向量，核对源wheel字节而不冒称本机秘密，也不为它新增拦截。

未交付WeCom0.2/UI0.0.30保持版本，新输出/唯一签名key，历史20根保留追加第21根，随后缓存修复再追加第22根，未交付0.0.30完整snapshot随新候选更新，旧20根副本保留首批build。Hosttgz字节不变复用，安装器因嵌新根重新生成。最终NSIS使用第二份从真实0.0.29开始的全新fixture，不对已装首轮0.2候选强行同版本覆盖。

### W11开发缓存修复（P2）

Crypto修复候选`0965e6ba...3c62`已通过实际加密快照解密，但独立审查发现测试再次生成的drivers/py/__pycache__被原递归复制带入。该候选不交付；不再靠每次手动清缓存，改为现复制函数排除__pycache__/pyc/pyo，其余driver源字节/BOM不变。新增一项真实文件系统反证，打包定向9项通过，P2源码CR通过。最终候选使用新输出/唯一key及完整第22根重出，Runtime只因嵌入新根重新生成；既有Crypto正确性证据可复用，最终仍验证固定包字节和实际解密。

### W11真实安装长路径修复

真正装入fixture后解释器路径332字符，Node原spawn失败；仅namespace exe可启动但SQLite/Crypto原生库仍报路径过长。仅短EXE又不能覆盖cv2将别名realpath展开回长目录，故短目录/import通过不足以确认实际安装可用。

最终窄修：一份bundled-only Python启动适配复用到TS DB及两处PS OCR。namespace起步可加载标准ctypes，DLL目录句柄保留，按系统现成短名表示sys.path；原realpath完整调用后仅对嵌入runtimeRoot内做兼容表示，driver/业务文件与严格/bytes语义保持。PS既有Win32类只增加GetShortPathNameW的只读查询，解决.Net NULL applicationName的EXE长度限制。无Crypto私有patch、无系统策略变更、无目录/缓存搬迁、新工具或业务门禁；未验证禁用短名的卷配置。

预签独立实证：真实默认TS bridge在已装deep资产解密12KiB/6行成功（不注入spawn）；真实新Resolve查得现成alias并samefile确认；真实PS函数与共用bootstrap识别合成HELLO/123。runtimeRoot外realpath/Path.resolve、argv/stdin、Crypto懒加载均通过；新增1个真实协议测试，相关Node25项及包装9项通过，源码CR通过。CLI修复升0.2.1/UI0.0.31，Host/Core不变，已装0.2.0/0.0.30 fixture做正常更高版本升级，既有0.0.29→0.0.30证据另列，不改semver保护。

### W11已核实的升级边界

既有clean-generation安装只迁移可复用身份和验签插件，不迁移journal/outbox，旧home通常会清理。故本次不能宣称有未决本地操作的无缝热升级；说明要求正常退出并先收尾待回传，不增程序门禁，也不把same-home Host恢复误写成NSIS跨代际保留。独立TEST做实际反证并分别登记。

找到的旧WeCom0.1实验档案与产品19根复用了同名key_id但公钥不同，产品正确拒绝；不替换历史公钥或把旧dev根加入客户安装器。真实发行库存迁移复用旧0.0.29组合包的BOSS0.3.27；最终WeCom0.2.1使用第23根，原22根完整保留，0.0.30历史快照不再改写。旧WeCom0.1→新包在其自带旧根加本次唯一新根的独立测试trust验证，只说明机械升级，不作为产品历史信任兼容。

### W11最终交付与验证结论

最终目录：`clients/release/AID-Work-Runtime-0.0.31-WeCom-0.2.1-20261028-111140/`，同名ZIP和`.sha256`旁置。安装器127004664字节、SHA256 `9ce7ab500e7e130b48a319f453e0047b4b3603fa34f2b426f9b8d72e21aa829c`；插件119178601字节、SHA256 `9718121713fcb34a7e080a2175755ae312fd14a44fef39583c89ea50ecf1773d`。只交付本版，前三候选留在build下WITHHELD，不混用其报告。基线aa6885b4，本轮发行修复、版本/公开根及文档仍未提交；原lock三peer标记保留。

独立TEST冻结结论PASS_WITH_DISCLOSED_UPGRADE_BOUNDARIES：旧0.0.29→候选0.0.30与最终0.0.30→0.0.31实际NSIS分开登记；实际新ASAR经loopback核验合成身份/DPAPI及已发行BOSS库存，WeCom0.2.0→0.2.1按正常更高版本升级。迁移测试仅在NSIS已完成闲置核验后将stopLegacy替换为noop，不冒称真实云端部署。最终安装的332字符解释器路径、默认Node桥独立AES密文/SQLite六行、三个平台结果、manifest入口MCP三业务schema、包内真实PS OCR和安装EXE隔离SMOKE均通过，MCP业务调用与真实企微IO为0。标准doctor在非交互环境readiness=false单列，不充当业务ready。相关包装9项、Runtime包装/信任20项、CLI Node25项、Python协议1项按批登记，不累加重复验证。

独测JSON SHA256 `9b3340013b79b7d7d2d5511d534d947527f7240a7030d40bcc81f3ec9b3b09f5`；MD `d2af79cf16fc9e4195b18d164bbbeda59d865ebb6ec0f57a12839e9a847a3d6a`。最终独立CR为PASS，无未解决P0/P1/P2；包内7471清单项/90源文件、Runtime3850输入/41Host源、公开根及29个本轮源码/元数据hash均核对。交付含脱敏独测报告、最终插件/Runtime/交付CR与安装说明；不附原始合成凭据、消息库或日志。

隔离偏差如实保留：首轮MCP父logger漏设home可能向默认Runtime日志追加少量provider元数据，未读真实配对或执行业务/云请求，之后父子home严格隔离复验，真实日志不清理；末轮MCP测试误用导出模块入口，仅修测试入口按实际manifest补验。第一次清理专用Temp/HKCU fixture被自动审批以`blocked by policy`拒绝，未重试或换工具绕过；两份合成fixture保留，最终自有测试进程0。不得称全程无设置偏差或清理成功。

未验边界：真实UI发送、混合群外部企微真人类型、Web→配置智能体→Runner业务链路、HTTP ACK补投、多天运行与禁用短名的卷配置。用户明确部署/监听前不启动这些动作；升级前先收尾任务及待回传，按安装说明退出旧Runtime、运行安装器，再导入插件ZIP。

## W6 配置智能体完整链路（2026-10-26）

### 最新master与Runtime核查（2026-10-26，用户追问后补核）

本节首次核查之后，用户已明确授权完整整合master并重新独测/CR；原“不合并”仅指首次核查阶段，本阶段以新授权为准，仍禁止提交/推送、部署和恢复真实监听或发送。采用现项目dev-workflow高风险流程：主控唯一处理Git/前端/迁移清单/文档及W6备份重叠，backend开发角色仅解决engine/context_assembler/control_set三处冲突与其直接测试，独立TEST/CR冻结后只读验证；不新增wire或恢复平台，不改契约2.10语义。

2026-10-26实施记录：新fetch后两端master仍1721707f；HEAD仍ffd18b2f。合并前51文件逐一复制并SHA256校验至任务Temp（wecom-master-integration-backup-7l_qzhwf），无stash、无覆盖式reset。仅docs/ideas.md、src/api/agent_definitions.py与master路径重叠，先验证备份后对这两文件精确restore、--no-commit --no-ff完整merge，再按base/已合结果/原未提交副本三方重放，均无重放冲突。第一次merge因ideas CRLF/stat未刷新拒绝，在status确认两文件干净后重试成功进入合并；无丢失改动。其余49文件内容均核对原副本，13个merge仅换行变化已恢复原字节；package-lock原三peer改动保持。

8个提交间冲突已解：迁移清单双方独立条目均保留；3份索引保留新master条目和本分支已实现WeCom/agent2记录；MessageList退役旧send快捷选项事件但保持续任务history分页事件；engine先拒绝/消费不完整模型响应再normalize namespace，保continuous_guard与tool代次/游标顺序；control新定义移除present_options、保agent_sleep并保旧pending实例恢复；普通所有角色默认50轮，显式profile优先，master新增无profile期待20的两处测试同步既定50要求。共享Runtime productTrust、win32 sequence/windows、acceptance根与实例授权均核对master逐字一致。索引为未提交合并状态，MERGE_HEAD=1721707f，未自动生成合并commit。

整合后发现升级水位边界：master的2026-10-26 15:00迁移已推进单一last_datetime，原10月22日三条分支迁移即使按顺序合并也会被存量环境跳过。保留历史条目不改，在2026-10-26 17:00追加同结构幂等catchup（wake_at/唤醒索引、8列BIGINT、历史分页索引），不改全局迁移算法。新增真实PG反证将匿名库模拟为master旧schema与水位，保留真实usage/credit行；升级补齐与全新结构一致、重复启动/强制重放不丢记录、可写入超过INT32计数。该反证+原迁移+迁移单测33项自测通过。

合并语义额外6项真实Engine反证：length截断部分sleep/send不能派发或arm，model_usage与拒绝checkpoint两处重启不重放，后续同provider call ID仍namespace/游标顺序正确，只有真正提交的纯sleep抵扣业务轮次，普通50轮notice与continuous50轮PAUSEDguard分离；新测试及相邻98项自测通过。Runtime/WeCom/BOSS/Web构建通过（BOSS依赖按原锁npm ci --ignore-scripts，无lock改写）；shared native46、Web103及5文件真实PG/Worker43自测通过，PG包含两配置W6链路、实例授权、发送结算与休眠。所有pytest均显式新task loopback50925随机aid_test，LOGS默认连接禁用；无默认共享库访问，不以W9旧入口冒充隔离。

Runtime广跑的3项providers断言失败经独立CR核对：HEAD与MERGE_HEAD两端均BOSS25（测试期望24、遗漏boss_recovery_probe）、detail排除后digest d51463db...462f5（测试仍旧f1b9dd6d...522f）、Provider5含runtime-recovery（测试期望4），属于双父共有陈旧测试锚点，未发现丢BOSS能力。不删恢复工具、不改生产白名单凑绿，仍记录非全仓全绿；旧后端BOSS/catalog基线另按独测范围记录。

#### 最新master整合最终验收（2026-10-26）

独立真实PG/Worker主批100通过、0失败/跳过：既有W6与设备/恢复/对账/睡眠边界、master实例授权与发送结算，加实际HTTP finish_reason=length首帧的零派发，后续合法poll/sleep与fresh Worker复用provider call ID仍四个独立call、5个usage receipts，取消后无新模型；原大于12K完整JSON仍到达模型。另独立纯Engine等91项经显式隔离入口通过。

迁移独立34通过、0失败/跳过（原33加独立partial-rollout）：保留真实已park wake/checkpoint、已超INT32 token和credit17.25，在master15水位、其余7个INTEGER及缺历史索引的混合已发布状态下，catchup升级/重复启动/强制重放全部保数据；8列均能写INT32以上，原sleep随后真实claim一次且重复claim拒绝。不能以无账本行的全新库代替此证明。

客户端独立：shared native46、BOSS fake恢复/零输入/unknown43、Web111、插件package/store17均通过；Runtime定向16项为13通过和上述3项双父陈旧断言失败。额外23个真实不同Ed25519历史/新根的旧/新签名均crypto verify通过（1场景）；实际CLI lostACK仅补传原unknown4场景、MessageList保history事件且不转发退役send2场景通过。均未操作真实企业微信/Chrome；不声称实际发行安装或真实用户群验收。

独立完整CR通过，无未解决新增P0/P1/P2；6项真实Engine/sleep纯内存反证独立通过，升级水位缺陷已闭环。freeze2 295文件聚合`034f4a7611f0493d5479af7a1bcd14aa37b61ef229573fde3fe993bcd17904f0`逐项一致；最终仅增补文档验收记录。主控核113个变更Python AST、8冲突均解除、master关键5文件逐字一致和原package-lock三peer哈希保留。原51文件备份仍保留，不以stash或强制覆盖代替保全。

当前是已解除冲突的未提交merge：HEAD仍ffd18b2f、MERGE_HEAD1721707f，合并结果在index+worktree，W6及补齐修复仍有未暂存部分，不存在新merge commit或push。已确认0 aid_test数据库/连接及本批API/Worker/MCP残留；主控核任务root/data/pid后仅停止本任务50925 PG helper，端口无监听。仍未提交、部署、安装客户端或恢复真实群聊监听。W9旧测试入口、旧fingerprint恢复、真群/多天/发行包与本机证据保留策略继续按原边界另验。

独立全命令/文件/计数/双父基线及清理证据：`%TEMP%/wecom-master-independent-f130afb8/independent-master-integration-final-evidence.json`。其中Web111与额外组件2可合计113，不能再把合计与子项重复相加；其他计数按各批原始结果记录。最终文档仅补验收事实，产品源码与freeze2一致。

#### 本次验收版提交授权与后续（2026-10-26）

用户在完整整合及独测/CR完成后明确要求先提交本版，替代上文修复阶段的禁止提交/推送；部署、安装和恢复群聊监听仍未授权。提交前确认当前分支feature/ask-user-question-card、upstream origin同名分支及github同名分支均与原HEAD无分叉；fetch两端后master新增2dda0f1c（仅wecom_kf呈现/文档/对应单测），本次固定提交已验收的1721707f合并结果，不引入这条未经本轮验收的新提交。295个冻结文件逐项内容一致，另3个删除项精确继承master；原wecom-cli/package-lock.json三peer生成改动排除，其他本任务及合并改动统一入库。源码与现有主控构建/113个Python AST、独测和CR证据一致，只增补提交记录，不重复业务测试。实际提交SHA及推送结果以Git记录为准；目标为已确认origin配置的Codeup与GitHub当前分支，不推master、不force。

后续建议顺序：先单独修W9测试入口强制隔离及3项Runtime陈旧断言，避免后续验证默认回退共享库；再制作Runtime+wecom CLI组合验收包，验证真实安装/升级、配对与历史签名兼容，并明确旧fingerprint待恢复任务处理；用户明确授权部署agent2后用当前分支部署，由配置好的智能体做真群@本人/闲聊/同账号人工发言、断网/重启/unknown与多任务多群验收，再做至少跨日的累计积分/状态及磁盘增长观察。agent2验收通过后再按用户指示合回master。本节不是部署或恢复监听授权。

仅fetch origin/github、只读比较，两端master均为`1721707f`；当前分支HEAD `ffd18b2f`，共同祖先仍`3b713e08`，HEAD...master为本分支独有9/master独有39。上一轮W6开发未先合入这些更新，这是流程遗漏；PG76/产品CR只证明当前旧分支源码组合，不代表最新版Runtime、签名插件及已安装版本兼容。本次不合并、提交、部署或恢复监听，原50文件交付冻结中产品源码未改变，新增本核查记录。

缺少的相关更新：`54d5befc`隐藏/重建Chrome渲染窗口重新枚举、零输入失败后的原业务接续（含共享win32-native-input）；`e44830e2`候选配对、筛选草稿/提交接续、超屏归位；`371a73a0`归位后原CLI调用内接续，并修Runtime productTrust历史根数量超过16时错误拒绝；`6f8d684b`对应Runtime0.0.29/BOSS0.3.27组合验收包记录及新版信任根。Runtime源码package基线0.2.22、master0.2.23，不能将其与组合包Runtime标签0.0.29混为同一版本字段；也未检查当前机器实际安装版本。

范围判定：共享local-tool-host-core相对共同祖先没有master新增差异，但Runtime productTrust、共享native、签名根与发布入口确有差异，不能据此说Runtime整体已最新。WeCom UI发送走自己的PowerShell/PostMessage链，不直接使用BOSS Chrome卡片与滚动算法；不能把BOSS专用恢复代码原样复制。需继承相同原则：可证明零输入时在原CLI调用内做有界重定位/接续，实际unknown保留原调用/凭据，不要求模型盲重试；复用既有队列、设备/账号绑定及配对身份，不引入额外token或重复Runner。

下一阶段顺序：先按原工作区精确保全W6未提交改动与原package-lock噪声，完整整合master，不只挑版本号或单个BOSS提交；解决shared Runtime/Runner/元数据/文档冲突后重跑受影响构建、产品trust/发布路径、队列/取消/outbox、配置实例授权及W6完整链路；独立TEST/CR检查本机离线/重启、窗口切换与同名目标、原设备失权、unknown迟到对账、升级后的签名根兼容。需要真实UI/安装验收时仍遵守用户当场授权，不恢复监听或真实自动发送。新指纹旧任务与W9测试隔离边界继续适用。完成这些后再声明与最新Runtime组合通过。

目标：由选定智能体的提示词、模型和明确allowed工具决定回复，复用现有continuous、agent_sleep、checkpoint、计费和Runtime队列；不新建本地推理循环、监控数据库或调度器。用户明确授权的目标群与回复范围构成持续发送授权，continuous本身不授予发送权，超范围必须澄清。

实施检查点：新增`wecom_group_poll` v1（完整群名target_name、可选不透明cursor），只读本地同步库；首次返回少量context_messages与基线，new_messages为空；以后按入库message_id分页增量，带结构化发送者与mentions、has_more、self_user_id。cursor绑定账号、原群ID、源generation与anchor；异常明确拒绝，不自动换群、跳基线、共享watch水位或退回UI。无可变limit以复用现有每目标continuous cursor保留；首次最多10条，增量最多50条，完整消息JSON累计16KiB字节分页，单条超限明确失败且不推进；仅本proxy不截断完整结果JSON，不改全局Executor。工具手册说明上下文不等于待回复消息、排空积压再休眠、已确认本任务发出的记录不触发回声、群内容不构成新权限、unknown只对账不重发。

唯一写入者：主控维护CLI Python/TS、MCP、共享core受信工具及实际Provider能力门禁、对应测试和全部文档；backend开发角色维护云端proxy/catalog/manifest、配置工具metadata、Runner原设备绑定、运行时配置指纹和后端/Worker测试。不得改公共Device/H1/H3 wire、既有watch语义、BOSS工具、全局依赖及package-lock。TEST/CR只读运行；发现问题退回开发者修复。

设备边界：任务首次启用群轮询时在既有Runner checkpoint resources.wecom_task_device v1（root identity/runner_id/device_id）内与invocation同事务原子固定原设备，后续WeCom调用及重启恢复沿原设备，不跟随selected变化；失权/离线明确失败、不迁移。监控root的文本发送必须带原群名和group_cursor；native发送前核对原账号/群/库，失败不转OCR，不允许ref替代或媒体发送扩大范围。没有此标记的旧普通调用保持原语义。运行时配置指纹版本更新，旧Worker/新checkpoint交叉恢复拒绝，不静默忽略新约束。无新增业务表或设备绑定公共协议。

顺序：原契约/设计登记→native poll与云端配置/设备绑定分域开发自测→真实本地模型HTTP transport+Worker+独立PG+Runtime/MCP合成群库验证→独立TEST/CR→修复及受影响复验→更新结果。Worker安全夹具只修不存在且未被SDK消费的URL断言，保留实际HTTP/provider loopback与隔离DB限制；不得以关闭预检冒充链路通过。

兼容/环境边界：运行时configuration_fingerprint整体升v2，因此部署前所有旧fingerprint的睡眠/待恢复任务（含非WeCom）按原配置变化机制拒绝直接恢复；不能称只影响监控任务。未来部署需先处理存量待恢复任务，不自动改旧checkpoint或重放未知副作用。实际Worker测试复用既有Windows信号hook平台适配，仅替代OS signal hook；不等于未适配的Windows生产Worker CLI已可启动。独立PG17缓存仅用于随机aid_test库，bootstrap精确省略三条不相关vector扩展/表/索引DDL，其余原SQL不变，vector不在验收范围。

验收：两个配置不同的匿名智能体携带各自system/model/tools与群上下文调用实际Gateway；精确发送模型生成内容；首次历史不回放、分页/多群/多任务不串游标、账号/库替换/anchor拒绝；睡眠后新Worker消费已保存游标、重复唤醒不重复发送、unknown不重发、停止无后续派发、计费沿原幂等、长期history有界。UI配置可选择WeCom工具且不扩大现有allowed。真实企业微信/发行包/多天运行不在本次验收范围。

### W6修复与验证记录

开发自测完成跨组件闭环后，独立CR发现P1：原库群scope与UI同名联系人可能拼接；已对带group_cursor的发送在driver前强制UI目标为group且原名精确一致，DB解析限制group，匿名反证变为effectnone、发送driver0调用，旧普通发送保持。另一P2为显式cursor:null跨云/MCP schema不一致会误入unknown核验；poll cursor及新send group_cursor均在派发前拒绝null，真实ToolExecutor normalize反证确保设备选择/入队0，省略字段维持原路径。旧test_proxy工具计数33→34随新增工具修正。

真实Worker还暴露通用12K尾截破坏结果JSON，已用native完整消息16KiB分页+该proxy专用preserve修复；不可用全局关闭截断替代。容量自测5000条完成fact循环后，5个目标最新cursor与一个原设备pin均保留，活动fact/order≤32；这不代表本机发送artifact有自动清理。

自测有效隔离证据：后端freeze2的20文件聚合`f98df630c4f4c9286be85261c1172b72fac1a8dc01ced51df6c5f11cc889e12f`；215项随机aid_test单测通过、0失败/跳过。前版有效PG67项通过（含真实Worker3、root/child设备pin6及相邻恢复/对账/睡眠），后版null/count改动不改变这些valid-path。客户端构建及direct group6、Python新poll6通过；独立最终全链结果见本节后续，不能把自测写成独测通过。

测试设施遗留（本轮未擅自扩改全局hook）：普通pytest入口的根session hooks可回退默认共享库，扫描并删除测试命名tenant关联数据；旧registration用例的真实SkillLoader还可能执行6个非空init_script建表/索引。本轮早期普通unit183项数值断言通过，但未证明其目标隔离，随后追加慢批次已按精确父子PID中止；现存输出不足以证明未连接/未写入，没有连接业务库追查。上述183不作为最终隔离证据，已在明确loopback随机aid_test入口以215项重新验证。后续验证禁止依赖默认DSN；该旧入口需单独修复，删除硬编码回退、禁止未证明隔离的自动清理/初始化。文档不记录连接凭据。

### W6独立结果（2026-10-26）

独立PG76项通过、0失败/错误/跳过：基于原67项范围，加真实并发首次pin唯一设备、3个损坏marker回滚和实际Engine大于12K完整JSON到HTTP模型的5个独立反证、以及4个工具可见性用例。两配置分别核对实际system/model/tools，初context不回复，新@内容与上下文到达模型，返回正文经实际Runtime/stdio MCP/native加密合成库精确发送、local-record确认；换Worker续cursor、切selected不迁移、竞争唤醒、人工同账号发言与回声区分、取消停止、unknown不重放和usage receipts幂等通过。模型远端用request-aware本地HTTP peer替代，UI发送驱动纯注入；不可将此称为真实外部模型回答质量或真群验收。

独立新进程单测125通过、3项既有失败；4个可见性用例从unit中明确deselect，已转上述随机PG验收，不计skip。三项既有失败为两个BOSS注册差异（test_manifest_matches_proxy_tool_classes、TestRecruitingSubagentMd.test_frontmatter_and_body_parsed）及catalog原provider总数过期（test_existing_three_entries_unchanged期望4而HEAD已有5个含runtime-recovery），均按HEAD基线核对，未顺带修改。新tool计数34、真实Executor显式null零enqueue和省略路径在freeze2新解释器复验通过。普通unit入口此次显式guard loopback及隔离库名，LOGS默认连接禁用；Worker实际trace同随机PG并做安全预检。

客户端独立最终计数（不重叠）：CLI48、Python group54、core16、Runtime wecom4、独立客户端探针9项，均0失败/跳过。CLI包含group-poll6、既有发送27及groups/mentions/manifest15；未受影响部分复用同源码结果。探针含fresh-process Runtime精确首尾空格/多行回复、UTF8字节分页/oversize和actual SDK旧能力零RPC/缓存代次等。初次Temp探针有字段归属断言误解和与开发者MCP争全局互斥的BUSY，修正探针并串行重跑后通过，未修改生产锁绕过。原selftest SQLite连接上下文未close导致两次Windows清理失败已修test closing；旧manifest位置/计数断言随新增工具更新；这些初次失败不当作通过证据。

完整产品CR通过，无未解决P0/P1/P2；独立复核原同名联系人P1红绿、null P2真实Executor4项、原设备/root-child边界、preserve正常/恢复路径与5000fact有界。freeze2后端20文件hash全部一致，客户端受影响24文件冻结一致；主控核对22个变更Python语法、暂存0及原package-lock哈希未变。测试设施W9、真群/多天/发行包及本机artifact保留仍是明确未验/待处理项，不用本轮CR覆盖它们。

独测完整命令/初始失败/冻结边界证据：`%TEMP%/wecom-w6-independent-7b57fce8/independent-w6-final-evidence.json`。收尾确认0个aid_test库/连接、本批API/Worker/MCP均退出，主控核对任务元数据/原data/pid后仅关闭本任务64303 PG helper，未动其他服务；测试二进制缓存保留。当前分支`feature/ask-user-question-card`、HEAD `ffd18b2f`不变，未暂存、提交、推送、部署、执行真实UI发送或恢复群聊监听。

## 2026-10-26 MCP与Runtime发送对账接线

基线`4c024796`，用户授权继续接入现有发送对账能力，独立测试与CR；不提交/推送、部署或恢复监听。按高风险流程，主控开发MCP/Runtime并自测，后端开发角色限域写Python代理/精确scope及其测试，源码冻结后两个独立角色分别TEST/CR。保留原package-lock生成改动，不操作真实群发送。设计沿[§4.3.2](../design/wecom/wecom-cli-localdb-design.md#432-对账能力mcpruntime接线2026-10-26)。

实施共同检查点（唯一写入者）：主控拥有`clients/wecom-cli/src/mcp/{toolDefs,server}.ts`、共享core `legacy/{providers,providerManager}.ts`、相关TS/MJS测试及所有设计/计划/契约；后端角色拥有`src/local_tools/`必要注册/代理/repository/read-only scope与Python测试、`subagents/recruiting-operator/SUBAGENT.md`；另限域维护`src/services/agent_runner/local_recovery.py`在原严格参数比较前的status来源证明适配，并在overlay_flow.poll_operation同源适配Cloud ID与native receipt等价，原owner/phase严格比较及_known不变。不修改Device/Host/H3公共wire、其他Runner核心、outbox格式或全局package/lockfile。所有额外必要文件先明确责任，不让两个角色并行写同文件。

成功标准：真实stdio MCP发现/校验/调用到原只读operation，Runtime只给wecom路由并保留readonly/effectnone；旧CLI缺新能力明确拒绝，旧6工具不受影响，缓存随子进程清除；原发送unknown的receipt可由可信普通/late结果提取，cloud模型只给send_invocation_id，跨主体/会话/设备和伪造scope零派发；detached hold只允许这次精确只读查询且原hold/unknown不改；查询unknown不等于发送confirmed，默认工具价非零也不误扣本地工具费；原发送绝不重放。原Runner verification等待不自动解除，不把它扩成新的恢复平台或持续任务验收。

计划：登记新增schema v1与2.9共同边界 → MCP工具及Runtime受信/实际能力门禁 → 后端代理与来源scope/收费 → 匿名合成库或注入边界的生产消费集成测试 → 独立测试/CR并修复到通过 → 更新本节。真实群聊、真实配对/发行包安装和多天运行仍另验，不能用mock冒充；若真实数据只读核对确有必要，只读且不入项目日志。

### MCP/Runtime接线验证过程记录

主控已完成CLI、共享core/native和Runtime构建；依赖按原lockfile执行npm ci --ignore-scripts，未修改package/lockfile。初次core缺tsc/原生包声明由补齐依赖并先构建native解决；测试首次把ProviderSet.shutdownAll误写为shutdown，已修测试并只停止所属MCP测试子进程；旧manifest数组位置断言随新工具插入而失败，改按工具名取对应schema后11项通过。这些失败不计作产品通过证据。

TS freeze2自测：新WeCom兼容/stdio4项+既有续筛/结果解析9项共13通过，Runtime wecom定向4通过，CLI manifest/progress11通过，无跳过。真实stdio验证缺失凭据读取从MCP经Runtime原outbox ACK完整保留unknown/effectnone，未读真实账号或发消息。独立CR在freeze1发现额外必填参数被误接受（P2），已修required恰好receipt_id、可选字段仍可兼容；freeze2子集CR通过，完整后端待最终独测/CR。

后端新增权限触及入队/claim/started，使用本任务新建、loopback随机端口的PostgreSQL17.11 helper及随机aid_test_*库验证真实SQL，不连接.env或业务库。二进制来自[EDB官方下载](https://www.enterprisedb.com/download-postgresql-binaries)，ZIP SHA256为4b8db0930c38f6ef845db919551dedda3b6b845aeb0927b3d79a6e8e9e4537cf，仅开发测试依赖，无系统服务或业务部署；本地bin缓存保留。Windows pg_ctl stdout继承导致bootstrap未返回，但root依据自建data/postmaster.pid恢复所有权元数据并用psycopg2验证同helper，不接管旧helper。SQL验证及完整冻结结果待补。

只读对账的工具费固定0；原Runner _known不放开，cloud send_invocation_id与native receipt_id在resume及poll两处以同一原发送证据等价验证，不能省略取消或重启归属证明。旧BOSS注册面已有两项基线差异（manifest/测试期望漏boss_lookup_candidates、boss_greet_selected），不借本任务恢复无关BOSS能力，独立阶段须保留失败事实。首轮测试临时目录清理曾被自动审批返回blocked by policy而拒绝，未说明更具体原因，保留该匿名目录，不影响源码和测试通过结论。

后端全freeze1独测：147项通过、2项精确既有BOSS注册差异失败；初批相邻PG53项通过，另外4个旧BOSS真实worker用例在预检因当前dashscope SDK缺base_compatible_api_url而setup失败，teardown又遇Windows无os.killpg（1 error）；未将这些旧worker业务记为通过，独测仅按精确PID/父进程/命令清理其自有4个API子进程。原SQL测试随机库均DROP、helper保留给修后独测。此后仅重跑相关status/proof/admission存储范围，不为本任务修改全局SDK安全预检或通用worker夹具。

完整CR发现排队失效P2：原测试仅断言不派发但保留queued，遗漏设备队头会持续重选。最小修复为来源scope失效只结束尚未started的status自身，原send/hold/_known与终态幂等不改；其他未决hold仍阻断。同步补崩溃后消费该server零派发失败的有限私有证明，不把source解析失败当一般恢复许可，不新建调用。额外必填schema兼容P2在TS freeze2已独测/CR闭环；后端随后fullfreeze2复验通过前两点，但独测实际混合end_wait又发现server拒绝status自身被错误当作未完成而phantom hold；fullfreeze3仅对严格服务端no-dispatch证明识别该status自身完成，补持久hold查询所需列，未加全局错误码/未动local_wait策略，最终均独立转绿。

### MCP/Runtime最终结果（2026-10-26）

完成MCP工具及说明、Runtime受信第7项/实际Provider能力门禁、云端原send ID适配与原设备scope。初次调用若当前显式受权设备B与原send设备A不同，拒绝并需选回原设备，不自动换机；已有durable phase按原保存device核对。独立对账不改变原发送unknown/hold或自动解除原Runner verification等待。只有status自身确证未派发的失败可恢复消费已有事实/确证物理完成，不能把它用于原send或新业务授权。

- 构建：主控当前CLI、共享core/native、Runtime构建通过。最终metadata增量仅重跑CLI build及真实stdio断言；core逻辑/Runtime源码与已有构建证据一致，不机械重复全量。MCP初始化前512字符实际断言含Windows、UI登录解锁、单目标、未知不重发、只读查询成功不等于confirmed、真实触达和发送剪贴板覆盖不恢复。
- 独立TS32通过、0失败/跳过：core13、Runtime wecom定向4、CLI manifest/progress11、另4个真实SDK stdio独立场景（原结果ACK丢失仅重投outbox，不重调用Provider；旧CLI/额外required零调用拒绝、可选字段/respawn兼容等）。其中Runtime4按同hash复用，其余28在最终源码重新执行。
- 独立Python单测173通过、2失败、0跳过：`venv/Scripts/python.exe -m pytest tests/unit/local_tools/test_wecom_send_status.py tests/unit/local_tools/test_wecom_proxy_tool.py tests/unit/local_tools/test_proxy_tool.py tests/unit/local_tools/test_local_wait_recovery.py tests/unit/local_tools/test_write_result_billing.py tests/unit/local_tools/test_repository_state.py tests/unit/test_agent_local_tool_registration.py -p no:cacheprovider -q`。两失败精确为本节前述BOSS旧基线，HEAD AST差异与当前一致，未纳入修复；实际Wecom7项装配通过，不称全库全绿。
- 独立真实PG63通过、0失败/错误/跳过：本轮22、相邻proof/admission/store31、独立7及取消/旧hold3。包括原/late来源、跨主体/会话/设备拒绝、三个准入点、只有精确原hold只读旁路、configured默认付费重复result零工具费、失效队头后合法调用推进、拒绝后重启只消费已有失败、借用/客户端marker/坏版本/缺字段拒绝、mixed end_wait与历史held投影。只读查询confirmed/unknown都不改原发送审计/hold。
- PG使用本任务专有helper与原isolated入口随机aid_test库，Temp wrapper只精确省略vector extension/chunks_vec/hnsw3条无关DDL，各断言1，其余原init及业务SQL不变；pgvector未验。原4个BOSS Worker在SDK预检setup失败、Windows teardown错误已单列，不纳入最终63；独测按精确归属PID清其4个API进程。最终随机测试库及连接均0，root已核任务数据目录/PID后停止helper，原bootstrap捕获管道随之以TimeoutExpired退出（设施问题，非pytest结果），没有接管业务DB/旧helper；二进制cache和匿名证据保留。
- 完整独立CR通过，无未解决P0/P1/P2；审查者另执行22项匿名证明/归属/恢复断言通过。首轮CR harness全socket禁连误拦Windows asyncio自管道，先建loop再禁连后绿，未改产品源码。原`_known`函数及`write_result` AST与HEAD一致，无新恢复平台/后台loop/重发队列。

完整29文件freeze3前后hash一致，聚合`0206b5f523b4a11aebec88b89aa095683045e435ad152d4f5e65ddddf5c18be3`（sorted path:sha256，LF，无尾LF），最终仅更新文档验收状态。全命令/证据在`%TEMP%/wecom-status-runtime-independent-58f623d4/backend/independent-full-final-evidence.json`，冻结`%TEMP%/wecom-send-status-full-freeze3.json`。主控复核原unknown门未改、源码hash无漂移、无暂存；HEAD仍`4c024796`，原package-lock三peer生成变化继续保留。无提交、推送、部署、真实群消息或监听恢复。W6持续任务/真实Worker、配对发行、多设备和多天运行验收未因此完成。

### MCP/Runtime接线提交核验（2026-10-26）

用户在最终验收后明确授权提交并推送当前分支，替代上文接线阶段的禁止提交约束；部署、恢复群聊监听仍未授权。提交前fetch origin与github，当前`feature/ask-user-question-card`与两端对应远程分支均无分叉；29文件逐项匹配最终交付清单，原package-lock三peer生成改动保留不提交。复用同源码CLI/core/Runtime构建、独立PG63/TS32、单测173通过另2个HEAD旧BOSS失败及最终CR通过证据；主控追加隔离数据库guard下的注册/启动import/0价检查通过，不重复业务测试或启动真实群聊。未顺带合并新master、不进行发行包安装或部署。

## 2026-10-26 发送确认与对账恢复（W4）

以下保留CLI实现及`4c024796`提交前后的验收记录；当前MCP/Runtime增量进度以上节为准。

本轮基线`6daf84db`，仅在wecom工作树修复发送确认和只读恢复，禁止提交/推送、部署、监听及真实群消息发送。保留既有package-lock生成变化。高风险流程：主控开发、自测 → 独立测试 + 独立CR → 主控整合核验；数据投影由独立开发者限域修改，其余由主控统一整合。沿用[本地库设计§4.3.1](../design/wecom/wecom-cli-localdb-design.md#431-不确定发送的只读对账2026-10-26w4)，不新增Runtime恢复平台。

成功标准：已知多片段可信全文能匹配，未知/提及/截断不误报；第51条之后可达，上限/歧义为unknown；发送前加密凭据落盘，发送后异常及独立进程重启可通过原凭据只读对账，零自动重发；账号/源代际/锚/损坏凭据与时间窗拒绝假确认；结果附脱敏诊断。测试使用匿名合成库及注入发送边界，任何真实库核对仅只读，不把它声称为新真实发送验收。

实施顺序：补严格投影和回归；接发送专用分页核验与DPAPI证据；新增CLI只读send-status；冻结源码后独立测试/CR，修复到通过并同步本节。freeze1主控build与61项定向测试通过，独立Python75项及另3个边界通过；独测另复现2个已知无动作receipt的假confirmed：checkpoint取消零派发、navigate_required后搜索失败，之后出现相同文本可误确认。此轮不通过，统一补本阶段凭据删除、失效失败停止及readonly最终存在性复核，不靠intent文案回避。最初开发测试桩漏raw_last_line编译失败、子进程fixture安装元数据不足导致基线回退已在freeze1前修正；保留失败事实。freeze2独立47项TS和6个业务case通过，但新增真实fsync失败注入复现：wx自建完整密文已写入、flush失败返回前未清理，仍可被后到同文误确认。再次判不通过；修由保存函数统一清write/flush/close失败的owned文件，自清失败的unknown和ID不得被两层包装改成none，freeze3最终独测与CR通过，结果见下。

### W4最终结果（2026-10-26）

- CLI当前源码`npm run build`通过；独立测试隔离LOCALAPPDATA执行`node --test dist/tests/send-receipt.test.js dist/tests/message-send-localdb-verify.test.js dist/tests/message-send-v2.test.js dist/tests/manifest.test.js`，49通过、0失败、0跳过（3/27/13/6）。此前相邻图片/文件发送回归在同一未变源码已通过，不机械重跑。
- 独立匿名业务恢复7项全通过：真实CLI非法/缺失ID；checkpoint取消；导航无动作退役；正常二阶段与派发后取消可多次只读收敛；二阶段保存失败不返回旧ID；真实Windows FileShare.Read句柄阻删时unknown停止；完整真实DPAPI写入后fsync注入失败自清。不是7次真实群发送，也不把与TS重叠的业务意图累加为唯一场景数。
- Python独立执行旧query66、新send-messages9、另3个独立严格解析边界，共78通过、0失败、0跳过。开发者更广相关136通过另记，不冒充独测或重复累加；Python驱动从freeze1至freeze3的hash保持`1bac54ed87b1c9bf79cc19c04f647d6be7bdfd33cac35e30c5dd41cbf33e8fc3`。
- 独立CR最终通过，无未解决P0/P1/P2；两类P2（已知none阶段凭据、owned文件刷盘失败残留）均经过红→修复→独立绿。CR另实际复核新增退役4项、receipt3项及包装1项；其早期外层GBK日志打印失败不算产品失败，也不据此虚报细分测试通过。
- 冻结16文件审查前后hash全部相符；freeze3聚合SHA256为`89b8e2958af67f6a093547e6f749542767036911e457be3871f615b809560865`（sorted path:sha256、LF、无尾LF），最终仅更新验收文档状态。独测证据在本机`%TEMP%/wecom-w4-independent-3948d58e/independent-w4-final-evidence.json`，冻结清单`%TEMP%/wecom-send-w4-freeze3.json`；测试自有锁进程、合成库/凭据已清理，fsync注入finally恢复。

W4 CLI实现完成；不声称服务端送达/已读、业务exactly-once、断电持久性或新的真实群发送验收。子进程exit17证明进程退出后的原凭据只读恢复，不能冒充OS/电源故障。已知none到失效之间的强杀、所有失效IO失败及窗口内人工同账号同文仍按设计保守边界处理；本轮没有新全局账本、后台恢复平台或自动重发。CLI-only的send-status未接MCP/Runtime受信能力，W6集成、原W2/W3/W5/W7/W8继续保留。

本轮未暂存、提交、推送或部署，未恢复群聊监听，也未操作真实UI发送新群消息。HEAD保持`6daf84db`；原package-lock三处peer生成变化hash保持`baa047a299bcc9d514512ffa7f35d1c7db964a0bc66f19c631d611bfff080dda`，Runner/Runtime及前轮搜索/剪贴板代码未动。

### W4提交核验（2026-10-26）

用户在验收完成后明确授权提交并推送当前分支，替代上文修复阶段的禁止提交约束；部署和恢复群聊监听仍未授权。本次仅纳入W4代码、回归及关联文档16个文件，原package-lock三处生成变化保留。提交前fetch origin与github，确认当前分支`feature/ask-user-question-card`与两端对应远程分支均无分叉；源码逐项匹配freeze3，复用主控同源码CLI build、独测TS49/恢复7/Python78及最终CR通过结果，只补充提交记录，不重复业务测试。不引入新的master变更，不进行新UI/发送实验。

## 2026-10-26 继续修复

用户明确要求完成完整搜索和剪贴板恢复，走独立测试与CodeReview，不提交、不部署、不恢复群聊监听。基线为`d03dc07b`，保留原有CLI package-lock生成变化，不混入Runner/Runtime修改。沿用既有搜索和发送器，只修复发布凭证/恢复编排；先以新回归证明旧版Close后版本变化遗留nonce，再对最终工作树独立验证。

本轮成功标准：完整长群名搜索与连续搜索可用；原纯文本/空剪贴板在成功与可处理失败路径均按当前归属安全恢复，外部后到内容不被恢复动作覆盖；富格式、未知布局及系统写入/关闭失败安全中止；保持原草稿保护及unknown不重发。主控真机仅执行搜索、非发送清理与剪贴板核对，账号/群消息/原剪贴板正文不入项目文档。最终结果更新本节及W1，不以旧副本绿灯替代当前真机验收。

本轮新增边界：复制查询后首次读取失败、尚无copySeq时保守不恢复，但原内容可能已被替换；已补错误分类及真实Body回归，未恢复时明确告知用户，确已回收自有nonce时不虚报原值丢失。原测试5ms时限两次偶发首例失败保留记录，测试改为100ms（产品默认1500ms不变），100ms副本连续3轮及当前源通过，不声称直接捕获了两次失败的调度原因。

真机纯文本还附带`EnterpriseDataProtectionId`，用户从纯文本来源重新复制后仍存在。只读[官方EDP接口](https://learn.microsoft.com/en-us/windows/win32/devnotes/edpgetenterpriseidforclipboard-function)实际返回成功及NULL（非企业数据），此前‘任意额外格式一律拒绝’误拒个人文本。最小兼容只接受这一种额外标记且官方接口成功/NULL；实际企业身份、接口失败/缺失及其他HTML/图片/文件/未知格式仍拒绝。不读取、记录或修改企业身份，非NULL返回缓冲按官方要求HeapFree；所有证明仍受现有稳定sequence和原子CAS约束，不支持企业数据降级或通用富格式备份。若记录恢复后保护状态，须由本轮真机只读复核，不凭写入成功推断。

### 本轮最终结果与复现范围

- 实测OLE可能在原生写入后同一sequence、同一owner下首次返回空、下一次却返回nonce。`GetPlain`仅首次null/零格式时延迟25ms重新取对象一次，后续完整格式/EDP校验不变。copy阶段空值不建立copySeq，而是在原有1500ms期限内等非空可信结果；连续空读或读取失败且原文未恢复时明确提示，不盲写，也不自动重新输入查询。
- 最终独立策略/流式空读23场景及三个PS入口全部通过，0失败/跳过。独立CR通过，无未解决P0/P1/P2；实际执行生产GetPlain、Echo、搜索Body及原生policy helper，仅OS边界替换，测试不操作真实UI/剪贴板。先前Close版本、复制后读异常、copy永久空读的红灯均保留，不以旧轮次绿灯代替新边界验证，各分组不累加为唯一用例数。
- 主控CLI build与相关Node/PS定向42项通过（搜索、发送编排、图片/文件发送守卫与三个真实PS测试入口），无真实发送。最终Common SHA256为`05fe0c35fbb015996439d8c04436a0970b0ffe6c77e3ebe725efd0abedfb5795`；policy/空读回归为`03f6749bd2ecd78efb22d6a87c12559ea4fbf8bd1ec926ec8d240f0b245180e3`，Native CAS替换/回滚算法与基线逐字一致。
- 最终工作树真实CLI连续两次完整长群名搜索均`OK`；两次`full_copy_verified=true`、原剪贴板全文Ordinal相等、恢复后官方保护状态为个人。安全清理搜索后仍全文相等、个人状态一致、聊天草稿为空。UI搜索项名称带成员数后缀，验收按该已观察显示项核对；未放宽产品query的完整复制规则，也未执行select/read-history/send或监听命令。
- 最终真机证据保留本机任务Temp的`live-search-result.json`与CLI search artifacts，只有匿名布尔/计数进入项目记录，原剪贴板及群历史正文不落项目文件。最初因HTML/个人系统标记而拒绝、以及验收脚本误把成员数后缀判为不匹配的事实保留为过程失败，不计成产品成功；最终双轮为修正后的独立新调用。
- 空剪贴板、富格式、真实企业身份和系统故障路径由隔离模型验证；本轮真实环境只验证当前已标定布局的个人纯文本，不声称任意DPI或受保护企业数据可用。原被覆盖的历史剪贴板原文未保留，不能补称找回；进程强杀/系统无法写入也不保证finally，仍明确失败。没有新增持久备份、队列、监听服务或恢复状态机。

在`clients/wecom-cli`目录可复现无UI回归：

```powershell
npm run build
node --test dist/tests/input-locator.test.js dist/tests/powershell-input.independent.test.js dist/tests/chat-search-v2.test.js dist/tests/message-send-v2.test.js dist/tests/send-image.test.js dist/tests/send-file.test.js
```

本轮未暂存、提交、推送或部署；HEAD仍为`d03dc07b`。原package-lock生成变化保留不动，Runner及其他任务代码未改。W1已完成，W2扩展布局验收及W3–W8的产品编排/长期联动等后续项目仍保留，不因CLI本次修复而宣称完成。

### 本轮提交核验（2026-10-26）

用户在验收完成后明确授权提交并推送当前分支，替代上文修复阶段的禁止提交约束；部署与恢复群聊监听仍未授权。本次只纳入完整搜索/剪贴板恢复代码、回归及关联文档共8个文件，原package-lock生成变化继续保留不提交。提交前fetch确认upstream为`origin/feature/ask-user-question-card`且无分叉；Common与policy回归hash分别匹配上文最终验收版本，复用同源码下的CLI构建、42项定向测试、独立测试/CR及最终真机双轮结果。没有新增UI实验、群消息发送或监听进程。

## 后续处理清单（2026-10-23 提交检查点）

用户要求先记录问题、后续处理并提交当前代码。本次保存开发检查点，不恢复群聊监听、不部署，也不将未完成验收记为可正式上线。

| 编号 | 待处理内容 | 当前证据与后续验收标准 | 状态 |
|---|---|---|---|
| W1 | 完整群名搜索与剪贴板恢复 | 已应用旧补丁并补齐个人标记、OLE空读及未恢复提示；最终独测23项与CR通过，当前源码真实双轮完整搜索、原文恢复、个人状态、草稿和清理核对通过 | 已完成（2026-10-26） |
| W2 | 搜索与编辑区定位稳定性 | 工具栏误选已修；OCR横向裁切、图标先于正文、正文合并×已有回归。继续验证窗口尺寸、DPI及焦点变化，布局不能确认时须安全中止、保留人工草稿 | 待扩展验收 |
| W3 | 人工同账号发言与自动出站消息区分 | 配置智能体工具手册按确认出站ID排除回声，隔离真实Worker验证自消息不回声、本人手动@发言仍回复；真实模型效果另验 | 隔离接入完成 |
| W4 | 不确定发送的核验与幂等 | 本轮一次unknown经原群可信全文只读核对，未重复发送；严格投影/分页、加密凭据与CLI只读重启对账完成；独测TS49/恢复7/Python78及CR通过，unknown不重发 | CLI/MCP/Runtime接线已完成 |
| W5 | 普通群友闲聊不插话 | @本人和非@承接已实测；纯群友闲聊未遇真实样本。补第三人互聊、话题切换及多条@合并处理样本 | 待补验收 |
| W6 | 真正Runner与Runtime持续任务联动 | 两配置prompt/model/tools经实际Gateway到Runtime/MCP/CLI合成库，换Worker续游标、竞争唤醒/停止/unknown及用量幂等独测通过；真实群/多天另验 | 隔离集成完成 |
| W7 | 多任务共用企业微信设备 | 本轮只串行操作一个群。另验多会话任务与单任务多群，复用既有设备队列/锁，保证搜索/切群/发送的连续操作不相互串扰 | 待并发验收 |
| W8 | 时延与读取故障恢复 | 约10秒轮询另计CLI耗时，实际单次发送约20–27秒，不能承诺10秒回复；记录各阶段时延并定位瓶颈。补CLI无结构化输出、断连后的游标保留和pending恢复 | 待处理 |
| W9 | 测试入口强制隔离 | 按[独立计划](plan-test-database-isolation-runtime-assertions.md)完成无DB默认、精确库/清理证明及Runtime旧断言修复，独测/CR通过，真实Docker另验 | 已完成开发 |
| W10 | 长期本机证据保留 | 新poll无持久水位/截图且解密产物清理；既有send/search artifact与receipt随操作增长，生产前确定保留/磁盘策略，不自动删除未知发送凭据 | 待运维策略 |

W1的[历史补丁](patches/wecom-search-clipboard-recovery-pending.patch)已于2026-10-26应用并进一步修复，仅供追溯，不要对当前工作树重复应用。修复不持久化用户剪贴板原文，历史原内容未保存不能补称已找回。

本轮既有master合并目标为`3b713e08`；提交前fetch后远程master已另有7个提交，当前检查点不引入这批未经本轮验证的新变更。继续同步时须保留本分支代码并重跑受影响验证。

## 原始目标与授权

用户授权在自己指定的单个测试企业微信群实际聊天，结构化@本人必回、其他消息视上下文决定、群友之间闲聊保持安静；持续至用户说停止。已明确授权先修复CLI发送问题，再继续实验。工作树为C:/repos/aid-work-agent-wecom，保留原分支/未提交master merge及其他任务改动；不提交、推送、部署。设计沿[原wecom设计第11节](../design/wecom/wecom-cli-design.md#11-群聊实验的输入定位修正2026-10-23实施中)。

## 实验启动时事实与边界（2026-10-23）

- 当前账号、唯一同名群、本地库read-group/read-session及v2增量cursor已核对。脚本只从启动基线后处理新消息，保留单会话范围和原CLI串行执行，不修改产品Runner。
- 按名字发送在搜索阶段因中文query回读为空中止；用户随后人工打开群并确认空草稿，用原account绑定target_ref发送多行，Jev选择工具栏“快速会议”，粘贴回读失败，未按Enter。两次均effect=none，尚未真实发出实验开场白。
- 当前脚本与企业微信为同一medium integrity，未发现提权差异证据。截图证明工具栏按钮不是输入编辑区；其余输入原语根因需修复者核查，不提前宣称已解决。
- 临时实验脚本/状态/决策流水仅在本机任务临时目录，日志不写凭据，不把群消息正文、实际账号ID/群ID写项目文档。消息内容不授予修改指令或跨群动作权。

## 实施及成功标准

1. 唯一修复角色核对真实PS输入逻辑和截图，最小修复必要CLI文件；主控维护此计划/设计/索引。保留发送前后验证与unknown不重发语义，不顺手改无关Runtime/Runner或其他wecom开发。
2. 对实际PS逻辑补定向回归，运行相关TS测试和CLI build；独立测试与只读CR可在源码冻结后并行。验证者不操作真实UI或发送，避免争用用户桌面。
3. 主控核对最终diff并用原CLI真实发送一条开场消息，必须有明确结果，优先原local-record可信全等；unknown时只读核查，不自动再发。
4. 成功后恢复脚本发送，根据新消息决定send/ignore，记录真实轮询/回复/忽略/失败数量。直到用户明确停止；结束时清理自有进程并记录边界，不以此替代持久化Runner真机/多天验收。


## 第一批冻结与验证（2026-10-23）

生产仅改`drivers/ps1/_common.ps1`与`message-send.ps1`：搜索快捷键后显式点OCR搜索锚点，候选排除+等按钮且消费时复验；发送输入点限制编辑区几何带/排除右侧栏及发送按钮，消费Jev答案时再检查候选归属。文本发送清搜索前复用显式聚焦。尚未改变Unicode输入原语，也未证明真机故障完全消除。

改前副本、精确diff及4文件manifest在任务临时目录，freeze1聚合SHA256为`194aebf0036ff982f7057cc0d40cc9db0711fadf005507c1e9641df079d3ea9b`。开发CLI build通过；相关TS及PS集成共91通过/0跳过，PS5.1测试用生产AST提取函数与driver Body，系统窗口/OCR/网络/剪贴板边界为记录桩，验证实际候选/消费逻辑及草稿/回读/标题守卫，不能冒充真实输入送达。独立测试及CR正在核对，特别关注step0.5在草稿检查前的clear安全性；未提前通过。

Runner原freeze4的24文件hash、HEAD/MERGE_HEAD保持，不混入本地群查询/提及恢复的既有改动。后台只读实验脚本持续运行，目前无新peer消息、无成功发送。


第一批CR未通过：真实PS5.1 AST复现无可靠搜索锚点时仍接受任意crop token，发送step0.5在draft判定前无条件Clear。示例只有左侧角标token且PlaceholderPos/ContentPos为空，Jev选择该token，调用序列为CtrlF→click→clear→draft错误；开发测试把Clear整体桩住，未证明内部编辑焦点已切换。复现未操作真实UI或用户草稿。修复回原唯一写入者：空搜索无动作，未知状态失败；非空仅可信搜索专属×点击后回读，不再依靠CtrlA/Delete清当前焦点；Focus候选必须对应可靠PlaceholderPos/ContentPos且通过几何/消费门。补执行真实Clear函数的独立回归后再审，不以第一批91通过替代安全闭环。


独立测试对freeze1精确副本执行实际PS原语/driver决策，17场景中16通过、1失败；失败就是无锚点Jev token仍导致真实CtrlA/Delete清理。其余包含实际Unicode消息序列、chord状态恢复、回读完整/缺字、编辑点/草稿/剪贴板上限、title、unknown单Enter与VerifyOnly零输入。未触及真实UI。

第二批安全收敛：现有截图搜索为空，没有非空query右端×的可靠实测证据；不得猜按钮坐标。先实现empty无动作、unknown/has_content缺可靠专用清理证据时失败且零破坏性快捷键，可靠搜索锚点严格验证。该临时限制不等于完整非空清理已恢复；独测CR通过后，主控先用已开群/空搜索验证原ref发送，再单独执行搜索只读交互采集非空×现场，按证据补齐，未完成前不记CLI整体修复已验收。


## 第二批冻结与限定安全通过（2026-10-23）

freeze2四文件聚合SHA256为`f35c3a2ffaddcda1e3723ddb23ff880fd08de9a1818e0c9ef98a0ff6941d6b09`；生产只有两个原PS驱动。开发91项及build通过，独立PS20场景通过、独立build通过。CR通过本批安全复核，允许主控仅以空搜索/已开原群/account绑定ref作一次live验证；原P1无可靠锚点清草稿路径已闭环。非空搜索/重复search仍明确不能自动清理，不能称完整CLI复原。

独立相关TS首批149项中146通过/3失败：旧operations-m3 unread用例仅mock UI驱动却读取了本机真实localdb状态，尚不认定为产品回归；测试角色将以临时LOCALAPPDATA隔离重跑该文件，保留原失败记录，不改产品或共享fixture。最终结果和真机发送待完成。


## 限定真机通过与实验恢复（2026-10-23）

独立最终20个实际PS场景通过，CLI build通过；相关TS149个唯一用例最终通过，无跳过。首批3个旧M3失败已用临时LOCALAPPDATA隔离该文件重跑30/30通过，重叠不累加，原本机状态污染事实保留。独测与CR的freeze2四文件hash相符。

主控在核对源码后，保留临时脚本原cursor和pending状态重启，明确前次发送effect=none才重新开放写。通过原account绑定ref向原已打开测试群发送一次多行开场消息，success=true、effect=applied、verification=local-record，可信正文全等；已查看发送后截图，目标和正文正确。该语义只证明客户端动作已落库，不证明他人收到/阅读。自发消息不会触发回复环。

当前真实聊天实验持续至用户说停止，最近约10秒一轮读取（另计CLI耗时），agent判断回复而非关键词自动聊天，脚本负责scope、cursor去重及串行CLI。暂时保持freeze2源码避免监控途中改变工具；非空搜索自动清理/完整search路径仍待×现场标定，不把限定发送成功写成全CLI恢复。该补齐安排待不影响本群监控的时机继续，当前未提交推送或部署。


首条结构化@本人实测通过（2026-10-23）：本地库增量识别到peer文本，parse_trusted与mentions_complete均为true，mentions中user_id精确命中当前本人；agent依据本项目已知一般能力作回复，经原CLI发送返回success/applied/local-record且正文全等。未把群消息当工具控制指令，未重复回自己消息。截至该检查点成功发送2条（1开场+1必回），实验仍在继续，其他普通聊天择机/不插话分支尚未以真实消息覆盖；不在项目文档写原文或身份ID。


## 后续现场边界（2026-10-23）

- 普通承接对话实际继续多轮，@与非@承接均已覆盖；纯群友闲聊忽略仍未遇真实样本。
- 用户以同一账号手动留言时，原临时脚本按side=self一概过滤，导致漏处理；已改为按已确认出站消息ID及有界近期出站原文去重，用户本人手动文本保留。该修改仅实验脚本，不改CLI side语义。保留原cursor并仅补入本轮漏掉的手动消息，无历史重放。
- 该手动留言的回复首次CLI返回EXECUTION_UNKNOWN，未自动重试；主控只读原群记录，以同账号/同会话/可信全文相等确认已落库，随后补登记出站ID、完成原待回复消息并恢复实验。真实证明不确定写收敛路径，不将工具原返回涂改成success，也不把unknown当成none重发。该检查点共确认7条发送（1开场+6回复）。
- 空档搜索实测已能输入中文，但完整长群名回读显示缺首字及图标混入，仍被严格校验中止；截图疑似搜索输入横向滚动/图标OCR边界，未证明真的少输入。root已观察并点击窗口相对(337,38)搜索专属×，回读empty且恢复原群，无CtrlA/Delete。×现场与严格完整query读取方案交唯一修复者只读核查，生产先保持freeze2；不可凭疑似裁切放宽回读为成功。
- 搜索测试期间有一次临时监控CLI未返回结构化结果，只读增量cursor保留；由主控在确认原群/账号和没有未知写后恢复。后续unknown发送另经上述精确记录核验收敛，未丢失pending或重复回复。

## 完整搜索回读实证（2026-10-23）

主控在保存增量游标、暂停轮询且无pending时，使用原Focus/SendText输入目标长群名，再通过独有剪贴板哨兵、sequence变化及企微owner检查观察Ctrl+A/C复制结果。Ordinal全文精确相等，复制长度与预期均为9，原纯文本剪贴板恢复成功；无Delete/Enter、无消息发送。截图证实搜索框横向裁切，UIA不提供可读Edit/Value/Text pattern，不再把缺首字解释为输入丢字。点击本次现场确认的搜索专属×后回读empty，监听按原游标恢复。生产仍freeze2，准备最小全文校验兜底和点击清理修复，待独立验证；不放宽文本匹配，不将诊断成功冒充完整search验收。

搜索修复proposal3仅在Temp副本冻结，6文件hash为`7a5d1fbc7c43cf1c50cee0091dca8a2dd4ac86338f2a4ea0a487a3c88820ee15`，未应用生产。原OCR不等才触发精确复制兜底；清理按真实内容锚点、唯一同行右邻+及原窗口几何保护点击搜索专属点，回读empty，拒绝未知布局而不使用Delete。CR确认一项P2：系统EmptyClipboard后Set失败有原内容风险，但仅诊断reason可见、调用方给出普通query不匹配错误，需明确用户可见恢复失败；交唯一修复者在新副本修复，原proposal3保留供独测。不得先标通过或把OS记录桩测试当真实剪贴板验证。群聊监控持续运行在原freeze2。

proposal4已通过独测及CR后精确应用6文件，聚合SHA256为`51b5152d0f665c48d7194ea2634c4ed800e7edbde9f98e4c128f811ff51e0dc6`。P2已闭环：原生同锁单次原文补救，WRITE/CLOSE/恢复读取/owner关闭异常均否决匹配，调用方明确告知剪贴板可能未恢复。独测9个真实C#算法故障注入、26个实际PS格式/复制/搜索Body场景及TS入口2套件通过，无失败跳过；原20个未改变的发送边界结果复用，不累加重叠。C#的OS边界为managed替代，未冒称真实OS竞争验证。应用前后六hash、Runner24冻结hash与HEAD/MERGE_HEAD不变，主控CLI build通过。

完整search真机验收仍失败：原OCR裁切后复制helper被search_layout_changed保护提前中止（无复制、无消息发送），随后主控只读重读同布局为有效，尚需区分OCR波动与PS5执行上下文。未放宽保护或标完整通过。主控点击已观察搜索×后确认empty，监听按原cursor恢复，pending为0；唯一修复者继续只读诊断，生产proposal4保持冻结。

## 真机剪贴板发布与恢复缺陷（2026-10-23）

proposal5已通过窄修独测/CR后精确应用，聚合`a19be208eff8da4902e72db3bd4a2846c1612a0ee4f9ba5ba2b06cfbd5e8a66b`；独立11个实际producer/搜索流程、26个相邻安全场景通过，同失败帧在PS5.1/7重放旧版拒绝、新版通过。真实search已越过布局门，但在clipboard_changed处中止；生产Clear函数真实点击清搜索后回读empty，原游标监听恢复。此时仍不能记完整search通过。

主控只读检查发现剪贴板留下本次校验marker，已明确告知用户恢复逻辑有遗漏。系统只读probe排除当前格式、原文/终止符、内存长度不符；后续受控probe真实证明：native CAS成功写入后，锁内返回sequence为296，Close后实际为299，内容与本次nonce、owner均一致；用关闭后的稳定seq恢复成功（第二次301→304），仅恢复probe开始时的旧marker，不声称找回更早未保留的原剪贴板。原managed故障模型只在Set时递增序号，遗漏Close发布行为，原CR也漏此恢复边界；本轮不可复用为缺陷已闭环证据。

后续最小修复只调整PS编排：锁内write stamp不能当发布凭证；写完后稳定seq前后、全文nonce和本次hidden HWND共同确认归属，finally也可在未取得receipt时按同样证据回收自己的nonce，再交原CAS原子恢复。保护恢复阶段不覆盖后到外部写，不声称用户并行CtrlC完全不受影响。不得为硬杀进程/系统故障将用户剪贴板原文落磁盘；硬中断不保证finally，该更早原内容未保留则不能猜测补回。修复仍在Temp副本准备，生产proposal5保持，监控继续，未提交部署。

## 用户结束实验（2026-10-23）

用户明确要求先在群内说明实验暂停、期待后续正式上线，再停止跟踪。主控按原CLI串行排队一次结束招呼及stop，发送success/applied、local-record精确核验，随后立即退出；没有再轮询或补发，进程exit0。最终脚本累计590次成功轮询、8条确认发送（1开场+6对话回复+1结束招呼），ignored0、pending0、blockedfalse；纯群友闲聊忽略分支未遇真实样本，不能记实测覆盖。原unknown对话回复经只读记录核验收敛且未重发。状态与日志保留本机Temp，没有创建额外自动唤醒任务；不重启监控，后续继续需用户另行指示。

搜索修复proposal6已在Temp冻结，聚合`f3e9d184405221a8db10bed391bd229d9b729ed02b56f10545841fffeb1aea4a`，仅PS发布凭证和恢复编排及对应回归改变，Native不变。正式CR通过无未解决P0/P1/P2；独测已完成新增恢复16场景绿、旧proposal5对应Close发布回归红，以及实际Native dirty-Close模型1场景通过，独测已按结束实验要求收尾，最终六hash一致，无测试占用；未追加完整相邻回归，旧9/26不作为本次新恢复缺陷闭环证据。当前工作树仍proposal5，proposal6尚未应用或真机验收，完整search问题未称解决；结束跟踪后不再自行运行新的UI搜索实验。此前遗留marker对应更早剪贴板原文未保存，无法声称找回；后续受控probe只还原probe起始值。

索引保留部分完成，实验阶段已结束而完整CLI搜索修复尚未验收。没有提交、推送或部署。

## 本次提交核验（2026-10-23）

用户本轮明确授权提交当前检查点，替代前文历史阶段的禁止提交约束；部署仍未授权，群聊监听保持停止。本次代码保留proposal5，proposal6仅以待应用补丁和W1验收项入库，不新增真实UI实验。

提交前已fetch并确认当前分支upstream为origin/feature/ask-user-question-card。独立范围核对通过：既有暂存447项等于先前合并基线，新增改动均归入Runner/WeCom/agent2及关联文档；不把仅三处peer标记变化的CLI package-lock生成噪声纳入提交。Runner最终24文件hash逐项与freeze4一致，复用计划中的既有独测/CR证据。

主控本次检查：CLI build通过；定向Node/PS/群查询测试11通过、0失败、0跳过（无真实UI发送）；Web build及产物检查通过；62个变更Python文件语法编译通过；agent2脚本bash语法检查通过；待应用补丁git apply --check通过。没有为此次提交重启测试数据库或真实服务，不声称重新运行全部历史测试或完成正式集成验收。

暂存后范围核验：相对既有合并基线的123条新增/更新路径与本任务明确清单完全一致。默认git diff --cached --check唯一提示是从master原样合入的济南部署文档EOF空行，未顺手修改；本任务范围无新增空白错误。仅CLI package-lock三处peer生成噪声留在工作区，未暂存。


### W14本地Web最终验收（2026-10-29 15:31，北京时间）

用户自行登录后指出原ask_user卡片选项导致白屏。实际定位为sr-only输入焦点的包含块错误，已按[ask实施计划§9](plan-ask-user-interactive-question.md#9-选项焦点滚动修复2026-10-29)窄修并独立验证；与工具装配/Runner权限无关。随后主控在新会话发出自然语言有界只读请求，不指定工具名，要求简述实际企微/BOSS能力并只读取3条。MAIN正确陈述两类能力并选择wecom_read_sessions，未执行任何BOSS操作。

Runner `runner_3489164b16b04f7f9612fc24466f68d1` completed；唯一invocation `c5914698-70c7-42ff-b3b6-6067e448a962` succeeded/OK，channel=local-db、navigated=false，3会话/3消息、失败0，sender_platform=wecom2/unknown1/wechat0。任务总耗时23.744746秒，包含模型两次推理与本机调用，不是纯CLI耗时。没有正文或完整联系人标识写入本文。scheduled_tasks仍0，原会话仍6 completed/1 waiting，卡片未提交/忽略。

证据本机Temp `wecom-w14-live-read-metadata.json`、`wecom-w14-web-read-success.jpg`。网页已返回原待答会话，所有临时选项草稿随卡片重新挂载清空，留给用户自行作答。更新取代前述“等待用户登录”的当时交接状态。W14独测247及CR对应源码hash未变化，ask局部修复另有48项独测/CR和Web完整构建；不机械重跑前一批。无提交、推送、agent2部署、真实发送或恢复监听；多天长期运行与真实微信来源样本不在本次有界读取验收结论内。


### W14.5 服务端单独升级的旧端兼容复核（2026-10-29）

用户指出仅发布后台而不升级客户机是否会隐藏原BOSS工具。静态查验确认旧Runtime的根provider_id=ai.aidwork.boss-recruiting仍受现有get_provider_keys_for_device支持，历史bdcd1233前的producer也已报providers/manifests/旧provider_id；不要求最新客户端。本轮native工具目录/版本及Device wire未变。

但W14初版把设备可用性投影应用到显式allowed工具，缺设备能力记录/当前无对应provider时会把原本已配置的工具隐藏；boss_send_to还有不依赖设备的话术读取分支，这不是新增自动装配应施加的限制。此前247独测/CR未覆盖“显式配置仍保持旧可见性”，不以已通过的现代Runtime真机样本证明全部旧端。

收窄方案：ToolCatalog接收已解析配置的allowed（含既有读别名映射），与设备自动发现的候选并集后统一经过原registry和业务目录投影；excluded仍已在Assembly移除，不可能复活。main/继承自动新增项继续按设备能力显示；显式旧工具保留可见，执行仍由原设备/权限/离线/不支持错误说明实际状态，不新增批准或执行绕过。每次执行用自身config，无全局能力缓存，不改冻结调用/指纹/设备wire。主控唯一修改两处运行时接线和回归，独立TEST及CR只读；不提交/部署/监听。

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| W14.5 | 旧端上报与显式配置可见性兼容 | ✅ 完成（2026-10-29） | 196个唯一独测通过，CR无P0/P1/P2；原生协议不变，未做旧端二进制真机验收 |


W14.5实施补充：真实招聘配置链路回归又暴露轻量`manifest.py`漏掉早已存在于class/catalog/SUBAGENT配置的boss_lookup_candidates与boss_greet_selected；补入两名称使显式配置正确注册。没有改变Runtime/MCP/native claim工具清单。首次用例失败69通过/1失败正是该漏项，最终相关7文件自测87通过。测试只隔离不相关技能目录初始化，真实profile加载、Assembly和RuntimeExecution仍使用生产接线，避免用手工拼装ToolCatalog掩盖接线漏传。

主控AST逐项比较W14前五个Provider的tools/provider_id/min_provider_version/execution_target，均未改；证据Temp `wecom-w14-compat-static-proof.json`。本次尚无旧客户端二进制真机执行，不以静态/协议回归声称任意历史版本都已验证。


W14.5最终验证：主控相关7文件87通过；独立TEST初批195通过/1过期测试计数失败，确认W14前后proxy实现均35项且顺序未变后，仅把test_proxy_tool中的旧34断言/注释校准为35（wecom9）。独立补跑该文件28通过，按JUnit classname::name覆盖去重后最终196个唯一用例全部通过，0失败/错误/跳过，不把两个重叠批次相加。独立新增5项实际验证无设备时ToolExecutor可执行boss_send_to纯云话术分支且不选设备/不建外发、工具可见仍要求可信身份、inherit不误认显式配置、别名排除不复活写工具、轻量名称清单完整。此前独立7项恢复/隔离补充也重验通过。

独测证据Temp `wecom-w14-compat-test-kzykpvgp/FINAL-INDEPENDENT-TEST.json`（SHA256 `95f3bd29a86f907a5b51b69aae3c292423729ed334c9da8fdb0443af51c6866b`），复审证据`wecom-w145-independent-cr-_xplqww7/review.json`及review.md，无P0/P1/P2。主控最终复核4本轮源/测及7上下文hash一致；新增旧计数修正不改变产品源码。两端协议/原生tool名单未改，现有标准旧provider_id无需新客户端可被识别；不把服务端协议/配置回归冒充所有历史旧Runtime二进制真机组合验收。

本轮未安装/升级任何客户机、重启本地业务服务、提交、推送、部署或恢复监听。W14的现代Runtime Web读取证据与W14.5服务端旧配置/旧上报格式独立验证分开登记；实际发布仍需用户明确部署授权。


### W14.6 Runtime 0.0.29＋BOSS 0.3.27隔离兼容验收（2026-10-29）

用户要求仅验证这组旧包，不测试更早版本；禁止影响当前客户端、提交、部署或恢复监听。历史归档位于主工作树clients/release，只读解压到本机Temp，不运行NSIS安装器、不安装或重配真实设备。归档SHA256 `f1080050181fb050ed4aafee0b9fee268c6c4386122fc39ac0f50b934cf9c6d7`与登记一致。验收运行原包自带Node和编译Runtime core、签名BOSS MCP产物，不用当前源码重编旧包。

隔离边界：独立Runtime home/APPDATA/LOCALAPPDATA、命名锁资源键、空CDP端口、仅虚构租户/账号的临时PostgreSQL与HTTP测试服务；不复用当前配对凭据/客户端目录/18000或8000业务服务。旧包只读工具无真实BOSS账号/浏览器，不执行写工具；未知外发仅用虚构冻结记录证明不重放。当前客户端PID/启动时间及配置文件hash前后核对。测试容器/进程为有界验收夹具，不复用业务卷；临时PG使用Docker自动匿名卷，业务测试数据库及自有容器退出时回收，不对无法证实归属的卷做删除，不宣称匿名卷已全部清理；不是发布现有环境。

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| W14.6a | 历史包及当前客户端基线 | ✅ 完成（2026-10-29） | 仅0.0.29/0.3.27，归档hash匹配，安装器只读提取，当前3个状态文件保护基线已存Temp |
| W14.6b | 真实旧包能力/MCP/新服务端接线 | ✅ 完成（2026-10-29） | 原包Core0.2.20/Node22.23.3/MCP0.3.27；25项MCP、24项BOSS业务工具可见 |
| W14.6c | 中断/ACK丢失/原调用恢复 | ✅ 完成（2026-10-29） | API重启/租约过期后同原调用恢复，attempt1→2 completed；虚构成功回执0.50只结算一次，unknown零执行 |
| W14.6d | 隔离清理和证据复核 | ✅ 完成（2026-10-29） | 旧包7547文件、当前3状态文件/6进程不变；自有进程/容器退出，独立证据复核通过 |


W14.6最终结论：仅运行用户指定的Runtime UI0.0.29（内置Core0.2.20、Node v22.23.3）与BOSS0.3.27原包产物。插件签名使用随包验收根核验通过，归档SHA与登记一致，7547个提取文件前后hash一致；没有用新源码重编旧包或运行安装器。

最终1个组合场景PASS，0失败/错误/跳过，JUnit记录27.160秒。真实旧ApiClient/ProviderManager/runInvocation/ResultOutbox与当前Device路由和临时PG接线：MCP列出25工具，MAIN自动显示24个BOSS业务工具，真实招聘配置在缺cap记录时仍保留原工具。MCP只读进入受控无业务浏览器环境，实际返回CANCELLED/effect=none；这不是BOSS页面读取成功。结果落库后故意丢ACK，重启夹具API进程，旧core新进程仅补原/result、零Provider调用。

原工具绑定后、进展尚未保存的冻结checkpoint（ToolFact.invocation_id可为空）通过原phase和结果id恢复；真实RunnerAuthorizer、RecoveryCoordinator、RecoveryRepository、AgentEngine、ToolDispatcher及Finalizer沿同runner/同invocation从attempt1到2，最终completed，没有重建调用或重复结算。checkpoint为夹具构造，租约由夹具设过期，恢复后的终答使用确定性模型夹具；未执行完整Worker进程发版。另用明确虚构成功回执经旧outbox/API验证0.50积分只结算一次；虚构旧unknown写回执仍unknown且零Provider调用，未执行发送。

隔离复核：当前6个客户端进程的PID/路径/启动时间一致，3个客户端状态文件hash一致，测试旧Node剩余进程0，自有PG/API容器及测试数据库已退出/删除，原本地服务保留。匿名卷未做无归属删除，不宣称全部匿名卷清理；隔离文件保留证据。本轮未修改产品源码、升级客户机、重启当前服务、提交/推送/部署或恢复监听。前期夹具接口及生产前置假设修正的失败产物保留attempt-*目录，最终组合场景对应最终脚本，不把早期夹具错误当成产品缺陷或隐藏最终失败。

证据根为本机Temp `legacy-runtime-compat-20261029-0bs4ieil`：FINAL-COMPATIBILITY-REPORT.json、old-artifact-inspection.json、combined-result.json、driver-summary.json、pytest.xml及独立复核independent-evidence-review/review.md/json。独立复核通过，无新P0/P1/P2，明确不把真实MCP受控取消、虚构成功/unknown回执扩大成真实BOSS业务或完整Worker发布验收。

### W14 提交范围核对（2026-10-29）

用户授权提交并推送通用工具装配、旧端兼容及提问卡片修复。一并结束此前已验证的`12b7827c` master合并，纳入必要的持续/定时Runner合并回归；W11发行包、打包源码及本地容器配置保留在工作区，不随本次提交。提交前fetch发现master已前进至`20f13c2ec`，本次保持已通过独测/CR/Web验证的组合，不将新的master变更混入验收结果。兼容源码13项与最终独测hash一致，卡片源码与独立48项验证hash一致；本次授权不包括部署或恢复监听。

## W15 master合并与服务端单独升级评估（2026-10-30）

修复前评估结论：**当时不宜直接合并后发布生产**；本次修复后的结论见下方“W15最终验收与交接”。WeCom尚未完成真实业务验收本身不阻止合并；当时阻止合并的是旧任务恢复与存量数据库升级的确定性缺口。评估基线为已提交分支`376cfc33`与本次fetch后的`origin/master 10b579759`，不是客户当前线上版本的实测。`github/master a92f9761`与Codeup不一致，仍沿既有origin基线，不擅自混合两条master。工作区未提交的安装包/客户端修复及本地容器配置不在合并范围。

### 合并前必须关闭的三个问题

| 级别 | 问题与证据 | 对原生产功能的影响 | 最小收尾方向 |
|---|---|---|---|
| P1 | master的runtime_manifest生产配置指纹v1；分支只接受当前v3及legacy v2。相同profile、设置及prompt下，真实RecoveryCoordinator仍返回RECOVERY_CONFIGURATION_CHANGED | 未完成的原BOSS/main任务在服务端升级后无法恢复；旧Runtime升级与否不能解决 | 补齐真实master v1的精确兼容，保留已有checkpoint及原调用；用旧服务端算法生成的checkpoint验证升级恢复，不扩大接受任意未知hash |
| P1 | recruiting-operator的描述、WeCom能力及提示词被改写；ProfileCatalog对完整配置求hash，BOSS allowed集合相同也会触发RECOVERY_PROFILE_CHANGED | 内置招聘任务及引用该profile的活动子任务可能在恢复前被拒绝；与WeCom是否启用无关 | 优先移出不必要的内置招聘配置变更，保留当前master的招聘profile；不要为此增加另一套版本/授权系统 |
| P1 | 最新master迁移水位为2026-10-29 15:50:00，分支补齐块止于13:46:35；_apply_db_updates只选大于存量水位的块，而RunnerRepository.assert_schema无条件查询wake_at | 已跑过最新master迁移的存量库会跳过休眠列/BIGINT/索引补齐，可能导致Runner启动失败；durable_sleep_enabled=false也不能绕开schema检查 | 保留双方迁移并按时间合并，在最新master水位之后追加幂等补齐块；以该最新水位的存量库做真实PG升级验证 |

三方只读合并预览另确认`deploy/db_update.yaml`存在内容冲突；实际未执行merge或切换分支。该冲突必须保留最新通知规则迁移和分支补齐结构，不能简单选择一边。仅解决文本冲突不等于解决迁移水位跳过。

### 本次证据与已有验证的边界

- 在网络禁用、当前源码只读挂载的临时容器运行3项针对性复现，退出0、3项通过、0失败/跳过。这里“通过”表示**成功复现三项阻塞**，不是兼容验收通过。前两项实际调用生产RecoveryCoordinator，未调用Provider/设备/客户；第三项核对真实YAML水位选择及无条件schema检查，未执行真实PG升级。
- 脚本与JUnit保存在本机Temp `wecom-merge-assessment-20261030-96d6a747`。该脚本读取fetch后的master源码生成旧hash，并用真实SubagentLoader解析master/分支招聘配置；未使用客户checkpoint或导出凭据。产品代码仍与HEAD一致。
- 既有196项通用装配/兼容独测、48项提问卡片独测及CR仍是各自范围的有效证据；0.0.29/0.3.27原包core/MCP、原回执恢复及幂等验证仍成立。但W14.6冻结checkpoint由新版本夹具构造，未覆盖**master v1配置指纹或旧招聘profile跨服务端升级**，不能据此声称旧生产任务完整恢复已验收。
- 现有受信BOSS原生命令、Device执行链及旧provider_id兼容无需为了这次服务端装配强制升级客户机；持续能力开关默认关闭，WeCom业务未被调用时不应影响BOSS。但这两点不能消除上述恢复/迁移问题。

### 通过后的合并标准

只做上述兼容收尾及必要的最新master整合，不继续扩展WeCom功能。补充“master创建任务→新服务端恢复原调用”及“最新master水位存量库→新版本启动”两条回归，复用原包0.0.29/0.3.27隔离链路核对；继续保持unknown不自动重发、费用仅结算一次。受影响独测和CR通过后可在master继续开发，WeCom真机完善可后续独立进行。实际合并、提交和部署仍分别等待用户明确指示，本次均未执行，也未恢复监听。

### W15 最小修复实施计划（2026-10-30）

用户已授权在当前WeCom分支同步最新master并修复三项；未授权提交、合并回master、部署或监听。开发主控唯一写产品/仓库测试，独立测试与CR只读并分别验收。

1. 保全工作区全部44个既有变更文件至本机Temp `wecom-w15-before-master-60oocdpf`，按origin/master `10b579759`执行no-commit合入当前特性分支。迁移冲突保留双方内容并按原时间排序，不覆盖客户端发行修改。
2. 复用原legacy配置指纹算法，精确识别master v1和既有v2；当前新调用仍写v3。保持真实配置漂移校验，不新增数据库字段、配置版本或宽松匹配。
3. 将recruiting-operator恢复为本次master原文件，避免为未启用的WeCom功能改变存量BOSS profile。通用目录/三工具实现保持，其他配置智能体仍可使用。
4. 在最新迁移水位之后追加同结构的幂等补齐块；对最新水位的旧列/索引、原计费数据、大整数及重复启动实际PG验证。同步数据库说明，不更改迁移框架。
5. 用真实master指纹/招聘配置/旧执行树生成升级来源，验证原任务及原BOSS调用恢复、unknown不重发；补相关单元与集成用例。主控定向自测、独测与CR修复到通过，完成记录按实际证据更新，不复跑无关全项目测试。

| 子阶段 | 内容 | 状态 | 完成记录 |
|---|---|---|---|
| W15.1 | 最新master及三项最小修复 | ✅ 完成（2026-10-30） | 整合10b579759，迁移冲突已解；legacy v1/v2、原招聘配置及最新水位补齐完成 |
| W15.2 | 主控自测与启动/构建检查 | ✅ 完成（2026-10-30） | 51项定向unit、4入口import及Web完整构建通过；42个原有非任务改动hash保持 |
| W15.3 | 独立升级验证 | ✅ 完成（2026-10-30） | 49项unit/15项真实PG，最终0失败/错误/跳过；旧v1原BOSS调用及unknown/幂等、真实master存量升级通过 |
| W15.4 | 独立CodeReview | ✅ 完成（2026-10-30） | 最终8项freeze通过，原迁移块完整且有序，无P0/P1/P2；迁移测试假阳性修正已增量复审 |

### W15最终验收与交接（2026-10-30）

三项合并阻塞已关闭：当前配置仍写v3，真实master v1及分支v2精确兼容；内置招聘原文件与master逐字一致；新幂等补齐块2026-10-30 10:00晚于最新master 15:50水位。最终fetch确认origin/master仍为`10b579759`，已整合到当前特性分支工作区，HEAD保持`376cfc33`，未生成merge commit或修改master分支。没有新增Device wire、门禁、版本平台或迁移框架。

- **独测结果**：49个唯一unit、15个唯一真实PG场景，均0失败/错误/跳过。15项PG覆盖原v1/原ExecutionState→新Coordinator/真实claim恢复main及招聘原调用2项、旧unknown写1项、恢复证明3项、用量台账不可变7项、仓库最新水位1项、独立完整master重建升级1项。重叠补跑及诊断不重复计数。
- **原调用恢复**：独测从精确10b源文件执行原manifest函数、原ExecutionState及原招聘profile生成旧checkpoint，不用新helper伪造旧hash。经新Coordinator/SQLclaim保持同runner、同invocation；原设备撤销及模型schema隐藏后重复recover仍不新建调用，原成功回执只结算一次。原unknown写仍unknown、resume未消费、零新调用及零费用。
- **真实存量升级**：独立自有随机库从真实master init及全部master updates重建，实测旧水位15:50且旧schema无法通过新wake_at检查。升级后立即确认水位推进10/30，Runner assert_schema、八项BIGINT/索引、普通重复及新补齐块强制重放通过，原计量/积分canary保留且可写大整数。
- **测试有效性修正**：首批Temp补充脚本有3项将settings实例误作模块，修正临时导入后补跑3项通过；SQL批次有Temp池module作用域提前关闭，改为session后仅补跑受影响用例。仓库旧迁移测试另有两个人工旧水位与最新通知schema不匹配的假场景，初次执行可能报退休列错误、末尾force手工推进又掩盖失败；独立tripwire已证实并剔除，最终仅保留真实15:50起点，首次_apply后立即断言水位。没有为无效场景增加产品防御，未将剔除或诊断用例计作通过。主控首轮51项中招聘fixture路径导致1项失败，按相同部署路径对齐fixture的path/dir后51项全部通过，生产hash校验未被放宽。
- **CR与主控复核**：独立CR无遗留P0/P1/P2，v1算法AST与master一致，原master66块及原分支70块完整保留，最终73块唯一有序。最终8项源码/测试/fixture hash匹配独测与CR。主控51项定向unit、4个入口导入、Web类型/依赖边界/完整构建及产物验证通过；警告仅既有chunk体积、空chunk、Browserslist及npm配置。原有42个非本任务文件hash保持，44项备份中的另两项是本次明确更新的计划/索引。
- **证据**：本机Temp `wecom-w15-independent-test-xlcamnqy/FINAL-INDEPENDENT-TEST.json/md`，JSON SHA256 `6da7500ceeebfe0e4f905480136c846f9e07932e6ca276600243ae381f845bb8`；CR `wecom-w15-independent-cr-4gg16u2a/review.json/md`；最终冻结 `wecom-w15-freeze-current.json`；主控检查 `wecom-w15-root-checks.json`。自有PG及所有自建aid_test_库已清理，未重启或替换现有客户端/服务。
- **验收范围**：本批未重新执行Runtime0.0.29/BOSS0.3.27二进制，也未做真实BOSS页面业务或生产负载测试；原包core/MCP验收仍以W14.6独立证据为准。本批补齐的是旧服务端producer与新服务端恢复及存量PG升级边界，不把设备version字段当二进制验收。三项已知合并阻塞已完成修复与验证，可进入用户授权后的提交/合并流程；不宣称真实WeCom业务或生产验收已完成。

用户要求保持未提交：当前工作区保留master合入结果及本次修复，安装包/客户端发行与本地配置原有改动保留；未提交、推送、合并回master、部署或恢复监听。

### W15授权提交与master合并（2026-10-30）

用户随后明确授权“提交推送当前分支，再合并到master，后续在master继续开发”，继续禁止部署和监听。兼容修复以`1b0759cd`提交并推送到Codeup/GitHub的`feature/ask-user-question-card`，未带入原有客户端发行包或本地环境改动。

master整合保留origin/master `10b579759`、本地/GitHub master `a92f9761`及其通知认证/遮罩两项修复，没有强制覆盖任一远程历史。三方合并无冲突；相对已验证特性分支，生产源码仅新增`frontend/web/api/notifications.ts`、`AppHeader.vue`和`NotificationBell.vue`三项既有通知修复，后端8项验证冻结保持。主工作区原有6处未提交修改已逐项备份，仅对两个重叠索引临时还原、三方恢复后留作未暂存，未纳入合并提交；四个非重叠文件字节不变，重叠文件原增删内容保持。

在临时合并树快照完成Web完整构建（281源边界/1079转换/1143产物模块），不更新现有服务或客户端。通知API/client/入口/会话及提问卡片、路由补测总计63通过/1失败；唯一失败是`routes.test.ts`的旧inline snapshot漏掉已有`notification-rules`路径。该测试与路由源码均逐字等于现有origin/master，且两项GitHub通知修复不修改它们，明确记作master先存快照问题，未擅自修理或称为全绿；不是本轮兼容/合并回归。此前49项unit/15项真实PG的兼容验收仍对应相同后端源码。

合并预览树`0bf2987b0147e99ad8daea0aca5feee059b8df2e`与暂存合并结果一致；隔离构建/测试日志位于本机Temp `wecom-master-frontend-20261030-p13ce3e7`，主工作区备份为`master-before-wecom-merge-20261030-q7zso8jv`。主分支合并提交及推送编号以本轮Git记录为准。后续开发位置为`C:/repos/aid-work-agent`的master；WeCom工作树原有发行包/本地配置修改继续原地保留。未执行部署、客户端升级、真实业务发送或监听。

主工作区最终复核：W15冻结7项字节不变，另1项`provenance.json`仅Git将CRLF标准化为LF，解析JSON及源文件清单完全相同；该换行差异不改变产品或fixture语义。合并后在主工作区源码只读、无网络容器重新执行4项跨版本恢复用例全部通过，未启动现有服务。
