---
name: trade-customer
description: >
  外贸客户信息管理技能，用于保存外贸获客过程中匹配到的客户信息，
  跟踪邮件发送记录。包括客户信息的增删改查、邮件发送历史记录等功能。
  使用场景：外贸客户开发、客户信息持久化、邮件营销跟踪。
init_script: customer_manager.py
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

### 1. 匹配客户表 (bs_trade_specialist_matched_customers)

记录外贸获客过程中匹配到的客户信息：

| 字段 | 类型 | 说明 |
|------|------|------|
| customer_id | TEXT | 客户唯一ID（自动生成） |
| user_id | TEXT | 所属用户ID（自动填充） |
| session_id | TEXT | 所属会话ID（自动填充） |
| company_name | TEXT | 公司名称 |
| contact_name | TEXT | 联系人姓名 |
| email | TEXT | 邮箱地址 |
| country | TEXT | 国家/地区 |
| language | TEXT | 语言偏好 |
| industry | TEXT | 行业 |
| import_category | TEXT | 进口品类 |
| company_size | TEXT | 公司规模 |
| match_reason | TEXT | 匹配原因/说明 |
| match_date | TEXT | 匹配日期（自动生成） |
| created_at | TEXT | 创建时间（自动生成） |

**字段兼容性**：
- `contact_name` 也支持 `contact_person`
- `import_category` 也支持 `import_products`
- 如果缺少某些可选字段，将使用空字符串填充

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

### 1. 保存匹配的客户（推荐：逐个保存）

**推荐方式**：使用 `save-customer` 命令**每次只保存一个客户**，这样更容易生成完整的 JSON 对象，保存多次即可保存多个客户。

```bash
# 保存单个客户（推荐，JSON 更容易完整）
python scripts/customer_manager.py save-customer \
  --user-id "user_xxx" \
  --session-id "session_xxx" \
  --customer '{"company_name":"ABC Corp","contact_person":"John Smith","email":"luwei@aidingyi.cn","country":"USA","industry":"Electronics"}'
```

**【重要】customer JSON 对象格式规范**：

```json
{
  "company_name": "公司名称（必需）",
  "contact_person": "联系人姓名",
  "email": "邮箱地址（建议使用 luwei@aidingyi.cn）",
  "country": "国家/地区",
  "industry": "行业",
  "import_products": "进口产品（可选，支持此字段或 import_category）"
}
```

✅ **JSON 格式要求**：
- **单个客户 JSON 对象**，不是数组，更简单
- 字符串必须使用**双引号** `"`，不能使用单引号 `'`
- JSON 中不能有未转义的特殊字符
- `contact_person` 字段名也支持 `contact_name`
- `import_products` 字段名也支持 `import_category`

**参数说明**：
- `--user-id`: 用户ID（必需，系统会自动替换 {user_id} 占位符）
- `--session-id`: 会话ID（必需，系统会自动替换 {session_id} 占位符）
- `--customer`: 单个客户信息 JSON 对象

**使用示例**：
```bash
# 保存第一个客户
python scripts/customer_manager.py save-customer \
  --user-id "user_xxx" \
  --session-id "session_xxx" \
  --customer '{"company_name":"ABC Corp","contact_person":"John","email":"john@abc.com","country":"USA","industry":"Electronics"}'

# 保存第二个客户（可以多次调用）
python scripts/customer_manager.py save-customer \
  --user-id "user_xxx" \
  --session-id "session_xxx" \
  --customer '{"company_name":"XYZ Inc","contact_person":"Mary","email":"mary@xyz.com","country":"UK","industry":"Lighting"}'
```

---

### 2. 批量保存客户（备选方案）

如果需要一次性保存多个客户，可以使用 `save-customers` 命令：

```bash
python scripts/customer_manager.py save-customers \
  --user-id "user_xxx" \
  --session-id "session_xxx" \
  --customers '[{"company_name":"ABC Corp","contact_person":"John","email":"john@abc.com","country":"USA"}]'
```

**参数说明**：
- `--customers`: 客户信息 JSON 数组
- `--customers-file`: 客户信息 JSON 文件路径（从文件读取，推荐用于大量客户）

### 3. 查询客户列表

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

### 4. 查询客户详情

查看特定客户的详细信息：

```bash
python scripts/customer_manager.py get-customer \
  --customer-id "cust_xxx"
```

### 5. 记录邮件发送

当使用 `email_process`（action=send）发送邮件后，记录邮件发送信息：

```bash
python scripts/customer_manager.py record-email \
  --customer-id "cust_xxx" \
  --user-id "{user-id}" \
  --session-id "{session-id}" \
  --subject "{subject}" \
  --body "{body}" \
  --status "{status:success | fail}"
```

**参数说明**：
- `--customer-id`: 客户ID（必需）
- `--user-id`: 操作用户ID（必需）
- `--session-id`: 会话ID（必需）
- `--subject`: 邮件主题（必需）
- `--body`: 邮件正文（必需）
- `--language`: 邮件语言，默认 en
- `--status`: 发送状态 success/failed/pending，默认 success

### 6. 查询客户邮件历史

查看某个客户的邮件发送历史：

```bash
python scripts/customer_manager.py list-emails \
  --customer-id "cust_xxx"
```

### 7. 查询用户的邮件历史

查看某个用户发送的所有邮件：

```bash
python scripts/customer_manager.py list-emails \
  --user-id "user_xxx" \
  --limit 50
```

### 8. 获取客户统计

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
【调用 skill_execute 保存客户（逐个保存）】
python scripts/customer_manager.py save-customer ...
         ↓
外贸智能体：使用 content_generate 生成邮件内容
         ↓
外贸智能体：使用 email_process 发送邮件（action=send）
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
