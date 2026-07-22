"""
工作日报模块

提供个人日报 / 团队日报 / 周报 / 月报的生成、查询、推送能力。

核心组件：
- aggregator.py：从 chat_records 聚合输入数据
- summarizer.py：LLM 摘要生成（走 deepseek-v4-flash 小模型）
- time_saver.py：节省时间估算
- generator.py：日报生成主流程（含写 chat_records 计费链路）
- db.py：work_daily_reports / work_report_preferences 表 CRUD

详见 docs/research/ai-agent-experience-daily-report-research.md
"""

from src.reports.generator import ReportGenerator, ReportScope, ReportType
from src.reports.db import (
    WorkDailyReportDB,
    WorkReportPreferenceDB,
    generate_report_id,
)

__all__ = [
    "ReportGenerator",
    "ReportScope",
    "ReportType",
    "WorkDailyReportDB",
    "WorkReportPreferenceDB",
    "generate_report_id",
]
