# 微信端侧自动聊天交接（2026-09-15）

## 19:53 代码检查点提交范围

本次提交微信名称会话、OCR模糊匹配、submitted回执、近期历史对齐/恢复、阶段统计和相关验证文档；保留真机4/5及模型JSON输出失败的未完成状态。远程master已快进至ce274a51，合并后后端隔离126项、前端详情6项、前端/双客户端build及后端import通过。

便携打包命令及其测试暂不纳入：独立审查发现归档缺少_common.ps1依赖的probe-lib.ps1，留在工作区后续处理，不能宣称便携包可用。BOSS改动、本地租户实验开关、临时诊断脚本/截图均不纳入本次提交。

## 19:48 本轮4条后模型两次输出非合法JSON，已暂停监控（最新）

996b8b0e任务human_required/version3，fence1 ACK36。累计4submitted，无unknown、无gap事件；第4次发送input7耗时42.098秒（模型及结算15.439，执行至回执19.759）。input9新peer已读取接纳，决策尝试2次后failed/failure_code=invalid_model_output，故未达5回复。

只读加载该input9两条持久化模型结果，使用生产validate_decision_output离线复验，均在src/weixin_conversation/prompts.py:49抛OutputInvalid，即JSON解析失败；未输出模型正文，未调用新模型。不是OCR/顶部历史/发送动作失败。waynelu已PAUSED，无代码修改/重启/新基线/重发。四条总耗时42.920/34.203/39.882/42.098秒。模型选择wait的input6另有说明，不能把本轮称为连续五条全部回复通过。

## 19:43 本轮3条发送，第4条已识别但模型选择wait（最新）

996b8b0e仍active/version2/fence1 ACK25，无gap/unknown。前三条submitted耗时42.920/34.203/39.882秒，模型及结算13.927/5.529/9.813秒，执行到回执19.897/20.367/22.778秒。input6含1peer+1self，19:40:10模型决策ready/action=wait，运行时decision_phase明确waiting_peer，故未物化第4条发送；不是OCR或发送调度卡住。只看ready误判为生成回复的进度描述已更正，应一并检查action。尚未核验wait是否符合该条内容，不自动改策略或催发。仍3/5。

## 19:34 新版本已部署，新五条实验已发布（最新）

19:35就绪确认：新任务version2/fence1，19:35:01基线ACK1，随后连续6次普通observe成功（3239/3135/3092/3325/3289/3114ms），无gap事件、无发送。可开始发第一条新消息；本轮0/5，heartbeat持续记录回复及分阶段耗时。

用户要求继续实验并同步跟踪。旧994f7b5d已因peer_wait_timeout停止/version5、累计3条，不复用或拼接。Runtime更新PID67416、Provider81848（均最终build）、API父71112未变；新任务996b8b0e-70f0-4750-8b3b-55d8e73df093 active/version2，目标WayneLu、5回复15决策20积分、无开场白，截止20:33:33、等待对方30分钟。真实名称定位73558f85成功，工作台完整范围核对并发布。

新worker71304，脚本TEMP/aid-history-worker.py，日志aid-history-worker.stdout/stderr.log；状态TEMP/aid-history-status.py；耗时脚本aid-session-timings.py传新task和原tenant。Runtime日志TEMP/aid-runtime-history-recovery.stderr.log。19:34启动历史恢复扫描仍进行，尚未确认新baseline，不让用户提前发消息。waynelu heartbeat已ACTIVE并更新新任务和路径，无变化静默；其他新故障只诊断解释，不自动改代码/重启/重建基线/延长。当前0/5。

## 历史容错与恢复调整已完成独立验证（最新，未部署）

客户端统一近期水位及未ACK尾链对齐；移除整窗对象前置门禁，顶部残缺/低分/缺OCR局部保留opaque，由位置相对水位决定是否影响新消息。基线原有opaque保留内部顺序占位，不恢复pixelhash门禁；新增未知内容不静默吞掉。重复水位用唯一上下文消歧，首见正文/ID不漂移。

Runtime gap保留水位/批次/决策，5/10/30秒退避重读，不再三次永久blocked；恢复先合批再继续已有决策轮询，恢复事件先落盘。独立Runtime build及7文件55/55、客户端最终build及7文件40/40通过，独立CR通过（空基线媒体吞失、历史尾媒体永久gap、恢复后inFlight丢轮询均已修复）。

本地真实5帧objects经生产createNameOperations回放，不删除顶部对象，各帧complete_window，新消息数量0/1/1/0/0，每帧重复读取均0。仅原诊断缺失的evidence_hash填合法匿名占位，算法不依该hash。未重新截图操作微信、未部署/重启、未恢复自动跟踪/发送、未提交代码。实际真机仍原3/5，不能据离线回放声称5轮完成。仅媒体同位置同数量内容替换仍无法检测；进程重启失去Provider内存不在本轮自动恢复覆盖内。

## 用户批准整体调整历史对齐与读取恢复（开发验证中）

本轮改动不再按顶部某种切边打补丁：去除nameOperations整窗newNameObjects门禁，由aligner匹配已ACK水位及已见未ACK尾链，允许前部历史OCR/类型异常；重复锚点依唯一上下文消歧，未处理尾部不能跳过。读取阶段低置信/缺OCR区域局部转opaque，不再整窗失败。

Runtime coverage gap不再3次永久blocked，保留水位、pendingBatch、决策引用并退避重读，gap期间ACK不提交新决策；恢复优先合批再继续已有inFlight轮询，避免相位改变后丢失决策跟踪。成功恢复写observation recovered事件，不自动新建基线。身份/暂停/租约限制保留。客户端基线opaque历史边界正在按独立CR意见补验；不使用像素hash身份门禁。独立测试和CR进行中。waynelu仍PAUSED，未部署/重启/恢复真机实验，原3/5结果不变。

## 第四条未回复原因已离线复现；用户要求停止跟踪

waynelu heartbeat已PAUSED。未重启/恢复运行时，未改生产代码。五张本地证据回放（TEMP/aid-fourth-*）：19:00:34旧窗69040961、19:01:39成功窗08a0caa5、19:01:49/56和19:02:06失败窗a5cce868/82ee41e3/070a70a2。使用本地RapidOCR和当前dist分割/对齐，未上传截图。

