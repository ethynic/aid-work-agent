# 价格知识库结构定义 Price Table Schema

**权威价格来源是你提供的资料库（knowledge base）官方报价清单 / 标准价格表**，所有合同定价以其记录的标准售价为准。本技能**不内置任何价格数据**，也不会提供示例价目；你需准备自己的价格库 CSV（格式如下）通过 `--prices` 传入。后续可把取数实现直接替换为资料库 API，只要保留「按名称+模式查单价」的能力即可。

**技能里不写死任何「哪个字段要查价」。** 查价规则由第 2 步运行时生成的
`analysis.json` 声明（`deploy_mode`、`fields[].price_lookup`、
`service_tables[].columns[].price_lookup`），见 `analysis_schema.md`。

## CSV 字段

| 列名 | 说明 | 必填 |
|------|------|------|
| 名称 | 产品/服务的标准名称，用于查表匹配 | 是 |
| 模式 | 公有云 / 私有云（对应合同部署方式） | 是 |
| 单价 | 标准单价（数字，如 3000 / 20000 / 110000） | 是 |
| 单位 | 计价单位，如 元/起充(获30000点) / 元/套(一次性部署) / 元/月 | 否 |
| 币种 | 如 CNY / USD | 否，默认 CNY |
| 备注 | 价格口径说明，如「渠道商价」「项目参考价」 | 否 |

同一产品按公有云 / 私有云各登记一行即可。

## 匹配规则（scripts/lookup_price.py）

- 以清单条目的名称（或 `price_lookup.query_key` 指定的字段值）为 query。
- 用 `deploy_mode` 或 `price_lookup.mode` 过滤部署方式。
- 名称列做双向包含匹配，忽略大小写与首尾空格；命中多条取第一条，报告里会记录命中名称。
- 未命中时不编造价格：回填报告记 warning，由技能在第 3 步请用户确认或补充。

## 两种调用方式

> 以下示例用占位文件名演示**命令格式**；实际请传入**你自己的价格库 CSV**（来自资料库报价清单的标准售价）。

```bash
# 单条查询（对照已核对的价格库）
python3 scripts/lookup_price.py <已核对的价格库.csv> "AI在线客服" --mode 私有云 --json

# 批量补价（清单条数多时）
python3 scripts/lookup_price.py <已核对的价格库.csv> \
    --items service_items.json --name-key name --price-key unit_price \
    --mode 私有云 --out service_items_priced.json
```

第 4 步 `fill_contract.py` 也会自动对清单中缺价、且列声明了 `price_lookup` 的条目查价，
因此批量补价是可选优化，不是必需步骤。**查价所用的价格库必须以资料库报价清单为准。**

## 维护方式

**本技能不内置任何价格数据**，你需准备自己的价格库 CSV（列如下），通过 `--prices` 传入 `lookup_price.py` / `fill_contract.py`。可从资料库（knowledge base）的官方报价清单文档导出标准售价后另存为 CSV（如 `prices.csv`）。新增产品只需按下方字段追加行。
