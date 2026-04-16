# 外贸客户信息管理技能

用于保存外贸获客过程中匹配到的客户信息，跟踪邮件发送记录。

## 功能特性

- 保存匹配的客户信息（公司名称、联系人、邮箱、国家、行业等）
- 记录客户来源（用户ID、Session ID、匹配日期）
- 跟踪邮件发送历史（邮件主题、正文、发送时间、状态）
- 客户和邮件的增删改查
- 用户统计信息（客户总数、邮件发送统计、国家分布等）

## 文件结构

```
trade-customer-1.0.0/
├── SKILL.md           # 技能定义和使用说明
├── README.md          # 本文件
├── _meta.json         # 元数据
└── scripts/
    └── customer_manager.py  # 客户管理核心脚本
```

## 快速开始

### 1. 初始化数据库

脚本会自动创建所需的数据库表，无需手动操作。

### 2. 保存客户

```bash
python scripts/customer_manager.py save-customers \
  --user-id "user_xxx" \
  --session-id "session_xxx" \
  --customers '[{"company_name":"ABC Corp","contact_name":"John Smith","email":"luwei@aidingyi.cn","country":"USA","language":"en","industry":"Electronics","import_category":"LED Lights","company_size":"500","match_reason":"High match"}]'
```

### 3. 查询客户列表

```bash
python scripts/customer_manager.py list-customers --user-id "user_xxx"
```

### 4. 记录邮件发送

```bash
python scripts/customer_manager.py record-email \
  --customer-id "cust_xxx" \
  --user-id "user_xxx" \
  --session-id "session_xxx" \
  --subject "LED Lights Promotion" \
  --body "Dear Mr. Smith, we are pleased to introduce..." \
  --language "en" \
  --status "success"
```

### 5. 查看统计

```bash
python scripts/customer_manager.py stats --user-id "user_xxx"
```

## 数据库表

### bs_trade_specialist_matched_customers（匹配客户表）

| 字段 | 类型 | 说明 |
|------|------|------|
| customer_id | TEXT | 客户唯一ID |
| user_id | TEXT | 所属用户ID |
| session_id | TEXT | 所属会话ID |
| company_name | TEXT | 公司名称 |
| contact_name | TEXT | 联系人 |
| email | TEXT | 邮箱 |
| country | TEXT | 国家 |
| language | TEXT | 语言偏好 |
| industry | TEXT | 行业 |
| import_category | TEXT | 进口品类 |
| company_size | TEXT | 公司规模 |
| match_reason | TEXT | 匹配原因 |
| match_date | TEXT | 匹配日期 |
| created_at | TIMESTAMP | 创建时间 |

### customer_emails（客户邮件表）

| 字段 | 类型 | 说明 |
|------|------|------|
| email_id | TEXT | 邮件记录ID |
| customer_id | TEXT | 关联客户ID |
| user_id | TEXT | 操作用户ID |
| session_id | TEXT | 所属会话ID |
| email_subject | TEXT | 邮件主题 |
| email_body | TEXT | 邮件正文 |
| email_language | TEXT | 邮件语言 |
| send_time | TEXT | 发送时间 |
| send_status | TEXT | 发送状态 |
| error_message | TEXT | 错误信息 |
| created_at | TIMESTAMP | 创建时间 |

## 与外贸智能体集成

在外贸智能体（trade-specialist）的流程中：

1. **生成客户后**：调用 `save-customers` 保存匹配的客户
2. **发送邮件后**：调用 `record-email` 记录邮件发送
3. **任务完成时**：可调用 `stats` 查看本次获客统计

详细集成说明请参考 `SKILL.md`。
