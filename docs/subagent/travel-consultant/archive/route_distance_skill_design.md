# 导航距离计算 Skill 设计文档

> 版本: v1.0 | 创建: 2026-05-12 | 状态: 待审核

## 1. 概述

### 1.1 功能定位

导航距离计算 Skill（`route-distance`）封装高德地图 Web 服务 API，提供两个地点之间的**驾车导航距离**和**预计行驶时间**。

**核心能力**：
- 输入两个地址（支持模糊地址，如"黄果树瀑布"、"贵阳北站"）
- 返回导航距离（公里）和预计时间（小时）
- **导航距离 ≠ 直线距离**，基于实际道路计算

### 1.2 为什么是 Skill 而不是 Tool

| 维度 | Skill | Tool |
|------|-------|------|
| 调用方式 | `use_skill("route-distance", args)` | Agent 直接执行 |
| 适用场景 | 需要多步骤编排（地理编码 → 路径规划 → 结果解析） | 单一原子操作 |
| 扩展性 | 可在 SKILL.md 中扩展指令，支持多点路线、途经点等 | 需改代码 |
| 复用性 | 多个子智能体可共享（旅游顾问、物流等） | 同 |
| 可观测性 | 技能执行有完整上下文和进度回调 | 静默执行 |

导航距离计算涉及两步 API 调用（地理编码 + 路径规划）和结果解析，适合作为 Skill 管理。

### 1.3 使用场景

| 场景 | 说明 |
|------|------|
| 旅游报价 - 按公里计费 | 车辆定价模式为 `per_km` 时，计算行程导航距离用于计价 |
| 行程规划 | 计算景点之间的距离和车程时间 |
| 接送机服务 | 计算机场到酒店的接送距离 |
| 城际专线 | 计算城市间包车距离 |

---

## 2. 高德地图 API 集成

### 2.1 API 概览

本 Skill 封装两个高德 Web 服务 API：

| API | 用途 | URL | 免费额度 |
|-----|------|-----|---------|
| 地理编码 | 地址 → 经纬度 | `https://restapi.amap.com/v3/geocode/geo` | 5,000 次/天 |
| 驾车路径规划 | 经纬度 → 导航距离+时间 | `https://restapi.amap.com/v3/direction/driving` | 5,000 次/天 |

**选择驾车路径规划而非距离测量 API 的原因**：
- 距离测量 API（`/v3/distance`）只返回距离数值
- 驾车路径规划返回完整信息：距离、时间、途经城市、收费信息
- 更灵活，后续可扩展途经点规划

### 2.2 调用流程

```
输入：origin="贵阳市"  destination="黄果树瀑布"
  │
  ├─ Step 1: 地理编码（地址 → 坐标）
  │   GET https://restapi.amap.com/v3/geocode/geo?address=贵阳市&key=xxx
  │   Response: { geocodes: [{ location: "106.630153,26.647661" }] }
  │
  │   GET https://restapi.amap.com/v3/geocode/geo?address=黄果树瀑布&key=xxx
  │   Response: { geocodes: [{ location: "105.666282,25.993623" }] }
  │
  ├─ Step 2: 驾车路径规划
  │   GET https://restapi.amap.com/v3/direction/driving
  │     ?origin=106.630153,26.647661
  │     &destination=105.666282,25.993623
  │     &strategy=2         ← 常规最快策略
  │     &extensions=base    ← 只需基本信息
  │     &key=xxx
  │   Response: {
  │     route: {
  │       paths: [{
  │         distance: "128500",   ← 128.5 公里
  │         duration: "7200",     ← 2 小时
  │         tolls: "65"           ← 过路费 65 元
  │       }]
  │     }
  │   }
  │
  └─ 返回: { distance_km: 128.5, duration_hours: 2.0, tolls: 65 }
```

### 2.3 地理编码 API 详解

**请求参数**：

