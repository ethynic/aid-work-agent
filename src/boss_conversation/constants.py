"""boss.chat_reply.v1 场景常量（B1.3 骨架，设计 §5.1 冻结值）。"""

SCENARIO_KEY = "boss.chat_reply.v1"

# ----- 执行链（设计 §1 决策表/§7）-----
PROVIDER_KEY = "boss-recruiting"
OPERATION_MESSAGE_SEND = "boss_send_to_v2"

# 发送能力（设备 manifest 上报后按场景校验；与发送操作同名，设计 §6.1 分配前置防线）
REQUIRED_SEND_CAPABILITY = "boss_send_to_v2"

# 决策正文冻结上限（设计 §10 开放问题 2：初值 500）
REPLY_TEXT_MAX_CHARS = 500

# 回执策略（设计 §5.1/§7.3 冻结）：submission 双证据命名空间；verified 证据由
# 发送 verifier 产出（boss-send-verifier:<request_id>:<n>）
RECEIPT_POLICY = {
    "mode": "submission",
    "context": "boss_reply",
    "submission_evidence_namespace": "boss-submission",
    "verified_evidence_namespace": "boss-send-verifier",
}

# payload_ref 场景不透明段前缀：boss-reply:<decision_id>（da: 前缀由 build_payload_ref 补齐）
PAYLOAD_REF_PREFIX = "boss-reply"

# 执行道（local_tool_invocations.execution_lane；仅服务端设置）。
# 与微信同 lane 常量来源对齐（weixin_conversation/constants.py 同名常量）：
# 会话类场景共用通用层唯一执行道 "session_task"。
EXECUTION_LANE_SESSION_TASK = "session_task"
