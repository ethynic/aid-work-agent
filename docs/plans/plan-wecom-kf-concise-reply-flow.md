# 微信客服渠道回复流程精简 — 开发计划

> 关联设计：[wecom_kf_concise_reply_flow.md](../channel/wecom_kf/wecom_kf_concise_reply_flow.md)
> ideas 条目：20261123-1341（docs/ideas.md 渠道集成分区）
> 需求来源：飞书《项目需求及修改清单》智能客服 sheet 行 4（2026-11-18，旅行社，P2）
> 流程：高风险（微信客服交付链路与回复预算、旅行社业务流程提示词）→ 三智能体（开发 → 独立测试 → CR）

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 渠道层：md 单独发送（设计 §5.2、§5.3） | ✅ 完成（2026-11-23） | 改 `adapter.py`、`reply_delivery.py`、`reply_format.py`；更新 7 项、新增 12 项用例；`tests/unit/channels` 1014 项通过；独立测试两轮通过，第一轮发现的降级文字回归已修复；CR 无 P0/P1；更大范围回归的 10 项失败经对照为原有失败 |
| Phase 2 | 旅行社提示词：微信客服渠道分支（设计 §5.1） | ✅ 完成（2026-11-23） | 改 `SUBAGENT.md`、`channel_routes.py` 渠道标识；本地 deepseek-flash 23 次行程模拟：确认粗略行程后一句话 + 行程 Word，修改行程直接重发 Word，改行程同时报价时先发新 Word 再报价，对话无表格、无 md，模型实际输入含分支与渠道标识；23 次行程模拟共 23 个报价轮，其中 21 次未重复发行程 Word（这 21 次里有 1 次报价 Excel 被模型重复登记，见遗留），2 次重复发行程 Word（见遗留） |
| Phase 3 | 回归与 agent2 真机验证 | 📋 待开发 | 真机验证需用户授权部署 |

## Phase 1 — 渠道层：md 单独发送

改动点：

1. `src/channels/wecom_kf/adapter.py` 的 `_send_prepared_reply`
   - prefix / notice / llm 模式：只发送 md 文件，不再发送前缀文字
   - image 模式：仍为长图 + md
   - failed 模式：降级发送前缀文字（去掉文件提示，受单条 2048 字节上限约束），替代固定失败话术
   - md 上传失败：发送下载链接；无可用链接时降级发送前缀文字
2. `src/saas/api/channel_routes.py`：进度提示预留条数维持 2，不新增改动；核对最终交付的预算消耗与设计 §5.3 一致
3. `src/channels/wecom_kf/reply_delivery.py`：`channelDelivery.text` 继续保存前缀文字；仅在需要时调整降级文案的生成

验证：

- 单测覆盖各交付模式的实际发送序列：只发 file 不发 text、image 仍 2 条、failed 降级、上传失败降级、预算消耗
- 既有 `reply_delivery` / `reply_format` / `adapter` / `budget` 测试不弱化，受影响断言按新行为更新
- 命令：`bash scripts/dev_test.sh tests/unit/channels/test_wecom_kf_reply_delivery.py tests/unit/channels/test_wecom_kf_reply_format.py tests/unit/channels/<adapter 与 budget 测试> -p no:cacheprovider -q`
- 集成：`docker compose -f docker-compose.test-db.yml run --rm sql tests/integration/test_wecom_kf_reply_delivery.py -p no:cacheprovider -q`

## Phase 2 — 旅行社提示词：微信客服渠道分支

改动点：`subagents/travel-consultant/SUBAGENT.md`

1. 新增微信客服渠道分支的适用条件：对话中出现渠道标识「【渠道：微信客服】」；标识不出现时走现有流程
2. `src/saas/api/channel_routes.py`：`_KF_CHANNEL_EXTRA_SYSTEM_PROMPT` 开头加固定标识「【渠道：微信客服】」，其后的现有文字不变
3. 阶段二第四步（细化行程）：客户满意粗略行程后直接进入细化并调用 `word_process` 生成 Word，不在回复文字中输出表格；白名单自检保留
4. 客户对详细行程的修改：修改后直接重新生成 Word
5. 阶段三报价与换酒店：回复只写一句话（总价、人均，说明明细在 Excel 里），不输出明细表
6. 报价 `itinerary_text`：明确取生成行程 Word 时的同一份完整行程 Markdown
7. 「快速判断当前阶段」「行为约束」两处同步补充微信客服渠道的说明
8. 报价环节不重复发行程 Word：客户已收到当前这版行程 Word 时，报价只生成报价 Excel（2026-11-23 开发自测发现偶发重复后补充）

验证：

- 提示词无法单测；核对文件结构与 frontmatter 完好，仓库内与旅行社提示词相关的测试（如有）通过
- 行为验证在 Phase 3 真机完成

## Phase 3 — 回归与 agent2 真机验证

1. 回归：`tests/unit/channels/` 全目录；微信客服相关集成测试
2. agent2 真机（微信客服渠道，旅行社智能体，需用户安排并授权部署）：
   - 完整走一遍演示对话，核对客户收到 7 条消息、2 个文件（行程 Word、报价 Excel；第 7 条是 `travel-quote` 技能自带的进度提示），无 md、无重复内容
   - 检查模型在微信客服渠道分支下的遵循情况：回复文字无表格、直接生成 Word、报价一句话
   - 验证 `word_process` 调用失败后的恢复路径（设计 §六 风险 2）
   - 核对报价结果与原流程一致（`itinerary_text` 取值来源变化）
   - 核对客户修改详细行程后能重新生成 Word
   - 核对报价环节没有重复发行程 Word
   - 客户在同一句话里要求修改详细行程并报价：应先发新 Word，报价与新版行程对应
   - 客户在尚未生成行程 Word 时直接要求报价：应先生成并发 Word，再按规则决定是否报价
   - 核对含表格的其他回复只发 md
3. Web 端回归：旅行社流程与改动前一致
4. 收尾：更新本计划进度登记区、`docs/ideas.md` 状态；部署与飞书任务状态更新由用户指示

## 遗留

范围外，建议另立项：

- 回复预算优先级：业务文件被进度提示与自动 md 挤占而静默丢弃（2026-11-10 至 11-13 共 21 个文件），见设计 §七
- `summary_mode=llm` 的租户仍会为不再发送的摘要调用一次计费模型。依据：本地 agent2 库副本中全部微信客服渠道配置都没有该字段（生产库未核对）

已记录、待 Phase 3 真机核对：

- 报价环节偶发重新生成并重发行程 Word：本地模型 23 次报价中出现 2 次，模型自述原因是上一版“图集和细节”不全。已在「报价调用方式」下加明确约束（现为条件式：客户已收到当前这版行程 Word 时不重复发），之后 11 次未复现（绝对化措辞 8 次、条件式措辞 3 次），样本小，需用生产模型确认
- 模型偶发把同一份报价 Excel 登记两次（登记名打错后又登记一次），客户会收到两个相同的文件；本地 23 次报价中出现 1 次，与本次改动无关
- 含图片的回复在长图发送成功、md 上传失败且无公网地址时，长图页脚仍印着“完整回复请查看发送的文件《详细答复.md》。”而文件未送达（既有行为，客户已有完整内容）
- 换酒店时列 3-5 个候选，若模型在微信客服渠道用表格输出，会落入只发 md 的路径
- 提示词行为约束 3、6、7 的例外表述未重复“对话中出现标识”的括注，分支小节已有定义
