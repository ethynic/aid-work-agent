"""官网客服（web_c）C 端 API

路由：/api/c/web_c/{tenant_id}/{config_id}/*

C 端访客免注册/免登录，身份两种来源（设计 docs/system/web-c-channel-design.md §3）：
- 签名链接（ext）：租户官网服务端 HMAC-SHA256 签名，channel_user_id = ext_{官网userid}
- 匿名（anon）：前端生成 uid 存 localStorage + cookie，channel_user_id = anon_{uuid4}

安全要点：
- 校验失败一律 401/403，不区分失败原因细节（防枚举）
- runner_id / session_id / file_id 全部校验归属当前验签身份（防遍历窃听）
- 会话/消息查询强制叠加 tenant_id + session_id 前缀双重过滤
"""

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from src.api.rate_limit import SlidingWindowRateLimiter
from src.channels import ask_render
from src.channels.runner_agent import ChannelRunnerAgent, _clarification_wait
from src.channels.session import channel_session_manager
from src.config.settings import settings
from src.core.cache_utils import CacheKeys
from src.db.database import get_db_connection
from src.saas.db.channel_config_db import ChannelConfigDB
from src.saas.services.credit_gate import check_tenant_credit_blocked
from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.event_stream import FRAME_BYTES, ObserverResponse, parse_cursor
from src.services.agent_runner.web_client import RunnerServiceClient, close_event_transport

router = APIRouter(prefix="/api/c/web_c", tags=["官网客服"])


def _guard(func):
    """RunnerError -> 稳定错误响应（只透出稳定 code，不泄露内部细节）。"""
    import functools

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except RunnerError as error:
            status = error.status if 400 <= error.status < 600 else 502
            return JSONResponse(status_code=status, content={
                "success": False, "error": error.code, "debug": error.code})

    return wrapper

# 签名时效 24h（裁决 2026-11-16 #6，后续按需配置化）
_SIG_TS_TTL_SECONDS = 24 * 3600
# 匿名 uid：anon_ + uuid4（小写连字符形态）
_ANON_UID_RE = re.compile(
    r"^anon_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.IGNORECASE
)
# ext 签名 userid：官网用户标识，长度与字符集白名单
_EXT_USERID_RE = re.compile(r"^[A-Za-z0-9_\-@.]{1,128}$")
# 客户端幂等键
_CLIENT_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_\-:.]{8,128}$")
# 上传白名单扩展名（设计 §4.6）
_UPLOAD_ALLOWED_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".md", ".csv",
}
_UPLOAD_MAX_BYTES = 20 * 1024 * 1024
_UPLOAD_MAX_FILES_PER_CHAT = 10
# /chat 默认限流阈值（渠道 config JSON 可调：rate_limit_uid_per_min / rate_limit_ip_per_hour）
_DEFAULT_UID_LIMIT_PER_MIN = 5
_DEFAULT_IP_LIMIT_PER_HOUR = 60
# bootstrap 轻限流（页面加载触发，匿名换 uid 可刷，防脏数据制造）
_DEFAULT_BOOTSTRAP_LIMIT_PER_MIN = 30

_RUNNER_HEADER_DESC = "身份凭证缺失或无效"


# ============== 限流器（阈值入渠道 config JSON 可调，按 config_id 缓存实例） ==============

_LIMITERS: Dict[str, Dict[str, SlidingWindowRateLimiter]] = {}


def _get_limiters(config_id: str, conf: dict) -> Dict[str, SlidingWindowRateLimiter]:
    uid_max = max(1, int(conf.get("rate_limit_uid_per_min") or _DEFAULT_UID_LIMIT_PER_MIN))
    ip_max = max(1, int(conf.get("rate_limit_ip_per_hour") or _DEFAULT_IP_LIMIT_PER_HOUR))
    entry = _LIMITERS.get(config_id)
    if entry and entry.get("_sig") == (uid_max, ip_max):
        return entry
    limiters = {
        "chat_uid": SlidingWindowRateLimiter(
            max_requests=uid_max, window_seconds=60, key_prefix=CacheKeys.WEB_C_RATE_LIMIT_CHAT
        ),
        "chat_ip": SlidingWindowRateLimiter(
            max_requests=ip_max, window_seconds=3600, key_prefix=CacheKeys.WEB_C_RATE_LIMIT_IP
        ),
        "upload": SlidingWindowRateLimiter(
            max_requests=10, window_seconds=60, key_prefix=CacheKeys.WEB_C_RATE_LIMIT_UPLOAD
        ),
        "bootstrap": SlidingWindowRateLimiter(
            max_requests=_DEFAULT_BOOTSTRAP_LIMIT_PER_MIN, window_seconds=60,
            key_prefix=CacheKeys.WEB_C_RATE_LIMIT_BOOTSTRAP
        ),
    }
    limiters["_sig"] = (uid_max, ip_max)
    _LIMITERS[config_id] = limiters
    return limiters


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


# ============== 身份验签（设计 §3.2/§3.4） ==============


class WebCIdentity:
    """验签后的 C 端身份。"""

    def __init__(self, tenant_id: str, config_id: str, mode: str,
                 channel_user_id: str, display_name: str, subagent_type: str,
                 greeting: str = ""):
        self.tenant_id = tenant_id
        self.config_id = config_id
        self.mode = mode  # ext / anon
        self.channel_user_id = channel_user_id
        self.display_name = display_name or ""
        self.subagent_type = subagent_type or ""
        self.greeting = greeting or ""

    @property
    def session_prefix(self) -> str:
        return f"{self.tenant_id}_web_c_{self.channel_user_id}_"


def _param(request: Request, name: str) -> str:
    """读取身份参数：query 优先，同名大写 header 兜底（签名服务集成两种形态）。"""
    value = request.query_params.get(name)
    if value is not None:
        return value
    return request.headers.get(f"X-Web-C-{name.replace('_', '-').upper()}", "") or ""


