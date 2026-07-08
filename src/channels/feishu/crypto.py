"""
飞书消息加解密模块

实现飞书事件订阅的 AES-256-CBC 加解密协议。
协议参考: https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/encryption-key-encryption-algorithm-case

密钥派生:
  AES key = SHA256(encrypt_key)，固定 32 字节。
  IV 在密文前缀取（Base64 解码后的前 16 字节），不从 key 中取。

签名算法（v2.0 事件）:
  SHA256(timestamp + nonce + encrypt_key + body)
  注意拼接的是 encrypt_key 原文，不是 SHA256 后的 aes_key。

解密流程（与企微不同，飞书 v2.0 解密后是裸 JSON，无随机前缀和 msg_len 头）:
  Base64 解码 -> 取前 16 字节作 IV -> AES-256-CBC 解密 ->
  PKCS7 去填充（块大小 16，AES.block_size）-> JSON 解析返回 dict

对照飞书官方 SDK lark-oapi core/utils/decryptor.py 实现。
"""

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from loguru import logger


# 飞书允许的时间戳偏差上限（秒），超过即视为重放攻击
_TIMESTAMP_TOLERANCE_SECONDS = 3600

# AES 块大小（16 字节，PKCS7 填充以此为单位）
_AES_BLOCK_SIZE = 16


