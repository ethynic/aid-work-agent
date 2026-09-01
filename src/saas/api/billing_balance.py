"""
租户余额查询与用量明细 API（#37 租户积分充值与计费）

路由：/api/saas/billing/*
- GET /balance    当前租户积分余额 + 日均消耗（动态 n 天窗口）+ 预估可用天数 + 是否待续费
- GET /usage      用量明细列表（按日聚合，含 credit_cost）
- GET /recharges  本租户充值记录列表（只读）

权限：tenant_admin / user / platform_admin（代管理需带 X-Tenant-Id）
"""

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, Query
from loguru import logger

from src.config.settings import settings
from src.saas.api.tenant_auth import require_admin, sanitize_error_info
from src.saas.db.channel_config_db import ChannelConfigDB
from src.saas.db.tenant_db import TenantDB
from src.saas.services.renewal import compute_renewal_status
from src.db.models import TenantRechargesDB
from src.db.database import get_db_connection


router = APIRouter(prefix="/api/saas/billing", tags=["SaaS 余额与用量"])


# ============== API 端点 ==============

@router.get("/balance")
async def get_balance(request: Request):
    """获取当前租户积分余额 + 预估可用天数"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        return {"success": False, "message": "未关联租户"}

    try:
        tenant = TenantDB.get_by_id(tenant_id)
        if not tenant:
            return {"success": False, "message": "租户不存在"}

        credit_balance = float(tenant.get("credit_balance") or 0)
        renewal = compute_renewal_status(tenant)

        return {
            "success": True,
            "balance": {
                "credit_balance": credit_balance,
                "daily_avg_cost": renewal["daily_avg_cost"],
                # 兼容旧字段名，值为动态 n 日均（开通 > 30 天取 30，否则取开通天数）
                "daily_avg_cost_7d": renewal["daily_avg_cost"],
                "estimated_days_left": renewal["estimated_days_left"],
                "renewal_pending": renewal["renewal_pending"],
            },
        }
    except Exception as e:
        logger.error(f"获取积分余额失败: {e}")
        return {"success": False, "message": "查询失败", "debug": sanitize_error_info(str(e))}


@router.get("/usage")
async def get_usage(
    request: Request,
    date_from: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    session_id: Optional[str] = Query(None, description="按会话筛选"),
    model: Optional[str] = Query(None, description="按模型筛选"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=200, description="每页记录数"),
):
    """用量明细（按日聚合，含 credit_cost）

    双表口径（P3 §4.3）：chat_records（智能体对话）+ client_usage_logs（客户端计费行，
    credit_cost>0）UNION 后按 DATE(created_at) 分组，返回每日消耗积分、会话数、消息数、
    客户端调用数（及 chat/client 分项消耗）。
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        return {"success": False, "message": "未关联租户"}

    try:
        # 双表口径（P3 客户端计费统一接入 §4.3）：智能体对话 chat_records + 客户端计费
        # client_usage_logs UNION 后按日聚合。此前只聚 chat_records，boss 工具/协会 LLM
        # 的客户端消耗不上页面（与日均消耗的双表口径自相矛盾）。credit_cost>0 排除遥测行。
        where_clauses: list = ["tenant_id = %s"]
        params: list = [tenant_id]
        if date_from:
            where_clauses.append("created_at >= %s")
            params.append(f"{date_from} 00:00:00")
        if date_to:
            where_clauses.append("created_at <= %s")
            params.append(f"{date_to} 23:59:59")
        if session_id:
            where_clauses.append("session_id = %s")
            params.append(session_id)
        if model:
            where_clauses.append("model = %s")
            params.append(model)
        chat_where_sql = " AND ".join(where_clauses)

        # 客户端台账侧等价过滤（model 列存命令名，boss_filter/boss_greet 等）
        client_where_clauses: list = ["tenant_id = %s", "credit_cost > 0"]
        client_params: list = [tenant_id]
        if date_from:
            client_where_clauses.append("created_at >= %s")
            client_params.append(f"{date_from} 00:00:00")
        if date_to:
            client_where_clauses.append("created_at <= %s")
            client_params.append(f"{date_to} 23:59:59")
        if session_id:
            client_where_clauses.append("session_id = %s")
            client_params.append(session_id)
        if model:
            client_where_clauses.append("model = %s")
            client_params.append(model)
        client_where_sql = " AND ".join(client_where_clauses)

        union_sql = f"""
            SELECT created_at, credit_cost, session_id, 'chat' AS usage_type
            FROM chat_records
            WHERE {chat_where_sql}
            UNION ALL
            SELECT created_at, credit_cost, session_id, 'client' AS usage_type
            FROM client_usage_logs
            WHERE {client_where_sql}
        """
        union_params = (*params, *client_params)

        offset = (page - 1) * page_size
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # 总数（按日聚合后的天数）
            cursor.execute(
                f"""
                SELECT COUNT(*) AS cnt FROM (
                    SELECT 1 FROM ({union_sql}) t GROUP BY DATE(created_at)
                ) AS grouped
                """,
                union_params,
            )
            total = int(cursor.fetchone()["cnt"] or 0)

            # 按日聚合：会话数/消息数只统计智能体对话，客户端调用单列
            cursor.execute(
                f"""
                SELECT
                    DATE(created_at) AS date,
                    COALESCE(SUM(credit_cost), 0) AS credit_cost,
                    COALESCE(SUM(credit_cost) FILTER (WHERE usage_type = 'chat'), 0) AS chat_credit_cost,
                    COALESCE(SUM(credit_cost) FILTER (WHERE usage_type = 'client'), 0) AS client_credit_cost,
                    COUNT(DISTINCT session_id) FILTER (WHERE usage_type = 'chat') AS session_count,
                    COUNT(*) FILTER (WHERE usage_type = 'chat') AS message_count,
                    COUNT(*) FILTER (WHERE usage_type = 'client') AS client_call_count
                FROM ({union_sql}) t
                GROUP BY DATE(created_at)
                ORDER BY DATE(created_at) DESC
                LIMIT %s OFFSET %s
                """,
                (*union_params, page_size, offset),
            )
            items = [
                {
                    "date": str(row.get("date")) if row.get("date") else None,
                    "credit_cost": float(row.get("credit_cost") or 0),
                    "chat_credit_cost": float(row.get("chat_credit_cost") or 0),
                    "client_credit_cost": float(row.get("client_credit_cost") or 0),
                    "session_count": int(row.get("session_count") or 0),
                    "message_count": int(row.get("message_count") or 0),
                    "client_call_count": int(row.get("client_call_count") or 0),
                }
                for row in cursor.fetchall()
            ]

            # 全量汇总（不受分页影响，与 items 列表使用相同筛选条件）
            cursor.execute(
                f"""
                SELECT
                    COALESCE(SUM(credit_cost), 0) AS total_credit_cost,
                    COUNT(DISTINCT session_id) FILTER (WHERE usage_type = 'chat') AS total_session_count,
                    COUNT(*) FILTER (WHERE usage_type = 'chat') AS total_message_count,
                    COUNT(*) FILTER (WHERE usage_type = 'client') AS total_client_call_count
                FROM ({union_sql}) t
                """,
                union_params,
            )
            summary_row = cursor.fetchone() or {}
            summary = {
                "total_credit_cost": float(summary_row.get("total_credit_cost") or 0),
                "total_session_count": int(summary_row.get("total_session_count") or 0),
                "total_message_count": int(summary_row.get("total_message_count") or 0),
                "total_client_call_count": int(summary_row.get("total_client_call_count") or 0),
            }

        return {
            "success": True,
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "summary": summary,
        }
    except Exception as e:
        logger.error(f"获取用量明细失败: {e}")
        return {"success": False, "message": "查询失败", "debug": sanitize_error_info(str(e))}


