# 微信客服渠道回复流程精简 — 开发计划

> 关联设计：[wecom_kf_mobile_delivery_flow.md](../../channel/wecom_kf/wecom_kf_mobile_delivery_flow.md)
> ideas 条目：20261123-1341（docs/ideas.md 渠道集成分区）
> 需求来源：飞书《项目需求及修改清单》智能客服 sheet 行 4（2026-11-18，旅行社，P2）
> 流程：高风险（微信客服交付链路与回复预算、旅行社业务流程提示词）→ 三智能体（开发 → 独立测试 → CR）

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 渠道层：md 单独发送（设计 §5.2、§5.3） | 📋 待开发 | — |
| Phase 2 | 旅行社提示词：手机聊天渠道分支（设计 §5.1） | 📋 待开发 | — |
| Phase 3 | 回归与 agent2 真机验证 | 📋 待开发 | — |

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

## Phase 2 — 旅行社提示词：手机聊天渠道分支

改动点：`subagents/travel-consultant/SUBAGENT.md`

1. 新增手机聊天渠道分支的适用条件：系统提示说明当前会话来自手机聊天渠道（如企业微信客服）；条件不成立时走现有流程
2. 阶段二第四步（细化行程）：客户满意粗略行程后直接进入细化并调用 `word_process` 生成 Word，不在回复文字中输出表格；白名单自检保留
3. 客户对详细行程的修改：修改后直接重新生成 Word
4. 阶段三报价与换酒店：回复只写一句话（总价、人均，说明明细在 Excel 里），不输出明细表
5. 报价 `itinerary_text`：明确取生成行程 Word 时的同一份完整行程 Markdown
6. 「快速判断当前阶段」「行为约束」两处同步补充手机聊天渠道的说明

验证：

- 提示词无法单测；核对文件结构与 frontmatter 完好，仓库内与旅行社提示词相关的测试（如有）通过
- 行为验证在 Phase 3 真机完成

## Phase 3 — 回归与 agent2 真机验证

1. 回归：`tests/unit/channels/` 全目录；微信客服相关集成测试
2. agent2 真机（微信客服渠道，旅行社智能体，需用户安排并授权部署）：
   - 完整走一遍演示对话，核对客户收到 6 条消息、2 个文件（行程 Word、报价 Excel），无 md、无重复内容
   - 检查模型在手机渠道分支下的遵循情况：回复文字无表格、直接生成 Word、报价一句话
   - 验证 `word_process` 调用失败后的恢复路径（设计 §六 风险 2）
   - 核对报价结果与原流程一致（`itinerary_text` 取值来源变化）
   - 核对客户修改详细行程后能重新生成 Word
   - 核对含表格的其他回复只发 md
3. Web 端回归：旅行社流程与改动前一致
4. 收尾：更新本计划进度登记区、`docs/ideas.md` 状态；部署与飞书任务状态更新由用户指示

## 遗留（范围外，建议另立项）

- 回复预算优先级：业务文件被进度提示与自动 md 挤占而静默丢弃（2026-11-10 至 11-13 共 21 个文件），见设计 §七
