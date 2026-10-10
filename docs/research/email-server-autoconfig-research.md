# 邮箱服务器自动发现（Autoconfig）调研与配置体验优化方案

> 2026-09-28 调研。目标：让普通用户只填「邮箱地址 + 授权码」两个东西即可完成邮箱绑定，SMTP/IMAP 服务器、端口、加密方式全部自动识别；识别不了的再降级为现在的手动表单。

## 1. 现状与问题

### 1.1 用户现在要填什么

邮箱绑定入口是设置弹窗「邮箱设置」Tab（`frontend/web/components/SettingsDialog.vue:93-207`），用户需要手填 **9 个字段**：邮箱地址、SMTP 服务器、SMTP 端口、SMTP 用户名、SMTP 密码、SMTP 加密方式、IMAP 服务器、IMAP 端口、IMAP 加密方式。

对普通用户（尤其 QQ/163/企业邮用户）这是最大的配置门槛：他们不知道什么是 SMTP/IMAP、不知道端口号、不知道「加密方式」选哪个，更不知道密码栏要填的是**授权码而不是邮箱密码**。

### 1.2 代码现状（探索结论）

| 层 | 位置 | 现状 |
|---|---|---|
| 工具实现 | `src/tools/email/email_tool.py` + `email_lib.py` | smtplib/imaplib 标准库，SSL/STARTTLS 分支齐全 |
| 数据模型 | `src/models/user.py:21-60` `UserEmail`；表 `user_email_settings` | SMTP/IMAP 共用一对账密（`use_smtp_auth` 默认 true），密码 Fernet 加密 |
| API | `src/api/email_settings.py` | GET/POST/DELETE `/api/email-settings`；POST 先给自己发测试邮件再落库 |
| 前端 | `SettingsDialog.vue` | 手填表单，校验仅查非空 |
| 供应商识别 | **无** | 全仓库无任何域名→服务器映射、MX/Autoconfig/Autodiscover 逻辑 |

顺带发现的三个体验缺陷（与本方案一并解决）：

1. **保存只验证 SMTP，不验证 IMAP**（`email_settings.py:104-110`）——IMAP 配错要到 agent 第一次读信才暴露，用户已以为绑定成功。
2. **编辑配置时密码必须重填**（前端 placeholder「不修改请留空」，但后端没有留空保留旧密码的逻辑，留空会在测试发送时失败）。
3. SMTP 用户名对绝大多数供应商就等于邮箱地址，却要求用户手填。

### 1.3 一个必须先接受的事实：凭据拿不到，只能引导

服务器参数可以高度自动化（主流域名命中率 90%+），但**登录凭据无法自动化**：

| 供应商 | 现状（2025-2026） |
|---|---|
| QQ 邮箱 / Foxmail / 腾讯企业邮 | 需网页端「设置→账户」开启 IMAP/SMTP 服务并生成**授权码**；填邮箱密码必然失败 |
| 163/126/yeah | 同上，需生成**客户端授权密码** |
| Gmail | 2022 年起彻底停用账号密码，需 OAuth2 或应用专用密码（需先开 2FA） |
| Outlook.com/M365 | 基本认证已弃用，走 OAuth2 为主 |
| 部分企业安全策略 / 受保护账户 | 仅支持动态密码/短信 OTP 或设备验证（如 Gmail 高级保护计划账户只能 OAuth）——**静态凭据天然不存在，agent 无法连接** |

所以「极致体验」= **自动发现服务器（全自动）+ 按供应商精准引导用户完成「开通协议 → 生成授权码」（半自动）** 两件事都做好；对凭据天然不可用的场景，识别出来就明确告知「暂不支持」，不浪费用户试错。注意 QQ/163 的 IMAP/SMTP 服务**默认关闭**，且开通流程本身含人机/短信验证（如 163 开授权码需短信确认）——这些只能引导，无法代劳。识别出供应商后直接给出该家的开启步骤卡片和官方直达链接，比让用户自己去搜强得多。

## 2. 业界标准方案调研

业界没有单一标准，成熟客户端（Thunderbird/Apple Mail/Outlook）都是一条「多源探测链」。

### 2.1 Thunderbird Autoconfig + ISPDB（Mozilla，主力方案）

