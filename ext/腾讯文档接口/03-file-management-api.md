# 03 · 文件管理 API（V2，前缀 `/openapi/drive/v2/`）

> 快照来源：https://docs.qq.com/open/document/app/openapi/v2/file/ 及其子页
> 抓取日期：2026-10-20
> 鉴权：Header 三元组 `Access-Token` / `Client-Id` / `Open-Id`（见 02 文档）
> 所有路径相对 `https://docs.qq.com`

---

## 1. 接口索引

### 1.1 文件操作

| 接口 | 方法 | 路径 |
| --- | --- | --- |
| 新建文档 | POST | `/openapi/drive/v2/files` |
| 查询文档 | GET | `/openapi/drive/v2/files/{fileID}/metadata` |
| 重命名文档 | PATCH | `/openapi/drive/v2/files/{fileID}` |
| 移动文档 | PATCH | `/openapi/drive/v2/files/{fileID}/move` |
| 删除文档 | DELETE | `/openapi/drive/v2/files/{fileID}` |
| 生成副本 | POST | `/openapi/drive/v2/files/{fileID}/copy` |
| 收藏文档 | PATCH | `/openapi/drive/v2/files/{fileID}/star` |
| 置顶文档 | PATCH | `/openapi/drive/v2/files/{fileID}/pin` |
| 设置水印 | PATCH | `/openapi/drive/v2/files/{fileID}/watermark` |
| 创建快捷方式 | POST | `/openapi/drive/v2/files/{fileID}/shortcut` |
| 恢复文档 | PATCH | `/openapi/drive/v2/files/{fileID}/recover` |

### 1.2 导入文件

| 接口 | 方法 | 路径 |
| --- | --- | --- |
| 预导入文档 | POST | `/openapi/drive/v2/files/upload-url` |
| 异步导入文档 | POST | `/openapi/drive/v2/files/async-import` |
| 查询导入进度 | GET | `/openapi/drive/v2/files/import-progress` |

### 1.3 导出文件

| 接口 | 方法 | 路径 |
| --- | --- | --- |
| 导出文档 | POST | `/openapi/drive/v2/files/{fileID}/async-export` |
| 导出进度查询 | GET | `/openapi/drive/v2/files/{fileID}/export-progress` |

### 1.4 文件夹操作

| 接口 | 方法 | 路径 |
| --- | --- | --- |
| 获取文档列表 | GET | `/openapi/drive/v2/folders/{folderID}` |
| 查询文件夹信息 | GET | `/openapi/drive/v2/folders/{folderID}/metadata` |
| 添加文件夹 | POST | `/openapi/drive/v2/folders` |
| 删除文件夹 | DELETE | `/openapi/drive/v2/folders/{folderID}` |
| 查询文件夹权限 | GET | `/openapi/drive/v2/folders/{folderID}/permission` |
| 移动文件夹 | POST | `/openapi/drive/v2/folders/{folderID}/move` |
| 重命名文件夹 | PATCH | `/openapi/drive/v2/folders/{folderID}` |

### 1.5 文档权限

| 接口 | 方法 | 路径 |
| --- | --- | --- |
| 查询用户访问权限 | GET | `/openapi/drive/v2/files/{fileID}/access` |
| 转让文档所有权 | PATCH | `/openapi/drive/v2/files/{fileID}/ownership` |
| 查看文档权限 | GET | `/openapi/drive/v2/files/{fileID}/permission` |
| 设置文档权限 | PATCH | `/openapi/drive/v2/files/{fileID}/permission` |
| 申请文档权限 | POST | `/openapi/drive/v2/files/{fileID}/permission/apply` |
| 添加协作成员 | PATCH | `/openapi/drive/v2/files/{fileID}/collaborators` |
| 移除协作成员 | DELETE | `/openapi/drive/v2/files/{fileID}/collaborators` |
| 查询协作成员 | GET | `/openapi/drive/v2/files/{fileID}/collaborators` |

### 1.6 搜索

| 接口 | 方法 | 路径 |
| --- | --- | --- |
| 列表过滤 | GET | `/openapi/drive/v2/filter` |
| 关键字搜索 | GET | `/openapi/drive/v2/search` |

### 1.7 功能接口 / 通知

| 接口 | 方法 | 路径 |
| --- | --- | --- |
| fileID 转换 | GET | `/openapi/drive/v2/util/converter` |
| 上传图片 | POST | `/openapi/resources/v2/images` |
| 获取应用 OpenAPI 使用详情 | GET | `/openapi/drive/v2/util/resource-use` |
| 查询消息列表未读数量 | GET | `/openapi/drive/v2/notification/unread-count` |

---

## 2. 重点接口详情

### 2.1 获取文档列表 `GET /openapi/drive/v2/folders/{folderID}`

**用途**：拉取目录下的文件与文件夹列表（**SaaS 里"选文档"界面的基础**）
**权限**：`scope.drive.readonly`
**频控**：openID 300 次/分钟

**路径参数**

| 参数名 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| `folderID` | string | 否 | 文件夹唯一标识，不填默认为「我的文档」目录 |

**查询参数**

| 参数名 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| `sortType` | string | 否 | 排序类型，默认 `browse` |
| `asc` | integer | 否 | `1` 正序；`0` 倒序（默认） |
| `start` | integer | 否 | 开始值，默认 `0` |
| `limit` | integer | 否 | 拉取条目，默认 `20` |

**响应体**

| 参数名 | 类型 | 描述 |
| --- | --- | --- |
| `ret` | integer | 业务返回码 |
| `msg` | string | 描述 |
| `data` | json object | 列表数据，字段见下方示例 |

**示例**

