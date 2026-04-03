---
name: eas-contract-verify
description: >
  全筑EAS合同核对技能。用户上传已盖章的合同扫描件（PDF）后，Agent 使用 OCR 工具解析扫描件中的
  合同编号、甲方、乙方、合同金额及盖章信息，然后调用 EAS 合同归档 API 查询对应合同编号在 EAS 系统中
  的登记信息，自动比对两者是否一致，输出结构化的比对结果。
  使用场景：合同归档核对、合同扫描件验证、盖章合同与系统数据一致性检查。
metadata:
  version: "1.0.0"
  author: aid-work-agent
  openclaw:
    emoji: "🔍"
    requires:
      bins: [python]
---

# 全筑EAS合同核对技能

## 何时使用此技能

**使用全筑EAS合同核对用于**：
- 核对已盖章合同扫描件与 EAS 系统数据是否一致
- 验证合同编号、甲方、乙方、合同金额等关键字段
- 合同归档前的自动校验

**关键词触发**：
- 合同核对
- 合同比对
- EAS合同
- 合同验证
- 合同扫描件核对
- 盖章合同核对
- 合同归档核对
- 全筑合同

---

## 完整工作流程

```
用户上传已盖章的合同扫描件 PDF
    ↓
Agent 调用 OCR 工具（paddleocr_doc_parsing）解析扫描件
    ↓
从 OCR 结果中提取：合同编号、甲方、乙方、合同金额、盖章信息
    ↓
加载本技能 use_skill(eas-contract-verify)
    ↓
调用 EAS API 查询合同信息
python scripts/eas_contract_verify.py query --contract-code "<合同编号>"
    ↓
调用合同比对
python scripts/eas_contract_verify.py compare --contract-code "<合同编号>" --ocr-data '<OCR JSON>'
    ↓
输出比对结果，告知用户是否一致及具体差异
```

---

## 操作步骤

### 步骤 1：OCR 解析合同扫描件

用户上传 PDF 扫描件后，首先使用 PaddleOCR 工具解析文档：

```bash
python src/skills/paddleocr-doc-parsing-2.0.4/scripts/vl_caller.py --file-path "<上传文件路径>" --pretty
```

从 OCR 解析结果中，Agent 需要提取以下信息：

| 字段 | 说明 | 示例 |
|------|------|------|
| `contract_code` | 合同编号 | `XZCG-2026-0001` |
| `party_a` | 甲方名称 | `全筑控股集团有限公司` |
| `party_b` | 乙方名称 | `上海某某建筑材料有限公司` |
| `amount` | 合同金额 | `100000.00` |
| `stamp_info` | 盖章信息（可选） | `甲方公章：全筑控股集团有限公司` |

### 步骤 2：查询 EAS 合同信息

使用从 OCR 提取到的合同编号，查询 EAS 系统中的合同数据：

```bash
python scripts/eas_contract_verify.py query --contract-code "<合同编号>"
```

**返回示例（成功）**：
```json
{
  "success": true,
  "data": {
    "contract_code": "XZCG-2026-0001",
    "party_a": "全筑控股集团有限公司",
    "party_b": "上海某某建筑材料有限公司",
    "amount": "100000.00",
    "contract_name": "材料采购合同",
    "raw_data": { ... },
    "match_count": 1
  }
}
```

**返回示例（失败）**：
```json
{
  "success": false,
  "error": "EAS 查询失败: 合同编号不存在",
  "debug": "{\"success\":false,\"msg\":\"合同编号不存在\"}"
}
```

### 步骤 3：比对合同信息

将 OCR 提取的数据与 EAS 系统数据进行比对：

```bash
python scripts/eas_contract_verify.py compare --contract-code "<合同编号>" --ocr-data '{"party_a":"全筑控股集团有限公司","party_b":"上海某某建筑材料有限公司","amount":"100000.00"}'
```

**返回格式**（匹配成功）：
```json
{
  "result": "yes",
  "message": "合同信息一致",
  "details": {
    "ocr_data": { ... },
    "eas_data": { ... }
  }
}
```