class FeishuCrypto:
    """飞书消息加解密"""

    # 兼容旧测试引用：块大小为 16（AES.block_size），非企微的 32
    BLOCK_SIZE = _AES_BLOCK_SIZE

    def __init__(self, verification_token: str, encrypt_key: str):
        """
        Args:
            verification_token: 事件配置的 Verification Token
            encrypt_key: 事件配置的 Encrypt Key（用于 AES 密钥派生和签名校验）
        """
        self.verification_token = verification_token
        self.encrypt_key = encrypt_key
        # 飞书官方：AES key = SHA256(encrypt_key)，固定 32 字节
        self.aes_key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
        # 注意：IV 不从 key 中取，而是从 Base64 解码密文后的前 16 字节取

    def decrypt(self, encrypt_data: str) -> dict:
        """
        解密 encrypt 字段。

        流程（与企微不同，飞书 v2.0 解密后直接是裸 JSON）:
        1. Base64 解码 -> enc_bytes
        2. IV = enc_bytes[:16]，ciphertext = enc_bytes[16:]
        3. AES-256-CBC 解密 -> PKCS7 去填充（块大小 16）-> 裸 JSON 字节串
        4. JSON 解析返回 dict

        Args:
            encrypt_data: Base64 编码的加密消息

        Returns:
            解密后的 dict

        Raises:
            ValueError: 解密失败或数据格式错误
        """
        # 1. Base64 解码
        enc_bytes = base64.b64decode(encrypt_data)

        if len(enc_bytes) < _AES_BLOCK_SIZE * 2:
            # 至少需要 1 个 IV 块 + 1 个密文块
            raise ValueError("密文太短，无法提取 IV")
        if len(enc_bytes) % _AES_BLOCK_SIZE != 0:
            raise ValueError("密文长度不是 AES 块大小的整数倍")

        # 2. IV 从密文前 16 字节取（关键差异：不是从 key 取）
        iv = enc_bytes[:_AES_BLOCK_SIZE]
        ciphertext = enc_bytes[_AES_BLOCK_SIZE:]

        # 3. AES-256-CBC 解密
        cipher = Cipher(algorithms.AES(self.aes_key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(ciphertext) + decryptor.finalize()
        if not decrypted:
            raise ValueError("解密后数据为空")

        # PKCS7 去填充（块大小 16，AES.block_size）
        pad_len = decrypted[-1]
        if pad_len < 1 or pad_len > _AES_BLOCK_SIZE or pad_len > len(decrypted):
            raise ValueError(f"无效的 PKCS7 填充长度: {pad_len}")
        if decrypted[-pad_len:] != bytes([pad_len] * pad_len):
            raise ValueError("PKCS7 填充字节不一致")
        plain_bytes = decrypted[:-pad_len]

        if not plain_bytes:
            raise ValueError("解密后明文为空")

        # 4. JSON 解析（飞书 v2.0 解密后直接是裸 JSON，无随机前缀和 msg_len 头）
        try:
            return json.loads(plain_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"解密后明文不是合法 JSON: {e}")

    def encrypt(self, data: dict, app_id: str = "") -> str:
        """
        加密数据（主要用于测试和回复加密）。

        流程（与企微不同，飞书 v2.0 明文就是裸 JSON）:
        1. JSON 序列化 -> json_bytes
        2. PKCS7 填充到 16 字节块边界
        3. 生成随机 16 字节 IV
        4. AES-256-CBC 加密
        5. IV + 密文 -> Base64 编码

        Args:
            data: 要加密的 dict
            app_id: 兼容旧接口签名，飞书 v2.0 不附加 app_id 到明文末尾，此参数被忽略

        Returns:
            Base64 编码的加密消息
        """
        _ = app_id  # 兼容旧调用签名，飞书 v2.0 不使用
        json_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")

        # PKCS7 填充到 AES 块边界（16 字节）
        # len % 16 范围 0..15，16 - (0..15) 范围 1..16，刚好对齐时 pad_len=16（补完整块）
        pad_len = _AES_BLOCK_SIZE - (len(json_bytes) % _AES_BLOCK_SIZE)
        plaintext = json_bytes + bytes([pad_len] * pad_len)

        # 随机 IV（每次加密都不同，与企微不同）
        iv = os.urandom(_AES_BLOCK_SIZE)

        # AES-256-CBC 加密
        cipher = Cipher(algorithms.AES(self.aes_key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        encrypted = encryptor.update(plaintext) + encryptor.finalize()

        # IV 前缀 + 密文 -> Base64
        return base64.b64encode(iv + encrypted).decode("utf-8")

    def verify_url_verification(self, body: dict) -> Optional[str]:
        """
        处理 url_verification 请求。

        1. 若 body 含 encrypt 字段，先 decrypt 得到明文
        2. 校验 body["token"] == self.verification_token
        3. 返回 body["challenge"]

        Args:
            body: 已解析的 JSON body

        Returns:
            challenge 字符串，校验失败返回 None
        """
        # 加密模式下先解密
        if "encrypt" in body:
            try:
                body = self.decrypt(body["encrypt"])
            except Exception as e:
                logger.error(f"[Feishu] url_verification 解密失败: {e}")
                return None

        if body.get("type") != "url_verification":
            return None

        token = body.get("token", "")
        if token != self.verification_token:
            logger.warning("[Feishu] url_verification token 不匹配")
            return None

        return body.get("challenge")

    def verify_signature(
        self, timestamp: str, nonce: str, body: str, signature: str
    ) -> bool:
        """
        校验 v2.0 事件的 X-Lark-Signature 请求头。

        算法: SHA256(timestamp + nonce + encrypt_key + body)
        注意：拼接的是 encrypt_key 原文，不是 SHA256 后的 aes_key。
        额外校验时间戳偏差（±1 小时），防止重放攻击。

        Args:
            timestamp: X-Lark-Request-Timestamp
            nonce: X-Lark-Request-Nonce
            body: 原始请求体字符串
            signature: X-Lark-Signature

        Returns:
            签名是否有效
        """
        # 时间戳偏差校验（防重放）
        try:
            ts = int(timestamp)
        except (TypeError, ValueError):
            logger.warning("[Feishu] 签名校验失败：时间戳非法")
            return False
        if abs(time.time() - ts) > _TIMESTAMP_TOLERANCE_SECONDS:
            logger.warning(
                f"[Feishu] 签名校验失败：时间戳偏差超限，now={int(time.time())}, ts={ts}"
            )
            return False

        content = timestamp + nonce + self.encrypt_key + body
        calculated = hashlib.sha256(content.encode("utf-8")).hexdigest()
        # 使用常量时间比较，防止时序攻击
        return hmac.compare_digest(calculated, signature)
