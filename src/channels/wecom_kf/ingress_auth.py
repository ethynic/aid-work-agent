"""Private current-config proofs for the KF ingress; never caller supplied identity."""

import hashlib
import json
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.channels.wecom.crypto import WeComCrypto


class KfIngressError(Exception):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


def text(value: Any, *, limit: int = 256, optional: bool = False) -> str:
    if optional and value in (None, ""):
        return ""
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > limit or "\x00" in value:
        raise KfIngressError("KF_INGRESS_INVALID_FIELD", 400)
    return value


def encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def nonnegative(value):
    if type(value) is not int or not 0 <= value <= 9223372036854775807:
        raise KfIngressError("KF_INGRESS_INVALID_FIELD", 400)
    return value


@dataclass(frozen=True)
class AccountProof:
    tenant_id: str
    config_id: str
    corp_id: str
    open_kfid: str
    profile_id: str
    raw_profile: str
    config_version: datetime

    @property
    def account_id(self) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, encoded([
            "wecom_kf", self.tenant_id, self.config_id, self.corp_id, self.open_kfid])))


@dataclass(frozen=True)
class CurrentAccount:
    proof: AccountProof
    secret: str = field(repr=False)
    token: str = field(repr=False)
    encoding_aes_key: str = field(repr=False)


def config_in_tx(cursor, tenant_id: str, config_id: str, *, lock: bool = False):
    text(tenant_id); text(config_id)
    cursor.execute("""SELECT tenant_id,config_id,channel_type,config,verified,
        subagent_type,updated_at FROM tenant_channel_configs
        WHERE tenant_id=%s AND config_id=%s AND channel_type='wecom_kf'"""
        + (" FOR SHARE" if lock else ""), (tenant_id, config_id))
    row = cursor.fetchone()
    if row is None or row["verified"] != 1 or not isinstance(row["updated_at"], datetime):
        raise KfIngressError("KF_INGRESS_CONFIG_UNAVAILABLE", 403)
    try:
        cfg = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]
    except (ValueError, TypeError):
        raise KfIngressError("KF_INGRESS_CONFIG_INVALID", 403) from None
    if not isinstance(cfg, dict) or cfg.get("enabled", True) is not True:
        raise KfIngressError("KF_INGRESS_CONFIG_UNAVAILABLE", 403)
    return row, cfg


def current_config_in_tx(cursor, tenant_id: str, config_id: str, open_kfid: str,
                         *, lock: bool = False) -> CurrentAccount:
    """Fresh exact lookup. A cached Factory adapter is never authorization."""
    text(open_kfid)
    row, cfg = config_in_tx(cursor, tenant_id, config_id, lock=lock)
    accounts = cfg.get("kf_account")
    if not isinstance(accounts, list) or len(accounts) > 1000:
        raise KfIngressError("KF_INGRESS_CONFIG_INVALID", 403)
    matches = [item for item in accounts if isinstance(item, dict) and item.get("open_kfid") == open_kfid]
    if len(matches) != 1:
        raise KfIngressError("KF_INGRESS_ACCOUNT_UNAVAILABLE", 403)
    raw_selector = matches[0].get("subagent_type")
    raw_profile = text("" if raw_selector is None else raw_selector, optional=True)
    proof = AccountProof(tenant_id, config_id, text(cfg.get("corp_id")), open_kfid,
                         raw_profile or "main", raw_profile, row["updated_at"])
    return CurrentAccount(proof, text(cfg.get("secret"), limit=1024),
                          text(cfg.get("token"), limit=1024), text(cfg.get("encoding_aes_key"), limit=128))


def assert_same(current: AccountProof, expected: AccountProof):
    if (current.tenant_id, current.config_id, current.corp_id, current.open_kfid,
        current.profile_id, current.config_version) != (expected.tenant_id, expected.config_id,
        expected.corp_id, expected.open_kfid, expected.profile_id, expected.config_version):
        raise KfIngressError("KF_INGRESS_CONFIG_CHANGED")


def callback_account_in_tx(cursor, tenant_id: str, config_id: str, query: dict, body: bytes):
    # The account ID is inside the authenticated plaintext. Never select it
    # from an unauthenticated outer XML to invent the account authorization.
    row, cfg = config_in_tx(cursor, tenant_id, config_id, lock=True)
    event = decrypt_callback(text(cfg.get("token"), limit=1024),
        text(cfg.get("encoding_aes_key"), limit=128), text(cfg.get("corp_id")), query, body)
    account = current_config_in_tx(cursor, tenant_id, config_id,
                                  text(event.findtext("OpenKfId")), lock=True)
    if account.proof.config_version != row["updated_at"]:
        raise KfIngressError("KF_INGRESS_CONFIG_CHANGED")
    return account, callback_message(event, account.proof)


