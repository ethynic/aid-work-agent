# 微信公众号自有号清单源设计（历史文章导入，主通道）

> 依据：[wechat-download-api 原理调研](../../research/wechat-mp/wechat-download-api-principle-research.md) + 2026-09-16 真机实验（§7）。
> 状态：开发完成（2026-09-16，三智能体闭环），待部署验收。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| 调研 | wechat-download-api 源码级原理分析 | ✅ 完成（2026-09-16） | [调研报告](../../research/wechat-mp/wechat-download-api-principle-research.md)，已登记索引 |
| 实验 | 真机实验 E1~E5 | ✅ 完成（2026-09-16） | 自有号可用、群发覆盖坐实（27 群发记录）、跨号 200013 收紧、注销号 200007 |
| WP13 开发 | 三智能体闭环（开发→独立测试→CR→主控终检） | ✅ 完成（2026-09-16） | list_source/list_session/对账/tick④/前端改版；独立测试 452 passed+回归 109+真实探针 ret=0 两页 97 子篇；测试修模式切换非原子 1 处；CR 修 P1 前端切换静默 no-op 1 处，P2×9 登记（租户守卫纵深/批内重复自愈/update 保留字段清单/list↔freepublish 无去重等）；主控终检 452+build+import 全绿 |
| WP13-r1 | 首次回填上限+历史清单（负责人反馈迭代） | ✅ 完成（2026-09-16） | 默认 100 可调 ≤500 按子篇计数、增量走到重叠即停、history 实时只读接口+前端对话框；独立测试 557 passed（含另一会话计费/入库流共存）+逻辑/DB/真机探针 64 项全过；CR 修 P1 换号重扫绕过回填上限（重绑重置 backfill_done）+补 list_last_sync_at 保留字段，P2 登记见下；主控终检 557+build+import 全绿 |
| 部署验收 | 真机扫码绑定→同步→检索闭环 | 📋 待部署 | 宏陶瓷砖管理员扫码实测；会话 TTL 持续观察校准 |

### r1 遗留与设计取舍（CR 登记，按优先级）

1. **稳态删除盲区（设计取舍，需低频全量兜底）**：增量"重叠即停"后，重叠点以下老文章的 `is_deleted`/编辑变更不可见（清单按消息序排列，老文编辑不改变位置）。兜底方案：每周一次全量对账（下轮迭代补，reconcile 复用 complete 扫描即可）；当前版本删除感知仍以 URL 通道 24h 复核兜底。
2. 前端篇数输入空值钳 1 与后端空=默认 100 不一致（清空保存会把上限写成 1）；`loadMpListSession` 失败时保存会用初始 100 覆盖租户设置——建议前端空值回归 100+加载失败跳写该字段。
3. `_health_check` 未接通用 ListSourceError（网络耗尽穿 500，Redis 中间态残留至 TTL）。
4. list↔freepublish 双开同号会产生重复文档（身份体系不同，WP13 已登记）；URL/手动通道行 wx_update_time=NULL 使增量谓词视为未知→多翻页（不漏新，预算兜底）。
5. 页粒度早停溢出：小 N（如 1）实际回填整页（前端文案需注明"按消息整条计入"）。
6. 通知站外推送（webhook/邮件无租户管理员寻址）——待租户级通知通道后接入。
> 一句话：**租户管理员扫码自己的公众号，系统定期拉取该号完整「发表记录」清单（含群发历史），走既有 URL 直采管道入库。**
> **通道定位（负责人 2026-09-16 定版）**：①自有号清单源 = **第一方案（主通道）**；②手动粘贴 URL + 回调 = 第二；③freepublish 接口（WP9）= 最后（仅覆盖"发布"渠道的能力补齐，保留不删）。客户无需填写回调地址/Token/AESKey 即可完成主通道接入。

## 1. 背景与定位

