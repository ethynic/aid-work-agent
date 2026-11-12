# 强钩子库：按业务 + 市场自动积累

> 目标：**强钩子实例不是通用模板，它必须按「你卖什么 + 卖给谁 + 卖给哪个国家」
> 分区沉淀，并用真实战绩（打开率 / 回复率）排序。**
> 同一句 cost 型钩子对密封件厂有效，对 AI 营销智能体未必；同一句钩子从德国换到
> 法国，效果也可能天差地别。所以钩子库是「业务 × 市场」分区制的，不是全局模板表。

---

## 一、为什么必须按业务 + 市场分区

四个事实决定了钩子不能通用：

1. **钩子的说服力来自对方的具体事实**，而"具体事实"在不同业务里指向完全不同的东西。
   密封件业务的 cost 钩子是"进口交期波动"，AI 营销业务的 cost 钩子是"广告费吃掉毛利"。
2. **同一模式在不同客群里效果差异极大**。合并统计会把「agency|peer 有效而
   agency|cost 无效」这类差异抹平。
3. **市场（国家/语言）是独立维度**。目标国家在每次任务开场由客户问答动态确定，
   德语钩子与英语钩子不能混算；同一钩子换国家后效果常常天差地别。
4. **库越用越准的前提是数据不串味**。业务或市场混了，库就废了。

分区键 = `business`（业务）+ `market`（市场/国家）+ `segment`（客群）。

### 关于语言：语言是渲染目标，不是资产

这一点很容易搞错，所以单独说明：

- **结构**（心理机制 + 骨架 + 槽位）是语言中立的，一份就够 -> 存 `assets/subject_templates.json`
- **成品文案**（具体的一句德语/日语标题）是按语言沉淀的实战产物
  -> 存租户级钩子库的业务分区里，**带 `market` + `lang` 标签**

所以库里**不需要**为每种语言预准备模板。发德语信时：
先查该市场有没有德语实测结论（有就直接复用实例），没有就取结构骨架现渲染。
详见第四节。

---

## 二、存储位置与结构

```
storage/tenants/{tenant_id}/lead-gen/hook_library/   # 租户级，跨 campaign 复用，技能升级不覆盖
├── index.json                          # 业务清单
└── businesses/
    └── ai-marketing-agent.json          # 一个业务 = 一个分区
        ├── business_key / name / product / icp / lang
        ├── segments[]                   # 该业务下的客群
        ├── markets[]                    # 该业务已知的目标市场（国家码）
        ├── patterns{}                   # hook_id -> {seeds[], manual[]}  人工/种子实例
        └── campaigns{}                  # campaign_N -> 一次投放的完整战绩（幂等覆盖）
```

可用环境变量 `B2B_HOOK_LIBRARY` 改位置（仅测试隔离用）。租户身份来自
`AID_TENANT_ID`（skill_executor 注入），缺省时 fail-fast 拒绝执行。

---

## 三、完整闭环（四步）

### 步骤 1 · 建业务分区（第 1 步问答之后做一次即可）

```bash
python scripts/hook_library.py register \
  --key ai-marketing-agent \
  --name "AI 营销智能体" \
  --product "AI 营销智能体（营销自动化代理）" \
  --icp "海外营销代理 / 电商 / B2B SaaS" \
  --lang zh \
  --segment agency --segment ecommerce --segment b2bsaas

python scripts/hook_library.py add-market --business ai-marketing-agent --market DE
```

`--key` 是分区标识，**同一业务的所有任务必须用同一个 key**，战绩才会累积。
建议用小写英文短横线（如 `ai-marketing-agent`）。seed 批量导入时也必须
`--business <key>` 显式指定（缺省按 name 派生 key，容易分裂分区）。

### 步骤 2 · 写邮件前先查库（话术步骤）

```bash
python scripts/hook_library.py recommend \
  --business ai-marketing-agent --market DE --segment agency
```

