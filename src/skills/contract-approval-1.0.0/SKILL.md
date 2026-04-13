---
name: contract-approval
description: >
  合同审批流程自动化技能，优先通过 EAS 审批 API 接口完成审批操作，
  API 不可用时自动回退到 Playwright 模拟网页操作。
  使用场景：合同审批、OA流程处理、审批单据处理。
metadata:
  version: "1.0.0"
  author: aid-work-agent
  openclaw:
    emoji: "📋"
    requires:
      bins: [python]
      env:
        - CONTRACT_OA_URL
        - CONTRACT_OA_USERNAME
        - CONTRACT_OA_PASSWORD
    primaryEnv: CONTRACT_OA_URL
---

# 合同审批流程自动化技能

## 何时使用此技能

**使用合同审批技能用于**：
- 查询用户待审批的合同流程
- 通过或驳回合同审批申请
- 查看审批单据信息

**关键词触发**：
- 合同审批
- 审批合同
- OA审批
- 待办处理
- 审批通过
- 审批驳回
- 合同编号审批

---

## ⚠️ 严禁事项

**绝对禁止使用浏览器工具（browser tool）去操作审批网页！**

本技能内置了精确定位页面元素的 Playwright 脚本（CSS选择器/XPath），比浏览器工具的语义点击准确得多。所有审批操作必须且只能通过 `skill_execute` 调用本脚本来完成：

```
✅ 正确：skill_execute(skill="contract-approval", command='python scripts/contract_approval.py ...')
❌ 错误：自己用 browser tool 打开网页、查找元素、点击按钮
```

---

## 环境配置

### 必需环境变量

在 skill 目录下的 `.env` 文件中配置（不要提交到 git）：

```
CONTRACT_OA_URL=http://f.trendzone.com.cn:8089/portal/main.jsp
CONTRACT_OA_USERNAME=你的用户名
CONTRACT_OA_PASSWORD=你的密码
```

### 可选环境变量

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `CONTRACT_APPROVAL_API_URL` | EAS 审批 API 地址 | `https://dc.trendzone.com.cn/manage/api/trend_eas_approval` |
| `CONTRACT_OA_HEADLESS` | 是否无头模式运行浏览器 | `false` |

### 参数说明

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `contract_no` | string | 是 | 合同编号 |
| `approve` | flag | 是（二选一） | 通过审批 |
| `reject` | flag | 是（二选一） | 驳回审批 |
| `comment` | string | 否 | 审批意见/备注 |

---

## 如何使用此技能

### 完整工作流程

```
用户：请帮我审批通过合同 HT-2024-001234
         ↓
加载技能 use_skill(contract-approval)
         ↓
【调用 skill_execute，只需一条命令】
skill_execute(
  skill="contract-approval",
  command='python scripts/contract_approval.py --contract-no "HT-2024-001234" --approve'
)
         ↓
脚本内部自动完成（API 优先）：
  1. 调用 API 查询该合同是否在当前用户待审批列表中
  2. 找到 → 通过 API 执行审批，返回结果
  3. 未找到 → 返回提示（合同不在待审批中）
  4. API 不可用（500/网络错误）→ 自动回退到 Playwright 脚本操作网页
         ↓
调用 skill_complete 标记完成
```

### 通过审批

```bash
python scripts/contract_approval.py --contract-no "HT-2024-001234" --approve --comment "同意"
```

### 驳回审批（仅当用户明确要求驳回时使用）

```bash
python scripts/contract_approval.py --contract-no "HT-2024-001234" --reject --comment "请修改合同金额"
```

### 执行策略

脚本内部采用 **API 优先、Playwright 脚本备用** 的策略，调用者无需关心回退逻辑：

| 场景 | 脚本行为 |
|------|---------|
| API 返回审批数据 | 直接通过 API 完成审批 |
| API 返回空列表 | 提示"合同不在待审批中"，结束 |
| API 不可用（500/网络错误） | 自动回退到内置 Playwright 脚本操作网页 |

---

## 输出格式

所有命令返回 JSON 格式结果：

**成功响应（API 方式）**：
```json
{
  "success": true,
  "message": "合同 HT-2024-001234 审批操作（通过）完成",
  "data": {"step": "api_check", "status": "completed", "method": "api", "assignId": "xxx"}
}
```

**合同不在待审批中**：
```json
{
  "success": false,
  "message": "合同 HT-2024-001234 不在当前用户的待审批列表中",
  "data": {"step": "api_list", "status": "not_found", "method": "api"},
  "debug": "可能原因：流程尚未到达当前审批人，或该合同已审批完毕"
}
```

**API 不可用，回退网页**：
```json
{
  "success": true,
  "message": "API 调用失败，正在回退到网页操作...",
  "data": {"step": "api_fallback", "status": "fallback", "method": "api"}
}
```

---

## 错误处理

| 错误情况 | 处理方式 |
|---------|---------|
| 环境变量未配置 | 提示用户在 .env 文件中配置 |
| API 不可用 | 脚本自动回退到 Playwright 网页操作 |
| 合同不在待审批中 | 提示用户（可能流程未到或已审批） |
| 网页登录失败 | 检查用户名密码，提示重试 |

---

## 重要说明

1. **API 优先**：脚本优先使用 API 接口，更快更稳定
2. **自动回退**：仅在 API 不可用（500/网络错误）时才回退到 Playwright 网页操作
3. **用户一致性**：API 的 userCode 和网页登录使用同一个用户名（`CONTRACT_OA_USERNAME`）
4. **必须通过脚本操作**：不要使用浏览器工具自行操作审批网页，本技能的 Playwright 脚本已精确定位所有页面元素
5. **安全要求**：用户名密码保存在 `.env` 文件中，**不**提交到 git
6. **操作确认**：执行审批操作前应向用户确认
