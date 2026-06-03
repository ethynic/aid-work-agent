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
import time
from typing import Any, Dict, Optional

import httpx
from loguru import logger


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

    # ==================== HTTP 客户端 ====================

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )
        return self._http_client

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

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
                    logger.warning(f"获取 access_token 重试 {attempt + 1}/{max_retries}: {e}")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"获取 access_token 最终失败: {e}")
                    raise

    def _invalidate_token(self):
        self._access_token = None
        self._token_expires = 0

    # ==================== 通用请求 ====================

    async def _request(self, method: str, path: str, json_body: Optional[Dict] = None) -> Dict[str, Any]:
        """发送请求（带 token 自动刷新重试）"""
        url = f"{self.BASE_URL}{path}"
        max_retries = 2
        for attempt in range(max_retries):
            try:
                token = await self.get_access_token()
                client = await self._get_client()

                params = {"access_token": token}
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
                if attempt < max_retries - 1:
                    logger.warning(f"请求 {path} 重试 {attempt + 1}/{max_retries}: {e}")
                    await asyncio.sleep(1)
                else:
                    logger.error(f"请求 {path} 最终失败: {e}")
                    return {"errcode": -1, "errmsg": str(e)}
        return {"errcode": -1, "errmsg": "unknown error"}

    # ==================== 消息同步 ====================

    async def sync_msg(self, open_kfid: str, cursor: str = "", limit: int = 100, voice_format: int = 0) -> Dict[str, Any]:
        """
        拉取消息列表。

        Args:
            open_kfid: 客服账号 ID
            cursor: 上一次拉取的 next_cursor，首次为空
            limit: 本次拉取的消息条数，最大 1000
            voice_format: 语音消息格式，0=amr，1=mp3

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

    async def get_customer_info(self, open_kfid: str, external_userid: str) -> Dict[str, Any]:
        """
        获取客户信息。

        Returns:
            {
                "errcode": 0,
                "external_userid": "wmXXX",
                "nickname": "昵称",
                "avatar": "头像URL",
                "gender": 1,
                ...
            }
        """
        body = {"open_kfid": open_kfid, "external_userid": external_userid}
        return await self._request("POST", "/cgi-bin/kf/customer/get", json_body=body)

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
