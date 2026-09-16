"""公众号自有号清单源：扫码会话与绑定管理（WP13，设计 §3.1/§3.5）。

职责边界：只做「扫码登录状态机（bizlogin/scanloginqrcode）→ 健康检查 → 凭据
加密入库」与绑定状态的读写/解绑；清单拉取在 list_source.py，对账/入队在
service.py，本模块不含任何抓取或入库逻辑。

登录时序（2026-09-16 真机实验钉死）：
  POST /cgi-bin/bizlogin?action=startlogin（form，token 留空）
  → GET /cgi-bin/scanloginqrcode?action=getqrcode（PNG 二维码）
  → GET …/action=ask 轮询（status 0=等待 4/6=已扫待确认 1=已确认，
     401~405=二维码过期）
  → POST /cgi-bin/bizlogin?action=login → redirect_url query 提取 token
  → 健康检查（own-context appmsgpublish begin=0 count=1：
     200007=账号异常拒绑 / 200013=会话异常不绑 / ret=0=通过）
  → 凭据经 config_codec Fernet 加密入库（自动建配置或绑定既有 wechat_mp 配置，
     每租户仅一个绑定）

中间态存 Redis（key 前缀 ``wechat_mp_scan:``，TTL 5min）：cookie 全量串 +
sessionid + tenant_id（轮询时校验租户，防跨租户探测）。会话凭据形态实测：
必须以原始 ``k=v; ...`` 串进 Cookie header（httpx jar 重放因 domain/path 属性
丢失会 200003），故中间态只存序列化串，每轮轮询后合并响应新 cookie 回写。

v1 限制：每租户仅一个扫码绑定；注销/冻结号靠绑定健康检查拦截（扫码能"成功"
但接口全拒）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from src.core.redis_client import redis_client
from src.db.database import get_db_connection
from src.knowledge.embedding.embedding_client import sanitize_error_info
from src.saas.api.tenant_auth import require_admin
from src.saas.db.channel_config_db import ChannelConfigDB
from src.wechat_mp import config_codec as wechat_mp_codec
from src.wechat_mp.api import _require_tenant_context
from src.wechat_mp.identity import URLIdentityError, normalize_url
from src.wechat_mp.list_source import (
    ListAccountError,
    ListFreqControlError,
    ListSessionExpiredError,
    ListSourceError,
    OwnListClient,
    MP_BASE_URL,
    parse_list_page,
)

router = APIRouter(prefix="/api/saas/wechat-mp/list-session", tags=["公众号清单源"])

# ------------------------------- 常量 -------------------------------

SCAN_KEY_PREFIX = "wechat_mp_scan:"  # 扫码中间态 key 前缀（make_key 自动拼全局前缀）
SCAN_STATE_TTL_SECONDS = 300  # 中间态 TTL 5min（设计 §3.1）
LIST_SESSION_TTL_HOURS = 96  # 会话预计有效期 ≈ 扫码+96h（实测校准，设计 §3.1）
EXPIRING_WINDOW = timedelta(hours=24)  # expiring 预警窗口（expire_at-24h 起）

SYNC_MODES = ("auto_all", "manual")
LIST_SYNC_DEFAULT_INTERVAL_HOURS = 1  # 清单源默认同步频率（设计 §3.3）

# WP13-r1 历史清单（设计 §3.4：超出回填范围的历史，按需实时只读不落库）
HISTORY_DEFAULT_COUNT = 5  # 默认每页消息数（与「加载更多」步长一致）
HISTORY_MAX_COUNT = 20  # 每页消息数上限（清单接口单页上限口径）

_NICK_NAME_RE = re.compile(r'nick_name\s*[:=]\s*"([^"\']{1,64})"')
_USER_NAME_RE = re.compile(r'user_name\s*[:=]\s*"(gh_[A-Za-z0-9]{4,32})"')

# 后台首页 HTML 含 token/页面数据，绝不入日志；身份抓取失败不阻断绑定（nickname 留空）


class ScanFlowError(Exception):
    """扫码流程失败（reason_code 面向前端状态机；message 可直接展示）。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


# ------------------------------- HTTP 客户端工厂（测试注入点） -------------------------------


