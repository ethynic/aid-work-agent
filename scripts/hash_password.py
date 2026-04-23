"""
Password Hash 验证工具

用法：
    python -m scripts.hash_password <明文密码> <password_hash>

说明：
    <明文密码> 和 <password_hash> 中如有特殊符号（例如 $ ），应加引号包裹，以免截断。

验证输入的明文密码与数据库中存储的哈希值是否匹配。
"""

import sys
from src.db.models import hash_password, verify_password


def main():
    if len(sys.argv) < 3:
        print("用法: python -m scripts.hash_password <明文密码> <password_hash>")
        sys.exit(1)

    plaintext = sys.argv[1]
    password_hash = sys.argv[2]

    # 计算明文密码的哈希（仅供展示）
    computed_hash = hash_password(plaintext)

    # 验证密码是否匹配
    is_valid = verify_password(plaintext, password_hash)

    print(f"输入的明文密码: {plaintext}")
    print(f"待验证的哈希值: {password_hash}")
    print(f"明文重新哈希结果: {computed_hash}")
    print()
    print(f"验证结果: {'OK - 密码正确' if is_valid else 'FAIL - 密码不匹配'}")


if __name__ == "__main__":
    main()
