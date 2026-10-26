#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发件/收件邮箱三层解析（设计文档 §7.4）。

解析顺序（用户个人 -> 租户兜底 -> 报错）：
  1. user_email_settings（用户个人邮箱，EmailCredentialDB）
  2. tenant_email_settings（租户邮箱，TenantEmailCredentialDB）
  3. 两者皆无 -> 返回 None，调用方报错提示用户去配置
系统 .env（SMTP_*/IMAP_*）仅用于平台级通知，本技能禁止回退到系统邮箱。

供 send_emails.py（SMTP 发送）与后续回复监控（IMAP 轮询）共用。
身份来自 AID_* 环境变量（skill_executor 注入），解密只发生在本模块内存中，
密码不落盘、不打印、不进 LLM 上下文。
"""

import os
import sys
from pathlib import Path


def _identity():
    tenant_id = (os.environ.get("AID_TENANT_ID")
                 or os.environ.get("CURRENT_TENANT_ID") or "").strip()
    user_id = (os.environ.get("AID_USER_ID")
               or os.environ.get("CURRENT_USER_ID") or "").strip()
    return tenant_id, user_id


def _conn():
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from src.db.database import (
        get_db_connection as _get_conn,
        get_postgres_pool,
        init_postgres_pool,
    )
    if get_postgres_pool() is None:
        init_postgres_pool()
    return _get_conn()


def _decrypted_row(row):
    """(email_address, smtp_server, smtp_port, smtp_user, smtp_password_enc,
    smtp_encryption, imap_server, imap_port, imap_encryption) -> dict，密码已解密。"""
    from src.db.encryption import encryption_manager
    (email_address, smtp_server, smtp_port, smtp_user, smtp_password_enc,
     smtp_encryption, imap_server, imap_port, imap_encryption) = row
    return {
        "email_address": email_address,
        "smtp_server": smtp_server,
        "smtp_port": int(smtp_port),
        "smtp_user": smtp_user,
        "smtp_password": encryption_manager.decrypt(smtp_password_enc),
        "smtp_encryption": smtp_encryption or "ssl",
        "imap_server": imap_server,
        "imap_port": int(imap_port),
        "imap_encryption": imap_encryption or "ssl",
    }


_MAILBOX_COLUMNS = ("email_address, smtp_server, smtp_port, smtp_user, smtp_password, "
                    "smtp_encryption, imap_server, imap_port, imap_encryption")


def resolve_mailbox(tenant_id=None, user_id=None):
    """三层解析发件邮箱。返回 dict（含 level: user|tenant），无可用配置返回 None。

    密码已解密（仅内存），调用方不得打印或落盘。
    """
    tenant_id, user_id = tenant_id or _identity()[0], user_id or _identity()[1]
    if not tenant_id:
        raise RuntimeError("resolve_mailbox: 缺少租户身份（AID_TENANT_ID）")
    with _conn() as conn, conn.cursor() as cur:
        if user_id:
            cur.execute(
                f"SELECT {_MAILBOX_COLUMNS} FROM user_email_settings "
                "WHERE user_id = %s AND status = 'active'", (user_id,))
            row = cur.fetchone()
            if row:
                cfg = _decrypted_row(row)
                cfg["level"] = "user"
                return cfg
        cur.execute(
            f"SELECT {_MAILBOX_COLUMNS} FROM tenant_email_settings "
            "WHERE tenant_id = %s AND status = 'active'", (tenant_id,))
        row = cur.fetchone()
        if row:
            cfg = _decrypted_row(row)
            cfg["level"] = "tenant"
            return cfg
    return None


def resolve_mailbox_or_die():
    """resolve_mailbox 的 CLI 包装：无配置时报错并给出用户可操作的指引。"""
    cfg = resolve_mailbox()
    if cfg is None:
        print("ERROR: 尚未配置发件邮箱，无法发送邮件。")
        print("  请在「邮箱设置」中配置你的个人邮箱；")
        print("  或联系租户管理员在企业设置页配置租户邮箱。")
        sys.exit(3)
    return cfg


if __name__ == "__main__":
    cfg = resolve_mailbox_or_die()
    # 只输出非敏感字段，密码永不打印
    print({k: v for k, v in cfg.items() if "password" not in k})
