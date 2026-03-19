#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
外贸数据查询工具

通过Tendata API查询进出口贸易记录。

使用方法:
    python scripts/trade_query.py \
        --catalog imports \
        --start-date "2023-01-01" \
        --end-date "2023-12-31" \
        --hs-code "63049239" \
        --page-size 20
"""

import argparse
import io
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Any

import requests
from requests.exceptions import RequestException

# Fix Windows console encoding
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, 'buffer'):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, 'buffer'):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except:
        pass


class TendataAPI:
    """Tendata外贸数据API客户端"""
    
    def __init__(self, api_key: str):
        """
        初始化API客户端
        
        Args:
            api_key: Tendata API密钥
        """
        self.api_key = api_key
        self.base_url = "https://open-api.tendata.cn/v2"
        self.access_token = None
        self.token_expires_at = None
        
    def get_access_token(self) -> str:
        """
        获取访问令牌
        
        Returns:
            访问令牌字符串
            
        Raises:
            Exception: 获取令牌失败时抛出异常
        """
        # 如果token还有效，直接返回
        if self.access_token and self.token_expires_at:
            if time.time() < self.token_expires_at:
                return self.access_token
        
        # 请求新的access token
        url = f"{self.base_url}/access-token"
        params = {"apiKey": self.api_key}
        
        try:
            response = requests.get(
                url,
                params=params,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "*/*"
                },
                timeout=30
            )
            
            if response.status_code != 200:
                raise Exception(f"获取access token失败: HTTP {response.status_code}")
            
            data = response.json()
            
            if data.get("code") != 200:
                raise Exception(f"API错误: {data.get('message', '未知错误')}")
            
            self.access_token = data.get("data", {}).get("accessToken")
            if not self.access_token:
                raise Exception("响应中没有找到accessToken")
            
            # 设置token过期时间（提前5分钟刷新）
            expires_in = data.get("data", {}).get("expiresIn", 7200)
            self.token_expires_at = time.time() + expires_in - 300
            
            return self.access_token
            
        except RequestException as e:
            raise Exception(f"网络请求失败: {str(e)}")
    
    def query_trade_records(
        self,
        catalog: str,
        start_date: str,
        end_date: str,
        page_no: int = 1,
        page_size: int = 10,
        **kwargs
    ) -> Dict[str, Any]:
        """
        查询贸易记录
        
        Args:
            catalog: 数据源类型 (imports/exports)
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            page_no: 页码（从1开始）
            page_size: 每页条数
            **kwargs: 其他可选参数
            
        Returns:
            API响应数据
        """
        # 获取access token
        token = self.get_access_token()
        
        # 构建请求体
        request_body = {
            "pageNo": page_no,
            "pageSize": page_size,
            "catalog": catalog,
            "startDate": start_date,
            "endDate": end_date
        }
        
        # 添加可选参数
        optional_params = [
            "exporter", "importer", "productDesc", "hsCode",
            "countryOfOriginCode", "countryOfDestinationCode",
            "portOfDeparture", "portOfArrival", "transportType",
            "quantityUnit", "searchMode"
        ]
        
        for param in optional_params:
            if param in kwargs and kwargs[param] is not None:
                request_body[param] = kwargs[param]
        
        # 添加布尔和数组参数
        bool_params = ["containProducer", "optimizationModel"]
        for param in bool_params:
            if param in kwargs and kwargs[param] is not None:
                request_body[param] = kwargs[param]
        
        array_params = [
            "weight", "quantity", "sumOfUSD",
            "weightUnitPriceUSD", "quantityUnitPriceUSD",
            "filterBlankFields", "filterLogisticFields"
        ]
        for param in array_params:
            if param in kwargs and kwargs[param] is not None:
                request_body[param] = kwargs[param]
        
        # 发送请求
        url = f"{self.base_url}/trade"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
        
        try:
            response = requests.post(
                url,
                headers=headers,
                json=request_body,
                timeout=60
            )
            
            if response.status_code == 401:
                # Token可能过期，重新获取
                self.access_token = None
                token = self.get_access_token()
                headers["Authorization"] = f"Bearer {token}"
                response = requests.post(
                    url,
                    headers=headers,
                    json=request_body,
                    timeout=60
                )
            
            if response.status_code != 200:
                raise Exception(f"查询失败: HTTP {response.status_code}")
            
            data = response.json()
            
            if data.get("code") != 200:
                error_msg = data.get("message", "未知错误")
                error_detail = data.get("data", {})
                raise Exception(f"API错误: {error_msg} - 详情: {error_detail}")
            
            return data.get("data", {})
            
        except RequestException as e:
            raise Exception(f"网络请求失败: {str(e)}")


def load_env_file(skill_dir: Path) -> Dict[str, str]:
    """
    从.env文件加载环境变量
    
    Args:
        skill_dir: skill目录路径
        
    Returns:
        环境变量字典
    """
    env_vars = {}
    env_file = skill_dir / ".env"
    
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()
    
    return env_vars


def get_api_key() -> str:
    """
    获取API密钥
    
    优先级：
    1. 环境变量 TENDATA_API_KEY
    2. .env 文件
    
    Returns:
        API密钥
        
    Raises:
        Exception: 未配置API密钥时抛出异常
    """
    # 1. 检查环境变量
    api_key = os.environ.get("TENDATA_API_KEY")
    if api_key:
        return api_key
    
    # 2. 从.env文件加载
    skill_dir = Path(__file__).parent.parent
    env_vars = load_env_file(skill_dir)
    api_key = env_vars.get("TENDATA_API_KEY")
    
    if api_key:
        return api_key
    
    # 未配置
    raise Exception(
        "TENDATA_API_KEY not configured. Get your API key at: https://open-api.tendata.cn\n"
        "Please set the TENDATA_API_KEY environment variable or create a .env file in the skill directory."
    )


def parse_array_arg(value: str) -> list:
    """
    解析数组参数
    
    Args:
        value: JSON数组字符串
        
    Returns:
        列表对象
    """
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        # 如果不是JSON格式，尝试作为逗号分隔的字符串
        return [x.strip() for x in value.split(",")]


def main():
    parser = argparse.ArgumentParser(
        description="外贸数据查询工具 - 查询进出口贸易记录",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 查询特定HS编码的进口记录
  python scripts/trade_query.py --catalog imports --start-date "2023-01-01" --end-date "2023-12-31" --hs-code "63049239"
  
  # 查询特定企业的进口记录
  python scripts/trade_query.py --catalog imports --start-date "2024-01-01" --end-date "2024-03-31" --importer "华为"
  
  # 查询特定产品的出口记录
  python scripts/trade_query.py --catalog exports --start-date "2024-01-01" --end-date "2024-06-30" --product-desc "电子产品;手机"
  
配置:
  设置环境变量: TENDATA_API_KEY
  或创建 .env 文件: TENDATA_API_KEY=your_api_key_here
        """
    )
    
    # 必需参数
    parser.add_argument(
        "--catalog",
        required=True,
        choices=["imports", "exports"],
        help="数据源类型: imports(进口) 或 exports(出口)"
    )
    parser.add_argument(
        "--start-date",
        required=True,
        help="开始日期 (格式: YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end-date",
        required=True,
        help="结束日期 (格式: YYYY-MM-DD)"
    )
    
    # 分页参数
    parser.add_argument("--page-no", type=int, default=1, help="页码（从1开始，默认1）")
    parser.add_argument("--page-size", type=int, default=10, help="每页条数（默认10）")
    
    # 企业名称参数
    parser.add_argument("--importer", help="进口商名称（不少于2个字符）")
    parser.add_argument("--exporter", help="出口商名称（不少于2个字符）")
    
    # 产品参数
    parser.add_argument("--hs-code", help="海关编码（4位以上）")
    parser.add_argument("--product-desc", help="产品描述（多关键词用分号分隔）")
    parser.add_argument("--contain-producer", action="store_true", help="包含生产企业")
    
    # 国家参数
    parser.add_argument("--country-of-origin", help="原产国代码（多个用分号分隔，如: CHN;USA）")
    parser.add_argument("--country-of-destination", help="目的国代码（多个用分号分隔，如: USA;CHN）")
    
    # 港口和运输
    parser.add_argument("--port-of-departure", help="起运港")
    parser.add_argument("--port-of-arrival", help="目的港")
    parser.add_argument("--transport-type", help="运输方式")
    
    # 数量和价格过滤
    parser.add_argument("--weight", help="重量区间（JSON数组，如: [100, 1000]）")
    parser.add_argument("--quantity", help="数量区间（JSON数组，如: [1, 100]）")
    parser.add_argument("--quantity-unit", help="数量单位")
    parser.add_argument("--sum-of-usd", help="美元总价区间（JSON数组，如: [1000, 10000]）")
    parser.add_argument("--weight-unit-price-usd", help="重量美元单价区间（JSON数组）")
    parser.add_argument("--quantity-unit-price-usd", help="数量美元单价区间（JSON数组）")
    
    # 过滤参数
    parser.add_argument("--filter-blank-fields", help="过滤空白公司（JSON数组，如: [\"importer\"]）")
    parser.add_argument("--filter-logistic-fields", help="过滤物流公司（JSON数组，如: [\"exporter\"]）")
    
    # 搜索模式
    parser.add_argument("--search-mode", help="搜索模式（FUZZY_SINGLE开启模糊搜索）")
    parser.add_argument("--optimization-model", action="store_true", help="启用优选模式")
    
    # 输出格式
    parser.add_argument("--pretty", action="store_true", help="美化输出JSON")
    
    args = parser.parse_args()
    
    try:
        # 获取API密钥
        api_key = get_api_key()
        
        # 初始化API客户端
        client = TendataAPI(api_key)
        
        # 构建查询参数
        query_params = {
            "page_no": args.page_no,
            "page_size": args.page_size
        }
        
        # 添加可选参数
        if args.importer:
            query_params["importer"] = args.importer
        if args.exporter:
            query_params["exporter"] = args.exporter
        if args.hs_code:
            query_params["hsCode"] = args.hs_code
        if args.product_desc:
            query_params["productDesc"] = args.product_desc
        if args.contain_producer:
            query_params["containProducer"] = True
        if args.country_of_origin:
            query_params["countryOfOriginCode"] = args.country_of_origin
        if args.country_of_destination:
            query_params["countryOfDestinationCode"] = args.country_of_destination
        if args.port_of_departure:
            query_params["portOfDeparture"] = args.port_of_departure
        if args.port_of_arrival:
            query_params["portOfArrival"] = args.port_of_arrival
        if args.transport_type:
            query_params["transportType"] = args.transport_type
        if args.quantity_unit:
            query_params["quantityUnit"] = args.quantity_unit
        if args.search_mode:
            query_params["searchMode"] = args.search_mode
        if args.optimization_model:
            query_params["optimizationModel"] = True
        
        # 解析数组参数
        if args.weight:
            query_params["weight"] = parse_array_arg(args.weight)
        if args.quantity:
            query_params["quantity"] = parse_array_arg(args.quantity)
        if args.sum_of_usd:
            query_params["sumOfUSD"] = parse_array_arg(args.sum_of_usd)
        if args.weight_unit_price_usd:
            query_params["weightUnitPriceUSD"] = parse_array_arg(args.weight_unit_price_usd)
        if args.quantity_unit_price_usd:
            query_params["quantityUnitPriceUSD"] = parse_array_arg(args.quantity_unit_price_usd)
        if args.filter_blank_fields:
            query_params["filterBlankFields"] = parse_array_arg(args.filter_blank_fields)
        if args.filter_logistic_fields:
            query_params["filterLogisticFields"] = parse_array_arg(args.filter_logistic_fields)
        
        # 执行查询
        result = client.query_trade_records(
            catalog=args.catalog,
            start_date=args.start_date,
            end_date=args.end_date,
            **query_params
        )
        
        # 输出结果
        output = {
            "success": True,
            "data": result,
            "query": {
                "catalog": args.catalog,
                "startDate": args.start_date,
                "endDate": args.end_date,
                "pageNo": args.page_no,
                "pageSize": args.page_size
            }
        }
        
        if args.pretty:
            print(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(output, ensure_ascii=False))
        
    except Exception as e:
        error_output = {
            "success": False,
            "error": str(e)
        }
        
        if args.pretty:
            print(json.dumps(error_output, ensure_ascii=False, indent=2), file=sys.stderr)
        else:
            print(json.dumps(error_output, ensure_ascii=False), file=sys.stderr)
        
        sys.exit(1)


if __name__ == "__main__":
    main()