def _verify_ext_signature(secret: str, userid: str, ts: str, name: str, sig: str) -> bool:
    """HMAC-SHA256(key=channel_secret, msg=f"{u}.{ts}.{name}") hex 小写；ts 时效 24h。"""
    if not secret or not userid or not ts or not sig:
        return False
    try:
        ts_int = int(str(ts).strip())
    except (TypeError, ValueError):
        return False
    if abs(time.time() - ts_int) > _SIG_TS_TTL_SECONDS:
        return False
    message = f"{userid}.{ts_int}.{name or ''}"
    expected = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, str(sig).strip().lower())


def _load_web_c_config(tenant_id: str, config_id: str):
    """加载并校验 web_c 渠道配置行；失败一律 403（防枚举，不区分细节）。"""
    cfg = ChannelConfigDB.get_by_id(config_id)
    if (
        not cfg
        or cfg.get("tenant_id") != tenant_id
        or cfg.get("channel_type") != "web_c"
    ):
        raise HTTPException(status_code=403, detail=_RUNNER_HEADER_DESC)
    conf = cfg.get("config") or {}
    if not conf.get("enabled"):
        raise HTTPException(status_code=403, detail=_RUNNER_HEADER_DESC)
    return cfg, conf


def _resolve_identity(request: Request, tenant_id: str, config_id: str) -> WebCIdentity:
    """验签并构建 C 端身份（ext 签名优先，uid 模式免签名，其次匿名 uid）。"""
    cfg, conf = _load_web_c_config(tenant_id, config_id)
    # identity_mode 缺省 anon（2026-11-19 起下线 both 组合模式，新配置三选一；
    # 存量 both 行为走既有分支不变，由管理员改配置收敛为单一模式）
    mode_cfg = str(conf.get("identity_mode") or "anon")

    userid = _param(request, "u").strip()
    if userid:
        if mode_cfg == "anon":
            raise HTTPException(status_code=403, detail=_RUNNER_HEADER_DESC)
        # name 参与签名但可缺省（缺省时签名串用空串）；URL decode 由框架完成
        name = _param(request, "name")
        # uid 模式（仅 userid 链接免签名，设计 §3.5）：userid 不验签，只做归属
        # 不做会话读取凭据；会话闸门见 _assert_session_owned 与 bootstrap/sessions
        # 服务端置空。签名参数（ts/sig）在本模式被忽略，不做「验签通过升级 ext」混合语义。
        if mode_cfg == "uid":
            if not _EXT_USERID_RE.match(userid):
                raise HTTPException(status_code=403, detail=_RUNNER_HEADER_DESC)
            return WebCIdentity(tenant_id, config_id, "uid", f"uid_{userid}", name,
                                cfg.get("subagent_type"), conf.get("greeting") or "")
        ts = _param(request, "ts")
        sig = _param(request, "sig")
        if not _EXT_USERID_RE.match(userid):
            raise HTTPException(status_code=403, detail=_RUNNER_HEADER_DESC)
        if not _verify_ext_signature(str(conf.get("channel_secret") or ""), userid, ts, name, sig):
            raise HTTPException(status_code=403, detail=_RUNNER_HEADER_DESC)
        return WebCIdentity(tenant_id, config_id, "ext", f"ext_{userid}", name,
                            cfg.get("subagent_type"), conf.get("greeting") or "")

    uid = _param(request, "uid").strip()
    if uid:
        # ext（仅签名）与 uid（仅 userid 链接）均为单一身份路径，匿名不放行
        if mode_cfg in ("ext", "uid"):
            raise HTTPException(status_code=403, detail=_RUNNER_HEADER_DESC)
        if not _ANON_UID_RE.match(uid):
            raise HTTPException(status_code=403, detail=_RUNNER_HEADER_DESC)
        return WebCIdentity(tenant_id, config_id, "anon", uid, "", cfg.get("subagent_type"),
                            conf.get("greeting") or "")

    raise HTTPException(status_code=403, detail=_RUNNER_HEADER_DESC)


# uid 模式会话 id 随机后缀（_s + 至少 8 位 hex）：确定性派生的会话 id 可由 userid
# 推算，在 uid 模式（userid 不验签）下一律不可寻址（设计 §3.5）
_UID_SESSION_SUFFIX_RE = re.compile(r"_s[0-9a-f]{8,}$", re.IGNORECASE)


def _assert_session_owned(identity: WebCIdentity, session_id: str) -> dict:
    """session_id 归属校验：前缀（租户+渠道+身份）+ DB 租户双重过滤。

    uid 模式（仅 userid 链接免签名）额外要求随机后缀：userid 可伪造，
    任何按 userid 确定性派生的会话 id 在该模式下一律拒绝。
    """
    if not session_id or not session_id.startswith(identity.session_prefix):
        raise HTTPException(status_code=403, detail="会话不存在")
    if identity.mode == "uid" and not _UID_SESSION_SUFFIX_RE.search(session_id):
        raise HTTPException(status_code=403, detail="会话不存在")
    session = channel_session_manager.get_session_by_id(session_id)
    if not session or session.get("tenant_id") != identity.tenant_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


# ============== Runner 客户端与归属校验 ==============


def _runner_client() -> RunnerServiceClient:
    return RunnerServiceClient(settings.agent_runner)


_RUNNER_NOT_FOUND = HTTPException(status_code=404, detail="执行不存在")


