# 旅游咨询与规划子智能体设计方案

## 一、背景与目标

### 1.1 业务背景

旅游业务场景中，客户经理需要根据客户需求（人数、天数、偏好等），结合公司已有的旅游产品资料（景点介绍、门票价格、住宿标准、课程活动等），快速给出行程建议和报价。

当前痛点：
- 资料分散在 Word、PDF、PPT、Excel 等多种格式文件中
- 报价需要逐项查找价格、手工计算，容易出错
- 行程组合依赖经验，新人上手慢
- 客户需求变化频繁，需要快速调整方案和报价

### 1.2 目标

构建一个**旅游咨询与规划子智能体**，能够：
1. 基于知识库准确回答客户关于景点、行程、费用的咨询
2. 根据客户需求（人数、天数、预算、偏好）智能推荐行程方案
3. 自动生成结构化报价单，**严格按照知识库中的价格数据计算，不自行编造**
4. 支持行程方案的快速调整和重新报价

### 1.3 设计原则

- **准确性优先**：所有价格、行程信息必须来自知识库，不得臆造
- **可溯源**：报价中每项费用的来源可追溯
- **结构化输出**：报价结果格式化输出，便于生成 Excel

---

## 二、知识库分析

### 2.1 文档类型与内容概览

| 文档 | 格式 | 核心内容 | 价格信息 |
|------|------|----------|----------|
| 问天贵州三日行程费用预算.docx | Word | 三日行程费用明细（按33人学生预算） | 门票、餐饮、住宿、交通、杂费等详细单价 |
| 问天贵州项目详情(1).docx | Word | 六天行程方案、11门研学课程详情、Q&A | 无价格 |
| 问天贵州产品手册(1).docx | Word | 产品介绍、活动内容、行程计划表、51张图片 | 无具体价格 |
| 贵州研学方案（2025）.pdf | PDF | 公司整体产品线、多个行程方案、课程体系、公司介绍 | 超级贵州5980元/人 |
| 问天贵州PPT.pptx | PPT | 产品推介、景点介绍、研学目的 | 费用包含/不含条款 |
| 超级贵州行程最终报价(30人).xls | Excel | **报价模板**，30人六天五晚 | **完整报价结构** |

### 2.2 报价模板结构分析（核心参考）

报价模板 `超级贵州行程最终报价(30人).xls` 是报价系统的核心参考：

**表头字段**：
| 字段 | 说明 |
|------|------|
| 成本类别 | 大分类：用车、用餐、住宿、门票/活动、其他费用 |
| 项目 | 具体费用项目名称 |
| 单价 | 单项价格 |
| 数量 | 每次数量 |
| 单位 | 人/辆/团 |
| 次数 | 发生次数 |
| 单位 | 夜/餐/次/天 |
| 费用小计 | 人均费用 |
| 随队老师 | 老师费用 |
| 备注 | 说明信息 |

**费用计算逻辑**：
- 按人均分摊（单位为"团"）：`费用小计 = 单价 / 总人数`
- 直接人均计算（单位为"人"）：`费用小计 = 单价 × 次数`
- 住宿特殊计算：`费用小计 = 单价 / 2 × 夜数`（两人一间）

**六大成本类别**：
1. **用车**：大巴包车，按团均摊
2. **用餐**：按人×餐数
3. **住宿**：按人×夜数，注意两人一间
4. **门票/活动**：按人或按团，门票、讲解费、研学课程
5. **其他费用**：保险/用水、研学导师、司陪房、老师费用、操作费

---

## 三、系统架构设计

### 3.1 整体架构

```
用户对话
  ↓
旅游咨询子智能体 (travel-consultant)
  ├── 知识库检索 (knowledge_base_search)
  │     └── 查询景点信息、价格数据、行程方案
  ├── 行程规划引擎 (trip-planner skill)
  │     ├── 需求分析：人数、天数、预算、偏好
  │     ├── 方案匹配：从知识库中匹配可用行程
  │     └── 方案调整：根据客户反馈修改行程
  ├── 报价引擎 (quote-generator skill)
  │     ├── 费用项提取：从知识库获取各项目单价
  │     ├── 费用计算：按报价模板逻辑计算
  │     └── 报价输出：生成结构化报价数据
  └── 报价导出工具 (quote-export tool)
        └── 生成 Excel 报价单（.xlsx 格式）
```

### 3.2 子智能体定义