- **格式**：一份 XML 直接给出 IMAP/SMTP 的主机、端口、加密、认证方式。实测 ISPDB 返回 `imap.qq.com:993 SSL / smtp.qq.com:465 SSL`，与本项目 `EncryptionType(ssl/tls/none)` 一一对应（XML 的 SSL/STARTTLS/plain）。
- **两个数据源**：
  - 供应商自托管：`https://autoconfig.{domain}/mail/config-v1.1.xml`、`https://{domain}/.well-known/autoconfig/mail/config-v1.1.xml`——覆盖企业自建邮局（automx2 等工具可同时发布）。
  - **中央库 ISPDB**：`https://autoconfig.thunderbird.net/v1.1/{domain}`（只传域名不传完整邮箱，隐私友好）。**MPL-2.0 许可，官方明确免费供任何客户端使用，可在线查也可整库镜像自托管**。
- **覆盖**：gmail/qq/163/outlook 等主流域名均在库；但**中国小众域名与国内企业自建邮局覆盖弱**，且实测 qq.com/163.com 自己的 well-known 路径都是死链——国内域名主要靠 ISPDB 和内置表。
- **格式扩展点**：XML 支持 `<enable visiturl>` + `<instruction>`，语义正是「用户需先去某网址开启服务」——与我们的授权码引导天然契合。

### 2.2 Microsoft Autodiscover（Exchange/M365 企业邮）

- 探测链：`https://{domain}/autodiscover/autodiscover.xml` → `https://autodiscover.{domain}/autodiscover/autodiscover.xml` → 兜底查 SRV 记录 `_autodiscover._tcp.{domain}`。
- 客户端 POST 一段含邮箱地址的 XML（**历史上曾带凭据，造成过凭据收割事故**；自实现必须遵守：先无凭据探测、仅 HTTPS + 有效证书才继续）。
- 适用场景：Exchange/M365/企业自建；对 QQ/Gmail 等消费邮箱无效。作为企业域名的探测环节而非主路径。

### 2.3 DNS 直接探测

- **MX → 供应商映射**：MX 记录能可靠判断域名归属。实测特征：`*.l.google.com`→Gmail、`mx*.qq.com`→QQ、`*.mxmail.netease.com`→网易、`*.olc.protection.outlook.com`→微软、`mxbiz1/2.qq.com`→腾讯企业邮、`mxhichina.com`→阿里企业邮。**这是识别「企业自有域背后用的是哪家邮箱服务」的关键手段**——自有域（如 @company.com）本身猜不出服务器，但 MX 一查便知是腾讯企业邮还是阿里企业邮还是自建。
- 注意：MX 只说明收件归属，用户侧服务器还要靠「MX 后缀 → 该供应商服务器参数」静态映射；且同供应商不同子品牌 host 不同（163 与 126 的 imap host 分别是 imap.163.com / imap.126.com），所以**邮箱域名精确匹配永远排在 MX 匹配前面**。
- **RFC 6186 SRV 记录**（`_imaps/_submission/_submissions._tcp.{domain}`）：格式标准（如 `_imaps._tcp SRV 0 1 993 imap.example.com.`），但实测只有 Gmail/Fastmail/iCloud 发布，QQ/163/outlook/Zoho 均未发布——高质量信号但覆盖率低，只能作加分项。规范要求：SRV 目标在域外时需用户确认（防劫持）。

### 2.4 主机名猜测（最后兜底）

`imap.{domain}` / `mail.{domain}` / `smtp.{domain}` × 端口 993/143/587/465/25，用 TLS 握手 + 读 CAPABILITIES 验证是否真是邮件服务器。自建邮局（如 mail.company.com 直接提供服务）有一定命中率，但有「探测到假阳性服务器」和耗时问题，只作为可选增强。

### 2.5 客户端策略参考

- Thunderbird：本地配置 → autoconfig.{domain} → well-known → ISPDB → 主机名猜测 → 手动。
- Apple Mail：内置大供应商直连 + Exchange Autodiscover + 普通邮箱靠主机名猜测实测。
- K-9 Mail：供应商 autoconfig → ISPDB。

### 2.6 安全风险与底线

- 早期 autoconfig 规范用 http://，存在中间人篡改配置窃取凭据风险 → **探测一律 HTTPS + 有效证书（拒绝自签）**。
- Autodiscover 的历史凭据收割事故 → **探测阶段绝不发送任何凭据**；凭据只在最终「保存并测试」时发往已确认的服务器。
- SRV/autoconfig 指向域外目标 → 需用户确认（RFC 6186 §5）。
- **SSRF**：我们的探测发生在服务端，`{domain}` 是用户输入。well-known/Autodiscover 的 HTTP 探测必须先解析 DNS，**目标为私网/环回地址（10./172.16-31./192.168./127./169.254. 等）时拒绝发起请求**，并对 discover 端点限流。
- 超时与预算：每个探测步骤 3-5s 超时，前几步并行，整体预算 ≤10s；成功结果按域名缓存（TTL 数天），同一域名全租户共享缓存。

