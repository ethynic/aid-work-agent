"""企业微信个人账号 RPA 渠道客户端密钥加解密（兼容壳）。

Fernet 原语已抽取至公共模块 ``src.core.secret_crypto``（设计文档
docs/system/wechat-mp/wechat-mp-knowledge-ingestion-design.md §4），
本模块保留旧导入路径，全部能力 re-export，密钥策略不再复制。

相关契约：docs/system/wecom-personal-rpa-protocol.md §A.1（client_secret 仅在服务端解密后参与签名）。
"""

from src.core.secret_crypto import (  # noqa: F401
    APP_SECRET_KEY_ENV,
    RPA_SECRET_KEY_ENV,
    _get_fernet,
    _load_master_key,
    decrypt_secret,
    encrypt_secret,
    looks_like_ciphertext,
    mask_value,
)

# 旧代码可能引用的私有名
_RPA_SECRET_KEY_ENV = RPA_SECRET_KEY_ENV
