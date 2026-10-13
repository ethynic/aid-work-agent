# BOSS 已加载候选人直接定位

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 真机定位实验 | ✅ 完成（2026-10-13） | 唯一姓名匹配；一次向上大距离滚动将目标完整带入视窗，未点击或发送消息 |
| Phase 2 | 列表定向打招呼定位 | ✅ 完成（2026-10-13） | Provider 0.3.7；独立55项及额外5项断言通过，CR问题已修复，未部署 |
| Phase 3 | 重名与未找到跳过并反馈 | ✅ 完成（2026-10-13） | 独立Node111/Python44通过，CR通过；真机61张卡片优势只读识别，未发送 |
| Phase 4 | 重名卡片信息与明确引用选择 | ✅ 完成（2026-10-14） | Provider0.3.8；独立Node122/Python61及反例15通过，CR通过，正式查询真机只读验收通过 |
| Phase 5 | 授权单次发送与真实发送后状态识别 | ✅ 完成（2026-10-14） | 一次滚动/一次真实点击，截图继续沟通；修复btn-continue，独立64项及反例7通过，CR通过，幂等真机零输入 |

## 方案与边界

用户确认需打招呼的人仍在已加载推荐列表，只是不在可见视窗。2026-10-13 真机确认 DOMSnapshot 包含视窗外卡片，文档坐标不随文档滚动变化。指定目标文档中心 Y=6938、滚动偏移=16683，换算 iframe 偏移及根 HTML CSS 比例后，发送一次 deltaY=-5090.8 CSS px；实际滚动偏移变为 6501，目标姓名位于屏幕 Y=517，卡片完整可见，按钮配对成功。实验只读取/滚动，不证明真实消息发送结果。

每个 fresh snapshot 从可信列表结构建立临时姓名→卡片索引，不持久保存姓名、坐标或账号信息，不扩展数据库及 Runtime/Device 接口。对已加载的目标按实时距离滚动，进入视窗后重新确定同卡片按钮；同名跳过该姓名并继续其他唯一者。多个目标优先当前可见者，再选择最近者；每人最多三次定位滚动，无推进停止，不将其退回逐屏扫描。正常列表结构存在时，索引没有该历史姓名即跳过，不逐屏搜索；旧无结构页面保持原有限搜索兼容。

必须区分正常筛选卡片与“为你推荐”来源；空态立即停止，已加载的正常目标即使在推荐边界上方仍可向上定位。新定位路径点击后必须看到同名卡片翻转为“继续沟通”，失去目标或结构变化不能仅凭按钮消失宣称成功。点击仍使用 fresh device 坐标，滚轮独立换算 CSS 单位。

不改变用户批准清单、默认人工审核和详情页显式启用规则，不扩展 CDP 白名单，不注入页面脚本，不部署。真实测试发送必须取得用户对具体对象的明确授权；本次仅授权并发送一位候选人一次。重复姓名只有姓名参数无法消歧时明确失败。

### 用户最终确定的姓名清单处理

用户明确初始只会提供一串姓名。最终实现不增加优势输入参数，重名不自动消歧：names中匹配多张卡片的姓名记录为ambiguous_names（包含各卡片优势），零点击跳过并继续其他人；正常列表索引中未找到的姓名或定位中原卡片消失/原文变化记录为missing_names，跳过继续其他人。结果区分greeted_names、ambiguous_names、missing_names，不把重名再列入未找到。

真机已确认`card-item`内的`geek-desc`行有`label`“优势”以及`content`文本。从同卡片读取完整DOM文字，仅规范空白，用于重名反馈，不根据优势自动选择。缺失原文返回null，Agent如实说明；不得跨卡片找相似文字。定位中的优势及点击后同一组合再次核对，防止同名替换卡片；点击后无法确认发送状态仍是UNKNOWN并停止，绝不继续或重试外部写动作。Agent先分批处理完整姓名清单再集中汇报；重名附优势供用户确认，未找到说明可能名字输入错误或页面已刷新移除。

撤销初始要求优势的参数/简历原文回执扩展，没有改造owned简历入库或Runtime wire，也没有新增需要旧Provider识别的输入安全约束。相同姓名、不同优势在用户未澄清时一律不打招呼。用户确认具体重名对象前，不通过详情路径绕过列表歧义。

### 卡片选项与确认后的准确发送