| 参数 | 说明 | 必填 |
|------|------|------|
| key | 高德 Web 服务 API Key | 是 |
| address | 结构化地址或地名（如"黄果树瀑布"、"贵阳北站"） | 是 |
| output | 返回格式，固定 JSON | 否 |

**响应关键字段**：

```json
{
  "status": "1",           // 1=成功
  "geocodes": [{
    "formatted_address": "贵州省安顺市镇宁布依族苗族自治县黄果树瀑布",
    "location": "105.666282,25.993623",  // 经度,纬度
    "level": "景点"
  }]
}
```

**特点**：
- 支持模糊地址：输入"黄果树瀑布"能自动匹配到准确坐标
- 支持景点名称、建筑名称、详细地址
- 一次请求只返回一个最佳匹配结果

### 2.4 驾车路径规划 API 详解

**请求参数**：

| 参数 | 说明 | 必填 |
|------|------|------|
| key | 高德 Web 服务 API Key | 是 |
| origin | 起点坐标 `经度,纬度` | 是 |
| destination | 终点坐标 `经度,纬度` | 是 |
| strategy | 驾车策略（见下表） | 否（默认 0） |
| extensions | `base` 基本信息 / `all` 全部信息 | 否（默认 base） |
| waypoints | 途经点，坐标用 `;` 分隔，最多 16 个 | 否 |

**strategy 策略选择**：

| 值 | 说明 | 适用场景 |
|----|------|---------|
| 0 | 速度优先 | 默认 |
| 2 | 常规最快（综合距离/耗时） | **推荐：旅游报价场景** |
| 10 | 躲避拥堵+路程较短 | 高峰期 |
| 22 | 货车策略 | 货运场景 |

**响应关键字段**：

```json
{
  "status": "1",
  "route": {
    "paths": [{
      "distance": "128500",   // 总距离，单位：米
      "duration": "7200",     // 预计时间，单位：秒
      "tolls": "65",          // 道路收费，单位：元
      "toll_distance": "85000" // 收费路段距离，单位：米
    }]
  }
}
```

---

## 3. Skill 设计

### 3.1 目录结构

```
src/skills/route-distance-1.0.0/
  SKILL.md                    ← Skill 定义文件
  scripts/
    route_distance.py         ← 导航距离计算核心脚本
```

### 3.2 SKILL.md 内容设计

```yaml
---
name: 导航距离计算
description: 计算两个地点之间的驾车导航距离和预计时间（基于高德地图）
version: 1.0.0
author: system
capabilities:
  - 地理编码（地址转坐标）
  - 驾车路径规划（导航距离+时间）
  - 支持模糊地址输入
triggers:
  keywords:
    - 距离
    - 导航距离
    - 驾车距离
    - 公里数
    - 车程
context:
  max_input_tokens: 2000
  max_output_tokens: 1000
---

# 导航距离计算

你是一个导航距离计算助手。当用户需要计算两个地点之间的驾车导航距离时，使用以下工具。

## 使用方式

当子智能体需要计算导航距离时，调用 `route_distance` 工具：

### 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| origin | string | 是 | 出发地地址 |
| destination | string | 是 | 目的地地址 |

### 返回值

```json
{
  "success": true,
  "origin": "贵阳市",
  "destination": "黄果树瀑布",
  "distance_km": 128.5,
  "duration_hours": 2.0,
  "tolls": 65,
  "origin_coords": "106.630153,26.647661",
  "destination_coords": "105.666282,25.993623"
}
```

### 使用示例

- 计算"贵阳→黄果树瀑布"的导航距离
- 计算贵阳龙洞堡机场到市区的距离
- 计算景区之间的车程时间

### 错误处理

- 地址无法解析时返回 `{"success": false, "error": "地址解析失败: ..."}`
- 无可达路线时返回 `{"success": false, "error": "无法规划路线: ..."}`
- API Key 未配置时返回 `{"success": false, "error": "未配置高德地图 API Key"}`
```

### 3.3 核心脚本设计