创建 `subagents/travel-consultant/SUBAGENT.md`，配置如下：

```yaml
name: 旅游咨询顾问
description: 贵州研学旅游咨询与行程规划智能体，提供行程建议和报价服务
version: 1.0.0
capabilities:
  - travel_consultation    # 旅游咨询
  - trip_planning          # 行程规划
  - quote_generation       # 报价生成
triggers:
  file_patterns:
    - "*.xls"
    - "*.xlsx"
    - "*.docx"
tools:
  inherit: true
  additional: []
skills:
  allowed:
    - trip-planner
    - quote-generator
    - quote-export
    - word-processing
    - excel-data-assistant
    - pdf
```

### 3.3 System Prompt 设计

子智能体的 System Prompt 需要明确以下规则：

```
你是贵州研学旅游咨询顾问，专业为客户提供贵州研学旅游的咨询服务。

## 核心工作原则

1. **准确性第一**：所有景点信息、价格数据必须来自知识库检索结果。
   - 不得编造任何价格、景点信息或行程安排
   - 如果知识库中没有相关信息，明确告知客户需要确认

2. **严格按知识库报价**：
   - 门票价格必须使用知识库中记录的价格
   - 餐饮标准按知识库中的餐标计算
   - 住宿按知识库中的酒店等级和价格
   - 交通、导游等费用按知识库中的标准

3. **报价结构**：按照标准报价模板，分为六大类：
   - 用车：大巴等交通费用
   - 用餐：按人×餐数计算
   - 住宿：按人×夜数计算，注意标间为2人一间
   - 门票/活动：门票、讲解费、研学课程等
   - 其他费用：保险、研学导师、司陪房、操作费等

4. **服务态度**：
   - 专业、简洁地回答客户问题
   - 主动了解客户需求（人数、时间、预算、特殊要求）
   - 给出明确建议，附带依据

## 报价计算规则

- 按团均摊项目：费用小计 = 单价 ÷ 总人数
- 按人计算项目：费用小计 = 单价 × 次数
- 住宿计算：费用小计 = 单价 ÷ 2 × 夜数（两人一间）
- 总费用 = Σ 费用小计
- 人均费用 = 总费用 ÷ 人数

## 工作流程

1. 了解需求：人数、天数、出发时间、预算范围、特殊要求
2. 检索知识库：查找匹配的行程方案和价格
3. 推荐行程：给出1-3个方案供客户选择
4. 确认行程：客户确认后生成详细报价
5. 调整方案：根据客户反馈调整，重新报价
6. 导出报价：生成Excel报价单
```

---

## 四、技能设计

### 4.1 技能一：trip-planner（行程规划）

**路径**：`src/skills/trip-planner/SKILL.md`

**功能**：
- 分析客户需求（人数、天数、预算、偏好）
- 从知识库中检索匹配的行程方案
- 生成/调整行程安排

**工作流程**：
1. 接收用户需求参数
2. 调用 `knowledge_base_search` 搜索相关行程方案
3. 根据天数、景点偏好匹配行程
4. 输出结构化行程表

**输入参数**：
- `people_count`：人数
- `days`：天数
- `budget`：预算范围（可选）
- `preferences`：偏好（如天文、地质、民族文化等）
- `start_date`：出发日期（可选）
- `accommodation_level`：住宿标准（可选，如4钻酒店）

**输出格式**（行程表）：
```
| 日期 | 时间 | 活动主题 | 活动内容 | 涉及课程 |
|------|------|----------|----------|----------|
| Day 1 | 上午 | 出发集结 | ... | - |
| Day 1 | 下午 | 开营仪式 | ... | - |
| ... | ... | ... | ... | ... |
```

### 4.2 技能二：quote-generator（报价生成）

**路径**：`src/skills/quote-generator/SKILL.md`

**功能**：
- 根据确定的行程方案，从知识库提取各项目单价
- 按报价模板逻辑计算每项费用
- 汇总生成结构化报价

**工作流程**：
1. 接收行程方案和人数
2. 逐项检索知识库获取价格：
   - 门票：各景点门票价格
   - 课程：研学课程费用
   - 住宿：酒店房价
   - 餐饮：餐标
   - 交通：包车费用
   - 其他：导游、保险等
3. 按计算规则逐项计算费用小计
4. 汇总输出报价表

**报价计算逻辑**（Python 脚本辅助）：

