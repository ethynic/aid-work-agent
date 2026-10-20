# 腾讯文档知识库导入接入计划（Phase 1）

> 创建：2026-10-20。前置调研：`ext/腾讯文档接口.txt`；官方文档快照（6 文件，2026-10-20 抓取）：`ext/腾讯文档接口/`（README 含关键结论、调用链骨架、已知文档矛盾表）。

## 1. 背景与目标

产品反馈：大量客户需要把既有文档系统（腾讯文档、飞书文档、语雀等）中的文档内容导入本系统知识库。经调研（2026-10-20），按受众面排序：腾讯文档 > 飞书文档 > 金山文档 > 钉钉文档 > 语雀 > 石墨文档。腾讯文档自 2025-07 起开放平台支持个人开发者接入，接入难度从「难」降级为「中易」，定为 Phase 1 首个接入对象。

目标：租户用户授权自己的腾讯文档账号后，系统批量拉取所选在线文档内容，走既有知识库解析 -> 分块 -> 向量化管线入库。

## 2. 关键技术事实（已对照官方文档快照核实）

| 维度 | 现状 |
|------|------|
| 开放平台 | https://docs.qq.com/open ，仅 OAuth 2.0 授权码模式（无应用级 client_credentials），个人开发者可接入；**应用创建需审核通过才发 Client ID/Secret** |
| 凭据三元组 | 请求头 `Access-Token` + `Client-Id` + `Open-Id`（Open-Id 在 token 响应的 `user_id` 字段返回） |
| Token 有效期 | **文档自相矛盾**（文字 30 天 vs 示例 `expires_in=259200`=3 天）——必须以响应 `expires_in` 为准、提前 1 天刷新；Refresh Token 1 年（刷新不换新，沿用旧的），过期须重新授权 |
| 内容读取（主路径） | `GET /openapi/doc/v3/{fileId}` 返回 Document→MainStory→Section→Paragraph→Run→Text 属性树，150 次/分钟，仅 `type=="Text"` 叶子节点有文字，需自写递归转 Markdown |
| 内容导出（兜底） | `POST /openapi/drive/v2/files/{fileID}/async-export` **仅支持导出 PDF**（doc/slide→pdf），配额**每 openID 每天 9 次**（多租户隔离），operationID 20 分钟有效 |
| 文件管理 | drive v2：文件列表/元信息/关键字搜索/ID 转换（`encodedID`→`fileID` 走 `util/converter`）；快捷方式文档操作须传 `shortcutID` |
| scope | 固定传 `all`，实际权限以审核批复为准：读文档需 `scope.doc(.readonly)`、列目录 `scope.drive.readonly`、导出 `scope.drive.exportable` |
| 频控 | 单文件 150 次/分、单用户 300 次/分、导入 60 次/分；2025-07 起每应用免费 2 万次调用 |
| 回调 | `redirect_uri` 后台白名单 + **仅 HTTPS**；多租户用统一回调域名 + `state` 带租户标识 |
| 身份坑 | 用户换绑账号时 `Open ID` 会变 → DB 以 `(client_id, open_id)` 组合，保留 unionID 兜底 |
| 域名区分 | 腾讯文档（docs.qq.com）与企业微信文档（doc.weixin.qq.com）两套体系，API 不通用 |

## 3. 总体设计

```
租户用户（知识库页）
  -> 授权腾讯文档账号（OAuth 授权码，跳转 authorize -> code 换 token）
  -> 系统加密存储 token（按 tenant_id + user_id 维度，(client_id, open_id) 组合键）
  -> 选择文档（drive 文件列表/关键字搜索枚举，多选）
  -> 创建导入任务（复用 runner 异步任务模式，限速频控）
  -> 逐篇拉取：doc v3 结构化读取 -> 自写递归转换器（属性树 -> Markdown）
       低频兜底：async-export 导出 PDF（每用户 9 次/天）走既有 PDF 解析
  -> KnowledgeService 入库 / 向量化
```

### 3.1 多租户授权模型

- 每个租户用户独立走 OAuth 授权，token 按 `(tenant_id, user_id)` 隔离存储，敏感字段加密（安全原则：不以明文存储/返回）。
- Access Token 过期自动用 Refresh Token 刷新；Refresh Token 失效标记授权失效，引导用户重新授权。
- Client Secret 仅存服务端（configs/env），不下发前端。

### 3.2 导入任务

- 批量导入走异步任务队列 + 限速（避免瞬间打满 20000 次配额），复用现有 runner 任务/后台任务基础设施。
- 任务状态（排队/拉取中/成功/失败）落库，前端可见；单篇失败不阻塞整批。

### 3.3 通用导入兜底（配套，低成本）

不依赖任何平台 API：用户从任意文档系统导出 docx/pdf/md 后批量上传（知识库已支持）。Phase 1 交付时在 UI 上与「腾讯文档导入」并列露出，覆盖长尾文档系统。

