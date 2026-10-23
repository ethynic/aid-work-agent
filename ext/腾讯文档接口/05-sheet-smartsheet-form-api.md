# 05 · 在线表格 / 智能表 / 收集表 API（V2）

> 快照来源：
> - 在线表格：https://docs.qq.com/open/document/app/openapi/v2/sheet/
> - 在线智能表：https://docs.qq.com/open/document/app/openapi/v2/smartsheet/
> - 在线收集表：https://docs.qq.com/open/document/app/openapi/v2/form/
> 抓取日期：2026-10-20 · 域名 `https://docs.qq.com`

---

## 1. 在线表格 API（`/openapi/sheetbook/v2/`）

### 1.1 表格接口

| 接口 | 方法 | 路径 |
| --- | --- | --- |
| 查询子表 | GET | `/openapi/sheetbook/v2/{bookID}/sheets-info` |
| 添加子表 | POST | `/openapi/sheetbook/v2/{bookID}:batchUpdate` |
| 删除多行（列） | POST | `/openapi/sheetbook/v2/{bookID}:batchUpdate` |

### 1.2 表格内容接口

| 接口 | 方法 | 路径 |
| --- | --- | --- |
| 清空区域内容 | POST | `/openapi/sheetbook/v2/{bookID}/values/{range}:clear` |
| 更新区域内容 | PUT | `/openapi/sheetbook/v2/{bookID}/values/{range}` |
| 批量插入图片 | POST | `/openapi/sheetbook/v2/{bookID}:batchUpdate` |

> 注：官方索引中**未列出独立的"读取区域内容"接口**（只有更新/清空）。
> 读取表格内容的 Practical 做法通常是：
> ① 走 V2 导出（`async-export`）拿 .xlsx；
> ② 或让租户把表格转成**智能表**，用智能表「查询记录」接口读。
> 落地前建议实测确认。

---

## 2. 在线智能表 API（`/openapi/smartbook/v2/`）

在「视图 / 子表 / 记录 / 字段」四种资源上提供增删改查。
基本概念见：https://docs.qq.com/open/document/app/openapi/v2/smartsheet/common/concept.html

### 2.1 子表接口

| 接口 | 方法 | 路径 |
| --- | --- | --- |
| 添加子表 | POST | `/openapi/smartbook/v2/files/{fileID}/sheets` |
| 删除子表 | POST | `/openapi/smartbook/v2/files/{fileID}/sheets` |
| 查询子表 | GET | `/openapi/smartbook/v2/files/{fileID}/sheets` |

### 2.2 视图接口

| 接口 | 方法 | 路径 |
| --- | --- | --- |
| 添加视图 | POST | `/openapi/smartbook/v2/files/{fileID}/sheets/{sheetID}` |
| 删除视图 | POST | `/openapi/smartbook/v2/files/{fileID}/sheets/{sheetID}` |
| 查询视图 | POST | `/openapi/smartbook/v2/files/{fileID}/sheets/{sheetID}` |

### 2.3 记录接口

| 接口 | 方法 | 路径 |
| --- | --- | --- |
| 添加记录 | POST | `/openapi/smartbook/v2/files/{fileID}/sheets/{sheetID}` |
| 删除记录 | POST | `/openapi/smartbook/v2/files/{fileID}/sheets/{sheetID}` |
| **查询记录** | POST | `/openapi/smartbook/v2/files/{fileID}/sheets/{sheetID}` |
| 更新记录 | POST | `/openapi/smartbook/v2/files/{fileID}/sheets/{sheetID}` |

### 2.4 字段接口

| 接口 | 方法 | 路径 |
| --- | --- | --- |
| 添加字段 | POST | `/openapi/smartbook/v2/files/{fileID}/sheets/{sheetID}` |
| 删除字段 | POST | `/openapi/smartbook/v2/files/{fileID}/sheets/{sheetID}` |
| 查询字段 | POST | `/openapi/smartbook/v2/files/{fileID}/sheets/{sheetID}` |
| 更新字段 | POST | `/openapi/smartbook/v2/files/{fileID}/sheets/{sheetID}` |

> 智能表是**结构化读数的首选**：「查询字段」拿列定义 + 「查询记录」拿行数据，
> 比解析 doc 属性树、比导出 xlsx 都更适合对接业务系统。
> 容量上限：4 万行 / 150 列 / 80 万单元格 / 40MB 关键帧（错误码 `22029`）。

---

## 3. 在线收集表 API（`/openapi/drive/v2/forms/`）

| 接口 | 方法 | 路径 |
| --- | --- | --- |
| 更新收集截止时间 | PUT | `/openapi/drive/v2/forms/{formID}/release` |
| 生成收集结果 | POST | `/openapi/drive/v2/forms/{formID}/result` |

> 收集表 API 能力很薄（只有 2 个接口），没有直接"读取收集结果数据"的接口。
> 若要拿收集结果，通常做法是：把收集表关联到智能表，再用智能表「查询记录」读取。

---

## 4. 品类选择建议（面向"读文档内容"的 SaaS）

| 租户文档类型 | 推荐读取方式 |
| --- | --- |
| 在线文档 doc | `GET /openapi/doc/v3/{fileId}` + 递归解析（04 文档） |
| 智能表 smartsheet | 查询字段 + 查询记录（结构化，最友好） |
| 在线表格 sheet | 导出 xlsx（有 9 次/天限制）或建议租户改用智能表 |
| 幻灯片 slide | 导出 pdf |
| 收集表 form | 关联智能表后读取 |
