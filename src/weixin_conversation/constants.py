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
