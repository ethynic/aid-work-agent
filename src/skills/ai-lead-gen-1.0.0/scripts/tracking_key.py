#!/usr/bin/env python3
"""自动生成并持久化每个发件邮箱的 tracking_key（track_tenant.secret）。

移植自上游 b2b-lead-gen tracking_key.py，零配置口径不变：
- track_tenant = md5(from_email.lower())[:8]
    稳定、按发件人隔离，且匿名（不泄露邮箱明文）。
- secret = md5(from_email.lower() + "|" + random_salt)  —— MD5 固定 32 位 hex。
    random_salt 在「第一次为该邮箱生成」时随机产生并持久化，
    保证发送 / 回拉使用同一把 key（读取接口用 secret 鉴权）。

我方改造点（与上游差异）：
- key store 从 ~/.workbuddy/b2b_tracking_keys.json 改为租户级
  storage/tenants/{tenant_id}/lead-gen/tracking_keys.json，
  身份取 skill_executor 注入的 AID_TENANT_ID（缺身份 fail-fast）；
- 服务端 /api/track/* 对 k 严格校验 hex8.hex32（防注入），
  手动覆盖的 tracking_key 同样必须符合该格式。
"""
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

# 进程内缓存，避免同一次运行里反复读盘
_cache = {}


def _hash(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _tenant_dir():
    tenant_id = (os.environ.get("AID_TENANT_ID")
                 or os.environ.get("CURRENT_TENANT_ID") or "").strip()
    if not tenant_id:
        raise SystemExit(
            "ERROR: 缺少可信执行身份（AID_TENANT_ID），本工具必须经 skill_execute "
            "在智能体执行上下文中调用，tracking_key 才能落到租户目录")
    from src.core.storage import configured_storage_root, normalize_tenant_id
    return (configured_storage_root() / "tenants" / normalize_tenant_id(tenant_id)
            / "lead-gen")


def _load_store():
    path = _tenant_dir() / "tracking_keys.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"keys": {}}
    return {"keys": {}}


def _save_store(store):
    directory = _tenant_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "tracking_keys.json").write_text(
        json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_or_create_key(from_email, override=None):
    """返回 (track_tenant, secret, key)。

    override 为手动指定的 "track_tenant.secret"（高级用法，优先级最高）；
    服务端要求两边均为 hex（track_tenant 8 位、secret 32 位），不符合即拒绝。
    否则按 from_email 自动生成并持久化到租户目录。
    """
    if override and "." in str(override):
        key = str(override).strip()
        tenant, _, secret = key.partition(".")
        import re
        if not re.match(r"^[0-9a-f]{8}$", tenant) or not re.match(r"^[0-9a-f]{32}$", secret):
            raise SystemExit(
                "ERROR: 手动指定的 tracking_key 不符合服务端校验格式。\n"
                "       必须是 <8位hex>.<32位hex>（如 abc123de.<md5hex32>）；"
                "一般留空让系统按发件邮箱自动生成即可。")
        return tenant, secret, key

    email = (from_email or "").strip().lower()
    if not email:
        raise SystemExit(
            "ERROR: 无法自动生成 tracking_key —— 配置里缺少发件邮箱。\n"
            "       发送邮件必须配置 from_email，tracking_key 才能据此自动生成。")

    if email in _cache:
        rec = _cache[email]
        return rec["tenant"], rec["secret"], f"{rec['tenant']}.{rec['secret']}"

    store = _load_store()
    keys = store.setdefault("keys", {})
    if email in keys:
        rec = keys[email]
        _cache[email] = rec
        return rec["tenant"], rec["secret"], f"{rec['tenant']}.{rec['secret']}"

    # 第一次：生成并持久化
    tenant = _hash(email)[:8]
    salt = secrets.token_hex(8)            # 16 hex 字符的随机数
    secret = _hash(f"{email}|{salt}")      # 32 hex 字符（MD5 固定长度）
    rec = {
        "tenant": tenant,
        "secret": secret,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    keys[email] = rec
    _save_store(store)
    _cache[email] = rec
    return tenant, secret, f"{tenant}.{secret}"
