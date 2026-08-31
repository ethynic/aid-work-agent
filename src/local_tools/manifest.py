"""本地代理轻量清单；不得从此模块导入 repository/service。"""

LOCAL_PROXY_TOOL_NAMES = frozenset({
    "boss_filter", "boss_clear_filter", "boss_filter_options", "boss_goto",
    "boss_greet", "boss_accept_resume", "boss_reject_current",
    "boss_interview_demo", "boss_list_jobs", "boss_select_job",
    "boss_jobs_list", "boss_resume_detail", "boss_resume_batch",
    "boss_send_to", "boss_send_current", "boss_interview_notify",
    "boss_read_chat", "boss_open_chat",
})