## 4. Phase 1 范围

**做**：
- OAuth 授权流（授权、回调、token 刷新、失效处理）
- 腾讯文档文件列表浏览与多选（Word 类在线文档）
- DOCX 导出拉取 + 既有知识库解析管线入库
- 导入任务队列、状态展示、失败重试
- 通用批量上传兜底入口

**不做（后续 Phase）**：
- 在线表格 / 智能表格 / 幻灯片（格式差异大，先聚焦 Word 类）
- 定时增量同步（文档变更自动更新知识库）
- 飞书 / 钉钉等其他平台（复用本期沉淀的 OAuth + 导入任务框架）

## 5. 任务分解

| # | 任务 | 说明 |
|---|------|------|
| 1 | 开发者账号与应用创建 | 见 §7；**需平台审核**，尽早启动；拿到 Client ID/Secret + 配置 redirect_uri 白名单 |
| 2 | OAuth 服务端实现 | authorize 跳转（`new_login=1`）、回调、code 换 token（code 5 分钟一次性）、refresh（提前 1 天按 `expires_in` 刷新）、加密存储、37017/37019 失效处理 |
| 3 | drive 文件列表适配 | 文件枚举 + 元信息 + 关键字搜索 + `encodedID`→`fileID` converter + `shortcutID` 处理，分页 |
| 4 | doc v3 树转 Markdown 转换器 | 递归遍历 Document/Paragraph/Run/Text 属性树，Paragraph 边界补 `\n`；样式/表格/图片映射策略实现时定 |
| 5 | 导入任务框架 | 异步队列 + 频控限速（单用户 300 次/分、单文件 150 次/分）+ 状态落库 + 前端任务页 |
| 6 | PDF 导出兜底（可选后置） | async-export + progress 轮询（operationID 20 分钟）+ 每 openID 9 次/天配额闸门，走既有 PDF 解析 |
| 7 | 知识库前端入口 | 知识库页新增「从腾讯文档导入」+ 授权状态管理（含重新授权引导） |
| 8 | 测试与真机验收 | 单测（OAuth 刷新/树转换器/频控 mock）+ 测试环境真实账号联调 |

## 6. 官方文档资料（已备齐）

搜索智能体已于 2026-10-20 完成 6 文件快照：`ext/腾讯文档接口/`（01 总览与 OAuth / 02 通用规范与错误码 / 03 文件管理 / 04 doc v3 / 05 表格类 / 06 开源项目）。9 项清单全覆盖，本文档 §2/§5 已按快照修正。快照内文档自相矛盾处（code 有效期、access_token 有效期）以实测为准，见快照 README 第四节。

## 7. 测试账号开通（问题解答）

**建议**：Phase 1 开发测试用**个人名义**即可（个人开发者可直接接入）；正式生产前用**公司名义**重新创建应用（应用主体影响配额上限与用户授权时的信任展示）。

开通步骤：
1. 用 QQ/微信登录 https://docs.qq.com/open （腾讯文档开放平台），开发者中心完成注册
2. 创建第三方应用，填写应用名称与回调域名（**仅 HTTPS + 白名单**）-> 提交**审核**（通过后才发 `Client ID` / `Client Secret`，周期未知，尽早启动）
3. 确认审核批复的 scope 覆盖：`scope.doc.readonly`（读内容）、`scope.drive.readonly`（列目录）、`scope.drive.file.metadata`（ID 转换）、`scope.drive.exportable`（PDF 导出兜底）、`scope.user.info.base`
4. 准备一个普通腾讯文档账号放测试文档（在线文档为主，表格/智能表各 1-2 篇验证 scope 拒绝行为），含一篇他人共享的快捷方式文档（验证 `shortcutID` 分支）

**测试环境回调**：redirect_uri 仅支持 HTTPS，测试环境需 HTTPS 域名或内网穿透方案；多租户统一回调域名 + `state` 回带标识。

## 8. 决策点与风险

| # | 决策点 | 当前倾向 |
|---|--------|---------|
| 1 | 应用主体 | 测试个人名义，生产公司名义 |
| 2 | 内容拉取主路径 | **doc v3 结构化读取 -> 自写树转 Markdown 转换器**（150 次/分宽裕）；PDF 导出仅低频兜底（9 次/天/用户） |
| 3 | 表格类文档 | Phase 1 不做，UI 明确提示暂不支持 |
| 4 | 导入去重语义 | 同一文档重复导入是「覆盖更新」还是「新建副本」，待产品定 |
| 5 | 配额管理 | 按租户统计 API 调用次数（应用级 2 万次 + 频控），超限排队；是否需要租户级配额告警待定 |
| 6 | 树转 Markdown 保真度 | 纯文本 + 标题层级保真即可 vs 尽量保留表格/图片（工作量差异大），实现时按快照 §04 节点类型评估 |