## 3. 推荐方案

### 3.1 探测链（后端 `POST /api/email-settings/discover`）

输入只有邮箱地址，全程不带凭据。按性价比排序，1-3 并行发起：

| 序 | 探测源 | 覆盖谁 | 说明 |
|---|---|---|---|
| 1 | **内置供应商表**（域名精确匹配，如 `qq.com`→QQ） | 国内主流 + 全球主流 | 命中即返回，最快最可靠；表内容实施时逐家用脚本实测核实后固化 |
| 2 | **MX 记录 → MX 后缀映射** | 企业自有域（腾讯企业邮/阿里企业邮/M365/网易企业邮/Gmail Workspace） | 国内企业客户的主力场景；先查 MX 再查 SRV，一次 DNS 会话完成 |
| 3 | **ISPDB 在线查询**（可镜像自托管） | 海外长尾 + 国内补充 | `GET https://autoconfig.thunderbird.net/v1.1/{domain}`，MPL-2.0 可商用 |
| 4 | **域名自托管 autoconfig / Autodiscover** | 自建邮局企业域 | 仅 HTTPS+有效证书；私网目标拒绝（SSRF 防护） |
| 5 | **RFC 6186 SRV**（`_imaps/_submission/_submissions`） | Gmail/Fastmail/iCloud 等少数 | 高质量信号，作加分项 |
| 6 | 主机名猜测 + TLS/CAPABILITIES 验证 | 自建邮局长尾 | **可选、最后排期**；未命中直接走手动表单 |

任一源命中 SMTP+IMAP 即可返回，并标注 `source` 供前端展示置信度；全部未命中 → 返回未识别，前端切换手动表单（即现状表单）。

响应为**候选列表**（同一域名可能命中多个产品线/别名域，按置信度排序）：

```json
{
  "success": true,
  "data": {
    "domain": "company.com",
    "matched_by": "mx",
    "candidates": [
      {
        "provider": { "id": "tencent_exmail", "name": "腾讯企业邮箱", "auth_type": "auth_code" },
        "smtp": { "server": "smtp.exmail.qq.com", "port": 465, "encryption": "ssl" },
        "imap": { "server": "imap.exmail.qq.com", "port": 993, "encryption": "ssl" },
        "source": "mx",
        "confidence": "high"
      }
    ],
    "username_hint": "user@company.com",
    "auth_help": {
      "steps": ["登录腾讯企业邮箱网页版 → 设置 → 客户端专用密码", "生成专用密码并粘贴到下方"],
      "url": "https://example.com/help"
    }
  }
}
```

### 3.2 前端交互：两步式表单

把现在的 9 字段表单改成「主路径两步 + 高级设置折叠」：

1. **第一步**：只填邮箱地址。失焦/点击「下一步」时调 discover，展示「已识别：QQ 邮箱」+ **两段式引导卡片**：① 先开通 IMAP/SMTP 服务（QQ/163 默认关闭，需网页端手动开启）→ ② 再生成授权码，每步带官方直达链接——正对应 ISPDB XML 里 `<enable visiturl>` 的语义（QQ/163 条目自带该字段）。
2. **第二步**：只填授权码/密码一个框。SMTP 用户名自动预填为邮箱地址。
3. **「高级设置」折叠区**（默认收起）：展开即现完整手动表单（识别错误/未识别/自建邮局调优时用），已用识别结果预填。
4. **保存并测试（候选实测定序）**：后端除现在的 SMTP 测试发送外，**增加 IMAP 登录验证**（login + logout 即可，不拉信），两个都过才落库——修掉「IMAP 配错绑定后才发现」的坑。当 discover 返回多个候选时，用户填完密码后**按置信度依次尝试各候选配置**（SMTP 发信 + IMAP 登录），第一个全通过的落库，用户无感知——这就是多产品线歧义的最终裁定机制。
5. **失败对症提示（报错 → 人话 + 引导）**：各家在「协议未开通 / 授权码错误 / 需要网页端动态验证」时返回的 SMTP/IMAP 报错特征不同。实施时逐家采集真实报错样本，建立「报错特征 → 人话提示 + 对应引导链接」映射：未开通 IMAP → 提示先去开通（带直达链接）；授权码错误 → 提示重新生成；开通流程需短信/人机验证 → 说明网页端完成后再回来。**报错样本采集纳入 Phase 1 实测核实清单**（§3.4.2）。

