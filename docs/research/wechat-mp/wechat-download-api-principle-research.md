# wechat-download-api 工作原理调研（源码级分析）

> 调研日期：2026-09-16。对象：github.com/tmwgsicp/wechat-download-api（AGPL 3.0，FastAPI，浅克隆快照在 `.tmp/wechat-download-api/`，约 7900 行）。
> 核心问题：**为什么只要知道公众号，它就能拿到该号的历史文章清单和内容？方法能否自研借鉴，还是必须用它的 Docker 兜底？**
> 结论先行：**方法可自研借鉴（非独门秘籍）**——它本质是「管理员扫码登录公众平台网页后台，调用后台自带的『搜公众号 + 看任意号发表记录』接口」。没有任何私有协议或加密逆向。建议自研清单适配器；其 Docker 服务降级为实验对照与应急选项。

## 1. 原理一句话

把「公众号管理员扫码登录 mp.weixin.qq.com」得到的**网页会话（Cookie + token）**，用于调用公众平台后台已有的两个内部 CGI 接口：`searchbiz`（按名称搜出任意公众号的 fakeid）和 `appmsgpublish`（按 fakeid 分页翻该号的全部「发表记录」）。清单里的 `link` 是公开文章长链，正文抓公开页面解析即可（我们的约束是托底源**只出清单**，正文一律走自家 URL 直采管道，见设计 §13.3 统一约束）。

这就是运营者在后台「超链接 → 查找文章」UI 里手动做的事，该项目只是把它 API 化了。同源方法在 wechat-article-exporter 等多个开源项目独立实现，属公共知识。

## 2. 完整获取流程（5 步，含源码证据）

### 2.1 扫码登录 → 会话（routes/login.py, utils/auth_manager.py）

1. `POST /cgi-bin/bizlogin?action=startlogin` 初始化登录会话（login.py:73-112）；
2. `GET /cgi-bin/scanloginqrcode?action=getqrcode` 取二维码图片（login.py:154-232）；
3. `GET /cgi-bin/scanloginqrcode?action=ask` 轮询扫码状态，`status=1` 表示已确认（login.py:288-340）；
4. `POST /cgi-bin/bizlogin?action=login` 完成登录：从响应 `redirect_url` 的 query 里**提取 token**，把浏览器全程累积的 Cookie 与响应 Set-Cookie **合并**为最终凭证（login.py:340-420）；
5. 凭证 `{token, cookie, fakeid, nickname, expire_time(毫秒)}` 存 `data/.credentials.json`（auth_manager.py:88-152）。**会话约 4 天过期**；失效信号为 `ret=200003 invalid session` / `ret=200040 invalid csrf token`（utils/wechat_status.py:21-28），配 webhook 提前 24h/6h 预警重新扫码（utils/login_reminder.py）。

注意：它的凭证是**明文 JSON 落盘**——这一点**不可借鉴**，我们已有 `config_codec` Fernet 字段加密基建，会话必须按敏感字段加密存储。

### 2.2 搜号 → fakeid（routes/search.py:51-114）

`GET https://mp.weixin.qq.com/cgi-bin/searchbiz?action=search_biz&token=..&query={公众号名称}&begin=0&count=5`，Header 带 `Cookie` + 浏览器 UA。响应 `list[]{fakeid, nickname, alias, round_head_img, service_type}`。**"只要知道公众号"就够的原因在此：名称 → fakeid。**

### 2.3 清单分页（routes/articles.py:84-200）

`GET https://mp.weixin.qq.com/cgi-bin/appmsgpublish?sub=list&begin={N}&count={≤100}&fakeid=..&type=101_1&free_publish_type=1&sub_action=list_ex&token=..&f=json&ajax=1`，Header 带 `Cookie` + `Referer: https://mp.weixin.qq.com/`。

响应：`publish_page.publish_list[].publish_info`（**JSON 字符串**需二次解析）→ `appmsgex[]{aid, title, link(长链), update_time, create_time, digest, cover, author}`，顶层 `publish_page.total_count`。搜索变体：`sub=search&search_field=7&query=关键词` 号内搜索（articles.py:85-99）。

