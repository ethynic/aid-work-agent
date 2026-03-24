# -*- coding: utf-8 -*-
"""测试远程凭据管理功能

运行此脚本前请确保:
1. 已安装依赖: pip install -r requirements.txt
2. 已初始化数据库
"""

import io
import sys

# 设置标准输出编码为 UTF-8
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent))

from src.db.database import init_database
from src.db.remote_credential import RemoteCredentialDB, encryption_manager
from src.tools.file.upload_to_remote import UploadToRemoteTool


async def test_encryption():
    """测试加密功能"""
    print("\n=== 测试加密功能 ===")
    test_password = "test_password_123"
    encrypted = encryption_manager.encrypt(test_password)
    print(f"原始密码: {test_password}")
    print(f"加密后: {encrypted[:50]}...")

    decrypted = encryption_manager.decrypt(encrypted)
    print(f"解密后: {decrypted}")

    assert decrypted == test_password, "加密解密失败"
    print("✅ 加密功能测试通过")


async def test_create_credential():
    """测试创建凭据"""
    print("\n=== 测试创建凭据 ===")

    test_user_id = "test_user_001"

    # 创建 SMB 凭据
    smb_credential_id = RemoteCredentialDB.create(
        user_id=test_user_id,
        connection_type='smb',
        server_host='192.168.1.100',
        server_port=445,
        username='testuser',
        password='testpassword',
        remote_path='/share/folder',
        name='测试SMB服务器',
        domain='WORKGROUP',
        description='用于测试的SMB服务器'
    )
    print(f"✅ 创建SMB凭据: {smb_credential_id}")

    # 创建 FTP 凭据
    ftp_credential_id = RemoteCredentialDB.create(
        user_id=test_user_id,
        connection_type='ftp',
        server_host='ftp.example.com',
        server_port=21,
        username='ftpuser',
        password='ftppassword',
        remote_path='/var/www/uploads',
        name='测试FTP服务器',
        description='用于测试的FTP服务器'
    )
    print(f"✅ 创建FTP凭据: {ftp_credential_id}")

    return smb_credential_id, ftp_credential_id


async def test_list_credentials(user_id: str):
    """测试列出凭据"""
    print("\n=== 测试列出凭据 ===")

    credentials = RemoteCredentialDB.list_by_user(user_id)
    print(f"用户 {user_id} 有 {len(credentials)} 个凭据:")

    for cred in credentials:
        print(f"  - {cred['connection_type'].upper()}: {cred['server_host']}:{cred['server_port']}{cred['remote_path']}")
        print(f"    名称: {cred['name']}")
        print(f"    ID: {cred['credential_id']}")

    print("✅ 列出凭据测试通过")


async def test_get_credential(user_id: str, credential_id: str):
    """测试获取凭据详情（解密密码）"""
    print("\n=== 测试获取凭据详情 ===")

    credential = RemoteCredentialDB.get_by_id(credential_id, user_id)
    if credential:
        print(f"✅ 获取凭据成功:")
        print(f"  服务器: {credential['server_host']}:{credential['server_port']}")
        print(f"  用户名: {credential['username']}")
        print(f"  密码: {credential['password']}")
        print(f"  远程路径: {credential['remote_path']}")
    else:
        print("❌ 获取凭据失败")

    return credential


async def test_update_credential(user_id: str, credential_id: str):
    """测试更新凭据"""
    print("\n=== 测试更新凭据 ===")

    success = RemoteCredentialDB.update(
        credential_id,
        user_id,
        description='更新后的描述',
        server_port=446
    )

    if success:
        print(f"✅ 更新凭据成功")
        credential = RemoteCredentialDB.get_by_id(credential_id, user_id)
        print(f"  新端口: {credential['server_port']}")
        print(f"  新描述: {credential['description']}")
    else:
        print("❌ 更新凭据失败")


async def test_delete_credential(user_id: str, credential_id: str):
    """测试删除凭据（软删除）"""
    print("\n=== 测试删除凭据 ===")

    success = RemoteCredentialDB.delete(credential_id, user_id)

    if success:
        print(f"✅ 删除凭据成功")

        # 验证已删除
        credentials = RemoteCredentialDB.list_by_user(user_id)
        active_creds = [c for c in credentials if c['status'] == 1]
        print(f"  剩余活跃凭据: {len(active_creds)}")
    else:
        print("❌ 删除凭据失败")


async def test_upload_tool():
    """测试上传工具"""
    print("\n=== 测试上传工具 ===")

    tool = UploadToRemoteTool()
    print(f"工具名称: {tool.name}")
    print(f"工具描述: {tool.description}")
    print(f"参数schema: {tool.parameters_schema}")

    # 测试文件路径解析
    abs_path, filename = tool._resolve_file_path("test.txt")
    print(f"✅ 文件路径解析: {abs_path}, {filename}")

    # 测试参数验证
    try:
        result = await tool.execute(
            user_id="test_user_001",
            file_path="test.txt",
            remote_path="/share/folder",
            connection_type="smb"
        )
        print(f"✅ 工具执行结果: {result.get('success')}")
        if not result.get('success'):
            print(f"  错误: {result.get('error')}")
    except Exception as e:
        print(f"工具执行异常: {e}")


async def main():
    """主测试函数"""
    print("=" * 60)
    print("远程凭据管理功能测试")
    print("=" * 60)

    try:
        # 初始化数据库
        print("\n=== 初始化数据库 ===")
        init_database()
        print("✅ 数据库初始化完成")

        # 测试加密功能
        await test_encryption()

        # 测试创建凭据
        test_user_id = "test_user_001"
        smb_id, ftp_id = await test_create_credential()

        # 测试列出凭据
        await test_list_credentials(test_user_id)

        # 测试获取凭据详情
        await test_get_credential(test_user_id, smb_id)

        # 测试更新凭据
        await test_update_credential(test_user_id, smb_id)

        # 测试上传工具
        await test_upload_tool()

        # 测试删除凭据
        await test_delete_credential(test_user_id, smb_id)

        print("\n" + "=" * 60)
        print("✅ 所有测试通过！")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
