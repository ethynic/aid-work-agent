---
name: lead-management
description: >
  销售线索管理技能，用于线索的导入、增删改查、阶段流转、分配、统计分析。
  支持 Excel 批量导入、手动录入、阶段管理（new→contacting→qualified→proposal→negotiation→won/lost）、
  智能分配（轮询/负载均衡/区域匹配）、线索评分和统计。
  使用场景：客户跟进智能体的核心数据管理。
init_script: lead_manager.py
metadata:
  openclaw:
    emoji: "🎯"
    requires:
      bins: ["python"]
---

# 销售线索管理技能

## 何时使用此技能

**使用线索管理技能用于**：
- Excel 批量导入销售线索
- 手动添加单条线索
- 查询和筛选线索列表
- 更新线索阶段（new→contacting→qualified→proposal→negotiation→won/lost）
- 将线索分配给销售人员
- 获取线索统计数据

**关键词触发**：
- 导入线索
- 添加线索
- 线索列表
- 线索详情
- 更新线索
- 删除线索
- 分配线索
- 线索统计
- 导出线索

---

## 数据表结构

### 1. 线索主表 (bs_customer_followup_leads)

| 字段 | 类型 | 说明 |
|------|------|------|
| lead_id | TEXT | 线索唯一ID（自动生成 lead_<uuid12>） |
| tenant_id | TEXT | 租户ID（自动填充） |
| user_id | TEXT | 创建/导入用户ID（自动填充） |
| company_name | TEXT | 公司名称 |
| contact_name | TEXT | 联系人姓名 |
| phone | TEXT | 电话 |
| email | TEXT | 邮箱 |
| source | TEXT | 来源: import/api/manual/referral/website/exhibition |
| industry | TEXT | 行业 |
| region | TEXT | 地区 |
| product_interest | TEXT | 感兴趣的产品 |
| budget_range | TEXT | 预算范围 |
| estimated_deal_amount | NUMERIC | 预计成交金额 |
| description | TEXT | 备注 |
| stage | TEXT | 阶段: new/contacting/qualified/proposal/negotiation/won/lost |
| score | INTEGER | 线索评分 0-100 |
| assigned_to | TEXT | 被分配的销售人员 user_id |
| status | TEXT | 状态: active/converted/lost/recycled |
| next_followup_at | TIMESTAMP | 下次跟进时间 |
| tags | TEXT[] | 标签数组 |

### 2. 跟进记录表 (bs_customer_followup_records)

| 字段 | 类型 | 说明 |
|------|------|------|
| record_id | TEXT | 记录唯一ID |
| lead_id | TEXT | 关联线索 |
| user_id | TEXT | 跟进人 |
| followup_type | TEXT | phone/email/visit/wechat/ai_call/other |
| content | TEXT | 跟进内容 |
| outcome | TEXT | positive/neutral/negative/no_response |
| quality_score | INTEGER | LLM 评估质量分 1-10 |
| call_transcript | TEXT | AI 外呼转写文本 |
| call_sentiment | TEXT | 通话情感 |

### 3. 销售人员表 (bs_customer_followup_sales_reps)

| 字段 | 类型 | 说明 |
|------|------|------|
| rep_id | TEXT | 销售唯一ID |
| user_id | TEXT | 系统用户ID |
| name | TEXT | 姓名 |
| active_lead_count | INTEGER | 当前活跃线索数 |
| max_leads | INTEGER | 最大线索配额 |
| region | TEXT | 负责区域 |

### 4. 分配规则表 (bs_customer_followup_assign_rules)

| 字段 | 类型 | 说明 |
|------|------|------|
| rule_id | TEXT | 规则唯一ID |
| rule_type | TEXT | round_robin/load_balance/region_based/skill_based/manual |
| conditions | JSONB | 匹配条件 |
| target_rep_ids | TEXT[] | 目标销售ID列表 |

### 5. 转化漏斗表 (bs_customer_followup_conversion_funnel)（仅追加）

