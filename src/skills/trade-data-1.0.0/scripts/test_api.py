#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
外贸数据API测试脚本

测试API配置和连接是否正常。
"""

import sys
from pathlib import Path

# Add scripts dir to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from trade_query import get_api_key, TendataAPI


def test_configuration():
    """测试API配置"""
    print("=" * 60)
    print("测试API配置")
    print("=" * 60)
    
    try:
        api_key = get_api_key()
        print(f"✓ API密钥已配置: {api_key[:10]}...{api_key[-10:]}")
        return True
    except Exception as e:
        print(f"✗ API密钥未配置")
        print(f"  错误: {e}")
        return False


def test_access_token():
    """测试获取访问令牌"""
    print("\n" + "=" * 60)
    print("测试获取访问令牌")
    print("=" * 60)
    
    try:
        api_key = get_api_key()
        client = TendataAPI(api_key)
        
        print("正在请求访问令牌...")
        token = client.get_access_token()
        
        print(f"✓ 成功获取访问令牌")
        print(f"  Token: {token[:20]}...{token[-20:]}")
        return True
    except Exception as e:
        print(f"✗ 获取访问令牌失败")
        print(f"  错误: {e}")
        return False


def test_query():
    """测试查询贸易记录"""
    print("\n" + "=" * 60)
    print("测试查询贸易记录")
    print("=" * 60)
    
    try:
        api_key = get_api_key()
        client = TendataAPI(api_key)
        
        print("正在查询贸易记录...")
        result = client.query_trade_records(
            catalog="imports",
            start_date="2024-01-01",
            end_date="2024-01-31",
            page_no=1,
            page_size=5
        )
        
        print(f"✓ 查询成功")
        print(f"  总记录数: {result.get('total', 0)}")
        print(f"  当前页码: {result.get('pageNo', 1)}")
        print(f"  每页条数: {result.get('pageSize', 5)}")
        
        records = result.get('records', [])
        if records:
            print(f"  返回记录数: {len(records)}")
            print(f"\n  示例记录:")
            first_record = records[0]
            for key, value in list(first_record.items())[:5]:
                print(f"    {key}: {value}")
        else:
            print(f"  返回记录数: 0")
        
        return True
    except Exception as e:
        print(f"✗ 查询失败")
        print(f"  错误: {e}")
        return False


def main():
    """主测试函数"""
    print("\n外贸数据API测试")
    print("=" * 60)
    
    results = []
    
    # 测试1: 配置
    results.append(("配置检查", test_configuration()))
    
    # 测试2: 访问令牌
    if results[0][1]:  # 只有配置成功才测试令牌
        results.append(("访问令牌", test_access_token()))
    else:
        results.append(("访问令牌", False))
    
    # 测试3: 查询
    if results[1][1]:  # 只有令牌成功才测试查询
        results.append(("查询记录", test_query()))
    else:
        results.append(("查询记录", False))
    
    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    
    for name, success in results:
        status = "✓ 通过" if success else "✗ 失败"
        print(f"{name}: {status}")
    
    all_passed = all(r[1] for r in results)
    
    print("\n" + "=" * 60)
    if all_passed:
        print("✓ 所有测试通过！API配置正确，可以正常使用。")
    else:
        print("✗ 部分测试失败，请检查配置和网络连接。")
    print("=" * 60)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
