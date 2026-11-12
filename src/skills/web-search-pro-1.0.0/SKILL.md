---
name: web-search-pro
description: >
  可移植的网页检索与内容提炼工具集：多引擎智能路由检索（百度/搜狗/360/Bing/Naver 等）、
  页面正文抓取、邮箱电话等结构化字段抽取，以及「检索→抓取→内容级摘要」组合管线。
  适用于需要批量抓取网页、调研企业与联系人信息、平台 web_search 摘要不够深入
  需要页面级信息（官网联系方式、邮箱、电话）的场景。
version: 1.1.0
metadata:
  openclaw:
    emoji: "🔍"
    requires:
      bins: ["python"]
---

# web-search-pro —— 多引擎检索与联系人抽取管线

## 定位（与平台工具的分工）

平台内置 `web_search` / `web_fetch` 工具是**首选通道**：`web_search` 快速拿摘要、
`web_fetch` 直读已知 URL。但它们有两个边界：

1. `web_search` 只返回搜索摘要（snippet），**没有页面级邮箱/电话抽取**；
2. 无多引擎路由，海外/垂直场景（site: 国别、companies-only 过滤、掩码号码还原）覆盖不到。

本技能是**深挖兜底通道**：

| 能力 | 平台工具 | 本技能 |
|---|---|---|
| 快速摘要检索 | web_search（首选） | 兜底 |
| 页面级联系人抽取（邮箱/电话/负责人） | 无 | `pipeline.py --extract` 一次完成 |
| 多引擎智能路由 + 降级链 | 无 | 百度/搜狗/360/Bing 等 + 全球备份 |
| JS 站渲染兜底 | 无 | render.py（需 Playwright） |
| 掩码号码按位合并还原 | 无 | `recovered_phones` |

**典型用法**：先 `web_search` 确认方向，需要**从页面里挖邮箱/电话**时改用本技能
`pipeline.py search "<关键词>" --extract`——检索、抓页、抽取一次完成，
零 LLM 依赖，输出结构化 JSON 供你直接消费入库。

## 环境与执行方式

- 通过 `skill_execute` 执行，命令中写 `scripts/<脚本名>` 相对路径即可，
  系统会自动替换为技能目录绝对路径，无需 cd。不要写裸文件名（会解析到临时目录）
  或 `/app/...` 绝对路径（会被二次拼接）。
- 依赖 `httpx` / `beautifulsoup4` / `lxml` 已随容器镜像安装。
- `render.py` / 管线渲染兜底需要 Playwright，**当前容器不可用**（2026-11-07 实测：
  包可导入但 chromium 打开页面即 `Page crashed`，共享内存不足）：渲染路径会尝试
  然后失败降级，不影响 `search` / `fetch` / `extract` 和管线的非渲染路径。
  不要主动走渲染兜底；JS 站改由平台 web_fetch 或浏览器工具处理。
- **百度通道代理（可选）**：环境变量 `WEBSEARCH_BAIDU_PROXY`
  （形如 `http://user:pass@host:port`，支持逗号分隔多个出口）设置后，baidu / baidu_m
  通道经代理执行并逐次轮换出口（用于服务器 IP 被百度风控标记时改走外部宽带/办公网出口）；
  代理连接失败自动回落直连并在 errors 中如实记录（只记 host:port，不带凭据），
  失败出口进程级熔断 10 分钟，轮换自动避开，未设置或全部熔断中则直连。
  百度通道已内置 cookie 预热（先取 BAIDUID 再搜索，缓存按出口隔离），
  降低无 cookie 裸搜被风控的概率。

## 本环境引擎可用性（2026-10-27 服务器实测）

| 通道 | 可用性 | 说明 |
|---|---|---|
| baidu / baidu_m | ⚠️ 动态风控 | 服务器 IP 已被标记时经代理通道执行（见上节） |
| so(360) | ✅ | 中文主力通道 |
| bing | ✅ | 海外与 site: 国别路由主力 |
| sogou | ❌ HTTP 399 | 反爬拦服务器 IP |
| google / ddg | ❌ | 服务器网络不可达 |

- `site:` 国别域名只用于路由，**必须写在 query 字符串里**（如
  `"bathroom fixtures importer site:.de"`），不要拆成独立参数。
- 自动路由失败时指定 `--engine bing`（海外）或 `--engine baidu`（中文）重试。

## 脚本一览

| 脚本 | 用途 |
|---|---|
| `scripts/web_search.py` | 检索 / 抓取 / 字段抽取 / 批量抓取（基础层） |
| `scripts/render.py` | 无头浏览器渲染兜底（当前容器不可用） |
| `scripts/pipeline.py` | 组合管线：检索→抓取→内容级摘要（核心） |
| `scripts/efficiency.py` | 请求节流 / 风控冷却 / LLM 响应缓存（状态落盘，跨进程生效） |

