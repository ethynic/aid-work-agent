---
name: route-distance
description: 计算两个地点之间的驾车导航距离和预计时间（基于高德地图 API）。支持模糊地址输入，如景点名称、城市名称。
metadata:
  version: "1.0.0"
  author: aid-work-agent
dependencies:
  - httpx>=0.24.0
---

# 导航距离计算

计算两个地点之间的驾车导航距离和预计行驶时间。

**触发词**："距离"、"导航距离"、"驾车距离"、"公里数"、"车程"、"多远"

## 调用方式

```python
skill_execute(
  skill="route-distance",
  command="python scripts/route_distance.py",
  content='<JSON 参数>'
)
```

**必须通过 `content` 参数传递 JSON**，不要用命令行参数。

## 输入参数（JSON）

```json
{
  "origin": "贵阳市",
  "destination": "黄果树瀑布",
  "region": "贵州"
}
```

### 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `origin` | string | 是 | 出发地地址（支持模糊地址，如"贵阳北站"、"黄果树瀑布"） |
| `destination` | string | 是 | 目的地地址 |
| `region` | string | 否 | 地区限定（如"贵州"、"云南"），提高模糊地址的解析准确度 |

## 输出格式

**成功**：

```json
{
  "success": true,
  "origin": "贵阳市",
  "destination": "黄果树瀑布",
  "distance_km": 128.1,
  "duration_hours": 1.5,
  "tolls": 66,
  "origin_coords": "106.628201,26.646694",
  "destination_coords": "105.682368,26.010005",
  "origin_formatted": "贵州省贵阳市",
  "destination_formatted": "贵州省安顺市镇宁布依族苗族自治县黄果树瀑布村"
}
```

**失败**：

```json
{
  "success": false,
  "error": "地址解析失败: xxx"
}
```

### 返回字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `distance_km` | float | 导航距离（公里），基于实际道路计算 |
| `duration_hours` | float | 预计行驶时间（小时） |
| `tolls` | float | 道路收费（元） |
| `origin_coords` | string | 出发地坐标 `经度,纬度` |
| `destination_coords` | string | 目的地坐标 `经度,纬度` |
| `origin_formatted` | string | 出发地完整地址（可用于验证解析是否正确） |
| `destination_formatted` | string | 目的地完整地址（可用于验证解析是否正确） |

### 错误类型

| 错误信息 | 原因 |
|---------|------|
| `未配置高德地图 API Key` | 环境变量 AMAP_API_KEY 未设置 |
| `地址解析失败: {address}` | 地址无法识别，建议使用更详细的地址 |
| `无法规划路线` | 两地之间无可达驾车路线（如海外地址） |
| `网络异常，无法计算导航距离` | API 调用失败 |

## 使用示例

- "贵阳到黄果树瀑布有多远？" → `{"origin": "贵阳", "destination": "黄果树瀑布", "region": "贵州"}`
- "从贵阳龙洞堡机场到市区车程多久？" → `{"origin": "贵阳龙洞堡机场", "destination": "贵阳市区", "region": "贵州"}`
- "计算荔波小七孔到西江千户苗寨的距离" → `{"origin": "荔波小七孔", "destination": "西江千户苗寨", "region": "贵州"}`

## 注意事项

- **导航距离 ≠ 直线距离**，返回的是基于实际道路的驾车距离
- 高德 API 免费额度 5000 次/天，热门路线有内存缓存，无需担心超额
- 地址支持模糊输入：景点名、建筑名、城市名均可
- 同一对地址的缓存有效期为 24 小时