用户进一步明确重名反馈应附卡片上可区别的信息，用户可以用口语选择，不需要逐字提供优势。重名回执增加option_index、card_info（基本信息、薪资、期望、经历、教育、标签及优势）和candidate_ref；Agent展示编号与区别信息，结合原始选项语义理解“年轻的那位”“上海本科那个”“第二个”，唯一对应后原封复制该选项ref。依旧不清楚时继续澄清，禁止工具对用户自由文字做模糊匹配。

新增只读boss_lookup_candidates用于查询/刷新选项，新增单人写工具boss_greet_selected要求name+candidate_ref；初始boss_greet(names)输入保持不变。独立工具名使旧Provider/旧Host不支持时明确拒绝，不会剥离新selector并退回姓名发送。选定发送禁止自动弹层自愈重放；unknown仍按原流程处理。

引用格式boss-card-v1:<13位签发时间>:<64位摘要>，30分钟有效。摘要绑定时间戳及卡片稳定信息、头像摘要、当前账号与招聘职位标签；不包含位置、活跃状态或动作文案，不持久化原文/URL/账号。滚动/重排后重新读图文与有效iframe区域；相同引用唯一命中且来源正常才可点击，换账号/职位、年龄经历变化、过期或完整身份重复均拒绝。引用是定位键，不是授权凭据，发送仍必须有用户明确选择和受信Runner/Device invocation。已是继续沟通时返回未重复发送。

卡片字段来自本次真机确认的base-info、salary-wrap、expect-wrap、work-exps、edu-exps、tag-item、geek-desc；不存在的字段返回null/空数组，不猜。签发范围来自根账号摘要及head-wrap>job-selecter-wrap>ui-dropmenu-label实际选中职位，排除下拉列表其他职位（当前真机下拉展开仍有61个可用ref）。不确定账号/职位时ref为null，查询不能创造可发送定位。

### 共同契约检查点（Phase 4）

- 生产端与唯一业务写入者：root负责Provider身份/选定执行，委托接线智能体负责CLI/MCP、云工具注册和现有Host工具allowlist；不修改Runtime调度、Device wire/schema、租户/授权/binding或锁机制。
- 公共版本：Provider0.3.8通过既有manifest/schema_digest公布两项新增能力；仅扩展既有tool_name集合，Runner/Desktop/Runtime共同契约v2.0语义与Device envelope不变，不另造本地Agent loop。
- 消费端：云proxy使用既有Base.execute/owned恢复，Host共享legacy allowlist新增读/写分类；旧name-only调用和已接受invocation不改写、不重放。旧Host/Provider未知工具必须拒绝；确认引用不添加到旧boss_greet参数。
- 验证矩阵：Provider/MCP/CLI/云InputModel/raw、catalog/Host枚举与旧manifest拒绝；引用漂移/重复/取消/unknown和公开回执；不实际发送、不部署。新工具积分采用现有default_credit_price配置路径，未新增价格配置或计费wire。

## 验证

定向验证上下跳转、远距离一次到位、滚动偏差、窗口比例、重新排布、同名、来源边界、取消、无推进及缺失几何。复用关联 Greet/operation/推荐边界回归，由独立测试与独立 CR 验证外部动作目标安全。最终使用真实页面只读定位验收，所有真实点击依赖替换为拒绝函数。

### 完成记录