识别失败（私有域、探测超时）时**优雅降级为「预填版」手动表单，不是裸的 9 字段表单**：

- 已确定的信息全部预填：SMTP 用户名 = 邮箱地址、端口默认 465/993、加密默认 SSL——用户实际只需手填两个服务器地址 + 密码（4 个字段）；
- MX 查询结果即使没命中映射表也展示为提示（「检测到贵司 MX 指向 mx.company.com，收件服务器通常形如 imap. 或 mail. + 贵司域名」）；
- Phase 3 的主机名猜测（`imap.{domain}` / `mail.{domain}` × 常见端口，TLS 握手 + CAPABILITIES 验证）正是为此场景准备：自建邮局命名规律集中，命中即可自动预填；
- 无论手动还是自动，**保存即端到端验证**（SMTP 测试发信 + IMAP 登录都过才落库），错误配置进不了系统。

### 3.2.1 数据来源：静态为主、动态补充，分层信任

| 探测源 | 静态/动态 | 信任模型 |
|---|---|---|
| 内置供应商表 | **静态**，随版本发布 | 实施时逐家实测核实后固化；主流参数十几年稳定（smtp.qq.com:465 等），维护成本≈0 |
| MX → 供应商 | DNS 动态查，MX后缀→参数的映射**静态** | DNS 本身可信；映射表同上 |
| ISPDB | **动态**（HTTPS 实时查，可镜像自托管） | Mozilla 维护、MPL-2.0 |
| 域名自托管 autoconfig / Autodiscover | **动态** | 域名持有者自己发布——对「这个域名该用什么服务器」而言域主即权威；强制 HTTPS + 有效证书防中间人 |

动态源不被「盲信」的安全分层：探测阶段绝不发凭据；最终凭据只在「保存并测试」时发往探测结果服务器，SMTP 发信 + IMAP 登录的端到端验证兜住一切被污染/过期的探测结果；探测结果按域名缓存 TTL 数天。

### 3.3 落点与改动面

| 改动 | 位置 |
|---|---|
| 探测模块（探测链 + 内置表 + 缓存） | 新增 `src/services/email_autoconfig.py`（或 `src/tools/email/autoconfig.py`），纯函数 + 按 domain 的进程内 TTL 缓存 |
| discover 端点（限流 + SSRF 防护） | `src/api/email_settings.py` 新增路由 |
| IMAP 登录验证 | `src/api/email_settings.py` save 流程 + `email_lib.py` 已有 `imap_session` 可直接复用 |
| 密码留空保留旧值 | save 端点：已绑定且密码留空时从库取旧密码参与测试，落库不覆盖 |
| 前端两步式表单 | `SettingsDialog.vue` 邮箱 Tab 重构 + `frontend/web/api/settings.ts` 加 discover 调用 |
| DNS 依赖 | 补 `dnspython`（纯 Python，标准库不支持 MX/SRV 查询） |

### 3.4 分期建议

- **Phase 1（核心体验，覆盖绝大多数用户）**：内置表（域名精确 + MX 后缀映射，含 QQ/Foxmail/163/126/yeah/sina/aliyun/139/腾讯企业邮/阿里企业邮/Gmail/Outlook/iCloud）+ discover 端点 + 前端两步式表单 + 授权码引导卡片 + IMAP 登录验证 + 密码留空保留。不做任何出网探测，无新增外部依赖风险。
- **Phase 2（长尾覆盖）**：ISPDB 在线查询（或镜像自托管）+ 域名自托管 autoconfig/Autodiscover + SRV 记录 + SSRF 防护与限流。
- **Phase 3（可选）**：主机名猜测 + 端口验证；Gmail/Outlook OAuth2（XOAUTH2），属独立大项，另立调研。

### 3.4.1 内置表必须按「供应商 × 产品线」建模，不能只到供应商粒度

同一供应商的个人邮箱与企业邮箱、乃至不同企业邮箱产品线，服务器地址常常不同；**映射表的最小单元是产品线，不是供应商**。已知差异（均需实施时实测核实，此处为设计示意）：

