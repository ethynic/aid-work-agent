"""
语音转文字工具

使用阿里云智能语音交互服务，将语音音频文件转为文字。
支持一句话识别（≤60秒短音频），适用于微信语音消息场景。
"""

import asyncio
import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any, Dict, Optional
from urllib import parse

import aiohttp
from loguru import logger
from pydantic import BaseModel, Field

from src.config.settings import settings
from src.tools.base import BaseTool


class SpeechToTextInput(BaseModel):
    """语音转文字参数"""
    audio_content: str = Field(..., description="音频文件内容（base64编码）或本地文件绝对路径")
    format: Optional[str] = Field("mp3", description="音频格式：mp3/wav/opus，默认mp3")
    sample_rate: Optional[int] = Field(16000, description="采样率：8000或16000，默认16000")
    language: Optional[str] = Field("zh_cn", description="识别语言：zh_cn中文普通话，en英文，默认zh_cn")


class SpeechToTextTool(BaseTool):
    """语音转文字工具 - 基于阿里云智能语音交互一句话识别"""

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
            format: 音频格式（mp3/wav/opus）
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
            return {"success": False, "error": "请提供音频文件内容（base64编码）或本地文件路径"}

        asr_config = settings.tools.asr
        if not asr_config.aliyun_access_key_id or not asr_config.aliyun_access_key_secret:
            return {"success": False, "error": "未配置阿里云 ASR 凭证，请在环境变量或配置文件中设置 ALIYUN_ASR_ACCESS_KEY_ID 和 ALIYUN_ASR_ACCESS_KEY_SECRET"}

        if not asr_config.aliyun_appkey:
            return {"success": False, "error": "未配置阿里云 ASR AppKey，请设置 ALIYUN_ASR_APPKEY"}

        # 判断是 base64 内容还是文件路径
        audio_bytes = None
        if audio_content.startswith("/") or audio_content.startswith("./") or audio_content.startswith("../"):
            # 本地文件路径
            import os
            if not os.path.exists(audio_content):
                return {"success": False, "error": f"音频文件不存在: {audio_content}"}
            try:
                loop = asyncio.get_event_loop()
                audio_bytes = await loop.run_in_executor(
                    None, self._read_file, audio_content
                )
            except Exception as e:
                return {"success": False, "error": f"读取音频文件失败: {str(e)}"}
        else:
            # base64 编码内容
            try:
                audio_bytes = base64.b64decode(audio_content)
            except Exception as e:
                return {"success": False, "error": f"base64解码失败: {str(e)}"}

        if len(audio_bytes) > 10 * 1024 * 1024:  # 10MB 限制
            return {"success": False, "error": "音频文件过大，最大支持 10MB（约60秒语音）"}

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
        """调用阿里云一句话识别 REST API"""
        try:
            # 构建请求 body（二进制音频）
            body = audio_bytes

            # 计算 body 的 MD5
            body_md5 = base64.b64encode(hashlib.md5(body).digest()).decode("utf-8")

            # 当前时间戳（GMT）
            date_str = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())

            # 构建 UUID 作为请求 ID
            request_id = str(uuid.uuid4())

            # 构建要签名的字符串
            # 格式: POST\nAccept:\nContent-MD5:\nContent-Type:\nDate:\nx-acs-signature-method:\nx-acs-signature-nonce:\nPathAndParameters
            string_to_sign = (
                f"POST\n"
                f"Accept: application/json\n"
                f"Content-MD5: {body_md5}\n"
                f"Content-Type: application/octet-stream\n"
                f"Date: {date_str}\n"
                f"x-acs-signature-method: HMAC-SHA1\n"
                f"x-acs-signature-nonce: {request_id}\n"
                f"/api/sls/v1/speech/transcription"
            )

            # 计算签名
            signature = hmac.new(
                access_key_secret.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                hashlib.sha1,
            ).digest()
            signature_b64 = base64.b64encode(signature).decode("utf-8")

            # 构建 Authorization header
            authorization = f"Dataplus {access_key_id}:{signature_b64}"

            # 构建请求 URL
            url = f"https://{endpoint}/api/sls/v1/speech/transcription"

            # 构建 query 参数
            query_params = {
                "appkey": appkey,
                "format": audio_format,
                "sample_rate": str(sample_rate),
                "enable_punctuation_prediction": "true",
                "enable_inverse_text_normalization": "true",
            }
            if language:
                query_params["language"] = language

            full_url = f"{url}?{parse.urlencode(query_params)}"

            headers = {
                "Accept": "application/json",
                "Content-MD5": body_md5,
                "Content-Type": "application/octet-stream",
                "Date": date_str,
                "Authorization": authorization,
                "x-acs-signature-method": "HMAC-SHA1",
                "x-acs-signature-nonce": request_id,
            }

            # 发送请求
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(full_url, headers=headers, data=body) as resp:
                    resp_text = await resp.text()

                    if resp.status != 200:
                        logger.error(f"阿里云 ASR 请求失败: status={resp.status}, body={resp_text}")
                        return {
                            "success": False,
                            "error": f"阿里云 ASR 服务异常: HTTP {resp.status}",
                        }

                    result_json = json.loads(resp_text)
                    status_code = result_json.get("status", -1)

                    if status_code == 0:
                        # 识别成功
                        text = result_json.get("result", "")
                        logger.info(f"语音转文字成功，识别结果: {text[:100]}")
                        return {
                            "success": True,
                            "text": text,
                            "message": "语音转文字成功",
                        }
                    else:
                        # 识别失败
                        error_msg = result_json.get("message", "未知错误")
                        logger.error(f"阿里云 ASR 识别失败: status={status_code}, message={error_msg}")
                        return {
                            "success": False,
                            "error": f"语音识别失败: {error_msg} (code={status_code})",
                        }

        except aiohttp.ClientError as e:
            logger.error(f"阿里云 ASR 网络错误: {e}")
            return {"success": False, "error": f"阿里云 ASR 网络错误: {str(e)}"}
        except Exception as e:
            logger.error(f"阿里云 ASR 未知错误: {e}")
            return {"success": False, "error": f"语音转文字失败: {str(e)}"}