## 工作流 1：检索网页

```bash
python scripts/web_search.py search "卫浴 进出口贸易 有限公司" --limit 8 --json
python scripts/web_search.py search "bathroom fixtures importer site:.de" --companies-only --json
```

- **引擎自动路由**：中文→百度系；韩文→Naver；日文→Yahoo!Japan；西里尔→Yandex；
  其他→Bing。区域主引擎之后始终追加全球备份，任一失败自动降级。
- **`site:` 国别域名**强制区域引擎（`site:.cn`→百度系），发往非 Bing 引擎前自动剥离。
- **`--companies-only`**：硬过滤词典/百科/翻译/社媒/黄页/电商域名，挖客户名单用。
- **搜信息**（默认）：噪音域名不丢弃，作为备选排在干净结果之后。
- 参数：`--limit`（默认 8）、`--engine`、`--companies-only`、`--json`。

## 工作流 2：抓页面正文

```bash
python scripts/web_search.py fetch "https://example.com/contact" --json
```

HTML → 正文（剔除 script/style/nav/footer 等）。比平台 web_fetch 多一层正文清洗，
适合后续做邮箱/电话扫描。

## 工作流 3：抽结构化字段（邮箱 / 电话 / 负责人）

```bash
python scripts/web_search.py extract "https://example.com/about"
python scripts/web_search.py batch sites.txt               # 批量：默认抽结构化字段
```

- **邮箱**：优先 `mailto:` 链接，其次页面正则。
- **电话**：`tel:` 链接、带「电话/热线/Tel」标签的同行文本、号码前置排版里的
  完整手机号/400 号/带区号固话。不做无约束的全文数字扫描。
- **负责人**：中文称谓 + 英文职位模式匹配，弱信号，须结合页面上下文核对。
- 批量参数：`--full-text`、`--delay`（默认 1.5s）、`--min-domain-interval`
  （按目标域名额外限速，默认 0=关闭；对访问过快会触发验证的站点建议设 2.0）。

## 工作流 4：组合管线（★ 联系人挖掘核心）

一次完成「检索 → 抓取 → 必要时渲染 → 结构化抽取」。

```bash
# 采集模式：检索 + 抓取 + 结构化字段（零 LLM 依赖，获客挖掘用这个）
python scripts/pipeline.py search "爱定义（上海）科技有限公司" --top 3 --extract

# 直接给 URL，跳过检索
python scripts/pipeline.py urls "https://a.com,https://b.com" --extract
```

> `pipeline.py` 只有 `search` / `urls` / `summarize` 三个子命令，输出**恒为 JSON**，
> **没有 `--json` 参数**；单页抓取用 `web_search.py fetch`。
> 用 shell 重定向落盘时先看退出码：命令失败（非 0）时输出文件可能为空，
> 不要直接 `json.load` 空文件，先读 stderr 定位原因。

**快照优先 + 确认即停（默认行为）**：管线先对每条检索结果的摘要/snippet 跑联系方式
抽取（`snippet_findings` 第一优先）——企查查/天眼查等详情页常被 405/登录墙挡住，
先读摘要是最快路径。摘要里确认到**未掩码的电话或邮箱**即判定准确并提前停止
（`early_stop=true`），不再抓其它页面。需强制抓页加 `--no-snippet-stop`。

**反分词漂移（默认行为）**：中文公司名常被引擎切碎误命中无关公司。处理方式：
- 保留查询原文（含全角括号）直发引擎，不改写；
- 品牌核（如「爱定义」）作为必含项硬过滤；
- 同引擎漂移自动重试（最多 4 次尝试）；
- 全部漂移时返回 0 条而非错误公司——**宁可空、绝不假**。

**掩码号码还原（`recovered_phones`，`--extract` 时启用）**：企业信息平台不同页面
打码窗口不同（`1391680****` / `139****7580` / `139******80`），管线自动按位合并
多页掩码还原完整号码，给出 `confidence` 与合并来源；位冲突判为不同号码，
信息不足不下结论。

> ⚠️ **合并结果是推断值，必须人工核对。** 算法只校验「长度一致 + 已知位不冲突」，
> 无法证明两个掩码来自同一个号码——两个**不同**号码只要掩码窗口互补
> （如 `139****7580` + `1391234****`），会拼出看似完整、实际不存在的号码且仍标
> `confidence=high`。输出已带 `basis: "mask_merge_inference"` 与 `verification`
> 提醒字段；**下游请勿把 `recovered_phones` 当作已确认事实直接写入客户库或外发**。

### 效率与风控（v1.1.0）

