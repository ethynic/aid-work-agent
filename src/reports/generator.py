"""
日报生成主流程

负责：
1. 调用 aggregator 聚合数据
2. 调用 summarizer 生成 LLM 摘要
3. 调用 billing 计算积分
4. 写 work_daily_reports 表
5. 写 chat_records 表（source_type=report_*）+ 同事务扣积分
"""

import enum
import time
from datetime import date
from typing import Any, Dict, Optional

from loguru import logger

from src.db.models import ChatRecordDB
from src.reports.aggregator import aggregate_personal, aggregate_team
from src.reports.db import WorkDailyReportDB
from src.reports.summarizer import (
    get_report_model,
    summarize_personal,
    summarize_team,
)
from src.saas.models.enums import ChatRecordSourceType
from src.services.billing import calculate_credit_cost


class ReportScope(str, enum.Enum):
    PERSONAL = "personal"
    TEAM = "team"


class ReportType(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class ReportGenerator:
    """报告生成器

    用法：
        gen = ReportGenerator()
        report = await gen.generate_personal(tenant_id, user_id, date(2026,7,22), "daily")
    """

    async def generate_personal(
        self,
        tenant_id: str,
        user_id: str,
        user_name: str,
        department: Optional[str],
        report_date: date,
        report_type: str = "daily",
        is_regenerate: bool = False,
    ) -> Dict[str, Any]:
        """生成个人报告

        Args:
            tenant_id: 租户 ID
            user_id: 用户 ID
            user_name: 用户姓名
            department: 部门
            report_date: 报告日期
            report_type: daily / weekly / monthly
            is_regenerate: 是否为重新生成（regenerated_count 自增）

        Returns:
            报告 dict（含 report_id / summary_text / metrics 等）
        """
        start_ts = time.perf_counter()
        report_model = get_report_model()
        type_label = {"daily": "日报", "weekly": "周报", "monthly": "月报"}.get(report_type, "报告")

        # 1. 聚合数据
        agg = aggregate_personal(tenant_id, user_id, report_date, report_type)
        records = agg["records"]
        if not records:
            logger.info(
                f"个人{type_label}无数据: tenant={tenant_id}, user={user_id}, date={report_date}"
            )
            # 仍然落库一条空报告，避免下次重复查询
            empty_report = self._build_empty_report(agg, report_type)
            upsert_result = WorkDailyReportDB.upsert(
                tenant_id=tenant_id,
                scope=ReportScope.PERSONAL.value,
                report_type=report_type,
                report_date=report_date,
                target_user_id=user_id,
                metrics=empty_report["metrics"],
                summary_text=empty_report["summary_text"],
                model=report_model,
                token_cost=0,
                credit_cost=0.0,
                is_regenerate=is_regenerate,
            )
            return {
                "report_id": upsert_result["report_id"],
                "scope": ReportScope.PERSONAL.value,
                "report_type": report_type,
                "report_date": report_date.isoformat(),
                "target_user_id": user_id,
                **empty_report,
                "model": report_model,
                "credit_cost": 0.0,
            }

        # 2. 调 LLM 生成摘要
        summary_text, usage = await summarize_personal(
            user_name=user_name,
            department=department,
            report_date_str=report_date.isoformat(),
            records=records,
            report_type=report_type,
        )
        prompt_tokens = usage["prompt_tokens"]
        completion_tokens = usage["completion_tokens"]

        # 3. 计算积分
        credit_cost = calculate_credit_cost(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=report_model,
        )

        # 4. 落库 work_daily_reports
        metrics = self._build_personal_metrics(agg)
        upsert_result = WorkDailyReportDB.upsert(
            tenant_id=tenant_id,
            scope=ReportScope.PERSONAL.value,
            report_type=report_type,
            report_date=report_date,
            target_user_id=user_id,
            metrics=metrics,
            summary_text=summary_text,
            model=report_model,
            token_cost=prompt_tokens + completion_tokens,
            credit_cost=credit_cost,
            is_regenerate=is_regenerate,
        )

        # 5. 写 chat_records 计费链路（source_type=report_personal[_weekly|_monthly]）
        duration_ms = int((time.perf_counter() - start_ts) * 1000)
        source_type = self._personal_source_type(report_type)
        self._write_billing_chat_record(
            tenant_id=tenant_id,
            user_id=user_id,
            scope=ReportScope.PERSONAL.value,
            report_type=report_type,
            report_date=report_date,
            user_message=f"[个人{type_label}生成] 输入 {len(records)} 条对话记录",
            assistant_message=summary_text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=report_model,
            duration_ms=duration_ms,
            source_type=source_type,
            credit_cost=credit_cost,
        )

        logger.info(
            f"个人{type_label}生成完成: tenant={tenant_id}, user={user_id}, "
            f"date={report_date}, model={report_model}, credit_cost={credit_cost}, "
            f"duration={duration_ms}ms"
        )

        return {
            "report_id": upsert_result["report_id"],
            "scope": ReportScope.PERSONAL.value,
            "report_type": report_type,
            "report_date": report_date.isoformat(),
            "target_user_id": user_id,
            "metrics": metrics,
            "summary_text": summary_text,
            "model": report_model,
            "token_cost": prompt_tokens + completion_tokens,
            "credit_cost": credit_cost,
        }

    async def generate_team(
        self,
        tenant_id: str,
        tenant_name: str,
        total_users: int,
        report_date: date,
        report_type: str = "daily",
        personal_summaries: Optional[list] = None,
        is_regenerate: bool = False,
    ) -> Dict[str, Any]:
        """生成团队报告

        Args:
            tenant_id: 租户 ID
            tenant_name: 租户名称
            total_users: 团队总人数
            report_date: 报告日期
            report_type: daily / weekly / monthly
            personal_summaries: 成员个人报告摘要列表（已脱敏）。None 时只做统计不调 LLM
            is_regenerate: 是否为重新生成

        Returns:
            报告 dict
        """
        start_ts = time.perf_counter()
        report_model = get_report_model()
        type_label = {"daily": "日报", "weekly": "周报", "monthly": "月报"}.get(report_type, "报告")

        # 1. 聚合数据
        agg = aggregate_team(tenant_id, report_date, report_type)
        active_users = agg["active_user_count"]

        # 2. 调 LLM 生成摘要（仅有活跃成员时才调）
        if personal_summaries and active_users > 0:
            summary_text, usage = await summarize_team(
                tenant_name=tenant_name,
                report_date_str=report_date.isoformat(),
                total_users=total_users,
                active_users=active_users,
                personal_summaries=personal_summaries,
                report_type=report_type,
            )
            prompt_tokens = usage["prompt_tokens"]
            completion_tokens = usage["completion_tokens"]
            credit_cost = calculate_credit_cost(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                model=report_model,
            )
        else:
            summary_text = f"本期团队无活跃成员，无{type_label}摘要。"
            prompt_tokens = 0
            completion_tokens = 0
            credit_cost = 0.0

        # 3. 落库 work_daily_reports
        metrics = self._build_team_metrics(agg, total_users)
        upsert_result = WorkDailyReportDB.upsert(
            tenant_id=tenant_id,
            scope=ReportScope.TEAM.value,
            report_type=report_type,
            report_date=report_date,
            target_user_id=None,
            metrics=metrics,
            summary_text=summary_text,
            model=report_model,
            token_cost=prompt_tokens + completion_tokens,
            credit_cost=credit_cost,
            is_regenerate=is_regenerate,
        )

        # 4. 写 chat_records 计费链路
        duration_ms = int((time.perf_counter() - start_ts) * 1000)
        source_type = self._team_source_type(report_type)
        # 团队报告 user_id 留空（不属于某个用户）
        self._write_billing_chat_record(
            tenant_id=tenant_id,
            user_id=None,
            scope=ReportScope.TEAM.value,
            report_type=report_type,
            report_date=report_date,
            user_message=f"[团队{type_label}生成] 输入 {active_users} 个成员摘要",
            assistant_message=summary_text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=report_model,
            duration_ms=duration_ms,
            source_type=source_type,
            credit_cost=credit_cost,
        )

        logger.info(
            f"团队{type_label}生成完成: tenant={tenant_id}, date={report_date}, "
            f"active_users={active_users}, model={report_model}, "
            f"credit_cost={credit_cost}, duration={duration_ms}ms"
        )

        return {
            "report_id": upsert_result["report_id"],
            "scope": ReportScope.TEAM.value,
            "report_type": report_type,
            "report_date": report_date.isoformat(),
            "target_user_id": None,
            "metrics": metrics,
            "summary_text": summary_text,
            "model": report_model,
            "token_cost": prompt_tokens + completion_tokens,
            "credit_cost": credit_cost,
        }

    # ============== 内部工具 ==============

    def _personal_source_type(self, report_type: str) -> str:
        """根据 report_type 返回个人报告的 source_type"""
        if report_type == "weekly":
            return ChatRecordSourceType.REPORT_PERSONAL_WEEKLY.value
        if report_type == "monthly":
            return ChatRecordSourceType.REPORT_PERSONAL_MONTHLY.value
        return ChatRecordSourceType.REPORT_PERSONAL.value

    def _team_source_type(self, report_type: str) -> str:
        """根据 report_type 返回团队报告的 source_type"""
        if report_type == "weekly":
            return ChatRecordSourceType.REPORT_TEAM_WEEKLY.value
        if report_type == "monthly":
            return ChatRecordSourceType.REPORT_TEAM_MONTHLY.value
        return ChatRecordSourceType.REPORT_TEAM.value

    def _build_personal_metrics(self, agg: Dict[str, Any]) -> Dict[str, Any]:
        """构造个人报告 metrics"""
        return {
            "dialog_count": agg["dialog_count"],
            "credit_cost": agg["credit_cost"],
            "saved_minutes": agg["saved_minutes"],
            "subagent_distribution": agg["subagent_distribution"],
            "tool_distribution": agg["tool_distribution"],
            "source_distribution": agg["source_distribution"],
            "time_range": agg["time_range"],
        }

    def _build_team_metrics(self, agg: Dict[str, Any], total_users: int) -> Dict[str, Any]:
        """构造团队报告 metrics"""
        active = agg["active_user_count"]
        return {
            "total_users": total_users,
            "active_user_count": active,
            "active_rate": round(active / total_users, 4) if total_users > 0 else 0.0,
            "total_dialog_count": agg["total_dialog_count"],
            "total_credit_cost": agg["total_credit_cost"],
            "total_saved_minutes": agg["total_saved_minutes"],
            "source_distribution": agg["source_distribution"],
            "user_stats": agg["user_stats"],
            "time_range": agg["time_range"],
        }

    def _build_empty_report(self, agg: Dict[str, Any], report_type: str) -> Dict[str, Any]:
        """无数据时的空报告"""
        type_label = {"daily": "今天", "weekly": "本周", "monthly": "本月"}.get(report_type, "本期")
        return {
            "metrics": self._build_personal_metrics(agg),
            "summary_text": f"{type_label}暂无对话记录，无法生成工作报告。",
        }

    def _write_billing_chat_record(
        self,
        tenant_id: str,
        user_id: Optional[str],
        scope: str,
        report_type: str,
        report_date: date,
        user_message: str,
        assistant_message: str,
        prompt_tokens: int,
        completion_tokens: int,
        model: str,
        duration_ms: int,
        source_type: str,
        credit_cost: float,
    ) -> None:
        """写 chat_records 记录报告类 LLM 调用，复用现有计费链路

        session_id 用专用命名空间 report:{tenant}:{user}:{date}:{type}，
        与真实对话会话隔离。
        """
        session_id = f"report:{tenant_id}:{user_id or 'team'}:{report_date.isoformat()}:{report_type}"
        try:
            ChatRecordDB.create(
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                user_message=user_message,
                assistant_message=assistant_message,
                total_token_count=prompt_tokens + completion_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_input_tokens=0,
                model=model,
                provider=None,
                execution_details={
                    "report_scope": scope,
                    "report_type": report_type,
                    "report_date": report_date.isoformat(),
                    "input_dialog_count": 0,  # 可后续扩展
                },
                agent_iterations=1,
                subagent_calls=None,
                status="completed",
                error_message=None,
                duration_ms=duration_ms,
                source_type=source_type,
                credit_cost=credit_cost,
            )
        except Exception as e:
            # 写 chat_records 失败不应该让报告生成失败
            # 报告已落库 work_daily_reports，用户能看到；只是用量页缺一条记录
            logger.error(
                f"报告 chat_records 写入失败（不影响报告本身）: scope={scope}, "
                f"type={report_type}, date={report_date}, error={e}",
                exc_info=True,
            )