async def _get_owned_runner(identity: WebCIdentity, runner_id: str,
                            client: Optional[RunnerServiceClient] = None) -> dict:
    """runner_id 归属校验：Runner 侧行属主鉴权 + 本地 session 前缀一致性检查。"""
    if not runner_id or len(runner_id) > 128 or not re.match(r"^[A-Za-z0-9_\-]+$", runner_id):
        raise _RUNNER_NOT_FOUND
    value = await (client or _runner_client()).request(
        "GET", "/v1/runners/" + quote(runner_id, safe=""), authorization=None,
        channel_source="web_c", channel_user=identity.channel_user_id)
    row = value.get("runner") or {}
    session_id = ((row.get("session") or {}).get("session_id")) or ""
    if not session_id.startswith(identity.session_prefix):
        raise _RUNNER_NOT_FOUND
    return row


# ============== 终态落库（代理层职责，设计 §4.3 步骤 3） ==============


def _has_final_message(session_id: str, runner_id: str) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM channel_messages WHERE session_id = %s "
            "AND metadata->>'runner_id' = %s AND COALESCE(metadata->>'kind', '') = '' LIMIT 1",
            (session_id, runner_id),
        )
        return cursor.fetchone() is not None


def _persist_assistant_message(tenant_id: str, session_id: str, runner_id: str,
                               output: str, attachments: List[dict],
                               kind: str = "", client_request_id: Optional[str] = None) -> None:
    """assistant 消息落库（幂等：唯一部分索引 + ON CONFLICT，并发双写原子拦截）。

    direct_reply 类（runner_id 为空）按 client_request_id 判重——同会话多次
    纠正/重发回复内容各不相同，仅按 (runner_id, kind) 判重会吞掉后续回复。
    """
    message_id = f"msg_{uuid.uuid4().hex[:16]}"
    metadata = {"runner_id": runner_id}
    if kind:
        metadata["kind"] = kind
    if client_request_id:
        metadata["client_request_id"] = client_request_id
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO channel_messages
            (message_id, session_id, tenant_id, role, content, message_type, attachments, metadata)
            VALUES (%s, %s, %s, 'assistant', %s, 'text', %s::jsonb, %s::jsonb)
            ON CONFLICT DO NOTHING
            """,
            (
                message_id, session_id, tenant_id, output or "",
                json.dumps(attachments, ensure_ascii=False) if attachments else None,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        inserted = cursor.rowcount > 0
        if inserted:
            from datetime import datetime

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                "UPDATE channel_sessions SET last_message_at = %s, updated_at = %s "
                "WHERE session_id = %s",
                (now, now, session_id),
            )
        conn.commit()
        if not inserted:
            logger.debug(f"[web_c] 终态消息已存在，跳过重复落库 runner={runner_id}")


def _runner_files_to_attachments(row: dict) -> List[dict]:
    """public snapshot 投影（口径同 runner_agent.py downloadableFiles/images）。"""
    snapshot = row.get("snapshot") or {}
    result = row.get("result") or {}
    attachments: List[dict] = []
    for item in snapshot.get("downloadableFiles") or []:
        if isinstance(item, dict) and item.get("file_id"):
            attachments.append({
                "type": "file",
                "file_id": item["file_id"],
                "name": item.get("name"),
                "mime_type": item.get("mime_type"),
                "size": item.get("size"),
                "download_url": item.get("download_url"),
            })
    images = result.get("images") or snapshot.get("images") or []
    for item in images:
        if isinstance(item, dict) and item.get("file_id"):
            attachments.append({
                "type": "image",
                "file_id": item["file_id"],
                "name": item.get("display_name"),
                "mime_type": item.get("mime_type"),
                "size": item.get("size_bytes"),
                "download_url": item.get("download_url"),
            })
    return attachments


async def _persist_runner_result(identity: WebCIdentity, session_id: str, row: dict) -> None:
    """终态 assistant 消息落库（completed 幂等落库；失败/取消不落）。"""
    if row.get("status") != "completed":
        return
    runner_id = row.get("runner_id") or ""
    if not runner_id or await asyncio.to_thread(_has_final_message, session_id, runner_id):
        return
    result = row.get("result") or {}
    output = result.get("output") or ""
    attachments = _runner_files_to_attachments(row)
    await asyncio.to_thread(
        _persist_assistant_message, identity.tenant_id, session_id, runner_id, output, attachments
    )


async def _backfill_terminal_runners(identity: WebCIdentity, session_id: str) -> int:
    """SSE 断开兜底：会话下未落库的终态 runner 补写 + 等待中反问补写（设计 §4.3 步骤 3/4）。

    终态 completed 补最终回复；waiting（ask_user 停车）补澄清反问——访客在
    反问后关闭页面时，无人再轮询 get_runner，靠下次拉取历史补齐。
    """
    page = await _runner_client().request(
        "GET", "/v1/sessions/channel/" + quote(session_id, safe="") + "/runners",
        authorization=None, channel_source="web_c", channel_user=identity.channel_user_id,
        params={"source": "web_c", "profile_id": identity.subagent_type or "main",
                "channel_user_id": identity.channel_user_id, "channel_chat_id": ""})
    persisted: set = set()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT metadata->>'runner_id' AS runner_id FROM channel_messages WHERE session_id = %s "
            "AND metadata->>'runner_id' IS NOT NULL AND COALESCE(metadata->>'kind', '') = ''",
            (session_id,),
        )
        persisted = {row["runner_id"] for row in cursor.fetchall()}
    count = 0
    for row in (page.get("runners") or []):
        runner_id = row.get("runner_id") or ""
        if row.get("status") == "completed":
            if not runner_id or runner_id in persisted:
                continue
            await _persist_runner_result(identity, session_id, row)
            count += 1
        elif row.get("status") == "waiting":
            question, wait_id = _clarification_question(row)
            if await asyncio.to_thread(
                _persist_clarification_message, identity.tenant_id, session_id,
                runner_id, question, wait_id,
            ):
                count += 1
    return count


def _clarification_question(row: dict) -> tuple:
    """从 runner 行提取澄清反问文本与 wait_id（无反问返回空串二元组）。"""
    wait = _clarification_wait((row.get("snapshot") or {}).get("waiting")) or {}
    if wait.get("kind") != "clarification":
        return "", ""
    wait_id = wait.get("wait_id") or ""
    form = wait.get("form")
    if isinstance(form, dict) and form.get("questions") and wait_id:
        return ask_render.render_single_message(form, ask_render.source_segment_limit("web_c")), wait_id
    return wait.get("question") or "", wait_id


def _waiting_text(row: dict) -> str:
    """提问等待的渠道文本化口径（web_c 归入 single 渠道组，设计 §4.3 步骤 4）。"""
    return _clarification_question(row)[0]


def _persist_clarification_message(tenant_id: str, session_id: str, runner_id: str,
                                   question: str, wait_id: str) -> bool:
    """澄清反问落库（接待页/历史可见，设计 §4.3 步骤 4 的代理层落库半边）。

    幂等：metadata.client_request_id = clarify_{wait_id}，命中
    uq_web_c_messages_client_request 唯一部分索引 + ON CONFLICT 原子拦截；
    先 SELECT 存在检查避免高频轮询路径反复空写。同一 runner 多次 ask_user
    按 wait_id 各自落库，互不吞并。
    """
    if not question or not wait_id:
        return False
    dedup_key = f"clarify_{wait_id}"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM channel_messages WHERE session_id = %s "
            "AND metadata->>'client_request_id' = %s LIMIT 1",
            (session_id, dedup_key),
        )
        if cursor.fetchone() is not None:
            return False
        message_id = f"msg_{uuid.uuid4().hex[:16]}"
        metadata = {"runner_id": runner_id, "kind": "clarification",
                    "client_request_id": dedup_key}
        cursor.execute(
            """
            INSERT INTO channel_messages
            (message_id, session_id, tenant_id, role, content, message_type, attachments, metadata)
            VALUES (%s, %s, %s, 'assistant', %s, 'text', NULL, %s::jsonb)
            ON CONFLICT DO NOTHING
            """,
            (
                message_id, session_id, tenant_id, question,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        inserted = cursor.rowcount > 0
        if inserted:
            from datetime import datetime

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                "UPDATE channel_sessions SET last_message_at = %s, updated_at = %s "
                "WHERE session_id = %s",
                (now, now, session_id),
            )
        conn.commit()
        return inserted


# ============== SSE 透传（事件流在 terminal+settled 时由上游关闭） ==============


class WebCEventResponse(ObserverResponse):
    """Runner events SSE 薄代理；上游流结束后触发代理层终态落库（幂等）。"""

    def __init__(self, response, client, admission, finalize=None):
        super().__init__(admission)
        self.response, self.client = response, client
        self._finalize = finalize

    async def pump(self, send):
        async for chunk in self.response.aiter_raw():
            if len(chunk) > FRAME_BYTES:
                return
            await send(chunk)
        if self._finalize is not None:
            try:
                await self._finalize()
            except Exception:
                logger.opt(exception=True).warning("[web_c] SSE 终态落库失败（messages 拉取兜底补写）")

    async def close(self):
        await close_event_transport(self.response, self.client)


# ============== 用户/会话构建 ==============


async def _ensure_user_and_session(identity: WebCIdentity) -> tuple:
    """首次出现身份时创建 users 记录（source=web_c）+ 渠道会话。"""
    user_id = await _ensure_web_c_user(identity)
    session = await asyncio.to_thread(
        channel_session_manager.get_or_create_session,
        "web_c", identity.channel_user_id, identity.tenant_id,
        {"user_id": user_id, "name": identity.display_name or ""},
        None, {"channel_config_id": identity.config_id}, identity.subagent_type or "",
    )
    # 存量会话无渠道归属（渠道归属功能上线前的会话）：本渠道访客到达时补记，
    # 使租户侧官网接待页可按渠道展示/筛选。幂等：仅补记缺失归属的会话
    await asyncio.to_thread(_backfill_session_channel_config, identity)
    return user_id, session


def _backfill_session_channel_config(identity: "WebCIdentity") -> int:
    """为本渠道访客的存量 web_c 会话补记 channel_config_id（jsonb 合并，幂等）。

    匿名 uid 按渠道配置隔离（localStorage key web_c_anon_{config_id}），
    同一 channel_user_id 的会话必属本渠道；ext 签名 userid 跨官网复用时
    理论上可能误归，按 userid 官网唯一约定风险可忽略。
    不失效会话缓存：缓存对象无归属字段读点（C 端仅做归属校验），租户侧
    列表直查 DB，Redis TTL 10 分钟内自愈。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE channel_sessions
            SET metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
            WHERE tenant_id = %s AND channel_type = 'web_c' AND channel_user_id = %s
              AND (metadata IS NULL OR NOT (metadata ? 'channel_config_id'))
            """,
            (
                json.dumps({"channel_config_id": identity.config_id}, ensure_ascii=False),
                identity.tenant_id,
                identity.channel_user_id,
            ),
        )
        conn.commit()
        return cursor.rowcount


async def _ensure_web_c_user(identity: WebCIdentity) -> str:
    """确保 web_c 身份对应 users 记录存在，返回 user_id。"""
    from src.saas.services.auto_register import ensure_user_registered

    user_info = {"name": identity.display_name} if identity.display_name else None
    return await ensure_user_registered(
        "web_c", identity.channel_user_id, identity.tenant_id,
        user_info=user_info, source="web_c",
    )


def _create_extra_session_sync(identity: WebCIdentity, user_id: str) -> dict:
    """创建 web_c 身份的额外会话（「新会话」按钮，首条消息发送时惰性创建）。

    session_id 须满足归属前缀 {tenant}_web_c_{uid}_（_assert_session_owned 校验），
    以 _s{12hex} 随机后缀与默认会话区分；uid 模式（仅 userid 链接）依赖该随机
    后缀保证会话 id 不可由 userid 推算（48bit 熵，设计 §3.5）。

    channel_chat_id 恒为 NULL：Runner 侧 _channel_in_tx 把请求 chat_id 与
    channel_sessions.channel_chat_id 严格比对（web_c 代理对 Runner 的 submit/
    快照/SSE/cancel 调用全程 channel_chat_id=None，2026-11-19 聊易生产
    CHANNEL_ACTOR_FORBIDDEN 事故），额外会话与默认会话的区分改用 metadata
    extra_session 标记（get_or_create_session 默认查找按该标记排除），
    不得再把唯一 token 写进 channel_chat_id 列。
    """
    session_id = (
        f"{identity.tenant_id}_web_c_{identity.channel_user_id}_"
        f"{identity.subagent_type or ''}_s{uuid.uuid4().hex[:12]}"
    )
    return channel_session_manager.create_session(
        session_id=session_id,
        channel_type="web_c",
        channel_user_id=identity.channel_user_id,
        tenant_id=identity.tenant_id,
        channel_chat_id=None,
        user_info={"user_id": user_id, "name": identity.display_name or ""},
        subagent_id=identity.subagent_type or "",
        metadata={"channel_config_id": identity.config_id, "extra_session": "1"},
    )


def _add_user_message(session_id: str, tenant_id: str, text: str,
                      attachments: List[dict], client_request_id: str) -> str:
    """用户消息落库（幂等：client_request_id 唯一索引 + ON CONFLICT，防重试双写）。"""
    message_id = f"msg_{uuid.uuid4().hex[:16]}"
    from datetime import datetime

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO channel_messages
            (message_id, session_id, tenant_id, role, content, message_type, attachments, metadata)
            VALUES (%s, %s, %s, 'user', %s, 'text', %s::jsonb, %s::jsonb)
            ON CONFLICT DO NOTHING
            """,
            (
                message_id, session_id, tenant_id, text,
                json.dumps(attachments, ensure_ascii=False) if attachments else None,
                json.dumps({"client_request_id": client_request_id}, ensure_ascii=False),
            ),
        )
        inserted = cursor.rowcount > 0
        if inserted:
            cursor.execute(
                "UPDATE channel_sessions SET last_message_at = %s, updated_at = %s "
                "WHERE session_id = %s",
                (now, now, session_id),
            )
        conn.commit()
    return message_id if inserted else ""


