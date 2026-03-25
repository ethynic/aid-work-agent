---
name: trade-customer
description: >
  外贸客户信息管理技能，用于保存外贸获客过程中匹配到的客户信息，
  跟踪邮件发送记录。包括客户信息的增删改查、邮件发送历史记录等功能。
  使用场景：外贸客户开发、客户信息持久化、邮件营销跟踪。
metadata:
  openclaw:
    emoji: "📋"
    requires:
      bins: ["python"]
---

# 外贸客户信息管理技能

## 何时使用此技能

**使用外贸客户管理用于**：
- 保存外贸获客过程中匹配到的客户信息
- 记录客户所在的 session 和用户信息
- 跟踪为哪些客户发送了邮件
- 查看历史邮件发送记录
- 客户信息的持久化存储

**关键词触发**：
- 保存客户
- 客户信息记录
- 邮件发送记录
- 客户列表
- 发送邮件给客户
- 邮件历史
- 客户跟踪

---

## 数据表结构

### 1. 匹配客户表 (matched_customers)

记录外贸获客过程中匹配到的客户信息：

| 字段 | 类型 | 说明 |
|------|------|------|
| customer_id | TEXT | 客户唯一ID |
| user_id | TEXT | 所属用户ID |
| session_id | TEXT | 所属会话ID |
| company_name | TEXT | 公司名称 |
| contact_name | TEXT | 联系人姓名 |
| email | TEXT | 邮箱地址 |
| country | TEXT | 国家/地区 |
| language | TEXT | 语言偏好 |
| industry | TEXT | 行业 |
| import_category | TEXT | 进口品类 |
| company_size | TEXT | 公司规模 |
| match_reason | TEXT | 匹配原因/说明 |
| match_date | TEXT | 匹配日期 |
| created_at | TEXT | 创建时间 |

### 2. 客户邮件表 (customer_emails)

跟踪发送给客户的邮件：

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
| send_status | TEXT | 发送状态(success/failed/pending) |
| error_message | TEXT | 错误信息(如有) |
| created_at | TEXT | 创建时间 |

---

## 如何使用此技能

### 1. 保存匹配的客户

当外贸智能体通过 `content_generate` 生成客户列表后，使用此技能保存客户信息：

```bash
python scripts/customer_manager.py save-customers \
  --user-id "user_xxx" \
  --session-id "session_xxx" \
  --customers '[{"company_name":"ABC Corp","contact_name":"John Smith","email":"luwei@aidingyi.cn","country":"USA","language":"en","industry":"Electronics","import_category":"LED Lights","company_size":"500","match_reason":"High match - Electronics industry"}]'
```

**参数说明**：
- `--user-id`: 用户ID（必需）
- `--session-id`: 会话ID（必需）
- `--customers`: 客户信息JSON数组（必需）

### 2. 查询客户列表

查看某个用户或会话的客户列表：

```bash
# 查看用户的所有客户
python scripts/customer_manager.py list-customers \
  --user-id "user_xxx"

# 查看特定会话的客户
python scripts/customer_manager.py list-customers \
  --user-id "user_xxx" \
  --session-id "session_xxx"
```

### 3. 查询客户详情

查看特定客户的详细信息：

```bash
python scripts/customer_manager.py get-customer \
  --customer-id "cust_xxx"
```

### 4. 记录邮件发送

当使用 `email_send` 发送邮件后，记录邮件发送信息：

```bash
python scripts/customer_manager.py record-email \
  --customer-id "cust_xxx" \
  --user-id "user_xxx" \
  --session-id "session_xxx" \
  --subject "LED Lights Product Promotion" \
  --body "Dear Mr. Smith, we are pleased to..." \
  --language "en" \
  --status "success"
```

**参数说明**：
- `--customer-id`: 客户ID（必需）
- `--user-id`: 操作用户ID（必需）
- `--session-id`: 会话ID（必需）
- `--subject`: 邮件主题（必需）
- `--body`: 邮件正文（必需）
- `--language`: 邮件语言，默认 en
- `--status`: 发送状态 success/failed/pending，默认 success

### 5. 查询客户邮件历史

查看某个客户的邮件发送历史：

```bash
python scripts/customer_manager.py list-emails \
  --customer-id "cust_xxx"
```

### 6. 查询用户的邮件历史

查看某个用户发送的所有邮件：

```bash
python scripts/customer_manager.py list-emails \
  --user-id "user_xxx" \
  --limit 50
```

### 7. 获取客户统计

获取用户的客户统计信息：

```bash
python scripts/customer_manager.py stats \
  --user-id "user_xxx"
```

---

## 工作流程集成

### 外贸获客完整流程

```
用户：推广LED产品
         ↓
外贸智能体：使用 content_generate 生成客户列表
         ↓
【调用 skill_execute 保存客户】
python scripts/customer_manager.py save-customers ...
         ↓
外贸智能体：使用 content_generate 生成邮件内容
         ↓
外贸智能体：使用 email_send 发送邮件
         ↓
【调用 skill_execute 记录邮件】
python scripts/customer_manager.py record-email ...
         ↓
任务完成，总结时列出：
- 本次匹配的客户清单
- 已发送邮件的客户列表
```

---

## 输出格式

所有命令返回 JSON 格式结果：

**成功响应**：
```json
{
  "success": true,
  "data": { ... }
}
```

**错误响应**：
```json
{
  "success": false,
  "error": "错误描述",
  "debug": "详细错误信息"
}
```

---

## 错误处理

| 错误情况 | 处理方式 |
|---------|---------|
| 缺少必需参数 | 显示参数使用说明 |
| 数据库连接失败 | 返回错误信息 |
| 客户不存在 | 返回空结果或错误 |
| JSON格式错误 | 返回解析错误详情 |

---

## 测试技能

验证技能是否正常工作：

```bash
# 测试数据库连接
python scripts/customer_manager.py list-customers --user-id "test_user"

# 测试保存客户
python scripts/customer_manager.py save-customers \
  --user-id "test_user" \
  --session-id "test_session" \
  --customers '[{"company_name":"Test Corp","contact_name":"Test User","email":"test@test.com","country":"China","language":"zh","industry":"Electronics","import_category":"LED","company_size":"100","match_reason":"Test"}]'

# 查看统计
python scripts/customer_manager.py stats --user-id "test_user"
```