**返回格式**（匹配失败）：
```json
{
  "result": "no",
  "message": "甲方不一致: 扫描件为\"全筑控股集团\", EAS为\"全筑控股集团有限公司\"; 合同金额不一致: 扫描件为\"100000.00\", EAS为\"200000.00\"",
  "details": {
    "ocr_data": { ... },
    "eas_data": { ... },
    "discrepancies": [
      "甲方不一致: ...",
      "合同金额不一致: ..."
    ]
  }
}
```

---

## 比对规则说明

### 甲方/乙方比对
- 文本标准化后进行相似度比较（去除标点、空格、统一大小写）
- 支持完全匹配和模糊匹配（相似度阈值 80%）
- 支持 A 包含 B 或 B 包含 A 的子串匹配

### 金额比对
- 支持多种金额格式：`100000`、`100,000.00`、`¥100000`、`100000元`
- 支持中文大写金额：`壹拾万元整`、`拾万圆整`
- 允许微小浮点误差（相对误差 < 1%）

### 合同编号比对
- 合同编号必须从 OCR 结果中提取，用于查询 EAS
- 编号本身不参与比对（作为查询条件）

---

## EAS API 说明

- **URL**: `https://dc.trendzone.com.cn/manage/api/eas_auto_contAttach`
- **方式**: POST
- **涵盖合同类型**: 销售收入合同、采购合同、劳务合同、其它合同
- **API 自行分析合同类型，无需传参**

### 参数说明

| 参数 | 说明 | 示例 |
|------|------|------|
| `doType` | 操作类型 | `create`(新增归档)、`list`(查询列表)、`bill`(查询单据) |
| `contract_code` | 合同编号 | `XZCG-2026-0001` |
| `file_name` | 文件名（create时必填，不含中文） | `20260326165556463044.pdf` |
| `file_desc` | 文件说明（可选，允许中文） | `材料采购合同扫描件` |

---

## 文件上传（归档功能）

如需将合同附件归档到 EAS 系统：

```bash
python scripts/eas_contract_verify.py upload --contract-code "<合同编号>" --file-path "<本地文件路径>" --file-desc "文件说明"
```

上传规则：
- 共享路径：`\\192.168.200.10\AIUpload`
- 文件按合同编号建目录存放
- 文件名自动去除中文字符

---

## 输出规范

Agent 在完成核对后，应向用户输出清晰的比对结果：

**一致时**：
```
✅ 合同核对通过

合同编号：XZCG-2026-0001
比对结果：一致
- 甲方：全筑控股集团有限公司 ✓
- 乙方：上海某某建筑材料有限公司 ✓
- 合同金额：100,000.00 元 ✓
```

**不一致时**：
```
❌ 合同核对不通过

合同编号：XZCG-2026-0001
比对结果：不一致，发现以下差异：

1. 甲方不一致
   - 扫描件：全筑控股集团
   - EAS系统：全筑控股集团有限公司

2. 合同金额不一致
   - 扫描件：100,000.00 元
   - EAS系统：200,000.00 元
```

同时输出 JSON 格式的比对结果：
```json
{"result": "no", "message": "甲方不一致; 合同金额不一致"}
```

---

## 错误处理

| 错误情况 | 处理方式 |
|---------|---------|
| OCR 未能提取合同编号 | 提示用户提供合同编号 |
| OCR 提取的合同编号有误 | 提示用户确认合同编号 |
| EAS 查询失败 | 显示错误信息，建议检查合同编号 |
| EAS 中无此合同 | 告知用户该合同未在 EAS 系统中登记 |
| 文件上传失败 | 提示检查共享路径权限 |
| 金额无法解析 | 列出具体差异，标注"金额无法比对" |

---

## 重要说明

1. **OCR 提取精度**：合同扫描件的 OCR 结果可能存在误差，Agent 应告知用户结果仅供参考
2. **模糊匹配**：公司名称比对采用模糊匹配，允许一定程度的差异
3. **盖章信息**：盖章信息作为附加参考，不参与自动比对
4. **操作确认**：上传归档操作前应向用户确认
