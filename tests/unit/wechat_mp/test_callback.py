"""wechat_mp 回调端点（WP4 产品版）单元测试。

分层：
- 纯逻辑：验签、AES 加解密 roundtrip（微信官方算法自测样本）、事件解析多子篇、event_key。
- 路由级（TestClient + mock 配置/入库）：GET 验证、明文/安全模式 POST、ToUserName 拒收、
  DB 故障 500、未知事件吞掉、幂等重复、响应不含敏感信息。
- 真实 DB（require_db 门禁）：三件套事务落库 + event_key 幂等不重复建行。

凭据/token 均为伪造测试值，不进断言输出。
"""

import base64
import hashlib
import os
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

import pytest

# 加密主密钥（伪造测试值），须在任何 src 加密模块初始化前设置（模块惰性初始化，import 期安全）
os.environ.setdefault("RPA_SECRET_KEY", "wmp-unit-test-master-key-0123456789abcdef")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.channels.wecom.crypto import WeComCrypto  # noqa: E402
from src.wechat_mp import callback as cb  # noqa: E402

# ------------------------------- 伪造配置与样本 -------------------------------

TOKEN = "test_callback_token_0123456789"  # 伪造
APPID = "wx0000000000000000"  # 伪造
ORIGINAL_ID = "gh_test00000000"  # 伪造
CONFIG_ID = "chan_wmpunittest"
TENANT_ID = "wmp_unit_tenant"
# 43 字符 EncodingAESKey（伪造，去掉 base64 填充）
AES_KEY = base64.b64encode(b"wmp-unit-test-aes-key-32bytes!!!").decode().rstrip("=")

ARTICLE_URL_1 = "https://mp.weixin.qq.com/s/AbCdEfGh0123"
ARTICLE_URL_2 = (
    "https://mp.weixin.qq.com/s?__biz=MzAxTestBiz&mid=100&idx=2&sn=deadbeef&scene=6#wechat_redirect"
)

SAMPLE_EVENT_XML = f"""<xml>
<ToUserName><![CDATA[{ORIGINAL_ID}]]></ToUserName>
<FromUserName><![CDATA[oTestFan]]></FromUserName>
<CreateTime>1757872800</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[MASSSENDJOBFINISH]]></Event>
<MsgID>1000001</MsgID>
<Status><![CDATA[send success]]></Status>
<TotalCount>92</TotalCount>
<FilterCount>92</FilterCount>
<SentCount>92</SentCount>
<ErrorCount>0</ErrorCount>
<ArticleUrlResult>
<Count>2</Count>
<ResultList>
<item><ArticleIdx>1</ArticleIdx><ArticleUrl><![CDATA[{ARTICLE_URL_1}]]></ArticleUrl></item>
<item><ArticleIdx>2</ArticleIdx><ArticleUrl><![CDATA[{ARTICLE_URL_2}]]></ArticleUrl></item>
</ResultList>
</ArticleUrlResult>
</xml>"""


def _fake_config(**cfg_overrides):
    config = {
        "appid": APPID,
        "original_id": ORIGINAL_ID,
        "callback_token": TOKEN,
        "encoding_aes_key": AES_KEY,
        "enabled": True,
        "sync_interval_hours": 6,
    }
    config.update(cfg_overrides)
    return {
        "config_id": CONFIG_ID,
        "tenant_id": TENANT_ID,
        "channel_type": "wechat_mp",
        "config": config,
    }


def _sign(token: str, timestamp: str, nonce: str) -> str:
    return hashlib.sha1("".join(sorted([token, timestamp, nonce])).encode()).hexdigest()


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(cb.router)
    return TestClient(app)


@pytest.fixture()
def mock_config():
    """patch 配置读取 + 三态回写，避免触 DB。"""
    with (
        patch.object(cb.ChannelConfigDB, "get_by_id_decrypted", return_value=_fake_config()) as m_get,
        patch.object(cb.ChannelConfigDB, "update_config_field", return_value=True),
        patch.object(cb.ChannelConfigDB, "set_verified", return_value=True),
    ):
        yield m_get