```python
def calculate_quote(items, total_people):
    """
    items: List[QuoteItem] - 报价项列表
    total_people: int - 总人数

    QuoteItem:
      - category: 成本类别
      - name: 项目名称
      - unit_price: 单价
      - quantity: 数量
      - unit: 单位（人/辆/团）
      - frequency: 次数
      - freq_unit: 次数单位（夜/餐/次/天）
    """
    results = []
    for item in items:
        if item.unit == '团':
            # 按团均摊
            subtotal = item.unit_price / total_people * item.frequency
        elif item.unit == '人' and item.name in ['住宿相关']:
            # 住宿：两人一间
            subtotal = item.unit_price / 2 * item.frequency
        else:
            # 按人计算
            subtotal = item.unit_price * item.frequency

        results.append({
            'category': item.category,
            'name': item.name,
            'unit_price': item.unit_price,
            'quantity': item.quantity,
            'unit': item.unit,
            'frequency': item.frequency,
            'freq_unit': item.freq_unit,
            'subtotal': round(subtotal, 2)
        })

    total = sum(r['subtotal'] for r in results)
    per_person = round(total, 2)
    return results, per_person
```

**输出格式**（报价表）：
```
贵州天悦旅行社有限公司研学报价表

课程名称：XXX    日期：XXX    人数：XX人

| 成本类别 | 项目 | 单价 | 数量 | 单位 | 次数 | 单位 | 费用小计 | 备注 |
|----------|------|------|------|------|------|------|----------|------|
| 用车 | 旅游大巴 | 9800 | 1 | 辆 | 1 | 次 | 326.67 | ... |
| ... | ... | ... | ... | ... | ... | ... | ... | ... |
| **合计** | | | | | | | **XXXX.XX** | |
```

### 4.3 技能三：quote-export（报价导出）

**路径**：`src/skills/quote-export/SKILL.md`

**功能**：
- 将结构化报价数据导出为 Excel 文件
- 格式参照标准报价模板

**实现方式**：
- 使用 Python `openpyxl` 库生成 .xlsx 文件
- 包含合并单元格、格式化、审核签字区
- 参照 `超级贵州行程最终报价(30人).xls` 的格式

**辅助脚本**：`src/skills/quote-export/scripts/export_xlsx.py`

```python
import openpyxl
from openpyxl.styles import Font, Alignment, Border, Side

def export_quote_xlsx(quote_data, output_path):
    """
    导出报价单为 Excel 文件

    quote_data: {
        'title': '贵州天悦旅行社有限公司研学报价表',
        'course_name': str,
        'date': str,
        'people_count': int,
        'items': [
            {
                'category': str,
                'name': str,
                'unit_price': float,
                'quantity': int,
                'unit': str,
                'frequency': int,
                'freq_unit': str,
                'subtotal': float,
                'teacher_fee': float,
                'remark': str
            }
        ],
        'total': float,
        'teacher_total': float
    }
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = quote_data.get('course_name', '报价')

    # 设置列宽
    col_widths = [12, 20, 10, 8, 6, 8, 6, 12, 12, 30]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    # 标题行
    ws.merge_cells('A1:J1')
    ws['A1'] = quote_data['title']
    ws['A1'].font = Font(size=14, bold=True)
    ws['A1'].alignment = Alignment(horizontal='center')

    # 信息行
    ws.merge_cells('A2:C2')
    ws['A2'] = f"课程名称={quote_data['course_name']}"
    ws.merge_cells('D2:H2')
    ws['D2'] = f"日期={quote_data['date']}"
    ws.merge_cells('I2:J2')
    ws['I2'] = f"人数={quote_data['people_count']}"

    # 表头行
    headers = ['成本类别', '项目', '单价', '数量', '单位', '次数', '单位', '费用小计', '随队老师', '备注']
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col, value=header)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center')

    # 数据行
    current_category = None
    category_start_row = 4
    for i, item in enumerate(quote_data['items']):
        row = 4 + i
        ws.cell(row=row, column=1, value=item['category'])
        ws.cell(row=row, column=2, value=item['name'])
        ws.cell(row=row, column=3, value=item['unit_price'])
        ws.cell(row=row, column=4, value=item['quantity'])
        ws.cell(row=row, column=5, value=item['unit'])
        ws.cell(row=row, column=6, value=item['frequency'])
        ws.cell(row=row, column=7, value=item['freq_unit'])
        ws.cell(row=row, column=8, value=item['subtotal'])
        ws.cell(row=row, column=9, value=item.get('teacher_fee', 0))
        ws.cell(row=row, column=10, value=item.get('remark', ''))

    # 合并同类别单元格
    # ... (省略合并逻辑，按 category 分组合并第1列)

    # 合计行
    total_row = 4 + len(quote_data['items'])
    ws.merge_cells(f'A{total_row}:G{total_row}')
    ws.cell(row=total_row, column=1, value='合计')
    ws.cell(row=total_row, column=8, value=quote_data['total'])
    ws.cell(row=total_row, column=9, value=quote_data.get('teacher_total', 0))

    # 审核签字区
    sign_row = total_row + 2
    ws.cell(row=sign_row, column=1, value='审核')
    ws.cell(row=sign_row, column=2, value='成本审核员')
    ws.cell(row=sign_row, column=5, value='研学负责人')
    ws.cell(row=sign_row, column=9, value='财务部审核')

    wb.save(output_path)
    return output_path
```