### 2.4 增量与去重（utils/rss_poller.py:28-30, 98-140; utils/rss_store.py:114, 227）

轮询每小时（POLL_INTERVAL=3600）对每个订阅号只拉**最新一页**（begin=0, count=10），SQLite `UNIQUE(fakeid, link)` + `ON CONFLICT DO UPDATE` 去重；`ret=200002 + "invalid args"` 判定 fakeid 失效（注销/改名）自动拉黑（rss_poller.py:121-137; wechat_status.py:31-33）。**历史全量由调用方自行递增 begin 翻页**（它的 /articles 接口直接暴露 begin 参数）。

### 2.5 正文抓取（utils/article_fetcher.py, utils/http_client.py, utils/helpers.py）

- 文章页公开可访问；它在 URL 上**追加 token 并带 Cookie + Referer=mp.weixin.qq.com**，伪装成后台用户访问以提升稳定性（article_fetcher.py:64-72）；
- `curl_cffi impersonate="chrome120"` 模拟 **Chrome TLS 指纹**，SOCKS5 代理池轮转（失败冷却 120s）→ 全部失败直连兜底（http_client.py:93-116, proxy_pool.py）；
- 解析 `js_content`，对特殊类型有专用分支：item_show_type=7 音视频分享页（动态 Vue，只能拿元数据）、=8 图片消息（picture_page_info_list）、=10 短内容/转发（content_noencode）、mpvoice 音频（helpers.py:533-698）；
- **验证页识别**：「环境异常 + 完成验证后即可继续访问 + 去验证」三特征命中 = IP 风控，**不算文章不可用**，换代理重试（helpers.py:738-743）；
- **永久不可用判定表**：删除/违规/隐私/辟谣等 8 种文案特征 + 体积启发式（>1MB 且有正文容器则视为 JS 内字符串防误判；<2KB 独立小页判定；空 Vue app 判定）（helpers.py:746-803）。

## 3. 反风控体系分层（谁在防谁）

| 层 | 机制 | 防的对象 |
|----|------|---------|
| 清单 CGI（searchbiz/appmsgpublish） | 无代理无 TLS 伪装，仅 Cookie+UA+Referer，httpx 直连 | 相对宽松：会话 Cookie 是主要信任凭证 |
| 正文公开页 | Chrome TLS 指纹 + SOCKS5 代理池轮转 + 文章间隔 ≥3s | 主战场：IP/TLS 风控（验证页） |
| 登录态 | ~4 天过期 + 失效信号识别 + 过期预警 webhook | 会话生命周期 |
| 入站限频（全局 10/min、单 IP 5/min） | rate_limiter.py | **自身用户**，与微信无关 |

关键观察：**清单接口本身风控压力小**（会话信任），风控成本集中在正文页——而正文我们走自家 URL 直采管道（WP3 已有多信号删除判定与 risk_blocked 语义），托底源自研面只需清单部分，风控面大幅缩小。

## 4. 与 freepublish 接口通道（WP9）的关系

| | freepublish batchget（WP9 已上线） | appmsgpublish（本方案） |
|---|---|---|
| 鉴权 | 服务端凭据 appid/secret + IP 白名单 | 管理员扫码网页会话（~4 天续命） |
| 覆盖 | 仅「发布」渠道；**WP0 实测群发不进集合** | 后台「发表记录」视图，社区口径含群发历史——**待 WPS 实测证实（核心假设）** |
| 稳定性 | 高（官方 API，错误码可预期） | 中（账号风控、会话过期、验证页） |
| 定位 | 自有号主通道 | 托底/历史回补 |

## 5. 可借鉴性判定：方法可自研，非独门秘籍

1. **核心是公众平台自身后台接口**（官方 UI 能力），非该项目私有协议/逆向成果；无加密、无签名算法、无混淆参数。
2. **全部"壁垒"有开源替代**：curl_cffi（MIT 许可）、httpx、SOCKS5 代理、限频/去重都是普通工程件。
3. **AGPL 边界**：接口名、参数、响应结构是协议事实，阅读行为后独立实现不受 AGPL 传染；**不复制其代码**（同为 Python，更须只学协议不抄码）。直接用其 Docker 镜像对外提供 SaaS 能力则涉及 AGPL 义务，需法务过目（设计 D12 已有此结论）。
4. **适配成本极低**：清单 `link` 是长链（__biz/mid/idx/sn），`identity.py` 直接消费出规范身份；增量语义复用 WP9（update_time vs `wx_update_time`、未变跳过）；会话可存租户渠道配置（`config_codec` 加密基建现成）。

