"""wecom_personal_rpa 渠道的 verify 路由专属验证逻辑

server 模式下，验证凭证有效性的完整链路：
1. 用 corp_id + archive_secret 调 get_access_token（验证拉取凭证）
2. 调 get_chat_data(seq=0, limit=1) 拉一条密文（验证 access_token 有效）
3. 用 private_key RSA 解密 encrypt_random_key（验证私钥）
4. 用 random_key AES 解密 encrypt_chat_msg（验证私钥与密文匹配）
5. 构造一个假事件，用 token + encoding_aes_key 自测验签 + AES 解密
   （验证回调凭证）

任一步失败返回详细错误诊断，前端据此给用户友好提示。

client 模式（第一期不开放）：verify_archive_client_mode 函数代码保留，
永远不会被调用（路由层注释掉调用入口）。
"""

import json
from typing import Any, Dict

from loguru import logger

from src.channels.wecom_personal_rpa.archive import callback_crypto, chat_crypto, http_client
from src.channels.wecom_personal_rpa.archive.callback_crypto import SignatureError
from src.channels.wecom_personal_rpa.archive.credential_codec import FORCED_LISTEN_MODE
from src.channels.wecom.crypto import WeComCrypto


def _err(stage: str, message: str, detail: str = "") -> Dict[str, Any]:
    """构造失败响应。"""
    return {
        "success": False,
        "verified": False,
        "stage": stage,
        "message": message,
        "detail": detail,
    }


