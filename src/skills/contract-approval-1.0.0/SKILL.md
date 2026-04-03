---
name: contract-approval
description: >
  合同审批流程自动化技能，使用 Playwright 自动登录 OA 系统，在待办列表中搜索合同编号，
  打开审批单据并执行通过或驳回操作。
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
- 登录 OA 系统处理合同审批
- 在待办列表中搜索特定合同编号
- 打开审批单据查看详情
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

## 环境配置

### 必需环境变量

在 skill 目录下的 `.env` 文件中配置（不要提交到 git）：

```
CONTRACT_OA_URL=http://f.trendzone.com.cn:8089/portal/main.jsp
CONTRACT_OA_USERNAME=你的用户名
CONTRACT_OA_PASSWORD=你的密码
```

### 参数说明

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `contract_no` | string | 是 | 合同编号，用于在待办中搜索 |
| `approve` | boolean | 是（二选一） | 通过审批 |
| `reject` | boolean | 是（二选一） | 驳回审批 |
| `comment` | string | 否 | 审批意见/备注 |
| `headless` | boolean | 否 | 是否无头模式运行，默认 false（显示浏览器窗口） |

---

## 如何使用此技能

### 完整工作流程

```
用户：请帮我审批通过合同 HT-2024-001234
         ↓
加载技能 use_skill(contract-approval)
         ↓
【调用 skill_execute 执行完整审批流程】
python scripts/contract_approval.py --contract-no "HT-2024-001234" --approve --comment "同意"
         ↓
自动完成：登录 → 搜索合同 → 打开单据 → 审批通过 → 关闭浏览器
         ↓
调用 skill_complete 标记完成
```

### 通过审批

```bash
python scripts/contract_approval.py --contract-no "HT-2024-001234" --approve --comment "同意"
```

### 驳回审批

```bash
python scripts/contract_approval.py --contract-no "HT-2024-001234" --reject --comment "请修改合同金额"
```

脚本会自动执行完整流程：**登录 → 搜索合同 → 打开审批单据 → 执行审批 → 关闭浏览器**

---

## 输出格式

所有命令返回 JSON 格式结果：

**成功响应**：
```json
{
  "success": true,
  "action": "login",
  "message": "登录成功",
  "data": { ... }
}
```

**错误响应**：
```json
{
  "success": false,
  "error": "登录失败：用户名或密码错误",
  "debug": "详细错误信息"
}
```

---

## 错误处理

| 错误情况 | 处理方式 |
|---------|---------|
| 环境变量未配置 | 提示用户在 .env 文件中配置 |
| 登录失败 | 检查用户名密码，提示重试 |
| 合同未找到 | 提示用户检查合同编号 |
| 页面元素未找到 | 截图并提示可能的页面变化 |
| 网络超时 | 提示检查网络连接 |

---

## 重要说明

1. **完整流程**：脚本自动执行 登录→搜索→打开单据→审批→关闭，无需分步操作
2. **安全要求**：用户名密码保存在 `.env` 文件中，**不**提交到 git
3. **操作确认**：执行审批操作前应向用户确认
4. **元素定位**：使用 CSS 选择器或 XPath 定位页面元素，页面结构变化时需要更新定位策略

---

## 测试技能

验证技能是否正常工作：

```bash
# 通过审批（完整流程）
python scripts/contract_approval.py --contract-no "TEST-001" --approve

# 驳回审批（完整流程）
python scripts/contract_approval.py --contract-no "TEST-001" --reject --comment "测试驳回"
```
