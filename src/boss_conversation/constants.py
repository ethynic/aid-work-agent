"""boss.chat_reply.v1 场景常量（B1.3 骨架 + B2 频控/渲染/完成语义，设计 §5 冻结值）。"""

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

# ----- 频控（设计 §1 频控行/§5.5.3 冻结口径）-----
RATE_INTERVAL_SECONDS = 60          # 间隔 ≥60s（含 reserved 行）
RATE_WINDOW_SECONDS = 600           # 10 分钟窗
RATE_WINDOW_MAX = 3                 # 10min 窗内 ≤3 条
RATE_DAILY_CAP = 10                 # 日上限（Asia/Shanghai 当日 reserved+settled ≥10）
BUSINESS_TIMEZONE = "Asia/Shanghai"

# 退避附加按 effective_count（设计 §5.5.3 冻结）：≤1→+0；2→+2min；3→+5min；
# ≥4→terminal human_required(repeated_rate_trigger)
RATE_BACKOFF_SECONDS_BY_COUNT = {1: 0, 2: 120, 3: 300}

# ----- 受限决策（设计 §5.4 冻结词汇）-----
DECISION_ACTIONS = ("select_script", "fill_slots", "handoff")
REASON_CODES = (
    "low_confidence", "sensitive_topic", "missing_evidence",
    "ambiguous", "policy_conflict", "rate_limited",
)
# 占位符语法：{slot_name}，名称 ^[a-z][a-z0-9_]{0,63}$（§5.3 白名单语法）
PLACEHOLDER_RE = r"\{([a-z][a-z0-9_]{0,63})\}"

# spec 结构上限（设计 §5.4 冻结）
SCRIPTS_MAX_COUNT = 10
SCRIPTS_TOTAL_TEMPLATE_MAX_CHARS = 10000
SCRIPT_TEMPLATE_MAX_CHARS = 2000

# resume_field 槽位默认白名单（key_info.* 子集；设计 §10 开放问题 2 初值）。
# config.yaml boss_conversation.resume_field_whitelist 热读可覆盖；键为 key_info
# 内字段名（recruiting_match_service key_info 固定 schema 子集）。
DEFAULT_RESUME_FIELD_WHITELIST = (
    "education",
    "current_company",
    "years_of_experience",
    "core_skills",
    "highlights",
    "salary_expectation",
)

# 敏感词默认表（设计 §10 开放问题 2 初值；config.yaml
# boss_conversation.sensitive_words 热读可覆盖）。命中 → 优先 handoff(sensitive_topic)。
DEFAULT_SENSITIVE_WORDS = (
    "薪资", "工资", "待遇", "offer", "录用", "到面", "违约", "赔偿",
    "身份证", "银行卡", "贷款", "博彩",
)

# 话术模板规范化：渲染/哈希统一按 NFC + \r\n→\n 规范化字节（content_hash 口径，
# 设计 §5.3 content_hash=模板规范化字节 sha256）
