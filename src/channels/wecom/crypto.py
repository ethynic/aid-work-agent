"""
企业微信消息加解密模块

实现企业微信回调消息的 AES-256-CBC 加解密协议。
协议参考: https://developer.work.weixin.qq.com/document/path/90930

密钥派生:
  encoding_aes_key 为 43 字符 Base64，解码后得到 32 字节 AES-256 密钥。

签名算法:
  SHA1(sort([token, timestamp, nonce, encrypt]))

解密流程:
  Base64 解码 → AES-256-CBC 解密 → PKCS7 去填充 →
  去除 16 字节随机前缀 → 读取 4 字节消息长度 → 提取消息 → 验证 corp_id
"""

import base64
import hashlib
import hmac
import os
import struct
from typing import Tuple

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from loguru import logger


class WeComCrypto:
    """企业微信消息加解密"""

    BLOCK_SIZE = 32  # AES 块大小 (PKCS7 以 32 字节块填充)

    def __init__(self, token: str, encoding_aes_key: str, corp_id: str):
        """
        Args:
            token: 回调配置的 Token
            encoding_aes_key: 回调配置的 EncodingAESKey（43 字符 Base64）
            corp_id: 企业 ID
        """
        self.token = token
        self.corp_id = corp_id
        # Base64 解码 encoding_aes_key 得到 32 字节 AES 密钥
        # WeCom 的 encoding_aes_key 可能缺少 Base64 填充，需补 '='
        aes_key_str = encoding_aes_key + "=" * (4 - len(encoding_aes_key) % 4)
        self.aes_key = base64.b64decode(aes_key_str)
        if len(self.aes_key) != 32:
            raise ValueError(
                f"EncodingAESKey 解码后应为 32 字节，实际 {len(self.aes_key)} 字节"
            )

    def verify_signature(
        self, signature: str, timestamp: str, nonce: str, encrypt: str
    ) -> bool:
        """
        验证企业微信消息签名

        算法: SHA1(sort([token, timestamp, nonce, encrypt]))

        Args:
            signature: 请求中的 msg_signature 或 signature
            timestamp: 时间戳
            nonce: 随机数
            encrypt: 加密消息体（GET 时为 echostr，POST 时为 Encrypt 字段值）

        Returns:
            签名是否有效
        """
        items = [self.token, timestamp, nonce, encrypt]
        items.sort()
        combined = "".join(items)
        calculated = hashlib.sha1(combined.encode("utf-8")).hexdigest()
        # hmac.compare_digest 防时序攻击（与 wechat_mp 回调验签对齐）
        return hmac.compare_digest(calculated, signature)

    def decrypt(self, encrypted_text: str) -> str:
        """
        解密企业微信消息

        流程:
        1. Base64 解码
        2. AES-256-CBC 解密 (IV 为密钥的前 16 字节)
        3. PKCS7 去填充
        4. 去除前 16 字节随机内容
        5. 读取 4 字节网络字节序的消息长度
        6. 提取消息内容
        7. 验证尾部 corp_id

        Args:
            encrypted_text: Base64 编码的加密消息

        Returns:
            解密后的明文消息

        Raises:
            ValueError: 解密失败或 corp_id 不匹配
        """
        # Base64 解码
        encrypted_bytes = base64.b64decode(encrypted_text)

        # AES-256-CBC 解密，IV 为 aes_key 的前 16 字节
        iv = self.aes_key[:16]
        cipher = Cipher(algorithms.AES(self.aes_key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(encrypted_bytes) + decryptor.finalize()

        # PKCS7 去填充
        pad_len = decrypted[-1]
        if pad_len < 1 or pad_len > self.BLOCK_SIZE:
            raise ValueError(f"无效的 PKCS7 填充长度: {pad_len}")
        decrypted = decrypted[:-pad_len]

        # 去除 16 字节随机前缀
        content = decrypted[16:]

        # 读取 4 字节消息长度（网络字节序/大端）
        msg_len = struct.unpack("!I", content[:4])[0]
        msg_content = content[4 : 4 + msg_len]
        received_corp_id = content[4 + msg_len :]

        # 验证 corp_id
        received_corp_id_str = received_corp_id.decode("utf-8")
        if received_corp_id_str != self.corp_id:
            raise ValueError(
                f"corp_id 不匹配: 期望 '{self.corp_id}'，实际 '{received_corp_id_str}'"
            )

        return msg_content.decode("utf-8")

    def encrypt(self, reply_msg: str) -> str:
        """
        加密回复消息（被动回复时使用）

        流程:
        1. 生成 16 字节随机前缀
        2. 4 字节消息长度（大端）
        3. 消息内容 + corp_id
        4. PKCS7 填充到 32 字节块边界
        5. AES-256-CBC 加密（IV 为密钥前 16 字节）
        6. Base64 编码

        Args:
            reply_msg: 明文回复消息

        Returns:
            Base64 编码的加密消息
        """
        # 构造明文: random(16) + msg_len(4) + msg + corp_id
        msg_bytes = reply_msg.encode("utf-8")
        corp_id_bytes = self.corp_id.encode("utf-8")
        random_bytes = os.urandom(16)
        msg_len_bytes = struct.pack("!I", len(msg_bytes))

        plaintext = random_bytes + msg_len_bytes + msg_bytes + corp_id_bytes

        # PKCS7 填充到 BLOCK_SIZE 边界
        pad_len = self.BLOCK_SIZE - (len(plaintext) % self.BLOCK_SIZE)
        plaintext += bytes([pad_len] * pad_len)

        # AES-256-CBC 加密
        iv = self.aes_key[:16]
        cipher = Cipher(algorithms.AES(self.aes_key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        encrypted = encryptor.update(plaintext) + encryptor.finalize()

        return base64.b64encode(encrypted).decode("utf-8")

    def generate_signature(
        self, timestamp: str, nonce: str, encrypt: str
    ) -> str:
        """
        生成签名

        Args:
            timestamp: 时间戳
            nonce: 随机数
            encrypt: 加密后的消息体

        Returns:
            SHA1 签名字符串
        """
        items = [self.token, timestamp, nonce, encrypt]
        items.sort()
        combined = "".join(items)
        return hashlib.sha1(combined.encode("utf-8")).hexdigest()

    def generate_encrypted_reply(
        self, reply_msg: str, timestamp: str, nonce: str
    ) -> str:
        """
        生成完整的加密回复 XML

        用于被动回复模式（可选，当前使用主动消息 API 故非必需）。

        Args:
            reply_msg: 明文回复消息
            timestamp: 时间戳
            nonce: 随机数

        Returns:
            完整的加密 XML 字符串
        """
        encrypt = self.encrypt(reply_msg)
        signature = self.generate_signature(timestamp, nonce, encrypt)

        return (
            f"<xml>"
            f"<Encrypt><![CDATA[{encrypt}]]></Encrypt>"
            f"<MsgSignature><![CDATA[{signature}]]></MsgSignature>"
            f"<TimeStamp>{timestamp}</TimeStamp>"
            f"<Nonce><![CDATA[{nonce}]]></Nonce>"
            f"</xml>"
        )
