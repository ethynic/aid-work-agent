"""wecom_personal_rpa 会话存档密钥诊断脚本

排查「拉到消息但 RSA 解密失败」问题：
1. 从数据库读取 wecom_personal_rpa 配置的私钥
2. 推导对应公钥
3. 调 C SDK 拉一条会话存档密文
4. 打印 publickey_ver（企微告诉我们用哪版公钥加密）
5. 尝试用数据库私钥解密

用法（容器内）：
    python /app/scripts/check_archive_keys.py chan_616337ad19f6

输出说明：
- publickey_ver=1 + decrypt_fail → 企微用旧公钥，数据库是新私钥（不匹配）
- publickey_ver=2 + decrypt_ok   → 企微用新公钥，数据库是新私钥（匹配）
- publickey_ver=2 + decrypt_fail → 企微用新公钥，但数据库私钥对应别的公钥（上传错了）
"""

import asyncio
import sys

from cryptography.hazmat.primitives import serialization

from src.db.database import init_postgres_pool
from src.saas.db.channel_config_db import ChannelConfigDB
from src.channels.wecom_personal_rpa.archive import wecom_finance_sdk, chat_crypto


def main():
    config_id = sys.argv[1] if len(sys.argv) > 1 else "chan_616337ad19f6"

    init_postgres_pool()

    cfg = ChannelConfigDB.get_by_id_decrypted(config_id)
    if not cfg:
        print(f"配置不存在: {config_id}")
        return

    c = cfg.get("config") or {}
    private_key = c.get("private_key")
    if not private_key:
        print("数据库 private_key 为空")
        return

    # 推导公钥（应与企微后台配置一致）
    priv_obj = serialization.load_pem_private_key(
        private_key.encode("utf-8"), password=None
    )
    pub_pem = priv_obj.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    print("=" * 60)
    print("数据库私钥推导公钥（应与企微后台配置一致）：")
    print("=" * 60)
    print(pub_pem)
    print(f"私钥 key_size: {priv_obj.key_size} bits")
    print()

    # 拉一条密文
    raw = wecom_finance_sdk.get_chat_data_raw(
        corpid=c["corp_id"],
        secret=c["archive_secret"],
        seq=0,
        limit=5,
    )

    items = raw.get("chatdata", [])
    if not items:
        print("未拉到任何消息（企业可能还没产生会话存档）")
        return

    print(f"拉到 {len(items)} 条消息：")
    print("=" * 60)
    for item in items:
        seq = item.get("seq")
        msgid = item.get("msgid")
        pk_ver = item.get("publickey_ver")
        set_no = item.get("set_no", "N/A")
        msg_type = item.get("msgtype")
        from_ = item.get("from", "N/A")
        print(f"seq={seq} publickey_ver={pk_ver} set_no={set_no} msgtype={msg_type}")
        print(f"  msgid={msgid}")
        print(f"  from={from_}")
        print(f"  encrypt_random_key 前 30 字符: {item.get('encrypt_random_key', '')[:30]}")
        print()

    # 尝试解密每一条
    print("=" * 60)
    print("尝试用数据库私钥解密：")
    print("=" * 60)
    for item in items:
        seq = item.get("seq")
        pk_ver = item.get("publickey_ver")
        try:
            rk = chat_crypto.decrypt_random_key(
                c["private_key"], item["encrypt_random_key"]
            )
            print(f"seq={seq} publickey_ver={pk_ver} → 解密成功，random_key 长度={len(rk)}")
        except Exception as e:
            print(f"seq={seq} publickey_ver={pk_ver} → 解密失败: {e}")


if __name__ == "__main__":
    main()
