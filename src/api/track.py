"""邮件打开追踪接收端（ai-lead-gen 技能 D 项，上游 b2b-lead-gen 口径）。

两个端点：
- GET /api/track/p.gif   像素打点（匿名写入，返回 1x1 透明 GIF）
- GET /api/track/opens   游标分页回拉（tracking_key secret 鉴权）

tracking_key 口径（技能侧 tracking_key.py 生成并持久化到租户目录）：
- track_tenant = md5(from_email.lower())[:8]，按发件邮箱隔离且匿名（不泄露邮箱明文）；
- secret = md5(from_email.lower() + "|" + salt)，md5 固定 32 位 hex；
- k 参数格式 "<track_tenant>.<secret>"，均按 hex 严格校验（防注入）。

安全边界：
- 像素 URL 出现在邮件源码里，收件人可见，因此 k 的 secret 不做强鉴权，
  只用于归属；真正的读保护在 /api/track/opens 的 secret 一致性校验；
- 两个端点在 TenantContextMiddleware 中匿名放行（/api/track/ 前缀）；
- t 参数（平台租户归属）由发送侧注入，属尽力而为字段——匿名端点无法强校验，
  伪造只会污染伪造者自己构造的请求，业务视图统一以 emails 表（真实 tenant_id）为准；
- 像素命中为单行 INSERT（连接池 + to_thread），打开风暴下最坏情况是写入排队，
  不阻塞图片响应；量级（冷邮件打开事件）远低于连接池承载上限。
"""

import asyncio
import base64
import re
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response
from loguru import logger

from src.api.rate_limit import SlidingWindowRateLimiter
from src.db.database import get_db_connection

router = APIRouter(tags=["邮件打开追踪"])

# 标准 1x1 透明 GIF（43 字节）
_GIF_BYTES = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)

_GIF_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}

# k 必须是 hex8.hex32（与 tracking_key.py 生成口径一致；手动覆盖 key 同样受此约束）
_KEY_RE = re.compile(r"^([0-9a-f]{8})\.([0-9a-f]{32})$")
# t（平台租户归属）：租户 ID 字符集，不匹配则忽略该字段
_TENANT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

MAX_MESSAGE_ID_LEN = 255
MAX_CAMPAIGN_LEN = 200
MAX_RECIPIENT_LEN = 320
MAX_UA_LEN = 512
OPENS_LIMIT_DEFAULT = 2000
OPENS_LIMIT_MAX = 5000

# 收件方邮箱客户端 / 图片代理会高频拉取像素，按 IP 做基础限速；
# 正常量级（ campaign 日发几十封、每封打开 1-2 次）远达不到该阈值
_pixel_limiter = SlidingWindowRateLimiter(
    max_requests=300, window_seconds=60, key_prefix="rate_limit:track_pixel",
)


def parse_tracking_key(k: str) -> Optional[tuple]:
    """解析 tracking_key，返回 (track_tenant, secret)；格式非法返回 None。"""
    if not k or len(k) > 64:
        return None
    match = _KEY_RE.match(k.strip().lower())
    if not match:
        return None
    return match.group(1), match.group(2)


def _clean(value: str, max_len: int) -> str:
    """像素参数清洗：去首尾空白、截断到上限。"""
    return (value or "").strip()[:max_len]