第四条底部peer文本已提取9字。失败三帧顶部一条历史peer气泡在滚动后没有归入文字气泡，OCR框777,175,948,208（6字，score0.9985）被nameSession未分配框分支归类为image/system；splitClippedTop未标为clipped。因此newNameObjects对旧文字对象和新image对象比type/sender不通过，返回null，nameOperations:42返回alignment_broken，尚未进入后续ConversationAligner。成功窗到失败窗原始回放三次均null；只在离线副本去除该顶部误分类对象，三次均唯一新增1条peer/9字，后续aligner全部ok。直接根因是历史文字气泡归属/类型误分类阻断整个窗口，而非第四条未OCR识别或正文相似度不足。分割器未接纳该顶部气泡的更细条件尚未逐条件探针，不凭截图断言具体阈值。用户本轮只要求诊断，无代码修改。

## 19:03 五轮实验在第3条后消息对齐阻断（最新）

当前994f7b5d任务同一version4/fence2，累计3条submitted、无unknown，未达到连续5条。三轮batch到receipt分别33.071/40.732/49.121秒；模型及结算7.699/6.299/18.731秒，执行到receipt19.299/25.289/22.890秒。

19:01:50、19:01:57、19:02:07观察事件均alignment_broken，第3次后19:02:08运行时phase变blocked；云端任务仍active且租约推进，不能仅凭active判断正常。Provider读取success，故障在读取后的消息序列对齐。尚未分辨nameOperations多个同名分支与aligner的具体子分支，不能断言是哪段OCR差异或阈值问题。未改代码、未重启、未重建基线、未补发；按用户要求先查明并解释新问题。

## 18:46 删除像素/框重叠门禁已部署并接续（最新）

用户明确要求立即修改部署。已删除跨帧pixel比较/重读/缓存依赖及死实现、读后frame相等、PS选择全帧/输入标题帧相等；框按中心唯一归属、同行x排序，原4px overlap当前真机只读success10objects。输入/Enter前本地OCR标题双锚仍核验，期限取消防重复保留。独立44项/build/PS AST/CR通过。中心归属不保证巨型框跨多个发送方时正确；未伪称全面媒体识别。

同一任务994f7b5d-1ff4-4319-903b-c1fa8e46f94d维护fresh_baseline恢复active/version4/fence2，18:45:49 baseline ACK1，随后普通observe3222/3568/3374ms成功。Provider60472、Runtime88300、API父71112、worker90680，日志TEMP/aid-five-nopixel-worker.*.log（脚本aid-five-worker.py），Runtime仍aid-runtime-retention.stderr.log。状态aid-five-status.py，初始0/5，原19:33:57截止/5回复15决策20积分不变。告诉用户现在发新消息，持续跟踪5条回复和阶段耗时。


## 用户已批准删除像素/轻微OCR框几何阻断，正在修改部署

最新明确授权：删除文字框重叠和像素判定，改完本地部署接续5条实验。已工作台维护暂停994f7b5d任务（原0/5），不再停在解释或等待确认。范围：普通读取不依赖pixelWindows/跨帧像素差异/重读提示，不以fresh.frame一致作门禁；文字框中心唯一归属、行内排序，重叠或轻微越界不整体失败。select全帧/type-enter标题帧像素比较移除，发送身份仍由动作前本地标题OCR确认，期限/取消/防重复保留，无草稿和发送后OCR。开发/独立测试/CR进行。


## 18:37 五轮实验当前阻断：OCR框重叠4px（未改代码）

994f7b5d任务baseline后observe持续UI_CHANGED，0发送0新批。当前只读生产分割复现ocr_gap:overlapping_boxes：框a=[979,1152,1112,1187]、b=[1108,1154,1737,1185]，水平重叠4px/垂直31px，置信度0.9999/0.961、字符数4/23。bubbleSegmentation.textFromBubbleRegions对任意两OCR框有任意面积相交直接整体gap，这次不是正文相等/pixel/title问题。临时dist/nameSession-diagnostic.js只读截图/本地OCR输出坐标，不是正式源码；没发送或重启。基线成功后过早宣布就绪已向用户更正。其他故障先解释再改的要求保留，五轮尚未完成。


## 18:34 最新五轮实验已启动

用户明确要求部署本地服务并继续，今天验收改为连续5条不卡住。API已更新父PID71112，Provider41352（包括OCR全链路模糊修复），Runtime88300不变；新任务994f7b5d-1ff4-4319-903b-c1fa8e46f94d active/version2/fence1，18:34:27 baseline ACK1，初始0/5。目标WayneLu，5回复/15决策/20积分，无开场白，截止19:33:57（Asia/Shanghai），等待对方30分钟。工作台完整范围核对发布；首次搜索弹窗未出现TARGET_NOT_FOUND，第二次实际resolve3f983178-2d18-44db-bff1-275e00e6e655成功，无发送。

worker88916脚本TEMP/aid-five-worker.py，日志TEMP/aid-five-worker.*.log；状态TEMP/aid-five-status.py；耗时TEMP/aid-session-timings.py --tenant tenant_1dc997a1806b --task 994f7b5d-1ff4-4319-903b-c1fa8e46f94d，仓库PYTHONPATH和venv。API日志aid-api-fuzzy.*.log，Runtime仍aid-runtime-retention.stderr.log。新消息由运行时自主回复，不逐条代发；其他未授权故障只诊断解释，不自动改代码。旧任务stopped记录保留，不重发。


## OCR精确文本门槛全链路清理（最新，尚未部署）

用户明确授权所有OCR硬相等门槛改近似。客户端标题/联系人采用完整标签模糊匹配并保留全部候选唯一性、原目标+首次选中标签双锚；固定UI标签近似；默认ConversationAligner模糊；旧视觉驱动prompt取消exact/substring标题和正文要求（不调用云图）。wire旧exact_name/title_exact仅兼容规范目标确认，另有name_match_mode=ocr_fuzzy_unique。