公众号内容入知识库现有三条采集通道：回调+URL 直采（P1）、手动粘贴 URL、freepublish 接口通道（WP9，仅覆盖"发布"渠道）。租户主流发文习惯是**群发**，群发历史此前无自动获取路径。

本通道（自有号清单源）升为**主通道**：租户扫码自己的号一次授权，即可自动覆盖该号全部已发表内容（含群发历史+增量新文章），拉取式同步天然替代回调的"自动感知"价值，且无需客户配置回调三件套。

## 2. 平台能力边界（2026-09-16 实测钉死）

| 能力 | 状态 | 证据 |
|------|------|------|
| 扫码登录（bizlogin/scanloginqrcode） | ✅ 可用，纯 HTTP 脚本可完成 | 实验 §7.1 |
| searchbiz（名称→fakeid） | ✅ 健康账号可用 | 实验 §7.2 |
| **跨号清单**（appmsgpublish 带 fakeid 查任意号） | ❌ **平台收紧，一律 200013** | 实验 §7.3 |
| **自有号清单**（appmsgpublish 不带 fakeid，登录上下文） | ✅ 可用，含群发历史 | 实验 §7.4 |
| 注销/冻结号 | 扫码"成功"但全部接口 200007 access deny | 实验 §7.5 |
| 正文页 | 公开可抓，复用既有 URL 直采 | — |

结论：可行且合规的产品形态是**租户自扫自有号**——账号主人授权，能力边界与租户需求完全重合。

## 3. 后端设计

### 3.1 扫码会话与凭据保存（复用 WP4 渠道配置基建）

- 扫码会话挂到租户的 wechat_mp 渠道配置：已有回调配置则共用一条（补清单字段），无配置则**扫码成功自动创建**；v1 限制：每租户仅一个扫码绑定（多号并存登记为限制）。
- config 新增字段（全部走既有 `config_codec` Fernet 加密，加入 SENSITIVE_KEYS 白名单）：
  - `list_session_token` / `list_session_cookie`（加密存储，即凭据保存）
  - `list_session_at` / `list_session_expire_at`（≈扫码+96h，实测校准）
  - `list_account_nickname`（登录号身份，用于展示与防错绑）
  - `list_sync_status`：`active` / `expiring`（<24h）/ `expired` / `account_error`
- 扫码端点（`/api/saas/wechat-mp/list-session/*`，require_admin + 租户隔离）：
  - `POST /scan`：发起登录（startlogin→getqrcode），二维码返前端，中间态存 Redis（5min TTL）
  - `GET /scan/status`：轮询（ask）；status=1 → bizlogin 提 token → 合并 cookie → **健康检查** → 加密入库 → 返回账号昵称
  - `DELETE /scan`：解绑清除
- **健康检查（绑定即执行）**：own-context `appmsgpublish` begin=0 count=1 探活；200007→`account_error` 拒绑；200013→会话异常不绑；ret=0→绑定成功。

### 3.2 清单拉取（新模块 `src/wechat_mp/list_source.py`）

薄适配器，只出清单，正文一律走统一 URL 直采：

- `fetch_own_publish_list(session, begin, count=20)`：own-context（`fakeid=""`，type=101_1，sub_action=list_ex）；间隔 ≥2s、timeout 15s、瞬时退避、日志脱敏。
- `fetch_all(session)`：`begin += count` 直到 `>= publish_page.total_count`（**total_count 是消息数不是子篇数**，20 消息可展开 80 子篇）；重复页/无进展检测；单轮总预算 10min。
- 解析：`publish_list[].publish_info`（JSON 字符串二次解析）→ `appmsgex[]` 取 `aid/title/link/update_time/create_time/is_deleted/item_show_type/itemidx/digest/cover/author_name` + 记录级 `msgid/publish_type`。
- 失败语义：`session_expired`（200003/200040/非 JSON）、`account_error`（200007）、`freq_control`（200013，能力受限告警不进退避风暴）、结构异常不误报 success。

### 3.3 同步调度与会话保活（需求：凭据尽量不过期）