`--market` 用**本次任务问答确定的国家码**。输出按优先级分层：

| 层级 | 标记 | 含义 | 怎么用 |
|---|---|---|---|
| 1 | `种子实例 · 已是目标语言` | 同语言的人工/种子实例 | 参考或改写 |
| 2 | `跨语言机制参考（原句 zh，需重写为 de）` | 不同语言的原句 | **只看机制，不抄原句**，按渲染规则重写 |
| — | 战绩排名表 | 各 `市场|客群|钩子` 的打开率/回复率/结论 | 决定本轮主推哪个模式 |
| — | 结构骨架 | 语言中立骨架 + 机制说明 + 渲染规则 | 按目标语言渲染后填空 |

语言由国家码自动推断（`DE`->de、`FR`->fr、`US`->en…），可用 `--lang` 覆盖。
**语言不符的种子不会被推荐**（中文种子不会出现在德国市场的结果里）。

> 注：我方 DB 管线不在战绩里留存逐封原句实例（上游 send_log.csv 形态），
> 战绩是 hook_id 级聚合数。被验证有效的**具体原句**要靠「本地化后录回库」（§四）。

### 步骤 3 · 发信时带上标注（话术步骤）

`add-email-draft --hook-id <pattern>` 是积累的**原料**，缺 hook_id 的草稿
不进入战绩统计：

```bash
python scripts/lead_db.py add-email-draft --campaign-id 12 \
  --contact-id 88 --subject "..." --body-file draft.md --hook-id peer
```

| 要素 | 含义 | 缺失后果 |
|---|---|---|
| `hook_id` | 钩子模式（cost/peer/number/question/pain...） | 归入 `untagged`，不参与排名 |
| `--market`（record 时） | 市场/国家码（问答得来） | 与其他市场混算，看不出差异 |
| `--segment`（record 时） | 客群细分 | 与其他客群混算，看不出差异 |

### 步骤 4 · 发送核验后回写战绩

发送技能完成发送 + 核验后（见 b2b-email-sender 的 post-send-verification.md）：

```bash
# ① 聚合（输出 JSON，可存文件或直接管道）
python scripts/lead_db.py hook-stats --campaign-id 12 > /tmp/hook_stats.json

# ② 回写（幂等：同一 campaign 重跑覆盖不叠加）
python scripts/hook_library.py record \
  --business ai-marketing-agent --market DE \
  --stats-file /tmp/hook_stats.json
```

同一 campaign 分批发送且市场不同时，各批 record 各自标 `--market`，
后一批覆盖前一批的同 campaign 战绩（按需一并重放前批数据，或分 campaign 发）。

---

## 四、没有主题样板怎么办：结构 + 渲染

**结论：不要凭空想主题行，也不要预先翻译。用语言中立的结构骨架，发送时渲染成目标语言。**

`assets/subject_templates.json` 存的是**结构**，不是成品文案：

- **5 个模式**：cost / peer / number / question / pain
- 每个模式含：`intent`（心理机制）、`mechanism`（为什么有效）、`when_to_use`、
  `avoid`（忌用场景）、2-3 个 `structures`（骨架）
- 每个骨架含：`subject_skeleton`、`hook_lead_skeleton`、`slots`、`adapt_notes`
- 槽位：`{n}` `{peer}` `{topic}` `{pain}` `{result}` `{metric}` `{size}` `{region}`

### 为什么不做预翻译（上游设计决策，我方沿用）

1. **小语种质量不可控**——手写的小语种钩子读起来是"翻译过的中文营销话术"，
   当地买家一眼能看出不对味
2. **库体积爆炸**——模式 × 语言组合数随语言线性增长，加一门语言就要重做一遍
3. **机制本来就是语言中立的**——"同侪证据 + 时间锚点 + 具体数字"这个心理机制，
   在德语和日语里等价。预翻译把「资产」和「渲染」混为一谈了

