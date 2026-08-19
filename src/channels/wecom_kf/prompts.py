"""微信客服渠道固定话术常量。

历史说明：本文件曾定义渠道约束提示词 WECOM_KF_CHANNEL_PROMPT（Layer 1 产出节俭
约束），2026-08 废除——实测它会干扰 agent 工具失败后的恢复路径（fill_template
报错后模型倾向绕过模板直接 markdown 一次性生成），且 5 条回复上限的风险改由
发送侧长图化承接（adapter 将含表格/图片或超长的回复整段渲染为 1 张长图）。

详见：docs/channel/wecom_kf/reply_quota_control_plan.md
"""

# ============ 客服账号到期/积分拦截固定话术 ============
# 由 channel_routes 消息入口拦截使用：到期（expire_at 当天结束）或积分用尽
# （credit_limit>0 且累计 >= 上限）时不调智能体、不计费，仅回固定话术。
MSG_EXPIRED = "本服务已到期，如需继续使用请联系工作人员开通。"
MSG_CREDIT_EXHAUSTED = "本服务积分已用完，如需继续使用请联系工作人员充值。"