# ============== API 端点 ==============


@router.get("/{tenant_id}/{config_id}/bootstrap")
@_guard
async def bootstrap(tenant_id: str, config_id: str, request: Request):
    """渠道可用性校验 + greeting / 绑定子智能体 / 身份回显 / 最近会话摘要。"""
    identity = _resolve_identity(request, tenant_id, config_id)
    _, conf = _load_web_c_config(tenant_id, config_id)
    limiters = _get_limiters(config_id, conf)
    if not limiters["bootstrap"].is_allowed(f"{config_id}:{identity.channel_user_id}"):
        return JSONResponse(status_code=429, content={
            "success": False, "error": "请求过于频繁，请稍后再试", "code": "RATE_LIMITED"})
    from src.saas.services.agent_name_resolver import resolve_agent_names

    # uid 模式（设计 §3.5）：userid 不验签，只登记归属不派生默认会话；
    # 最近会话摘要在服务端强制置空（开关只挡 UI 不挡 API）
    if identity.mode == "uid":
        await _ensure_web_c_user(identity)
        await asyncio.to_thread(_backfill_session_channel_config, identity)
        recent = []
    else:
        _, sessions = await _ensure_user_and_session(identity)
        recent = await asyncio.to_thread(
            channel_session_manager.list_sessions,
            "web_c", None, identity.tenant_id, None, None, identity.channel_user_id, 10,
        )
        recent.sort(key=lambda x: x.get("updated_at") or x.get("created_at") or "", reverse=True)
    # 历史会话入口仅签名链接（ext）模式开放：uid 不验签无跨访问历史；anon 模式
    # 强制关闭（2026-11-19 与渠道配置弹框禁用置灰同口径，开关只挡 UI 不挡 API）
    show_history = bool(conf.get("show_history", False)) and identity.mode == "ext"
    subagent = None
    if identity.subagent_type:
        names = resolve_agent_names(tenant_id, [identity.subagent_type])
        subagent = {
            "agent_id": identity.subagent_type,
            "name": names.get(identity.subagent_type, identity.subagent_type),
        }
    return {
        "success": True,
        "greeting": identity.greeting or "您好，我是本公司的智能助手，有什么可以帮您？",
        # 附件上传开关（渠道 config，默认 False：隐藏附件按钮 + 服务端拒绝上传）
        "allow_attachment": bool(conf.get("allow_attachment", False)),
        # 历史会话入口开关（渠道 config，默认 False：不显示右上角「历史会话」按钮；
        # 仅 ext 模式生效，uid/anon 服务端强制 False，见 §3.5）
        "show_history": show_history,
        "identity": {
            "mode": identity.mode,
            "channel_user_id": identity.channel_user_id,
            "display_name": identity.display_name or None,
        },
        "subagent": subagent,
        "sessions": [
            {
                "session_id": s.get("session_id"),
                "subagent_id": s.get("subagent_id") or "",
                "updated_at": s.get("updated_at"),
                "last_message_at": s.get("last_message_at"),
            }
            for s in recent
        ],
    }


