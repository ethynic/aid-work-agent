# 连接邮箱开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 连接邮箱页面与品牌弹窗、旧入口移除、后端错误类型与连接时间、引导文案 | 🔧 进行中 | — |

> 设计文档：[连接邮箱设计](../system/design-connect-mailbox-design.md)。品牌参数、交互状态和设计决策以设计文档为准，本计划只写怎么实现、怎么验证。
> 工作分支 `dev/connect-mailbox`，独立 worktree `C:\repos\aid-work-agent-connect-mailbox`。本功能录入邮箱密码和授权码，属于敏感数据范围，按高风险走完整三智能体流程（开发 → 独立测试 → 独立 Code Review）。

## 一、范围

把个人邮箱配置从「设置」弹窗搬到连接中心：

- 新页面「连接邮箱」：QQ邮箱、163邮箱、腾讯企业邮箱、网易企业邮箱四个品牌弹窗，加自定义邮箱表单。
- 移除「设置」弹窗里的邮箱设置。
- 后端只做两处小改动：保存失败时返回错误类型，查询时带上连接时间。接口路径和 9 项参数不变，旧客户端不受影响。
- 把「去设置里绑定邮箱」这类引导改成指向新入口。

不在本期：租户共用邮箱、一人多个邮箱、收件（IMAP）登录校验、Outlook 和 iCloud 邮箱接入、按 MX 记录识别服务商。

## 二、实现要点

### 2.1 前端

| 文件 | 改动 |
|---|---|
| `frontend/web/api/connectMailbox.ts`（新增） | 从 `api/settings.ts` 迁来查询、保存、删除三个函数和配置类型；保存失败的返回增加 `error_code`，查询结果增加 `updated_at`。继续用 `getAuthHeader()`，带 `X-Tenant-Id` |
| `frontend/web/components/connections/mailboxBrands.ts`（新增） | 品牌表和纯函数。品牌分三类：域名固定的个人邮箱（QQ、163）、填完整地址的企业邮箱（腾讯、网易）、自定义。每个企业品牌带一组按顺序尝试的服务器。Outlook、iCloud 邮箱在表里标记为预留，不渲染 |
| `frontend/web/components/connections/MailboxBrandIcon.vue`（新增） | 品牌图标，用设计稿里的示意 SVG，后续替换官方素材 |
| `frontend/web/components/connections/ConnectMailbox.vue`（新增） | 页面：`AppHeader` 加内容区，未连接 / 已连接 / 加载中 / 加载失败四种状态 |
| `frontend/web/components/connections/ConnectMailboxDialog.vue`（新增） | 弹窗：`BaseModal`（品牌 `md`、自定义 `lg`，开启 `accessible`），表单、校验、依次尝试、失败提示 |
| `frontend/web/components/ui/HelpExpand.vue` | 增加可选的 `open` 双向绑定，让失败提示能展开步骤。不传时行为不变 |
| `frontend/web/router/agentRoutes.ts` | 加路由 `connections/email`，名称 `tenant-connect-mailbox` |
| `frontend/web/components/MenuSidebar.vue` | 连接中心子菜单在「外部系统」后加「连接邮箱」（全体员工可见）；`isConnectionActive` 加上这个路径 |
| `frontend/web/components/SettingsDialog.vue` | 删除邮箱页签和左侧导航，只保留基础设置表单 |
| `frontend/web/api/settings.ts` | 删除邮箱相关的类型和函数 |

品牌表里的纯函数：

| 函数 | 作用 |
|---|---|
| 拼出完整地址 | 域名固定的品牌：输入不含 `@` 时补上固定后缀；含 `@` 但域名不属于该品牌时由调用方提示 |
| 按顺序生成 9 项配置 | 每个品牌返回一个数组，单服务器品牌一项，腾讯企业邮箱两项（通用、信创），网易企业邮箱三项（付费华北、付费华东、免费版）；自定义品牌 SMTP 用户名留空时取邮箱地址 |
| 从已保存配置识别品牌 | 根据邮箱域名和 SMTP 服务器判断是哪个品牌，用来显示图标、名称，以及「重新连接」时打开对应的弹窗 |
| 选择失败提示 | 多套服务器都失败时：有任意一次是授权失败就显示「填写内容有误」的提示，否则显示连接超时或网络异常 |

连接流程：按品牌给出的顺序逐个调用保存接口，第一个成功即结束；全部失败才显示失败提示。

### 2.2 后端

| 文件 | 改动 |
|---|---|
| `src/tools/email/email_tool.py` | 发送失败的结果里增加 `error_code`，取值 `auth`（授权失败）、`connect`（连不上服务器）、`timeout`、`network`、`other`。未绑定邮箱时的提示改为指向「连接中心 → 连接邮箱」 |
| `src/api/email_settings.py` | 查询响应带上 `updated_at`；保存失败时把 `error_code` 原样带回 |

