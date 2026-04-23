"""
QBTOKEN 哈希计算工具

用法：
    python -m scripts.hash_qbtoken <明文密码>

将输出的哈希值设置到 .env 或 config.yaml 的 qb_token_hash 字段中。
"""

import sys
from src.db.models import hash_password


def main():
    if len(sys.argv) < 2:
        print("用法: python -m scripts.hash_qbtoken <明文密码>")
        sys.exit(1)

    plaintext = sys.argv[1]
    hashed = hash_password(plaintext)
    print(f"QBTOKEN 明文: {plaintext}")
    print(f"QBTOKEN 哈希: {hashed}")
    print()
    print("请将哈希值设置到 .env 中：")
    print(f"QBTOKEN_HASH={hashed}")
    print()
    print("或设置到 config.yaml 中：")
    print(f"qb_token_hash: \"{hashed}\"")
    print()
    print("设置 qb_token_hash 后，可删除明文 QBTOKEN 配置以提高安全性。")


if __name__ == "__main__":
    main()