# ------------------------------- 纯逻辑测试 -------------------------------


class TestSignature:
    def test_signature_ok(self):
        ts, nonce = "1757872800", "abc123"
        assert cb._check_signature(TOKEN, _sign(TOKEN, ts, nonce), ts, nonce)

    def test_signature_mismatch(self):
        assert not cb._check_signature(TOKEN, "0" * 40, "1757872800", "abc123")

    def test_signature_empty(self):
        assert not cb._check_signature(TOKEN, "", "1757872800", "abc123")


class TestAesRoundtrip:
    """安全模式 AES 加解密 roundtrip（微信官方 WXBizMsgCrypt 算法自测样本）。"""

    def test_encrypt_decrypt_roundtrip(self):
        crypto_enc = WeComCrypto(token=TOKEN, encoding_aes_key=AES_KEY, corp_id=APPID)
        encrypt_field = crypto_enc.encrypt(SAMPLE_EVENT_XML)
        msg_sig = crypto_enc.generate_signature("1757872800", "nonce1", encrypt_field)

        # 模拟端点侧：新实例验签 + 解密
        crypto_dec = WeComCrypto(token=TOKEN, encoding_aes_key=AES_KEY, corp_id=APPID)
        assert crypto_dec.verify_signature(msg_sig, "1757872800", "nonce1", encrypt_field)
        decrypted = crypto_dec.decrypt(encrypt_field)
        assert "MASSSENDJOBFINISH" in decrypted
        root = ET.fromstring(decrypted)
        assert root.findtext("ToUserName") == ORIGINAL_ID

    def test_decrypt_wrong_appid_rejected(self):
        crypto_enc = WeComCrypto(token=TOKEN, encoding_aes_key=AES_KEY, corp_id=APPID)
        encrypt_field = crypto_enc.encrypt("<xml><x>1</x></xml>")
        other = WeComCrypto(token=TOKEN, encoding_aes_key=AES_KEY, corp_id="wxffffffffffffffff")
        with pytest.raises(ValueError):
            other.decrypt(encrypt_field)

    def test_msg_signature_mismatch(self):
        crypto = WeComCrypto(token=TOKEN, encoding_aes_key=AES_KEY, corp_id=APPID)
        encrypt_field = crypto.encrypt("<xml/>")
        assert not crypto.verify_signature("0" * 40, "1757872800", "nonce1", encrypt_field)


class TestEventParsing:
    def test_extract_multi_article_urls(self):
        root = ET.fromstring(SAMPLE_EVENT_XML)
        urls = cb._extract_article_urls(root)
        assert urls == [("1", ARTICLE_URL_1), ("2", ARTICLE_URL_2)]

    def test_extract_missing_container(self):
        root = ET.fromstring("<xml><MsgType>event</MsgType></xml>")
        assert cb._extract_article_urls(root) == []

    def test_extract_item_without_url_skipped(self):
        xml = (
            "<xml><ArticleUrlResult><ResultList>"
            "<item><ArticleIdx>1</ArticleIdx></item>"
            f"<item><ArticleIdx>2</ArticleIdx><ArticleUrl>{ARTICLE_URL_1}</ArticleUrl></item>"
            "</ResultList></ArticleUrlResult></xml>"
        )
        assert cb._extract_article_urls(ET.fromstring(xml)) == [("2", ARTICLE_URL_1)]

    def test_event_key_msgid_event(self):
        key = cb._build_event_key({"MsgID": "1000001", "Event": "MASSSENDJOBFINISH"}, "raw")
        assert key == "1000001:MASSSENDJOBFINISH"

    def test_event_key_fallback_hash(self):
        k1 = cb._build_event_key({"Event": "E"}, "body-a")
        k2 = cb._build_event_key({"Event": "E"}, "body-a")
        k3 = cb._build_event_key({"Event": "E"}, "body-b")
        assert k1 == k2 and k1 != k3 and k1.startswith("noid:")