class WebCChatRequest(BaseModel):
    text: str = Field(default="", max_length=100_000, description="消息文本")
    client_request_id: str = Field(min_length=8, max_length=128, description="客户端幂等键")
    attachments: List[dict] = Field(default_factory=list, max_length=10, description="上传 ref 数组")
    session_id: str = Field(default="", max_length=200, description="续聊目标会话（空=默认会话）")
    new_session: bool = Field(default=False, description="开新会话（首条消息时惰性创建）")

    class Config:
        extra = "forbid"


@router.post("/{tenant_id}/{config_id}/chat")
@_guard
async def chat(tenant_id: str, config_id: str, body: WebCChatRequest, request: Request):
    """提交消息：验签 -> 限流 -> 积分拦截 -> 落用户消息 -> submit 受理 -> 返回 runner_id。"""
    identity = _resolve_identity(request, tenant_id, config_id)
    cfg, conf = _load_web_c_config(tenant_id, config_id)
    if not _CLIENT_REQUEST_ID_RE.match(body.client_request_id):
        raise HTTPException(status_code=422, detail="client_request_id 格式无效")

    limiters = _get_limiters(config_id, conf)
    # 限流键拼 config 维度：ext_{userid}/匿名 uid 跨租户/跨渠道配置不共享配额桶
    uid_key = f"{config_id}:{identity.channel_user_id}"
    if not limiters["chat_uid"].is_allowed(uid_key):
        return JSONResponse(status_code=429, content={
            "success": False, "error": "发送过于频繁，请稍后再试", "code": "RATE_LIMITED"})
    if not limiters["chat_ip"].is_allowed(f"{config_id}:{_client_ip(request)}"):
        return JSONResponse(status_code=429, content={
            "success": False, "error": "当前网络请求过于频繁，请稍后再试", "code": "RATE_LIMITED"})

    credit = check_tenant_credit_blocked(tenant_id)
    if credit is not None:
        return credit

    text = (body.text or "").strip()
    refs = _validate_attachment_refs(body.attachments)
    # 附件开关（渠道 config，默认关）：未开启时拒绝带附件的消息（前端按钮已隐藏，此处兜底）
    if refs and not conf.get("allow_attachment", False):
        raise HTTPException(status_code=403, detail="该渠道未开放附件发送")
    if not text and not refs:
        raise HTTPException(status_code=422, detail="消息内容不能为空")

    user_id = await _ensure_web_c_user(identity)
    # 会话解析：显式 session_id 续聊（归属校验）> new_session 惰性建新会话 > 默认会话
    # （多会话模型：一个身份可有多条会话；旧客户端不传两参时行为不变）。
    # uid 模式（设计 §3.5）：userid 不验签，无确定性默认会话，未带 session_id 一律
    # 惰性创建随机后缀会话（随机会话由前端 sessionStorage 持有）。
    if body.session_id:
        session = _assert_session_owned(identity, body.session_id)
    elif body.new_session or identity.mode == "uid":
        session = await asyncio.to_thread(_create_extra_session_sync, identity, user_id)
    else:
        _, session = await _ensure_user_and_session(identity)
    session_id = session["session_id"]

    # 用户消息落库（幂等）；附件元数据不含内容，Runner 侧按 file_id 租户域解析。
    # 纯附件消息与 agent 输入同文案，保证历史渲染与执行输入一致。
    user_content = text or "（见附件）"
    att_meta = [
        {"type": r.get("type") or "file", "file_id": r["file_id"], "name": r.get("name"),
         "mime_type": r.get("mime_type"), "size": r.get("size")}
        for r in refs
    ]
    await asyncio.to_thread(
        _add_user_message, session_id, tenant_id, user_content, att_meta, body.client_request_id)

    agent = ChannelRunnerAgent(
        source="web_c", session_id=session_id, channel_user_id=identity.channel_user_id,
        channel_chat_id=None, profile_id=identity.subagent_type or "main",
        config_id=config_id, tenant_id=tenant_id)
    outcome = await agent.submit(user_input=user_content, attachments=refs,
                                 client_request_id=body.client_request_id)

    # 纠正提示 / 重发分段等直接回复：不进入轮询，代理层直接落库并回显
    if outcome.direct_response is not None:
        reply = outcome.direct_response.text or ""
        if reply:
            await asyncio.to_thread(
                _persist_assistant_message, tenant_id, session_id, "", reply, [],
                "direct_reply", body.client_request_id)
        return {"success": True, "session_id": session_id, "runner_id": None,
                "status": "completed", "direct_reply": reply}

    row = outcome.row
    runner_id = row.get("runner_id")
    response = {"success": True, "session_id": session_id, "runner_id": runner_id,
                "status": row.get("status"), "direct_reply": None}
    if row.get("status") == "waiting":
        question, wait_id = _clarification_question(row)
        if question:
            await asyncio.to_thread(
                _persist_clarification_message, tenant_id, session_id,
                runner_id, question, wait_id)
        response["waiting_text"] = question
    return response