---

## 五、知识库数据准备

### 5.1 文档处理策略

知识库需要结构化存储以下几类信息：

#### 5.1.1 景点信息

| 景点 | 地址 | 门票价格 | 讲解费 | 备注 |
|------|------|----------|--------|------|
| 中国天眼FAST | 黔南平塘 | 110元/人（含观光车+天文体验馆+天象影院+证书+保险） | 400元/团 | 需存放所有电子产品 |
| 南仁东纪念馆 | 黔南平塘 | 含在天眼门票内 | 含在讲解费内 | - |
| 天眼洞穴基地 | 黔南平塘 | 含在研学课程中 | - | 喀斯特溶洞探险 |
| 天坑博物馆 | 黔南平塘 | 含在研学课程中 | - | 地质研学 |
| 掌布天书景区 | 黔南平塘 | 含在研学课程中 | - | 7亿年地质奇观 |
| 布依族民族村寨 | 黔南平塘 | 含在研学课程中 | - | 非遗体验 |
| 天空之桥（平塘特大桥） | 黔南平塘 | 含在课程中 | - | 桥梁科普 |
| 坝陵河大桥 | 安顺 | 25元/人（观光），88元/人（含上桥+博物馆+搭建） | 150元/团 | - |
| 黄果树瀑布 | 安顺 | 60元/人（观光车+保险），120元/人（夜游） | - | 旺季建议夜游 |
| 关岭化石国家地质公园 | 安顺 | 30元/人 | 200元/团 | 化石挖掘体验 |
| 西江千户苗寨 | 黔东南 | 60元/人（符合免票政策为30元） | - | 蜡染/苗歌/稻田捉鱼课程 |
| 贵州长征文化数字艺术演出 | - | 98元/人 | - | - |
| 青岩古镇 | 贵阳 | 免费 | - | - |
| 安顺屯堡 | 安顺 | 88元/人（含修缮房屋活动） | - | - |
| 梵净山 | 铜仁 | 待确认 | - | - |
| 荔波小七孔 | 黔南 | 待确认 | - | - |

#### 5.1.2 研学课程

| 课程 | 课时 | 价格 | 适用景点 |
|------|------|------|----------|
| 天文科普馆+天象影院 | 2课时 | 含在天眼门票内 | 天眼 |
| 南仁东纪念馆 | 1课时 | 含在讲解费内 | 天眼 |
| 天眼观景台 | 2课时 | 含在天眼门票内 | 天眼 |
| 发报机实验（摩斯密码） | 2课时 | 30元/人（三选一） | 天眼 |
| 天眼模型搭建 | 1课时 | 45元/人或30元/人（三选一） | 天眼 |
| 观星活动 | 2课时 | 30元/人（三选一） | 天眼 |
| 喀斯特溶洞探秘 | 3课时 | 含在课程包中 | 天眼洞穴基地 |
| 天坑博物馆研学 | 4课时 | 含在课程包中 | 天坑 |
| 喀斯特专题研讨 | 3课时 | 含在课程包中 | - |
| 掌布天书徒步 | 4课时 | 含在课程包中 | 掌布 |
| 布依族非遗体验 | 4课时 | 含在课程包中 | 布依寨 |
| 天空之桥桥梁模型搭建 | 4课时 | 含在课程包中 | 平塘特大桥 |
| 化石挖掘 | 2课时 | 含在关岭门票中 | 关岭 |
| 屯堡修缮房屋 | 2课时 | 含在安顺屯堡门票中 | 屯堡 |
| 蜡染/苗歌/稻田捉鱼 | 2课时 | 58元/人（三选一） | 西江 |