后端新增ocr_matching.py，同ID近似正文复用首次密文；legacy verified回显按唯一模糊候选消费原成功次数，submitted仍不比较正文。Python与TS按Unicode数字、ASCII英文否定边界、JS空白集合和UTF16输入长度统一。Python用Myers位向量距离，2万字周期10%错字样例由29s降约0.35s。ID/租户/sender/版本/冻结摘要/签名及引用真实性保持。

独立Python隔离4文件42项通过（含400组参考距离随机对照），客户端build与11文件60项通过；PS静态语法检查通过。独立CR通过。旧PS新增确定性共享TS匹配桥ocrMatchCli，UTF8 JSON stdin、隐藏进程5秒超时；spawn使用当前process.execPath，PATH兼容只取首个node。独立CLI/Unicode4项、PS实际中文stdin/双Node/空PATH、超时Kill+Dispose/异常输出及3脚本AST补验通过。未部署/重启，原任务stopped/version9、监控暂停，不新建实验。


## OCR消息模糊对齐已完成验证，尚未部署（最新）

用户批准相似OCR文字认定同一历史消息。新增ocrTextMatch.ts：NFKC、空白标点与大小写归一化，>=10码点允许90%编辑相似；短句仅格式等价。数字含正负/小数、指定否定词、emoji符号序列变化不合并。共享规则接入nameSession对象重叠和pixel提示锚点、名称专用ConversationAligner；通用默认exact。ID/缓存保留初见正文以免异文冲突和渐进漂移。联系人核验与发送不改。

最终build通过，独立有效51项/CR通过。TEMP/aid-align.mjs对原最后成功帧和3张失败截图的两层回放均objectAdded1、aligner ok、newMessages1、retainedOriginalText true，不需手改OCR正文。阈值不代表语义等价或已真机稳定。当前任务stopped/version9，监控暂停；未重启Provider、未新建实验。


## 18:04 对齐故障原帧回放定位（只读，无代码修改）

原任务已stopped/version9，worker正常退出，监控waynelu暂停。累计6submitted，修复后1条，未达10轮。

对17:57:23最后成功证据2f20fec8和17:58:03/25/55三张失败读取证据c08d671a/b71986b7/8f8f851b本地解密、同RapidOCR与生产分割/newNameObjects回放。每帧10文字对象，无clipped；窗口上滚一条，旧尾9条与新首9条中8条精确相同，唯一不同是上一条self笑话中的引号：全角直双引号变右双引号，末尾新增左单引号。三张失败帧同样差异，newNameObjects均null。只在离线副本还原该旧self的OCR文本后，三组均唯一新增1条peer（14字）。因此可复现直接失败点为nameOperations调用newNameObjects的全正文等值重叠门禁，而非联系人/新peer识别/pixel门禁；并未修改实际消息/水位/代码。私有TEMP/aid-align.py、aid-align.mjs、aid-align-0..3.png/json/rgba，不提交或上传。


## 17:59 新故障：消息序列对齐失败（未修复）

累计6submitted，修复后1条。input9仅self回显，未调用模型。17:58:04/26/56观察事件reason=alignment_broken，Runtime连续3次coverage gap后本地blocked；云端task仍active/version8/fence10、lease推进。不是pixel_gap，也不是发送回执失败；6条submitted保持，无unknown。Provider对应读取success，18.011/9.799/9.986s，后续消息对齐拒绝。尚未定位是newNameObjects还是ConversationAligner的具体分支，不凭reason推断OCR文字差异根因。遵守用户逐项复盘，不自动修复/恢复/重建基线；原18:01:13截止不变。


## 17:57 接续首轮已执行发送

累计6条submitted，pixel重读修复部署后1条，task仍active/version8/fence10、租约推进，无unknown。input8、invocation8a2849f4-cd0c-4e4a-9382-0fe73fead314已ACK。batch→receipt31.946s：建决策0.255、worker2.910、模型及结算8.065、结算→invocation2.478、领取1.227、开始0.150、执行回执16.860；Provider发送子区间12.024s。观察一次11.825s且success（没有直接span，不能断言全部是pixel重读耗时），其余约3.1–3.5s。继续观察，尚非连续10轮。


## 17:55 pixel_gap重读修复已部署接续（最新）

用户明确要求继续实验，已工作台维护暂停version7，更新Provider后以fresh_baseline恢复同一任务6059a3ff-f649-46ad-8243-207cf0b764ee，active/version8/fence10；17:55:15 baseline ACK1，随后普通观察3233/3098ms成功，租约推进。Runtime88300、Provider91036、worker91628；worker日志TEMP/aid-session-pixel-refresh-worker.*.log，脚本仍aid-session-submitted-worker.py。累计5条submitted，本次部署后0条；原18:01:13及10/25/20上限不变。旧消息不补发，不能拼接为连续10轮稳定。

本次仅部署用户批准的pixel_gap有界重读修复（独立43项/CR通过）。其他新问题只诊断、解释，用户逐项确认才改。继续只读监控及各阶段耗时。


## 用户逐项复盘要求与本次批准范围（优先于旧自动修复指令）

- 用户要求先复盘流程，只修改逐项认可的问题。已明确批准本次将普通观察pixel_gap由硬阻断改为最多一次额外完整重读；持续像素变化也不能单独拒绝已识别对象。代码与build完成，独立8文件43/43通过（无跳过）、独立CR通过；尚未部署到运行中Provider。滚动条裁边方案未实施，其他OCR/对齐/frame_changed规则留待用户复盘。
- 原pixel_gap已本地定位为滚动条1944个像素变化，不是漏读文字的证据。停止该非错误分支的故障截图加密写入；名称实验不保证所有无文字媒体均被识别。
- 17:48只读任务active/version6/fence9，累计5条submitted，无unknown。最新一轮batch→receipt38.478s：建决策0.357、worker1.385、模型及结算17.412、结算→invocation2.228、领取1.263、开始0.131、执行回执15.701。任务原18:01:13期限不变，不能声称连续10轮稳定。
- waynelu监控已改仅只读状态与耗时，禁止按旧指令自动改代码/重启/重建基线。本次批准修复由当前对话执行；其余建议不自动实施。


## 17:38 截断历史修复后已恢复（最新状态）

