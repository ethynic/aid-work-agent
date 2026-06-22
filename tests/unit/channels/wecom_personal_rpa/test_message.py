"""企业微信个人账号 RPA 回调消息解析单元测试

覆盖：
- parse_rpa_message：text / image / voice 各映射正确
- parse_status_event：status 事件解析
- parse_action_result：action_result 事件解析
- 缺字段报错（text 消息缺 text、payload 缺失、raw 非 dict）
"""
import pytest

pytestmark = pytest.mark.channels


# ---------------------------------------------------------------------------
# parse_rpa_message
# ---------------------------------------------------------------------------

class TestParseRpaMessage:
    """parse_rpa_message 覆盖用例。"""

    def _envelope(self, payload: dict, **overrides) -> dict:
        """构造已校验的 RpaCallbackEnvelope（event_type=message）。"""
        env = {
            "event_id": "evt_20260622_001",
            "client_id": "client_001",
            "account_id": "wecom_account_001",
            "event_type": "message",
            "occurred_at": "2026-06-22T10:00:00+08:00",
            "payload": payload,
        }
        env.update(overrides)
        return env

    def test_text_message_maps_to_text(self):
        """text 类型消息应映射为 MessageType.TEXT，content 含 text/conversation_*。"""
        from src.channels.wecom_personal_rpa.message import parse_rpa_message
        from src.models.message import ChannelType, MessageType

        raw = self._envelope({
            "conversation_id": "binding_abc",
            "conversation_type": "external_user",
            "sender_display_name": "张三",
            "sender_stable_id": "ext_user_zhangsan",
            "message_type": "text",
            "text": "你好",
            "attachments": [],
        })

        msg = parse_rpa_message(raw)
        assert msg.message_id == "evt_20260622_001"
        assert msg.channel_type == ChannelType.WECOM_PERSONAL_RPA
        assert msg.user_id == "ext_user_zhangsan"
        assert msg.user_name == "张三"
        assert msg.message_type == MessageType.TEXT
        assert msg.content["text"] == "你好"
        assert msg.content["conversation_id"] == "binding_abc"
        assert msg.content["conversation_type"] == "external_user"
        assert msg.content["account_id"] == "wecom_account_001"
        assert msg.attachments == []
        # raw_message 必须原样保留（Pydantic 重建 dict，按值相等校验）
        assert msg.raw_message == raw
        assert msg.raw_message["event_id"] == "evt_20260622_001"
        assert msg.raw_message["payload"]["text"] == "你好"

    def test_text_message_user_id_fallback_to_conversation_id(self):
        """sender_stable_id 缺失时 user_id 回退到 conversation_id。"""
        from src.channels.wecom_personal_rpa.message import parse_rpa_message

        raw = self._envelope({
            "conversation_id": "conv_fallback",
            "conversation_type": "internal_user",
            "sender_display_name": "内部用户",
            "sender_stable_id": None,
            "message_type": "text",
            "text": "在吗",
            "attachments": [],
        })

        msg = parse_rpa_message(raw)
        assert msg.user_id == "conv_fallback"
        assert msg.user_name == "内部用户"

    def test_image_message_maps_to_image(self):
        """image 类型消息应映射为 MessageType.IMAGE，附件正确转换。"""
        from src.channels.wecom_personal_rpa.message import parse_rpa_message
        from src.models.message import MessageType

        raw = self._envelope({
            "conversation_id": "binding_img",
            "conversation_type": "external_group",
            "sender_display_name": "李四",
            "sender_stable_id": "ext_user_lisi",
            "message_type": "image",
            "text": None,
            "attachments": [
                {
                    "type": "image",
                    "url": "https://example.com/img/1.png?sig=xxx",
                    "name": "截图.png",
                    "size": 10240,
                    "mime_type": "image/png",
                },
            ],
        })

        msg = parse_rpa_message(raw)
        assert msg.message_type == MessageType.IMAGE
        # 非文本类型 content.text 为空字符串，且保留原始 message_type
        assert msg.content["text"] == ""
        assert msg.content["message_type"] == "image"
        assert len(msg.attachments) == 1
        att = msg.attachments[0]
        assert att.type == "image"
        assert att.url == "https://example.com/img/1.png?sig=xxx"
        assert att.name == "截图.png"
        assert att.size == 10240
        assert att.mime_type == "image/png"

    def test_voice_message_maps_to_event(self):
        """voice 类型无对应 MessageType，应降级为 EVENT。"""
        from src.channels.wecom_personal_rpa.message import parse_rpa_message
        from src.models.message import MessageType

        raw = self._envelope({
            "conversation_id": "binding_voice",
            "conversation_type": "external_user",
            "sender_display_name": "王五",
            "sender_stable_id": "ext_user_wangwu",
            "message_type": "voice",
            "text": None,
            "attachments": [
                {
                    "type": "voice",
                    "url": "https://example.com/voice/1.amr",
                    "mime_type": "audio/amr",
                },
            ],
        })

        msg = parse_rpa_message(raw)
        # voice 无直接对应，降级 EVENT
        assert msg.message_type == MessageType.EVENT
        assert msg.content["message_type"] == "voice"
        assert len(msg.attachments) == 1
        assert msg.attachments[0].type == "voice"

    def test_file_message_maps_to_file(self):
        """file 类型消息应映射为 MessageType.FILE。"""
        from src.channels.wecom_personal_rpa.message import parse_rpa_message
        from src.models.message import MessageType

        raw = self._envelope({
            "conversation_id": "binding_file",
            "conversation_type": "internal_group",
            "sender_display_name": "赵六",
            "sender_stable_id": "user_zhaoliu",
            "message_type": "file",
            "text": None,
            "attachments": [
                {
                    "type": "file",
                    "url": "https://example.com/file/quote.xlsx?sig=yyy",
                    "name": "报价单.xlsx",
                    "size": 51200,
                    "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                },
            ],
        })

        msg = parse_rpa_message(raw)
        assert msg.message_type == MessageType.FILE
        assert len(msg.attachments) == 1
        assert msg.attachments[0].name == "报价单.xlsx"

    def test_text_message_missing_text_raises(self):
        """text 类型但 payload.text 为空时必须抛 ValueError。"""
        from src.channels.wecom_personal_rpa.message import parse_rpa_message

        raw = self._envelope({
            "conversation_id": "binding_bad",
            "conversation_type": "external_user",
            "sender_display_name": "缺文本",
            "sender_stable_id": "ext_user_bad",
            "message_type": "text",
            "text": None,  # text 类型但缺文本
            "attachments": [],
        })

        with pytest.raises(ValueError, match="text"):
            parse_rpa_message(raw)

    def test_missing_payload_raises(self):
        """信封缺 payload 字段必须抛 ValueError。"""
        from src.channels.wecom_personal_rpa.message import parse_rpa_message

        raw = {
            "event_id": "evt_no_payload",
            "client_id": "client_001",
            "account_id": "wecom_account_001",
            "event_type": "message",
            "occurred_at": "2026-06-22T10:00:00+08:00",
            # 故意不写 payload
        }

        with pytest.raises(ValueError, match="payload"):
            parse_rpa_message(raw)

    def test_non_dict_raw_raises(self):
        """raw 非 dict 必须抛 ValueError。"""
        from src.channels.wecom_personal_rpa.message import parse_rpa_message

        with pytest.raises(ValueError, match="dict"):
            parse_rpa_message("not a dict")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# parse_status_event
# ---------------------------------------------------------------------------

class TestParseStatusEvent:
    def test_need_login_status(self):
        """status=need_login 的 payload 应正确解析为 RpaStatusPayload。"""
        from src.channels.wecom_personal_rpa.message import parse_status_event
        from src.channels.wecom_personal_rpa.schemas import RpaStatusPayload

        raw = {
            "event_id": "evt_status_001",
            "client_id": "client_001",
            "account_id": "wecom_account_001",
            "event_type": "status",
            "occurred_at": "2026-06-22T10:01:00+08:00",
            "payload": {
                "status": "need_login",
                "account_display_name": "销售-王经理",
                "detail": "二维码已展示，等待扫码",
                "qr_image_ref": "tmp://qr/abc.png",
            },
        }

        result = parse_status_event(raw)
        assert isinstance(result, RpaStatusPayload)
        assert result.status == "need_login"
        assert result.account_display_name == "销售-王经理"
        assert result.detail == "二维码已展示，等待扫码"
        assert result.qr_image_ref == "tmp://qr/abc.png"

    def test_online_status_minimal(self):
        """status=online 仅必填 status 字段即可解析。"""
        from src.channels.wecom_personal_rpa.message import parse_status_event

        raw = {
            "event_id": "evt_status_002",
            "client_id": "client_001",
            "account_id": "wecom_account_001",
            "event_type": "status",
            "occurred_at": "2026-06-22T10:02:00+08:00",
            "payload": {"status": "online"},
        }

        result = parse_status_event(raw)
        assert result.status == "online"
        assert result.account_display_name is None
        assert result.detail is None

    def test_invalid_status_enum_raises(self):
        """status 取值不在枚举内应抛 ValidationError。"""
        from src.channels.wecom_personal_rpa.message import parse_status_event
        from pydantic import ValidationError

        raw = {
            "event_id": "evt_status_bad",
            "client_id": "client_001",
            "account_id": "wecom_account_001",
            "event_type": "status",
            "occurred_at": "2026-06-22T10:03:00+08:00",
            "payload": {"status": "totally_unknown_status"},
        }

        with pytest.raises(ValidationError):
            parse_status_event(raw)


# ---------------------------------------------------------------------------
# parse_action_result
# ---------------------------------------------------------------------------

class TestParseActionResult:
    def test_success_action_result(self):
        """成功的 send_text 回执应正确解析为 RpaActionResultPayload。"""
        from src.channels.wecom_personal_rpa.message import parse_action_result
        from src.channels.wecom_personal_rpa.schemas import RpaActionResultPayload

        raw = {
            "event_id": "evt_action_001",
            "client_id": "client_001",
            "account_id": "wecom_account_001",
            "event_type": "action_result",
            "occurred_at": "2026-06-22T10:05:00+08:00",
            "payload": {
                "request_id": "req_xxx",
                "action_result_id": "res_001",
                "action_index": 0,
                "action_type": "send_text",
                "success": True,
                "error_code": None,
                "error_message": None,
                "executed_at": "2026-06-22T10:00:05+08:00",
            },
        }

        result = parse_action_result(raw)
        assert isinstance(result, RpaActionResultPayload)
        assert result.request_id == "req_xxx"
        assert result.action_result_id == "res_001"
        assert result.action_index == 0
        assert result.action_type == "send_text"
        assert result.success is True
        assert result.error_code is None
        assert result.error_message is None

    def test_failed_action_result_with_error(self):
        """失败的回执应保留 error_code 与脱敏后的 error_message。"""
        from src.channels.wecom_personal_rpa.message import parse_action_result

        raw = {
            "event_id": "evt_action_002",
            "client_id": "client_001",
            "account_id": "wecom_account_001",
            "event_type": "action_result",
            "occurred_at": "2026-06-22T10:06:00+08:00",
            "payload": {
                "request_id": "req_yyy",
                "action_result_id": "res_002",
                "action_index": 1,
                "action_type": "send_file",
                "success": False,
                "error_code": "unsupported_action",
                "error_message": "文件类型暂不支持",
                "executed_at": "2026-06-22T10:00:08+08:00",
            },
        }

        result = parse_action_result(raw)
        assert result.success is False
        assert result.error_code == "unsupported_action"
        assert result.error_message == "文件类型暂不支持"
        assert result.action_index == 1

    def test_missing_required_field_raises(self):
        """payload 缺必填字段（request_id）应抛 ValidationError。"""
        from src.channels.wecom_personal_rpa.message import parse_action_result
        from pydantic import ValidationError

        raw = {
            "event_id": "evt_action_bad",
            "client_id": "client_001",
            "account_id": "wecom_account_001",
            "event_type": "action_result",
            "occurred_at": "2026-06-22T10:07:00+08:00",
            "payload": {
                # 缺 request_id
                "action_result_id": "res_003",
                "action_index": 0,
                "action_type": "send_text",
                "success": True,
                "executed_at": "2026-06-22T10:00:05+08:00",
            },
        }

        with pytest.raises(ValidationError):
            parse_action_result(raw)