class TestConfigCodec:
    """config_codec 加密/解密/掩码 roundtrip（伪造配置）。"""

    def test_encrypt_decrypt_roundtrip(self):
        from src.wechat_mp import config_codec

        plain = {"secret": "s3cret", "encoding_aes_key": AES_KEY, "callback_token": TOKEN, "appid": APPID}
        enc = config_codec.encrypt_sensitive_fields(plain)
        assert enc["secret"] != plain["secret"]
        assert enc["callback_token"].startswith("gAAAAA")
        assert enc["appid"] == APPID  # 非敏感字段不动
        dec = config_codec.decrypt_sensitive_fields(enc)
        assert dec["secret"] == "s3cret"
        assert dec["callback_token"] == TOKEN

    def test_encrypt_idempotent_on_ciphertext(self):
        from src.wechat_mp import config_codec

        enc = config_codec.encrypt_sensitive_fields({"secret": "s3cret"})
        enc2 = config_codec.encrypt_sensitive_fields(enc)
        assert enc2["secret"] == enc["secret"]  # 密文不二次加密

    def test_mask(self):
        from src.wechat_mp import config_codec

        masked = config_codec.mask_sensitive_fields({"secret": "abcdefghij", "callback_token": "gAAAAAxyz"})
        assert masked["secret"] == "***ghij"
        assert masked["callback_token"] == "***"
        assert masked["encoding_aes_key"] == ""


# ------------------------------- 路由级测试（mock 入库） -------------------------------


class TestGetVerify:
    def test_verify_ok(self, client, mock_config):
        ts, nonce = "1757872800", "n1"
        resp = client.get(
            f"/api/wechat-mp/callback/{CONFIG_ID}",
            params={"signature": _sign(TOKEN, ts, nonce), "timestamp": ts, "nonce": nonce, "echostr": "echo-me"},
        )
        assert resp.status_code == 200
        assert resp.text == "echo-me"

    def test_verify_config_not_found(self, client):
        with patch.object(cb.ChannelConfigDB, "get_by_id_decrypted", return_value=None):
            resp = client.get(f"/api/wechat-mp/callback/{CONFIG_ID}")
        assert resp.status_code == 404

    def test_verify_wrong_channel_type(self, client):
        cfg = _fake_config()
        cfg["channel_type"] = "wecom"
        with patch.object(cb.ChannelConfigDB, "get_by_id_decrypted", return_value=cfg):
            resp = client.get(f"/api/wechat-mp/callback/{CONFIG_ID}")
        assert resp.status_code == 404

    def test_verify_disabled(self, client):
        with patch.object(
            cb.ChannelConfigDB, "get_by_id_decrypted", return_value=_fake_config(enabled=False)
        ):
            resp = client.get(f"/api/wechat-mp/callback/{CONFIG_ID}")
        assert resp.status_code == 403

    def test_verify_bad_signature(self, client, mock_config):
        resp = client.get(
            f"/api/wechat-mp/callback/{CONFIG_ID}",
            params={"signature": "0" * 40, "timestamp": "1", "nonce": "n", "echostr": "x"},
        )
        assert resp.status_code == 403

    def test_verify_missing_token(self, client):
        with patch.object(
            cb.ChannelConfigDB, "get_by_id_decrypted", return_value=_fake_config(callback_token="")
        ):
            resp = client.get(f"/api/wechat-mp/callback/{CONFIG_ID}")
        assert resp.status_code == 500


def _post_event(client, body: str, token: str = TOKEN, extra_params: dict | None = None):
    ts, nonce = "1757872800", "n1"
    params = {"signature": _sign(token, ts, nonce), "timestamp": ts, "nonce": nonce}
    if extra_params:
        params.update(extra_params)
    return client.post(f"/api/wechat-mp/callback/{CONFIG_ID}", params=params, content=body)