- **默认同步频率 1h**（`sync_interval_hours` 默认值对清单源取 1，客户可调）：自有号清单拉取消耗极小（1~2 请求/次），高频同步最大化会话存活概率（会话 TTL 是否随使用续期未获官方口径，实测观察 §7.6 跟踪）。
- **诚实口径**：不承诺会话永不过期（微信侧 TTL 未公开）；策略=高频使用 + 提前预警 + 过期通知兜底（§3.5）。
- 复用 30min tick 第④生成器：有 active 会话且到期的租户 → `trigger_type='list_sync'` queued run（config_id 落值）。

### 3.4 增量对账与文章级同步选择（与 WP9 同构 + 新需求）

- **首次回填数量上限（2026-09-16 负责人反馈新增）**：号级 `list_sync_max_articles`（默认 100，客户可调，硬顶 500，按**子篇**计数）。首次对账只取最新 N 篇建行/入队，边界消息整条计入（允许轻微超出，截断不超过 500）；回填完成置 `list_backfill_done`。**上限只约束首次回填**：此后增量新文章全部正常进入。
- **增量对账走"走到重叠即停"**：从最新页向下翻，遇到整页全部已知且未变即停（不做全量翻页），控制每轮请求数。
- **历史清单（超出回填范围的历史，不落库）**：按需实时只读接口 `GET /list-session/history`（require_admin+租户隔离，分页参数，复用 OwnListClient 限速），返回 标题/时间/链接 + 「已入库/未入库」标注（按 identity 查 articles 批量判定）；客户对未入库文章复制 URL 走既有手动粘贴通道导入。前端入口在渠道配置扫码区块（「查看历史文章清单」对话框，加载更多分页）。
- 对账（run 内，复用 WP9 三阶段模式）：
  - 子篇 `link`（短链）→ `identity.normalize_url` → `external_id=mp:s:{token}` → 与 articles 撞键，**多通道天然去重、记录位置语义与既有通道完全一致**（新增建行+item / `update_time` 变更重拉 / 未变跳过仅推进 `last_synced_at`）；
  - **删除**：清单 `is_deleted=true` 为源侧明确信号，直接软删文档，优先级高于 URL 通道多信号推断；
  - 对账失败 run=failed 不误报 success。
- **文章级同步选择（新需求）**：号级模式 `list_sync_mode`：
  - `auto_all`（默认）：全部清单文章自动入库（"用户可以选择所有"即此模式的默认体验）；
  - `manual`：新文章仅建 articles 行（`processing_status='pending_manual'`，新枚举值，不建 item 不计费不入库），用户在公众号内容页勾选后入队；
  - 模式可随时切换；`manual` 期间积压的文章在切回 `auto_all` 时自动补齐入队；被勾选文章复用既有 retry 入队语义（单篇/批量）。
- items 进既有 worker 队列 → URL 直采管道（fetch→extract→hash→VL/总结→落库→计费→售前挂接），零新增抓取/入库代码。

### 3.5 过期通知机制（新需求）

- 预警与通知两层：
  - `expiring`（expire_at-24h 起）：渠道配置页三态徽标 + 公众号内容页顶部横幅「清单授权即将过期，请重新扫码」；
  - `expired`（对账遇 session_expired 即置）：立即触发**通知租户管理员**——优先复用系统现有通知设施（开发时排查站内信/企微机器人/webhook 等既有模块，有则接入；无则 v1 以管理端横幅+渠道配置红点+run 失败记录兜底，站外推送登记后续）；
  - 通知去重：同一会话周期内同级别通知至多一次（Redis 标记）。

## 4. 前端设计（ChannelConfig.vue 改版 + 公众号内容页）