- 当前同一任务active/version6/fence9，17:37:26新baseline已ACK1，连续普通observe3.1/3.2秒成功，续租推进。累计4条submitted；本次修复后0条，不能累计拼接稳定10轮。原截止18:01:13、上限10/25/20不变。
- Runtime仍88300，Provider已更换为84464，worker85948；worker日志TEMP/aid-session-clipped-worker.*.log，脚本仍aid-session-submitted-worker.py。Runtime日志仍aid-runtime-retention.stderr.log。状态/耗时脚本沿用。
- splitClippedTop墨迹边缘修复已build、独立34项相关覆盖通过，最终fill>=1锚点增加后独立name-session13项重跑通过，CR通过；当前真机只读原故障已消失（11objects/1clipped）。更新Provider后工作台fresh_baseline恢复，已有消息归基线不补发。


## 17:32 顶部截断历史OCR故障修复中

- 当前累计4条submitted、retention后2条；fence8租约稳定。17:27新peer之后连续观察UI_CHANGED，当前标题WayneLu本地OCR0.9983，独立只读复现ocr_gap:crossing_boundary（不是发送回执失败）。
- 顶部历史self实际被窗口标题栏截断，region x1209..1762 y160..188，viewport y150，OCR残字框y159..169 score0.765。splitClippedTop只数纯背景fill首行比例0.613，被黑字笔画干扰；按现有气泡fill+near-black抗锯齿混合模型首行比例1.0。修平顶计数包含墨迹，同时至少1个真实fill像素；nearTop、crossing、85%其他条件与通用OCR边界保持。
- 临时私有fixture TEMP/aid-observe-gap-fixture.json，仅本机不得提交；dist/src/platform/nameSession-diagnostic.js是只读临时复现（预置knownNames，不搜索/发送），不是正式源码。测试以匿名合成数据覆盖。
- 已工作台维护暂停当前任务，最小修复build/开发13项通过，独立最终fill锚点复核中；当前真机只读read已success（11objects含1clipped），原crossing_boundary已消失。随后更新Provider并fresh_baseline恢复。不改4条submitted，不补历史，仍非连续10轮通过。


## 17:27 持续观察记录

- 累计第4条、修复后第2条：invocation fe5eef39-bd86-467e-8830-9357f001c840 已submitted并ACK；fence8保持、无新gap/unknown，进程正常。
- input_version5耗时44.402s：建决策0.290、worker3.980、模型及结算19.105、结算→invocation3.192、领取1.499、开始0.198、执行回执16.139；Provider11.375s。较修复后首条慢主要在模型及结算（8.628→19.105s），不再出现31秒空档。
- input_version4仅self回显，决策未调用模型/未发送，随后superseded，不计回复轮数。仍未达到连续10轮，按用户要求不逐轮通知。


## 17:24 修复后首条耗时

- 新peer触发第三次累计发送，invocation757d3a8e-6b69-4445-84f0-cb8574f8d088已submitted并ACK。累计3/10，修复后连续1条，fence8跨多个60秒周期仍保持，续租正常。
- 第3轮batch→receipt32.345s：建决策0.245、worker等待4.631、模型及结算8.628、结算→invocation1.983、领取1.148、开始0.109、执行回执15.601秒；Provider发送10.983秒。此前结算后的31–33秒空档本轮降至约2秒。仍需继续样本，不能仅1轮判稳定。
- 耗时脚本已修正按decision.batch_id关联批次（fresh_baseline生成同input_version的synthetic批次，单按版本会重复旧决策）；不改变业务状态。


## 17:21 retention修复后接续（最新运行状态）

- 同一任务6059a3ff-f649-46ad-8243-207cf0b764ee已在工作台由维护paused恢复为active/version4，明确fresh_baseline，assignment fence8。17:20:59新baseline已ACK1，随后连续3次observe约3.2s成功；租约已多次续至17:22:01，尚需跨周期持续验证。旧两条submitted保持，历史消息不补。
- Runtime88300、Provider78176、worker87320；API仍父92288。Runtime日志TEMP/aid-runtime-retention.*.log；worker脚本仍TEMP/aid-session-submitted-worker.py，日志改aid-session-retention-worker.*.log。原状态/耗时脚本沿用。
- 周期retention修复已部署、build及独立48项有效覆盖/CR通过；实际目录检查22.34ms且删除0。新基线的冷启动观察33.35s，只发生初始化；后续普通观察约3.2s。继续等待新peer并对比回复耗时，不声称已有10轮稳定。
- 当前累计2/10，修复后连续发送0。原授权截止18:01:13与上限不变，不能把维护前后拼成连续10轮。


## 17:16 性能故障维护（优先于下方状态）

- 已发现周期retention是明确调度阻塞源：engine.run每60s await checkRetention，retentionStates逐条DPAPI解密所有旧assignment日志，最终却全部terminal=false/noPendingJournal=false不能清理。只读实测16条解密14.792秒，本机约85条事件；等待期间主循环不再调度renewDue/claim/pollDecision。与前两轮31–33秒发送空档及后续60秒租约过期一致，精确每轮归因仍缺直接span。
- 17:12起fence持续到7，后续观察全为alignment_broken；仅两条submitted，不能算连续10轮通过。已通过工作台暂停当前任务（version3），worker因paused退出；Runtime85752/Provider89896已停止用于维护。API仍运行。
- 修复：周期保留仅执行enforceRetention(runtimeHome,[],{now})，保留磁盘统计/stopNew、不删旧日志；删无效retentionStates回放，启动恢复不变。独立CR通过，独立48项有效覆盖通过（初次47/48，新增恢复测试fixture修正后3/3通过，原45项复用）。同实际目录容量检查22.34ms、deleted0。新版Runtime88300已启动，日志TEMP/aid-runtime-retention.*.log，正在一次性启动恢复。恢复需工作台选择fresh_baseline，不补旧消息/不改submitted；沿用当前任务与原18:01:13期限及上限，不新建任务。


## 17:10 耗时跟踪（用户新增要求）