| 字段 | 类型 | 说明 |
|------|------|------|
| funnel_id | TEXT | 记录唯一ID |
| lead_id | TEXT | 关联线索 |
| from_stage | TEXT | 前一阶段 |
| to_stage | TEXT | 后一阶段 |
| days_in_previous_stage | INTEGER | 在前阶段停留天数 |

---

## 阶段定义

```
new（新线索）→ contacting（联系中）→ qualified（有意向）→ proposal（方案中）→ negotiation（谈判中）→ won（成交）
                                                                                                   ↘ lost（丢失）
```

---

## 如何使用此技能

### 1. 初始化数据表

首次使用时自动调用，无需手动执行：

```bash
python scripts/lead_manager.py init_tables
```

### 2. Excel 导入线索

```bash
python scripts/lead_manager.py import-leads \
  --user-id "user_xxx" \
  --file-path "/path/to/leads.xlsx" \
  --mapping '{"公司名称":"company_name","联系人":"contact_name","电话":"phone","邮箱":"email","行业":"industry"}'
```

**mapping 参数**：将 Excel 列名映射到系统字段名。如果不提供 mapping，则自动匹配以下列名：
- company_name / 公司名称 / 公司
- contact_name / 联系人 / 姓名
- phone / 电话 / 手机
- email / 邮箱 / 邮件
- industry / 行业
- region / 地区 / 区域
- source / 来源

### 3. 手动添加线索

```bash
python scripts/lead_manager.py add-lead \
  --user-id "user_xxx" \
  --lead '{"company_name":"ABC公司","contact_name":"张三","phone":"13800138000","email":"zhang@abc.com","industry":"制造业","region":"华东","source":"manual"}'
```

### 4. 查询线索列表

```bash
# 查询所有线索
python scripts/lead_manager.py list-leads --user-id "user_xxx"

# 按阶段筛选
python scripts/lead_manager.py list-leads --user-id "user_xxx" --stage "new"

# 按状态筛选
python scripts/lead_manager.py list-leads --user-id "user_xxx" --status "active"

# 关键词搜索
python scripts/lead_manager.py list-leads --user-id "user_xxx" --keyword "ABC公司"

# 分页
python scripts/lead_manager.py list-leads --user-id "user_xxx" --page 1 --page-size 20
```

### 5. 查看线索详情

```bash
python scripts/lead_manager.py get-lead --lead-id "lead_xxx"
```

### 6. 更新线索信息

```bash
python scripts/lead_manager.py update-lead \
  --lead-id "lead_xxx" \
  --fields '{"phone":"13900139000","description":"高价值客户"}'
```

### 7. 变更线索阶段

```bash
python scripts/lead_manager.py update-stage \
  --lead-id "lead_xxx" \
  --stage "qualified" \
  --note "客户表达采购意向"
```

阶段变更会自动在转化漏斗表中记录。

### 8. 删除线索（软删除）

```bash
python scripts/lead_manager.py delete-lead \
  --lead-id "lead_xxx" \
  --reason "重复线索"
```

### 9. 分配线索

```bash
# 手动分配
python scripts/lead_manager.py assign-lead \
  --lead-id "lead_xxx" \
  --assigned-to "user_yyy"

# 按规则自动分配
python scripts/lead_manager.py assign-lead \
  --lead-id "lead_xxx" \
  --rule "load_balance"
```

### 10. 获取线索统计

```bash
python scripts/lead_manager.py stats --user-id "user_xxx"
```

返回各阶段线索数量、来源分布、跟进状态等统计数据。

### 11. 导出线索

```bash
python scripts/lead_manager.py export-leads \
  --user-id "user_xxx" \
  --stage "new" \
  --format "xlsx"
```

---

## 输出格式

所有命令返回 JSON 格式结果：

```json
{"success": true, "data": { ... }}
{"success": false, "error": "错误描述", "debug": "详细信息"}
```

---

## 错误处理

| 错误情况 | 处理方式 |
|---------|---------|
| 缺少必需参数 | 显示参数使用说明 |
| 数据库连接失败 | 返回错误信息 |
| 线索不存在 | 返回错误提示 |
| JSON 格式错误 | 返回解析错误详情 |
| Excel 文件不存在 | 返回文件错误 |
| 无效阶段值 | 提示有效阶段列表 |