async def verify_archive_server_mode(config_data: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
    """server 模式凭证验证：5 步链路自测。

    Args:
        config_data: 已解密的 config dict（含明文 corp_id / archive_secret /
            private_key / token / encoding_aes_key）。
        tenant_id: 租户 ID（access_token 缓存隔离用）。

    Returns:
        dict: {success, verified, stage?, message, detail?}
        - 成功：{"success": True, "verified": True, "message": "..."}
        - 失败：{"success": False, "verified": False, "stage": "...", "message": "..."}
    """
    corp_id = config_data.get("corp_id")
    archive_secret = config_data.get("archive_secret")
    private_key = config_data.get("private_key")
    token = config_data.get("token")
    encoding_aes_key = config_data.get("encoding_aes_key")

    # Step 0：字段完整性检查
    missing = []
    if not corp_id:
        missing.append("corp_id")
    if not archive_secret:
        missing.append("archive_secret")
    if not private_key:
        missing.append("private_key")
    if not token:
        missing.append("token")
    if not encoding_aes_key:
        missing.append("encoding_aes_key")
    if missing:
        return _err("fields", f"凭证字段缺失: {', '.join(missing)}")

    # Step 1：get_access_token（验证 corp_id + archive_secret）
    try:
        access_token = await http_client.get_access_token(
            tenant_id=tenant_id, corpid=corp_id, secret=archive_secret
        )
    except http_client.WeComApiException as e:
        # 40013 = invalid corpid；40125 = invalid secret
        if e.errcode in (40013, 40125):
            return _err(
                "access_token",
                "Corp ID 或会话存档 Secret 错误（企微拒绝签发 access_token）",
                detail=f"errcode={e.errcode} errmsg={e.errmsg}",
            )
        return _err("access_token", f"获取 access_token 失败: {e.errmsg}", detail=f"errcode={e.errcode}")
    except Exception as e:
        return _err("access_token", f"获取 access_token 网络异常: {type(e).__name__}: {e}")

    # Step 2：get_chat_data(seq=0, limit=1)（验证 access_token 有效 + 拉取权限）
    try:
        batch = await http_client.get_chat_data(access_token, seq=0, limit=1)
    except http_client.WeComRateLimitException:
        return _err(
            "chat_data",
            "企微返回 45009（接口频率限制），请稍后再试",
        )
    except http_client.WeComApiException as e:
        # 60011 = no privilege；48002 = no SDK permission
        if e.errcode in (60011, 48002):
            return _err(
                "chat_data",
                "应用未获得会话存档 SDK 权限（需在企业微信后台开通）",
                detail=f"errcode={e.errcode} errmsg={e.errmsg}",
            )
        return _err("chat_data", f"拉取会话存档失败: {e.errmsg}", detail=f"errcode={e.errcode}")
    except Exception as e:
        return _err("chat_data", f"拉取会话存档网络异常: {type(e).__name__}: {e}")

    # Step 3+4：RSA 解密 encrypt_random_key + AES 解密 encrypt_chat_msg（验证 private_key）
    # 注意：seq=0 拉到的可能是空批次（新企业还没消息），此时无法验证私钥——
    # 给出"暂时无消息可验证，私钥未测试"的提示，但回调签名仍可自测
    if not batch.items:
        logger.info(f"[verify] 拉取到空批次（新企业无消息），跳过私钥验证 tenant={tenant_id}")
        private_key_tested = False
    else:
        item = batch.items[0]
        try:
            random_key = chat_crypto.decrypt_random_key(private_key, item.encrypt_random_key)
            chat_crypto.decrypt_chat_msg(random_key, item.encrypt_chat_msg)
            private_key_tested = True
        except ValueError as e:
            return _err(
                "private_key",
                "RSA 私钥错误或与 encrypt_chat_msg 不匹配",
                detail=str(e),
            )
        except Exception as e:
            return _err(
                "private_key",
                f"私钥解密异常: {type(e).__name__}: {e}",
            )

    # Step 5：构造假事件自测验签 + AES 解密（验证 token + encoding_aes_key）
    # 用 WeComCrypto.encrypt 加密一段测试明文（自签自验）
    try:
        crypto = WeComCrypto(token, encoding_aes_key, corp_id)
        test_plain = "verify_test_event"
        encrypted = crypto.encrypt(test_plain)
        sig = crypto.generate_signature("verify_ts", "verify_nonce", encrypted)

        # 用 callback_crypto 解密（验签 + AES）
        decrypted = callback_crypto.verify_and_decrypt_event(
            token=token,
            encoding_aes_key=encoding_aes_key,
            corp_id=corp_id,
            msg_signature=sig,
            timestamp="verify_ts",
            nonce="verify_nonce",
            encrypt_field=encrypted,
        )
        if decrypted != test_plain:
            return _err(
                "callback",
                "回调验签通过但解密内容不匹配（疑似 EncodingAESKey 错误）",
                detail=f"expected={test_plain!r} actual={decrypted!r}",
            )
    except SignatureError as e:
        return _err(
            "callback",
            "Token 或 EncodingAESKey 错误（自测事件验签/解密失败）",
            detail=str(e),
        )
    except ValueError as e:
        return _err(
            "callback",
            f"EncodingAESKey 格式错误（应为 43 字符 Base64）: {e}",
        )
    except Exception as e:
        return _err(
            "callback",
            f"回调验签异常: {type(e).__name__}: {e}",
        )

    # 全部通过
    private_key_note = "" if private_key_tested else "（私钥暂未测试：企业暂无会话存档消息）"
    return {
        "success": True,
        "verified": True,
        "message": f"凭证验证通过{private_key_note}",
        "stages_passed": ["access_token", "chat_data"] +
                         (["private_key"] if private_key_tested else []) +
                         ["callback"],
    }


async def verify_archive_client_mode(config_data: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
    """client 模式凭证验证（第一期不开放，代码保留）。

    第一期服务端永远不下发 listen_mode='client'，此函数永远不会被调用。
    未来开放 client 模式时取消路由层注释即可启用。
    """
    client_secret = config_data.get("client_secret")
    if not client_secret:
        return _err("fields", "client_secret 字段缺失")

    # client 模式只需检查 client_secret 已设置 + 客户端注册存在
    # （真正的连通性验证由客户端心跳 + callback 上报体现）
    return {
        "success": True,
        "verified": True,
        "message": "client 模式凭证验证通过（client_secret 已设置）",
    }
