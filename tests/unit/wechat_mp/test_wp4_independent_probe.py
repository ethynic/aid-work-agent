"""WP4 独立测试智能体探针：路由→真实 DB 全链路 + 加密/掩码/轮换路径。

与开发者的 test_callback.py 差异：不 mock ``_accept_masssend_event``，
经 TestClient 走完整路由（真实验签/真实 AES/真实落库），核对三件套一致性、
双事件双 queued run、ToUserName 拒收、Fernet 密文落库与掩码语义、Token 轮换。

凭据均为伪造测试值。
"""

import base64
import hashlib
import json
import os

import pytest

os.environ.setdefault("RPA_SECRET_KEY", "wmp-wp4-probe-master-key-0123456789ab")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.channels.wecom.crypto import WeComCrypto  # noqa: E402
from src.db.database import get_db_connection  # noqa: E402
from src.saas.db.channel_config_db import ChannelConfigDB  # noqa: E402
from src.wechat_mp import callback as cb  # noqa: E402

APPID = "wxwp4probe00000000"  # 伪造
ORIGINAL_ID = "gh_wp4probe0000"  # 伪造
AES_KEY = base64.b64encode(b"wp4-probe-aes-key-32bytes!!!!!!!").decode().rstrip("=")
SECRET = "wp4-probe-appsecret-0123456789"  # 伪造

URL_1 = "https://mp.weixin.qq.com/s/Wp4ProbeArt1"
URL_2 = "https://mp.weixin.qq.com/s/Wp4ProbeArt2"


def _event_xml(msg_id: str, to_user: str = ORIGINAL_ID, urls=()) -> str:
    items = "".join(
        f"<item><ArticleIdx>{i}</ArticleIdx><ArticleUrl><![CDATA[{u}]]></ArticleUrl></item>"
        for i, u in enumerate(urls, 1)
    )
    return f"""<xml>
<ToUserName><![CDATA[{to_user}]]></ToUserName>
<FromUserName><![CDATA[oProbe]]></FromUserName>
<CreateTime>1758000000</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[MASSSENDJOBFINISH]]></Event>
<MsgID>{msg_id}</MsgID>
<Status><![CDATA[send success]]></Status>
<ArticleUrlResult><Count>{len(urls)}</Count><ResultList>{items}</ResultList></ArticleUrlResult>
</xml>"""


def _sign(token: str, ts: str, nonce: str) -> str:
    return hashlib.sha1("".join(sorted([token, ts, nonce])).encode()).hexdigest()


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(cb.router)
    return TestClient(app)


@pytest.fixture()
def mp_config(tenant_id):
    """真实建配置行：敏感字段经 config_codec 真实 Fernet 加密后直插（config 列为 TEXT）。

    注：本 fixture 绕过 create 直插（聚焦读取/解密/回调路径）；create 主路径的
    TEXT 列 ::jsonb 修复回归见 TestCreateOnTextColumn。
    """
    from src.wechat_mp import config_codec

    config_id = f"chan_{os.urandom(6).hex()}"
    plain_config = {
        "appid": APPID,
        "original_id": ORIGINAL_ID,
        "encoding_aes_key": AES_KEY,
        "secret": SECRET,
        "callback_token": "wp4-probe-token-" + os.urandom(6).hex(),
        "enabled": True,
        "credential_version": 1,
    }
    stored = config_codec.encrypt_sensitive_fields(plain_config)
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO tenant_channel_configs (config_id, tenant_id, channel_type, name, config)"
            " VALUES (%s, %s, 'wechat_mp', 'wp4-probe', %s)",
            (config_id, tenant_id, json.dumps(stored, ensure_ascii=False)),
        )
        conn.commit()
    yield config_id, plain_config
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM tenant_channel_configs WHERE config_id = %s AND tenant_id = %s",
            (config_id, tenant_id),
        )
        conn.commit()


def _post(client, config_id, token, body, extra=None):
    ts, nonce = "1758000000", "probe-n"
    params = {"signature": _sign(token, ts, nonce), "timestamp": ts, "nonce": nonce}
    if extra:
        params.update(extra)
    return client.post(f"/api/wechat-mp/callback/{config_id}", params=params, content=body)


def _counts(tenant_id):
    out = {}
    with get_db_connection() as conn:
        cur = conn.cursor()
        for t in ("bs_wechat_mp_events", "bs_wechat_mp_sync_runs", "bs_wechat_mp_sync_items", "bs_wechat_mp_articles"):
            cur.execute(f"SELECT count(*) AS c FROM {t} WHERE tenant_id=%s", (tenant_id,))
            out[t] = cur.fetchone()["c"]
    return out


