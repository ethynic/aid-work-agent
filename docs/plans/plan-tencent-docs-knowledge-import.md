# 腾讯文档知识库导入接入计划（Phase 1）

> 创建：2026-10-20。前置调研：`ext/腾讯文档接口.txt`；官方文档快照（6 文件，2026-10-20 抓取）：`ext/腾讯文档接口/`（README 含关键结论、调用链骨架、已知文档矛盾表）。

## 1. 背景与目标

产品反馈：大量客户需要把既有文档系统（腾讯文档、飞书文档、语雀等）中的文档内容导入本系统知识库。经调研（2026-10-20），按受众面排序：腾讯文档 > 飞书文档 > 金山文档 > 钉钉文档 > 语雀 > 石墨文档。腾讯文档自 2025-07 起开放平台支持个人开发者接入，接入难度从「难」降级为「中易」，定为 Phase 1 首个接入对象。

目标：租户用户授权自己的腾讯文档账号后，系统批量拉取所选在线文档内容，走既有知识库解析 -> 分块 -> 向量化管线入库。

## 2. 关键技术事实（已对照官方文档快照核实）

| 维度 | 现状 |
|------|------|
| 开放平台 | https://docs.qq.com/open ，C 端个人版仅 OAuth 2.0 授权码模式（无应用级 client_credentials），个人开发者可接入；**应用创建需审核通过才发 Client ID/Secret**。注：client_credentials 并非全平台缺失——企业版 SaaS（自建应用 + OneID）支持应用级 token，与本 Phase 无关（见 ext/腾讯文档接口/07） |
| 凭据三元组 | 请求头 `Access-Token` + `Client-Id` + `Open-Id`（Open-Id 在 token 响应的 `user_id` 字段返回） |
| Token 有效期 | **文档自相矛盾**（文字 30 天 vs 示例 `expires_in=259200`=3 天）——必须以响应 `expires_in` 为准、提前 1 天刷新；Refresh Token 1 年（刷新不换新，沿用旧的），过期须重新授权 |
| 内容读取（主路径） | `GET /openapi/doc/v3/{fileId}` 返回 Document→MainStory→Section→Paragraph→Run→Text 属性树，150 次/分钟，仅 `type=="Text"` 叶子节点有文字，需自写递归转 Markdown；**⚠️ 实测缺陷（2026-10-20）：文档含表格+图片后稳定返回 `400010 Unmarshal document error`**（纯文本文档正常），失败时须降级导出路径 |
| 内容导出（兜底） | `POST /openapi/drive/v2/files/{fileID}/async-export`，配额**每 openID 每天 9 次**（多租户隔离），operationID 20 分钟有效，下载 URL 1800 秒有效。**实测（2026-10-20）：不填 `exportType` 时 doc 导出为 DOCX**（快照「仅支持 doc/slide→pdf」记载不准确），DOCX 可直接复用既有解析管线；`exportType=pdf` 导出 PDF |
| 文件管理 | drive v2：文件列表/元信息/关键字搜索/ID 转换（`encodedID`→`fileID` 走 `util/converter`）；快捷方式文档操作须传 `shortcutID` |
| scope | 固定传 `all`，实际权限以审核批复为准：读文档需 `scope.doc(.readonly)`、列目录 `scope.drive.readonly`、导出 `scope.drive.exportable` |
| 频控 | 单文件 150 次/分、单用户 300 次/分、导入 60 次/分；2025-07 起每应用免费 2 万次调用；**个人开发者 Token 控制台口径为按天 2000 次**（超级会员 20000/Plus 40000，见开发者后台温馨提示） |
| 回调 | `redirect_uri` 后台白名单 + **仅 HTTPS**；多租户用统一回调域名 + `state` 带租户标识 |
| 身份坑 | 用户换绑账号时 `Open ID` 会变 → DB 以 `(client_id, open_id)` 组合，保留 unionID 兜底 |
| 域名区分 | 三套独立体系，API 不通用：腾讯文档个人版（docs.qq.com，三元组鉴权 + drive/v2 + doc/v3）、腾讯文档企业版 SaaS（saas.docs.qq.com 或企业专属域名，两元组鉴权 + OneID + drive/v3）、企业微信文档（doc.weixin.qq.com，corpid+secret）。接入前先看租户文档链接域名判断体系（ext/腾讯文档接口/07 §1） |

> **范围决策（2026-10-20，产品确认）**：现有租户的腾讯文档账号均为**个人账号**（docs.qq.com），本 Phase 仅对接个人版 C 端路径（路径 A）；企业版 SaaS（租户自建应用 + OneID + 两元组鉴权 + drive/v3）与企业微信文档体系在出现实际租户需求前**不对接**。届时按 ext/腾讯文档接口/07 §5 的建议扩展 Provider 抽象：文档内容树解析器可复用，鉴权/路径/分页/错误码需另写适配。

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
- OAuth 授权流（授权、回调、token 刷新、失效处理）——**被第三方应用入口阻塞，后置**（见 §7）
- 腾讯文档文件列表浏览与多选（Word 类在线文档）——凭个人 Token 即可开发
- doc v3 内容拉取 + 树转 Markdown 转换器 + 知识库解析管线入库
- 导入任务队列、状态展示、失败重试
- 通用批量上传兜底入口

