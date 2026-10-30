"""渠道语音 ASR 通用链路单元测试（wecom/dingtalk/feishu 复刻 wecom_kf 语音支持）。

覆盖：
- _normalize_audio_for_asr：AMR/WAV 透传、PCM（content_type 提示）包装 WAV、
  SILK 解码 WAV（mock decode）、未知格式回退
- _transcribe_channel_voice：非语音直通、下载失败/异常、ASR 成功
  （"[ASR识别结果] " 前缀 + voice_meta 合入 raw_message）、ASR 失败保持占位
- 三渠道 parse_message 语音分支标记（is_voice / 占位文本 / TEXT 类型）
- wecom 渠道后台语音路径计费接线：识别成功 -> skip_save=False + add_asr_usage(1)；
  识别失败 -> skip_save=True 且不计费（对齐 wecom_kf 2736-2747 行口径）
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.message import ChannelType, MessageType, UnifiedMessage
from src.saas.api import channel_routes


# ---------- fixtures ----------

VOICE_XML = """<xml>
<MsgType>voice</MsgType>
<MediaId>media_voice_001</MediaId>
<Recognition>语音转文字内容</Recognition>
<Format>amr</Format>
<FromUserName>user123</FromUserName>
<ToUserName>agent456</ToUserName>
<CreateTime>1234567890</CreateTime>
<MsgId>msg_005</MsgId>
</xml>"""


def _make_unified_message(content: dict, raw_message: dict = None) -> UnifiedMessage:
    return UnifiedMessage(
        message_id="msg_voice_1",
        channel_type=ChannelType.WECOM,
        user_id="user_1",
        message_type=MessageType.TEXT,
        content=content,
        timestamp=datetime.now(),
        raw_message=raw_message if raw_message is not None else {"origin": 1},
    )


@pytest.fixture
def patched_asr(monkeypatch):
    """替换 _transcribe_voice_with_asr，返回可控 AsyncMock"""
    mock = AsyncMock(return_value="你好世界")
    monkeypatch.setattr(channel_routes, "_transcribe_voice_with_asr", mock)
    return mock


# ---------- _normalize_audio_for_asr ----------


class TestNormalizeAudioForAsr:
    def test_amr_passthrough(self):
        """AMR 有头格式透传，不做包装"""
        content = b"#!AMR\n" + b"\x00" * 32
        out, fmt, sr = channel_routes._normalize_audio_for_asr(content, "audio/amr")
        assert out == content
        assert fmt == "amr"

    def test_wav_passthrough(self):
        content = b"RIFF----WAVEfmt " + b"\x00" * 32
        out, fmt, _sr = channel_routes._normalize_audio_for_asr(content, "audio/wav")
        assert out == content
        assert fmt == "wav"

    def test_pcm_without_header_wrapped_as_wav(self):
        """无文件头原始 PCM（content_type 含 pcm 提示）包装为 WAV"""
        raw = b"\x01\x02\x03\x04" * 64
        out, fmt, sr = channel_routes._normalize_audio_for_asr(raw, "audio/pcm")
        assert fmt == "wav"
        assert sr == 16000
        assert out[:4] == b"RIFF"
        assert out[8:12] == b"WAVE"
        assert out != raw

    def test_silk_decoded_to_wav(self, monkeypatch):
        """SILK（阿里云 ASR 不支持）解码为 WAV"""
        captured = {}

        def fake_decode(data, sample_rate=16000):
            captured["sample_rate"] = sample_rate
            return b"RIFF----WAVEfmt " + b"\x00" * 32

        monkeypatch.setattr("src.utils.audio_format.decode_silk_to_wav", fake_decode)
        silk = b"\x02#!SILK_V3" + b"\x00" * 32
        out, fmt, sr = channel_routes._normalize_audio_for_asr(silk, "")
        assert fmt == "wav"
        assert sr == 16000
        assert out[:4] == b"RIFF"
        assert captured["sample_rate"] == 16000

    def test_silk_decode_failure_keeps_original(self, monkeypatch):
        """SILK 解码失败时保留原格式（不吞数据）"""
        def boom(data, sample_rate=16000):
            raise RuntimeError("no pysilk")

        monkeypatch.setattr("src.utils.audio_format.decode_silk_to_wav", boom)
        silk = b"\x02#!SILK_V3" + b"\x00" * 32
        out, fmt, _sr = channel_routes._normalize_audio_for_asr(silk, "")
        assert fmt.startswith("silk")
        assert out == silk


# ---------- _transcribe_channel_voice ----------


class TestTranscribeChannelVoice:
    async def test_non_voice_message_short_circuits(self, patched_asr):
        """非语音/微信 Recognition 已有文本时不触发下载与 ASR"""
        msg = _make_unified_message({"text": "普通文本"})
        called = AsyncMock(return_value=None)
        result = await channel_routes._transcribe_channel_voice(
            msg, "t1", called, "[Test]"
        )
        assert result is False
        called.assert_not_awaited()
        patched_asr.assert_not_awaited()

    async def test_recognition_text_skips_asr(self, patched_asr):
        """wecom Recognition 非空（text 非占位）直接用文本，不下载不 ASR"""
        msg = _make_unified_message({"text": "微信识别文本", "is_voice": True})
        called = AsyncMock(return_value=None)
        result = await channel_routes._transcribe_channel_voice(
            msg, "t1", called, "[Test]"
        )
        assert result is False
        called.assert_not_awaited()
        patched_asr.assert_not_awaited()

    async def test_download_failure_returns_false(self, patched_asr):
        msg = _make_unified_message({"text": "[语音消息]", "is_voice": True})
        result = await channel_routes._transcribe_channel_voice(
            msg, "t1", AsyncMock(return_value=None), "[Test]"
        )
        assert result is False
        assert msg.text == "[语音消息]"
        patched_asr.assert_not_awaited()

    async def test_download_exception_returns_false(self, patched_asr):
        msg = _make_unified_message({"text": "[语音消息]", "is_voice": True})
        result = await channel_routes._transcribe_channel_voice(
            msg, "t1", AsyncMock(side_effect=RuntimeError("net down")), "[Test]"
        )
        assert result is False
        assert msg.text == "[语音消息]"

    async def test_asr_success_sets_prefix_and_meta(self, patched_asr):
        """识别成功：text 替换为带前缀的识别文本，voice_meta 合入 raw_message"""
        msg = _make_unified_message(
            {"text": "[语音消息]", "is_voice": True, "media_id": "m1"},
            raw_message={"origin": 1},
        )

        async def download():
            return (b"#!AMR\n" + b"\x00" * 32, "audio/amr", {"voice_local_path": "/x/y.amr"})

        result = await channel_routes._transcribe_channel_voice(
            msg, "t1", download, "[Test]"
        )
        assert result is True
        assert msg.text == "[ASR识别结果] 你好世界"
        # ASR 收到 base64 音频与格式
        args = patched_asr.await_args
        import base64 as b64mod

        assert b64mod.b64decode(args.args[0]).startswith(b"#!AMR\n")
        assert args.args[1] == "amr"
        assert msg.raw_message["voice_meta"]["voice_local_path"] == "/x/y.amr"
        assert msg.raw_message["voice_meta"]["audio_format"] == "amr"
        assert msg.raw_message["origin"] == 1

    async def test_asr_failure_keeps_placeholder(self, monkeypatch):
        """识别失败：保持占位文本返回 False，不写 voice_meta"""
        monkeypatch.setattr(
            channel_routes, "_transcribe_voice_with_asr",
            AsyncMock(return_value="[语音消息]"),
        )
        msg = _make_unified_message({"text": "[语音消息]", "is_voice": True})

        async def download():
            return (b"#!AMR\n" + b"\x00" * 32, "audio/amr", {})

        result = await channel_routes._transcribe_channel_voice(
            msg, "t1", download, "[Test]"
        )
        assert result is False
        assert msg.text == "[语音消息]"
        assert "voice_meta" not in msg.raw_message


# ---------- parse_message 语音分支标记 ----------


class TestParseMessageVoiceMarkers:
    @pytest.mark.asyncio
    async def test_wecom_voice_marks_is_voice(self):
        from src.channels.wecom.adapter import WeComAdapter

        a = WeComAdapter(corp_id="ww_test", agent_id="1000001", secret="test_secret")
        msg = await a.parse_message({"body": VOICE_XML})
        assert msg.content["is_voice"] is True
        assert msg.content["media_id"] == "media_voice_001"
        assert msg.content["voice_format"] == "amr"

    @pytest.mark.asyncio
    async def test_dingtalk_audio_becomes_voice_text(self):
        """audio 从 FILE 改为 TEXT + 占位文本 + is_voice（对齐 wecom_kf 约定）"""
        from src.channels.dingtalk.adapter import DingTalkAdapter

        a = DingTalkAdapter(app_key="dk_test", app_secret="s" * 16)
        msg = await a.parse_message({
            "msgtype": "audio",
            "audio": {"downloadCode": "dl_audio", "fileName": "test.amr"},
            "msgId": "msg_audio",
            "createAt": 1700000000000,
            "conversationType": "1",
            "senderId": "user_001",
        })
        assert msg.message_type == MessageType.TEXT
        assert msg.text == "[语音消息]"
        assert msg.content["is_voice"] is True
        assert msg.content["download_code"] == "dl_audio"

    @pytest.mark.asyncio
    async def test_dingtalk_video_stays_file(self):
        from src.channels.dingtalk.adapter import DingTalkAdapter

        a = DingTalkAdapter(app_key="dk_test", app_secret="s" * 16)
        msg = await a.parse_message({
            "msgtype": "video",
            "video": {"downloadCode": "dl_video", "fileName": "test.mp4"},
            "msgId": "msg_video",
            "createAt": 1700000000000,
            "conversationType": "1",
            "senderId": "user_001",
        })
        assert msg.message_type == MessageType.FILE

    @pytest.mark.asyncio
    async def test_feishu_audio_becomes_voice_text(self):
        """飞书 audio 从降级占位改为语音分支（is_voice + fileKey 提取）"""
        from src.channels.feishu.adapter import FeishuAdapter

        a = FeishuAdapter(
            app_id="cli_test", app_secret="secret_test",
            verification_token="vt_test", encrypt_key="",
        )
        event = {
            "header": {"event_id": "evt1", "event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_id": "om_voice_1",
                    "chat_type": "p2p",
                    "message_type": "audio",
                    "content": '{"fileKey":"fk_1","durationMillisecond":3200}',
                },
            },
        }
        msg = await a.parse_message(event)
        assert msg.message_type == MessageType.TEXT
        assert msg.text == "[语音消息]"
        assert msg.content["is_voice"] is True
        assert msg.content["file_key"] == "fk_1"


# ---------- wecom 渠道后台语音计费接线 ----------


class TestWecomBackgroundVoiceBilling:
    """_process_tenant_wecom_background 语音路径：skip_save 与 add_asr_usage 接线。

    对齐 wecom_kf 口径：识别成功 -> skip_save=False + add_asr_usage(calls=1)；
    识别失败/非语音 -> skip_save=True 且不计费。
    """

    async def _run_background(self, message, download_media_result, asr_text):
        """构造最小 mock 环境跑一遍后台函数，返回捕获的 record"""
        adapter = MagicMock()
        adapter.media.download_media = AsyncMock(return_value=download_media_result)
        adapter.send_text = AsyncMock()
        adapter.get_user_info = AsyncMock(return_value={})
        adapter.verbose_feedback = None

        captured = {}

        def fake_start_record(**kwargs):
            record = MagicMock()
            captured["record"] = record
            return record

        async def fake_process_and_persist(**kwargs):
            return {"status": "ok"}

        with patch.object(
            channel_routes.ChannelFactory, "create_from_tenant_config",
            AsyncMock(return_value=(adapter, MagicMock(), None)),
        ), patch(
            "src.saas.services.auto_register.ensure_user_registered",
            AsyncMock(return_value="u_1"),
        ), patch.object(
            channel_routes.channel_session_manager, "get_or_create_session",
            MagicMock(return_value={"session_id": "sess_1"}),
        ), patch.object(
            channel_routes.channel_session_manager, "process_and_persist",
            AsyncMock(side_effect=fake_process_and_persist),
        ), patch(
            "src.channels.runner_agent.ChannelRunnerAgent", MagicMock()
        ), patch.object(
            channel_routes.SessionRecordManager, "start_record",
            MagicMock(side_effect=fake_start_record),
        ), patch.object(
            channel_routes.SessionRecordManager, "end_record", MagicMock()
        ), patch.object(
            channel_routes.SessionRecordManager, "get_current_record",
            MagicMock(return_value=None),
        ), patch(
            "src.channels.agent_user_builder.build_agent_user_for_channel",
            AsyncMock(return_value=None),
        ), patch.object(
            channel_routes, "_transcribe_voice_with_asr",
            AsyncMock(return_value=asr_text),
        ), patch.object(
            channel_routes, "ensure_tenant_storage_dir",
            MagicMock(return_value="/tmp/voice_asr_test"),
        ):
            await channel_routes._process_tenant_wecom_background(
                "tenant_1", message, config_id="c1", subagent_type=None
            )
        return captured.get("record")

    @pytest.mark.asyncio
    async def test_asr_success_bills_and_saves(self):
        """识别成功：skip_save=False + add_asr_usage(calls=1) + 语音存档"""
        message = _make_unified_message(
            {"text": "[语音消息]", "is_voice": True, "media_id": "m1", "voice_format": "amr"},
        )
        record = await self._run_background(
            message,
            (b"#!AMR\n" + b"\x00" * 32, "voice.amr", "audio/amr"),
            "你好世界",
        )
        assert record is not None
        assert record.skip_save is False
        record.add_asr_usage.assert_called_once_with(calls=1)

    @pytest.mark.asyncio
    async def test_asr_failure_no_billing_and_skip_save(self):
        """识别失败：skip_save=True + 不计费"""
        message = _make_unified_message(
            {"text": "[语音消息]", "is_voice": True, "media_id": "m1", "voice_format": "amr"},
        )
        record = await self._run_background(
            message,
            (b"#!AMR\n" + b"\x00" * 32, "voice.amr", "audio/amr"),
            "[语音消息]",
        )
        assert record is not None
        assert record.skip_save is True
        record.add_asr_usage.assert_not_called()

    @pytest.mark.asyncio
    async def test_text_message_no_billing_and_skip_save(self):
        """普通文本消息：skip_save=True + 不计费（行为与改造前一致）"""
        message = _make_unified_message({"text": "你好"})
        record = await self._run_background(message, None, "")
        assert record is not None
        assert record.skip_save is True
        record.add_asr_usage.assert_not_called()
