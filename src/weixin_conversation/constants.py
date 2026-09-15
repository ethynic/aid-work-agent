"""微信会话场景常量。"""

SCENARIO_KEY = "weixin.conversation.v1"

CONVERSATION_TYPES = ("direct", "group")

# 绑定验证状态（设计 §13.3：pending 起步，verified 只能由服务端接纳受信 Provider
# 真机证据写入；不得用群名/假时间戳/fake 证据填成 verified）
BINDING_PENDING = "pending"
BINDING_VERIFIED = "verified"
BINDING_INVALID = "invalid"
BINDING_EXPIRED = "expired"
BINDING_STATUSES = (BINDING_PENDING, BINDING_VERIFIED, BINDING_INVALID, BINDING_EXPIRED)

# ----- C3 执行链（设计 §10）-----
PROVIDER_KEY = "weixin"
OPERATION_MESSAGE_SEND = "weixin_message_send_v2"

# 决策正文冻结上限（与 spec opening_text max_length 对齐的保守微信文字上限）
REPLY_TEXT_MAX_CHARS = 500

# payload_ref 场景不透明段：session-reply:<decision_id>（da: 前缀由 build_payload_ref 补齐）
PAYLOAD_OPAQUE_PREFIX = "session-reply"

# 执行道（local_tool_invocations.execution_lane；仅服务端设置）
EXECUTION_LANE_SESSION_TASK = "session_task"

# 模型动作枚举（设计 §9 结构化输出）
ACTIONS = ("reply", "wait", "handoff", "done")
WAIT_FOR_VALUES = ("peer", "work_window")