class TestPostPlaintext:
    def test_masssend_accepted(self, client, mock_config):
        accept = MagicMock(return_value=cb._ACCEPT_ACCEPTED)
        with patch.object(cb, "_accept_masssend_event", accept):
            resp = _post_event(client, SAMPLE_EVENT_XML)
        assert resp.status_code == 200
        assert resp.text == "success"
        accept.assert_called_once()
        args = accept.call_args[0]
        assert args[0] == TENANT_ID and args[1] == CONFIG_ID
        assert args[2] == "1000001:MASSSENDJOBFINISH"
        urls = args[4]
        assert [u for _, u in urls] == [ARTICLE_URL_1, ARTICLE_URL_2]

    def test_duplicate_event_success_no_error(self, client, mock_config):
        with patch.object(cb, "_accept_masssend_event", MagicMock(return_value=cb._ACCEPT_DUPLICATE)):
            resp = _post_event(client, SAMPLE_EVENT_XML)
        assert resp.status_code == 200 and resp.text == "success"

    def test_db_failure_returns_500(self, client, mock_config):
        with patch.object(cb, "_accept_masssend_event", MagicMock(side_effect=RuntimeError("db down"))):
            resp = _post_event(client, SAMPLE_EVENT_XML)
        assert resp.status_code == 500

    def test_touser_mismatch_rejected(self, client, mock_config):
        bad_xml = SAMPLE_EVENT_XML.replace(ORIGINAL_ID, "gh_otheraccount")
        accept = MagicMock()
        with patch.object(cb, "_accept_masssend_event", accept):
            resp = _post_event(client, bad_xml)
        assert resp.status_code == 200 and resp.text == "success"
        accept.assert_not_called()

    def test_unknown_event_swallowed(self, client, mock_config):
        xml = f"<xml><ToUserName>{ORIGINAL_ID}</ToUserName><MsgType>event</MsgType><Event>subscribe</Event></xml>"
        accept = MagicMock()
        with patch.object(cb, "_accept_masssend_event", accept):
            resp = _post_event(client, xml)
        assert resp.status_code == 200 and resp.text == "success"
        accept.assert_not_called()

    def test_bad_signature_403(self, client, mock_config):
        resp = _post_event(client, SAMPLE_EVENT_XML, token="wrong_token")
        assert resp.status_code == 403

    def test_malformed_xml_success(self, client, mock_config):
        resp = _post_event(client, "<xml><broken")
        assert resp.status_code == 200 and resp.text == "success"

    def test_response_has_no_sensitive(self, client, mock_config):
        accept = MagicMock(return_value=cb._ACCEPT_ACCEPTED)
        with patch.object(cb, "_accept_masssend_event", accept):
            resp = _post_event(client, SAMPLE_EVENT_XML)
        assert TOKEN not in resp.text
        assert AES_KEY not in resp.text


class TestPostAes:
    def _build_aes_body(self, inner_xml: str = SAMPLE_EVENT_XML):
        crypto = WeComCrypto(token=TOKEN, encoding_aes_key=AES_KEY, corp_id=APPID)
        encrypt_field = crypto.encrypt(inner_xml)
        ts, nonce = "1757872800", "n1"
        msg_sig = crypto.generate_signature(ts, nonce, encrypt_field)
        outer = (
            f"<xml><ToUserName><![CDATA[{ORIGINAL_ID}]]></ToUserName>"
            f"<Encrypt><![CDATA[{encrypt_field}]]></Encrypt></xml>"
        )
        params = {
            "signature": _sign(TOKEN, ts, nonce),
            "timestamp": ts,
            "nonce": nonce,
            "encrypt_type": "aes",
            "msg_signature": msg_sig,
        }
        return outer, params

    def test_aes_accepted(self, client, mock_config):
        outer, params = self._build_aes_body()
        accept = MagicMock(return_value=cb._ACCEPT_ACCEPTED)
        with patch.object(cb, "_accept_masssend_event", accept):
            resp = client.post(f"/api/wechat-mp/callback/{CONFIG_ID}", params=params, content=outer)
        assert resp.status_code == 200 and resp.text == "success"
        accept.assert_called_once()
        assert accept.call_args[0][2] == "1000001:MASSSENDJOBFINISH"

    def test_aes_bad_msg_signature_403(self, client, mock_config):
        outer, params = self._build_aes_body()
        params["msg_signature"] = "0" * 40
        accept = MagicMock()
        with patch.object(cb, "_accept_masssend_event", accept):
            resp = client.post(f"/api/wechat-mp/callback/{CONFIG_ID}", params=params, content=outer)
        assert resp.status_code == 403
        accept.assert_not_called()

    def test_aes_missing_key_returns_500_with_guidance(self, client):
        outer, params = self._build_aes_body()
        with (
            patch.object(
                cb.ChannelConfigDB, "get_by_id_decrypted",
                return_value=_fake_config(encoding_aes_key=""),
            ),
            patch.object(cb.ChannelConfigDB, "update_config_field", return_value=True),
        ):
            resp = client.post(f"/api/wechat-mp/callback/{CONFIG_ID}", params=params, content=outer)
        assert resp.status_code == 500
        assert "encoding_aes_key" in resp.text

    def test_aes_response_no_sensitive(self, client, mock_config):
        outer, params = self._build_aes_body()
        accept = MagicMock(return_value=cb._ACCEPT_ACCEPTED)
        with patch.object(cb, "_accept_masssend_event", accept):
            resp = client.post(f"/api/wechat-mp/callback/{CONFIG_ID}", params=params, content=outer)
        assert TOKEN not in resp.text and AES_KEY not in resp.text


