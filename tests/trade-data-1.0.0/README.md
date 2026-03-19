# 外贸数据查询技能

## 简介

本技能用于查询外贸数据，包括进出口贸易记录、海关数据、国际贸易统计等信息。通过Tendata API获取准确、及时的国际贸易数据。

## 功能特性

- ✅ 查询进口/出口贸易记录
- ✅ 多维度筛选：HS编码、企业名称、产品描述、国家、港口等
- ✅ 支持分页查询
- ✅ 支持模糊搜索
- ✅ 自动管理访问令牌（Access Token）
- ✅ 完整的错误处理

## 安装配置

### 1. 获取API密钥

访问 [Tendata开放平台](https://open-api.tendata.cn) 注册并获取API密钥。

### 2. 配置API密钥

在skill目录下创建 `.env` 文件：

```bash
# 复制示例文件
cp .env.example .env

# 编辑.env文件，填入您的API密钥
TENDATA_API_KEY=your_api_key_here
```

或者设置环境变量：

```bash
export TENDATA_API_KEY=your_api_key_here
```

### 3. 安装依赖

```bash
pip install -r scripts/requirements.txt
```

### 4. 测试配置

运行测试脚本验证配置是否正确：

```bash
python scripts/test_api.py
```

## 使用方法

### 基本用法

**查询特定HS编码的进口记录：**

```bash
python scripts/trade_query.py \
  --catalog imports \
  --start-date "2023-01-01" \
  --end-date "2023-12-31" \
  --hs-code "63049239" \
  --page-size 20
```

**查询特定企业的进口记录：**

```bash
python scripts/trade_query.py \
  --catalog imports \
  --start-date "2024-01-01" \
  --end-date "2024-03-31" \
  --importer "华为"
```

**查询特定产品的出口记录：**

```bash
python scripts/trade_query.py \
  --catalog exports \
  --start-date "2024-01-01" \
  --end-date "2024-06-30" \
  --product-desc "电子产品;手机"
```

### 参数说明

#### 必需参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `--catalog` | 数据源类型 | imports（进口）/ exports（出口） |
| `--start-date` | 开始日期 | 2024-01-01 |
| `--end-date` | 结束日期 | 2024-12-31 |

#### 可选参数

| 参数 | 说明 |
|------|------|
| `--hs-code` | 海关编码（4位以上） |
| `--importer` | 进口商名称 |
| `--exporter` | 出口商名称 |
| `--product-desc` | 产品描述（多关键词用分号分隔） |
| `--country-of-origin` | 原产国代码（如：CHN;USA） |
| `--country-of-destination` | 目的国代码（如：USA;CHN） |
| `--port-of-departure` | 起运港 |
| `--port-of-arrival` | 目的港 |
| `--search-mode` | 搜索模式（FUZZY_SINGLE开启模糊搜索） |
| `--page-no` | 页码（从1开始，默认1） |
| `--page-size` | 每页条数（默认10） |
| `--pretty` | 美化JSON输出 |

### 常见国家代码

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

## 错误处理

### 常见错误

**1. API密钥无效**

```
error: API错误: user invalid
```

解决方案：
- 检查API密钥是否正确
- 确认API密钥是否已激活
- 联系Tendata客服确认账户状态

**2. API配额超限**

```
error: API quota exceeded
```

解决方案：
- 等待配额重置（通常每天重置）
- 升级API套餐

**3. 网络错误**

```
error: Network error
```

解决方案：
- 检查网络连接
- 确认防火墙设置
- 稍后重试

## 在智能体中使用

当智能体检测到用户需要查询外贸数据时，会自动调用此技能：

**用户**: "帮我查询HS编码63049239的进口记录"

**智能体**: 自动调用 `trade-data` skill并执行查询

## 注意事项

1. **API密钥安全**: 不要在代码中硬编码API密钥，使用.env文件或环境变量
2. **频率限制**: API有调用频率限制，建议合理控制查询频率
3. **数据时效**: 数据可能有1-2天的延迟
4. **权限范围**: 确保API密钥有足够的权限访问所需数据

## 技术支持

- API文档: https://open-api.tendata.cn/docs
- 技术支持: 联系Tendata客服

## 更新日志

### v1.0.0 (2024-03-17)
- ✨ 初始版本发布
- ✨ 支持进出口贸易记录查询
- ✨ 自动Access Token管理
- ✨ 多维度数据筛选
- ✨ 完整的错误处理
