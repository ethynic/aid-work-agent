"""招聘面试邀约企微通知服务（两点式：邀约前知会 + 邀约后通报，Phase 1）

设计：docs/design/recruiting/recruiting-interview-notify-design.md

职责分层：
- 本模块管「要不要发、发什么、留痕」：租户配置读写、消息模板、通知编排、日志补推
- 发送动作委托 src/services/wecom_bot.py（纯发送，便于测试替身）

两表（幂等建表，database.py 启动注册 + deploy/db_update.sql 登记）：
- bs_recruiting_notify_settings：租户通知配置（webhook_url Fernet 密文存储，
  API 层只出掩码 ***+末4位，沿 channel_config 范式）
- bs_recruiting_notify_logs：通知留痕（kind=pre|done / candidates JSONB / content /
  status=sent|failed / error），用于查询与手动补推

关键约定：
- boss_interview_demo 入参只有 remark（无候选人名/日期），名单只存在于 agent 对话
  上下文——因此事前知会与事后通报统一为 push_interview_notify 的两种 kind，
  由 SUBAGENT 链路规定调用（pre 在邀约前、done 在邀约后）
- 通知是流程增强不是闸门：未启用静默跳过，发送失败只写 failed 日志不外抛
"""
import asyncio
from typing import Any, Dict, List, Optional

import psycopg2.extras
from loguru import logger

from src.channels.wecom_personal_rpa.secret_crypto import decrypt_secret, encrypt_secret
from src.db.database import get_db_connection
from src.services import wecom_bot

NOTIFY_KINDS = ("pre", "done")

# 掩码前缀（与 wecom_personal_rpa credential_codec 范式一致：***+末4位）
_MASK_PREFIX = "***"


# ============== 建表（幂等） ==============

