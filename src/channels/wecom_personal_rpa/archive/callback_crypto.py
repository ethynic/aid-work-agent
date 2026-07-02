"""企微会话存档回调签名校验 + AES 解密

复用 ``src.channels.wecom.crypto.WeComCrypto`` 的算法实现（企微加解密协议所有渠道通用）。
本模块在其基础上补两层：
1. ``hmac.compare_digest`` 防时序攻击（参考飞书审核 issue + wecom 渠道同一改进）
2. 组合 API（verify_and_decode_echostr / verify_and_decrypt_event），方便 callback_handler 直接调用

协议参考：https://developer.work.weixin.qq.com/document/path/90930

异常类 SignatureError 用于双验签兼容路由（设计文档 §5.2）识别「企微签名验签失败」、
回退到客户端 HMAC 验签分支。
"""

import hmac

from src.channels.wecom.crypto import WeComCrypto


class SignatureError(Exception):
    """签名验证失败或 AES 解密失败（用于双验签兼容路由识别回退分支）。"""


def _new_crypto(token: str, encoding_aes_key: str, corp_id: str) -> WeComCrypto:
    """构造 WeComCrypto 实例。

    WeComCrypto.__init__ 在 encoding_aes_key 长度异常时会抛 ValueError，
    上层应捕获并转 SignatureError。
    """
    try:
        return WeComCrypto(token=token, encoding_aes_key=encoding_aes_key, corp_id=corp_id)
    except ValueError as e:
        raise SignatureError(f"EncodingAESKey 无效: {e}") from e


def verify_signature(
    token: str,
    encoding_aes_key: str,
    corp_id: str,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    encrypt: str,
) -> bool:
    """验签：SHA1(sort([token, timestamp, nonce, encrypt])) == msg_signature。

    用 hmac.compare_digest 防时序攻击。
    """
    crypto = _new_crypto(token, encoding_aes_key, corp_id)
    calculated = crypto.generate_signature(timestamp=timestamp, nonce=nonce, encrypt=encrypt)
    return hmac.compare_digest(calculated, msg_signature)


def decrypt_aes(token: str, encoding_aes_key: str, corp_id: str, encrypt_b64: str) -> str:
    """AES-CBC-256 解密 encrypt_b64，返回明文（同时校验 corp_id）。

    失败抛 SignatureError。
    """
    crypto = _new_crypto(token, encoding_aes_key, corp_id)
    try:
        return crypto.decrypt(encrypt_b64)
    except ValueError as e:
        raise SignatureError(f"AES 解密失败: {e}") from e


def verify_and_decode_echostr(
    token: str,
    encoding_aes_key: str,
    corp_id: str,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    echostr: str,
) -> str:
    """URL 验证（GET echostr）：验签 + 解密 echostr，返回明文 echostr 原样回传给企微。

    用于企微首次配置回调 URL 时发的 GET 请求。流程：
    1. 用 echostr 作为 encrypt 字段验签
    2. 验签通过后 AES 解密 echostr（解密结果就是 echostr 明文本身）
    3. 返回明文 echostr 给企微
    """
    if not verify_signature(
        token, encoding_aes_key, corp_id, msg_signature, timestamp, nonce, echostr
    ):
        raise SignatureError("echostr 验签失败")
    return decrypt_aes(token, encoding_aes_key, corp_id, echostr)


def verify_and_decrypt_event(
    token: str,
    encoding_aes_key: str,
    corp_id: str,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    encrypt_field: str,
) -> str:
    """事件接收（POST）：验签 + AES 解密 encrypt 字段，返回事件明文。

    返回解密后的明文字符串。**事件明文格式由企微决定**：

    - 会话存档回调事件（``产生会话回调事件``，文档 path/95039）：明文为 **XML**
      （外层 ``<xml><ToUserName/><Encrypt/></xml>`` 解密后是内层 XML，含 ``MsgType``
      ``Event`` 等字段）
    - 客户同意聊天内容存档事件（文档 path/92005）：明文同样是 XML
    - 主动拉取 SDK 返回的会话内容（非回调）才是 JSON

    本函数不绑定任何特定格式（JSON / XML），由 ``callback_handler`` 根据业务场景
    自行解析，避免格式假设导致正常事件被误判为错误。

    用于 callback_handler 收到企微 POST 事件时调用。
    """
    if not verify_signature(
        token, encoding_aes_key, corp_id, msg_signature, timestamp, nonce, encrypt_field
    ):
        raise SignatureError("事件验签失败")
    return decrypt_aes(token, encoding_aes_key, corp_id, encrypt_field)