**翻译是渲染，不是资产。** 钩子的说服力来自心理机制，机制跨语言等价；
语言差异应该在发送时由智能体按当地商务表达重写，而不是靠提前准备。

### 正确流程

```
查库 -> 该市场该语言有实测赢家？
          |- 有 -> 直接用（已是目标语言，无需翻译）
          |- 无 -> 取结构骨架 -> 按 rendering_rules 用目标语言重写 -> 填槽 -> 发
```

`rendering_rules` 里有渲染四步、硬规则、按语言族的本地化要点（如德语用 Sie、
日语用敬体、越南语用 kính 语体、阿拉伯语 RTL 排版）、槽位取值规范。

### 业务中立性

骨架**刻意不绑定行业**。营销业专属的措辞对卖密封件、家居、设备的买家就是错的。
统一用槽位式：

```
❌ 你有 20% 的广告预算花在没人优化的素材上
✅ {pain} 一年要吃掉你 {metric}
✅ {n}% 的 {topic} 投入从没变成结果
```

**槽位里的事实必须来自当前收件人的情报**（官网 / 社媒 / 招聘 JD），不能编。

### 本地化后录回库

一旦某个市场跑出了真实有效的本地化版本，录回库，下次该市场直接复用：

```bash
python scripts/hook_library.py add --business <key> --pattern cost \
  --market DE --lang de --subject "..." --hook "..." --segment agency
```

---

## 五、结论标签怎么读

| 标签 | 含义 | 该怎么办 |
|---|---|---|
| **已验证有效** | 样本 ≥ 30 且回复 > 0 | 下轮主推这个组合 |
| **样本够·无效** | 样本 ≥ 30 但回复 = 0 | **停用**这个组合，换模式 |
| **有信号·样本少** | 有回复但样本不足 30 | 可用，继续累积 |
| **未见效果** | 无回复且样本不足 | 继续测，或换客群 |

> ⚠ `min-sent` 默认 30。低于这个量，「已验证有效」只是噪声。
> 小样本下回复率 100% 可能只意味着发了 1 封恰好中了。
>
> 注：我方 replies 表不区分有效/无效回复，「有效回复率」即回复率；
> 自动回复（out-of-office 等）过滤由发送侧 reply_monitor 意图判定承担。

---

## 六、常用命令速查

```bash
# 看所有业务分区及其战绩
python scripts/hook_library.py list

# 查某业务在某市场的有效钩子
python scripts/hook_library.py recommend --business ai-marketing-agent --market DE --segment agency

# 登记目标市场
python scripts/hook_library.py add-market --business ai-marketing-agent --market FR

# 手工录入一条钩子实例（还没发、或来自别处的灵感）
python scripts/hook_library.py add --business ai-marketing-agent --pattern cost \
  --market DE --lang de --subject "..." --hook "..." --segment agency --note "来源：某次通话"

# 批量导入种子实例
python scripts/hook_library.py seed --business ai-marketing-agent \
  --file assets/hook_seeds_ai-marketing-agent.json

# 战绩聚合（发送侧调用，输出 JSON）
python scripts/lead_db.py hook-stats --campaign-id 12
```

---

## 七、纪律

1. **不要为了好看而标 hook_id**。标错模式 = 污染统计 = 下一轮推荐错误。
2. **不要把不同业务混进同一 `business` key**。混了就要拆分区重来。
3. **不要跨市场混用钩子**。`market` 必填；语言不符的种子不要手工改一两个词就发。
4. **不要在样本 <30 时下结论**。宁可继续发，不要凭 1 封回复就改策略。
5. **自动回复必须过滤**。reply_monitor 意图判定已内置；漏网的由发送侧核验时人工剔除。
6. **被验证有效的原句要录回库**。战绩只有 hook_id 级数字，具体原句靠 add 回填。
7. **每轮只换一个变量**。否则下一轮不知道是哪件事起了作用。