@router.get("/recharges")
async def list_my_recharges(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    """本租户充值记录列表（只读，无操作列）"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        return {"success": False, "message": "未关联租户"}

    try:
        result = TenantRechargesDB.list(
            tenant_id=tenant_id,
            page=page,
            page_size=page_size,
        )
        # 租户只读视图：不暴露 operator_id 等敏感字段
        for item in result["items"]:
            item.pop("operator_id", None)
            item.pop("payment_order_id", None)
        return {"success": True, **result}
    except Exception as e:
        logger.error(f"获取租户充值记录失败: {e}")
        return {"success": False, "message": "查询失败", "debug": sanitize_error_info(str(e))}


@router.get("/usage/daily-detail")
async def get_daily_usage_detail(
    request: Request,
    date: str = Query(..., description="查询日期 YYYY-MM-DD"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=200, description="每页记录数"),
):
    """查询某日用量明细，chat（智能体对话）+ client（客户端调用）双类型行
    （平台管理员 + 租户管理员可访问）

    返回字段：record_id、usage_type（chat/client）、session_id、session_title
    （JOIN chat_sessions，client 行为 stage 中文标签）、user_display（JOIN users）、
    source_type、user_message、assistant_message、credit_cost、created_at；
    client 行附 command/arguments（取自 detail），不返回 token/breakdown 字段

    权限：platform_admin + tenant_admin。
    - 平台管理员需带 X-Tenant-Id 代管理目标租户，可看到全部字段。
    - 租户管理员只能查自己租户的数据，且 prompt_tokens / cached_input_tokens / completion_tokens
      三个字段不返回（前端也隐藏这 3 列），仅 platform_admin 可见。
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)

    # 二次权限校验：仅平台管理员 + 租户管理员
    role = admin.get("role")
    if role not in ("platform_admin", "tenant_admin"):
        return {"success": False, "message": "无权限查看对话用量明细"}

    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        return {"success": False, "message": "未关联租户"}

    # 是否返回 token 三列（仅平台管理员）
    reveal_tokens = role == "platform_admin"

    # 日期格式校验（参数化 SQL 已防注入，这里防逻辑错误）
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return {"success": False, "message": "日期格式错误，应为 YYYY-MM-DD"}

    try:
        offset = (page - 1) * page_size
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 明细（LEFT JOIN users / chat_sessions / channel_sessions，容忍 user_id/session_id 缺失）
            # users 表 nickname 字段：渠道用户（wecom_kf 等）常无 phone、username 是系统生成 ID，
            # nickname 才是可读名；web 端用户通常有 username + phone。三种字段都取，后端拼好展示字符串
            # session_title：web 端会话在 chat_sessions，渠道会话在 channel_sessions（按规范分离），
            # 用 COALESCE 取非空标题，避免渠道会话标题显示空
            #
            # 双类型明细（P3 客户端计费统一接入 §4.3）：chat（智能体对话）+ client（客户端计费行，
            # credit_cost>0 排除遥测行）。client 行 source_type=stage 标签（boss_tool→BOSS 工具），
            # command/arguments 取自 detail，用户经 detail->>'user_id' 反查 users。两子查询列按位对齐，
            # record_id 加 'client-' 前缀防与 chat record_id 撞 key。
            merged_sql = """
                SELECT
                    cr.record_id,
                    cr.created_at,
                    cr.session_id,
                    COALESCE(cs.title, chs.title) AS session_title,
                    cr.user_id,
                    u.username,
                    u.phone,
                    u.nickname,
                    cr.source_type,
                    cr.user_message,
                    cr.assistant_message,
                    cr.prompt_tokens,
                    cr.cached_input_tokens,
                    cr.completion_tokens,
                    cr.credit_cost,
                    cr.model,
                    cr.usage_breakdown,
                    chs.channel_chat_id,
                    chs.channel_type,
                    'chat' AS usage_type,
                    NULL AS command,
                    NULL AS arguments
                FROM chat_records cr
                LEFT JOIN users u ON u.user_id = cr.user_id
                LEFT JOIN chat_sessions cs ON cs.session_id = cr.session_id
                LEFT JOIN channel_sessions chs ON chs.session_id = cr.session_id
                WHERE cr.tenant_id = %s AND DATE(cr.created_at) = %s
                UNION ALL
                SELECT
                    ('client-' || cl.id::TEXT) AS record_id,
                    cl.created_at,
                    cl.session_id,
                    NULL AS session_title,
                    NULLIF(cl.detail, '')::jsonb->>'user_id' AS user_id,
                    u2.username,
                    u2.phone,
                    u2.nickname,
                    CASE cl.stage
                        WHEN 'boss_tool' THEN 'BOSS 工具'
                        WHEN 'llm' THEN '客户端 LLM'
                        WHEN 'ocr' THEN 'OCR 识别'
                        ELSE cl.stage
                    END AS source_type,
                    NULL AS user_message,
                    NULL AS assistant_message,
                    NULL AS prompt_tokens,
                    NULL AS cached_input_tokens,
                    NULL AS completion_tokens,
                    cl.credit_cost,
                    cl.model,
                    NULL AS usage_breakdown,
                    NULL AS channel_chat_id,
                    NULL AS channel_type,
                    'client' AS usage_type,
                    cl.model AS command,
                    NULLIF(cl.detail, '')::jsonb->'arguments' AS arguments
                FROM client_usage_logs cl
                LEFT JOIN users u2 ON u2.user_id = NULLIF(cl.detail, '')::jsonb->>'user_id' AND u2.tenant_id = cl.tenant_id
                WHERE cl.tenant_id = %s AND DATE(cl.created_at) = %s AND cl.credit_cost > 0
            """
            merged_params = (tenant_id, date, tenant_id, date)

            # 总数（chat + client 两类行）
            cursor.execute(
                f"SELECT COUNT(*) AS cnt FROM ({merged_sql}) merged",
                merged_params,
            )
            total = int(cursor.fetchone()["cnt"] or 0)

            cursor.execute(
                f"SELECT * FROM ({merged_sql}) merged ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (*merged_params, page_size, offset),
            )
            rows = cursor.fetchall()
            items = []
            for r in rows:
                # 用户展示拼接：nickname 优先（渠道用户 username 可读性差，phone 可能为空），
                # 拼接策略：以 nickname 为主名，括号内展示 username 和 phone（如有）
                # 示例：
                #   三者都有 -> "孙晨(user_abc, 13916323347)"
                #   nickname + phone -> "孙晨(13916323347)"
                #   username + phone -> "user_abc(13916323347)"（web 端常见）
                #   仅 nickname -> "孙晨"
                #   仅 phone -> "13916323347"
                #   全空 -> user_id 兜底，再不行显示 "-"
                nickname = (r.get("nickname") or "").strip()
                username = (r.get("username") or "").strip()
                phone = (r.get("phone") or "").strip()
                extras = [x for x in [username, phone] if x]
                main_name = nickname or username or phone or r.get("user_id") or "-"
                if extras and (nickname or username):
                    # 有主名（nickname 或 username）才展示括号补充信息
                    # 避免仅有 phone 时出现 "13916323347(13916323347)" 的冗余
                    extras_excluding_main = [x for x in extras if x != main_name]
                    if extras_excluding_main:
                        user_display = f"{main_name}({', '.join(extras_excluding_main)})"
                    else:
                        user_display = main_name
                else:
                    user_display = main_name
                channel_chat_id = r.get("channel_chat_id")
                channel_type = r.get("channel_type")
                usage_type = r.get("usage_type") or "chat"
                items.append({
                    "record_id": r.get("record_id"),
                    "usage_type": usage_type,
                    "session_id": r.get("session_id"),
                    "session_title": r.get("session_title") or "-",
                    "user_display": user_display,
                    "source_type": r.get("source_type") or "-",
                    "user_message": r.get("user_message") or "",
                    "assistant_message": r.get("assistant_message") or "",
                    "credit_cost": float(r.get("credit_cost") or 0),
                    "channel_label": _resolve_channel_label(tenant_id, channel_type, channel_chat_id),
                    "channel_chat_id": channel_chat_id,
                    "channel_type": channel_type,
                    "created_at": r.get("created_at").strftime("%Y-%m-%d %H:%M:%S")
                        if r.get("created_at") else None,
                })
                # 客户端行附加命令/参数（arguments 从 jsonb 来已是 dict/list，序列化为字符串供前端直接展示）
                if usage_type == "client":
                    args_val = r.get("arguments")
                    if isinstance(args_val, (dict, list)):
                        args_val = json.dumps(args_val, ensure_ascii=False)
                    items[-1]["command"] = r.get("command")
                    items[-1]["arguments"] = args_val
                # token 三列 + usage_breakdown 7 分项仅平台管理员可见，租户管理员不返回；
                # client 行无 token/breakdown（命令计费与 token 无关），不挂这些键
                if reveal_tokens and usage_type == "chat":
                    items[-1]["prompt_tokens"] = int(r.get("prompt_tokens") or 0)
                    items[-1]["cached_input_tokens"] = int(r.get("cached_input_tokens") or 0)
                    items[-1]["completion_tokens"] = int(r.get("completion_tokens") or 0)
                    items[-1]["breakdown_items"] = _parse_breakdown_items(r.get("usage_breakdown"))
                    # 文本模型：优先 usage_breakdown.chat.model（summary_llm 为 mid_term 摘要等场景），
                    # 老数据 breakdown 无 model 时回退到 chat_records.model 列
                    _bd = r.get("usage_breakdown") or {}
                    items[-1]["model"] = (
                        (_bd.get("chat") or _bd.get("summary_llm") or {}).get("model")
                        or r.get("model")
                    )

        return {
            "success": True,
            "date": date,
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    except Exception as e:
        logger.error(f"获取每日用量明细失败: {e}")
        return {"success": False, "message": "查询失败", "debug": sanitize_error_info(str(e))}


# ============== 辅助函数 ==============

def _resolve_channel_label(tenant_id: str, channel_type: str, channel_chat_id: str) -> Optional[str]:
    """渠道会话展示名解析（通用，不硬编码 wecom_kf）。

    channel_chat_id 为空返回 None（前端显示 "-"）；wecom_kf 渠道反查客服账号名称，
    未匹配回退显示 channel_chat_id 原始值；其它渠道暂直接返回 channel_chat_id，
    将来各渠道需展示群名/会话名时在此函数扩展分支。
    """
    if not channel_chat_id:
        return None
    if channel_type == "wecom_kf":
        try:
            for cfg in ChannelConfigDB.list_by_tenant(tenant_id, "wecom_kf"):
                for kf in cfg.get("config", {}).get("kf_account", []):
                    if kf.get("open_kfid") == channel_chat_id:
                        return kf.get("name") or channel_chat_id
        except Exception as e:
            logger.warning(
                f"[billing] 客服账号反查失败: tenant={tenant_id}, "
                f"open_kfid={channel_chat_id}, error={e}"
            )
    return channel_chat_id


def _parse_breakdown_items(breakdown) -> list:
    """把 chat_records.usage_breakdown JSON 解析为 7 分项对账结构（仅平台管理员明细弹框使用）。

    返回固定 7 项列表，每项：
    {
        "key": 分项标识（non_cached_input / cached_input / cache_creation_input / output / video / asr / embedding）,
        "label": 分项中文名,
        "qty": 数量（token / 秒 / 次），
        "unit_price": 单价原始值（每百万 token 或 每秒/每次），
        "usage_factor": 用量系数,
        "credit": 积分消耗,
        "is_per_million": 单价是否为每百万类（chat 分项 / embedding，前端需 ÷1M 换算）,
    }
    无对应分项或字段缺失时为 None，前端按 "-" 兜底（旧记录无 unit_prices/usage_factor/credits）。
    """
    breakdown = breakdown or {}
    chat = breakdown.get("chat") or breakdown.get("summary_llm") or {}
    video = breakdown.get("video") or {}
    asr = breakdown.get("asr") or {}
    emb = breakdown.get("embedding") or {}

    unit_prices = chat.get("unit_prices") or {}
    credits = chat.get("credits") or {}
    chat_factor = chat.get("usage_factor")

    # 老数据（2026-08-14 前）chat 分项无 non_cached_input_tokens 单独字段，
    # 用 prompt_tokens - cached_input_tokens 兜底计算（cached 缺失视为 0 -> 即全量 prompt）
    non_cached_qty = chat.get("non_cached_input_tokens")
    if non_cached_qty is None and chat.get("prompt_tokens") is not None:
        non_cached_qty = max(
            int(chat.get("prompt_tokens") or 0) - int(chat.get("cached_input_tokens") or 0), 0
        )

    return [
        {
            "key": "non_cached_input",
            "label": "未命中缓存输入",
            "qty": non_cached_qty,
            "unit_price": unit_prices.get("input_per_m"),
            "usage_factor": chat_factor,
            "credit": credits.get("non_cached_input"),
            "is_per_million": True,
        },
        {
            "key": "cached_input",
            "label": "命中缓存输入",
            "qty": chat.get("cached_input_tokens"),
            "unit_price": unit_prices.get("cached_input_per_m"),
            "usage_factor": chat_factor,
            "credit": credits.get("cached_input"),
            "is_per_million": True,
        },
        {
            "key": "cache_creation_input",
            "label": "缓存创建输入",
            "qty": chat.get("cache_creation_input_tokens"),
            "unit_price": unit_prices.get("cache_creation_input_per_m"),
            "usage_factor": chat_factor,
            "credit": credits.get("cache_creation_input"),
            "is_per_million": True,
        },
        {
            "key": "output",
            "label": "输出",
            "qty": chat.get("completion_tokens"),
            "unit_price": unit_prices.get("output_per_m"),
            "usage_factor": chat_factor,
            "credit": credits.get("output"),
            "is_per_million": True,
        },
        {
            "key": "video",
            "label": "视频模型",
            "qty": video.get("duration_seconds"),
            "unit_price": video.get("unit_price_per_second"),
            "usage_factor": video.get("usage_factor"),
            "credit": video.get("credit"),
            "is_per_million": False,
        },
        {
            "key": "asr",
            "label": "ASR",
            "qty": asr.get("calls"),
            "unit_price": asr.get("unit_price_per_call"),
            "usage_factor": asr.get("usage_factor"),
            "credit": asr.get("credit"),
            "is_per_million": False,
        },
        {
            "key": "embedding",
            "label": "向量模型",
            "qty": emb.get("tokens"),
            "unit_price": emb.get("unit_price_per_m"),
            "usage_factor": emb.get("usage_factor"),
            "credit": emb.get("credit"),
            "is_per_million": True,
        },
    ]