**文件**: `src/skills/route-distance-1.0.0/scripts/route_distance.py`

```python
import httpx
from loguru import logger


class RouteDistanceCalculator:
    """基于高德地图 API 的导航距离计算器"""

    BASE_URL = "https://restapi.amap.com/v3"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def geocode(self, address: str) -> tuple[float, float]:
        """地址 → 经纬度坐标"""
        resp = httpx.get(
            f"{self.BASE_URL}/geocode/geo",
            params={"key": self.api_key, "address": address},
            timeout=10,
        )
        data = resp.json()
        if data.get("status") == "1" and data.get("geocodes"):
            location = data["geocodes"][0]["location"]
            lng, lat = location.split(",")
            return float(lng), float(lat)
        raise ValueError(f"地址解析失败: {address}")

    def driving_distance(
        self, origin_coords: tuple, dest_coords: tuple
    ) -> dict:
        """计算驾车导航距离"""
        origin_str = f"{origin_coords[0]},{origin_coords[1]}"
        dest_str = f"{dest_coords[0]},{dest_coords[1]}"

        resp = httpx.get(
            f"{self.BASE_URL}/direction/driving",
            params={
                "key": self.api_key,
                "origin": origin_str,
                "destination": dest_str,
                "strategy": 2,          # 常规最快
                "extensions": "base",    # 基本信息
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

    def calculate(self, origin: str, destination: str) -> dict:
        """完整计算流程：地址 → 地理编码 → 驾车距离"""
        # Step 1: 地理编码
        origin_coords = self.geocode(origin)
        dest_coords = self.geocode(destination)

        # Step 2: 驾车路径规划
        result = self.driving_distance(origin_coords, dest_coords)
        result.update({
            "origin": origin,
            "destination": destination,
            "origin_coords": f"{origin_coords[0]},{origin_coords[1]}",
            "destination_coords": f"{dest_coords[0]},{dest_coords[1]}",
        })
        return result
```

### 3.4 缓存策略

行程路线相对固定，采用内存缓存减少 API 调用：

- **缓存 key**: `origin:destination`（标准化后）
- **缓存时长**: 24 小时
- **实现**: 使用 Python `dict` + 时间戳，进程内缓存
- **缓存上限**: 1000 条，LRU 淘汰

```python
import time

_route_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 86400  # 24 小时
_CACHE_MAX = 1000

def get_cached_distance(origin: str, destination: str) -> dict | None:
    key = f"{origin}:{destination}"
    if key in _route_cache:
        ts, data = _route_cache[key]
        if time.time() - ts < _CACHE_TTL:
            return data
        del _route_cache[key]
    return None

def set_cached_distance(origin: str, destination: str, data: dict):
    if len(_route_cache) >= _CACHE_MAX:
        # LRU: 删除最早的
        oldest = min(_route_cache, key=lambda k: _route_cache[k][0])
        del _route_cache[oldest]
    key = f"{origin}:{destination}"
    _route_cache[key] = (time.time(), data)
```

---

## 4. 配置

### 4.1 高德 API Key 获取

