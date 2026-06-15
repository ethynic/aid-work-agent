"""
语音转文字工具

使用阿里云智能语音交互服务（一句话识别 RESTful API），将语音音频文件转为文字。
支持最长 60 秒的短音频，适用于微信语音消息场景。

官方文档：https://help.aliyun.com/zh/isi/developer-reference/restful-api-2
"""

import asyncio
import base64
import json
import time
import uuid
from typing import Any, Dict, Optional

import aiohttp
from loguru import logger
from pydantic import BaseModel, Field

from src.config.settings import settings
from src.tools.base import BaseTool
from src.utils import sanitize_error_info


class SpeechToTextInput(BaseModel):
    """语音转文字参数"""
    audio_content: str = Field(..., description="音频文件内容（base64编码）或本地文件绝对路径")
    format: Optional[str] = Field("mp3", description="音频格式：mp3/wav/opus/pcm/amr/aac，默认mp3")
    sample_rate: Optional[int] = Field(16000, description="采样率：8000或16000，默认16000")
    language: Optional[str] = Field("zh_cn", description="识别语言：zh_cn中文普通话，en英文，默认zh_cn")


# ------------------------------------------------------------------
# Token 缓存（单例级别，避免每次识别都获取 Token）
# ------------------------------------------------------------------
_TOKEN_CACHE: Dict[str, Any] = {
    "token": "",
    "expire_at": 0.0,  # 提前 600s 视为过期
}
_TOKEN_LOCK = asyncio.Lock()

# 阿里云 NLS GetToken 服务域名（与一句话识别域名不同：meta 是 OpenAPI 风格）
_NLS_META_DOMAIN = "nls-meta.cn-shanghai.aliyuncs.com"