- 每轮运行TEMP/aid-session-timings.py --tenant tenant_1dc997a1806b --task 6059a3ff-f649-46ad-8243-207cf0b764ee；需仓库PYTHONPATH与venv。只读事务，输出阶段秒数，无正文。数据库会话Asia/Shanghai，旧invocation无时区字段按该时区解释。
- 两轮依次：接纳→建决策0.219/0.391s；worker等待3.016/2.422s；模型尝试及结算10.002/15.192s；结算后→invocation32.858/31.055s；领取1.157/1.174s；领取→开始0.349/0.111s；开始→回执15.870/15.945s；总计63.471/66.290s。Provider发送子区间11.105/11.392s（不要重复相加）。
- 最大待定位空档为结算后31–33秒，不能仅凭跨度断言轮询/排队根因。attempt.updated_at为结算更新时间，不是独立模型返回/ready埋点。缺屏幕消息出现时刻、标题OCR/输入/Enter分段计时；不伪造这些数值。先持续记录现有时间点，保持10轮观察。
- 17:08曾出现generation换代恢复记录，assignment变为fence2，task仍active，两个submitted不变；不可声称当前全程无中断稳定。监控须继续核查观察/接续结果，不能自动解除人工接管。


## 17:02 接续已启动（当前状态，优先于历史快照）

- 新任务 `6059a3ff-f649-46ad-8243-207cf0b764ee` 经真实名称定位 `d835143d-f5cb-4592-b8aa-0315ceda9431`、prepare、工作台核对发布，active / fence1；baseline已ACK（local_seq1），连续observe成功。只响应新消息，无开场白，最多10回复/25决策/20积分，截止18:01:13。
- 已部署下节submitted新语义：API父PID92288，Runtime85752，Provider89896；决策worker92652，TEMP/aid-session-submitted-worker.py。日志aid-api-submitted.*.log、aid-runtime-submitted.*.log、aid-session-submitted-worker.*.log。只读状态TEMP/aid-submitted-status.py，需仓库PYTHONPATH及venv。
- 监控waynelu切换到新任务；用户可从WayneLu另一端发新消息。17:05首条新peer触发自主回复，invocation `771014ba-3773-4cb2-9495-60343fd0a7e0` 已正式ACK：state=succeeded / phase=submitted / effect=applied，Provider耗时11.1秒，无发送后OCR。17:07第二条invocation `c54bcd6b-d7e7-426b-8432-81eae651f78d` 已ACK，当前2条已执行发送、无unknown、任务active。首条self随下一peer入批未触发人工介入；连续10轮未完成，用户明确要求达到10轮再判断稳定。
- 旧db900任务已stopped/version4，保留2succeeded/1unknown；原未发送草稿已由新任务授权回复替换，不补发旧正文。


## 最新发送语义修订（优先于下方历史快照）

- 用户进一步明确：发送前只核验联系人，替换输入并执行回车；发送后不做 OCR、正文等值或 pixel_gap 核验。动作正常完成记录 `submitted`，显示“已执行发送（未核验送达）”，不得伪装为 `verified`。
- 第三次旧请求 `b97b19c3-d93c-4368-82f3-eaa92d7808ec` 在 `draft_ocr` 失败，无 Enter 阶段；旧任务 `db900756-4db5-410d-bb75-2dd98a9033a8` blocked，2 succeeded / 1 unknown，worker退出，waynelu监控暂停。旧任务现已通过工作台停止（version4），旧unknown不改判、不重发。
- 新代码已删除发送前草稿OCR与全部发送后读取；保留名称标题核验、许可期限/取消、请求journal防重发。PS对聚焦点击、文字、Enter事件投递检查返回值；该证据只表明操作提交，不是对方收到。
- 服务端只对冻结 `receipt_mode=submission / receipt_context=weixin_name` 的有效名称场景允许 `submitted`；仍绑定设备、许可、请求、目标、载荷、专用 `weixin-submission` 引用。旧verified路径保持兼容。
- 后续self观察的正文精确匹配也在同步修复，避免OCR差异误触发人工介入。普通消息观察覆盖规则仍是独立路径。
- 已完成真机非空草稿替换 smoke：标题确认后仅type，没有Enter；替换后18字草稿匹配、标题不变。OCR在此只作开发验证，不是运行时发送门禁。独立TS定向32项、Runtime桥1项、前端6项、Python隔离117项通过；PS六类投递失败mock及AST通过，前端build通过；独立CR已通过。服务已重启（API父PID92288、Runtime85752、Provider89896），只读名称定位健康检查进行中。不能据此宣称连续10轮通过。


## 16:36 接续已就绪（当前运行状态）

- 用户询问是否可继续测试后，已通过工作台停止旧任务4cd7b1d5-5089-4f85-a653-72e193b52b50，保留1succeeded/1unknown。
- 新任务`db900756-4db5-410d-bb75-2dd98a9033a8`经真实resolve（6f6c9699-1ae9-42b1-b04c-2f43ac13f1c2）→prepare→工作台确认发布；active、fence1、baseline已ACK、连续observe成功。无开场白，最多10回复/25决策/20积分，期限17:34:35。
- Runtime PID22196、Provider PID72576（已部署独立OCR发送回执），worker PID85256，脚本TEMP/aid-session-receipt-worker.py、日志aid-session-receipt-worker.stdout.log及stderr.log。只读状态脚本TEMP/aid-receipt-status.py，需仓库PYTHONPATH及venv。
- 监控waynelu已更新至新任务并恢复，每分钟检查；用户可从WayneLu另一端发送新的左侧消息。旧消息归基线，不补发。16:38首轮正式succeeded且ACK（invocation72db4320-fc96-4651-9997-fd2c4aca39d7），后续observe成功并接到新peer，第二轮决策启动。16:40已正式2/10，第二轮invocation1db0077e-8117-4b5c-b8f0-4aeee55319d6已ACK；第三批新peer已接收并启动决策。无unknown，不代表已完成连续验收。

## 发送回执变更（用户明确要求，优先于以下快照）