#### 5.1.3 住宿价格

| 酒店 | 位置 | 星级 | 价格 | 备注 |
|------|------|------|------|------|
| 贵阳酒店 | 贵阳 | 携程4钻 | 320元/间/晚 | - |
| 安顺麦克达温德姆 | 安顺 | 携程4钻 | 280元/间/晚 | 180元/间/晚（三日行程） |
| 罗甸千岛湖大酒店 | 罗甸 | 携程4钻 | 180元/间/晚 | 天眼主题日入住 |
| 西江酒店 | 西江 | 携程4钻 | 428元/间/晚 | - |
| 天眼研学营地 | 平塘 | 营地 | 待确认 | 六人间高低床 |

#### 5.1.4 餐饮标准

| 类型 | 标准 | 备注 |
|------|------|------|
| 研学特色餐 | 40元/人/餐 | 标准餐标 |
| 团队桌餐 | 400元/桌/餐 | 每桌约10人 |
| 特色餐（烤肉+水果+蛋糕） | 约3113元/团 | 33人规格 |

#### 5.1.5 交通费用

| 类型 | 价格 | 备注 |
|------|------|------|
| 40座大巴（6天全程） | 9800元/辆 | 贵阳起止 |
| 40座大巴（3天） | 5300元/辆 | 贵阳起止 |

#### 5.1.6 其他费用标准

| 项目 | 价格 | 备注 |
|------|------|------|
| 每日用水/保险 | 20元/人 | 研学手册、道具等 |
| 研学导师 | 400-500元/人/天 | 2人/团 |
| 司陪房 | 200元/间/晚 | 1名司机+2名导师 |
| 操作费 | 10-20元/人/天 | - |
| 专家讲座 | 1000元/场 | 含专家费、场地费 |
| 天眼耳机 | 10元/人 | - |

### 5.2 文档上传建议

为保证知识库检索效果，建议将原始文档拆分为专题文档后上传：

1. **景点门票价格表.xlsx** — 所有景点门票、讲解费的详细价格表
2. **研学课程价格表.xlsx** — 所有研学课程的价格和课时
3. **住宿价格表.xlsx** — 各城市/地区酒店价格
4. **餐饮标准.xlsx** — 餐标和特色餐价格
5. **交通费用表.xlsx** — 大巴、自驾等交通费用
6. **其他费用标准.xlsx** — 导师、保险、操作费等
7. **行程方案合集** — 保留原始 Word/PDF 作为行程参考
8. **报价模板.xlsx** — 标准报价模板

> 注意：也可直接上传原始文档，知识库的解析器（Word/Excel/PDF/PPT）能自动提取文本内容。但拆分上传可提高检索精度，尤其是价格数据的检索。

---

## 六、核心工作流程

### 6.1 旅游咨询流程

```
客户提问
  ↓
1. 意图识别
   ├── 景点咨询 → 检索知识库 → 返回景点信息
   ├── 价格咨询 → 检索知识库 → 返回价格信息
   ├── 行程咨询 → 检索知识库 → 匹配行程方案 → 推荐
   └── 报价需求 → 进入报价流程
  ↓
2. 报价流程
   ├── 2.1 确认需求（人数、天数、住宿标准等）
   ├── 2.2 选择/生成行程方案
   ├── 2.3 检索知识库获取各项目价格
   ├── 2.4 计算费用（按报价模板逻辑）
   ├── 2.5 生成报价单
   └── 2.6 导出 Excel（可选）
```

### 6.2 报价生成详细步骤

