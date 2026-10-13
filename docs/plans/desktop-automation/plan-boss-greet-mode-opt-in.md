# BOSS 筛选后的打招呼模式与详情页显式启用

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 流程与参数边界确认 | ✅ 完成（2026-10-13） | 当前评分入库工具不发送；Agent工具描述引导合格后另调详情打招呼 |
| Phase 2 | 云端/Provider显式参数与模式编排 | ✅ 完成（2026-10-13） | Provider0.3.6；StrictBool/严格true双层门禁，默认人工审核与批准名单列表模式 |
| Phase 3 | 独立测试、CR与零发送验收 | ✅ 完成（2026-10-13） | 独立CLI66/Python44及额外CLI7/owned4通过，CR无阻断；未发送、未部署 |

## 需求与行为

筛选默认只评分入库并关闭详情，等待用户在简历库人工审核、给出打招呼名单，再调用列表页boss_greet(names=用户名单)。Agent首次筛选时若上下文没有模式，应询问“评分合格后直接打招呼，还是先人工审核再提供名单”；没有明确选择直接模式时，默认人工审核，只读筛选可继续，不能擅自给高分者发送。

用户明确要求本轮合格后直接打招呼时，按已有明确职位、任务人数及评分阈值授权，在单份boss_resume_detail确实成功入库且match_status=matched后调用boss_greet_detail(name=候选人,greet_in_detail=true)。不合格、失败、未评分或缺乏授权时boss_close_detail。模式仅属于当前任务，不创建账户级/职位库默认；用户改变选择后后续候选人按最新模式，不扩大授权。人工模式的高分/推荐名单不等于用户批准名单。

## 参数、兼容与契约

- 参数放在现有外部写动作boss_greet_detail：greet_in_detail:boolean默认false。保持boss_resume_detail纯读取+评分入库，避免把云端评分后派发混入既有只读工具/计费/durable契约；选择模式由Agent编排，不合并读写工具。
- 新调用缺省、false及非严格boolean true在云端参数检查前拒绝派发；Provider在连接Chrome前再次拒绝，effect:none。dry_run仍只定位，不能代替模式启用参数。CLI使用裸flag --greet-in-detail，显式字符串值false/true/1均拒绝，不用hasFlag将false误转true。
- 新Provider版本0.3.6，MCP输入新增可选默认false字段，旧客户机需要升级才具备本地默认关闭。云端同样检查显式true，混合版本下不会派发缺少/false字段的新调用；明确true仍复用原单候选人授权路径。历史已绑定invocation只轮询原记录，不改其参数、不重新发消息；normalize_provided_parameters只规范显式字段，不插入新默认。
- 已读共同集成契约v2.0，不修改Runtime core/Device wire/任务binding/Runner授权。保持LocalToolProxyTool.execute继承身份，复用_validate_args钩子以兼容durable/overlay路径，不覆写execute；旧unknown结果不自动重放。

高风险流程：主控开发、独立测试与独立CR。仅改BOSS业务参数/工具文案/招聘提示与相关测试，不修改简历库评分或UI；已有列表names精确配对和结果来源门禁继续有效。真机不发送，参数禁用路径与dry-run/已打过/错名/来源不符通过隔离替身验证。

## 验证与边界

- 主控Python44项、CLI59项初测通过；新增MCP集成时旧raw spawn测试未禁用自动Chrome启动，fake CDP端口连接等待超时，不能视为产品回归通过。已将SDK/raw子进程都显式禁用自动Chrome；最终独立build及六相关CLI/MCP套件66项通过（0失败/跳过），Python44项通过（4条既有Pydantic警告，venv与DB/Redis端口1隔离）。
- 独立额外CLI7个缺省/非法flag全部拒绝；owned4个内存断言覆盖旧intent不插默认、旧unknown只poll不派发及改变既有intent拒绝。额外测试脚本初轮文案断言不完整，修正断言后通过，无产品源码修改。
- 独立CR无P0/P1/P2：返回的resume_id/match_score/match_status足以供Agent判断已入库和真实合格；回写失败不会冒充matched。参数是显式意图开关，不能创造用户授权；人类选择与评分门槛遵循由Agent提示编排负责，未做真实LLM端到端遵循性验收。
- 未给真实候选人发送消息，真发行为以替身验证；已有外部写动作未知效果不重放。按会话已有开发完成后提交授权，提交本轮相关改动并推送已确认upstream，不部署。
- 提交前fetch发现远程通知/自动填表设计2项提交；仅对未提交共享索引文档做单文件临时保留后快进整合，恢复原文档改动并清理临时stash，其余已有改动保留。BOSS源码不重叠，整合后复跑受影响云端定向检查；仅暂存本任务文件。
