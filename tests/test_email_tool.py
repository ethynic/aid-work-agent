"""
邮件工具测试脚本

测试 EmailProcessTool（action: send / read 三合一工具）的发送和接收功能
"""

import asyncio
import os
from dotenv import load_dotenv

from src.models.user import UserEmail, EncryptionType
from src.tools.email import EmailProcessTool, create_email_tools


# 加载环境变量
load_dotenv()


def get_test_email_config() -> UserEmail:
    """
    获取测试邮箱配置

    从环境变量读取，或使用默认测试配置
    """
    # 解析加密协议，默认使用SSL
    smtp_encryption = os.getenv("TEST_SMTP_ENCRYPTION", "ssl").lower()
    imap_encryption = os.getenv("TEST_IMAP_ENCRYPTION", "ssl").lower()

    return UserEmail(
        email_address=os.getenv("TEST_EMAIL_ADDRESS", "luwei@tulin.cn"),
        smtp_server=os.getenv("TEST_SMTP_SERVER", "smtp.ym.163.com"),
        smtp_port=int(os.getenv("TEST_SMTP_PORT", "994")),
        smtp_user=os.getenv("TEST_SMTP_USER", "luwei@tulin.cn"),
        smtp_password=os.getenv("TEST_SMTP_PASSWORD", "p@$$w0rd"),
        smtp_encryption=EncryptionType(smtp_encryption),
        imap_server=os.getenv("TEST_IMAP_SERVER", "imap.ym.163.com"),
        imap_port=int(os.getenv("TEST_IMAP_PORT", "993")),
        imap_encryption=EncryptionType(imap_encryption),
    )


async def test_email_send():
    """测试发送邮件（action=send）"""
    print("\n" + "=" * 50)
    print("测试邮件发送")
    print("=" * 50)

    user_email = get_test_email_config()
    tool = EmailProcessTool(user_email)

    # 测试1: 发送简单邮件
    print("\n[测试1] 发送简单邮件...")
    result = await tool.execute(
        action="send",
        to=os.getenv("TEST_EMAIL_TO", "luwei@aidingyi.cn"),
        subject="测试邮件 - 简单邮件",
        body="这是一封测试邮件，用于验证邮件发送功能。\n\n来自 EmailProcessTool 测试。",
    )
    print(f"结果: {result}")

    # 测试2: 发送带抄送的邮件
    print("\n[测试2] 发送带抄送的邮件...")
    result = await tool.execute(
        action="send",
        to=os.getenv("TEST_EMAIL_TO", "luwei@aidingyi.cn"),
        subject="测试邮件 - 带抄送",
        body="这封邮件包含抄送人。",
        cc=os.getenv("TEST_EMAIL_CC", "lwei2004@126.com"),
    )
    print(f"结果: {result}")

    # 测试3: 发送给多个收件人（列表形式）
    print("\n[测试3] 发送给多个收件人...")
    to_address = os.getenv("TEST_EMAIL_TO", "luwei@aidingyi.cn")
    result = await tool.execute(
        action="send",
        to=[to_address, "lwei2004@126.com"],
        subject="测试邮件 - 多收件人",
        body="这封邮件发送给多个收件人。",
    )
    print(f"结果: {result}")

    # 测试4: 缺少必填参数
    print("\n[测试4] 缺少必填参数...")
    result = await tool.execute(
        action="send",
        to="",
        subject="",
        body="测试",
    )
    print(f"结果: {result}")
    assert result["success"] == False, "应该返回失败"


async def test_email_read():
    """测试收取邮件（action=read）"""
    print("\n" + "=" * 50)
    print("测试邮件收取")
    print("=" * 50)

    user_email = get_test_email_config()
    tool = EmailProcessTool(user_email)

    # 测试1: 收取最新100封邮件（返回含 folders 字段）
    print("\n[测试1] 收取最新100封邮件...")
    result = await tool.execute(action="read", limit=100)
    print(f"成功: {result.get('success')}")
    print(f"邮件数量: {result.get('count', 0)}")
    if result.get("folders"):
        print(f"文件夹列表: {[f['name'] for f in result['folders']]}")
    if result.get("emails"):
        for i, mail in enumerate(result["emails"][:3], 1):
            print(f"  邮件{i}: {mail['subject'][:50]}... (from: {mail['from']})")

    # 测试2: 只收取未读邮件
    print("\n[测试2] 收取未读邮件...")
    result = await tool.execute(action="read", limit=20, unseen_only=True)
    print(f"成功: {result.get('success')}, 数量: {result.get('count', 0)}")

    # 测试3: 按发件人过滤（两段式：先拉头过滤，命中才拉全信）
    print("\n[测试3] 按发件人过滤...")
    result = await tool.execute(
        action="read",
        limit=5,
        from_filter="noreply",
    )
    print(f"成功: {result.get('success')}, 数量: {result.get('count', 0)}")

    # 测试4: 按主题过滤
    print("\n[测试4] 按主题过滤...")
    result = await tool.execute(
        action="read",
        limit=50,
        subject_filter="通知",
    )
    print(f"成功: {result.get('success')}, 数量: {result.get('count', 0)}")


