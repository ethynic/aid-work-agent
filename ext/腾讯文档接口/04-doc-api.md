# 04 · 在线文档 API（V3，前缀 `/openapi/doc/v3/`）

> 快照来源：https://docs.qq.com/open/document/app/openapi/v3/doc/ 、.../v3/doc/get/get.html
> 抓取日期：2026-10-20
> 域名：`https://docs.qq.com`
> 这是**读取正文内容**的主接口所在。

---

## 1. 接口索引

| 分类 | 接口 | 方法 | 路径 |
| --- | --- | --- | --- |
| 内容获取 | 获取 doc 文档内容 | GET | `/openapi/doc/v3/{fileId}` |
| 批量更新内容 | 批量更新文档内容 | POST | `/openapi/doc/v3/{fileId}:batchUpdate` |

---

## 2. 获取 doc 文档内容

```
GET https://docs.qq.com/openapi/doc/v3/{fileId}
Accept: application/json
```

**权限（任一即可）**

- `scope.doc`
- `scope.doc.readonly`

**频控**：fileID 150 次/分钟

### 2.1 请求参数

**路径参数**

| 参数名 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| `fileId` | string | 是 | 文档唯一标识（形如 `300000000$AAAAAAAAAAAA`） |

**查询参数**：无
**请求体**：无

### 2.2 响应体

| 参数名 | 类型 | 描述 |
| --- | --- | --- |
| `document` | object | 文档内容，结构见下 |
| `version` | integer | 文档版本（可用于增量判断/缓存失效） |

### 2.3 请求示例

```shell
curl --location --request GET 'https://docs.qq.com/openapi/doc/v3/300000000$AAAAAAAAAAAA' \
--header 'Access-Token: ACCESS_TOKEN' \
--header 'Client-Id: CLIENT_ID' \
--header 'Open-Id: OPEN_ID'
```

### 2.4 响应示例（**结构树，注意深度**）

```json
{
    "document": {
        "begin": 0,
        "end": 14,
        "type": "Document",
        "children": [
            {
                "begin": 0,
                "end": 8,
                "type": "MainStory",
                "children": [
                    {
                        "begin": 0,
                        "end": 7,
                        "type": "Section",
                        "children": [
                            {
                                "begin": 0,
                                "end": 1,
                                "type": "Paragraph",
                                "children": [],
                                "text": "",
                                "property": { "paragraphProperty": { "...": "..." } },
                                "meta": null
                            },
                            {
                                "begin": 1,
                                "end": 6,
                                "type": "Paragraph",
                                "children": [
                                    {
                                        "begin": 1,
                                        "end": 5,
                                        "type": "Run",
                                        "children": [
                                            {
                                                "begin": 1,
                                                "end": 5,
                                                "type": "Text",
                                                "children": [],
                                                "text": "test",
                                                "property": { "...": "..." },
                                                "meta": null
                                            }
                                        ],
                                        "text": "",
                                        "property": { "runProperty": { "font": "", "bold": false, "...": "..." } },
                                        "meta": null
                                    }
                                ],
                                "text": "",
                                "property": { "...": "..." },
                                "meta": null
                            }
                        ],
                        "text": "",
                        "property": { "sectionProperty": { "pageSize": { "width": 793.67, "height": 1122.53 } } },
                        "meta": null
                    }
                ],
                "text": "",
                "property": { "...": "..." },
                "meta": null
            }
        ],
        "text": "",
        "property": { "...": "..." },
        "meta": null
    },
    "version": 4
}
```

（上例为精简版，实际响应中每个节点的 `property` 都完整展开，包含
`sectionProperty` / `paragraphProperty` / `runProperty` / `tableProperty` / `tableRowProperty` /
`tableCellProperty` / `textBoxProperty` / `drawingProperty` / `commentStoryProperty` /
`makeupProperty` / `footnoteProperty` / `endnoteProperty` / `footnoteOrEndnoteReferenceProperty` /
`headerProperty` / `footerProperty` 共 15 个字段，绝大多数为 `null`。）

### 2.5 文档树层级与节点类型

```
Document
└── MainStory
    └── Section            （节，带 pageSize / pageMargins）
        ├── Paragraph      （段落）
        │   └── Run        （文本段）
        │       └── Text   （叶子节点，text 字段才是真正的文字）
        └── Table / TableRow / TableCell ...（表格类节点）
```

**关键规律**

- 只有 `type == "Text"` 的叶子节点，`text` 字段才有实际文字内容
- 上层节点（`Document` / `MainStory` / `Section` / `Paragraph` / `Run`）的 `text` 通常是 `""`
- `begin` / `end` 是文档内的字符区间（增量更新时用于定位）
- `version` 随文档编辑递增，可做缓存 key

### 2.6 递归提取纯文本的参考实现

```python
def extract_text(node) -> str:
    """从 /openapi/doc/v3/{fileId} 返回的 document 树中提取纯文本"""
    buf = []
    def walk(n):
        t = n.get("type")
        if t == "Text":
            buf.append(n.get("text") or "")
        elif t == "Paragraph":
            # 段落内先递归收集文字
            inner = []
            stack = [n]
            # 用单独的子遍历避免重复加换行
            def collect(x):
                if x.get("type") == "Text":
                    inner.append(x.get("text") or "")
                for c in x.get("children") or []:
                    collect(c)
            collect(n)
            buf.append("".join(inner))
            buf.append("\n")
        else:
            for c in n.get("children") or []:
                walk(c)
    walk(node)
    return "".join(buf)
```

> 💡 更稳妥的做法：写一个通用递归，遇到 `type == "Text"` 就累加 `text`，
> 遇到 `type == "Paragraph"`（或有 `paragraphProperty`）就在其后补一个 `\n`，
> 遇到 `TableRow` 补 `\n`、`TableCell` 补 `\t`。然后再转成 Markdown。

### 2.7 错误码

| HTTP 状态码 | 业务错误码 | 描述 | 排查建议 |
| --- | --- | --- | --- |
| 400 | 400001 | 请求参数错误 | 确认参数是否合法 |
| 429 | 400007 | 请求数超过限制 | 降低调用频率（fileID 150 次/分钟） |
| 500 | 400010 | 服务内部错误 | 服务端错误 |

另外鉴权失败类见 02 文档（10301 / 10302 / 10303 / 37019 等）。

---

## 3. 批量更新文档内容

```
POST https://docs.qq.com/openapi/doc/v3/{fileId}:batchUpdate
```

用于写入 / 编辑文档内容（本快照聚焦"读取"，如需写入请查阅官方原文）：
https://docs.qq.com/open/document/app/openapi/v3/doc/batchupdate/update.html

---

## 4. 与「导出」方案的取舍

| 维度 | V3 结构化读取 | V2 异步导出 |
| --- | --- | --- |
| 接口 | `GET /openapi/doc/v3/{fileId}` | `POST /openapi/drive/v2/files/{fileID}/async-export` |
| 配额 | fileID 150 次/分钟 | **每用户每天 9 次** |
| 时效 | 实时 | 异步，operationID 有效期 20 分钟 |
| 输出 | JSON 属性树，需自己解析 | docx / pdf 文件，现成解析器可用 |
| 保真 | 需自己处理样式/图片/表格 | 高（官方渲染） |
| 适用 | **高频同步正文、做 RAG/索引** | 低频归档、生成 PDF、保真导出 |

**推荐**：主链路用 V3 读取；导出只用于租户手动触发的归档动作，并对每个租户做每日计数限流。
