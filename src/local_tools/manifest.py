"""本地代理轻量清单；不得从此模块导入 repository/service。"""

LOCAL_PROXY_TOOL_NAMES = frozenset({
    "boss_filter", "boss_clear_filter", "boss_filter_options", "boss_goto",
    "boss_greet", "boss_accept_resume", "boss_reject_current",
    "boss_interview_demo", "boss_list_jobs", "boss_select_job",
    "boss_jobs_list", "boss_resume_detail", "boss_resume_batch",
    # 详情页打招呼三件套（筛选主路径，2026-09-28，plan-boss-detail-greet）
    "boss_open_detail", "boss_greet_detail", "boss_close_detail",
    "boss_send_to", "boss_send_current", "boss_interview_notify",
    "boss_read_chat", "boss_open_chat",
    "boss_overlay_inspect", "boss_overlay_dismiss",
    # 企业微信 Provider（M11c，2026-09-29）：6 工具与 runtime wecom manifest /
    # catalog TRUSTED_PROVIDERS['wecom'] 逐字一致
    "wecom_probe", "wecom_message_send", "wecom_send_image",
    "wecom_send_file", "wecom_read_session", "wecom_unread_list",
})
