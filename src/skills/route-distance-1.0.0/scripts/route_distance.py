#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导航距离计算脚本

基于高德地图 API 计算两个地点之间的驾车导航距离和预计时间。
接收 JSON 参数（stdin），输出 JSON 结果（stdout）。
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from dotenv import load_dotenv
load_dotenv()

import httpx
from loguru import logger

# ============================================================
# 缓存
# ============================================================

_route_cache: Dict[str, Tuple[float, dict]] = {}
_CACHE_TTL = 86400  # 24 小时
_CACHE_MAX = 1000


def _cache_key(origin: str, destination: str) -> str:
    return f"{origin.strip()}:{destination.strip()}"


def _get_cached(origin: str, destination: str) -> Optional[dict]:
    key = _cache_key(origin, destination)
    if key in _route_cache:
        ts, data = _route_cache[key]
        if time.time() - ts < _CACHE_TTL:
            return data
        del _route_cache[key]
    return None


def _set_cached(origin: str, destination: str, data: dict):
    if len(_route_cache) >= _CACHE_MAX:
        oldest = min(_route_cache, key=lambda k: _route_cache[k][0])
        del _route_cache[oldest]
    _route_cache[_cache_key(origin, destination)] = (time.time(), data)


# ============================================================
# 核心计算
# ============================================================

BASE_URL = "https://restapi.amap.com/v3"


def geocode(address: str, api_key: str, region: str = "") -> Tuple[float, float, str]:
    """地址 → 经纬度坐标。region 为省份前缀，提高模糊地址准确度。返回 (lng, lat, formatted_address)。"""
    full_address = f"{region}{address}" if region else address
    resp = httpx.get(
        f"{BASE_URL}/geocode/geo",
        params={"key": api_key, "address": full_address},
        timeout=10,
    )
    data = resp.json()
    if data.get("status") == "1" and data.get("geocodes"):
        g = data["geocodes"][0]
        location = g["location"]
        lng, lat = location.split(",")
        formatted = g.get("formatted_address", "")
        logger.info(f"Geocode: '{full_address}' => {formatted} ({lng},{lat})")
        return float(lng), float(lat), formatted
    raise ValueError(f"地址解析失败: {address}")


def driving_distance(
    origin_coords: Tuple[float, float],
    dest_coords: Tuple[float, float],
    api_key: str,
) -> dict:
    """计算驾车导航距离"""
    origin_str = f"{origin_coords[0]},{origin_coords[1]}"
    dest_str = f"{dest_coords[0]},{dest_coords[1]}"

    resp = httpx.get(
        f"{BASE_URL}/direction/driving",
        params={
            "key": api_key,
            "origin": origin_str,
            "destination": dest_str,
            "strategy": 2,
            "extensions": "base",
        },
        timeout=10,
    )
    data = resp.json()
    if data.get("status") == "1" and data.get("route", {}).get("paths"):
        path = data["route"]["paths"][0]
        return {
            "distance_m": int(path["distance"]),
            "distance_km": round(int(path["distance"]) / 1000, 1),
            "duration_s": int(path["duration"]),
            "duration_hours": round(int(path["duration"]) / 3600, 1),
            "tolls": float(path.get("tolls", 0)),
        }
    raise ValueError("无法规划路线")


def calculate(origin: str, destination: str, api_key: str, region: str = "") -> dict:
    """完整计算流程：地址 → 地理编码 → 驾车距离（带缓存）"""
    # 检查缓存
    cached = _get_cached(origin, destination)
    if cached:
        logger.debug(f"Cache hit for {origin} → {destination}")
        return cached

    # Step 1: 地理编码
    origin_lng, origin_lat, origin_formatted = geocode(origin, api_key, region)
    dest_lng, dest_lat, dest_formatted = geocode(destination, api_key, region)
    origin_coords = (origin_lng, origin_lat)
    dest_coords = (dest_lng, dest_lat)

    # Step 2: 驾车路径规划
    result = driving_distance(origin_coords, dest_coords, api_key)
    result.update({
        "origin": origin,
        "destination": destination,
        "origin_formatted": origin_formatted,
        "destination_formatted": dest_formatted,
        "origin_coords": f"{origin_lng},{origin_lat}",
        "destination_coords": f"{dest_lng},{dest_lat}",
    })

    # 写入缓存
    _set_cached(origin, destination, result)
    return result


# ============================================================
# CLI 入口
# ============================================================

def main():
    api_key = os.environ.get("AMAP_API_KEY", "")
    if not api_key:
        print(json.dumps({"success": False, "error": "未配置高德地图 API Key"}, ensure_ascii=False))
        sys.exit(0)

    # 从 stdin 读取 JSON
    input_data = {}
    try:
        raw = sys.stdin.read()
        if raw.strip():
            input_data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"success": False, "error": f"参数格式错误: {e}"}, ensure_ascii=False))
        sys.exit(0)

    origin = input_data.get("origin", "").strip()
    destination = input_data.get("destination", "").strip()
    region = input_data.get("region", "").strip()

    if not origin or not destination:
        print(json.dumps({
            "success": False,
            "error": "缺少必填参数: origin 和 destination",
        }, ensure_ascii=False))
        sys.exit(0)

    try:
        result = calculate(origin, destination, api_key, region)
        result["success"] = True
        print(json.dumps(result, ensure_ascii=False))
    except ValueError as e:
        print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
    except httpx.TimeoutException:
        print(json.dumps({"success": False, "error": "API 请求超时，请稍后重试"}, ensure_ascii=False))
    except Exception as e:
        logger.error(f"route_distance error: {e}", exc_info=True)
        print(json.dumps({"success": False, "error": f"网络异常，无法计算导航距离"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