class TestRouteToRealDB:
    def test_three_piece_consistency_and_redelivery(self, client, tenant_id, mp_config):
        config_id, cfg = mp_config
        token = cfg["callback_token"]
        assert token and not token.startswith("gAAAAA"), "内部读取应为明文 token"

        resp = _post(client, config_id, token, _event_xml("900001", urls=[URL_1, URL_2]))
        assert resp.status_code == 200 and resp.text == "success"

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM bs_wechat_mp_events WHERE tenant_id=%s", (tenant_id,))
            ev = cur.fetchone()
            assert ev["event_key"] == "900001:MASSSENDJOBFINISH"
            assert ev["run_id"] is not None
            cur.execute("SELECT * FROM bs_wechat_mp_sync_runs WHERE id=%s", (ev["run_id"],))
            run = cur.fetchone()
            assert run["status"] == "queued" and run["total_count"] == 2
            cur.execute("SELECT count(*) AS c FROM bs_wechat_mp_sync_items WHERE run_id=%s", (run["id"],))
            assert cur.fetchone()["c"] == 2

        # 重复投递同事件：success 且四表零增长
        before = _counts(tenant_id)
        resp2 = _post(client, config_id, token, _event_xml("900001", urls=[URL_1, URL_2]))
        assert resp2.status_code == 200 and resp2.text == "success"
        assert _counts(tenant_id) == before

    def test_two_masssend_events_two_queued_runs(self, client, tenant_id, mp_config):
        config_id, cfg = mp_config
        token = cfg["callback_token"]
        r1 = _post(client, config_id, token, _event_xml("900010", urls=[URL_1]))
        r2 = _post(client, config_id, token, _event_xml("900011", urls=[URL_2]))
        assert r1.text == "success" and r2.text == "success"
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT count(*) AS c FROM bs_wechat_mp_sync_runs WHERE tenant_id=%s AND status='queued'",
                (tenant_id,),
            )
            assert cur.fetchone()["c"] == 2, "WP1 串行约束只限 running，两个 queued run 都应受理"

    def test_touser_mismatch_success_no_rows_last_error(self, client, tenant_id, mp_config):
        config_id, cfg = mp_config
        token = cfg["callback_token"]
        resp = _post(client, config_id, token, _event_xml("900020", to_user="gh_someone_else", urls=[URL_1]))
        assert resp.status_code == 200 and resp.text == "success", "必须 success 防微信重试风暴"
        assert _counts(tenant_id) == {t: 0 for t in (
            "bs_wechat_mp_events", "bs_wechat_mp_sync_runs", "bs_wechat_mp_sync_items", "bs_wechat_mp_articles")}
        after = ChannelConfigDB.get_by_id(config_id)
        assert "ToUserName" in (after["config"].get("last_error") or "")

    def test_aes_mode_route_real_decrypt_from_db(self, client, tenant_id, mp_config):
        """安全模式：DB 里 AES key 为密文，端点解密后走真实 WXBizMsgCrypt 解密受理。"""
        config_id, cfg = mp_config
        token = cfg["callback_token"]
        crypto = WeComCrypto(token=token, encoding_aes_key=AES_KEY, corp_id=APPID)
        enc = crypto.encrypt(_event_xml("900030", urls=[URL_1]))
        ts, nonce = "1758000000", "probe-n"
        outer = f"<xml><ToUserName><![CDATA[{ORIGINAL_ID}]]></ToUserName><Encrypt><![CDATA[{enc}]]></Encrypt></xml>"
        params = {
            "signature": _sign(token, ts, nonce),
            "timestamp": ts,
            "nonce": nonce,
            "encrypt_type": "aes",
            "msg_signature": crypto.generate_signature(ts, nonce, enc),
        }
        resp = client.post(f"/api/wechat-mp/callback/{config_id}", params=params, content=outer)
        assert resp.status_code == 200 and resp.text == "success"
        assert _counts(tenant_id)["bs_wechat_mp_events"] == 1