- 用户要求发送完成后不校验pixel_gap，只以OCR确认所发内容出现在对话消息中。已实现独立`read_receipt`路径：本地标题核对、完整高置信文字气泡提取、唯一有序新增self与授权正文匹配。发送回执不执行pixel_gap、浮层门禁、fresh.frame相等或前帧不等检查，也不修改观察像素基线、不宣称complete_window。
- 保留单次submit、请求journal、期限/取消、历史同文不能充当新增。非空但只有媒体/截断内容的原基线缺文字锚点，返回unknown；真正空白会话可验证首条。
- 第二轮原失败帧回放经新算法唯一对齐、恰好新增1条self，归一化OCR正文SHA256与原服务端授权decision一致。旧unknown保持不变，未重发。
- build:main成功；独立六文件38项全部通过，独立CR发现的无锚点误判问题已修复并复核通过。主要变更`nameSession.ts`与`tests/name-session.test.ts`。编译输出及运行Provider已更新（PID72576），通过Runtime真实名称定位invocation33fa0930-fa96-46e2-9951-807b67ddfed5成功；更新在旧任务blocked时执行，未发送新消息。
- 本次只改发送回执。普通观察仍保留pixel_gap/frame_changed及原媒体覆盖规则，若后续观察失败需单独处理；不能把本次修改描述为整个观察链路已替换。新客户端发送真机验收仍待进行；当前产品任务仍blocked、worker退出、监控暂停，正式1轮成功+1条unknown。

## 最新状态（16:25，优先于以下全部快照）

- 当前任务 `4cd7b1d5-5089-4f85-a653-72e193b52b50` 已于16:18再次blocked/unknown_send_result，version=3，worker正常退出；正式账本1条succeeded、1条unknown。第一轮invocation `ad0dd776-f83c-4290-b76e-ba255ffc4cee`成功且ACK；第二轮 `6684d4a6-a6ef-46a4-b60d-0bb8914bf80a`两次post_read均pixel_gap，未重发。
- 新失败原帧与发送前原帧已保存在本机TEMP：`aid-round2-before.png`（证据4b5fe965-b53c-4428-88c3-e12b7e3e61f7）及`aid-round2-gap.png`。离线脚本`aid-round2.py`/`aid-round2.mjs`复现-112px滚动，9629个未覆盖像素集中右上浮层。该次OCR把箭头并入文字，识别为“个0条新消息”、置信度0.8639，未命中首版精确规则；浮层同时影响顶部历史气泡分割。
- 用户已质疑跨帧像素比较的合理性，并要求先解释在哪一步、为何比较。已解释：原为弥补OCR漏无文字媒体，但读取被发送后验证复用，导致画面完整性检查阻断实际发送回执。当前尚未实施替代方案，不继续堆浮层例外。
- 独立设计审查建议分离发送效果验证与观察完整性；frame仅作变化触发/证据指纹，不能以pixel_gap或fresh.frame相等作为成功门禁。但直接删除后仍报告complete_window会漏掉无文字图片，需要同步处理非文字检测或明确文字实验能力契约。等待后续讨论确定范围，不伪称已解决。
- 自动监控暂停，防止已知blocked状态反复唤醒；修复/实验接续时再更新并恢复。旧unknown证据和账本保持不变。真实连续10轮未完成。

## 接续更新（16:14，优先于下方旧快照）

- 原失败已离线复现：发送前到故障帧唯一滚动-112px，全部3727个未覆盖像素位于右上“0条新消息”浮层。新增有限重采样，不豁免像素，浮层持续则拒绝；原deadline/取消覆盖DPAPI及证据写入，更新缓存和verified前复核。独立35项通过，CR通过。
- 原帧回放为gap→后续原帧成功；真实编译客户端名称定位+连续3次读取成功。尚未证明真机新发送遇到浮层时能自行恢复，连续10轮仍未完成。
- 旧任务 `d6677809-586d-4a2a-8090-eff057c8b940` 已在工作台停止，unknown不改判、不重发。
- 新任务 `4cd7b1d5-5089-4f85-a653-72e193b52b50` 经真实resolve→prepare→工作台确认发布，active、fence=1，baseline事件已ACK（local_seq=1），连续observe成功。无开场白，最多10回复/25决策/20积分，期限17:12:05。
- Runtime仍PID22196，Provider已更新并重新启动PID75732。worker PID63252，脚本 `%TEMP%/aid-session-overlay-worker.py`，日志 `aid-session-overlay-worker.stdout.log` / `.stderr.log`。只读状态脚本 `%TEMP%/aid-overlay-status.py`（运行需仓库PYTHONPATH及venv）。Runtime日志仍 `aid-runtime-postread.*.log`。
- 当前新任务正式成功0轮；等待WayneLu另一端新消息。新基线接续不补历史，也不是unknown核对恢复功能。原waynelu监控已恢复并切到当前会话与新任务，每分钟检查；无变化保持安静，故障继续核查，任务终态暂停。
- 编译只读实验 `%TEMP%/aid-live-read.mjs`；离线复现 `%TEMP%/aid-repro.py` 和 `aid-repro.mjs`，读取本机临时原图，输出无聊天正文。持续浮层提前拒绝不自动存原帧，目前只有专属原因码。

> 新会话先读本文，再读关联代码。当前为部分完成，真实连续10轮未通过；不要把单测、截图看到送达或一次成功描述成稳定可用。

## 1. 用户目标与约束

- 在本机已登录的微信上，由编译后的 Runtime 接收服务端任务、自主监听并回复 WayneLu，测试约10轮。开发助手在后面观察，不逐条驱动发送。
- 当前目标实际显示名称 **WayneLu**（用户输入 waynelu），不再找覃姗。
- 不采集当前登录微信号、不要求额外账号绑定；使用本机已登录微信，按名称定位聊天。
- 聊天记录必须走本地截图 + RapidOCR。曾误接云端视觉全文识别，已撤下；不要重新引入。
- 发送前确认当前聊天标题、文字确实进入输入框即可；已取消整窗哈希不变要求。不要重复添加无关门禁。
- 用户已授权普通测试聊天、说明AI测试身份和后续运营支持目的，不需再次询问收件人或发送授权。不重复开场白，不重发 unknown 请求。
- 固定营销回归的28项失败/A8可后置；优先完成客户端自动聊天。
- 用户对反复“好了，请再发”及只通知故障后停止非常不满。继续时应查证、修复、验证、实际恢复；不能假称回复结束后仍在后台修复。
- **未授权提交代码**。当前分支 master，工作区有大量本任务及无关BOSS改动，禁止整体重置、stash、全量暂存或自动提交。

