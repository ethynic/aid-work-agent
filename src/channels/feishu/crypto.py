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

解密流程:
  Base64 解码 → 取前 16 字节作 IV → AES-256-CBC 解密 →
  PKCS7 去填充（块大小 32）→ 去除 16 字节随机前缀 →
  读取 4 字节大端序消息长度 → 提取 JSON → 解析返回 dict
"""

import base64
import hashlib
import json
import os
import struct
from typing import Optional

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from loguru import logger


class FeishuCrypto:
    """飞书消息加解密"""

    BLOCK_SIZE = 32  # PKCS7 填充块大小（与企微相同）

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

        流程:
        1. Base64 解码 → enc_bytes
        2. IV = enc_bytes[:16]，ciphertext = enc_bytes[16:]
        3. AES-256-CBC 解密 → PKCS7 去填充 → content_bytes
        4. content_bytes 结构：[16字节随机串] + [4字节大端序消息长度] + [JSON] + [app_id]
        5. 跳过前 16 字节，读取 4 字节大端序得到 msg_len，截取 msg_len 字节的 JSON
        6. JSON 解析返回 dict

        Args:
            encrypt_data: Base64 编码的加密消息

        Returns:
            解密后的 dict

        Raises:
            ValueError: 解密失败或数据格式错误
        """
        # 1. Base64 解码
        enc_bytes = base64.b64decode(encrypt_data)

        if len(enc_bytes) < 16:
            raise ValueError("密文太短，无法提取 IV")

        # 2. IV 从密文前 16 字节取（关键差异：不是从 key 取）
        iv = enc_bytes[:16]
        ciphertext = enc_bytes[16:]

        if len(ciphertext) == 0:
            raise ValueError("密文为空")

        # 3. AES-256-CBC 解密
        cipher = Cipher(algorithms.AES(self.aes_key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(ciphertext) + decryptor.finalize()

        # PKCS7 去填充
        pad_len = decrypted[-1]
        if pad_len < 1 or pad_len > self.BLOCK_SIZE:
            raise ValueError(f"无效的 PKCS7 填充长度: {pad_len}")
        if decrypted[-pad_len:] != bytes([pad_len] * pad_len):
            raise ValueError("PKCS7 填充字节不一致")
        decrypted = decrypted[:-pad_len]

        # 4. 去除 16 字节随机前缀
        if len(decrypted) < 20:
            raise ValueError("解密后数据太短")
        content = decrypted[16:]

        # 5. 读取 4 字节大端序消息长度
        msg_len = struct.unpack("!I", content[:4])[0]
        if len(content) < 4 + msg_len:
            raise ValueError(
                f"消息长度不一致: 声明 {msg_len} 字节，实际 {len(content) - 4} 字节"
            )

        json_bytes = content[4:4 + msg_len]

        # 6. JSON 解析
        return json.loads(json_bytes.decode("utf-8"))

    def encrypt(self, data: dict, app_id: str = "") -> str:
        """
        加密数据（主要用于测试和回复加密）。

        流程:
        1. JSON 序列化 → json_bytes
        2. 构造明文: random(16) + msg_len(4) + json_bytes + app_id
        3. PKCS7 填充到 32 字节块边界
        4. 生成随机 16 字节 IV
        5. AES-256-CBC 加密
        6. IV + 密文 → Base64 编码

        Args:
            data: 要加密的 dict
            app_id: 附加在末尾的 app_id（可选）

        Returns:
            Base64 编码的加密消息
        """
        json_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
        app_id_bytes = app_id.encode("utf-8")

        # 构造明文: random(16) + msg_len(4) + json_bytes + app_id
        random_bytes = os.urandom(16)
        msg_len_bytes = struct.pack("!I", len(json_bytes))
        plaintext = random_bytes + msg_len_bytes + json_bytes + app_id_bytes

        # PKCS7 填充到 BLOCK_SIZE 边界
        pad_len = self.BLOCK_SIZE - (len(plaintext) % self.BLOCK_SIZE)
        plaintext += bytes([pad_len] * pad_len)

        # 随机 IV（每次加密都不同，与企微不同）
        iv = os.urandom(16)

        # AES-256-CBC 加密
        cipher = Cipher(algorithms.AES(self.aes_key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        encrypted = encryptor.update(plaintext) + encryptor.finalize()

        # IV 前缀 + 密文 → Base64
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

        Args:
            timestamp: X-Lark-Request-Timestamp
            nonce: X-Lark-Request-Nonce
            body: 原始请求体字符串
            signature: X-Lark-Signature

        Returns:
            签名是否有效
        """
        content = timestamp + nonce + self.encrypt_key + body
        calculated = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return calculated == signature