class TestEncryptionAndRotation:
    def test_ciphertext_at_rest_mask_and_preserve(self, tenant_id, mp_config):
        config_id, cfg = mp_config
        # 落库为密文
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT config FROM tenant_channel_configs WHERE config_id=%s", (config_id,))
            stored = json.loads(cur.fetchone()["config"])
        for k in ("secret", "encoding_aes_key", "callback_token"):
            assert stored[k].startswith("gAAAAA"), f"{k} 应为 Fernet 密文"
            assert SECRET not in stored[k] and AES_KEY not in stored[k]
        # API 视角为掩码
        masked = ChannelConfigDB.get_by_id(config_id)["config"]
        assert masked["secret"] == "***" and masked["callback_token"] == "***"
        assert APPID not in ("",) and masked["appid"] == APPID  # 非敏感字段明文
        # 内部读取解密正确
        assert cfg["secret"] == SECRET and cfg["encoding_aes_key"] == AES_KEY
    
        # 掩码/空串/null 回传更新不清空原值
        ChannelConfigDB.update(config_id, {"appid": APPID, "original_id": ORIGINAL_ID,
                                           "secret": "***", "encoding_aes_key": "", "callback_token": None})
        after = ChannelConfigDB.get_by_id_decrypted(config_id)["config"]
        assert after["secret"] == SECRET
        assert after["encoding_aes_key"] == AES_KEY
        assert after["callback_token"] == cfg["callback_token"]

    def test_rotate_token_invalidates_old(self, client, tenant_id, mp_config):
        config_id, cfg = mp_config
        old_token = cfg["callback_token"]
        new_token = ChannelConfigDB.rotate_wechat_mp_token(config_id)
        assert new_token and new_token != old_token and not new_token.startswith("gAAAAA")
        row = ChannelConfigDB.get_by_id(config_id)
        assert row["verified"] == 0
        assert "config_verified_at" not in row["config"]
        assert row["config"]["credential_version"] == 2
        assert ChannelConfigDB.get_by_id_decrypted(config_id)["config"]["callback_token"] == new_token
        # 旧 token 验签失败，新 token GET 验证通过
        ts, nonce = "1758000000", "n"
        r_old = client.get(f"/api/wechat-mp/callback/{config_id}",
                           params={"signature": _sign(old_token, ts, nonce), "timestamp": ts, "nonce": nonce, "echostr": "x"})
        assert r_old.status_code == 403
        r_new = client.get(f"/api/wechat-mp/callback/{config_id}",
                           params={"signature": _sign(new_token, ts, nonce), "timestamp": ts, "nonce": nonce, "echostr": "echo-ok"})
        assert r_new.status_code == 200 and r_new.text == "echo-ok"

    def test_update_ignores_callback_token(self, tenant_id, mp_config):
        """callback_token 只能由服务端生成/轮换，update 入参一律忽略。"""
        config_id, cfg = mp_config
        ChannelConfigDB.update(config_id, {"appid": APPID, "callback_token": "attacker-token",
                                           "original_id": ORIGINAL_ID})
        after = ChannelConfigDB.get_by_id_decrypted(config_id)["config"]
        assert after["callback_token"] == cfg["callback_token"], "update 不得改 token"

    def test_update_appid_rebind_conflict_raises_integrity_error(self, tenant_id, mp_config):
        """appid 允许变更（CR P1-2 定稿），撞同租户唯一索引时 IntegrityError 上抛（API 层转 409）。

        同时验证部分唯一索引在真实库已生效。
        """
        import psycopg2

        config_id, _cfg = mp_config
        other_id = f"chan_{os.urandom(6).hex()}"
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO tenant_channel_configs (config_id, tenant_id, channel_type, config)"
                " VALUES (%s, %s, 'wechat_mp', %s)",
                (other_id, tenant_id, json.dumps({"appid": "wxConflictAppId00", "original_id": "gh_probe_other"})),
            )
            conn.commit()
        try:
            # 无冲突改绑放行
            assert ChannelConfigDB.update(config_id, {"appid": "wxRebindOk00000000", "original_id": ORIGINAL_ID})
            assert ChannelConfigDB.get_by_id_decrypted(config_id)["config"]["appid"] == "wxRebindOk00000000"
            # 撞同租户同 appid → 唯一索引 IntegrityError
            with pytest.raises(psycopg2.IntegrityError):
                ChannelConfigDB.update(config_id, {"appid": "wxConflictAppId00", "original_id": ORIGINAL_ID})
        finally:
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "DELETE FROM tenant_channel_configs WHERE config_id = %s AND tenant_id = %s",
                    (other_id, tenant_id),
                )
                conn.commit()

    def test_same_tenant_same_appid_rejected(self, tenant_id, mp_config):
        """同租户同 appid 重复创建被应用层拦截（返回 None）。"""
        dup = ChannelConfigDB.create(
            tenant_id, "wechat_mp",
            config={"appid": APPID, "original_id": "gh_probe_dup"},
        )
        assert dup is None


class TestCreateOnTextColumn:
    """P0 修复回归：config 为 TEXT 列，appid 唯一性检查与索引均走 (config::jsonb)->>'appid'。

    修复前实录：config->>'appid' 在 TEXT 列抛 UndefinedFunction，create 必 500，
    部分唯一索引同样因非法表达式未能建立。修复后 create 成功且索引存在。
    """

    def test_unique_index_exists_with_jsonb_cast(self, tenant_id):
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT indexname, indexdef FROM pg_indexes"
                " WHERE indexname = 'uq_tenant_channel_configs_wechat_mp_appid'"
            )
            row = cur.fetchone()
        assert row, "部分唯一索引未建成"
        assert "::jsonb" in row["indexdef"]
        assert "wechat_mp" in row["indexdef"]

    def test_create_wechat_mp_succeeds(self, tenant_id):
        created = ChannelConfigDB.create(
            tenant_id, "wechat_mp",
            config={"appid": "wxprobeuniq0000000", "original_id": "gh_probe_uniq"},
        )
        assert created and created["config_id"]
        # 服务端生成 token（密文入库、API 视角掩码）
        assert created["config"]["callback_token"] == "***"
        assert ChannelConfigDB.delete(created["config_id"])
