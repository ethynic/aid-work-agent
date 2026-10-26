# 发件域名配置与预热手册

第 11 步发送前的**强制前置项**。写不出正确 DNS 记录的域名，
邮件正文写得再好也会进垃圾箱；退信率超过 5% 时，先查这份文件，不要改文案。

配套检测脚本：`scripts/domain_check.py`（一行命令跑完全项）。

---

## 一、先解决最关键的问题：不要用主域名发冷邮件

这是本手册最重要的建议，其余都是细节。

**主域名 `yourdomain.com` 承载的是和客户、供应商、HR 的真实往来邮件。**
它的声誉是多年积累出来的：客户回信、服务器 IP 反查正常、历史投诉率低。
把 300 封陌生陌生人的冷邮件从主域名发出去，等于拿全部声誉去赌：

- 主域一旦被 Gmail/Microsoft 拉黑，**日常办公邮件也会受影响**
- 客户经理收不到客户回信，损失的是真实业务
- 声誉修复周期以月计，不是改个配置就能回来

**正确做法：单独开一个发送子域。**

| 项目 | 建议 |
|---|---|
| 子域命名 | `outreach.` / `sales.` / `news.` / `cold.` —— 与主域区分即可 |
| 邮箱地址 | `zhang@outreach.yourdomain.com` |
| SPF/DKIM | **在子域上重新配置完整记录**，不要复用主域的 |
| IP | 小规模可与主域共用 IP，但注意：共享 IP 会让子域的声誉受主域流量影响 |
| 退订链接 | 独立页面（如 `unsub.outreach.yourdomain.com`），不要用主域 `/unsubscribe` |
| 退信处理 | 退信地址必须是 `bounce@outreach.yourdomain.com` 且能实际收信 |

**子域的唯一缺点**：部分冷门邮件服务对"新子域"信誉权重更低。
这是可接受的代价 —— 隔离主域风险远比这一点重要。

---

## 二、五项 DNS 记录（缺一不可）

### 1. SPF — 声明哪些服务器可以代表本域发信

SPF 是一段 TXT 记录，列出**允许**发信的服务器 IP。

```dns
outreach.yourdomain.com.  TXT  "v=spf1 include:_spf.provider.com -all"
```

要点：
- **`-all` 是硬失败（hard fail），必须加**。写成 `~all`（软失败）等于告诉收件方"这封可能不是我的"，等于没配
- 多个授权服务器用空格分隔，但 **10 次查询上限**，超了会被判 permerror
- 每个子域单独一条 SPF，**不要在主域 SPF 里 `include` 子域**
- 一条 SPF 只能有一个 `v=spf1`，重复定义会导致 permerror 直接失效

检查：
```bash
nslookup -type=SPF outreach.yourdomain.com
```

### 2. DKIM — 邮件签名，防篡改

DKIM 由邮件服务商生成一对密钥：私钥在发信时给邮件签名，公钥放 DNS。
收件方用公钥验证签名，确认邮件确实来自你、且未被中途篡改。

各服务商给的记录格式不同，典型形态：

```dns
s1._domainkey.outreach.yourdomain.com.  TXT  "v=DKIM1; h=sha256; k=rsa; p=MIGfMA0GCSq..."
selector._domainkey.outreach.yourdomain.com.  TXT  "v=DKIM1; k=rsa; p=..."
```

要点：
- **`selector` 部分每家不同**，必须用服务商给的那串，不能自己编
- **2048 位密钥**优于 1024 位，老旧收件方兼容性问题已很少，但服务商给 1024 就先用
- 强烈建议同时配置 **DKIM 签名对齐（alignment）**，选 strict 模式
- 轮换密钥：不要一次换掉所有 selector，新增一个并行使用，确认后再删旧的

检查（把 selector 换成服务商给的那串）：
```bash
nslookup -type=TXT s1._domainkey.outreach.yourdomain.com
```

### 3. DMARC — 认证失败的处置策略