# ------------------------------- 真实 DB：三件套事务与幂等 -------------------------------

CHANNEL_CONFIG_TABLE = "tenant_channel_configs"


@pytest.fixture()
def mp_config_row(tenant_id):
    """真实 DB 造一条 wechat_mp 渠道配置（明文配置即可，accept 路径不解密），测后清理。"""
    import json as _json

    from src.db.database import get_db_connection

    config_id = f"chan_{os.urandom(6).hex()}"
    config = {
        "appid": APPID,
        "original_id": ORIGINAL_ID,
        "callback_token": TOKEN,
        "enabled": True,
    }
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO tenant_channel_configs (config_id, tenant_id, channel_type, config) VALUES (%s,%s,'wechat_mp',%s)",
            (config_id, tenant_id, _json.dumps(config)),
        )
        conn.commit()
    yield tenant_id, config_id
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM tenant_channel_configs WHERE config_id = %s AND tenant_id = %s",
            (config_id, tenant_id),
        )
        conn.commit()


class TestAcceptTransactionRealDB:
    def test_three_piece_accept_and_idempotency(self, require_db, mp_config_row):
        tenant_id, config_id = mp_config_row
        payload = {"MsgID": "1000001", "Event": "MASSSENDJOBFINISH"}
        urls = [("1", ARTICLE_URL_1), ("2", ARTICLE_URL_2)]

        result = cb._accept_masssend_event(tenant_id, config_id, "1000001:MASSSENDJOBFINISH", payload, urls)
        assert result == cb._ACCEPT_ACCEPTED

        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM bs_wechat_mp_events WHERE tenant_id=%s AND config_id=%s",
                (tenant_id, config_id),
            )
            events = cursor.fetchall()
            assert len(events) == 1
            assert events[0]["status"] == "pending"
            assert events[0]["run_id"] is not None
            run_id = events[0]["run_id"]

            cursor.execute(
                "SELECT * FROM bs_wechat_mp_sync_runs WHERE tenant_id=%s AND id=%s",
                (tenant_id, run_id),
            )
            run = cursor.fetchone()
            assert run["status"] == "queued"
            assert run["trigger_type"] == "callback"
            assert run["total_count"] == 2

            cursor.execute(
                "SELECT * FROM bs_wechat_mp_sync_items WHERE tenant_id=%s AND run_id=%s ORDER BY id",
                (tenant_id, run_id),
            )
            items = cursor.fetchall()
            assert len(items) == 2
            assert all(i["status"] == "pending" for i in items)

            cursor.execute(
                "SELECT * FROM bs_wechat_mp_articles WHERE tenant_id=%s ORDER BY id",
                (tenant_id,),
            )
            articles = cursor.fetchall()
            assert len(articles) == 2
            assert articles[0]["external_id"] == "mp:s:AbCdEfGh0123"
            assert articles[0]["source_channel"] == "callback"
            assert articles[0]["processing_status"] == "pending"
            # 长链跟踪参数剔除：scene 不进 external_id
            assert articles[1]["external_id"].startswith("mp:q:")
            assert "scene" not in articles[1]["external_id"]

        # 幂等：同 event_key 重投不重复建行
        result2 = cb._accept_masssend_event(tenant_id, config_id, "1000001:MASSSENDJOBFINISH", payload, urls)
        assert result2 == cb._ACCEPT_DUPLICATE
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT count(*) AS c FROM bs_wechat_mp_events WHERE tenant_id=%s", (tenant_id,)
            )
            assert cursor.fetchone()["c"] == 1
            cursor.execute(
                "SELECT count(*) AS c FROM bs_wechat_mp_sync_runs WHERE tenant_id=%s", (tenant_id,)
            )
            assert cursor.fetchone()["c"] == 1
            cursor.execute(
                "SELECT count(*) AS c FROM bs_wechat_mp_sync_items WHERE tenant_id=%s", (tenant_id,)
            )
            assert cursor.fetchone()["c"] == 2

    def test_no_valid_url_rolls_back(self, require_db, mp_config_row):
        tenant_id, config_id = mp_config_row
        result = cb._accept_masssend_event(
            tenant_id, config_id, "2000002:MASSSENDJOBFINISH", {}, [("1", "https://example.com/not-mp")]
        )
        assert result == cb._ACCEPT_NO_VALID_URL

        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT count(*) AS c FROM bs_wechat_mp_events WHERE tenant_id=%s", (tenant_id,)
            )
            assert cursor.fetchone()["c"] == 0
            cursor.execute(
                "SELECT count(*) AS c FROM bs_wechat_mp_sync_runs WHERE tenant_id=%s", (tenant_id,)
            )
            assert cursor.fetchone()["c"] == 0