## 6. 结论与建议

**自研清单适配器为主路径（借协议不借代码），Docker 服务不作产品依赖**，理由：

1. 方法非独家，自研无技术不确定性；
2. 自研直接汇入既有租户串行队列/身份/入库/复核/计费全链路；引 Docker 会产生第二套状态与口径（且其正文接口与"统一 URL 直采"约束冲突，只能用它的清单接口）；
3. 会话归属与风控爆炸半径：自研可按租户维度加密存会话；Docker 模式单号会话共享全平台，一个号被限全平台停摆；
4. AGPL 隔离干净。

**扫码会话归属建议 WPS 实验双模式各验一遍**：A. 平台运营号统一扫码（现 D12 口径，风控集中、需代理池）；B. 租户管理员各自扫码（谁扫码谁担风控，合规面最小，凭证进租户配置）。若 B 可行，优先 B。

**Docker 服务的保留定位**：WPS 实验阶段先拿它做 3+ 号的历史完整性/群发覆盖实测（最快出结论，反哺自研）；生产上仅当自研被风控压制时再评估独立部署兜底。

## 7. 自研清单适配器契约草案（对齐 WPS 产出要求）

- **输入**：会话（token+cookie，Fernet 加密存储，含 expire_time 与失效信号）+ 公众号标识（名称→searchbiz→fakeid，结果缓存；或租户直接提供 fakeid）。
- **输出清单项**（对齐 WP9 articles 语义）：`[{link(长链→identity.py 身份), title, author, digest, cover, aid, publish_time(=create_time), update_time}]`。
- **游标/状态**：全量=begin 递增至 total_count（重复页/总数漂移检测，对齐 WP9 client 的 reliable 语义）；增量=begin=0 最新页+身份去重；状态枚举 `complete/total_drift/duplicate_page/session_expired/risk_blocked`。
- **失效信号映射**：ret 200003/200040→session_expired（触发重新扫码引导，渠道三态加"会话过期"展示）；200002+invalid args→fakeid 失效拉黑。
- **限频**：清单请求 ≥1~3s 间隔 + 每 tick 每号上限（复用 tick 限速风格）；验证页/会话失效不入退避风暴。

## 8. WPS 实验清单（按本调研具体化）

1. **群发覆盖验证（核心假设）**：找 1 个只群发不发布的号，对比 batchget 与 appmsgpublish 的集合差异；
2. 历史分页完整性：begin 翻页到底，核对 total_count 与实际条数，观察重复页/漂移；
3. count 真实上限实测（其 API 层限 100 是自家封装，微信侧真实上限待测，可能为 20）；
4. 会话生命周期：4 天过期实测、200003/200040 复现、「环境异常」验证页触发频率与解除方式；
5. 清单接口安全频率观测（QPS/日配额，触发风控的阈值）；
6. 扫码模式 A/B 各验证一遍（含会话加密存储与到期重新扫码的运营动线）。

## 9. 证据索引（相对 .tmp/wechat-download-api/）

- 清单接口与参数：routes/articles.py:84-113；searchbiz：routes/search.py:62-80
- 登录流程：routes/login.py:24-30, 73-112, 154-232, 288-340, 340-420
- 凭证存储/过期：utils/auth_manager.py:38-44, 88-152, 179-211；utils/wechat_status.py:21-33；utils/login_reminder.py
- 增量轮询/去重：utils/rss_poller.py:28-30, 98-140；utils/rss_store.py:114, 227
- TLS 指纹/代理池：utils/http_client.py:93-116；utils/proxy_pool.py:26-97
- 验证页/不可用判定：utils/helpers.py:715-803
- 正文类型解析：utils/helpers.py:533-698