```
输入: {people: 30, days: 6, itinerary: "超级贵州", accommodation: "4钻"}

Step 1: 确定行程方案
  → knowledge_base_search("超级贵州 六天 行程安排")

Step 2: 逐项检索价格
  → knowledge_base_search("天眼 门票 价格")     → 110元/人
  → knowledge_base_search("大巴 6天 费用")       → 9800元/辆
  → knowledge_base_search("贵阳 4钻 酒店 价格")  → 320元/间
  → knowledge_base_search("研学餐标")            → 40元/人/餐
  → ... 逐项查询

Step 3: 组装报价数据
  categories = [
    {category: "用车", items: [大巴: 9800/30=326.67]},
    {category: "用餐", items: [餐费: 40×8=320]},
    {category: "住宿", items: [贵阳: 320/2×2=320, 安顺: 280/2×1=140, ...]},
    {category: "门票/活动", items: [天眼: 110×1=110, ...]},
    {category: "其他费用", items: [保险: 20, 导师: 400×2/30×6=160, ...]}
  ]

Step 4: 计算汇总
  total = Σ 费用小计 = 2551.33 元/人

Step 5: 输出报价表
  → 格式化表格 + 可导出 Excel
```

### 6.3 典型对话示例

```
客户: 我们有30个学生，想做一个6天的贵州研学，大概什么价格？

智能体: 让我为您查询一下。

根据我们的超级贵州研学方案，6天5晚的行程报价如下：

【行程概览】
Day 1: 贵阳集合 → 黔南天眼营地
Day 2: 天文科普馆、南仁东纪念馆、天眼观景台
Day 3: 喀斯特溶洞探秘、天坑博物馆
Day 4: 掌布天书徒步、布依族民族村寨
Day 5: 天空之桥、结营仪式
Day 6: 返回贵阳

【报价明细】（按30人计算）
- 用车：326.67元/人（40座大巴6天全程）
- 用餐：320.00元/人（8个正餐，40元/人/餐）
- 住宿：764.00元/人（5晚4钻酒店）
- 门票/活动：526.00元/人
- 其他费用：566.66元/人

人均费用：约 2503.33 元

注：此报价基于4钻酒店标准，如需调整住宿等级或行程内容，可以重新报价。
需要我导出详细的Excel报价单吗？
```

---

## 七、实现计划

### Phase 1: 基础框架搭建

1. **创建子智能体目录和配置**
   - `subagents/travel-consultant/SUBAGENT.md`
   - 配置 capabilities、triggers、skills

2. **知识库数据准备**
   - 将原始文档上传到知识库
   - 验证检索效果（价格、景点、行程）

3. **基础咨询功能验证**
   - 景点咨询问答
   - 价格查询
   - 行程方案推荐

### Phase 2: 报价引擎

4. **创建 quote-generator 技能**
   - `src/skills/quote-generator/SKILL.md`
   - 编写报价计算辅助脚本 `scripts/calculate.py`
   - 实现结构化报价输出

5. **创建 quote-export 技能**
   - `src/skills/quote-export/SKILL.md`
   - 编写 Excel 导出脚本 `scripts/export_xlsx.py`
   - 参照标准报价模板格式

### Phase 3: 行程规划

6. **创建 trip-planner 技能**
   - `src/skills/trip-planner/SKILL.md`
   - 行程模板匹配逻辑
   - 行程调整支持

### Phase 4: 优化与测试

7. **知识库优化**
   - 补充缺失的价格数据
   - 优化文档分块策略
   - 提高检索准确率

8. **端到端测试**
   - 完整咨询流程测试
   - 报价准确性验证（与人工报价对比）
   - Excel 导出格式验证

---

## 八、文件清单

### 需要创建的文件

```
subagents/
  travel-consultant/
    SUBAGENT.md                          # 子智能体定义

src/skills/
  trip-planner/
    SKILL.md                             # 行程规划技能
  quote-generator/
    SKILL.md                             # 报价生成技能
    scripts/
      calculate.py                       # 报价计算脚本
  quote-export/
    SKILL.md                             # 报价导出技能
    scripts/
      export_xlsx.py                     # Excel 导出脚本
```

### 需要修改的文件

```
# 无需修改现有代码，子智能体和技能通过文件系统自动加载
```

---

## 九、风险与注意事项

1. **价格准确性**：知识库中的价格可能过时，需要定期更新。建议在报价输出中标注"价格以实际出行为准"。
2. **文档解析质量**：Word/PDF 中的价格表格可能解析不完整，建议关键价格数据用 Excel 整理后单独上传。
3. **计算精度**：费用分摊可能出现小数，需要统一精度（保留2位小数）。
4. **知识库覆盖**：部分价格在文档中缺失（如梵净山门票、荔波小七孔门票），需要补充。
5. **行程灵活性**：客户可能提出非常规需求（如调整景点顺序、增加自由活动时间），系统需要在知识库范围内灵活应对，超出范围时明确告知。