1. 注册 [高德开放平台](https://lbs.amap.com/) 账号
2. 进入控制台 → 创建应用 → 添加 Key
3. 选择 **Web 服务** 类型
4. 获得的 Key 即为 `AMAP_API_KEY`

### 4.2 系统配置

**`configs/config.yaml`**:

```yaml
tools:
  maps:
    amap_api_key: ${AMAP_API_KEY}
```

**`.env`**:

```
AMAP_API_KEY=你的高德Web服务API密钥
```

**`src/config/settings.py`** 新增:

```python
class MapsToolConfig(BaseModel):
    amap_api_key: str = ""
```

在 `Settings` 类中新增:

```python
maps: MapsToolConfig = MapsToolConfig()
```

---

## 5. 与旅游顾问子智能体的集成

### 5.1 SUBAGENT.md 配置

在 `subagents/travel-consultant/SUBAGENT.md` 的 `skills.allowed` 中添加:

```yaml
skills:
  allowed:
    - travel-quote
    - paddleocr-doc-parsing
    - route-distance        # 新增
```

### 5.2 调用时机

旅游顾问子智能体在生成报价时：

```
用户: 我要从贵阳出发去黄果树瀑布玩3天，25人

旅游顾问:
  1. 识别行程: 贵阳 → 黄果树瀑布
  2. 检查车辆定价模式
  3. 如果有 per_km 模式车辆:
     a. 调用 use_skill("route-distance", {"origin": "贵阳市", "destination": "黄果树瀑布"})
     b. 获取导航距离 128.5 公里
     c. 调用 calculate_vehicle_cost(route_distance_km=128.5)
  4. 生成报价
```

### 5.3 报价流程集成

在 `src/skills/travel-quote/scripts/generate.py` 中:

```python
# 报价生成主流程中
if has_per_km_vehicles(vehicles):
    # 调用 route-distance skill 获取导航距离
    route_result = use_skill("route-distance", {
        "origin": departure_city,
        "destination": destination
    })
    if route_result["success"]:
        route_distance_km = route_result["distance_km"]
    else:
        route_distance_km = None  # 降级到 daily 模式

    vehicle_items, vehicle_cost = calculate_vehicle_cost(
        items, tenant_id, region_names, total_people,
        trip_days, season_type, vehicle_count,
        route_distance_km=route_distance_km  # 新增参数
    )
```

---

## 6. 错误处理

| 错误场景 | 处理方式 |
|---------|---------|
| API Key 未配置 | 返回明确错误 "未配置高德地图 API Key" |
| 地址无法解析 | 返回 "地址解析失败: {address}"，提示用户使用更详细的地址 |
| 无可达路线 | 返回 "无法规划路线"，可能是海外地址或海上 |
| API 调用超时 | 10 秒超时，重试 1 次后返回错误 |
| API 调用频率超限 | 记录日志，返回缓存数据（如有）或错误 |
| 网络异常 | 返回 "网络异常，无法计算导航距离" |

**降级策略**: 如果导航距离计算失败，自动降级使用 `daily` 模式计价，不阻塞报价生成。

---

## 7. 高德 API 免费配额

| API | 免费额度 | 本项目预估用量 |
|-----|---------|-------------|
| 地理编码 | 5,000 次/天 | ~50 次/天（每次报价 2 次调用） |
| 驾车路径规划 | 5,000 次/天 | ~25 次/天 |
| 合计 | 10,000 次/天 | ~75 次/天 |

**缓存后实际消耗更低**：热门路线（贵阳→黄果树、贵阳→荔波等）只需首次调用。

---

## 8. 文件清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `docs/skills/mapskill/route_distance_skill_design.md` | 本文档 |
| `src/skills/route-distance-1.0.0/SKILL.md` | Skill 定义文件 |
| `src/skills/route-distance-1.0.0/scripts/route_distance.py` | 核心计算脚本 |

### 需要修改的文件

| 文件 | 改动 |
|------|------|
| `configs/config.yaml` | 新增 `tools.maps.amap_api_key` |
| `src/config/settings.py` | 新增 `MapsToolConfig` + `maps` 字段 |
| `subagents/travel-consultant/SUBAGENT.md` | `skills.allowed` 新增 `route-distance` |
| `src/skills/travel-quote/scripts/generate.py` | 报价流程集成导航距离 |

---

## 9. 后续扩展

当前 v1.0 仅支持两点间距离计算，后续可扩展：

| 功能 | 说明 | 优先级 |
|------|------|--------|
| 途经点规划 | 支持多点路线（A→B→C→D），计算总距离 | 高 |
| 地址自动补全 | 输入部分地址时提供候选列表 | 中 |
| 路线距离缓存持久化 | 缓存到数据库，重启不丢失 | 低 |
| 多策略对比 | 同时返回最短距离和最快时间 | 低 |
