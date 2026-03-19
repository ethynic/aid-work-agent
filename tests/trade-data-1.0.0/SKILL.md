---
name: trade-data
description: >
  查询外贸数据，包括进出口贸易记录、海关数据、国际贸易统计等信息。
  支持按海关编码(HS Code)、企业名称、产品描述、国家、时间范围等多维度查询进出口贸易记录。
  使用场景：外贸业务调研、市场分析、竞争对手分析、供应商/采购商背景调查、贸易数据统计等。
metadata:
  openclaw:
    requires:
      env:
        - TENDATA_API_KEY
    primaryEnv: TENDATA_API_KEY
    emoji: "🌍"
    homepage: https://open-api.tendata.cn
---

# 外贸数据查询技能

## 何时使用此技能

**使用外贸数据查询用于**：
- 查询特定产品的进出口贸易记录
- 分析竞争对手的进出口情况
- 调研潜在供应商或采购商
- 查询海关统计数据
- 分析特定国家/地区的贸易往来
- 按HS编码查询商品贸易情况
- 获取市场趋势和贸易统计

**关键词触发**：
- 外贸数据、进出口数据、海关数据
- 贸易记录、贸易统计
- HS编码、海关编码
- 进口商、出口商
- 供应商、采购商调研
- 国际贸易、外贸分析

## 如何使用此技能

**⛔ 强制限制 - 必须遵守 ⛔**

1. **仅使用外贸数据API** - 执行脚本 `python scripts/trade_query.py`
2. **不要尝试其他方法** - 不要建议使用其他数据源
3. **如果API失败** - 显示错误消息并立即停止
4. **没有回退方案** - 不要尝试其他方式获取外贸数据

如果脚本执行失败（API未配置、网络错误等）：
- 向用户显示错误消息
- 不要提供替代方案
- 等待用户修复配置

### 基本工作流程

#### 1. 查询贸易记录

**必需参数**：
- `catalog`: 数据源类型（imports-进口 / exports-出口）
- `startDate`: 开始日期（格式：YYYY-MM-DD）
- `endDate`: 结束日期（格式：YYYY-MM-DD）
- `pageNo`: 页码（从1开始）
- `pageSize`: 每页数量

**常用可选参数**：
- `hsCode`: 海关编码（4位以上）
- `importer`: 进口商名称
- `exporter`: 出口商名称
- `productDesc`: 产品描述（多个用分号分隔）
- `countryOfOriginCode`: 原产国代码
- `countryOfDestinationCode`: 目的国代码

**示例1：查询特定HS编码的进口记录**
```bash
python scripts/trade_query.py \
  --catalog imports \
  --start-date "2023-01-01" \
  --end-date "2023-12-31" \
  --hs-code "63049239" \
  --page-size 20
```

**示例2：查询特定企业的进口记录**
```bash
python scripts/trade_query.py \
  --catalog imports \
  --start-date "2024-01-01" \
  --end-date "2024-03-31" \
  --importer "华为" \
  --page-size 10
```

**示例3：查询特定产品的出口记录**
```bash
python scripts/trade_query.py \
  --catalog exports \
  --start-date "2024-01-01" \
  --end-date "2024-06-30" \
  --product-desc "电子产品;手机" \
  --country-of-destination "USA" \
  --page-size 15
```

**示例4：查询特定国家/地区的贸易记录**
```bash
python scripts/trade_query.py \
  --catalog imports \
  --start-date "2024-01-01" \
  --end-date "2024-12-31" \
  --country-of-origin "CHN;JPN" \
  --page-size 20
```

### 参数详细说明

#### 必需参数

| 参数 | 类型 | 说明 | 示例 |
|------|------|------|------|
| catalog | string | 数据源类型 | imports（进口）/ exports（出口） |
| startDate | string | 开始日期 | 2024-01-01 |
| endDate | string | 结束日期 | 2024-12-31 |
| pageNo | integer | 页码（从1开始） | 1 |
| pageSize | integer | 每页条数 | 10 |

#### 常用可选参数

| 参数 | 类型 | 说明 | 示例 |
|------|------|------|------|
| hsCode | string | 海关编码（4位以上） | 63049239 |
| exporter | string | 出口商名称（≥2字符） | 华为技术 |
| importer | string | 进口商名称（≥2字符） | Apple Inc |
| productDesc | string | 产品描述（多关键词用;分隔） | 电子产品;手机 |
| countryOfOriginCode | string | 原产国代码（多个用;分隔） | CHN;USA |
| countryOfDestinationCode | string | 目的国代码（多个用;分隔） | USA;GBR |

#### 高级可选参数

| 参数 | 类型 | 说明 |
|------|------|------|
| containProducer | boolean | 是否包含生产企业（默认false） |
| portOfDeparture | string | 起运港 |
| portOfArrival | string | 目的港 |
| transportType | string | 运输方式（海运/空运等） |
| weight | array | 重量区间（千克）[100, 1000] |
| quantity | array | 数量区间[1, 100] |
| quantityUnit | string | 数量单位 |
| sumOfUSD | array | 美元总价区间[1000, 10000] |
| weightUnitPriceUSD | array | 重量美元单价区间[10, 50] |
| quantityUnitPriceUSD | array | 数量美元单价区间[5, 20] |
| filterBlankFields | array | 过滤空白公司["importer", "exporter"] |
| filterLogisticFields | array | 过滤物流公司["importer", "exporter"] |
| searchMode | string | 搜索模式（FUZZY_SINGLE开启模糊搜索） |
| optimizationModel | boolean | 优选模式（默认false） |

### 国家代码参考