- **wechat_mp 配置弹窗改版：扫码区块置顶为主入口**，回调/接口配置折叠为「高级配置」（可选，保留 WP11 能力不动）：
  - 未绑定：「扫码授权」大按钮 → 二维码弹窗 → 轮询 → 成功显示账号昵称；失败显示明确原因（账号状态异常/会话异常）；
  - 已绑定：账号昵称 + 四态徽标（正常/即将过期/已过期/账号异常）+ 上次同步时间 + 同步频率（默认 1h）+ 同步模式切换（全部自动/手动挑选）+「重新扫码」「解绑」；
  - 文案如实：仅支持本租户自己的公众号；会话约 4 天，系统会提前提醒；群发+发布已发表内容都会同步。
- 公众号内容页：
  - 文章列表增加来源与同步状态展示（list 通道文章带同步模式标识）；
  - `manual` 模式下：新增「待挑选」过滤视图，支持单篇/批量「同步到知识库」按钮（入队后走既有进度展示）；
  - `expired`/`expiring` 横幅（§3.5）。
- 复用 Base* 组件与既有三态风格，无新视觉体系。

## 5. 工作流（端到端）

```
租户管理员扫码 → 健康检查 → 凭据加密入库（自动建/绑定配置）
      ↓
tick ④（默认 1h 到期）→ list_sync queued run
      ↓
list_source 拉自有号全清单（own-context 分页）
      ↓  identity 撞键去重 / update_time 增量 / is_deleted 软删
auto_all：articles+items 直接入队   manual：articles 落库待勾选
      ↓
用户勾选（manual 模式）→ 既有 worker 队列 → URL 直采 → 总结入库 → 计费 → 检索
      ↓
会话 expiring/expired → 前端横幅 + 通知管理员 → 重新扫码
```

## 6. 开发拆解（WP13，三智能体流程）

- WP13-r1（2026-09-16 负责人反馈迭代）：首次回填数量上限（默认 100 可调 ≤500，按子篇计数）+ 增量"走到重叠即停" + 历史清单实时只读接口与前端对话框（标注已入库/未入库，客户拿 URL 走手动粘贴导入）
- WP13：list_source.py + 扫码会话端点 + tick ④ + 对账/选择/软删 + ChannelConfig.vue 改版 + 通知接入 + 单测 ✅（2026-09-16 完成）
- 夹具：2026-09-16 真实清单样本（`.tmp/wps_own_list.json`）脱敏落 `tests/fixtures/wechat_mp/`
- 风险：平台进一步收紧自有号接口（薄适配器+状态码语义化+降级手动粘贴）；会话精确时长未实测（expiring 提前 24h+通知兜底）；manual 模式新枚举值对既有查询的影响需回归
- 合规：租户自扫自有号=账号主人授权；AGPL 无接触（协议自研）

## 7. 实验记录（2026-09-16，会话=讼迹AI）

1. **登录**：startlogin→getqrcode→ask 轮询（status 0→4→1）→bizlogin 提 token，纯 httpx 走通；cookie 11 项。
2. **searchbiz**：健康号正常（"人民日报"/"宏陶瓷砖"均命中返回 fakeid）。
3. **跨号清单**：appmsgpublish 带任何 fakeid（count 5/20/50/100）一律 `200013 freq control`——平台能力收紧，非频率问题；编辑器旧形状 `appmsg?action=list_ex&type=9` 同拒。
4. **自有号清单**：不带 fakeid → `ret=0`；total_count=27（消息数，群发 27/发布 0），首页 20 消息展开 80 子篇、80 唯一短链零重复，历史跨度 2025-11-27~2026-08-31；子篇含 aid/link/create_time/update_time/is_deleted/item_show_type/itemidx/digest/cover/author_name；publish_info 记录级含 msgid/publish_type/sent_status。
5. **注销号**：关联「全筑定制精装」（注销流程中）的微信扫码"成功"，但 home 302→acctclose，全部接口 200007 → 必须绑定后健康检查。
6. **会话保活观察（待跟踪）**：TTL 是否随使用续期未获官方口径；本会话高频探活持续观察，实测校准 expire_at。