@router.get("/{tenant_id}/{config_id}/runners/{runner_id}")
@_guard
async def get_runner(tenant_id: str, config_id: str, runner_id: str, request: Request):
    """Runner 行快照透传（归属校验后）；waiting 场景附渠道文本化提问文本。"""
    identity = _resolve_identity(request, tenant_id, config_id)
    row = await _get_owned_runner(identity, runner_id)
    waiting = _waiting_text(row)
    if waiting:
        # 中途 ask_user 反问落库（幂等 by wait_id）：SSE 对 waiting 不关闭，
        # 终态落库覆盖不到，C 端轮询本端点时补写，保证接待页与历史可见
        session_id = ((row.get("session") or {}).get("session_id")) or ""
        _, wait_id = _clarification_question(row)
        await asyncio.to_thread(
            _persist_clarification_message, tenant_id, session_id,
            runner_id, waiting, wait_id)
        row = dict(row)
        row["waiting_text"] = waiting
    return {"success": True, "runner": row}


@router.get("/{tenant_id}/{config_id}/runners/{runner_id}/events")
@_guard
async def runner_events(tenant_id: str, config_id: str, runner_id: str, request: Request,
                        after_seq: Optional[int] = Query(default=None, ge=0, le=2**63 - 1)):
    """Runner events SSE 透传（Last-Event-ID 续传）；终态由代理层落库。"""
    identity = _resolve_identity(request, tenant_id, config_id)
    client = _runner_client()
    # 归属校验先行：防遍历 runner_id 窃听他人事件流
    await _get_owned_runner(identity, runner_id, client)
    cursor = parse_cursor(after_seq, request.headers.get("Last-Event-ID"))
    from src.services.agent_runner.event_stream import ObserverAdmission

    event_admission = ObserverAdmission()
    event_admission.acquire()
    response = upstream_client = None
    transferred = False
    try:
        response, upstream_client = await client.open_events(
            "/v1/runners/" + quote(runner_id, safe="") + "/events",
            authorization=None, after_seq=cursor,
            channel_source="web_c", channel_user=identity.channel_user_id)

        async def finalize():
            try:
                row = await _get_owned_runner(identity, runner_id, client)
                await _persist_runner_result(identity, row["session"]["session_id"], row)
            except HTTPException:
                pass

        observer = WebCEventResponse(response, upstream_client, event_admission, finalize)
        transferred = True
        return observer
    finally:
        if not transferred:
            try:
                await close_event_transport(response, upstream_client)
            finally:
                event_admission.release()


