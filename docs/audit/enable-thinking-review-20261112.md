# 内置子智能体思考模式（enable_thinking）排查清单

> 2026-11-12 排查。背景与根因见 [标书积分优化排查](../../docs/) 同日 trace 分析：deepseek 默认开启思考，reasoning token 占模板填充型任务 completion 输出 60%+ 且 reasoning_content 回传历史膨胀上下文，关思考实测省 35~40% 积分。
>
> 已实现子智能体级 `enable_thinking` 配置贯通（内置 SUBAGENT.md frontmatter + 自定义数字员工 DB 列 + 前端三态开关），仅 Runner 执行链路生效。

## 判定口径

思考 token 收益大的特征：**单轮 completion 大、入参模板化、流程固定**（模板填充 / 表单映射 / 规则核对流水线）。对话类虽然 reasoning 占比也高，但关掉会伤意图理解质量，不是积分优化的合理标的。

trace 排查手法：对候选智能体从日志库各抽 1~2 条 trace，看大 completion 轮次紧邻 write 的入参量——输出远大于写入内容即 reasoning 占大头。

## 已关闭（2026-11-12 落地）

| agent_id | 理由 |
|---|---|
| ai-bid-assistant | 标书生成模板填充，trace 实测根因 |
| ai-contract-assistant | 模板匹配 + 字段定位回填 + 自检，纯模板填充链路 |
| auto-form-filler | 接口代填表单字段映射为主，流程固定 |
| contract-archive-review | 基础校验（合同类型/审批单/骑缝章/盖章）+ EAS 比对，规则核对流水线 |

## 候选（trace 实测 reasoning 占比后再定）

| agent_id | 理由 | 谨慎点 |
|---|---|---|
| ai-quotation | spec 转 Excel 清单模板填充，轮次多收益大 | 套价/替换留痕/复核需查表比对 |
| building-supply-chain | 清单生成为主（输出大） | spec book 空间扫描需理解 |
| order-processing | 订单 CRUD + ERP 对接操作型为主 | 审批沟通有对话 |
| weixin-marketing | 草稿准备/发布/投递运维流程固定 | 草稿文案环节占比小 |
| recruiting-operator | Chrome RPA 按步骤执行为主 | 简历筛选需判断 |
| supplier-qualification-review | 两份 Word 为模板产出 | 资质真实性审查结论影响业务 |

**候选实测时须一并统计自定义数字员工（subagent_definitions 链路）的思考情况**：自定义数字员工经「自定义数字员工」页面配置，部分实例可能复用内置智能体能力；统计范围 = 日志库中租户实例执行（tenant_agent_instances 关联 agent_id）+ 自定义定义（from_db=true）的 trace，区分「已显式关闭 / 显式开启 / 默认开启」三态分别汇总 reasoning 占比与积分构成，作为后续对自定义定义批量建议关思考的数据依据。

## 不关闭（对话交互 / 研判 / 创意）

- 对话类：after-sales、pre-sales、complaint-handling、travel-consultant
- 研判类：competitor-research、project-progress-assistant、procurement-comparison（比价结论决策性）
- 创意类：ai-lead-gen（开发信文案）、interior-render（生图提示词）、video-agent、social-media-operations