## 2. 当前真实状态（交接时已查询）

| 项目 | 状态 |
|---|---|
| 当前产品任务 | `d6677809-586d-4a2a-8090-eff057c8b940` |
| 当前状态 | `blocked / unknown_send_result`，最近assignment fence=2 |
| 当前任务发送 | 1条 unknown，截图可见本人气泡；不能计为正式成功 |
| 前一个任务 | `6cddf962-9e20-48ae-bcf2-1920c0cf0076` 已 stopped，保留1条 succeeded、1条 unknown |
| 正式成功轮数 | 前任务1轮；连续10轮未完成 |
| Runtime | 运行中，PID 22196；Provider PID 76992（后续先重新检查PID） |
| API | `http://127.0.0.1:8000`，venv启动，父PID 89548、实际Python PID 32208 |
| 网页 | `http://127.0.0.1:5173`，用户已登录 |
| 决策worker | 因任务blocked正常退出，目前不运行 |
| Codex监控 | automation ID `waynelu`，交接时已暂停，避免旧会话继续唤醒 |

任务页面：<http://127.0.0.1:5173/t/tenant_1dc997a1806b/weixin-marketing/session-tasks/d6677809-586d-4a2a-8090-eff057c8b940>

- 租户：`tenant_1dc997a1806b`
- 本机设备：`271af321-5131-4674-b2de-dfac9b8f5949`，名称“本机微信联测”，Runtime 0.2.13。
- Runtime配置：`%APPDATA%/aidwork-tool-runtime/config.json`，凭据为DPAPI文件。不要打印整份配置或凭据。
- API使用 `C:/repos/aid-work-agent/venv/Scripts/python.exe`，不是 `.venv`。
- `configs/config.yaml` 的 session_tasks/weixin_conversation已对本次租户开启；Runtime sessionTasks及v2Send已开启。
- `.env` 已有本地RPA加密密钥，不能输出或提交。历史 CRYPTO_UNAVAILABLE 已解决。

## 3. 最新故障：现在终于有失败瞬间证据

### 时间线

1. 当前任务首次运行时，本机账号于15:45发出右侧绿色短问候语，被正确识别self，触发人工接管。**这次不是OCR误判。**用户已获说明：应从WayneLu另一端发来左侧消息测试。
2. 同一任务恢复至fence=2，新基线建立、worker启动。
3. 15:50对方发来左侧消息，真正进入模型决策，生成回复并执行发送。
4. invocation **`dddaed52-0756-4dbb-9a9b-b79be9eef9c2`**：阶段 `precheck → type → draft_ocr → enter → post_read`，两次post_read均 `UI_CHANGED_PIXEL_GAP`，返回 `EXECUTION_UNKNOWN`，服务端已ACK，未重发。
5. 失败截图可见新本人回复气泡，右上有 **“0条新消息”浮层**。随后成功读取的截图浮层消失，并多了一条对方回复。

### 已知事实与未知项

- 本人回复确实出现在截图里；正式账本仍unknown，不能直接改判。
- 单次重读没有解决这次异常。
- “0条新消息”浮层是明确可见的新线索，**还没有离线证明它就是pixel_gap的全部根因**。
- 失败图与后续成功图的全ROI diff覆盖多个区域，因为后续新增peer导致再次滚动；不能直接凭两图差分认定浮层根因。
- 下一步应使用**发送前成功证据与失败原帧**复现，不只用后续稳定画面。

### 本地证据（私聊截图，不提交、不上传、不展开无关历史）

根目录：`%LOCALAPPDATA%/aid-weixin/`

| 用途 | 文件 |
|---|---|
| 此次发送前成功截图，15:51:05 | `ocr-evidence/f9e0f0df-4732-4834-b5c2-e4867db9531c.dpapi` |
| 此次失败截图，15:52:09 | `ocr-gap-evidence/07007758e9871a5f38d17e89b717ff248ff9ffa796d4f1b4801aa30fd97e60aa.dpapi` |
| 失败截图已解密副本 | `%TEMP%/aid-gap-1552.png` |
| 随后成功截图，15:52:20 | `ocr-evidence/436b4561-4773-4b0d-aba5-9bc32b86f81e.dpapi` |
| 随后成功截图已解密副本 | `%TEMP%/aid-stable-1552.png` |
| 15:45真实self事件截图 | `%TEMP%/aid-self-event.png` |

DPAPI文件内容是base64密文；解密所得是PNG的base64字符串。复用 `clients/weixin-cli/src/security/dpapi.ts` 或本机.NET ProtectedData CurrentUser，直接写临时PNG，**不要输出base64或密文解密正文**。

失败证据按联系人hash只保留最新一份，后续失败可能覆盖；如需固定本次样本，先在本机保留上述副本。

## 4. 关键代码与已完成修复

### 本地OCR和发送

- `clients/weixin-cli/src/platform/nameSession.ts`：真实截图、常驻RapidOCR、标题/草稿识别、消息对象、像素覆盖、发送后回执。
- `clients/weixin-cli/drivers/ps1/name-ocr.ps1`：Qt窗口PrintWindow、搜索popup、输入和Enter。
- `clients/weixin-cli/src/platform/bubbleSegmentation.ts`：文字气泡候选及OCR聚合。
- `clients/weixin-cli/src/operations/nameOperations.ts`：名称resolve/observe/send、内存watermark与对齐状态。
- `clients/weixin-cli/src/platform/aligner.ts`：有序后缀/前缀对齐与消息ID。
- `clients/agent-tool-runtime/src/sessionTasks/nameBridge.ts`：服务端许可到本地发送上下文的签名桥。

已修：