def init_recruiting_notify_tables(conn) -> None:
    """幂等建面试通知两表（bs_recruiting_notify_settings / bs_recruiting_notify_logs）。

    由 src/db/database.py 启动初始化与集成测试调用（conn 由调用方管理事务）。
    """
    cursor = conn.cursor()
    # 通知配置：每租户一行；webhook_url 存 Fernet 密文（gAAAAA 开头），严禁明文落库
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bs_recruiting_notify_settings (
            tenant_id TEXT PRIMARY KEY,
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            webhook_url TEXT,
            at_mobiles JSONB NOT NULL DEFAULT '[]'::jsonb,
            pre_notify_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 通知留痕：sent / failed，failed 可走手动补推 API 重发
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bs_recruiting_notify_logs (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            candidates JSONB,
            content TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_bs_rnl_tenant
        ON bs_recruiting_notify_logs(tenant_id, created_at)
    """)
    logger.info("recruiting_notify 通知表已就绪 (bs_recruiting_notify_settings / bs_recruiting_notify_logs)")


def ensure_tables() -> None:
    """幂等建表（自开连接版，服务函数入口兜底调用）"""
    with get_db_connection() as conn:
        init_recruiting_notify_tables(conn)
        conn.commit()


# ============== 掩码 ==============

def mask_webhook_url(plaintext: Optional[str]) -> str:
    """明文 webhook 转掩码（***+末4位）；空值返回空串。

    API 层对外只出掩码；长度不足 4 位整体打码，避免掩码泄露全文。
    """
    if not plaintext:
        return ""
    if len(plaintext) <= 4:
        return _MASK_PREFIX
    return f"{_MASK_PREFIX}{plaintext[-4:]}"


# ============== settings 读写 ==============

def get_settings(tenant_id: str) -> Optional[Dict[str, Any]]:
    """取租户通知配置（webhook_url 解密为**明文**，仅供服务端内部使用）。

    未配置返回 None（调用方据此判定「未启用」）。解密失败（主密钥更换等）按
    未配置处理并记日志，不让单租户坏密文拖垮通知链路。
    """
    ensure_tables()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM bs_recruiting_notify_settings WHERE tenant_id = %s",
            (tenant_id,),
        )
        row = cursor.fetchone()
    if not row:
        return None
    d = dict(row)
    encrypted = d.get("webhook_url")
    if encrypted:
        try:
            d["webhook_url"] = decrypt_secret(encrypted).decode("utf-8")
        except Exception as e:  # noqa: BLE001 坏密文降级为未配置
            logger.error(f"recruiting_notify webhook 解密失败 tenant={tenant_id}: {type(e).__name__}")
            d["webhook_url"] = None
    else:
        d["webhook_url"] = None
    return d


def get_settings_masked(tenant_id: str) -> Dict[str, Any]:
    """取租户通知配置（webhook_url 掩码版，API 响应用）；无配置返回默认值结构。"""
    settings = get_settings(tenant_id)
    if settings is None:
        return {
            "tenant_id": tenant_id,
            "enabled": False,
            "webhook_url": "",
            "at_mobiles": [],
            "pre_notify_enabled": True,
        }
    return {
        "tenant_id": tenant_id,
        "enabled": bool(settings.get("enabled")),
        "webhook_url": mask_webhook_url(settings.get("webhook_url")),
        "at_mobiles": list(settings.get("at_mobiles") or []),
        "pre_notify_enabled": settings.get("pre_notify_enabled", True),
    }


def upsert_settings(tenant_id: str, **fields) -> Dict[str, Any]:
    """UPSERT 租户通知配置，返回掩码版（API 直用）。

    webhook_url 三态（沿 channel_config 掩码保留旧值范式）：
    - 未传 / 空串 / *** 开头掩码 → 保留旧值（不进更新列）
    - None → 清除（置 NULL）
    - 其他非空字符串 → 视为新明文，encrypt_secret 加密后存储
    其余字段（enabled / at_mobiles / pre_notify_enabled）传入即更新。
    """
    ensure_tables()
    allowed = {"enabled", "webhook_url", "at_mobiles", "pre_notify_enabled"}
    # 非 webhook 字段 None=未传=不改（enabled/pre_notify_enabled/at_mobiles 均有库默认值，
    # 显式传 None 会违反 NOT NULL 约束；清空 at_mobiles 请传 []）
    updates: Dict[str, Any] = {
        k: v for k, v in fields.items() if k in allowed and (k == "webhook_url" or v is not None)
    }

    if "webhook_url" in updates:
        raw = updates["webhook_url"]
        if raw is None:
            updates["webhook_url"] = None  # 显式清除
        elif isinstance(raw, str):
            if not raw.strip() or raw.startswith(_MASK_PREFIX):
                updates.pop("webhook_url")  # 掩码/空串 = 保留旧值
            else:
                updates["webhook_url"] = encrypt_secret(raw)
        else:
            raise ValueError("webhook_url 必须是字符串")

    if updates:
        columns = ["tenant_id"] + list(updates.keys())
        placeholders = ["%s"] * len(columns)
        values: List[Any] = [tenant_id]
        for key, value in updates.items():
            values.append(psycopg2.extras.Json(value) if key == "at_mobiles" else value)
        update_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in updates.keys())
        sql = f"""
            INSERT INTO bs_recruiting_notify_settings ({', '.join(columns)})
            VALUES ({', '.join(placeholders)})
            ON CONFLICT (tenant_id) DO UPDATE
            SET {update_clause}, updated_at = CURRENT_TIMESTAMP
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(sql, values)
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.opt(exception=True).error(f"recruiting_notify settings UPSERT 失败: {e}")
                raise

    return get_settings_masked(tenant_id)


# ============== 消息模板 ==============

def _candidate_lines(candidates: List[Dict[str, Any]]) -> List[str]:
    """候选人列表行（pre：name · score分 · highlight；done：name · time）"""
    lines = []
    for c in candidates or []:
        parts = [(c.get("name") or "").strip() or "未署名"]
        if c.get("score") is not None:
            parts.append(f"{c['score']}分")
        if (c.get("highlight") or "").strip():
            parts.append(c["highlight"].strip())
        lines.append("> " + " · ".join(parts))
    return lines


def format_pre_content(job_name: str, candidates: List[Dict[str, Any]], note: Optional[str]) -> str:
    """事前知会模板（设计 §1 ①）：拟邀名单 + 拟时间 + 备注 + 无异议按计划发出"""
    lines = [f"【面试邀约知会】{job_name}", "招聘智能体拟邀约以下候选人面试："]
    lines.extend(_candidate_lines(candidates))
    times = [(c.get("time") or "").strip() for c in candidates or []]
    times = [t for t in times if t]
    if times:
        lines.append("拟安排时间：" + " / ".join(times))
    if (note or "").strip():
        lines.append(f"备注：{note.strip()}")
    lines.append("如对邀约有异议请尽快联系；无异议将按计划发出邀约。")
    return "\n".join(lines)


def format_done_content(job_name: str, candidates: List[Dict[str, Any]]) -> str:
    """事后通报模板（设计 §1 ②）：实际名单 + 面试时间 + 请面试官留意日程"""
    lines = [f"【面试邀约已完成】{job_name}", "已向以下候选人发出面试邀约："]
    for c in candidates or []:
        name = (c.get("name") or "").strip() or "未署名"
        time_text = (c.get("time") or "").strip()
        lines.append(f"> {name} · {time_text}" if time_text else f"> {name}")
    lines.append("请面试官及相关同事留意日程。")
    return "\n".join(lines)


# ============== 留痕读写 ==============

def _insert_log(
    tenant_id: str, kind: str, candidates: List[Dict[str, Any]],
    content: str, status: str, error: Optional[str],
) -> int:
    """写通知留痕，返回日志 id（独立连接，失败由调用方兜底）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_recruiting_notify_logs
                (tenant_id, kind, candidates, content, status, error)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (tenant_id, kind, psycopg2.extras.Json(candidates), content, status, error),
        )
        log_id = cursor.fetchone()["id"]
        conn.commit()
        return log_id


def get_log(tenant_id: str, log_id: int) -> Optional[Dict[str, Any]]:
    """取单条通知留痕（tenant_id 双重过滤）"""
    ensure_tables()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM bs_recruiting_notify_logs WHERE id = %s AND tenant_id = %s",
            (log_id, tenant_id),
        )
        row = cursor.fetchone()
    return dict(row) if row else None


def _update_log_status(tenant_id: str, log_id: int, status: str, error: Optional[str]) -> None:
    """补推后更新留痕状态"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bs_recruiting_notify_logs SET status = %s, error = %s WHERE id = %s AND tenant_id = %s",
            (status, error, log_id, tenant_id),
        )
        conn.commit()


def _normalize_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """留痕用候选人归一化：只保留 name/score/highlight/time 四键"""
    keep = ("name", "score", "highlight", "time")
    return [{k: c.get(k) for k in keep if c.get(k) is not None} for c in candidates or []]


# ============== 通知编排 ==============

async def push_interview_notify(
    tenant_id: str, kind: str, job_name: str,
    candidates: List[Dict[str, Any]], note: Optional[str] = None,
) -> Dict[str, Any]:
    """面试邀约企微通知编排（kind=pre 事前知会 / done 事后通报）。

    返回 {pushed, log_id?, reason?, error?}：
    - 未配置 / 未 enabled → {pushed: False, reason: "未启用"}（不写日志，避免空跑堆积）
    - pre 且 pre_notify_enabled=False → {pushed: False, reason: "事前知会未启用"}
    - 发送成功 → 写 sent 日志，返回 log_id
    - 发送失败 → 写 failed 日志（含 error），返回 pushed=False + error
    - 任何异常不外抛（通知绝不阻塞邀约链路）
    """
    try:
        if kind not in NOTIFY_KINDS:
            return {"pushed": False, "reason": "未知通知类型"}

        settings = await asyncio.to_thread(get_settings, tenant_id)
        if not settings or not settings.get("enabled") or not settings.get("webhook_url"):
            return {"pushed": False, "reason": "未启用"}
        if kind == "pre" and not settings.get("pre_notify_enabled", True):
            return {"pushed": False, "reason": "事前知会未启用"}

        webhook_url = settings["webhook_url"]
        if kind == "pre":
            content = format_pre_content(job_name, candidates, note)
        else:
            content = format_done_content(job_name, candidates)

        ok, err = await wecom_bot.send_markdown(webhook_url, content)
        # done 且配置了 @人 → markdown 后补发 text 携带 @（markdown 不支持 @）
        if ok and kind == "done":
            at_mobiles = list(settings.get("at_mobiles") or [])
            if at_mobiles:
                text_ok, text_err = await wecom_bot.send_text(
                    webhook_url, "面试邀约已完成，请查看上条详情", at_mobiles
                )
                if not text_ok:
                    ok = False
                    err = f"补充@提醒发送失败: {text_err}"

        status = "sent" if ok else "failed"
        log_id = await asyncio.to_thread(
            _insert_log, tenant_id, kind, _normalize_candidates(candidates),
            content, status, None if ok else (err or "发送失败"),
        )
        result: Dict[str, Any] = {"pushed": ok, "log_id": log_id}
        if not ok:
            result["error"] = err or "发送失败"
        return result
    except Exception as e:  # noqa: BLE001 通知是增强不是闸门，任何异常都不外抛
        logger.opt(exception=True).error(f"recruiting_notify 推送异常 tenant={tenant_id} kind={kind}: {e}")
        return {"pushed": False, "error": f"通知服务异常: {type(e).__name__}"}


async def resend_log(tenant_id: str, log_id: int) -> Optional[Dict[str, Any]]:
    """按留痕手动补推：取 log 的 content 直接重发，并更新留痕状态。

    用于发送失败后的补推（走管理 API，需 settings 已启用且配置 webhook）。
    留痕不存在返回 None（API 层转 404）；配置不满足抛 ValueError（API 层转 400）。
    """
    log = await asyncio.to_thread(get_log, tenant_id, log_id)
    if log is None:
        return None

    settings = await asyncio.to_thread(get_settings, tenant_id)
    if not settings or not settings.get("enabled") or not settings.get("webhook_url"):
        raise ValueError("企微通知未启用，请先在通知设置中开启并配置 webhook")

    webhook_url = settings["webhook_url"]
    ok, err = await wecom_bot.send_markdown(webhook_url, log["content"])
    if ok and log.get("kind") == "done":
        at_mobiles = list(settings.get("at_mobiles") or [])
        if at_mobiles:
            text_ok, text_err = await wecom_bot.send_text(
                webhook_url, "面试邀约已完成，请查看上条详情", at_mobiles
            )
            if not text_ok:
                ok = False
                err = f"补充@提醒发送失败: {text_err}"

    await asyncio.to_thread(
        _update_log_status, tenant_id, log_id,
        "sent" if ok else "failed", None if ok else (err or "发送失败"),
    )
    result: Dict[str, Any] = {"pushed": ok, "log_id": log_id}
    if not ok:
        result["error"] = err or "发送失败"
    return result
