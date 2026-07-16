"""RPA 独立外部联系人姓名解析。

仅使用当前 RPA 渠道的 CorpID 与客户联系 Secret 调用企微客户详情接口，
不读取其他渠道数据。任何解析失败均安全降级，不阻断会话归档主链路。
"""

import hashlib
from dataclasses import dataclass
from typing import Optional

import httpx
from loguru import logger

from src.core.redis_client import redis_client

_GET_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
_EXTERNAL_CONTACT_URL = "https://qyapi.weixin.qq.com/cgi-bin/externalcontact/get"
_TOKEN_PREFIX = "wecom_rpa:external_contact:token"
_NAME_PREFIX = "wecom_rpa:external_contact:name"
_FAIL_PREFIX = "wecom_rpa:external_contact:fail"
_TOKEN_ERROR_CODES = {40014, 42001, 40001}


class ExternalContactResolveError(Exception):
    def __init__(self, errcode: int, errmsg: str):
        super().__init__(f"客户详情接口失败 errcode={errcode}")
        self.errcode = errcode
        self.errmsg = errmsg


@dataclass(frozen=True)
class ResolvedExternalContact:
    external_userid: str
    display_name: str


def _secret_fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]


def _token_key(tenant_id: str, corp_id: str, secret: str) -> str:
    return redis_client.make_key(_TOKEN_PREFIX, f"{tenant_id}:{corp_id}:{_secret_fingerprint(secret)}")


def _name_key(tenant_id: str, corp_id: str, secret: str, external_userid: str) -> str:
    return redis_client.make_key(
        _NAME_PREFIX,
        f"{tenant_id}:{corp_id}:{_secret_fingerprint(secret)}:{external_userid}",
    )


def _fail_key(tenant_id: str, corp_id: str, secret: str, external_userid: str) -> str:
    return redis_client.make_key(
        _FAIL_PREFIX,
        f"{tenant_id}:{corp_id}:{_secret_fingerprint(secret)}:{external_userid}",
    )


class ExternalContactResolver:
    def __init__(self, timeout_seconds: float = 8.0):
        self.timeout_seconds = timeout_seconds

    async def resolve(
        self, tenant_id: str, corp_id: str, secret: str, external_userid: str
    ) -> Optional[ResolvedExternalContact]:
        if not all((tenant_id, corp_id, secret, external_userid)):
            return None
        if not external_userid.startswith(("wm", "wo")):
            return None
        name_key = _name_key(tenant_id, corp_id, secret, external_userid)
        cached = redis_client.get(name_key)
        if cached:
            name = cached if isinstance(cached, str) else str(cached)
            return ResolvedExternalContact(external_userid, name)
        fail_key = _fail_key(tenant_id, corp_id, secret, external_userid)
        if redis_client.get(fail_key):
            return None

        try:
            result = await self._fetch(tenant_id, corp_id, secret, external_userid)
            redis_client.set(name_key, result.display_name, ex=24 * 3600)
            return result
        except ExternalContactResolveError as exc:
            # 权限/可见范围错误不会短时间自行恢复，避免刷新页面造成高频请求。
            ttl = 3600 if exc.errcode in (48002, 60020) else 300
            redis_client.set(fail_key, str(exc.errcode), ex=ttl)
            logger.warning(
                "[RpaExternalContact] 解析失败 tenant={} errcode={}", tenant_id, exc.errcode
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            redis_client.set(fail_key, "network", ex=60)
            logger.warning("[RpaExternalContact] 网络异常 tenant={} type={}", tenant_id, type(exc).__name__)
        except Exception as exc:
            redis_client.set(fail_key, "unknown", ex=60)
            logger.warning("[RpaExternalContact] 解析异常 tenant={} type={}", tenant_id, type(exc).__name__)
        return None

    async def _fetch(self, tenant_id: str, corp_id: str, secret: str, external_userid: str) -> ResolvedExternalContact:
        token_key = _token_key(tenant_id, corp_id, secret)
        token = redis_client.get(token_key)
        token = token if isinstance(token, str) else (str(token) if token else "")
        if not token:
            token = await self._get_token(corp_id, secret)
            redis_client.set(token_key, token, ex=6900)

        for attempt in range(2):
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    _EXTERNAL_CONTACT_URL,
                    params={"access_token": token, "external_userid": external_userid},
                )
                response.raise_for_status()
                payload = response.json()
            errcode = int(payload.get("errcode", 0) or 0)
            if errcode in _TOKEN_ERROR_CODES and attempt == 0:
                redis_client.delete(token_key)
                token = await self._get_token(corp_id, secret)
                redis_client.set(token_key, token, ex=6900)
                continue
            if errcode:
                raise ExternalContactResolveError(errcode, str(payload.get("errmsg") or ""))
            contact = payload.get("external_contact") or {}
            name = str(contact.get("name") or "").strip()
            if not name:
                raise ExternalContactResolveError(-1, "姓名为空")
            return ResolvedExternalContact(external_userid, name)
        raise ExternalContactResolveError(-1, "token 刷新后仍失败")

    async def _get_token(self, corp_id: str, secret: str) -> str:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(_GET_TOKEN_URL, params={"corpid": corp_id, "corpsecret": secret})
            response.raise_for_status()
            payload = response.json()
        errcode = int(payload.get("errcode", 0) or 0)
        if errcode:
            raise ExternalContactResolveError(errcode, str(payload.get("errmsg") or ""))
        token = str(payload.get("access_token") or "")
        if not token:
            raise ExternalContactResolveError(-1, "token 为空")
        return token


external_contact_resolver = ExternalContactResolver()
