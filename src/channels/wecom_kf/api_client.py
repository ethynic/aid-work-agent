"""微信客服 API 客户端

封装所有微信客服相关的 API 调用：
- token 管理（带缓存和并发锁）
- sync_msg 拉取消息
- send_msg 发送消息
- send_welcome 发送欢迎语
- trans_service_state 转人工
- get_customer_info 获取客户信息
"""
import asyncio
import errno
import hashlib
import json
import os
import stat
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx
from loguru import logger


MAX_MEDIA_SIZE = 20 * 1024 * 1024  # 20MB


@dataclass(frozen=True)
class NativeWriteDecision:
    """Application-owned permission or an already persisted platform result."""
    operation: Any = field(repr=False)
    replay_result: Optional[dict] = field(default=None, repr=False)
    dispatch: bool = True


class NativeWriteError(RuntimeError):
    authoritative_storage_failure = True

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class NativeWriteUnknown(NativeWriteError):
    """A possible POST must not be retried or replaced with another send."""


class NativeWriteStopped(NativeWriteError):
    """The application denied dispatch, possibly with a durable suppression."""


class NativeWritePreparationFailed(RuntimeError):
    """Local media preparation failed before any platform POST."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class WeComKfApiClient:
    """微信客服 API 客户端"""

    BASE_URL = "https://qyapi.weixin.qq.com"

    def __init__(self, corp_id: str, secret: str):
        self.corp_id = corp_id
        self.secret = secret
        self._access_token: Optional[str] = None
        self._token_expires: int = 0
        self._token_lock = asyncio.Lock()
        self._http_client: Optional[httpx.AsyncClient] = None
        self._ingress_bytes: Optional[int] = None
        self._native_media_max_bytes = 10 * 1024 * 1024
        self._write_observer = None
        self._native_tasks = set()
        self._native_target = None
        self._native_closing = False

    # ==================== HTTP 客户端 ====================

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )
        return self._http_client

    async def close(self):
        if self.native_write_enabled:
            self._native_closing = True
            await self._drain_task(asyncio.create_task(self._close_native()))
            return
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def _close_native(self):
        while self._native_tasks:
            tasks = tuple(self._native_tasks)
            await asyncio.gather(*tasks, return_exceptions=True)
            self._native_tasks.difference_update(tasks)
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    @property
    def native_write_enabled(self):
        return self._write_observer is not None

    def enable_native_writes(self, observer, *, open_kfid=None, actor_id=None):
        """Install one trusted owner; credentials never enter the descriptor."""
        if self._write_observer is not None and self._write_observer is not observer:
            raise NativeWriteStopped('KF_NATIVE_OWNER_ALREADY_BOUND')
        if not all(callable(getattr(observer, name, None)) for name in
                   ('before_post', 'known', 'unknown', 'unwritten')):
            raise ValueError('KF_NATIVE_OBSERVER_INVALID')
        self._write_observer = observer
        self._native_target = (open_kfid, actor_id)
        # Token and read-only requests use the existing bounded/private path.
        if self._ingress_bytes is None:
            self.enable_ingress_mode(65536)

    @staticmethod
    async def _drain_task(task):
        """Caller cancellation never abandons an owned HTTP/SQL operation."""
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
            except BaseException:
                break
        if cancelled:
            # Retrieve failures without letting them replace caller cancellation.
            if not task.cancelled():
                task.exception()
            raise asyncio.CancelledError
        return task.result()

    async def _owned_native_task(self, coroutine):
        if self._native_closing:
            coroutine.close()
            raise NativeWriteStopped('KF_NATIVE_OWNER_CLOSING')
        task = asyncio.create_task(coroutine)
        self._native_tasks.add(task)
        try:
            return await self._drain_task(task)
        finally:
            self._native_tasks.discard(task)

    def _write_description(self, path, body):
        raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        if len(raw) > 65536:
            raise NativeWritePreparationFailed('KF_NATIVE_REQUEST_TOO_LARGE')
        descriptor = {'request_digest': hashlib.sha256(raw).hexdigest(), 'request_bytes': len(raw)}
        for key in ('open_kfid', 'touser', 'external_userid', 'msgtype', 'service_state', 'servicer_userid'):
            if key in body:
                descriptor[key] = body[key]
        if self._native_target:
            kfid, actor = self._native_target
            if ((kfid and 'open_kfid' in body and body['open_kfid'] != kfid)
                    or (actor and any(body[key] != actor for key in ('touser', 'external_userid') if key in body))):
                raise NativeWriteStopped('KF_NATIVE_TARGET_MISMATCH')
        return descriptor

    @staticmethod
    def _strict_platform_result(data):
        if (not isinstance(data, dict) or type(data.get('errcode')) is not int):
            raise NativeWriteUnknown('KF_NATIVE_REPLY_INVALID')
        # Only response fields used by the original API are retained. A platform
        # supplied response_origin cannot spoof the trusted HTTP boundary.
        result = {key: data[key] for key in ('errcode', 'msgid', 'media_id', 'type',
                                           'created_at', 'service_state', 'servicer_userid') if key in data}
        for key in ('msgid', 'media_id', 'type', 'servicer_userid'):
            if key in result and (not isinstance(result[key], str) or len(result[key].encode()) > 4096):
                raise NativeWriteUnknown('KF_NATIVE_REPLY_INVALID')
        for key in ('created_at', 'service_state'):
            if key in result and (type(result[key]) is not int or result[key] < 0):
                raise NativeWriteUnknown('KF_NATIVE_REPLY_INVALID')
        result['response_origin'] = 'platform'
        return result

    async def _native_post(self, path, descriptor, *, body=None, files=None, extra_params=None):
        # Token/read preparation occurs before durable start; no write retries.
        if self._native_closing:
            raise NativeWriteStopped('KF_NATIVE_OWNER_CLOSING')
        token = await self.get_access_token()
        client = await self._get_client()
        if self._native_closing:
            raise NativeWriteStopped('KF_NATIVE_OWNER_CLOSING')
        observer = self._write_observer
        decision = await observer.before_post(path, descriptor)
        if not isinstance(decision, NativeWriteDecision) or type(decision.dispatch) is not bool:
            raise NativeWriteStopped('KF_NATIVE_DECISION_INVALID')
        if decision.replay_result is not None:
            if (decision.dispatch or not isinstance(decision.replay_result, dict)
                    or decision.replay_result.get('response_origin') != 'platform'):
                raise NativeWriteStopped('KF_NATIVE_REPLAY_INVALID')
            return self._strict_platform_result(decision.replay_result)
        if not decision.dispatch:
            raise NativeWriteStopped('KF_NATIVE_DISPATCH_DENIED')
        operation = decision.operation
        try:
            guard = getattr(observer, 'assert_dispatch', None)
            if guard is not None:
                await guard(operation)
            if self._native_closing:
                raise NativeWriteStopped('KF_NATIVE_OWNER_CLOSING')
        except BaseException:
            await observer.unwritten(operation, 'KF_NATIVE_PREPOST_DENIED')
            raise
        try:
            data = bytearray()
            async with asyncio.timeout(30):
                async with client.stream('POST', f'{self.BASE_URL}{path}',
                        params={'access_token': token, **(extra_params or {})}, json=body, files=files) as response:
                    if response.status_code != 200:
                        raise NativeWriteUnknown('KF_NATIVE_HTTP_STATUS_UNKNOWN')
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > 65536:
                            raise NativeWriteUnknown('KF_NATIVE_REPLY_TOO_LARGE')
                        data.extend(chunk)
            def unique_object(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise NativeWriteUnknown('KF_NATIVE_REPLY_INVALID')
                    result[key] = value
                return result
            result = self._strict_platform_result(json.loads(data, object_pairs_hook=unique_object))
            if path == '/cgi-bin/media/upload' and result['errcode'] == 0 and not result.get('media_id'):
                raise NativeWriteUnknown('KF_NATIVE_MEDIA_REPLY_INVALID')
        except BaseException as error:
            code = error.code if isinstance(error, NativeWriteUnknown) else 'KF_NATIVE_HTTP_UNKNOWN'
            await observer.unknown(operation, code)
            if isinstance(error, asyncio.CancelledError):
                raise
            raise NativeWriteUnknown(code) from None
        try:
            await observer.known(operation, result)
        except BaseException:
            # The real response has not become a durable fact. Preserve the
            # original authoritative error; never manufacture an explicit reject.
            await observer.unknown(operation, 'KF_NATIVE_RESULT_STORAGE_UNKNOWN')
            raise
        return result

    def enable_ingress_mode(self, max_bytes: int, *, media_max_bytes: int = 10 * 1024 * 1024):
        """The received-only owner uses bounded HTTP and private error details."""
        if type(max_bytes) is not int or not 65536 <= max_bytes <= 4194304:
            raise ValueError("KF_INGRESS_HTTP_LIMIT_INVALID")
        if type(media_max_bytes) is not int or not 0 < media_max_bytes <= MAX_MEDIA_SIZE:
            raise ValueError("KF_INGRESS_MEDIA_LIMIT_INVALID")
        self._ingress_bytes = max_bytes
        self._native_media_max_bytes = media_max_bytes

    async def _ingress_json(self, method, url, *, params, body=None):
        client = await self._get_client()
        data = bytearray()
        async with client.stream(method, url, params=params, json=body) as response:
            if response.status_code != 200:
                raise RuntimeError("KF_INGRESS_HTTP_STATUS")
            # Consume original streaming chunks without aggregating to a delayed
            # application chunk size; check before extending the bounded buffer.
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > self._ingress_bytes:
                    raise RuntimeError("KF_INGRESS_HTTP_TOO_LARGE")
                data.extend(chunk)
        try:
            result = json.loads(data)
        except (ValueError, UnicodeError):
            raise RuntimeError("KF_INGRESS_HTTP_JSON_INVALID") from None
        if not isinstance(result, dict):
            raise RuntimeError("KF_INGRESS_HTTP_JSON_INVALID")
        return result

    # ==================== Token 管理 ====================

    async def get_access_token(self) -> str:
        """获取 access_token（带缓存和并发锁）"""
        if self._access_token and time.time() < self._token_expires:
            return self._access_token

        async with self._token_lock:
            if self._access_token and time.time() < self._token_expires:
                return self._access_token
            return await self._refresh_access_token()

    async def _refresh_access_token(self) -> str:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                client = await self._get_client()
                if self._ingress_bytes is not None:
                    data = await self._ingress_json("GET", f"{self.BASE_URL}/cgi-bin/gettoken",
                        params={"corpid": self.corp_id, "corpsecret": self.secret})
                    # Original success shape is access_token/expires_in; some
                    # success responses omit errcode. Present error codes are strict.
                    code = data.get("errcode", 0)
                    if type(code) is not int or code != 0:
                        raise RuntimeError("KF_INGRESS_TOKEN_REJECTED")
                    if (not isinstance(data.get("access_token"), str) or not data["access_token"]
                        or len(data["access_token"].encode()) > 4096
                        or type(data.get("expires_in")) is not int or not 300 < data["expires_in"] <= 86400):
                        raise RuntimeError("KF_INGRESS_TOKEN_SHAPE_INVALID")
                else:
                    response = await client.get(
                        f"{self.BASE_URL}/cgi-bin/gettoken",
                        params={"corpid": self.corp_id, "corpsecret": self.secret},
                    )
                    data = response.json()
                errcode = data.get("errcode", 0)
                if errcode != 0:
                    raise RuntimeError(
                        f"获取 access_token 失败: errcode={errcode}, "
                        f"errmsg={data.get('errmsg')}"
                    )
                self._access_token = data["access_token"]
                self._token_expires = time.time() + data["expires_in"] - 300
                logger.info("微信客服 access_token 获取成功")
                return self._access_token
            except Exception as e:
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    if self._ingress_bytes is not None:
                        logger.warning("KF token retry: type={}", type(e).__name__)
                    else:
                        logger.warning(f"获取 access_token 重试 {attempt + 1}/{max_retries}: {e}")
                    await asyncio.sleep(wait)
                else:
                    if self._ingress_bytes is not None:
                        logger.error("KF token failed: type={}", type(e).__name__)
                    else:
                        logger.error(f"获取 access_token 最终失败: {e}")
                    raise

    def _invalidate_token(self):
        self._access_token = None
        self._token_expires = 0

    # ==================== 通用请求 ====================

    async def _request(
        self,
        method: str,
        path: str,
        json_body: Optional[Dict] = None,
        extra_params: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """发送请求（带 token 自动刷新重试）"""
        if self.native_write_enabled and path not in (
                '/cgi-bin/kf/send_msg', '/cgi-bin/kf/send_msg_on_event', '/cgi-bin/kf/service_state/trans',
                '/cgi-bin/kf/account/list', '/cgi-bin/kf/sync_msg', '/cgi-bin/kf/service_state/get',
                '/cgi-bin/kf/customer/batchget'):
            raise NativeWriteStopped('KF_NATIVE_ENDPOINT_NOT_ALLOWED')
        if self.native_write_enabled and path in (
                '/cgi-bin/kf/send_msg', '/cgi-bin/kf/send_msg_on_event', '/cgi-bin/kf/service_state/trans'):
            if method != 'POST':
                raise NativeWriteStopped('KF_NATIVE_METHOD_INVALID')
            body = json.loads(json.dumps(json_body or {}, ensure_ascii=False, allow_nan=False))
            description = self._write_description(path, body)
            return await self._owned_native_task(self._native_post(path, description, body=body))
        url = f"{self.BASE_URL}{path}"
        max_retries = 2
        for attempt in range(max_retries):
            try:
                token = await self.get_access_token()
                client = await self._get_client()

                params = {"access_token": token}
                if extra_params:
                    params.update(extra_params)
                if self._ingress_bytes is not None:
                    if path not in ("/cgi-bin/kf/account/list", "/cgi-bin/kf/sync_msg", "/cgi-bin/kf/service_state/get",
                                    "/cgi-bin/kf/customer/batchget"):
                        raise RuntimeError("KF_INGRESS_ENDPOINT_NOT_ALLOWED")
                    data = await self._ingress_json(method, url, params=params, body=json_body)
                    code = data.get("errcode")
                    if type(code) is not int:
                        raise RuntimeError("KF_INGRESS_REPLY_CODE_INVALID")
                    if code in (40014, 42001):
                        self._invalidate_token()
                        continue
                    return data
                if method == "GET":
                    response = await client.get(url, params=params)
                else:
                    response = await client.post(url, params=params, json=json_body or {})

                if response.status_code != 200:
                    logger.warning(f"请求 {path} HTTP {response.status_code}: {response.text[:200]}")
                    return {"errcode": -1, "errmsg": f"HTTP {response.status_code}"}

                raw_text = response.text.strip()
                if not raw_text:
                    logger.warning(f"请求 {path} 返回空响应体")
                    return {"errcode": -1, "errmsg": "empty response body"}

                data = response.json()
                errcode = data.get("errcode", 0)

                if errcode in (40014, 42001):
                    logger.warning("微信客服 access_token 已过期，正在刷新")
                    self._invalidate_token()
                    continue

                return data
            except Exception as e:
                if self._ingress_bytes is not None:
                    logger.warning("KF pull HTTP failed: type={}", type(e).__name__)
                    # No raw exception string/response/request URL escapes this owner.
                    return {"errcode": -1, "errmsg": "KF_INGRESS_HTTP_UNAVAILABLE"}
                if attempt < max_retries - 1:
                    logger.warning(f"请求 {path} 重试 {attempt + 1}/{max_retries}: {e}")
                    await asyncio.sleep(1)
                else:
                    logger.error(f"请求 {path} 最终失败: {e}")
                    return {"errcode": -1, "errmsg": str(e)}
        return {"errcode": -1, "errmsg": "unknown error"}

    # ==================== 客服账号管理 ====================

    async def account_add(self, name: str, media_id: str) -> Dict[str, Any]:
        """
        创建客服账号。

        Args:
            name: 客服账号名称，不超过 16 个字符
            media_id: 客服头像临时素材 media_id（必须，通过 upload_media 获取）

        Returns:
            {"errcode": 0, "open_kfid": "wkxxxxxx"} 或错误
        """
        body = {"name": name, "media_id": media_id}
        result = await self._request("POST", "/cgi-bin/kf/account/add", json_body=body)
        if result.get("errcode", 0) != 0:
            errcode = result.get("errcode")
            errmsg = result.get("errmsg")
            hint = ""
            if errcode == 48002:
                hint = "。48002 错误，自建应用没有权限创建客服账号，检查要点：1. “微信客服”应用下的 “API” ，要勾选自建应用；2. 自建应用下需要手动创建第一个客服账号。"
            logger.error(f"微信客服创建账号失败: errcode={errcode}, errmsg={errmsg}{hint}")
        return result

    async def account_del(self, open_kfid: str) -> Dict[str, Any]:
        """删除客服账号。"""
        body = {"open_kfid": open_kfid}
        result = await self._request("POST", "/cgi-bin/kf/account/del", json_body=body)
        if result.get("errcode", 0) != 0:
            logger.error(f"微信客服删除账号失败: errcode={result.get('errcode')}, errmsg={result.get('errmsg')}")
        return result

    async def account_update(self, open_kfid: str, name: Optional[str] = None, media_id: Optional[str] = None) -> Dict[str, Any]:
        """更新客服账号名称/头像，未提供的字段保持不变。"""
        body: Dict[str, Any] = {"open_kfid": open_kfid}
        if name is not None:
            body["name"] = name
        if media_id is not None:
            body["media_id"] = media_id
        result = await self._request("POST", "/cgi-bin/kf/account/update", json_body=body)
        if result.get("errcode", 0) != 0:
            logger.error(f"微信客服更新账号失败: errcode={result.get('errcode')}, errmsg={result.get('errmsg')}")
        return result

    async def account_list(self, offset: int = 0, limit: int = 100) -> Dict[str, Any]:
        """
        获取客服账号列表。

        Returns:
            {"errcode": 0, "account_list": [{"open_kfid": "...", "name": "...", "avatar": "..."}]}
        """
        body = {"offset": offset, "limit": limit}
        result = await self._request("POST", "/cgi-bin/kf/account/list", json_body=body)
        if result.get("errcode", 0) != 0 and self._ingress_bytes is None:
            logger.error(f"微信客服获取账号列表失败: errcode={result.get('errcode')}, errmsg={result.get('errmsg')}")
        return result

    async def add_contact_way(self, open_kfid: str, scene: str) -> Dict[str, Any]:
        """
        获取客服账号的接待二维码/链接。

        Args:
            open_kfid: 客服账号 ID
            scene: 场景值，[0-9a-zA-Z_-]* 且长度不超过 32 字节，用于区分客户来源

        Returns:
            {"errcode": 0, "url": "https://work.weixin.qq.com/kfid/xxx"}
        """
        body = {"open_kfid": open_kfid, "scene": scene}
        result = await self._request("POST", "/cgi-bin/kf/add_contact_way", json_body=body)
        if result.get("errcode", 0) != 0:
            logger.error(f"微信客服获取接待二维码失败: errcode={result.get('errcode')}, errmsg={result.get('errmsg')}")
        return result

    # ==================== 通讯录成员 ====================

    async def get_user(self, userid: str) -> Dict[str, Any]:
        """
        查询企业微信成员，用于校验人工接待人员 userid 是否在通讯录中存在。

        Args:
            userid: 成员 userid（大小写敏感）

        Returns:
            {"errcode": 0, "userid": "...", "name": "...", ...} 或错误
        """
        result = await self._request("GET", "/cgi-bin/user/get", extra_params={"userid": userid})
        if result.get("errcode", 0) != 0:
            # userid 输入错误（如 errcode=60111 userid not found）属于业务输入问题，非系统故障，记 info 即可
            logger.info(
                f"微信客服查询成员失败: userid={userid}, "
                f"errcode={result.get('errcode')}, errmsg={result.get('errmsg')}"
            )
        return result

    # ==================== 接待人员管理 ====================

    async def servicer_add(self, open_kfid: str, userid_list: List[str]) -> Dict[str, Any]:
        """
        添加客服账号接待人员（单次最多 100 个，超过需分批）。

        Args:
            open_kfid: 客服账号 ID
            userid_list: 接待人员 userid 列表

        Returns:
            {"errcode": 0, "result_list": [{"userid": "...", "errcode": 0, "errmsg": "success"}]}
        """
        body = {"open_kfid": open_kfid, "userid_list": userid_list}
        result = await self._request("POST", "/cgi-bin/kf/servicer/add", json_body=body)
        if result.get("errcode", 0) != 0:
            logger.error(
                f"微信客服添加接待人员失败: open_kfid={open_kfid}, "
                f"errcode={result.get('errcode')}, errmsg={result.get('errmsg')}"
            )
        return result

    async def servicer_del(self, open_kfid: str, userid_list: List[str]) -> Dict[str, Any]:
        """
        从客服账号删除接待人员（单次最多 100 个，超过需分批）。

        Args:
            open_kfid: 客服账号 ID
            userid_list: 待删除接待人员 userid 列表

        Returns:
            {"errcode": 0, "result_list": [{"userid": "...", "errcode": 0, "errmsg": "success"}]}
        """
        body = {"open_kfid": open_kfid, "userid_list": userid_list}
        result = await self._request("POST", "/cgi-bin/kf/servicer/del", json_body=body)
        if result.get("errcode", 0) != 0:
            logger.error(
                f"微信客服删除接待人员失败: open_kfid={open_kfid}, "
                f"errcode={result.get('errcode')}, errmsg={result.get('errmsg')}"
            )
        return result

    async def servicer_list(self, open_kfid: str) -> Dict[str, Any]:
        """
        获取客服账号接待人员列表。

        Args:
            open_kfid: 客服账号 ID

        Returns:
            {"errcode": 0, "servicer_list": [{"userid": "...", "status": 0}]}
        """
        result = await self._request("GET", "/cgi-bin/kf/servicer/list", extra_params={"open_kfid": open_kfid})
        if result.get("errcode", 0) != 0:
            logger.error(
                f"微信客服获取接待人员列表失败: open_kfid={open_kfid}, "
                f"errcode={result.get('errcode')}, errmsg={result.get('errmsg')}"
            )
        return result

    # ==================== 消息同步 ====================

    async def sync_msg(self, open_kfid: str, cursor: str = "", limit: int = 100, voice_format: int = 0) -> Dict[str, Any]:
        """
        拉取消息列表。

        Args:
            open_kfid: 客服账号 ID
            cursor: 上一次拉取的 next_cursor，首次为空
            limit: 本次拉取的消息条数，最大 1000
            voice_format: 语音消息格式，0=amr（AMR-NB，8kHz），1=pcm（PCM，16kHz，16bit，单声道）。
                默认使用 1（PCM 16kHz），音质更佳，有利于 ASR 识别。

        Returns:
            {
                "errcode": 0,
                "msg_list": [...],
                "has_more": 0,
                "next_cursor": "..."
            }
        """
        body = {"open_kfid": open_kfid, "cursor": cursor, "limit": min(limit, 1000), "voice_format": voice_format}
        result = await self._request("POST", "/cgi-bin/kf/sync_msg", json_body=body)
        if self._ingress_bytes is None:
            logger.info(
                f"微信客服 sync_msg 响应: errcode={result.get('errcode')}, errmsg={result.get('errmsg')}, "
                f"msg_count={len(result.get('msg_list', []))}, has_more={result.get('has_more')}, "
                f"next_cursor={result.get('next_cursor', '')[:20]}..."
            )
        return result

    # ==================== 消息发送 ====================

    async def send_msg(self, touser: str, open_kfid: str, msgtype: str, content: Dict[str, Any]) -> Dict[str, Any]:
        """
        发送消息给外部微信用户。

        Args:
            touser: 外部用户 external_userid
            open_kfid: 客服账号 ID
            msgtype: 消息类型（text/image/voice/file/link）
            content: 消息内容，如 {"content": "你好"}

        Returns:
            API 响应
        """
        body = {
            "touser": touser,
            "open_kfid": open_kfid,
            "msgtype": msgtype,
            msgtype: content,
        }
        result = await self._request("POST", "/cgi-bin/kf/send_msg", json_body=body)
        if result.get("errcode", 0) != 0:
            logger.error(f"微信客服发送消息失败: errcode={result.get('errcode')}, errmsg={result.get('errmsg')}")
        return result

    # ==================== 事件消息 ====================

    async def send_welcome(self, code: str, msgtype: str, content: Dict[str, Any]) -> Dict[str, Any]:
        """
        发送欢迎语（在 enter_session 事件中使用）。

        Args:
            code: enter_session 回调中的 Code 字段
            msgtype: 消息类型
            content: 消息内容
        """
        body = {"code": code, "msgtype": msgtype, msgtype: content}
        result = await self._request("POST", "/cgi-bin/kf/send_msg_on_event", json_body=body)
        if result.get("errcode", 0) != 0:
            logger.error(f"微信客服发送欢迎语失败: errcode={result.get('errcode')}, errmsg={result.get('errmsg')}")
        return result

    # ==================== 会话状态 ====================

    async def trans_service_state(
        self,
        open_kfid: str,
        external_userid: str,
        service_state: int,
        servicer_userid: str = "",
    ) -> Dict[str, Any]:
        """
        变更会话状态。

        Args:
            open_kfid: 客服账号 ID
            external_userid: 外部用户 ID
            service_state: 0=未处理, 1=智能助手接待, 2=待接入池, 3=人工接待, 4=已结束
            servicer_userid: 接待人员企微 userid（service_state=3 时需要）
        """
        body = {
            "open_kfid": open_kfid,
            "external_userid": external_userid,
            "service_state": service_state,
        }
        if servicer_userid:
            body["servicer_userid"] = servicer_userid
        result = await self._request("POST", "/cgi-bin/kf/service_state/trans", json_body=body)
        if result.get("errcode", 0) != 0:
            logger.error(
                f"微信客服变更会话状态失败: errcode={result.get('errcode')}, errmsg={result.get('errmsg')}"
            )
        return result

    async def get_service_state(self, open_kfid: str, external_userid: str) -> Dict[str, Any]:
        """获取会话状态"""
        body = {"open_kfid": open_kfid, "external_userid": external_userid}
        return await self._request("POST", "/cgi-bin/kf/service_state/get", json_body=body)

    # ==================== 客户信息 ====================

    async def get_customer_info(self, external_userid: str, need_enter_session_context: int = 0) -> Dict[str, Any]:
        """
        获取客户信息（批量接口，每次查一个）。

        Args:
            external_userid: 客户 external_userid
            need_enter_session_context: 是否需要返回进入会话上下文，0/1

        Returns:
            {
                "errcode": 0,
                "customer_list": [{"external_userid": "wmXXX", "nickname": "昵称", "avatar": "...", "gender": 1, "unionid": "..."}],
                "invalid_external_userid": []
            }
        """
        body = {"external_userid_list": [external_userid], "need_enter_session_context": need_enter_session_context}
        return await self._request("POST", "/cgi-bin/kf/customer/batchget", json_body=body)

    # ==================== 临时素材 ====================

    async def upload_media(self, file_path: str, media_type: str = "file") -> Dict[str, Any]:
        """
        上传临时素材，获取 media_id。

        Args:
            file_path: 本地文件路径
            media_type: 素材类型（image/voice/video/file）

        Returns:
            {"errcode": 0, "type": "file", "media_id": "MEDIA_ID", "created_at": 123}
        """
        if self.native_write_enabled:
            return await self._owned_native_task(self._native_upload(file_path, media_type))
        url = f"{self.BASE_URL}/cgi-bin/media/upload"
        max_retries = 2
        for attempt in range(max_retries):
            try:
                token = await self.get_access_token()
                client = await self._get_client()
                with open(file_path, "rb") as f:
                    files = {"media": (file_path.split("/")[-1], f)}
                    response = await client.post(
                        url,
                        params={"access_token": token, "type": media_type},
                        files=files,
                    )
                data = response.json()
                errcode = data.get("errcode", 0)
                if errcode in (40014, 42001):
                    self._invalidate_token()
                    continue
                if errcode != 0:
                    logger.error(f"上传临时素材失败: errcode={errcode}, errmsg={data.get('errmsg')}")
                return data
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"上传素材重试 {attempt + 1}/{max_retries}: {e}")
                    await asyncio.sleep(1)
                else:
                    logger.error(f"上传素材最终失败: {e}")
                    return {"errcode": -1, "errmsg": str(e)}
        return {"errcode": -1, "errmsg": "unknown error"}

    async def _native_upload(self, file_path, media_type):
        if media_type not in ('image', 'voice', 'video', 'file'):
            raise NativeWritePreparationFailed('KF_NATIVE_MEDIA_TYPE_INVALID')

        def read_media():
            try:
                descriptor = os.open(file_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, 'rb') as media:
                    info = os.fstat(media.fileno())
                    if not stat.S_ISREG(info.st_mode):
                        raise NativeWriteError('KF_NATIVE_MEDIA_UNSAFE')
                    if not 0 < info.st_size <= MAX_MEDIA_SIZE:
                        raise NativeWritePreparationFailed('KF_NATIVE_MEDIA_SIZE_INVALID')
                    content = media.read(MAX_MEDIA_SIZE + 1)
                if not 0 < len(content) <= MAX_MEDIA_SIZE:
                    raise NativeWritePreparationFailed('KF_NATIVE_MEDIA_SIZE_INVALID')
                return content
            except OSError as error:
                if error.errno == errno.ELOOP:
                    raise NativeWriteError('KF_NATIVE_MEDIA_UNSAFE') from None
                raise NativeWritePreparationFailed('KF_NATIVE_MEDIA_UNAVAILABLE') from None

        # The original file bytes, not a mutable path, are the physical upload.
        content = await self._drain_task(asyncio.create_task(asyncio.to_thread(read_media)))
        description = {'file_digest': hashlib.sha256(content).hexdigest(),
                       'file_bytes': len(content), 'media_type': media_type}
        result = await self._native_post('/cgi-bin/media/upload', description,
            files={'media': (os.path.basename(file_path), content)}, extra_params={'type': media_type})
        return result

    async def download_media(self, media_id: str) -> Tuple[bytes, str]:
        """
        下载临时素材（图片/语音/文件），返回二进制内容与 Content-Type（20MB 限制）。

        微信临时素材接口：GET /cgi-bin/media/get?access_token=xxx&media_id=xxx

        - 图片素材返回 Content-Type: image/jpeg 等
        - 语音素材返回 Content-Type: audio/amr 等
        - 文件素材返回 Content-Type: application/octet-stream 等
        - 如果下载失败，微信会返回 JSON 错误（errcode != 0）

        Args:
            media_id: 临时素材 media_id

        Returns:
            (content, content_type) 二元组。content_type 来自 HTTP 响应头，
            可作为辅助线索用于音频格式识别。

        Raises:
            RuntimeError: 下载失败时抛出
        """
        if self._ingress_bytes is not None:
            return await self._download_native_media(media_id)
        url = f"{self.BASE_URL}/cgi-bin/media/get"
        max_retries = 2
        for attempt in range(max_retries):
            try:
                token = await self.get_access_token()
                client = await self._get_client()

                response = await client.get(
                    url,
                    params={"access_token": token, "media_id": media_id},
                )

                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")

                # 微信下载错误时返回 JSON 而非二进制内容
                content_type = response.headers.get("Content-Type", "")
                if "application/json" in content_type or "text/plain" in content_type:
                    data = response.json()
                    errcode = data.get("errcode", 0)
                    if errcode in (40014, 42001):
                        self._invalidate_token()
                        continue
                    if errcode != 0:
                        raise RuntimeError(
                            f"下载素材失败: errcode={errcode}, errmsg={data.get('errmsg')}"
                        )
                    raise RuntimeError(f"意外 JSON 响应: {data}")

                content = response.content
                if len(content) > MAX_MEDIA_SIZE:
                    raise RuntimeError(
                        f"素材大小 {len(content)} bytes 超过 20MB 限制"
                    )
                return content, content_type

            except RuntimeError:
                raise
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"下载素材重试 {attempt + 1}/{max_retries}: {e}")
                    await asyncio.sleep(1)
                else:
                    logger.error(f"下载素材最终失败: {e}")
                    raise RuntimeError(f"下载素材失败: {e}") from e

    async def _download_native_media(self, media_id):
        token=await self.get_access_token()
        client=await self._get_client()
        data=bytearray()
        async with client.stream('GET', self.BASE_URL+'/cgi-bin/media/get',
                params={'access_token':token,'media_id':media_id}) as response:
            if response.status_code!=200:
                raise RuntimeError('KF_MEDIA_HTTP_STATUS')
            content_type=response.headers.get('Content-Type','')
            async for chunk in response.aiter_bytes():
                if len(data)+len(chunk)>self._native_media_max_bytes:
                    raise RuntimeError('KF_MEDIA_TOO_LARGE')
                data.extend(chunk)
        if not data or 'json' in content_type.lower() or 'text/plain' in content_type.lower():
            raise RuntimeError('KF_MEDIA_NOT_AUDIO')
        return bytes(data),content_type