async def test_create_email_tools():
    """测试创建邮件工具工厂函数（三合一后仅一个工具）"""
    print("\n" + "=" * 50)
    print("测试 create_email_tools 工厂函数")
    print("=" * 50)

    user_email = get_test_email_config()
    tools = create_email_tools(user_email)

    print(f"创建的工具数量: {len(tools)}")
    print(f"工具名称: {[t.name for t in tools]}")

    assert len(tools) == 1, "三合一后应该只创建1个工具"
    assert tools[0].name == "email_process", "应该是 email_process 工具"
    print("测试通过!")


async def test_user_email_model():
    """测试 UserEmail 模型"""
    print("\n" + "=" * 50)
    print("测试 UserEmail 模型")
    print("=" * 50)

    user_email = UserEmail(
        email_address="test@example.com",
        smtp_server="smtp.example.com",
        smtp_port=465,
        smtp_user="test@example.com",
        smtp_password="password123",
        smtp_encryption=EncryptionType.SSL,
        imap_server="imap.example.com",
        imap_port=993,
        imap_encryption=EncryptionType.SSL,
    )

    print(f"邮箱地址: {user_email.email_address}")
    print(f"SMTP服务器: {user_email.smtp_server}:{user_email.smtp_port} ({user_email.smtp_encryption})")
    print(f"IMAP服务器: {user_email.imap_server}:{user_email.imap_port} ({user_email.imap_encryption})")

    # 测试获取IMAP认证信息
    creds = user_email.get_imap_credentials()
    print(f"IMAP认证: user={creds[0]}, password=***")

    # 测试不同加密协议
    print("\n测试不同加密协议:")
    for enc in [EncryptionType.SSL, EncryptionType.TLS, EncryptionType.NONE]:
        test_email = UserEmail(
            email_address="test@example.com",
            smtp_server="smtp.example.com",
            smtp_port=465 if enc == EncryptionType.SSL else 587,
            smtp_user="test@example.com",
            smtp_password="password",
            smtp_encryption=enc,
            imap_server="imap.example.com",
            imap_port=993,
            imap_encryption=enc,
        )
        print(f"  {enc.value}: SMTP端口={test_email.smtp_port}")

    print("模型测试通过!")


async def main():
    """主测试函数"""
    print("\n" + "=" * 60)
    print("邮件工具测试")
    print("=" * 60)
    print("\n注意: 请确保设置了正确的邮箱配置环境变量:")
    print("  - TEST_EMAIL_ADDRESS: 发件人邮箱地址")
    print("  - TEST_SMTP_SERVER: SMTP服务器地址")
    print("  - TEST_SMTP_PORT: SMTP端口(默认994)")
    print("  - TEST_SMTP_USER: SMTP登录用户名")
    print("  - TEST_SMTP_PASSWORD: SMTP密码/授权码")
    print("  - TEST_SMTP_ENCRYPTION: SMTP加密协议(ssl/tls/none, 默认ssl)")
    print("  - TEST_IMAP_SERVER: IMAP服务器地址")
    print("  - TEST_IMAP_PORT: IMAP端口(默认993)")
    print("  - TEST_IMAP_ENCRYPTION: IMAP加密协议(ssl/tls/none, 默认ssl)")
    print("  - TEST_EMAIL_TO: 测试收件人地址")
    print("  - TEST_EMAIL_CC: 测试抄送人地址(可选)")

    # 测试模型
    await test_user_email_model()

    # 测试工厂函数
    await test_create_email_tools()

    # 测试发送邮件（需要真实配置）
    # try:
    #     await test_email_send()
    # except Exception as e:
    #     print(f"\n发送测试失败（可能是配置问题）: {e}")

    # 测试收取邮件（需要真实配置，文件夹列表随 read 返回）
    try:
        await test_email_read()
    except Exception as e:
        print(f"\n收取测试失败（可能是配置问题）: {e}")

    print("\n" + "=" * 60)
    print("测试完成!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
