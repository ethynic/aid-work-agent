---
name: web-search-pro
description: >
  可移植的网页检索与内容提炼工具集：多引擎智能路由检索（百度/搜狗/360/Bing/Naver 等）、
  页面正文抓取、邮箱电话等结构化字段抽取，以及「检索→抓取→内容级摘要」组合管线。
  适用于需要批量抓取网页、调研企业与联系人信息、平台 web_search 摘要不够深入
  需要页面级信息（官网联系方式、邮箱、电话）的场景。
version: 1.0.0
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
- `render.py` / 管线渲染兜底需要 Playwright，**当前容器未安装**：渲染路径会以
  退出码 4 退出并提示，不影响 `search` / `fetch` / `extract` 和管线的非渲染路径。
  不要主动走渲染兜底；JS 站改由平台 web_fetch 或浏览器工具处理。
- **百度通道代理（可选）**：环境变量 `WEBSEARCH_BAIDU_PROXY`
  （形如 `http://user:pass@host:port`）设置后，baidu / baidu_m 通道经该代理执行
  （用于服务器 IP 被百度风控标记时改走办公网出口）；代理连接失败自动回落直连
  并在 errors 中如实记录，未设置时直连。百度通道已内置 cookie 预热
  （先取 BAIDUID 再搜索），降低无 cookie 裸搜被风控的概率。

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
- 批量参数：`--full-text`、`--delay`（默认 1.5s）。

## 工作流 4：组合管线（★ 联系人挖掘核心）

一次完成「检索 → 抓取 → 必要时渲染 → 结构化抽取」。

```bash
# 采集模式：检索 + 抓取 + 结构化字段（零 LLM 依赖，获客挖掘用这个）
python scripts/pipeline.py search "爱定义（上海）科技有限公司" --top 3 --extract --json

# 直接给 URL，跳过检索
python scripts/pipeline.py urls "https://a.com,https://b.com" --extract --json
```

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

**LLM 摘要**：`WSP_LLM_BASE_URL` 等环境变量未配置时自动降级为**采集模式**
（`mode: "collect"`），输出 `pages[].text` 结构化正文由你自行总结——本环境固定走
采集模式，不要尝试配置 LLM。

输出 JSON 关键字段：`mode`、`pages[]`（含 `emails`/`phones`/`persons`/`meta`/
`masked_phones`）、`serp[]`、`snippet_findings`、`recovered_phones`、`errors`、`hint`。

## 检索纪律（配合 ai-lead-gen 等批量场景）

- 搜索通道**串行**执行，连续失败时先换关键词/引擎，不要连续重试同一 query；
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
  （`pipeline.py search "<query>" --extract --json` / `urls` 模式），
  平台 web_search/web_fetch 仅在管线全通道失败时兜底
  `pipeline.py --extract` 执行，JSON 结果直接消费入库。
