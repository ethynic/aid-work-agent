# 检索层 —— 搜索与爬取的委托协议

本文件定义 b2b-lead-gen 技能**如何拿到互联网信息**。名单检索到发送各步的全部产出
（客户清单、官网情报、联系人邮箱）都依赖这一层。

> **重大变更（v3.0.0）**：搜索与爬取逻辑已从本技能剥离为独立技能
> `web-search-pro`（多引擎检索 + 页面抓取 + 联系人抽取管线）。
> 本技能不自带检索实现，而是**委托**外层智能体按降级链检索，
> 拿到结果后继续编排。本文件只定义委托协议与判定口径，不重复实现细节。

---

## 一、能力归属（谁能打包，谁不能）

| 能力 | 归属 | 在本流程中的角色 |
|---|---|---|
| `web-search-pro` | 独立技能 | **默认通道**：多引擎检索 + 页面级联系人抽取管线 |
| 平台 `web_search` / `web_fetch` 工具 | 平台内置 | 兜底：管线全通道失败时换通道搜摘要/补抓单页 |
| `agent-browser` | 独立技能 | JS 渲染站 / WAF / 交互页兜底 |

**结论**：搜索/爬取的深挖实现由独立技能承载。本技能专注于编排：
决定"搜什么 / 爬谁 / 怎么判 / 怎么发"，检索执行按降级链委托。

---

## 二、委托协议（Delegation Protocol）—— 核心机制

本技能**不自己搜索、不自己爬**。当流程走到需要检索/爬取的步骤时，按下面的规矩：

1. **产出任务块**：用下方 `SEARCH_TASK` / `SCRAPE_TASK` 的 JSON 结构，明确写出
   要搜什么 / 爬哪个 URL / 按什么口径判。
2. **停下来等待**：明确告诉用户（= 外层智能体）：
   > "请用 `web-search-pro` 技能的管线（skill_name=web-search-pro 发起 skill_execute）
   > 执行上述 `SEARCH_TASK` / `SCRAPE_TASK`，把结果贴回；管线全通道失败时
   > 改用平台 `web_search` / `web_fetch` 兜底。
   > 收到结果前，我不会继续第 3→8 步的后续动作。"
3. **消费结果续跑**：外层智能体返回结果后，本技能用 `scripts/lead_db.py` 把
   客户清单 / 联系人写入 CSV，继续后续撰写与发送。

> 为什么这样设计：搜索与爬取依赖网络与可分发实现，剥离成独立技能后，
> 平台工具与 `web-search-pro` 分层兜底；外层智能体统一掌握检索通道
> （内置工具或技能），避免每个获客技能各带一份抓取实现。

### SEARCH_TASK（找客户清单 / 找官网）

```json
{
  "task": "search",
  "query": "\"bathroom fixtures\" importer site:.ae",
  "region": "AE",
  "limit": 10,
  "companies_only": true,
  "purpose": "第3步：找卫浴进口商客户清单"
}
```

- `query`：搜索词（模板见 `references/lead-search-playbook.md`）。
- `region`：目标国家/地区（决定搜索引擎与语言，见 `references/search-engine-map.md`）。
- `companies_only`：过滤社媒/黄页/电商域名（挖名单时用）。
- 结果：一组 `{title, url, snippet}`，由本技能用 `lead_db.py add-lead` 入库（去重）。

### SCRAPE_TASK（找官网联系人 / 写情报）

```json
{
  "task": "scrape",
  "url": "https://example.com",
  "company": "ABC GmbH",
  "country": "DE",
  "target_functions": ["purchasing", "engineering", "production"],
  "purpose": "第7-8步：抽取官网情报与对口联系人邮箱"
}
```

- `url`：**已确定的官网 URL**（先由 `SEARCH_TASK` 或用户给出，检索管线不负责找官网）。
- `target_functions`：本次产品对应的对口职能（决定哪些邮箱算有效目标；可 `null` 不限制）。
- 结果：抓取/抽取返回的 JSON（含 `emails` / `phones` / `persons` 等字段，见
  `web-search-pro` 技能 `pipeline.py --extract` 输出），
  由本技能按职能口径筛选后写库。

---

## 三、降级链（严格按顺序）

```
第一级：web-search-pro 技能（pipeline.py --extract 一站式管线，默认通道）
    │ 全引擎/抓取失败、被判无关
    ↓
第二级：平台 web_search / web_fetch 工具（换通道搜摘要 / 直读已知 URL）
    │ 仍失败 / JS 渲染空壳
    ↓
第三级：agent-browser 技能（真实浏览器，处理 JS/WAF/交互）
    │ 站点需登录、需交互、有 WAF
    ↓
放弃并如实上报：把该客户标记 blocked / needs_manual，不要编造
```

**为什么这样分级**：web-search-pro 的管线一次完成检索→抓页→邮箱/电话抽取
（snippet 优先 + 掩码还原），纯 HTTP 零 LLM 消耗，且不让大段搜索摘要进对话
上下文——平台 web_search 只返回摘要，历史实测 23 次搜索仅换 4 个邮箱，
只作兜底；JS 渲染站（WAF、多语言站群）→ 浏览器技能唯一可靠。

---

## 四、邮箱类型与相关性判定（三道闸门共用口径）

邮箱判定由本技能 `scripts/email_rules.py` 统一负责，入库时强制执行同一口径。
**形态**（type）与**相关性**（relevance）是两个维度，必须一起看：

