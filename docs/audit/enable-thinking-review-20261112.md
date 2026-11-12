# 内置子智能体思考模式（enable_thinking）排查清单

> 2026-11-12 排查。背景与根因见 [标书积分优化排查](../../docs/) 同日 trace 分析：deepseek 默认开启思考，reasoning token 占模板填充型任务 completion 输出 60%+ 且 reasoning_content 回传历史膨胀上下文，关思考实测省 35~40% 积分。
>
> 已实现子智能体级 `enable_thinking` 配置贯通（内置 SUBAGENT.md frontmatter + 自定义数字员工 DB 列 + 前端三态开关），仅 Runner 执行链路生效。

## ⚠️ 2026-11-12 实测证伪与全面回退（最新状态）

同日对 ai-bid-assistant 关思考后的真实任务 trace（tr_runner_78a7c0ec77f44aaa87bffd6f613e1a11:1，agent2）复测，**推翻"省 35~40% 积分"结论**：

1. **积分不降反平**：关思考轮 62.25 积分 vs 思考开启轮 56.57~89.62（轮次方差远大于开关收益）。量化根因：省掉的 reasoning ≈ 1.9 万 output token ≈ ¥0.17（output ¥9/M、input ¥3/M、cached ¥0.1/M），理论节省上限 ~15~20%；但关思考轮 LLM 调用 39 次 vs 开启轮 23 次、prompt 4.19M vs 2.3M（模型多读文件、rm 重渲染一轮），把节省吃掉还倒贴。原"35~40%"是 completion 侧占比口径，不是积分占比口径，偏乐观。
2. **质量退化**：招标文件第六章明确 12 个格式文件，思考开启轮逐项 1:1 生成 12 份 docx；关思考轮漏核对「明确拆分优先」约定，走 SKILL.md 默认五册拆分，交付 5 册（少一半）。
3. 机制本身验证正常：39/39 请求带 `thinking: {"type": "disabled"}`，reasoning_tokens=0。

**裁决（2026-11-12）**：4 个已关闭的内置子智能体全部回退为 provider 默认（思考开启），`enable_thinking: false` 从 SUBAGENT.md 移除；候选清单冻结，未经同任务固定 prompt 多轮平均实测（单轮方差 >> 开关收益）不得再关。`enable_thinking` 配置机制保留，供未来有实测数据支撑时使用。

## 判定口径

思考 token 收益大的特征：**单轮 completion 大、入参模板化、流程固定**（模板填充 / 表单映射 / 规则核对流水线）。对话类虽然 reasoning 占比也高，但关掉会伤意图理解质量，不是积分优化的合理标的。

trace 排查手法：对候选智能体从日志库各抽 1~2 条 trace，看大 completion 轮次紧邻 write 的入参量——输出远大于写入内容即 reasoning 占大头。

## 已关闭 → 已回退（2026-11-12 关闭，同日回退）

| agent_id | 当初理由 | 回退原因 |
|---|---|---|
| ai-bid-assistant | 标书生成模板填充，trace 实测根因 | 实测积分不降 + 分册规划退化丢文件（12 -> 5） |
| ai-contract-assistant | 模板匹配 + 字段定位回填 + 自检，纯模板填充链路 | 同一未验证假设；合同生成与标书同类，关思考有同类结构退化风险，且 contract-generator 真机验收未闭环 |
| auto-form-filler | 接口代填表单字段映射为主，流程固定 | 字段映射输出小，省积分绝对值趋零；映射错有代填业务风险 |
| contract-archive-review | 基础校验 + EAS 比对，规则核对流水线 | 规则核对输出小，节省趋零；漏判/误判影响归档结论 |

## 候选（已冻结——见上方 2026-11-12 实测证伪，单轮实测不能作为关闭依据）

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