def decrypt_callback(token: str, key: str, corp_id: str, query: dict, body: bytes):
    """Only the configured encrypted mode proves a durable pull notification."""
    # 微信标准回调 XML 的文本节点用 <![CDATA[...]]> 包裹（官方格式，Encrypt 字段同），
    # 注入防护只允许精确拦截 DTD/实体声明（XXE），不得用 "<!" 泛匹配拒绝 CDATA 载荷。
    if (not isinstance(body, bytes) or len(body) > 65536
            or b"<!DOCTYPE" in body or b"<!ENTITY" in body):
        raise KfIngressError("KF_INGRESS_INVALID_CALLBACK", 400)
    try:
        outer = ET.fromstring(body)
        encrypted = text(outer.findtext("Encrypt"), limit=65536)
        signature = text(query.get("msg_signature"), limit=64)
        timestamp = text(query.get("timestamp"), limit=16)
        nonce = text(query.get("nonce"), limit=256)
        if not timestamp.isdecimal():
            raise KfIngressError("KF_INGRESS_INVALID_CALLBACK", 400)
        crypto = WeComCrypto(token, key, corp_id)
        if not crypto.verify_signature(signature, timestamp, nonce, encrypted):
            raise KfIngressError("KF_INGRESS_SIGNATURE_INVALID", 403)
        plain = crypto.decrypt(encrypted)
        if (len(plain.encode("utf-8")) > 65536
                or "<!DOCTYPE" in plain or "<!ENTITY" in plain):
            raise KfIngressError("KF_INGRESS_INVALID_CALLBACK", 400)
        event = ET.fromstring(plain)
        if len({child.tag for child in event}) != len(event):
            raise KfIngressError("KF_INGRESS_INVALID_CALLBACK", 400)
        if event.findtext("Event") not in ("kf_msg_or_event", "enter_session", "change_type"):
            raise KfIngressError("KF_INGRESS_CALLBACK_UNSUPPORTED", 403)
        # The opaque notification Token is not copied into the inbox. Its use
        # in sync_msg remains the existing API mode, not a guessed new grant.
        return event
    except KfIngressError:
        raise
    except (ET.ParseError, ValueError, TypeError, IndexError, UnicodeError):
        raise KfIngressError("KF_INGRESS_INVALID_CALLBACK", 403) from None