| 形态 type | 判定依据 | 相关性 relevance | 是否入发送队列 |
|---|---|---|---|
| `personal` | 本地部分形似人名 | `relevant` | 入，称呼前需核验姓名对应 |
| `department` | 职能词（`ceo@`/`purchasing@`/`buyer@`） | `relevant`（对口头）或 `irrelevant`（无关职能） | 仅 `relevant` 入 |
| `generic` | 公开窗口（`info@`/`contact@`/`office@`/`support@`） | `generic` | **不入**（无具体负责人） |
| `department` | 无关职能（`hr@`/`jobs@`/`legal@`/`press@`） | `irrelevant` | **不入**，须换路径继续找 |
| `unknown` | 形态不明 | `unknown` | 不入，需人工核对 |
| 邮件列表/归档 | `lists.*`/`debian-*`/`noreply@` | — | **不入** |
| 软 404 / 空 | 正文过短或页面无邮箱 | — | 不入，改走浏览器技能复核 |

**只有 `relevance == "relevant"` 的地址才是有效目标邮箱。**
`relevant` = 本地部分指向一个与当前产品/服务对应的负责人或部门；
`management`（CEO/老板）与个人邮箱恒定对口，不受白名单限制。

| 判定 | 结论 |
|---|---|
| `purchasing@` `export@` `production@` `engineering@` `ceo@` | ✅ 对口职能，有效 |
| `maria.schmidt@` `j.doe@` | ✅ 个人企业邮箱，有效（需核验姓名） |
| `info@` `contact@` `office@` `hello@` | ❌ 公开窗口，无负责人 |
| `hr@` `jobs@` `careers@` | ❌ 招聘邮箱，与推销无关 |
| `legal@` `finance@` `press@` `marketing@` | ❌ 无关职能部门 |
| `noreply@` `no-reply@` | ❌ 自动应答 |

**campaign 可用 `contact_targets.json` 声明本次产品对应的部门**，把不对口的职能也降为
`irrelevant`（如卖工业件时 `sales@` 也不是目标）。详见 `references/contact-discovery.md` 第一节。

**铁律**：`personal` 只是"形似人名"的弱信号，**不等于该邮箱属于某个人**。
写入 CSV 后必须核对官网页面上邮箱与姓名的邻近关系，才能在邮件里写称呼。
拿不准就用 `department` + "您好："，这比写错称呼安全得多。

**官网只有公开窗口或无关职能邮箱时，不构成 `no_email` 的理由** ——
必须按 `contact-discovery.md` 路径 2→5 继续找对口邮箱。

---

## 五、并发与限速（委托时的纪律）

搜索引擎和官网都会对高频请求做风控。委托外层智能体执行时仍须遵守：

- 搜索通道：串行，失败按退避重试（web-search-pro 内部 1.5s / 3s，最多 2 次）
- 官网巡检：页面间隔 ≥ 1.2s（web-search-pro 批量模式 `--delay` 默认已带）
- 批量子任务（找客户清单）：**并发不超过 3**，分组不超过 25 家
- 触发频控时：换关键词、换语言、加 `--engine bing`，或降级到浏览器技能

---

## 六、搜索质量的四个实测坑

这几条是真实项目里踩出来的。检索实现细节现收录在 `web-search-pro` 技能的
`references/retrieval-notes.md`，此处只列结论：

1. **Bing 会把英文 query 当单词查词典** → 按 query 语言自动切市场。
2. **Bing 结果 URL 是 base64 跳转链接** → 必须解 base64 还原真实地址。
3. **代理污染会返回完全无关的结果**（查土耳其卫浴返回"广州地铁时刻表"）→
   结果相关性低于阈值整批丢弃，**宁可失败也不放行无关结果**（`relevance_ok`）。
4. **中文网页的负责人字段极易误命中网页文案** → 落库前过形态闸门，避免把文案当人名。

---

## 七、失败时的动作

| 症状 | 判定 | 动作 |
|---|---|---|
| 搜索返回词典/百科页 | 市场语言错配 | 换 query 语言或引擎，重发 SEARCH_TASK |
| 结果与 query 无关 | 代理/缓存污染 | 丢弃，重发或降级浏览器技能，绝不入库 |
| 官网抓到空壳、无邮箱 | JS 渲染 | 改走 agent-browser 打开 contact 页 |
| 连续路径 404 | 官网结构非预设 | 用 web-search-pro `fetch` / `pipeline.py urls` 直抓真实路径 |
| 官网无联系页 | 小微企业 | 转 SCRAPE_TASK 其他来源 / 路径 2→5 |

**任何一种失败都不要编造邮箱。** 空着就是空着，在 `leads.csv` 标注
`reason=no_contact_page` 是正确做法，编一个通用邮箱是严重错误。

---

## 相关文件

- `references/search-engine-map.md` —— 各国搜索引擎与行业名录（第 2 步）
- `references/lead-search-playbook.md` —— 搜索词模板（构造 SEARCH_TASK.query）
- `references/contact-discovery.md` —— 联系人查找与邮箱验证
- 外部技能 `web-search-pro`（`references/retrieval-notes.md`）—— 深挖管线的实现细节与本环境引擎可用性
- `scripts/email_verify.py` —— 邮箱格式与 MX 校验（本技能保留）
- `scripts/lead_db.py` —— 客户/联系人的 CSV 入库存取（本技能保留）