DMARC 告诉收件方：SPF 和 DKIM 都失败时该怎么办。SPF 只查发信 IP，
**DKIM 才查域名**，所以 DMARC 才是真正把"域名"和"邮件"绑定的记录。

```dns
_dmarc.outreach.yourdomain.com.  TXT  "v=DMARC1; p=none; rua=mailto:dmarc@outreach.yourdomain.com; adkim=s; aspf=s"
```

**冷邮件场景的正确策略**：

| 阶段 | p 值 | 含义 | 何时用 |
|---|---|---|---|
| 观察期 | `p=none` | 只报告不处置 | 刚配置完，还没确认所有发信路径都通过认证。**至少观察 2 周** |
| 收紧期 | `p=quarantine` | 认证失败的进垃圾箱 | 确认 rua 报告里没有异常来源后 |
| 最终 | `p=reject` | 认证失败的直接拒收 | 稳定运行数周后 |

要点：
- **`p=none` 不是"没配"**，它是观察期的正确配置。但长期停在 `none` 保护效果有限
- **`rua=` 必须能收到邮件**，否则你收不到报告，等于瞎配
- **`adkim=s; aspf=s`** 是 strict 对齐：DKIM 的 d= 和 SPF 的域名都要完全等于 From 域。strict 更难被伪造，兼容性已基本无问题
- 用 relaxed（`r`）更宽松，但防伪造能力弱，冷邮件建议 strict
- DMARC 报告是发现**第三方未经授权冒用你域名发信**的主要手段，务必每季度查一次

检查：
```bash
nslookup -type=TXT _dmarc.outreach.yourdomain.com
```

### 4. PTR (rDNS) — 反向 DNS

**这一项和冷邮件的关系常被忽略，但共享 IP 的邮箱服务几乎都栽在这里。**
作用在 SMTP 服务器端，不在你的域名上。

PTR 是 IP 到域名的反向映射。收件方（尤其 Gmail、Microsoft）会检查：
**发信服务器的 IP 反查出来的域名，是否等于 HELO 里声明的域名。**

典型故障：你在 SendGrid 发信，HELO 声明 `outreach.yourdomain.com`，
但这个 IP 的 PTR 是 `mail.provider.com` → 不一致 → 轻则降权，重则直接拒收。

- 用第三方 SMTP 服务时，PTR 由服务商配置，你无法直接改，**需要开工单要求对齐**
- 自建邮件服务器时，在服务商后台设置 PTR，并在 IP 的反向区域文件配置
- PTR 对应的域名**必须也要有正向解析**（A 记录），否则 `forward-confirmed reverse DNS (FCrDNS)` 校验失败

检查：
```bash
nslookup 1.2.3.4        # 换成你的发信服务器 IP
```

### 5. MX — 收信记录

两个用途：
1. 接收退信（`bounce@`）和投诉通知 —— **必须有**
2. 部分收件方会检查 MX 是否存在，缺失可能降权

```dns
outreach.yourdomain.com.  MX  10 mx.provider.com.
```

要点：
- 主域和子域**都要配 MX**。子域没配 MX 时，`bounce@outreach.xxx` 的退信会直接被丢弃，你收不到失败通知
- `bounce@` / `bounce+*@` 地址必须真实可收信，且**不能用同一地址做人工邮箱**（会混入退信和真实回复）

---

## 三、域名层面的其他风险项

### TLD 选择

新顶级域名（TLD）是冷邮件的高风险区：

| 风险 | 说明 |
|---|---|
| `.xyz` `.top` `.shop` `.online` `.store` 等 | Gmail 有时仍会直接拦截整类 TLD |
| 新顶级域整体 | 信誉权重低，黑名单误伤率更高 |
| 免费/廉价域名 | 若你的域名注册价极低，部分安全厂商会降低信任度 |
| 拼写相近域名 | 部分收件方会做相似度检查，仿冒域名会被误判 |

**稳妥选择**：`.com` / `.cn` / `.co.uk` / `.de` 等历史较长的后缀。
**不建议**：刚上线不久的新 TLD，无论多便宜。