def _new_http_client(cookie_str: str = "", timeout: float = 15.0) -> httpx.Client:
    """登录流程 httpx 客户端（cookie 以原始串进 header，见模块 docstring 实测结论）。"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Referer": f"{MP_BASE_URL}/",
    }
    if cookie_str:
        headers["Cookie"] = cookie_str
    return httpx.Client(timeout=timeout, headers=headers, follow_redirects=True)


def _serialize_cookies(client: httpx.Client) -> str:
    """httpx cookie jar → 原始 ``k=v; ...`` 串（入库/Redis 中间态的唯一形态）。"""
    parts: Dict[str, str] = {}
    for c in client.cookies.jar:
        if c.name and c.value is not None:
            parts[c.name] = c.value
    return "; ".join(f"{k}={v}" for k, v in parts.items())


def _merge_cookies(base_str: str, client: httpx.Client) -> str:
    """存量 cookie 串与响应累积 jar 合并（后写者胜），保持会话跨轮询延续。"""
    cookies: Dict[str, str] = {}
    for part in (base_str or "").split(";"):
        if "=" in part:
            k, _, v = part.partition("=")
            if k.strip():
                cookies[k.strip()] = v.strip()
    for c in client.cookies.jar:
        if c.name and c.value is not None:
            cookies[c.name] = c.value
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


# ------------------------------- 绑定持久化 -------------------------------


def write_list_config_fields(
    config_id: str,
    fields: Optional[Dict[str, Any]] = None,
    remove_keys: Tuple[str, ...] = (),
) -> bool:
    """对 wechat_mp 配置做清单字段的读-改-写（其他字段原样保留，敏感值先加密）。

    不走 ChannelConfigDB.update：其「传入明文 callback_token 视为改密」等语义
    不适用于解密值回写（会把既有 Token 误判为改密、撤销回调验证态）。
    """
    fields = dict(fields or {})
    encrypted = wechat_mp_codec.encrypt_sensitive_fields(fields)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT config FROM tenant_channel_configs "
            "WHERE config_id = %s AND channel_type = 'wechat_mp'",
            (config_id,),
        )
        row = cursor.fetchone()
        if not row:
            return False
        cfg: Dict[str, Any] = json.loads(row["config"]) if row["config"] else {}
        for k in remove_keys:
            cfg.pop(k, None)
        cfg.update(encrypted)
        cursor.execute(
            "UPDATE tenant_channel_configs SET config = %s, updated_at = CURRENT_TIMESTAMP "
            "WHERE config_id = %s",
            (json.dumps(cfg, ensure_ascii=False), config_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def find_bound_list_config(tenant_id: str) -> Optional[Dict[str, Any]]:
    """本租户已扫码绑定的 wechat_mp 配置（掩码形态；v1 每租户仅一个绑定）。

    绑定判定 = config.list_session_token 非空；多个时取最早创建一条（防御性，
    正常不会出现）。
    """
    try:
        configs = ChannelConfigDB.list_by_tenant(tenant_id, "wechat_mp")
    except Exception:  # noqa: BLE001 查询失败按未绑定处理（上层给出明确错误）
        return None
    bound = [
        c for c in configs
        if str((c.get("config") or {}).get("list_session_token") or "").strip()
    ]
    if not bound:
        return None
    return bound[-1]


def _resolve_bind_target(tenant_id: str) -> Optional[Dict[str, Any]]:
    """绑定目标：已有扫码绑定优先（重新扫码），否则最早创建的 wechat_mp 配置。"""
    try:
        configs = ChannelConfigDB.list_by_tenant(tenant_id, "wechat_mp")
    except Exception:  # noqa: BLE001
        return None
    for c in configs:
        if str((c.get("config") or {}).get("list_session_token") or "").strip():
            return c
    return configs[-1] if configs else None  # list_by_tenant 为 id DESC，末位最早


def load_list_session(config_id: str) -> Optional[Dict[str, Any]]:
    """解密读取清单会话凭据（service 对账用）；缺失/非 wechat_mp 配置返回 None。"""
    try:
        cfg = ChannelConfigDB.get_by_id_decrypted(config_id)
    except Exception:  # noqa: BLE001
        return None
    if not cfg or cfg.get("channel_type") != "wechat_mp":
        return None
    data = cfg.get("config") or {}
    token = str(data.get("list_session_token") or "").strip()
    cookie = str(data.get("list_session_cookie") or "").strip()
    if not token or not cookie:
        return None
    return {"token": token, "cookie": cookie, "fields": data}


def _parse_iso_dt(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def effective_list_sync_status(fields: Dict[str, Any], now: Optional[datetime] = None) -> str:
    """持久状态 + 时间推导：active 且进入 expire_at-24h 窗口 → expiring。

    expired / account_error 只由真实信号置位（对账遇 session_expired /
    200007），不按时间提前推导——会话实际存活时间未获官方口径，宁可不猜。
    """
    status = str(fields.get("list_sync_status") or "active")
    if status == "active":
        expire_at = _parse_iso_dt(fields.get("list_session_expire_at"))
        now = now or datetime.now(timezone.utc)
        if expire_at and now >= expire_at - EXPIRING_WINDOW:
            return "expiring"
    return status


def get_list_session_status(tenant_id: str) -> Dict[str, Any]:
    """绑定状态查询（前端渠道配置弹窗与公众号内容页横幅共用；凭据不回传）。"""
    bound = find_bound_list_config(tenant_id)
    if not bound:
        return {"bound": False}
    cfg = bound.get("config") or {}
    return {
        "bound": True,
        "config_id": bound.get("config_id"),
        "nickname": str(cfg.get("list_account_nickname") or ""),
        "status": effective_list_sync_status(cfg),
        "expire_at": cfg.get("list_session_expire_at"),
        "last_sync_at": cfg.get("list_last_sync_at"),
        "sync_mode": cfg.get("list_sync_mode") or "auto_all",
        "sync_interval_hours": cfg.get("sync_interval_hours")
        or LIST_SYNC_DEFAULT_INTERVAL_HOURS,
        # WP13-r1：首次回填上限（钳制后回显）与回填完成标记（前端提示口径用）
        "max_articles": wechat_mp_codec.clamp_list_sync_max_articles(
            cfg.get("list_sync_max_articles")
        ),
        "backfill_done": bool(cfg.get("list_backfill_done")),
    }


def set_list_sync_status(config_id: str, status: str) -> None:
    """置位 list_sync_status（对账遇 session_expired→expired / 200007→account_error）。

    best-effort：写失败仅记日志，不影响对账主流程的失败落库。
    """
    try:
        write_list_config_fields(config_id, fields={"list_sync_status": status})
    except Exception as e:  # noqa: BLE001
        logger.opt(exception=True).warning(
            "后端日志：wechat_mp 清单状态置位失败 config_id={} status={}: {}",
            config_id, status, sanitize_error_info(str(e)),
        )


def bind_list_session(tenant_id: str, *, token: str, cookie: str, nickname: str) -> Dict[str, Any]:
    """健康检查通过后凭据入库：绑定既有 wechat_mp 配置或自动创建（每租户仅一个）。

    - 已有配置：读-改-写补清单字段（回调三件套/sync_interval_hours 等不动——
      「共用一条配置，补清单字段」，设计 §3.1）
    - 无配置：自动创建（走扫码绑定的新配置 sync_interval_hours 置 1，设计 §3.3）
    - list_sync_status 置 active（重新扫码绑定即恢复）
    - list_backfill_done 重置 False（WP13-r1 CR）：回填上限语义是「本次绑定的
      首次回填」。重扫码可能是换号绑定（无同号校验，nickname 仅展示不拦截），
      若沿用旧 done=true 会绕过上限对新号全量拉取入账；同号重扫多付一次封顶
      拉取（diff 全部未变零入库零计费），代价可忽略
    """
    now = datetime.now(timezone.utc)
    fields: Dict[str, Any] = {
        "list_session_token": token,
        "list_session_cookie": cookie,
        "list_session_at": now.isoformat(),
        "list_session_expire_at": (now + timedelta(hours=LIST_SESSION_TTL_HOURS)).isoformat(),
        "list_account_nickname": nickname or "",
        "list_sync_status": "active",
        "list_backfill_done": False,
    }
    target = _resolve_bind_target(tenant_id)
    if target is not None:
        # 重新扫码绑定：保留租户已选的同步模式（不被静默重置）
        existing_mode = (target.get("config") or {}).get("list_sync_mode")
        if existing_mode in SYNC_MODES:
            fields["list_sync_mode"] = existing_mode
        ok = write_list_config_fields(target["config_id"], fields=fields)
        if not ok:
            raise ScanFlowError("bind_failed", "绑定失败：渠道配置不存在或已删除")
        logger.bind(module="wechat_mp").info(
            "wechat_mp 清单会话绑定既有配置 tenant_id={} config_id={} nickname_len={}",
            tenant_id, target["config_id"], len(nickname or ""),
        )
        return {"config_id": target["config_id"], "created": False}
    config = dict(fields)
    config["enabled"] = True
    config["sync_interval_hours"] = LIST_SYNC_DEFAULT_INTERVAL_HOURS
    name = f"公众号-{nickname}" if nickname else "公众号内容源"
    try:
        created = ChannelConfigDB.create(tenant_id, "wechat_mp", config, name=name)
    except Exception as e:  # noqa: BLE001 并发撞同租户唯一索引等：给出明确业务错误
        logger.opt(exception=True).error(
            "后端日志：wechat_mp 清单绑定自动建配置失败 tenant_id={}: {}",
            tenant_id, sanitize_error_info(str(e)),
        )
        raise ScanFlowError("bind_failed", "自动创建渠道配置失败，请稍后重试") from None
    if created is None:
        raise ScanFlowError("bind_failed", "自动创建渠道配置失败，请稍后重试")
    logger.bind(module="wechat_mp").info(
        "wechat_mp 清单会话绑定新配置 tenant_id={} config_id={} nickname_len={}",
        tenant_id, created["config_id"], len(nickname or ""),
    )
    return {"config_id": created["config_id"], "created": True}


# ------------------------------- 扫码状态机 -------------------------------


def _scan_state_key(scan_id: str) -> str:
    return redis_client.make_key(SCAN_KEY_PREFIX, scan_id)


def _start_scan(tenant_id: str) -> Dict[str, Any]:
    """发起扫码登录：startlogin → getqrcode，中间态（cookie 串+租户）存 Redis。"""
    sessionid = uuid.uuid4().hex
    client = _new_http_client()
    try:
        try:
            client.post(
                f"{MP_BASE_URL}/cgi-bin/bizlogin",
                params={"action": "startlogin"},
                data={
                    "userlang": "zh_CN", "redirect_url": "", "login_type": 3,
                    "sessionid": sessionid, "token": "", "lang": "zh_CN",
                    "f": "json", "ajax": 1,
                },
            )
        except httpx.HTTPError:
            raise ScanFlowError("start_failed", "连接微信公众平台失败，请稍后重试") from None
        try:
            qr = client.get(
                f"{MP_BASE_URL}/cgi-bin/scanloginqrcode",
                params={"action": "getqrcode", "token": "", "lang": "zh_CN", "f": "json", "ajax": 1},
            )
        except httpx.HTTPError:
            raise ScanFlowError("start_failed", "连接微信公众平台失败，请稍后重试") from None
        if "image" not in (qr.headers.get("content-type") or ""):
            # 二维码接口异常（返回 JSON/HTML）：不外泄响应内容
            logger.bind(module="wechat_mp").warning(
                "wechat_mp 扫码二维码获取失败 tenant_id={} http={}",
                tenant_id, qr.status_code,
            )
            raise ScanFlowError("start_failed", "获取登录二维码失败，请稍后重试")
        cookie = _serialize_cookies(client)
    finally:
        client.close()

    scan_id = uuid.uuid4().hex
    redis_client.set(
        _scan_state_key(scan_id),
        {"cookie": cookie, "sessionid": sessionid, "tenant_id": tenant_id},
        ex=SCAN_STATE_TTL_SECONDS,
    )
    return {
        "scan_id": scan_id,
        "qr_data_url": "data:image/png;base64," + base64.b64encode(qr.content).decode("ascii"),
        "expires_in": SCAN_STATE_TTL_SECONDS,
    }


def _fetch_account_identity(token: str, cookie: str) -> str:
    """从后台首页 HTML 提取登录号昵称（best-effort，失败返回空串，不阻断绑定）。

    HTML 含 token 等页面数据：只回传昵称，原文与匹配过程绝不入日志。
    """
    client = _new_http_client(cookie_str=cookie)
    try:
        resp = client.get(
            f"{MP_BASE_URL}/cgi-bin/home",
            params={"t": "home/index", "lang": "zh_CN", "token": token},
        )
        html = resp.text or ""
        m = _NICK_NAME_RE.search(html)
        return m.group(1).strip() if m else ""
    except Exception:  # noqa: BLE001 昵称抓取失败不影响绑定
        logger.bind(module="wechat_mp").debug("wechat_mp 登录号昵称抓取失败（忽略）")
        return ""
    finally:
        client.close()


def _confirm_login(client: httpx.Client) -> str:
    """bizlogin?action=login 完成登录，从 redirect_url 提取 token（失败抛 ScanFlowError）。"""
    resp = client.post(
        f"{MP_BASE_URL}/cgi-bin/bizlogin",
        params={"action": "login"},
        data={
            "userlang": "zh_CN", "redirect_url": "", "cookie_forbidden": 0,
            "cookie_cleaned": 0, "plugin_used": 0, "login_type": 3,
            "token": "", "lang": "zh_CN", "f": "json", "ajax": 1,
        },
    )
    try:
        data = json.loads(resp.text)
    except ValueError:
        raise ScanFlowError("login_failed", "登录确认失败，请重新扫码") from None
    base = data.get("base_resp") or {}
    redirect_url = data.get("redirect_url") or ""
    token = ""
    if isinstance(redirect_url, str) and redirect_url:
        token = parse_qs(urlparse(redirect_url).query).get("token", [""])[0]
    if not isinstance(base.get("ret"), int) or base["ret"] != 0 or not token:
        logger.bind(module="wechat_mp").warning(
            "wechat_mp bizlogin 确认失败 ret={}", base.get("ret")
        )
        raise ScanFlowError("login_failed", "登录确认失败，请重新扫码")
    return token


def _new_list_client(token: str, cookie: str) -> OwnListClient:
    """清单探活客户端工厂（测试注入点：monkeypatch 本函数替换 http 传输层）。"""
    return OwnListClient(token=token, cookie=cookie)


def _health_check(token: str, cookie: str) -> None:
    """own-context 探活（设计 §3.1）：200007 拒绑 / 200013 与会话失效不绑 / ret=0 通过。"""
    probe = _new_list_client(token, cookie)
    try:
        probe.fetch_first(1)
    except ListAccountError:
        raise ScanFlowError(
            "account_error",
            "该公众号账号状态异常（已注销或冻结），无法绑定清单同步",
        ) from None
    except (ListFreqControlError, ListSessionExpiredError):
        raise ScanFlowError(
            "session_error",
            "登录会话校验未通过，未完成绑定；请稍后重新扫码",
        ) from None
    finally:
        probe.close()


def _poll_scan(tenant_id: str, scan_id: str) -> Dict[str, Any]:
    """轮询一次扫码状态；status=1 → 完成登录 → 健康检查 → 绑定入库。

    每轮轮询后合并响应 cookie 回写中间态（保持会话跨轮询延续）；中间态不存在
    或租户不符一律返回 expired（不区分原因，防跨租户探测）。
    """
    key = _scan_state_key(scan_id)
    state = redis_client.get(key)
    if (
        not isinstance(state, dict)
        or not state.get("cookie")
        or state.get("tenant_id") != tenant_id
    ):
        return {"status": "expired"}

    client = _new_http_client(cookie_str=str(state["cookie"]))
    try:
        try:
            resp = client.get(
                f"{MP_BASE_URL}/cgi-bin/scanloginqrcode",
                params={"action": "ask", "token": "", "lang": "zh_CN", "f": "json", "ajax": 1},
            )
        except httpx.HTTPError:
            return {"status": "waiting"}  # 瞬时网络异常：保持等待，下一轮继续
        try:
            data = json.loads(resp.text)
        except ValueError:
            return {"status": "waiting"}
        if not isinstance(data, dict):
            return {"status": "waiting"}
        status = data.get("status")

        if status in (401, 402, 403, 404, 405):
            redis_client.delete(key)
            return {"status": "qr_expired"}
        if status == 1:
            # ask 轮询期间服务端可能刷新 cookie：合并后换新 client 发确认请求，
            # 保证 bizlogin login 携带最新会话（对齐真机实验的 jar 延续语义）
            merged = _merge_cookies(str(state["cookie"]), client)
            client.close()
            client = _new_http_client(cookie_str=merged)
            try:
                token = _confirm_login(client)
            except ScanFlowError as e:
                redis_client.delete(key)
                return {"status": "failed", "reason_code": e.reason_code, "reason": e.message}
            # 确认后再次合并（login 响应可能再种 cookie），最终凭据以此次为准
            merged = _merge_cookies(merged, client)
            nickname = _fetch_account_identity(token, merged)
            try:
                _health_check(token, merged)
            except ScanFlowError as e:
                # 登录确认/健康检查不通过不绑定（凭据不入库），仅记日志不外泄凭据
                logger.bind(module="wechat_mp").warning(
                    "wechat_mp 扫码确认或健康检查未通过 tenant_id={} reason={}",
                    tenant_id, e.reason_code,
                )
                redis_client.delete(key)
                return {"status": "failed", "reason_code": e.reason_code, "reason": e.message}
            try:
                result = bind_list_session(
                    tenant_id, token=token, cookie=merged, nickname=nickname
                )
            except ScanFlowError as e:
                redis_client.delete(key)
                return {"status": "failed", "reason_code": e.reason_code, "reason": e.message}
            redis_client.delete(key)
            return {
                "status": "confirmed",
                "nickname": nickname,
                "config_id": result["config_id"],
            }

        # 未确认（0=等待 / 4、6=已扫待确认）：合并 cookie 回写，保持 TTL
        merged = _merge_cookies(str(state["cookie"]), client)
        redis_client.set(
            key,
            {"cookie": merged, "sessionid": state.get("sessionid"), "tenant_id": tenant_id},
            ex=SCAN_STATE_TTL_SECONDS,
        )
        return {"status": "scanned" if status in (4, 6) else "waiting"}
    finally:
        client.close()


# ------------------------------- API（薄入口） -------------------------------


@router.post("/scan")
async def start_scan(request: Request):
    """发起扫码：返回二维码（data URL）与 scan_id，前端轮询 scan/status。"""
    admin = require_admin(request)
    tenant_id = _require_tenant_context(admin)
    try:
        result = await asyncio.to_thread(_start_scan, tenant_id)
    except ScanFlowError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception:
        logger.opt(exception=True).error(
            "后端日志：wechat_mp 扫码发起失败 tenant_id={}", tenant_id
        )
        raise HTTPException(status_code=500, detail="发起扫码失败，请稍后重试")
    return {"success": True, **result}


@router.get("/scan/status")
async def scan_status(request: Request, scan_id: str):
    """轮询扫码状态：waiting / scanned / qr_expired / expired / failed / confirmed。"""
    admin = require_admin(request)
    tenant_id = _require_tenant_context(admin)
    if not scan_id or len(scan_id) > 64 or not scan_id.isalnum():
        raise HTTPException(status_code=400, detail="scan_id 非法")
    try:
        result = await asyncio.to_thread(_poll_scan, tenant_id, scan_id)
    except Exception:
        logger.opt(exception=True).error(
            "后端日志：wechat_mp 扫码轮询失败 tenant_id={}", tenant_id
        )
        raise HTTPException(status_code=500, detail="查询扫码状态失败，请稍后重试")
    return {"success": True, **result}


@router.delete("/scan")
async def unbind(request: Request):
    """解绑：清除清单会话字段（回调配置与历史文章保留），每租户仅一个绑定。"""
    admin = require_admin(request)
    tenant_id = _require_tenant_context(admin)
    try:
        result = await asyncio.to_thread(_unbind, tenant_id)
    except Exception:
        logger.opt(exception=True).error(
            "后端日志：wechat_mp 清单解绑失败 tenant_id={}", tenant_id
        )
        raise HTTPException(status_code=500, detail="解绑失败，请稍后重试")
    return {"success": True, **result}


def _unbind(tenant_id: str) -> Dict[str, Any]:
    bound = find_bound_list_config(tenant_id)
    if not bound:
        return {"unbound": False, "config_id": None}
    remove_keys = tuple(wechat_mp_codec.LIST_PLAIN_FIELDS) + (
        "list_session_token",
        "list_session_cookie",
    )
    ok = write_list_config_fields(bound["config_id"], remove_keys=remove_keys)
    if not ok:
        raise RuntimeError("unbind write failed")
    logger.bind(module="wechat_mp").info(
        "wechat_mp 清单会话解绑 tenant_id={} config_id={}", tenant_id, bound["config_id"]
    )
    return {"unbound": True, "config_id": bound["config_id"]}


@router.get("")
async def status(request: Request):
    """绑定状态查询（昵称/四态徽标/上次同步/模式/频率；凭据不回传）。"""
    admin = require_admin(request)
    tenant_id = _require_tenant_context(admin)
    try:
        result = await asyncio.to_thread(get_list_session_status, tenant_id)
    except Exception:
        logger.opt(exception=True).error(
            "后端日志：wechat_mp 清单绑定状态查询失败 tenant_id={}", tenant_id
        )
        raise HTTPException(status_code=500, detail="查询失败，请稍后重试")
    return {"success": True, **result}


class SwitchModeRequest(BaseModel):
    mode: str = Field(..., description="同步模式：auto_all（全部自动）/ manual（手动挑选）")


@router.post("/mode")
async def switch_mode(request: Request, body: SwitchModeRequest):
    """切换同步模式；manual→auto_all 时自动补齐 pending_manual 积压文章入队。"""
    admin = require_admin(request)
    tenant_id = _require_tenant_context(admin)
    if body.mode not in SYNC_MODES:
        raise HTTPException(status_code=400, detail="同步模式仅支持 auto_all / manual")
    from src.wechat_mp import service as wechat_mp_service

    try:
        result = await asyncio.to_thread(
            wechat_mp_service.switch_list_sync_mode, tenant_id, body.mode
        )
    except wechat_mp_service.WeChatMPBusinessError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.opt(exception=True).error(
            "后端日志：wechat_mp 清单模式切换失败 tenant_id={} mode={}", tenant_id, body.mode
        )
        raise HTTPException(status_code=500, detail="切换失败，请稍后重试")
    return {"success": True, **result}


class EnqueueManualRequest(BaseModel):
    article_row_ids: List[int] = Field(
        ..., description="待同步文章行 ID（pending_manual），单篇或批量，最多 100 条"
    )


@router.post("/articles/enqueue-manual")
async def enqueue_manual(request: Request, body: EnqueueManualRequest):
    """手动挑选同步：pending_manual 文章单篇/批量入队（走既有 URL 直采管道）。"""
    admin = require_admin(request)
    tenant_id = _require_tenant_context(admin)
    ids = sorted({int(i) for i in body.article_row_ids if int(i) > 0})
    if not ids:
        raise HTTPException(status_code=400, detail="请至少选择一篇文章")
    if len(ids) > 100:
        raise HTTPException(status_code=400, detail="单次最多勾选 100 篇")
    from src.wechat_mp import service as wechat_mp_service

    try:
        result = await asyncio.to_thread(
            wechat_mp_service.enqueue_manual_articles, tenant_id, admin["user_id"], ids
        )
    except wechat_mp_service.WeChatMPBusinessError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.opt(exception=True).error(
            "后端日志：wechat_mp 手动同步入队失败 tenant_id={} count={}", tenant_id, len(ids)
        )
        raise HTTPException(status_code=500, detail="操作失败，请稍后重试")
    return {"success": True, **result}


# ------------------------------- WP13-r1：历史清单（实时只读，不落库） -------------------------------


def _find_synced_external_ids(tenant_id: str, external_ids: List[str]) -> set:
    """批量查询已入库身份（bs_wechat_mp_articles，租户隔离 + ANY 查询）。

    status<>'deleted'：删除行视为「未入库」，用户可复制链接手动重导；
    alias 行计入（内容已收敛到主记录入库）。
    """
    if not external_ids:
        return set()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT external_id FROM bs_wechat_mp_articles
            WHERE tenant_id = %s AND status <> 'deleted' AND external_id = ANY(%s)
            """,
            (tenant_id, list(external_ids)),
        )
        return {r["external_id"] for r in cursor.fetchall()}