**不做（后续 Phase）**：
- 在线表格 / 智能表格 / 幻灯片（格式差异大，先聚焦 Word 类）
- 定时增量同步（文档变更自动更新知识库）
- 飞书 / 钉钉等其他平台（复用本期沉淀的 OAuth + 导入任务框架）

## 5. 任务分解

| # | 任务 | 说明 | 状态（2026-10-20） |
|---|------|------|------|
| 1 | 开发者账号与应用创建 | 见 §7；个人开发者凭据已开通；**第三方应用（client_secret）创建入口缺失，已走「我要反馈」待官方回复** | 🔧 个人 Token 已开通，第三方应用待反馈 |
| 2 | OAuth 服务端实现 | authorize 跳转（`new_login=1`）、回调、code 换 token（code 5 分钟一次性）、refresh（提前 1 天按 `expires_in` 刷新）、加密存储、37017/37019 失效处理 | ⏸ 被任务 1 第三方应用阻塞，后置 |
| 3 | drive 文件列表适配 | 文件枚举 + 元信息 + 关键字搜索 + `encodedID`→`fileID` converter + `shortcutID` 处理，分页 | ✅ 可先行——凭个人 Token 开发联调（文件列表已实测通） |
| 4 | doc v3 树转 Markdown 转换器 | 递归遍历 Document/Paragraph/Run/Text 属性树，Paragraph 边界补 `\n`；样式/表格/图片映射策略实现时定 | ✅ 可先行——doc v3 读取已实测通（注意 URL 中 `$` 编码为 `%24`） |
| 5 | 导入任务框架 | 异步队列 + 频控限速（单用户 300 次/分、单文件 150 次/分）+ 状态落库 + 前端任务页 | ✅ 已完成开发（2026-10-20：调度器/导入服务/DB 4 表/36 单测全绿） |
| 6 | 导出兜底（DOCX/PDF） | async-export（不填 exportType 得 DOCX，复用既有 docx 解析；`exportType=pdf` 得 PDF）+ progress 轮询（operationID 20 分钟 / URL 1800 秒）+ 每 openID 9 次/天配额闸门；**doc v3 报 400010 时自动降级此路径** | ✅ 已完成开发（0bcb03d3） |
| 7 | 知识库前端入口 | 知识库页新增「从腾讯文档导入」+ 授权状态管理（含重新授权引导）；开发期先用「绑定个人测试 Token」代替 OAuth 授权位 | ✅ 已完成开发（2026-10-20：独立页面 /t/:tenant_id/knowledge/tdoc-imports，浏览器金路径真机验证待做） |
| 8 | 测试与真机验收 | 单测（OAuth 刷新/树转换器/频控 mock）+ 测试环境真实账号联调 | 📋 随开发推进 |

**执行顺序**：#3 -> #4 -> #5 -> #7 并行推进（全部可凭 §7.1 个人测试 Token 联调）；#2/#6 待第三方应用到位后补齐。

## 6. 官方文档资料（已备齐）

搜索智能体已于 2026-10-20 完成 6 文件快照：`ext/腾讯文档接口/`（01 总览与 OAuth / 02 通用规范与错误码 / 03 文件管理 / 04 doc v3 / 05 表格类 / 06 开源项目）。9 项清单全覆盖，本文档 §2/§5 已按快照修正。快照内文档自相矛盾处（code 有效期、access_token 有效期）以实测为准，见快照 README 第四节。

## 7. 测试账号开通（现状：个人 Token 已通，第三方应用入口缺失）

**实际情况（2026-10-20）**：开发者后台（docs.qq.com/open/developers/）注册个人开发者并资质审核通过后，控制台**只有「首页 / 账号管理」两个菜单，没有「创建第三方应用」入口**——平台直接发放绑定开发者本人账号的个人 Token（client_id + access_token + open_id 三元组，30 天有效可重置）。该凭据**可直接调用 OpenAPI**（实测见 §7.1），足以支撑任务 #3/#4/#5/#7 全部开发联调。

**缺口**：第三方应用（发 `Client Secret`、配 `redirect_uri` 白名单）是多租户 OAuth 的前提，官方文档描述的创建流程（快照 01 §2.1）在当前控制台无对应 UI。已通过平台「我要反馈」询问，待官方回复后补任务 #2。