### 发信 IP 的历史

比 DNS 更隐蔽的影响因素。收件方的 IP 信誉数据库会记录：

- 该 IP 近期发信量突增（正常每天 50 封 → 突然 5000 封，直接触发）
- 该 IP 的历史投诉率
- 该 IP 是否为共享 IP（同 IP 其他用户拉黑会连带你）

**共享 IP 的真实代价**：Amazon SES 共享池尤其明显，冷邮件最好申请专用 IP。
预算有限时，先用共享 IP 小量测试，确认能进收件箱再投钱。

### 域名注册时间

部分反垃圾系统会看域龄。新注册域名（< 30 天）+ 新 TLD + 高发信量，
几乎必然进垃圾箱。**新域名建议先正常运营 2-4 周**再启动冷邮件。

---

## 四、诊断脚本

```bash
# 完整体检
python scripts/domain_check.py outreach.yourdomain.com

# 顺带查黑名单
python scripts/domain_check.py outreach.yourdomain.com --blacklist

# 已知发信服务器 IP 时补查 PTR
python scripts/domain_check.py outreach.yourdomain.com --smtp-ip 1.2.3.4
```

输出示例：
```
域名诊断：outreach.yourdomain.com

[通过] SPF       存在，硬失败(-all)，无 permerror 风险
[通过] DKIM      发现 2 个 selector(s)：s1, s2
[警告] DMARC     p=none（观察期）。建议观察满 2 周后再收紧
[失败] MX        未配置。子域缺 MX 会导致退信被丢弃
[通过] A→PTR    1.2.3.4 反解为 mail.provider.com

黑名单：
[通过] 未发现黑名单记录

结论：2 项失败 / 1 项警告 —— 发送前必须修复失败项
```

---

## 五、配置完成后的自检清单

逐项确认，任一项不过就不要发送：

- [ ] SPF 存在，含 `-all`，查询次数未超 10
- [ ] DKIM 签名通过（`adkim=s` 对齐模式下 d= 与 From 域一致）
- [ ] DMARC 记录存在，`rua=` 地址可正常收信
- [ ] 子域配了 MX，`bounce@` 能收到退信
- [ ] 发信服务器 PTR 与 HELO 声明一致（FCrDNS 通过）
- [ ] 发信地址与回复地址分属不同域或合理设计（`reply-to` 指向可收信地址）
- [ ] 未使用新 TLD
- [ ] 域名注册超过 30 天
- [ ] 退订链接可访问，且不追踪
- [ ] `Return-Path` 指向的 bounce 地址能收信

---

## 六、出问题时的排查顺序

| 症状 | 最可能原因 | 先查什么 |
|---|---|---|
| 全部进垃圾箱 | SPF/DKIM 缺失或配错 | `domain_check.py`，看 SPF/DKIM 项 |
| 收件方延迟 24h+ 才收到 | 内容被判可疑，内容先于 DNS 被拦 | 退信信息里的具体代码 |
| 退信码 550 5.7.1 | 被拒/黑名单 | PTR 对齐、投诉率 |
| 退信码 550 5.7.26 | SPF/DKIM 认证失败 | `domain_check.py` 认证项 |
| 退信码 421 | 限流，速率过快 | `daily_limit` 与 `delay_seconds` |
| Gmail 显示"疑似垃圾邮件"标签 | 通过了认证但内容分低 | 主题行、正文触发词、退订链接 |
| 开了主域后日常邮件也进垃圾箱 | **主域声誉已受损** | 立即停用冷邮件并迁移到子域 |

**Gmail 只标记不拒收**，说明 DNS 是好的，问题在内容与行为；
**直接拒收**通常是 DNS 认证或 IP 声誉问题。按这个分界定位能省大量时间。

---

## 相关文件

- `references/sending-and-deliverability.md` —— SMTP 配置、正文要点、发送节奏
- `references/email-compliance.md` —— 各地区法规要求
- `scripts/domain_check.py` —— 域名健康检测
- `assets/smtp_config.example.json` —— SMTP 配置样例