- 主控编译 `npm run build:main`（0.3.7）成功；定向 Greet/新定位/推荐边界/operation进度取消检查通过。
- Phase 2独立测试四个定位/来源套件55项通过、无跳过；另以合成fixture验证短owner、嵌套owner交集/正常区域、1.25倍CSS换算、snapshot期间取消5项断言；click后目标消失返回EXECUTION_UNKNOWN、effect unknown、不可重试且释放session。
- 独立CR发现的snapshot期间取消边界与独立测试发现的iframe owner裁剪问题已修复并复核，无剩余P0/P1/P2。
- 真机首次完整跳转实验成功（Phase 1）；正式编译后的索引唯一识别同一目标、正常来源及可见按钮。正式执行器复验的准备滚动遇到CDP Input.dispatchMouseEvent超时，未完成该轮完整复验；真实点击/消息发送始终为0，合成测试不能代替真实发送验收。部署与客户端升级尚未执行。
- 不持久化指纹，本次没有数据库、账号缓存或跨调用坐标历史。用户批准清单仍是调用前提；页内已加载的正常目标即使在补充推荐上方也允许返回定位，补充推荐自身始终排除。
- 最终Phase 3独立验证：`AID_BOSS_AUTO_CHROME=0 node --test` 定向运行loaded-candidate-navigation、greet-executor、recommendation-boundary-operations、recommendation-result-boundary、cli-validate、mcp-server六个套件，111项通过。Python通过venv和dev_test.sh执行boss_greet_mode/proxy_tool，44项通过、4条既有Pydantic警告，数据库/Redis强制隔离端口1。主控额外manifest和公开混合名单/边界回执检查通过，build成功。
- 独立反例覆盖：重名者中有优势null，缺失者，唯一者均在同名单中；仅唯一者执行替身click，其他零输入。列表变化后原目标跳过并继续唯一者；空/非空可信列表全名单不存在时零滚动。独立测试发现推荐边界分支message漏异常说明，已统一定向回执并复核，无剩余P0/P1/P2。
- 正式编译模块真机只读快照：可信列表61张正常卡片，61张可提取优势、0组重名、0输入；重名场景依合成fixture验证，未进行真实发送或独立LLM多batch遵循性验收。
- Phase 4：Provider0.3.8最终build通过；独立八个相关Node套件122项、Python61项通过（4条既有警告）；Host候选工具allowlist测试1项、Runtime既有Provider注册7项及相关build通过。额外15项断言覆盖引用换行/篡改时间/未来/有效期边界、重排、换账号职位、完整重复身份、取消与canvas前置。
- 独立CR发现的写后画布被误报无副作用问题已修：画布只在点击前作前置检查；selected点击后快照失败或目标未翻转统一unknown、不可重试。公开operation画布/断连回归均确认click1、session关闭1，不复发；CR无剩余P0/P1/P2。
- 2026-10-14真机：61张正常卡片全部有基本信息与工作经历，60张有期望、60张有教育，61个ref可签发；指定历史目标ref可唯一解析。正式boss_lookup_candidates入口返回OK/effect none、卡片信息与ref齐全（3段工作/1段教育），输入原语全部拦截仍为0调用。没有真实发送；LLM对自然语言选择的端到端遵循性未验收，不能以合成fixture替代该验收。代码尚未提交、未部署或升级正在运行的Host。
- 用户要求“全部真机验证好了再提交”后的补验：正式lookup再次OK/effect none；在真实61卡片DOM中，故意不存在的验收姓名经正式boss_greet返回missing_names/greeted0/effect none，全部输入原语拦截且调用0。当前无真实重名、无已沟通卡片，不能据此声称重名澄清/真实发送/幂等真机已通过；执行器滚动复验再次Input.dispatchMouseEvent超时，未完成。已请求用户明确授权一次真实候选人招呼测试，未获回复前不发送。非VIP付费弹窗真实账号缺口沿用VIP计划记录。提交条件尚未满足，暂未提交或推送。
- Phase 5用户明确确认唯一目标后执行：第一次定位滚动超时，click0；用户恢复后继续，正式选定operation一次向下大距离滚动与一次Win32点击完成发送，截图同一目标显示“继续沟通”。执行期间未点击第二人、未重发。原operation返回EXECUTION_UNKNOWN/effect unknown，因为真实按钮从BUTTON.btn-greet替换为BUTTON.btn-continue.btn-outline，索引未识别后者；只读快照确认真实父层级为btn-doc>button-list>btn-continue-wrap>BUTTON，文本为按钮直接子节点。
- 修复仅增加动作文案绑定的真实btn-continue识别，保留旧btn-greet继续沟通兼容；未放宽非BUTTON、同卡片唯一动作、完整身份或未知结果不可重试规则。fixture改为真实发送后层级，新增错类/类名片段不能误确认发送的回归。主控最终build通过；独立Node四个受影响套件64项、额外7项反例通过，独立CR无P0/P1/P2。
- 修复后正式lookup+selected operation真机只读复核：全部输入方法强制拒绝，返回OK/effect none、already_greeted true、greeted0，inputs0，确认不会重复发送。本次真实发送总计1次；修复后的完整首次发送成功回执尚未向第二位真人验证，不能将只读幂等结果描述为新一次发送测试。当前真实列表没有重名样本，真实重名澄清与LLM口语选择端到端仍未验收，非VIP真实账号缺口不变。未部署；依用户“全部真机验证后提交”条件，待用户决定是否接受缺口后提交。
- 用户随后明确“重名的不用验证了，其他验证好了就可以提交了”，豁免真实重名与对应口语选择流程验收并授权提交。真实定位、单次发送、发送后幂等及缺失姓名跳过证据如上；复用同状态独立测试与审查结果，提交仅包含本任务文件，不部署。非VIP真实账号缺口属于已提交VIP功能计划的既有记录，不在本次定位提交中宣称已通过。
