# 目标客户检索战术手册

第 3-6 步使用。

## 一、搜索意图分层

不要用一种搜索词打天下。按四种意图分别检索：

| 意图 | 问题 | 搜索模式 |
|---|---|---|
| **A. 谁在买** | 这个行业里谁是进口方/采购方 | 行业词 + importer/distributor/purchaser |
| **B. 谁需要** | 哪些企业有我们的痛点 | 痛点描述 + 行业词 |
| **C. 谁在做** | 目标市场的活跃玩家有哪些 | 行业词 + 国家 + 名单/榜单/展会 |
| **D. 谁在谈** | 谁在公开谈论相关话题 | 痛点词 + 采购/供应/供应链讨论 |

## 二、核心搜索模板

### 找进口商/经销商
```
"<行业词>" importer "<国家>"
"<行业词>" distributor "<国家>" contact
"<行业词>" "purchasing manager" "<国家>"
"<产品词>" supplier "<国家>" -site:alibaba.com
```

### 找有痛点的企业
```
"<行业>" "<痛点现象>" "<国家>"
"<行业>" "looking for" supplier "<国家>"
"<行业>" "problem with" OR "challenge with" "<国家>"
```

### 找活跃玩家（拿名单）
```
"<行业>" "<国家>" list of companies
"<行业>" "<国家>" top manufacturers
"<行业>" association members "<国家>"
"<行业>" exhibition "<国家>" exhibitors
"site:<协会域名>" members
"site:<展会域名>" exhibitor list
```

### 找官网（排除噪音）
```
"<公司名>" "<国家>" official website
"<公司名>" -site:yellowpages.com -site:yelp.com -site:facebook.com -site:linkedin.com
```

### 找文档与手册
```
"<行业>" catalog filetype:pdf "<国家>"
"<公司名>" brochure OR catalog OR "product manual" filetype:pdf
```

### 找联系人
```
site:<公司官网域名> (contact OR about OR team OR impressum)
"<公司名>" CEO OR founder OR "managing director"
"<公司名>" "purchasing manager" OR "procurement manager"
site:linkedin.com/in "<公司名>" <职位关键词>
```

## 三、本地语言关键词表

搜索时必须用目标语言，不能只搜英语。

| 语言 | 采购 | 老板 | 运营 | 联系我们 | 公司名单 |
|---|---|---|---|---|---|
| 英语 | purchasing manager, buyer, procurement | CEO, founder, managing director, owner | operations director, COO, head of operations | contact us, get in touch | list of companies, directory |
| 德语 | Einkäufer, Leiter Einkauf, Beschaffung | Geschäftsführer, Inhaber, Vorstand | Betriebsleiter, Leiter Produktion | Kontakt, Impressum, Kontaktformular | Anbieterverzeichnis, Mitglieder |
| 法语 | acheteur, responsable achats | PDG, directeur général, gérant | directeur des opérations | contact, à propos, formulaire | annuaire, liste des entreprises |
| 西班牙语 | comprador, responsable de compras | CEO, director general, propietario | director de operaciones | contacto, quiénes somos | directorio, listado de empresas |
| 意大利语 | acquirente, responsabile acquisti | amministratore delegato, titolare | direttore operativo | contatti, chi siamo | elenco aziende |
| 荷兰语 | inkoper, manager inkoop | directeur, eigenaar | productiedirecteur | contact, over ons | bedrijvenregister |
| 波兰语 | kupiec, specjalista ds. zakupów | prezes, właściciel | dyrektor operacyjny | kontakt, o nas | katalog firm |
| 俄语 | специалист по закупкам, менеджер по закупкам | генеральный директор, собственник | операционный директор | контакты, о компании | каталог компаний |
| 葡语 | comprador, responsável por compras | CEO, diretor, proprietário | diretor de operações | contato, sobre nós | lista de empresas |
| 日语 | 購買担当, 調達, 購買部長 | 代表取締役, 社長 | 工場長, 運営部長 | お問い合わせ, 会社概要 | 企業一覧 |
| 韩语 | 구매담당, 조달 | 대표이사, 사장 | 운영이사 | 문의처, 회사소개 | 기업목록 |
| 中文 | 采购经理, 采购总监, 供应商 | 总经理, 董事长 | 运营总监, 生产经理 | 联系我们, 公司简介 | 企业名录 |