### 2.3 引导文案

以下位置把「去设置 / 邮箱设置里绑定邮箱」改为「连接中心 → 连接邮箱」，只改措辞：

- `src/tools/email/email_tool.py` 的说明和返回文案
- `src/skills/excel-to-template-1.0.0/` 的 `SKILL.md` 和 `scripts/pipeline.py`
- `src/skills/b2b-email-sender-1.0.0/` 的 `SKILL.md` 和 `scripts/` 下的 `mailbox_resolver.py`、`reply_monitor.py`、`send_emails.py`
- `src/skills/b2b-lead-research-1.0.0/scripts/lead_db.py`

## 三、接口变化

接口路径和请求参数都不变。

| 接口 | 变化 |
|---|---|
| `GET /api/email-settings` | `data` 里增加 `updated_at`（最近一次连接成功的时间） |
| `POST /api/email-settings` | 失败时增加 `error_code`，原有 `error` 文案保留 |

## 四、测试

测试先行：先写能说明预期行为的测试，确认修改前失败，再实现。

| 范围 | 位置 | 覆盖 |
|---|---|---|
| 后端工具 | `tests/unit/tools/test_email_tool.py`（修改） | 授权失败、连不上、超时、其他四类错误的 `error_code`；未绑定邮箱的新文案 |
| 后端接口 | `tests/integration/test_email_settings_api.py`（新增） | 查询带 `updated_at`；保存成功和失败两种响应；不连接数据库 |
| 受文案影响的现有测试 | `tests/unit/tools/test_excel_pipeline.py` 等 | 断言跟着新文案更新 |
| 品牌表 | `frontend/web/__tests__/components/mailboxBrands.test.ts`（新增） | 各品牌的尝试顺序和拼出的 9 项；地址补全；从配置识别品牌；失败提示的选择 |
| 页面和弹窗 | `frontend/web/__tests__/components/ConnectMailbox.test.ts`（新增） | 两种分组、预留位不显示、校验提示、依次尝试、连接中禁止关闭、失败提示与展开步骤、更换提示、重新连接带出地址、断开确认、加载失败重试 |
| 侧栏菜单 | `frontend/web/__tests__/components/MenuSidebar.test.ts`（修改） | 普通员工能看到「连接邮箱」并能跳转 |

## 五、验证命令

从 worktree 根目录在 WSL2 的 Docker 里执行，镜像和前端依赖卷复用现有环境。

```bash
# 后端单测和接口测试
wsl.exe -d Ubuntu-22.04 --exec bash -lc 'cd /mnt/c/repos/aid-work-agent-connect-mailbox && docker compose -f docker-compose.dev-tools.yml run --rm -T python tests/unit/tools/test_email_tool.py tests/integration/test_email_settings_api.py -p no:cacheprovider -q'
# 后端语法和导入
wsl.exe -d Ubuntu-22.04 --exec bash -lc 'cd /mnt/c/repos/aid-work-agent-connect-mailbox && docker compose -f docker-compose.dev-tools.yml run --rm -T --entrypoint python python -c "from src.api.email_settings import router; from src.tools.email import email_tool"'
# 前端测试
wsl.exe -d Ubuntu-22.04 --exec bash -lc 'cd /mnt/c/repos/aid-work-agent-connect-mailbox && docker compose -f docker-compose.dev-tools.yml run --rm -T web test -- web/__tests__/components/mailboxBrands.test.ts web/__tests__/components/ConnectMailbox.test.ts web/__tests__/components/MenuSidebar.test.ts web/__tests__/architecture'
# 前端构建
wsl.exe -d Ubuntu-22.04 --exec bash -lc 'cd /mnt/c/repos/aid-work-agent-connect-mailbox && docker compose -f docker-compose.dev-tools.yml run --rm -T web run build'
```

修改前的基线：`test_email_tool.py` 36 个通过；`MenuSidebar.test.ts` 和 `BaseModal.test.ts` 共 29 个通过。

## 六、流程与风险

1. 开发智能体按任务书实现并自测，先做后端，再做前端。
2. 独立测试智能体跑新功能测试、受影响模块的回归和启动安全检查。
3. 主控者只读审查源码，通过后提交并推送本阶段。
4. 合并回 `master` 由用户确认后再执行。

测试重点：密码和授权码不出现在前端日志、请求日志和错误提示里；各品牌拼出的 9 项与设计一致；尝试顺序正确，第一套成功后不再继续；旧入口移除后设置弹窗的基础设置不受影响。

## 七、遗留

- 四个品牌的服务器参数来自官方帮助页，没有用真实账号实测。上线前每个品牌至少用测试账号连接一次；自动尝试的顺序和总耗时按实测调整。
- 品牌图标是示意 SVG，需要替换为品牌官方素材或由设计提供。
- 页面里的帮助文字需要同步到产品侧的《智能体后台操作指引》。