常用国家代码：
- **CHN**: 中国
- **USA**: 美国
- **JPN**: 日本
- **KOR**: 韩国
- **GBR**: 英国
- **DEU**: 德国
- **FRA**: 法国
- **IND**: 印度
- **BRA**: 巴西
- **AUS**: 澳大利亚
- **CAN**: 加拿大
- **RUS**: 俄罗斯
- **ITA**: 意大利
- **ESP**: 西班牙

### 理解返回结果

脚本返回JSON格式数据：

```json
{
  "success": true,
  "data": {
    "total": 150,
    "pageNo": 1,
    "pageSize": 10,
    "records": [
      {
        "hsCode": "63049239",
        "productDesc": "纺织品",
        "exporter": "Example Exporter",
        "importer": "Example Importer",
        "countryOfOrigin": "CHN",
        "countryOfDestination": "USA",
        "weight": 1000,
        "quantity": 100,
        "sumOfUSD": 5000,
        "tradeDate": "2023-06-15",
        "portOfDeparture": "上海",
        "portOfArrival": "洛杉矶",
        "transportType": "海运"
      }
    ]
  }
}
```

**关键字段**：
- `total`: 总记录数
- `records`: 贸易记录列表
- 每条记录包含：
  - `hsCode`: 海关编码
  - `productDesc`: 产品描述
  - `exporter`: 出口商
  - `importer`: 进口商
  - `countryOfOrigin`: 原产国
  - `countryOfDestination`: 目的国
  - `weight`: 重量（千克）
  - `quantity`: 数量
  - `sumOfUSD`: 美元总价
  - `tradeDate`: 交易日期
  - `portOfDeparture`: 起运港
  - `portOfArrival`: 目的港
  - `transportType`: 运输方式

### 分页查询

当记录总数较多时，需要进行分页查询：

```python
# 第一次查询（第1页）
python scripts/trade_query.py \
  --catalog imports \
  --start-date "2023-01-01" \
  --end-date "2023-12-31" \
  --hs-code "63049239" \
  --page 1 \
  --page-size 50

# 根据返回的total字段，继续查询后续页面
# 如果total=150，pageSize=50，则需要查询3页
```

### 高级用法

#### 1. 模糊搜索

启用模糊搜索模式，提高匹配率：

```bash
python scripts/trade_query.py \
  --catalog imports \
  --start-date "2024-01-01" \
  --end-date "2024-06-30" \
  --importer "华为" \
  --search-mode FUZZY_SINGLE \
  --page-size 20
```

#### 2. 过滤空白公司和物流公司

```bash
python scripts/trade_query.py \
  --catalog imports \
  --start-date "2024-01-01" \
  --end-date "2024-06-30" \
  --hs-code "63049239" \
  --filter-blank-fields '["importer"]' \
  --filter-logistic-fields '["exporter"]' \
  --page-size 20
```

#### 3. 价格和重量过滤

```bash
python scripts/trade_query.py \
  --catalog imports \
  --start-date "2024-01-01" \
  --end-date "2024-06-30" \
  --hs-code "63049239" \
  --weight '[100, 1000]' \
  --sum-of-usd '[1000, 10000]' \
  --page-size 20
```

### 首次配置

**当API未配置时**：

错误将显示：
```
TENDATA_API_KEY not configured. Get your API key at: https://open-api.tendata.cn
```

**配置工作流程**：

1. **显示确切的错误消息**给用户（包括URL）

2. **引导用户安全配置**：
   - 推荐通过应用程序的标准方法配置（例如，设置文件、环境变量UI）
   - 列出必需的环境变量：
     ```
     - TENDATA_API_KEY
     ```
   - 在skill目录下创建 `.env` 文件并添加：
     ```
     TENDATA_API_KEY=your_api_key_here
     ```

3. **如果用户在对话中提供凭证**（接受任何合理格式）：
   - `TENDATA_API_KEY=WvXBTCmlOtfjVfNbD1ywrTuAXNdLETxo`
   - `这是我的API密钥：WvXBTCmlOtfjVfNbD1ywrTuAXNdLETxo`
   - 复制粘贴的代码格式
   - **安全提示**：警告用户在对话中共享的凭证可能存储在对话历史中

4. **解析并验证值**：
   - 提取 `TENDATA_API_KEY`
   - 确认API密钥格式正确
   - 告诉用户需要在 `.env` 文件中设置哪个环境变量

5. **请用户确认环境已配置**：
   - 等待用户确认已在适当的位置设置了值

6. **确认后重试**：
   - 用户确认环境变量可用后，重试原始查询任务

**重要**：错误消息格式是严格的，必须完全按照脚本提供的显示。不要修改或改写。

### 错误处理

**认证失败（403）**：
```
error: Authentication failed
```
→ API密钥无效，使用正确的凭证重新配置

**API配额超限（429）**：
```
error: API quota exceeded
```
→ 每日API配额耗尽，通知用户等待或升级

**参数错误（400）**：
```
error: Invalid parameters
```
→ 检查参数格式，确保必需参数已提供

**网络错误**：
```
error: Network error
```
→ 检查网络连接，稍后重试

## 重要说明

- **脚本从不过滤内容** - 它总是返回完整的API响应
- **AI代理决定呈现什么** - 基于用户的具体请求
- **所有数据始终可用** - 可以针对不同需求重新解释
- **没有信息丢失** - 完整的贸易记录被保留

## 参考文档

详细的API文档请参考：
- 官方API文档：https://open-api.tendata.cn/docs

## 测试技能

要验证技能是否正常工作：
```bash
python scripts/test_api.py
```

这将测试配置和API连接。