**本环境固定走采集模式**（不配 `WSP_LLM_*`），技能内部零 LLM 调用，正文由调用方
智能体总结。以下能力主要服务于「少打目标网站、少走重复链路」：

- **跳转解析三重保护**：SERP 里每条 `…/link?url=` 不再零间隔连发 HEAD——同一 URL
  进程内只解析 1 次（重试结果集高度重叠）、每次补 ≥0.35s 同组间隔、单次搜索上限
  40 条、临时失败不缓存。这是「访问太快触发验证/临时禁用」的主因修复。
- **命中验证码 → 同厂商组冷却 20s**：baidu / baidu_m / *.baidu.com 同组；PC 被拦后
  不立刻打移动端继续加压（只会从验证码升级为禁用）；sogou / bing 等**不同组**的
  降级通道照常可用。`baidu_m` 是唯一例外：有**一次冷静重试**——等冷却自然走完
  （期间零请求）后再试，上限 25s 且受搜索总预算约束。`WSP_BLOCK_COOLDOWN=0`
  可关闭冷却。冷却只作用于搜索引擎通道，不误伤用户明确指定的目标 URL 抓取。
- **代理通道与冷却共存**：百度代理（`WEBSEARCH_BAIDU_PROXY`，多出口轮换）执行时
  命中风控同样触发整组冷却；代理连接失败熔断 10 分钟 + 回落直连的行为不变。
- **编码修复**：响应头声明 iso-8859-1 或未声明 charset 的 GBK/GB18030 中文页
  不再整页乱码（HTML meta 覆盖 → strict 失败回退 GB18030 → replace 兜底）。
- **长单行电话抽取修复**：搜索引擎 snippet 常是整段无换行，旧逻辑对 >200 字的
  行整行跳过会漏掉全部电话；现按 200 字窗口（步长 180 重叠）扫描。

全部开关（环境变量，默认值即更保守）：`WSP_CACHE_DIR`、`WSP_MIN_REQUEST_INTERVAL`(0)、
`WSP_REDIRECT_INTERVAL`(0.35)、`WSP_MAX_REDIRECTS`(40，0=不限)、
`WSP_BLOCK_COOLDOWN`(20)、`WSP_DOMAIN_MIN_INTERVAL`(0)、`WSP_LLM_CACHE_TTL`(86400)。

**LLM 摘要**：`WSP_LLM_BASE_URL` 等环境变量未配置时自动降级为**采集模式**
（`mode: "collect"`），输出 `pages[].text` 结构化正文由你自行总结——本环境固定走
采集模式，不要尝试配置 LLM。

输出 JSON 关键字段：`mode`、`pages[]`（含 `emails`/`phones`/`persons`/`meta`/
`masked_phones`）、`serp[]`、`snippet_findings`、`recovered_phones`、`errors`、`hint`。

## 检索纪律（配合 ai-lead-gen 等批量场景）

- 搜索通道**串行**执行，连续失败时先换关键词/引擎，不要连续重试同一 query；
- 命中验证码后**不要立刻换通道重试同组引擎**——整组冷却中，等冷却走完或换厂商；
- 官网页面间隔 ≥ 1.2s（管线 `--delay` 默认已带）；
- 相关性闸门（`relevance_ok`）**不得放宽**——宁可 0 条，不收无关结果。

## 关键坑位速查

1. `site:` 必须在 query 字符串内；发往百度等引擎前会被脚本自动剥离。
2. 百度结果链接是 `…/link?url=…` 跳转，脚本自动 HEAD 跟随还原真实 URL。
3. Bing 跳转链接 base64 已自动解码。
4. 代理/缓存污染会返回完全无关内容——脚本已按相关性丢弃，但成功率受限；
   此时优先 `pipeline.py urls` 直抓已知页面。
5. 渲染 ≠ 绕过反爬：带滑块验证码的站点（爱企查等）仍会拦自动化浏览器，
   本环境渲染路径不可用，改用平台 web_fetch 或浏览器工具。
6. 超时护栏：连接 8s / 读取 15s / 单引擎重试 1 次 / 整次搜索 75s 预算。

更多细节见 `references/retrieval-notes.md`。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 正常，有结果 |
| 2 | 网络全部失败 / 未取到结果 / 渲染失败 |
| 3 | 参数错误 |
| 4 | Playwright 不可用（仅 render.py） |

## 与其他技能配合

- **ai-lead-gen**：其 `SEARCH_TASK` / `SCRAPE_TASK` 委托协议的第一级执行通道（默认）——
  名单检索、官网定位、联系人挖掘一律直接用本技能管线
  （`pipeline.py search "<query>" --extract` / `urls` 模式），
  平台 web_search/web_fetch 仅在管线全通道失败时兜底
  `pipeline.py --extract` 执行，JSON 结果直接消费入库。