@router.post("/{tenant_id}/{config_id}/runners/{runner_id}/cancel")
@_guard
async def cancel_runner(tenant_id: str, config_id: str, runner_id: str, request: Request):
    """用户主动停止当前执行（透传 Runner cancel，归属校验后）。"""
    identity = _resolve_identity(request, tenant_id, config_id)
    value = await _runner_client().request(
        "POST", "/v1/runners/" + quote(runner_id, safe="") + "/cancel", authorization=None,
        channel_source="web_c", channel_user=identity.channel_user_id)
    return value


@router.get("/{tenant_id}/{config_id}/sessions")
async def list_sessions(tenant_id: str, config_id: str, request: Request,
                        limit: int = Query(default=50, ge=1, le=100)):
    """当前验签身份的历史会话列表（updated_at DESC）；uid 模式恒空（设计 §3.5）。"""
    identity = _resolve_identity(request, tenant_id, config_id)
    if identity.mode == "uid":
        # userid 不验签：按 userid 列会话 = 允许伪造者枚举他人历史，服务端直接置空
        return {"success": True, "sessions": []}
    sessions = await asyncio.to_thread(
        channel_session_manager.list_sessions,
        "web_c", None, identity.tenant_id, None, None, identity.channel_user_id, limit,
    )
    sessions.sort(key=lambda x: x.get("updated_at") or x.get("created_at") or "", reverse=True)
    return {
        "success": True,
        "sessions": [
            {
                "session_id": s.get("session_id"),
                "subagent_id": s.get("subagent_id") or "",
                "created_at": s.get("created_at"),
                "updated_at": s.get("updated_at"),
                "last_message_at": s.get("last_message_at"),
            }
            for s in sessions
        ],
    }


@router.get("/{tenant_id}/{config_id}/sessions/{session_id}/messages")
async def session_messages(tenant_id: str, config_id: str, session_id: str, request: Request,
                           page: int = Query(default=1, ge=1),
                           page_size: int = Query(default=50, ge=1, le=200)):
    """指定会话历史消息（新会话从旧会话切回时渲染）；拉取前触发终态补写检查。"""
    identity = _resolve_identity(request, tenant_id, config_id)
    _assert_session_owned(identity, session_id)
    try:
        await _backfill_terminal_runners(identity, session_id)
    except Exception:
        logger.opt(exception=True).warning(f"[web_c] 终态补写检查失败 session={session_id[:40]}")
    result = await asyncio.to_thread(
        channel_session_manager.get_messages_paginated,
        session_id, None, page, page_size,
    )
    return {"success": True, **result}


# ============== 附件与图片上传（设计 §4.6） ==============


def _validate_attachment_refs(attachments: List[dict]) -> List[dict]:
    """校验 /chat 附件 ref（仅保留白名单键，Runner 侧按 file_id 租户域解析）。"""
    refs = []
    for item in attachments or []:
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail="附件格式无效")
        file_id = str(item.get("file_id") or "")
        if not re.match(r"^file_[0-9a-f]{12}$", file_id):
            raise HTTPException(status_code=422, detail="附件 file_id 无效")
        name = str(item.get("name") or file_id)[:512]
        refs.append({
            "file_id": file_id,
            "name": name,
            "type": str(item.get("type") or "file"),
            "mime_type": str(item.get("mime_type") or ""),
            "size": item.get("size"),
        })
    return refs


def _sanitize_filename(name: str) -> str:
    """文件名清洗：去路径分隔符与控制字符，保留可读名（落盘名另用 file_id）。"""
    cleaned = re.sub(r"[\\/\x00-\x1f<>:\"|?*]", "_", str(name or "")).strip("._ ")
    return (cleaned or "附件")[:200]


_MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp",
    ".pdf": "application/pdf", ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
}