**开发期账号准备**：
- 开发者本人腾讯文档账号放测试文档：在线文档若干（树转换器素材）、表格/智能表各 1-2 篇（验证 scope/类型拒绝行为）、一篇他人共享的快捷方式文档（验证 `shortcutID` 分支）
- 生产前用**公司名义**申请第三方应用（应用主体影响配额上限与用户授权时的信任展示），确认批复 scope 覆盖：`scope.doc.readonly`（读内容）、`scope.drive.readonly`（列目录）、`scope.drive.file.metadata`（ID 转换）、`scope.drive.exportable`（PDF 导出兜底）、`scope.user.info.base`

**测试环境回调（任务 #2 启动前确认）**：redirect_uri 仅支持 HTTPS + 白名单；多租户统一回调域名 + `state` 回带标识。

### 7.1 测试凭据（个人开发者个人 Token，明文登记便于调试）

> ⚠️ **仅限测试调试用**：绑定开发者个人账号、账号内无真实数据；禁止用于生产或写入代码/配置提交。泄露可在开发者后台「重置」。正式多租户接入仍需第三方应用（client_secret + redirect_uri，入口待官方反馈确认）。

| 项 | 值 |
|----|----|
| client_id | `69103ee2bd204c599dcdffecf1fab528` |
| access_token | `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJjbHQiOiI2OTEwM2VlMmJkMjA0YzU5OWRjZGZmZWNmMWZhYjUyOCIsInR5cCI6MSwiZXhwIjoxNzk1MDY4ODkyLjE3OTM3NjgsImlhdCI6MTc5MjQ3Njg5Mi4xNzkzNzY4LCJzdWIiOiJlYjBhNGY3ZWUyMGY0NjI1YmNmZjYxZjRlYjdmYzAzMyJ9.4D7Do_Ckfq3ZjbNReix2ZYyT3VtVCq311Bi99MS2BGY` |
| open_id | `eb0a4f7ee20f4625bcff61f4eb7fc033` |
| 有效期 | 至 2026-11-19 14:14:52（30 天，过期后台重置） |
| 配额 | 普通用户 2000 次/天 |
| 对应 .env | `TDOC_CLIENT_ID` / `TDOC_ACCESS_TOKEN` / `TDOC_OPEN_ID` |

**调用方式**（三元组请求头，2026-10-20 实测）：

```bash
curl -s "https://docs.qq.com/openapi/<接口路径>" \
  -H "Access-Token: $TDOC_ACCESS_TOKEN" \
  -H "Client-Id: $TDOC_CLIENT_ID" \
  -H "Open-Id: $TDOC_OPEN_ID" \
  -H "Accept: application/json"
```

**实测连通性**（2026-10-20）：
- ✅ `GET /openapi/drive/v2/folders/` 文件列表（ret=0，返回测试目录+1 篇 doc）
- ✅ `GET /openapi/doc/v3/{fileID}` 内容读取（fileID 如 `300000000$MhMtWFTGtSzb`，URL 中 `$` 须编码为 `%24`；返回 Document/Paragraph/Run/Text 属性树）
- ✅ `POST .../async-export` + `GET .../export-progress`（《工作日报》含表格+图片，不填 exportType 得 DOCX 含 1 表格 1 图片文本完整；`exportType=pdf` 得 PDF；当日已耗 2/9 次配额）
- ❌ `GET /openapi/doc/v3/{fileID}` 对**含表格+图片的《工作日报》**稳定返回 `400010 Unmarshal document error`（同一文档补内容前正常；纯文本老文档交叉验证仍正常）——doc v3 解析器对部分内容形态有缺陷，兜底降级路径必备
- ❌ `GET /oauth/v2/userinfo`（ret=10000，OAuth 侧接口不认个人 token，属预期）
- ⚠️ `GET /openapi/drive/v2/search`（ret=10002 参数名待查快照 §1.6）

## 8. 决策点与风险

| # | 决策点 | 当前倾向 |
|---|--------|---------|
| 1 | 应用主体 | 测试个人名义（已开通），生产公司名义第三方应用（入口待官方反馈） |
| 2 | 内容拉取主路径 | **doc v3 结构化读取为主**（150 次/分无日配额）+ **400010 失败时自动降级 async-export DOCX**（9 次/天/用户，走既有 docx 解析）。开发期需进一步定位 400010 触发条件（图片 or 表格 or 特定插入方式），若覆盖面广则评估调高导出路径优先级 |
| 3 | 表格类文档 | Phase 1 不做，UI 明确提示暂不支持 |
| 4 | 导入去重语义 | 同一文档重复导入是「覆盖更新」还是「新建副本」，待产品定 |
| 5 | 配额管理 | 按租户统计 API 调用次数（应用级 2 万次 + 频控），超限排队；是否需要租户级配额告警待定 |
| 6 | 树转 Markdown 保真度 | 纯文本 + 标题层级保真即可 vs 尽量保留表格/图片（工作量差异大），实现时按快照 §04 节点类型评估 |
| 7 | 第三方应用申请路径 | 当前控制台无创建入口；若官方回复确认个人开发者不可建第三方应用，需评估公司主体资质申请或调整多租户授权方案 |