def _client_ip(request: Request) -> str:
    """客户端 IP：X-Forwarded-For 首段（生产在 nginx 后）-> request.client.host"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first[:64]
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


def _insert_open(track_tenant: str, tenant_id: Optional[str], campaign: str,
                 message_id: str, recipient: str, user_agent: str, ip: str) -> None:
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bs_ai_lead_gen_opens "
            "(tenant_id, track_tenant, campaign, message_id, recipient, user_agent, ip) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (tenant_id, track_tenant, campaign, message_id, recipient, user_agent, ip),
        )
        # get_db_connection 退出时无条件 rollback，必须显式提交
        conn.commit()


@router.get("/api/track/p.gif")
async def track_pixel(
    request: Request,
    k: str = Query(""),
    c: str = Query(""),
    m: str = Query(""),
    r: str = Query(""),
    t: str = Query(""),
):
    """像素打点：收件方邮箱客户端加载图片即产生一次打开记录。

    参数非法时静默丢弃（仍返回图片，不向收件端暴露校验细节），只记日志。
    """
    remote = _client_ip(request)
    if not await asyncio.to_thread(_pixel_limiter.is_allowed, remote):
        return Response(status_code=429, media_type="text/plain",
                        content=b"rate limited", headers=_GIF_HEADERS)

    parsed = parse_tracking_key(k)
    if not parsed:
        logger.warning(f"[track] p.gif 非法 tracking_key（丢弃不写入）：ip={remote}")
        return Response(content=_GIF_BYTES, media_type="image/gif", headers=_GIF_HEADERS)

    track_tenant, _secret = parsed
    message_id = _clean(m, MAX_MESSAGE_ID_LEN)
    if not message_id:
        logger.warning(f"[track] p.gif 缺 message_id（丢弃不写入）："
                       f"track_tenant={track_tenant}, ip={remote}")
        return Response(content=_GIF_BYTES, media_type="image/gif", headers=_GIF_HEADERS)

    tenant_id = t.strip() if _TENANT_RE.match(t.strip()) else None
    user_agent = (request.headers.get("user-agent") or "")[:MAX_UA_LEN]
    try:
        await asyncio.to_thread(
            _insert_open, track_tenant, tenant_id,
            _clean(c, MAX_CAMPAIGN_LEN), message_id,
            _clean(r, MAX_RECIPIENT_LEN), user_agent, remote,
        )
    except Exception as exc:
        # 打开记录尽力而为：写库失败不阻断图片响应，避免收件端看到坏图重试风暴
        logger.error(f"[track] p.gif 写入失败: {exc}")
    return Response(content=_GIF_BYTES, media_type="image/gif", headers=_GIF_HEADERS)


def _fetch_opens(track_tenant: str, campaign: str, cursor: int, limit: int) -> list:
    sql = (
        "SELECT id, campaign, message_id, recipient, opened_at, user_agent "
        "FROM bs_ai_lead_gen_opens WHERE track_tenant = %s AND id > %s"
    )
    params: list = [track_tenant, cursor]
    if campaign:
        sql += " AND campaign = %s"
        params.append(campaign)
    sql += " ORDER BY id ASC LIMIT %s"
    params.append(limit)
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
    opens = []
    for row in rows:
        opens.append({
            "id": row["id"],
            "campaign": row["campaign"] or "",
            "message_id": row["message_id"] or "",
            "recipient": row["recipient"] or "",
            "opened_at": row["opened_at"].isoformat() if row["opened_at"] else "",
            "user_agent": row["user_agent"] or "",
        })
    return opens


@router.get("/api/track/opens")
async def track_opens(
    k: str = Query(""),
    secret: str = Query(""),
    c: str = Query(""),
    cursor: int = Query(0, ge=0),
    limit: int = Query(OPENS_LIMIT_DEFAULT, ge=1),
):
    """游标分页回拉打开记录：k 中的 secret 必须与 secret 参数一致才返回。"""
    parsed = parse_tracking_key(k)
    if not parsed or not secret or secret.strip().lower() != parsed[1]:
        return JSONResponse(status_code=403, content={"detail": "鉴权失败"})
    track_tenant = parsed[0]

    limit = min(limit, OPENS_LIMIT_MAX)
    campaign = _clean(c, MAX_CAMPAIGN_LEN)
    try:
        opens = await asyncio.to_thread(_fetch_opens, track_tenant, campaign, cursor, limit)
    except Exception as exc:
        logger.error(f"[track] opens 查询失败: {exc}")
        return JSONResponse(status_code=500, content={"detail": "查询失败"})

    # 游标单调递增：满页时返回末条 id，不足一页返回空串（客户端停止翻页）
    next_cursor = str(opens[-1]["id"]) if len(opens) >= limit else ""
    return {"count": len(opens), "next_cursor": next_cursor, "opens": opens}