## 四、扩大候选量的技巧

单次搜索通常只能拿到 5-10 家。用以下方法批量扩展：

1. **长尾展开**：一个产品词派生 5 个修饰词（地区/规模/用途/工艺/认证），每个词单独搜
2. **协会爬取**：找到行业协会 → 直接打开 member directory → 抓取全部成员
3. **展会名录**：展会官网 exhibitor list 通常一次给几百家，且包含联系方式入口
4. **B2B 平台反查**：在 Alibaba/Europages 等搜品类 → 看同品类买家询盘提到的公司名
5. **竞争对手客户**：找到你的直接竞争对手 → "customers of X" / "X clients" → 同一批客户
6. **LinkedIn 公司搜索**：`site:linkedin.com/company "<行业>" "<国家>"`
7. **地图/评论站**：`"<行业>" "<城市>" reviews` —— Google Maps 评论常含公司官网链接

**纪律**：第一轮至少收集 30-50 家原始候选，才能筛出 10-15 家合格的。

## 五、验证与剔除

每条线索必须过以下检查，否则进不了 leads.csv：

| 检查项 | 通过标准 | 不通过处理 |
|---|---|---|
| 官网存在 | 域名与公司名一致，非黄页/社媒/中间商 | `rejected` / `reason=no_official_site` |
| 地域匹配 | 官网或注册地在目标国家 | `rejected` / `reason=wrong_region` |
| 业务相关 | 主营品类与我们产品有交集 | `rejected` / `reason=not_relevant` |
| 规模匹配 | 员工数/营收在 ICP 范围 | `rejected` / `reason=size_mismatch` |
| 资质匹配 | 有所需认证（若有硬门槛） | `rejected` / `reason=no_certification` |
| 非竞争对手 | 不属于我们的同行 | `rejected` / `reason=competitor` |
| 非无效实体 | 无重复、无停业、无纯中介 | 合并或剔除 |

## 六、leads.csv 字段规范

`scripts/lead_db.py` 使用以下列，勿改列名：

```csv
company,country,city,website,industry,employees,founded,source,source_url,score,status,reason,notes
```

- `source`：发现渠道，如 `search_engine` / `directory` / `trade_show` / `social` / `llm_research`
- `status`：`new` / `qualified` / `rejected` / `contacted` / `replied` / `bounced`
- `score`：0-100，评分维度见下

## 七、评分维度（0-100）

| 维度 | 权重 | 评分要点 |
|---|---|---|
| 业务相关性 | 30 | 主营品类与我们产品的重合度 |
| 痛点强度 | 25 | 官网/招聘/新闻中是否暴露相关痛点 |
| 规模匹配 | 15 | 是否在 ICP 规模区间 |
| 采购力 | 15 | 是否有进口/OEM 需求，年采购体量 |
| 联系方式可得性 | 15 | 官网是否公开联系邮箱/电话 |

## 八、常见检索失败与对策

| 问题 | 对策 |
|---|---|
| 搜出来的都是小作坊 | 加规模约束：`"employees" OR "established"`、用协会名录、避开 B2B 平台 |
| 结果全是中间商/贸易公司 | 加 `"manufacturer" OR "factory" OR "producer"`，排除 `distributor`/`trading` |
| 搜不到目标国的公司 | 用本地语言 + 去除国家词的组合；查该国 B2B 平台本地站 |
| 官网搜到了但内容空洞 | 补充查 LinkedIn / 新闻稿 / 招聘 JD / 产品 PDF |
| 同一批公司反复出现 | 说明搜索词太窄，扩展修饰词换一批候选 |