1. Qt搜索独立popup定位；实际WayneLu名称匹配。
2. 长气泡按边缘判方向，OCR按气泡聚合；顶部截断作为clipped历史，不当新正文。
3. “星期X HH:mm”不再误当图片：日期时间、置信度、居中窄行、viewport和气泡不相交约束。
4. 取消发送时整窗hash不变限制；输入框空则输入，相同草稿复用，不同拒绝；Enter前点击输入区保证焦点。
5. 发送后允许一个新增self同时有新增peer；OCR视觉CR/LF换行可与单行授权正文比较，空格仍严格。
6. post_read中frame_changed/pixel_gap两类错误**合计最多重读一次**，原signal/deadline不延长，submit永不重试；持续异常仍unknown。
7. pixel_gap抛出前保存最新DPAPI失败截图；原子替换；诊断加密受原取消信号+2秒超时限制，终止PowerShell，失败仍拒绝。

`hasUncoveredPixelChange`核心：完整文字序列找到唯一重叠及统一向上位移；比较平移后的像素，仅当前文字气泡、相邻头像、严格时间标签等解释变化。当前尚未专门处理“0条新消息”浮层。**不要用直接屏蔽大片区域的方式掩盖新图片或消息变化。**

原 `historyRead.ts`、p4 `run.ps1` / `ocr_chat.py` 保留，聊天内容不走云端视觉模型。

### 服务端与Runtime

- `src/main.py`：API进程生命周期注册会话场景hooks，修复prepare-send进程内缺注册导致500。
- `src/session_tasks/decisions.py`：成功发送回显归属支持OCR换行，保留成功次数容量和空格差异；unknown不给容量。
- 同文件 `_process_reply`：SQL确认**本批**存在accepted peer后才调用模型；纯self/system批次ready/wait，不反复回答历史消息。有peer但密文不可读仍走原错误门禁。
- `src/session_tasks/workbench.py`：当前resume只支持fresh_baseline，跳过已有历史。unknown阻断不能通过普通resume消除。
- Provider对齐状态/像素缓存为内存状态；重启后旧watermark无法自动恢复。不能无证据伪造缓存或改账本来恢复。

## 5. 验证结果与界限

- Provider四组定向26项通过，包括新增11帧/连续10次追加的合成序列，覆盖滚动、截断、时间行、同时self/peer及四类拒绝分叉。
- 最终诊断取消增量：name-session + dpapi-cancel，17项独立通过；真实PowerShell休眠10秒在250ms取消测试通过。
- 服务端回显及self-only：最终15项独立通过；既有5项reply集成回归通过；独立审查发现的不可读peer误判无peer问题已修复。
- 主控正式只读名称定位/OCR成功，已运行编译后客户端。
- 真机仅旧任务1轮正式succeeded；另外看到气泡的unknown均未篡改回执。
- 合成10次追加 ≠ 真机10轮。媒体仿制、全局同名、重启待处理消息恢复、unknown核对恢复、A9–A11仍未闭环，A8后置。

相关命令：

```powershell
npm --prefix clients/weixin-cli run build:main
node --test clients/weixin-cli/dist/tests/name-session.test.js clients/weixin-cli/dist/tests/name-pixel-coverage.test.js clients/weixin-cli/dist/tests/name-operations.test.js clients/weixin-cli/dist/tests/name-continuous-coverage.test.js clients/weixin-cli/dist/tests/dpapi-cancel.test.js
```

Python按项目 `scripts/dev_test.sh` 执行，Git Bash PATH前置 `venv/Scripts`；需要真实DB的回归用既有 `--isolated-db`，不要在共享库启动无关调度测试。按dev-workflow执行独立测试与CR，复用相同代码已有结果，避免机械全量重跑。

## 6. 运行文件与恢复注意

- 当前Runtime日志：`%TEMP%/aid-runtime-postread.stdout.log`、`stderr.log`。
- 当前worker脚本：`%TEMP%/aid-session-resumed-worker.py`；日志 `aid-session-resumed-worker-2.stdout.log` / `stderr.log`，已显示 `Worker stopped: blocked`。
- worker调用真实 `run_decision_tick()`，每5秒检查目标任务状态，非active/queued退出；不是开发助手逐条生成内容。脚本可能丢失，启动前先读取核对task ID。
- API日志：`%TEMP%/aid-api-echo.stdout.log` / `stderr.log`。
- 本地单条发送journal：`%LOCALAPPDATA%/aid-weixin/name-requests/`，`.stages.jsonl`只含阶段/错误码；不要删除unknown标记。
- TEMP中的旧 `aid-waynelu-task.py` 含开场白，**不要运行**。`aid-waynelu-resume-task.py` 会创建新草稿，不能不加判断重复运行。
- 当前任务原期限16:30:37（北京时间），接续前核对是否已过期、是否已有人新发消息及预算。
- 新任务草稿由产品工具prepare、真实resolve回执产生；发布需走工作台确认链。不能伪造confirmation或verified证据。
- 旧任务曾经通过停止并新建无开场白任务接续，这只是绕开旧任务继续测试，**不是unknown恢复功能已实现**。重复新基线会跳过当前未答消息，不应作为长期解决方案。

## 7. 新会话建议执行顺序

1. 确认本机服务/PID/当前任务状态；避免启动重复Runtime或worker。不必让用户再次发同一消息。
2. 以15:51发送前证据和15:52失败截图离线重跑同一OCR/气泡/位移/像素流程，输出差异位置、对象数量、原因枚举，不展开聊天原文。
3. 验证“0条新消息”浮层是否为根因，区分UI浮层与真实消息像素；补能复现真实失败的回归，再实现最小修复。不能把稳定截图通过当成故障已解决。
4. 独立测试和CR后编译更新客户端，处理保留unknown证据后的接续方案；当前无正式核对恢复入口，需明确解决，禁止直接SQL把unknown改succeeded。
5. 真正恢复后检查assignment已领取、baseline已持久化、连续observe成功、worker活着，再告知用户可继续。用户从WayneLu另一端发送左侧peer消息。
6. 如恢复后台监控，更新automation到新会话/当前任务和正确日志；监控不是后台修复。出现故障应继续核查证据，不只通知一句就停。不要假称在本轮结束后继续编码。

## 8. 关联文档

- [设计](../../design/desktop-automation/edge-session-task-design.md)
- [开发计划](plan-edge-session-task.md)
- [持续验收记录](../../research/weixin-cli/edge-session-acceptance.md)
- [进行中索引](../../ideas.md)

本文是交接快照；新会话应先复查运行状态，保留用户后来新增的代码和消息。