def _list_history_page(tenant_id: str, begin: int, count: int) -> Dict[str, Any]:
    """历史清单单页（设计 §3.4）：实时拉源不落库，按 identity 批量标注 synced。

    单次请求只拉 1 页消息（≤count≤20 消息），限速复用 OwnListClient；
    异常语义复用 list_source 失败分类（session_expired/account_error 由端点转
    明确 HTTP 错误）；httpx 客户端 finally 关闭；链接只做响应字段，不入日志。
    """
    bound = find_bound_list_config(tenant_id)
    if not bound:
        raise ScanFlowError("not_bound", "尚未绑定公众号清单源，请先扫码授权")
    session = load_list_session(bound["config_id"])
    if session is None:
        raise ScanFlowError("session_expired", "清单会话凭据缺失，请重新扫码授权")

    client = _new_list_client(session["token"], session["cookie"])
    try:
        total_count, articles = parse_list_page(client.fetch_page(begin, count))
    finally:
        client.close()

    items: List[Dict[str, Any]] = []
    for art in articles:
        try:
            identity = normalize_url(art.link)
        except URLIdentityError:
            # 单条链接不可规范身份（非 mp 域/参数缺失）：跳过该条（对齐对账口径）
            logger.bind(module="wechat_mp").debug(
                "wechat_mp 历史清单子篇链接不可规范（跳过）tenant_id={}", tenant_id
            )
            continue
        items.append(
            {
                "title": art.title,
                "create_time": art.create_time,
                "update_time": art.update_time,
                "link": art.link,
                "external_id": identity.external_id,
                "synced": False,
            }
        )
    synced_ids = _find_synced_external_ids(tenant_id, [it["external_id"] for it in items])
    for it in items:
        it["synced"] = it["external_id"] in synced_ids
    return {"items": items, "total_count": total_count, "begin": begin, "count": count}