def _save_upload_sync(tenant_id: str, user_id: Optional[str], channel_user_id: str,
                      filename: str, content: bytes) -> dict:
    """落盘 + 注册元数据（Redis 命名空间 + file_assets DB 持久化，同步实现）。"""
    from src.core.storage import get_tenant_storage_path
    from src.services.files.asset_store import register_uploaded_file

    ext = os.path.splitext(filename)[1].lower()
    if ext not in _UPLOAD_ALLOWED_EXTS:
        raise HTTPException(status_code=422, detail="不支持的文件类型")
    if len(content) > _UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=422, detail="文件过大，最大支持 20MB")

    file_id = f"file_{uuid.uuid4().hex[:12]}"
    # 落盘 basename == file_id（Runner 侧 resolve_scoped_uploaded_file 磁盘兜底
    # 按 basename 匹配，Redis TTL 过期后仍可解析；口径同渠道出站注册 helper）
    stored_name = f"{file_id}{ext}"
    file_path = get_tenant_storage_path(tenant_id, "conversation", filename=stored_name)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "wb") as f:
        f.write(content)

    mime_type = _MIME_BY_EXT.get(ext, "application/octet-stream")
    file_type = "image" if mime_type.startswith("image/") else "file"
    register_uploaded_file(
        file_id, file_path, name=_sanitize_filename(filename), size=len(content),
        mime_type=mime_type, file_type=file_type, tenant_id=tenant_id, user_id=user_id,
        source="web_c_upload",
    )
    try:
        from src.core.redis_client import redis_client

        redis_client.hset(redis_client.make_key("uploaded_file", file_id),
                          "channel_user_id", channel_user_id)
    except Exception:
        logger.debug("[web_c] 上传 channel_user_id 标记写入失败（不影响归属校验主路径）")

    return {
        "file_id": file_id,
        "name": _sanitize_filename(filename),
        "type": file_type,
        "mime_type": mime_type,
        "size": len(content),
    }


@router.post("/{tenant_id}/{config_id}/upload")
async def upload_file(tenant_id: str, config_id: str, request: Request,
                      file: UploadFile = File(...)):
    """C 端附件/图片上传（multipart，独立于 chat 限流：每身份每分钟 10 个）。"""
    identity = _resolve_identity(request, tenant_id, config_id)
    _, conf = _load_web_c_config(tenant_id, config_id)
    # 附件开关（渠道 config，默认关）：未开启时拒绝上传（前端按钮已隐藏，此处兜底）
    if not conf.get("allow_attachment", False):
        raise HTTPException(status_code=403, detail="该渠道未开放附件上传")
    limiters = _get_limiters(config_id, conf)
    if not limiters["upload"].is_allowed(f"{config_id}:{identity.channel_user_id}"):
        return JSONResponse(status_code=429, content={
            "success": False, "error": "上传过于频繁，请稍后再试", "code": "RATE_LIMITED"})

    # Content-Length 早拒：避免 20MB 超限文件先全量读入内存再判（边界 +1MB 容忍分片开销）
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _UPLOAD_MAX_BYTES + 1024 * 1024:
        raise HTTPException(status_code=422, detail="文件过大，最大支持 20MB")

    filename = _sanitize_filename(file.filename or "")
    if not os.path.splitext(filename)[1]:
        raise HTTPException(status_code=422, detail="文件缺少扩展名")

    content = await file.read()
    if len(content) > _UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=422, detail="文件过大，最大支持 20MB")

    user_id, _ = await _ensure_user_and_session(identity)
    ref = await asyncio.to_thread(
        _save_upload_sync, tenant_id, user_id, identity.channel_user_id, filename, content)
    return {"success": True, **ref}


@router.get("/{tenant_id}/{config_id}/files/{file_id}")
async def download_file(tenant_id: str, config_id: str, file_id: str, request: Request):
    """C 端自有附件代理下载（免登录身份校验 + 归属校验，防遍历窃取）。"""
    from src.api.message_share import content_disposition_header

    identity = _resolve_identity(request, tenant_id, config_id)
    if not re.match(r"^file_[0-9a-f]{12}$", file_id):
        raise HTTPException(status_code=404, detail="文件不存在")

    # 归属校验：file_id 出现在当前身份的会话消息中，或为当前身份上传
    owned = await asyncio.to_thread(_file_owned_by_identity, identity, file_id)
    if not owned:
        raise HTTPException(status_code=404, detail="文件不存在")

    from src.core.storage import resolve_scoped_uploaded_file
    from src.services.files.asset_store import get_file_asset

    metadata = None
    try:
        from src.core.redis_client import redis_client

        metadata = redis_client.hgetall(redis_client.make_key("uploaded_file", file_id)) or None
    except Exception:
        metadata = None
    if not metadata:
        metadata = await asyncio.to_thread(get_file_asset, file_id)
    found = await asyncio.to_thread(
        resolve_scoped_uploaded_file, file_id, identity.tenant_id, metadata=metadata)
    if not found or not found.get("path") or not os.path.exists(found["path"]):
        raise HTTPException(status_code=404, detail="文件不存在")

    name = (metadata or {}).get("name") or os.path.basename(found["path"])
    mime_type = found.get("mime_type") or "application/octet-stream"
    return FileResponse(
        path=found["path"],
        media_type=mime_type,
        headers={"Content-Disposition": content_disposition_header(str(name), True)},
    )


def _file_owned_by_identity(identity: WebCIdentity, file_id: str) -> bool:
    """file_id 归属判定：当前身份上传 OR 出现在当前身份会话消息附件中（防遍历）。

    仅匹配服务端写入的 attachments 列；content 是访客可控文本（可粘贴他人
    file_id），不构成下载授权（CR P1-1）。
    """
    try:
        from src.core.redis_client import redis_client

        meta = redis_client.hgetall(redis_client.make_key("uploaded_file", file_id)) or {}
        if meta.get("channel_user_id") == identity.channel_user_id:
            return True
    except Exception:
        pass
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 1
            FROM channel_messages m
            JOIN channel_sessions s ON s.session_id = m.session_id
            WHERE s.tenant_id = %s AND s.channel_type = 'web_c'
              AND s.channel_user_id = %s
              AND CAST(m.attachments AS TEXT) LIKE %s
            LIMIT 1
            """,
            (identity.tenant_id, identity.channel_user_id, f"%{file_id}%"),
        )
        return cursor.fetchone() is not None
