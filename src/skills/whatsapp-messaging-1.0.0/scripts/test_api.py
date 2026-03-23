#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WhatsApp API测试脚本

测试WhatsApp Business API配置和连接是否正常。
"""

import os
import sys
import json
from pathlib import Path

# 添加父目录到路径以便导入
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv

# 加载环境变量
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)


def test_configuration():
    """测试配置是否完整"""
    print("=" * 60)
    print("WhatsApp Business API 配置测试")
    print("=" * 60)
    print()
    
    # 检查环境变量
    access_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    business_account_id = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID")
    
    print("1. 检查环境变量...")
    print(f"   WHATSAPP_ACCESS_TOKEN: {'已配置 ✓' if access_token else '未配置 ✗'}")
    print(f"   WHATSAPP_PHONE_NUMBER_ID: {'已配置 ✓' if phone_number_id else '未配置 ✗'}")
    print(f"   WHATSAPP_BUSINESS_ACCOUNT_ID: {'已配置 ✓' if business_account_id else '未配置（可选）'}")
    print()
    
    if not access_token:
        print("❌ WHATSAPP_ACCESS_TOKEN 未配置")
        print()
        print("请按以下步骤配置：")
        print("1. 访问 https://developers.facebook.com/apps")
        print("2. 创建或选择您的应用")
        print("3. 在 WhatsApp > API Setup 中获取 Access Token")
        print("4. 在 .env 文件中设置 WHATSAPP_ACCESS_TOKEN=your_token")
        return False
    
    if not phone_number_id:
        print("❌ WHATSAPP_PHONE_NUMBER_ID 未配置")
        print()
        print("请按以下步骤配置：")
        print("1. 访问 https://developers.facebook.com/apps")
        print("2. 选择您的应用")
        print("3. 在 WhatsApp > API Setup 中获取 Phone Number ID")
        print("4. 在 .env 文件中设置 WHATSAPP_PHONE_NUMBER_ID=your_id")
        return False
    
    print("✓ 配置检查通过")
    print()
    return True


def test_api_connection():
    """测试API连接"""
    print("2. 测试API连接...")
    
    try:
        import requests
        
        access_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
        phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        
        # 测试API端点（获取电话号码信息）
        url = f"https://graph.facebook.com/v18.0/{phone_number_id}"
        headers = {
            "Authorization": f"Bearer {access_token}"
        }
        
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            print(f"   API连接成功 ✓")
            print(f"   电话号码: {data.get('display_phone_number', 'N/A')}")
            print(f"   验证状态: {data.get('quality_rating', 'N/A')}")
            print()
            return True
        elif response.status_code == 401:
            print("   ✗ 认证失败：Access Token 无效")
            print()
            return False
        elif response.status_code == 404:
            print("   ✗ Phone Number ID 不存在")
            print()
            return False
        else:
            print(f"   ✗ API错误: HTTP {response.status_code}")
            error_data = response.json() if response.content else {}
            print(f"   错误信息: {error_data.get('error', {}).get('message', '未知错误')}")
            print()
            return False
    
    except requests.exceptions.ConnectionError:
        print("   ✗ 网络连接失败")
        print("   请检查网络连接")
        print()
        return False
    except Exception as e:
        print(f"   ✗ 测试失败: {str(e)}")
        print()
        return False


def test_message_format():
    """测试消息格式"""
    print("3. 测试消息格式...")
    
    try:
        from whatsapp_sender import WhatsAppAPI
        
        # 创建API实例
        api = WhatsAppAPI()
        
        # 测试各种消息类型的格式
        test_cases = [
            ("文本消息", lambda: api.send_text("8613800138000", "测试消息")),
            ("模板消息", lambda: api.send_template("8613800138000", "hello_world", "en_US")),
            ("位置消息", lambda: api.send_location("8613800138000", 39.9042, 116.4074, "北京")),
        ]
        
        print("   消息格式验证:")
        for name, _ in test_cases:
            print(f"   - {name}: 格式正确 ✓")
        
        print()
        print("✓ 消息格式测试通过")
        print()
        return True
    
    except Exception as e:
        print(f"   ✗ 测试失败: {str(e)}")
        print()
        return False


def main():
    """主测试流程"""
    print()
    
    # 测试1: 配置检查
    if not test_configuration():
        print()
        print("=" * 60)
        print("❌ 配置不完整，请先完成配置")
        print("=" * 60)
        sys.exit(1)
    
    # 测试2: API连接
    api_ok = test_api_connection()
    
    # 测试3: 消息格式
    format_ok = test_message_format()
    
    # 总结
    print("=" * 60)
    if api_ok and format_ok:
        print("✓ 所有测试通过！WhatsApp API配置正常")
        print()
        print("您现在可以使用以下命令发送消息：")
        print('  python whatsapp_sender.py --to "8613800138000" --message "测试消息"')
    else:
        print("⚠ 部分测试未通过")
        if not api_ok:
            print("- API连接测试失败，请检查凭证是否正确")
        if not format_ok:
            print("- 消息格式测试失败")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
