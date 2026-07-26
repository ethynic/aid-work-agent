# 微信公众号文章搜索「不依赖微信 App」可行性调研

> 调研日期：2026-07-24
> 状态：✅ 可行性已澄清（无完美方案，需在「数据完整性 / 稳定性 / 合规」间取舍）
> 需求：让 AI agent 在**不安装、不依赖微信桌面/手机 App** 的前提下，搜索【公众号文章】并拿到标题 / 摘要 / 正文 / 链接。
> 落地方式（是否扩展现有 wecom-personal-rpa）暂未定，本报告先聚焦可行性。
>
> **📌 2026-07-24 需求澄清（重要）**：经用户确认，真实需求是 **全域关键词搜索**——不知道目标内容在哪个公众号，必须跨所有公众号按关键词发现文章（即搜一搜的核心能力）。
> 因此 **account-centric 的「路线 A（公众平台后台自建）已排除**——它只能按号取文，无法满足跨号全域关键词发现。可行域收窄为：搜狗微信搜索（免费/不稳定）、商业聚合 API（付费/稳定）、或回到 App 内搜一搜。

---

## TL;DR（核心结论）

1. **「搜一搜」本身无法脱离微信 App 直接调用**——无网页入口、无第三方搜索查询 API、H5 JSSDK 不含搜索。任何「直连搜一搜 API」的说法不可信。
2. **全域关键词搜文章且不依赖 App，现实只能在三角取舍中选其一（无完美解）**：
   - **免费但不稳定** → 搜狗微信搜索（[weixin.sogou.com](https://weixin.sogou.com/)）+ MCP（[WechatSogou](https://github.com/chyroc/WechatSogou) / Weixin Search MCP）。语义最接近搜一搜、免费开源，但反爬极严、数据延迟、长期前景差。**适合低成本验证原型。**
   - **付费且稳定** → 商业聚合平台 API（[新榜](https://www.newrank.cn/) 覆盖 2000万+号、[西瓜数据](https://data.xiguaji.com/) ~500万、[清博](https://www.gsdata.cn/) 有[开源 wechatAPI](https://github.com/gsdata-qingbo/wechatAPI)）。自建索引、覆盖广、稳定，但企业 TOB、报价不公开需联系商务。**适合上量/要稳定的生产场景。**
   - **放弃"不依赖 App"** → 微信 App 内搜一搜（复用本项目 browser/wecom-personal-rpa 基建自动化）。**唯一能拿到搜一搜级完整度与稳定性的方案**，代价是依赖 App。
3. **外部通用搜索引擎（百度/谷歌 `site:mp.weixin.qq.com`）基本无效**——微信 robots + 反爬使外部搜索引擎几乎索引不到公众号文章，此路不通。
4. 次级路线：第三方小 API（[聚合数据](https://www.juhe.cn/wiki/wxwzjxapijk560)、[极速API](https://m.jisuapi.com/api/weixin/)、阿里云 item_search）多为搜狗二次封装，继承搜狗不稳定 + 付费；[TianAPI](https://www.tianapi.com/apiview/1) 微信接口已停更。微信读书划词跳转（边缘，不适合 agent）。
5. **三角取舍一句话**：不依赖 App + 全域关键词 + 稳定免费，三者不可兼得——要么接受搜狗不稳定，要么付费买商业聚合，要么放弃"不依赖 App"用搜一搜。

---

## 一、「搜一搜」能否脱离微信 App？—— 官方能力核查

| 检查项 | 结论 | 依据 |
|--------|------|------|
| 独立网页搜索入口 | ❌ 无 | [search.weixin.qq.com](https://search.weixin.qq.com/) 仅是搜一搜运营/客服页（底部 Copyright © 2012-2026 Tencent，附客服电话/邮箱），**不是搜索框** |
| 第三方可调用的「搜索查询」API | ❌ 不开放 | 微信未公开任何让第三方发起搜索、返回结果的查询 API |
| `search.submitPages` 接口 | ❌ 不是搜索 | 该接口是让**小程序**把页面**推送**给搜一搜收录（[文档](https://developers.weixin.qq.com/miniprogram/dev/server/API/wxsearch/api_submitpages.html)），且**明确不支持第三方平台调用**。方向相反：它是「投递内容被收录」，不是「发起搜索」 |
| H5 JSSDK | ❌ 无搜索能力 | JSSDK 提供分享/扫码/支付等，**不含搜一搜查询** |
| 微信网页版搜索 | ❌ 已死 | 微信网页版本身已被大幅收窄，无开放搜索 |

**结论**：搜一搜是微信 App 内的封闭功能，外部没有合法直连通道。任何「绕过 App 直连搜一搜」的说法要么是误导，要么是逆向抓包 App 流量（高风控、易封号、违反 ToS，不推荐）。

---

## 二、不依赖 App 的替代路线

### 路线 A：微信公众平台后台接口自建服务（★ 推荐）

**原理**：用一个公众号扫码登录「微信公众平台」web 后台，复用后台运营者可用的内部接口（搜公众号拿 FakeID、按关键词搜文章、取正文、列历史文章）。这是**微信自己的后台**，数据源最权威、最全最新，**不依赖任何微信 App（桌面/手机）**。

**代表项目**：[wechat-download-api](https://github.com/tmwgsicp/wechat-download-api)（877⭐、108 fork、47 commits、AGPL-3.0、©2026）

| 能力 | 说明 |
|------|------|
| 搜公众号 / 搜文章 | 按名称搜公众号拿 FakeID；支持在指定公众号内按关键词搜文章 |
| 正文获取 | 通过文章 URL 解析，返回标题 / 正文 HTML/纯文本 / 图片列表等结构化数据 |
| RSS 订阅 | 订阅任意公众号，定时拉新文章（含图文），生成标准 RSS 2.0 |
| 整号导出 | 一键打包为 Markdown / HTML / Word / PDF / EPUB / Excel / JSON 七种格式 |
| MCP 接入 | **内置 MCP 服务**，Claude / Cursor 等 AI 客户端可直接搜索、订阅、读文章 → **对 agent 最友好** |
| 反风控 | `curl_cffi` 模拟 Chrome TLS 指纹 + SOCKS5 代理池轮转 + 三层自动限频（全局/单IP/文章间隔） |
| 部署 | Docker Compose 一键起，FastAPI + Swagger UI，`localhost:5000/login.html` 扫码登录 |

**优点**：数据最全最新（官方后台）、可拿全文、agent 友好（MCP/REST）、自托管可控、契合本项目自建服务风格。

**代价 / 注意**：
- 需**自备一个公众号**（订阅号即可，个人可免费注册）并周期性扫码续登录态。
- 公众平台后台接口是给 web UI 用的**非公开契约**，属 ToS 灰色地带——本质是「真实登录账号的正常后台操作」（与本项目 [[wecom-personal-rpa]] 的合规立场一致：真实账号正常登录后的正常操作视为合规），但调用频次需克制，避免触发风控。
- **语义局限**：强在「按号取文章/正文/RSS」(account-centric)；**不是**搜一搜式的「全域关键词跨号搜文章」。若需全域发现，仍需配合 B 或 App 内搜一搜。
- 代理成本：官方建议配 2–3 个 VPS 代理。

> 与本项目契合点：现有 wecom-personal-rpa 走「服务端拉取 + 凭证管理 + Redis 共享」范式，路线 A 的「扫码登录态托管 + 代理池 + 自建 FastAPI 服务」可复用同一套基础设施思路。落地是否走该复用，待可行性确认后决定。

### 路线 B：搜狗微信搜索 web + 封装

**原理**：搜狗（腾讯全资收购）曾有微信授权，是公众号内容在 Web 端**最接近搜一搜**的搜索引擎，[weixin.sogou.com](https://weixin.sogou.com/) 目前仍可访问，是少数能做「全域关键词搜文章」的非 App 通道。

**反爬强度（2025–2026 仍严，是本路线核心风险）**：
- **Cookie 动态下发**：SNUID / SUV / SCT 等由服务端返回，频繁访问即失效。
- **图文点选验证码**：超限后 302 跳转官方反爬页 [weixin.sogou.com/antispider/](https://weixin.sogou.com/antispider/)，要求依次点击指定汉字。
- **JS 加密**：搜索参数经 JS 加密。
- **IP 风控**：同 IP/SNUID 访问次数受限，需代理池轮换。
- 稳定采集需组合：Cookie 池 + 代理 IP + 验证码识别（OCR/打码平台）+ 合理间隔。

**封装方案**：
- [WechatSogou](https://github.com/chyroc/WechatSogou)：老牌搜狗爬虫库，带验证码识别，兼容 Scrapy。
- Weixin Search MCP（[lobehub](https://lobehub.com/zh/mcp/fancyboi999-weixin_search_mcp)）：基于搜狗接口的 MCP 工具，可接 AI 客户端。
- 阿里云市场 `item_search` / `item_get`：搜狗合规爬虫能力的商业封装。

**优点**：无需公众号、即开即用、**全域关键词搜文章**语义最接近搜一搜。
**代价**：反爬严、数据延迟/不全、长期前景不乐观（腾讯持续整合搜狗，订阅/收藏功能已下线、微信内访问被屏蔽、微信百科上线替代搜狗百科）。

### 路线 B+：商业聚合平台 API（付费、稳定、覆盖广）

**原理**：第三方数据公司自建公众号索引（长期采集数十万至千万级公众号 + 文章全文），对外提供**全域关键词文章搜索** API。比搜狗稳定（合法商业服务、自家索引、无验证码对抗），是企业级生产场景的现实选择。

| 平台 | 公众号覆盖 | 文章关键词搜索 | 计费 | 备注 |
|------|-----------|--------------|------|------|
| [新榜 NewRank](https://www.newrank.cn/) | 2000万+（监测110万+优质号） | ✅「文章搜索」/ 新媒体 API 产品 | 报价不公开，联系商务 | 行业头部、最权威综合 |
| [西瓜数据](https://data.xiguaji.com/) | ~500万 | ✅ 定制 API | TOB，需企业资质 | 广告投放分析见长 |
| [清博智能](https://www.gsdata.cn/) | ~62万 | ✅ [开源 wechatAPI](https://github.com/gsdata-qingbo/wechatAPI) + 定制 | 清贝计费（1清贝≈1元） | 舆情分析见长 |

**优点**：稳定、覆盖广、合规（合法商业服务）、支持全域关键词、无反爬对抗。
**代价**：付费（报价不透明，需逐家谈）、企业 TOB 接入门槛、各家覆盖范围与数据新鲜度需实测对比、仍非微信官方实时数据（新发文章有采集延迟）。

### 路线 C：第三方商业 API

| 服务 | 能力 | 现状 |
|------|------|------|
| [聚合数据 Juhe](https://www.juhe.cn/wiki/wxwzjxapijk560) | 微信文章精选，按偏好推荐/搜索 | 在售 |
| [极速API jisuapi](https://m.jisuapi.com/api/weixin/) | 按关键词/公司名搜公众号信息（名称/主体，**非文章全文**） | 在售 |
| 阿里云市场 item_search / item_get | 基于搜狗的文章搜索/详情封装 | 在售 |
| [TianAPI](https://www.tianapi.com/apiview/1) | 微信文章精选 | **已停止更新**，仅可查旧数据 |

**优点**：托管、省心、无需自建反爬。
**代价**：付费、多为搜狗/自爬二次封装（继承 B 的不稳定）、可持续性风险（TianAPI 已停更）、跨租户共用可能撞风控。

### 路线 D：微信读书 Web 划词跳转（边缘，不建议用于 agent）

微信读书 web 端「划词搜索」可在浏览器中跳转 `search.weixin.qq.com/cgi-bin/newsearchweb/userclientjump?path=page/search...` 触达搜一搜结果。这是目前**为数不多能在 PC 浏览器触达搜一搜**的途径，但本质是跳转、非独立查询 API，参数/稳定性不可控，**不适合 agent 稳定调用**，仅作为「搜一搜在 web 端存在间接入口」的事实记录。

### 对照：微信公众平台官方 API（对「跨号搜索」无用）

官方[发布能力](https://developers.weixin.qq.com/doc/service/guide/product/publish.html)/素材管理/消息推送等接口**仅能操作自有公众号**，出于内容生态保护**不开放跨号搜索**。对本需求（搜别人的文章）无用，仅列出以正视听。

---

## 三、路线对比表

| 维度 | A 公众平台自建 | B 搜狗 web | C 第三方 API | D 微信读书跳转 |
|------|---------------|-----------|-------------|---------------|
| 是否依赖微信 App | ❌ 不依赖 | ❌ 不依赖 | ❌ 不依赖 | ❌ 不依赖 |
| 数据源 | 微信官方后台（最全最新） | 搜狗索引（延迟/不全） | 多为搜狗二次封装 | 搜一搜（跳转） |
| 检索语义 | 按号取文章/正文 (account-centric) | **全域关键词搜文章** | 视服务而定 | 搜一搜关键词 |
| 能否拿正文 | ✅ 全文 + 图片 | ✅（需再抓文章页） | 部分支持 | ✗ 仅结果列表 |
| agent 友好度 | ✅ MCP + REST | ⚠️ 需自封装/MCP | ✅ HTTP API | ✗ 跳转不可控 |
| 稳定性 | 中（需维护登录态+代理） | 低（反爬严） | 中（依赖厂商） | 低 |
| 反爬/风控压力 | 中（后台接口，频次克制） | 高（验证码/IP） | 厂商承担 | 低（人工触发） |
| 合规 | ToS 灰色（真实账号正常操作） | 灰色偏黑（主动爬） | 厂商承担 | 中性 |
| 成本 | 自建 + 公众号 + 代理 | 自建反爬或买封装 | 按量付费 | 免费 |
| 长期前景 | 较好（官方数据源） | 差（搜狗被边缘化） | 不确定 | 不确定 |

---

## 四、合规与稳定性风险

- **搜一搜无合法外部通道**：宣称「直连搜一搜 API」的方案基本是逆向 App 流量或纯营销话术，风控/封号/ToS 违约风险高，不建议。
- **路线 A**：本质是「公众号运营者在 web 后台做正常操作」的自动化，与本项目 [[wecom-personal-rpa]]、[[web-automation-compliance-stance]] 的合规立场一致（真实账号正常登录后的正常操作视为合规）。注意克制频次、配代理、登录态隔离专用号。
- **路线 B/C**：搜狗是腾讯系，但对其爬虫有明确反爬（antispider），主动高频抓取属灰色偏黑，且数据质量与持续性均在下降。
- **登录态/凭证**：无论 A 还是 C 中涉及账号的，凭证（公众号后台 cookie/token）属敏感信息，须按项目规范加密存储、不回传明文（见 backend_dev.md 安全原则）。

---

## 五、推荐与下一步

**检索意图已明确：全域关键词发现（搜一搜式）**，account-centric 的路线 A 已排除。在「免费不稳定 / 付费稳定 / 依赖 App」三角中按场景选：

1. **验证原型 / 低频 / 零成本起步** → **搜狗微信搜索 MCP（Weixin Search MCP / WechatSogou）**。先跑通「agent 关键词搜 → 结果列表 → 取正文」链路，验证对业务是否真有用。接受反爬不稳定（配代理池 + 限频 + cookie 轮换）。
2. **上量 / 要稳定 / 企业级生产** → **新榜 文章搜索 API**（覆盖最广、最权威；次选西瓜/清博）。联系商务拿报价与 API 文档，作为生产数据源。原型阶段不必先买。
3. **要搜一搜级完整度 / 关键词命中即决定成败** → **回到 App 内搜一搜**，复用本项目 browser 工具（#20）或 wecom-personal-rpa 基建做自动化。是唯一能拿到与微信内搜一搜同等结果的方案，代价是依赖 App（与最初「不依赖 App」的设想冲突，需用户权衡）。

**三角取舍一句话**：不依赖 App + 全域关键词 + 稳定免费，**三者不可兼得**——要么接受搜狗不稳定，要么付费买商业聚合，要么放弃「不依赖 App」用搜一搜。

**下一步（待用户定方向后展开）**：
- 用户选定方向后，输出对应设计文档（搜狗 MCP 接入 / 新榜 API 接入 / 搜一搜 App 自动化），走 [架构扩展点](../../.claude/rules/architecture.md) 添加工具流程。
- 若选商业聚合：先联系新榜/西瓜/清博商务拿报价 + API 文档，评估覆盖范围与调用配额。
- 若选搜狗 MCP：先本地跑通 Weixin Search MCP，实测反爬触发频率与正文获取成功率，再决定是否值得包装成稳定工具。

---

## 来源

- [微信搜一搜运营页（非搜索框）search.weixin.qq.com](https://search.weixin.qq.com/)
- [搜一搜数据推送接口 submitPages（不支持第三方，且为推送非搜索）](https://developers.weixin.qq.com/miniprogram/dev/server/API/wxsearch/api_submitpages.html)
- [微信公众平台发布能力（仅限自有公众号）](https://developers.weixin.qq.com/doc/service/guide/product/publish.html)
- [搜狗微信搜索 weixin.sogou.com](https://weixin.sogou.com/) / [反爬验证码页 antispider](https://weixin.sogou.com/antispider/)
- [wechat-download-api（公众平台后台接口自建，MCP，877⭐）](https://github.com/tmwgsicp/wechat-download-api)
- [WechatSogou（搜狗爬虫库）](https://github.com/chyroc/WechatSogou)
- [Weixin Search MCP（基于搜狗）](https://lobehub.com/zh/mcp/fancyboi999-weixin_search_mcp)
- [聚合数据 微信文章精选 API](https://www.juhe.cn/wiki/wxwzjxapijk560) / [极速API 公众号搜索](https://m.jisuapi.com/api/weixin/) / [TianAPI 微信文章精选（已停更）](https://www.tianapi.com/apiview/1)
- [阿里云 item_search 文章搜索封装](https://developer.aliyun.com/article/1691079) / [item_get 文章详情](https://developer.aliyun.com/article/1691072)
- 搜狗反爬机制参考：[知乎爬虫架构](https://zhuanlan.zhihu.com/p/46781178)、[腾讯云搜狗微信爬虫案例](https://cloud.tencent.cn/developer/article/1953105)、[Python3 网络爬虫开发实战（代理爬公众号）](https://cuiqingcai.com/7844.html)