# ------------------------------- 真实 DB：渠道配置 create/update/rotate -------------------------------

import json as _json  # noqa: E402


@pytest.fixture()
def mp_cfg_cleanup(tenant_id):
    """真实 DB 渠道配置测试：测后清理本租户的 wechat_mp 配置行。"""
    yield tenant_id
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM tenant_channel_configs WHERE tenant_id = %s AND channel_type = 'wechat_mp'",
            (tenant_id,),
        )
        conn.commit()


def _raw_config(config_id: str) -> dict:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT config FROM tenant_channel_configs WHERE config_id = %s", (config_id,)
        )
        row = cursor.fetchone()
    return _json.loads(row["config"]) if row else {}


class TestChannelConfigRealDB:
    """ChannelConfigDB wechat_mp 分支真实 DB 验证（含 P0-1 修复的 ::jsonb 路径）。"""

    BASE_CONFIG = {
        "appid": APPID,
        "original_id": ORIGINAL_ID,
        "secret": "fake-secret",
        "encoding_aes_key": AES_KEY,
    }

    def test_create_success_token_server_generated(self, require_db, mp_cfg_cleanup):
        from src.saas.db.channel_config_db import ChannelConfigDB

        created = ChannelConfigDB.create(
            tenant_id=mp_cfg_cleanup,
            channel_type="wechat_mp",
            name="公众号",
            config={**self.BASE_CONFIG, "callback_token": "user-supplied-must-be-ignored"},
        )
        assert created and created["config_id"]
        # API 视角：敏感字段掩码
        assert created["config"]["callback_token"] == "***"
        assert created["config"]["secret"] == "***"
        # DB 原始值：token 服务端生成（忽略入参）且加密入库
        raw = _raw_config(created["config_id"])
        assert raw["callback_token"] != "user-supplied-must-be-ignored"
        assert raw["callback_token"].startswith("gAAAAA")
        assert raw["secret"].startswith("gAAAAA")
        assert raw["encoding_aes_key"].startswith("gAAAAA")
        assert raw["enabled"] is True
        assert raw["sync_interval_hours"] == 6
        assert raw["credential_version"] == 1
        # 内部解密视图拿回明文
        dec = ChannelConfigDB.get_by_id_decrypted(created["config_id"])
        assert dec["config"]["callback_token"] and not dec["config"]["callback_token"].startswith("gAAAAA")
        assert dec["config"]["secret"] == "fake-secret"

    def test_create_duplicate_appid_rejected(self, require_db, mp_cfg_cleanup):
        from src.saas.db.channel_config_db import ChannelConfigDB

        first = ChannelConfigDB.create(mp_cfg_cleanup, "wechat_mp", config=dict(self.BASE_CONFIG))
        assert first
        dup = ChannelConfigDB.create(
            mp_cfg_cleanup, "wechat_mp",
            config={**self.BASE_CONFIG, "original_id": "gh_other"},
        )
        assert dup is None  # 应用层拦截（同租户同 appid 唯一）

    def test_update_backfill_identity_and_preserve_sensitive(self, require_db, mp_cfg_cleanup):
        from src.saas.db.channel_config_db import ChannelConfigDB

        created = ChannelConfigDB.create(mp_cfg_cleanup, "wechat_mp", config=dict(self.BASE_CONFIG))
        cid = created["config_id"]
        old_raw = _raw_config(cid)
        # 模拟回调验证通过后的三态字段
        assert ChannelConfigDB.update_config_field(cid, "config_verified_at", "2026-09-15T00:00:00+00:00")

        # 更新时身份/敏感字段缺失或掩码/null → 全部保留旧值
        ok = ChannelConfigDB.update(cid, {"enabled": False, "secret": None, "encoding_aes_key": "***"})
        assert ok
        new_raw = _raw_config(cid)
        assert new_raw["appid"] == APPID  # 缺失回填，未被抹掉
        assert new_raw["original_id"] == ORIGINAL_ID
        assert new_raw["secret"] == old_raw["secret"]  # null 不可清空
        assert new_raw["encoding_aes_key"] == old_raw["encoding_aes_key"]  # 掩码回传保留
        assert new_raw["callback_token"] == old_raw["callback_token"]
        assert new_raw["config_verified_at"] == "2026-09-15T00:00:00+00:00"  # runtime 字段保留
        assert new_raw["enabled"] is False

    def test_update_appid_conflict_raises_integrity_error(self, require_db, mp_cfg_cleanup):
        """appid 变更撞同租户唯一索引 → IntegrityError 上抛（API 层转 409，与 create 对称）。"""
        import psycopg2

        from src.saas.db.channel_config_db import ChannelConfigDB

        a = ChannelConfigDB.create(mp_cfg_cleanup, "wechat_mp", config=dict(self.BASE_CONFIG))
        b = ChannelConfigDB.create(
            mp_cfg_cleanup, "wechat_mp",
            config={**self.BASE_CONFIG, "appid": "wx1111111111111111"},
        )
        assert a and b
        with pytest.raises(psycopg2.IntegrityError):
            ChannelConfigDB.update(b["config_id"], {**self.BASE_CONFIG, "appid": APPID})

    def test_rotate_token_real_db(self, require_db, mp_cfg_cleanup):
        from src.saas.db.channel_config_db import ChannelConfigDB

        created = ChannelConfigDB.create(mp_cfg_cleanup, "wechat_mp", config=dict(self.BASE_CONFIG))
        cid = created["config_id"]
        ChannelConfigDB.set_verified(cid, True)
        ChannelConfigDB.update_config_field(cid, "config_verified_at", "2026-09-15T00:00:00+00:00")
        old_token = ChannelConfigDB.get_by_id_decrypted(cid)["config"]["callback_token"]

        new_token = ChannelConfigDB.rotate_wechat_mp_token(cid)
        assert new_token and new_token != old_token

        dec = ChannelConfigDB.get_by_id_decrypted(cid)
        assert dec["config"]["callback_token"] == new_token
        assert dec["config"]["credential_version"] == 2
        assert "config_verified_at" not in dec["config"]
        assert dec["verified"] == 0
        # 其他敏感字段原样保留
        assert dec["config"]["secret"] == "fake-secret"