```shell
curl --location --request GET 'https://docs.qq.com/openapi/drive/v2/folders/FOLDER_ID?sortType=browse&asc=0&start=0&limit=20' \
--header 'Access-Token: ACCESS_TOKEN' \
--header 'Client-Id: CLIENT_ID' \
--header 'Open-Id: OPEN_ID'
```

```python
{'ret': 0, 'msg': 'Succeed', 'data': {
  'next': 20,
  'list': [
    {'ID': 'FOLDER_ID1', 'title': 'Hi，欢迎使用腾讯文档', 'type': 'folder',
     'url': 'https://docs.qq.com/desktop/mydoc/folder/FOLDER_ID1', 'status': 'normal',
     'isCreator': True, 'createTime': 1587134924, 'creatorName': '张三',
     'isOwner': True, 'ownerName': '张三', 'lastModifyTime': 1597200449, 'lastBrowseTime': 1603358078},
    {'ID': '300000000$AAAAAAAAAAAA', 'title': 'file', 'type': 'doc',
     'url': 'https://docs.qq.com/doc/DAAAAAAAAAAAAAAAA', 'status': 'normal',
     'isCreator': True, 'createTime': 1598499361, 'creatorName': '张三',
     'isOwner': True, 'ownerName': '张三', 'lastModifyTime': 1599538733,
     'lastBrowseTime': 1602837243, 'starred': False, 'pinned': False}
  ]}}
```

**列表项关键字段**（`type` 常见值：`folder` / `doc` / `sheet` / `slide` / `pdf` / `smartcanvas`）

- `ID`：文件或文件夹 ID（doc 场景即 `fileID`）
- `title`：标题
- `type`：类型
- `url`：访问链接（从 url 可解析出 `encodedID`）
- `isOwner` / `ownerName`：是否所有者
- `starred` / `pinned`：收藏 / 置顶
- `shortcutID`：**非空说明是快捷方式文档**，内容操作要传它
- `next`：下一页 `start` 值（翻页用）

---

### 2.2 导出文档 `POST /openapi/drive/v2/files/{fileID}/async-export`

**用途**：异步导出文档（**获取"整篇文档内容"最省事的方案，但配额极紧**）
**权限**：`scope.drive.exportable`
**频控**：**每用户每天仅 9 次**

**流程**：调用本接口 → 拿 `operationID` → 20 分钟内用 `operationID` 查导出进度 → 拿下载链接

**支持的导出类型**

| 文档类型 | 可导出为 |
| --- | --- |
| doc | doc, pdf |
| sheet | sheet |
| slide | slide, pdf |
| pdf | pdf |
| smartcanvas | doc, pdf |

**请求体**（`Content-Type: application/x-www-form-urlencoded`）

| 参数名 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| `exportType` | string | 否 | 目前仅支持 doc、slide 转 `pdf`。不填 = 不转换，导出原类型 |

**响应体**

| 参数名 | 类型 | 描述 |
| --- | --- | --- |
| `ret` | integer | 业务返回码 |
| `msg` | string | 描述 |
| `data.operationID` | string | 操作 ID |

**示例**

```bash
curl --location --request POST 'https://docs.qq.com/openapi/drive/v2/files/300000000$AAAAAAAAAAAA/async-export' \
--header 'Access-Token: ACCESS_TOKEN' \
--header 'Client-Id: CLIENT_ID' \
--header 'Open-Id: OPEN_ID' \
--header 'Content-Type: application/x-www-form-urlencoded' \
--data-urlencode 'exportType=pdf'
```

```
{'ret': 0, 'msg': 'Succeed', 'data': {'operationID': 'OPERATION_ID'}}
```

> ⚠️ `operationID` 有效期 **20 分钟**，超时需重新发起导出（会再消耗 1 次配额）。

### 2.3 导出进度查询 `GET /openapi/drive/v2/files/{fileID}/export-progress`

用于轮询导出结果，拿到最终文件下载地址。
（详见 https://docs.qq.com/open/document/app/openapi/v2/file/export/export_progress.html ）

---

## 3. 导入链路（如果你想让租户把本地 Office 文件传上来）

```
① POST /openapi/drive/v2/files/upload-url      预导入，拿上传凭证
② 把文件上传到 ① 返回的 URL
③ POST /openapi/drive/v2/files/async-import     异步导入
④ GET  /openapi/drive/v2/files/import-progress  轮询进度 → 拿 fileID
```

**频控**：导入接口 openID 60 次/分钟（比普通文件操作严格得多）

**相关错误码**：`10106` 操作太频繁、`10107` 文件过大、`10108` 格式不支持（需先转 .docx）、
`10109` 文件已损坏、`10111` 文件是加密的

---

## 4. 权限相关（多租户场景的边界）

如果你只是**读取**租户文档内容，理论上只需要只读 scope。
但要注意：

- 租户共享给你的文档是**快捷方式文档**（`shortcutID` 非空），操作时传 `shortcutID`
- 用 `GET /openapi/drive/v2/files/{fileID}/access` 可以先探测当前用户对该文档的实际访问权限，避免直接调内容接口报 403
- 权限不足相关错误码：`10007`（操作权限不足）、`10011`（用户无操作权限）、`403/400005`

---

## 5. 其他值得留意的接口

| 接口 | 用途 |
| --- | --- |
| `GET /openapi/drive/v2/util/resource-use` | **获取应用 OpenAPI 使用详情** —— 用来监控 2 万次免费额度还剩多少，SaaS 建议做成定时巡检 |
| `GET /openapi/drive/v2/search` | 关键字搜索文档（租户文档多时比遍历目录高效） |
| `GET /openapi/drive/v2/filter` | 列表过滤 |
| `POST /openapi/resources/v2/images` | 上传图片（写入场景用） |