@dataclass(frozen=True)
class InboxMessage:
    message_id: str
    actor_id: str
    origin: int
    message_type: str
    send_time: int
    payload: dict = field(repr=False)
    namespace: str = "sync"
    capability_ciphertext: str | None = field(default=None, repr=False)

    @property
    def digest(self):
        return hashlib.sha256(encoded(self.payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VerifiedPage:
    messages: tuple[InboxMessage, ...]
    next_cursor: str = field(repr=False)
    has_more: bool


def _capability(value):
    if not value:
        return None
    from src.core.secret_crypto import encrypt_secret
    return encrypt_secret(encoded({"welcome_code": text(value, limit=4096)}))


def callback_message(event, proof):
    kind = event.findtext("Event")
    if kind == "kf_msg_or_event":
        return None
    change = text(event.findtext("ChangeType"), optional=True)
    if kind == "change_type" and change != "session_status_change":
        raise KfIngressError("KF_INGRESS_CALLBACK_UNSUPPORTED", 403)
    created = text(event.findtext("CreateTime"), limit=16)
    if not created.isdecimal():
        raise KfIngressError("KF_INGRESS_INVALID_CALLBACK", 400)
    actor = text(event.findtext("ExternalUserID") or event.findtext("ExternalUserId"), optional=True)
    fields = {"event_type": kind, "change_type": change, "external_userid": actor}
    for name, key in (("Scene", "scene"), ("ServiceState", "session_status"),
                      ("ServicerUserId", "servicer_userid"), ("Status", "status")):
        value = event.findtext(name)
        if value:
            fields[key] = text(value, limit=4096)
    # This is a callback lifecycle identity, never a user/provider message ID.
    created = nonnegative(int(created))
    grant = text(event.findtext("Code"), limit=4096, optional=True)
    grant_digest = hashlib.sha256(grant.encode()).hexdigest() if grant else None
    identity = str(uuid.uuid5(uuid.NAMESPACE_URL, encoded([proof.account_id, created, fields, grant_digest])))
    payload = {"event": fields, "msgtype": "event", "origin": 0,
               "send_time": created, "external_userid": actor}
    return InboxMessage(identity, actor, 0, "event", created, payload,
                        "callback", _capability(grant))


def bounded_page(reply: Any, proof: AccountProof, *, limit: int = 100,
                 max_bytes: int = 1048576) -> VerifiedPage:
    if not isinstance(reply, dict) or type(reply.get("errcode")) is not int or reply["errcode"] != 0:
        raise KfIngressError("KF_INGRESS_PULL_REJECTED")
    raw_messages = reply.get("msg_list")
    if not isinstance(raw_messages, list) or len(raw_messages) > limit or type(reply.get("has_more")) is not int or reply["has_more"] not in (0, 1):
        raise KfIngressError("KF_INGRESS_PAGE_INVALID")
    cursor = text(reply.get("next_cursor"), limit=4096, optional=not reply["has_more"])
    messages = []
    nested = {"text": ("content",), "image": ("media_id",), "voice": ("media_id", "recognition"),
              "video": ("media_id",), "file": ("media_id", "file_name"),
              "link": ("title", "desc", "url"),
              "event": ("event_type", "external_userid", "open_kfid", "recall_msgid", "servicer_userid",
                        "old_servicer_userid", "new_servicer_userid", "scene", "session_status", "change_type")}
    for item in raw_messages:
        if not isinstance(item, dict):
            raise KfIngressError("KF_INGRESS_PAGE_INVALID")
        mid = text(item.get("msgid")); kind = text(item.get("msgtype"), limit=32)
        origin, sent = item.get("origin"), item.get("send_time")
        if type(origin) is not int or not 0 <= origin <= 10:
            raise KfIngressError("KF_INGRESS_PAGE_INVALID")
        nonnegative(sent)
        if item.get("open_kfid", proof.open_kfid) != proof.open_kfid:
            raise KfIngressError("KF_INGRESS_PAGE_INVALID")
        block = item.get(kind, {})
        if kind in nested and not isinstance(block, dict):
            raise KfIngressError("KF_INGRESS_PAGE_INVALID")
        safe_block = {key: block[key] for key in nested.get(kind, ()) if key in block}
        if kind == "voice":
            text(safe_block.get("media_id"), limit=4096)

        for key, value in safe_block.items():
            if key == "session_status":
                nonnegative(value)
            else:
                text(value, limit=32768 if key == "recognition" else 65536 if key == "content" else 4096, optional=key not in ("media_id",))
        if kind == "event" and safe_block.get("open_kfid", proof.open_kfid) != proof.open_kfid:
            raise KfIngressError("KF_INGRESS_PAGE_INVALID")
        top_actor = text(item.get("external_userid"), optional=True)
        event_actor = text(safe_block.get("external_userid"), optional=True)
        if top_actor and event_actor and top_actor != event_actor:
            raise KfIngressError("KF_INGRESS_PAGE_INVALID")
        actor = top_actor or event_actor
        if kind == "event":
            safe_block["open_kfid"] = proof.open_kfid
            safe_block["external_userid"] = actor
        if origin in (3, 5) and not actor:
            raise KfIngressError("KF_INGRESS_PAGE_INVALID")
        payload = {"msgid": mid, "external_userid": actor, "origin": origin,
                   "msgtype": kind, "send_time": sent, kind: safe_block}
        if kind not in nested:
            payload["unsupported"] = True
        # Employee identity stays independent of the bound external customer.
        # Context history consumes this original field without swapping actors.
        if item.get("servicer_userid"):
            payload["servicer_userid"] = text(item["servicer_userid"])
        messages.append(InboxMessage(mid, actor, origin, kind, sent, payload,
                                    "sync", _capability(block.get("welcome_code") if kind == "event" else None)))
    if len(encoded([message.payload for message in messages]).encode("utf-8")) > max_bytes:
        raise KfIngressError("KF_INGRESS_PAGE_TOO_LARGE")
    return VerifiedPage(tuple(messages), cursor, reply["has_more"] == 1)