@router.get("/history")
async def history(request: Request, begin: int = 0, count: int = HISTORY_DEFAULT_COUNT):
    """历史文章清单（超出首次回填范围的历史，按需翻看）。

    实时只读不落库：返回 标题/发布时间/链接 + synced 标注；未入库文章由用户
    复制链接走既有手动粘贴导入通道。begin 为消息偏移，count 每页消息数。
    """
    admin = require_admin(request)
    tenant_id = _require_tenant_context(admin)
    if begin < 0:
        raise HTTPException(status_code=400, detail="begin 需 ≥ 0")
    if count < 1 or count > HISTORY_MAX_COUNT:
        raise HTTPException(
            status_code=400, detail=f"count 需在 1~{HISTORY_MAX_COUNT} 之间"
        )
    try:
        result = await asyncio.to_thread(_list_history_page, tenant_id, begin, count)
    except ScanFlowError as e:
        # 未绑定 / 凭据缺失：明确业务错误（凭据不入 detail）
        raise HTTPException(status_code=400, detail=e.message)
    except ListSessionExpiredError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ListAccountError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ListSourceError as e:
        # 频控/结构异常/网络等：message 面向用户可直接展示（不含凭据与响应原文）
        logger.bind(module="wechat_mp").warning(
            "wechat_mp 历史清单拉取失败 tenant_id={} begin={}: {}", tenant_id, begin, e
        )
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        logger.opt(exception=True).error(
            "后端日志：wechat_mp 历史清单查询失败 tenant_id={}", tenant_id
        )
        raise HTTPException(status_code=500, detail="获取历史清单失败，请稍后重试")
    return {"success": True, **result}