| 供应商 | 产品线 | 识别特征 | 服务器（示意，需核实） |
|---|---|---|---|
| 腾讯 | 个人 QQ 邮箱（qq.com/foxmail.com） | 域名精确 | imap.qq.com / smtp.qq.com |
| 腾讯 | 企业邮箱（exmail） | **MX mxbiz1/2.qq.com** | imap.exmail.qq.com / smtp.exmail.qq.com |
| 网易 | 个人 163 / 126 / yeah | 域名精确（**host 互不相同**） | imap.163.com / imap.126.com / imap.yeah.net |
| 网易 | 个人 VIP（vip.163.com） | 域名精确 | 服务器与免费版不同 |
| 网易 | 企业邮箱 | MX 特征（与企业产品线绑定） | 独立服务器 |
| 阿里 | 个人（aliyun.com） | 域名精确 | imap.aliyun.com |
| 阿里 | 企业邮箱（原 mxhichina） | **MX mxhichina.com** | imap.mxhichina.com / smtp.qiye.aliyun.com |
| 微软 | 个人（outlook.com/hotmail.com） | 域名精确 | imap-mail.outlook.com / smtp-mail.outlook.com |
| 微软 | M365 商业租户（自有域） | **MX {tenant}.mail.protection.outlook.com** | outlook.office365.com / smtp.office365.com |
| Google | 个人 Gmail 与 Workspace 自有域 | MX *.l.google.com | 同一套 imap.gmail.com / smtp.gmail.com（正面例子） |

四条推论：

1. **个人邮箱靠域名精确匹配即可**（qq.com 天然等于个人版）；**企业邮箱的用户域名是自定义的，必须靠 MX 识别产品线**，因此 MX 特征表要按产品线粒度维护，且部分供应商不同产品线 MX 特征相近甚至相同——这正是 discover 返回**候选列表**、保存时**实测定序**的原因（§3.1/§3.2）。
2. **表的维护要有可观测闭环**：产品线换地址是低频但真实会发生的事。保存阶段的实测失败按邮箱域名聚合打日志/埋点，某域名失败率异常即触发更新内置表；Phase 2 的 ISPDB 动态查询本身也是交叉校验渠道。不能指望静态表永远正确——它只需要「大概率对」，正确性最终由保存实测保证。
3. 企业侧治本选项：租户管理员在后台为本租户**预配/覆盖产品线映射**（如全公司都用某企业邮），该租户用户免识别直接命中；与规划中的连接中心（`docs/system/connection-center-design.md`）属同一租户级配置思路，可复用其扩展点。
4. **开通方式与凭据形态也按产品线维护**：是否默认开启 IMAP/SMTP、开通入口 URL、凭据是授权码/专用密码/OAuth/仅动态密码，同供应商各产品线不同（个人版默认关闭需自行开通；企业邮常由管理员后台统一开通或默认开启）。这些字段挂在 §3.4.1 的产品线表上，驱动前端的引导卡片与「暂不支持」判定——凭据天然不可用（仅 OTP/受保护账户）的产品线标记 `unsupported`，discover 直接返回不可绑定原因。

### 3.4.2 内置表数据核实流程

所有服务器参数**实施时必须逐家实测核实**（本报告表内参数来自 ISPDB 与公开文档，供设计参考，不直接作为事实落地）：用脚本对每家产品线做 MX 查询 + TLS 握手 + CAPABILITIES 读取 + 真实账号登录验证，核实结果连同探测证据一并固化进表与测试用例。

## 4. 来源

- Thunderbird Autoconfiguration 规范/格式/查找顺序：https://wiki.mozilla.org/Thunderbird:Autoconfiguration 、…/ConfigFileFormat
- ISPDB（MPL-2.0）：https://github.com/thunderbird/autoconfig ；在线实测 https://autoconfig.thunderbird.net/v1.1/qq.com
- Microsoft Autodiscover：https://learn.microsoft.com/en-us/exchange/client-developer/exchange-web-services/autodiscover-for-exchange
- RFC 6186（IMAP SRV）：https://www.rfc-editor.org/rfc/rfc6186 ；RFC 8314（submissions/465）
- automx2：https://rseichter.github.io/automx2/ ；nodemailer well-known services：https://nodemailer.com/smtp/
- MX/SRV/well-known 实测（gmail/qq/163/126/outlook/hotmail/icloud/fastmail/zoho，2026-09-28）：nslookup/curl