class SpeechToTextTool(BaseTool):
    """语音转文字工具 - 基于阿里云智能语音交互一句话识别 RESTful API"""

    name = "speech_to_text"
    description = "将语音音频转为文字，支持中文普通话和英文。适用于语音消息识别，音频时长不超过60秒。参数需要提供音频文件的base64编码内容或本地文件路径。"
    usage_guide = "当需要处理语音消息时调用此工具。提供音频文件的base64编码内容或本地文件路径。"
    display_name = "语音转文字"
    category = "asr"
    InputModel = SpeechToTextInput

    def get_display_name(self, tool_args: Optional[Dict[str, Any]] = None) -> str:
        base = self.display_name
        if tool_args:
            lang = tool_args.get("language", "zh_cn")
            lang_label = "中文" if lang == "zh_cn" else "英文"
            return f"{base}（{lang_label}）"
        return base

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行语音转文字

        Args:
            audio_content: 音频文件内容（base64编码）或本地文件绝对路径
            format: 音频格式（mp3/wav/opus/pcm/amr/aac）
            sample_rate: 采样率（8000或16000）
            language: 识别语言（zh_cn/en）

        Returns:
            识别结果，包含识别文字
        """
        audio_content = kwargs.get("audio_content", "")
        audio_format = kwargs.get("format", "mp3")
        sample_rate = kwargs.get("sample_rate", 16000)
        language = kwargs.get("language", "zh_cn")

        if not audio_content:
            return {
                "success": False,
                "error": "请提供音频文件内容（base64编码）或本地文件路径",
            }

        asr_config = settings.tools.asr
        if not asr_config.aliyun_access_key_id or not asr_config.aliyun_access_key_secret:
            logger.error("阿里云 ASR 凭证未配置（ALIYUN_ASR_ACCESS_KEY_ID/SECRET）")
            return {
                "success": False,
                "error": "未配置阿里云 ASR 凭证，请在环境变量或配置文件中设置 ALIYUN_ASR_ACCESS_KEY_ID 和 ALIYUN_ASR_ACCESS_KEY_SECRET",
            }

        if not asr_config.aliyun_appkey:
            logger.error("阿里云 ASR AppKey 未配置（ALIYUN_ASR_APPKEY）")
            return {
                "success": False,
                "error": "未配置阿里云 ASR AppKey，请设置 ALIYUN_ASR_APPKEY",
            }

        # 判断是 base64 内容还是文件路径
        audio_bytes = None
        if audio_content.startswith("/") or audio_content.startswith("./") or audio_content.startswith("../"):
            # 本地文件路径
            import os
            if not os.path.exists(audio_content):
                return {
                    "success": False,
                    "error": f"音频文件不存在: {audio_content}",
                }
            try:
                loop = asyncio.get_event_loop()
                audio_bytes = await loop.run_in_executor(
                    None, self._read_file, audio_content
                )
            except Exception as e:
                logger.error(f"读取音频文件失败: {e}", exc_info=True)
                return {
                    "success": False,
                    "error": "读取音频文件失败",
                    "debug": sanitize_error_info(str(e)),
                }
        else:
            # base64 编码内容
            try:
                audio_bytes = base64.b64decode(audio_content)
            except Exception as e:
                logger.error(f"base64 解码失败: {e}", exc_info=True)
                return {
                    "success": False,
                    "error": "base64 解码失败，请提供合法的 base64 音频内容",
                    "debug": sanitize_error_info(str(e)),
                }

        if len(audio_bytes) > 10 * 1024 * 1024:  # 10MB 限制
            return {
                "success": False,
                "error": "音频文件过大，最大支持 10MB（约60秒语音）。如需更长音频请使用录音文件识别。",
            }

        return await self._call_aliyun_asr(
            audio_bytes=audio_bytes,
            audio_format=audio_format,
            sample_rate=sample_rate,
            language=language,
            access_key_id=asr_config.aliyun_access_key_id,
            access_key_secret=asr_config.aliyun_access_key_secret,
            appkey=asr_config.aliyun_appkey,
            endpoint=asr_config.endpoint,
        )

    def _read_file(self, path: str) -> bytes:
        """同步读取文件（用于 run_in_executor）"""
        with open(path, "rb") as f:
            return f.read()

    # ------------------------------------------------------------------
    # Token 管理：单例缓存 + 自动刷新（提前 10 分钟过期）
    # ------------------------------------------------------------------
    async def _get_or_refresh_token(
        self,
        access_key_id: str,
        access_key_secret: str,
        appkey: str,
    ) -> str:
        """获取/刷新阿里云 NLS Token（带缓存）"""
        now = time.time()
        if _TOKEN_CACHE["token"] and now < _TOKEN_CACHE["expire_at"] - 600:
            return _TOKEN_CACHE["token"]

        async with _TOKEN_LOCK:
            # 双重检查：进锁之后再判一次，避免并发刷新
            now = time.time()
            if _TOKEN_CACHE["token"] and now < _TOKEN_CACHE["expire_at"] - 600:
                return _TOKEN_CACHE["token"]

            url = f"https://{_NLS_META_DOMAIN}/pop/2018-05-18/GetToken"
            params = {
                "AccessKeyId": access_key_id,
                "Action": "GetToken",
                "AppKey": appkey,
                "Format": "JSON",
                "RegionId": "cn-shanghai",
                "SignatureMethod": "HMAC-SHA1",
                "SignatureNonce": str(uuid.uuid4()),
                "SignatureVersion": "1.0",
                "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "Version": "2018-05-18",
            }
            # 签名：按 Key 排序，拼 query string，HMAC-SHA1
            sorted_query = "&".join(
                f"{parse_quote(k)}={parse_quote(v, safe='~')}"
                for k, v in sorted(params.items())
            )
            string_to_sign = (
                f"GET&{parse_quote('/', safe='~')}&{parse_quote(sorted_query, safe='~')}"
            )
            from hashlib import sha1
            import hmac
            signature = base64.b64encode(
                hmac.new(
                    (access_key_secret + "&").encode("utf-8"),
                    string_to_sign.encode("utf-8"),
                    sha1,
                ).digest()
            ).decode("utf-8")
            params["Signature"] = signature

            from urllib.parse import urlencode
            full_url = f"{url}?{urlencode(params)}"

            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(full_url) as resp:
                    resp_text = await resp.text()
                    if resp.status != 200:
                        raise RuntimeError(
                            f"GetToken HTTP {resp.status}: {resp_text[:300]}"
                        )
                    data = json.loads(resp_text)
                    token_info = data.get("Token") or {}
                    token_id = token_info.get("Id")
                    expire_time = int(token_info.get("ExpireTime", 0))
                    if not token_id or not expire_time:
                        raise RuntimeError(f"GetToken 响应异常: {resp_text[:300]}")

                    _TOKEN_CACHE["token"] = token_id
                    _TOKEN_CACHE["expire_at"] = expire_time
                    logger.info(
                        f"后端日志：NLS Token 刷新成功，过期时间戳={expire_time}"
                    )
                    return token_id

    # ------------------------------------------------------------------
    # 一句话识别 RESTful API 调用
    # ------------------------------------------------------------------
    async def _call_aliyun_asr(
        self,
        audio_bytes: bytes,
        audio_format: str,
        sample_rate: int,
        language: str,
        access_key_id: str,
        access_key_secret: str,
        appkey: str,
        endpoint: str,
    ) -> Dict[str, Any]:
        """调用阿里云 NLS 一句话识别 RESTful API

        官方文档：https://help.aliyun.com/zh/isi/developer-reference/restful-api-2
        """
        try:
            # 1) 获取 Token
            token = await self._get_or_refresh_token(
                access_key_id, access_key_secret, appkey
            )

            # 2) 构建 URL（注意：endpoint 已含 nls-gateway-cn-shanghai）
            base_url = endpoint if endpoint.startswith("http") else f"https://{endpoint}"
            url = f"{base_url.rstrip('/')}/stream/v1/asr"
            query_params = {
                "appkey": appkey,
                "format": audio_format,
                "sample_rate": str(sample_rate),
                "enable_punctuation_prediction": "true",
                "enable_inverse_text_normalization": "true",
            }
            if language:
                query_params["language"] = language

            from urllib.parse import urlencode
            full_url = f"{url}?{urlencode(query_params)}"

            # 3) 发送请求（X-NLS-Token 鉴权）
            headers = {
                "X-NLS-Token": token,
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(audio_bytes)),
            }

            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(full_url, headers=headers, data=audio_bytes) as resp:
                    resp_text = await resp.text()

                    if resp.status != 200:
                        logger.error(
                            f"后端日志：阿里云 ASR HTTP 异常 status={resp.status}, body={resp_text[:500]}"
                        )
                        return {
                            "success": False,
                            "error": f"阿里云 ASR 服务异常: HTTP {resp.status}",
                            "debug": sanitize_error_info(resp_text),
                        }

                    result_json = json.loads(resp_text)
                    status_code = result_json.get("status", -1)

                    # 官方成功码：20000000
                    if status_code == 20000000:
                        text = result_json.get("result", "")
                        logger.info(f"后端日志：语音转文字成功 text={text[:100]}")
                        return {
                            "success": True,
                            "text": text,
                            "message": "语音转文字成功",
                        }
                    else:
                        error_msg = result_json.get("message", "未知错误")
                        task_id = result_json.get("task_id", "")
                        logger.error(
                            f"后端日志：阿里云 ASR 识别失败 status={status_code}, "
                            f"task_id={task_id}, message={error_msg}"
                        )
                        return {
                            "success": False,
                            "error": f"语音识别失败: {error_msg} (code={status_code})",
                            "debug": sanitize_error_info(
                                f"{error_msg} (task_id={task_id})"
                            ),
                        }

        except aiohttp.ClientError as e:
            logger.error(f"后端日志：阿里云 ASR 网络错误: {e}", exc_info=True)
            return {
                "success": False,
                "error": "阿里云 ASR 网络错误，请稍后重试",
                "debug": sanitize_error_info(str(e)),
            }
        except Exception as e:
            logger.error(f"后端日志：阿里云 ASR 未知错误: {e}", exc_info=True)
            return {
                "success": False,
                "error": "语音转文字失败",
                "debug": sanitize_error_info(str(e)),
            }


def parse_quote(s: str, safe: str = "") -> str:
    """URL 编码（兼容 from urllib.parse import quote 行为）"""
    from urllib.parse import quote
    return quote(str(s), safe=safe)
