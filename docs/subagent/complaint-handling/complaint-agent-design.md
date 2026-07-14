# AI投诉处理专员 — 智能体设计文档

> 版本: v1.0 | 创建: 2026-05-25 | 状态: 开发中（Phase 1-2 已完成）

## 1. 需求分析

### 1.1 角色定义

AI投诉处理专员是企业面向客户的投诉受理终端，核心职责：

| 职责 | 说明 |
|------|------|
| 投诉智能分类 | 自动识别投诉类型（产品质量、服务态度、物流配送、虚假宣传等），评估紧急程度（一般/紧急/严重） |
| 快速响应安抚 | 第一时间响应客户，识别情绪强度，给予针对性安抚 |
| 解决方案推荐 | 基于历史案例库推荐最优解决方案，给出处理建议 |
| 升级预警机制 | 识别重大/群体性投诉，自动升级到人工，通知相关负责人 |

### 1.2 核心挑战

1. **情绪识别与安抚**：客户投诉时情绪波动大，需要共情式回复而非机械流程
2. **分类准确性**：投诉原因多样且交叉，需要准确分类才能匹配正确解决方案
3. **历史案例匹配**：如何从历史投诉中快速找到相似案例并提取有效方案
4. **升级时机判断**：何时自动处理、何时必须升级人工，判断边界模糊
5. **跨渠道一致性**：不同渠道（企微、钉钉、Web）的客户体验需一致

---

## 2. 基础设施补充（全局复用层） ✅ 已完成

以下基础设施是投诉智能体所必需的，同时设计为**其他未来智能体也可复用**的通用能力。

### 2.1 通用情绪分析服务 (`src/services/sentiment_service.py`) ✅

**为什么需要**：投诉处理需要情绪识别，但未来的客服类智能体（如售前咨询、技术支持）也需要情绪感知能力。作为通用服务提供。

**设计**：

```
src/services/
├── __init__.py
└── sentiment_service.py    # 新增：通用情绪分析服务
```

```python
class SentimentResult:
    """情绪分析结果"""
    sentiment: str           # positive / neutral / negative / angry
    intensity: float         # 0.0 ~ 1.0，情绪强度
    urgency: str             # low / medium / high / critical
    key_emotions: List[str]  # 识别到的关键情绪标签，如 ["愤怒", "失望", "焦虑"]
    confidence: float        # 分析置信度

class SentimentService:
    """
    通用情绪分析服务，基于 LLM 进行文本情绪分析。

    复用场景：
    - 投诉智能体的客户情绪识别与安抚策略选择
    - 任何客服类智能体的用户情绪感知
    - 会话质量评估（后续可接入）
    """

    def analyze(self, text: str, context: str = "") -> SentimentResult:
        """分析单条文本的情绪"""

    def analyze_batch(self, texts: List[str]) -> List[SentimentResult]:
        """批量分析（用于对话历史情绪趋势）"""

    def get_emotion_trend(self, messages: List[Dict]) -> List[Dict]:
        """分析对话中的情绪变化趋势，返回 [{turn, sentiment, intensity}]"""
```

**实现方式**：通过 LLM 调用实现（非独立模型），使用结构化输出保证返回格式一致。利用现有 `LLMGateway` 的 `chat` 方法。

**Prompt 设计思路**：
- 输入：用户消息文本 + 可选的对话上下文
- 输出：JSON 结构（sentiment, intensity, urgency, key_emotions, confidence）
- 关键：Prompt 要求模型从客户服务角度理解情绪，而非简单的正负面判断

### 2.2 通用通知服务 (`src/services/notification_service.py`) ✅

**为什么需要**：投诉升级需要通知人工，但未来的智能体（如合同审核、审批流）也需要通知能力。当前系统完全没有通知基础设施。

**设计**：

```
src/services/
└── notification_service.py  # 新增：通用通知服务
```

```python
class NotificationChannel(str, Enum):
    EMAIL = "email"
    WECHAT = "wechat"       # 企业微信消息
    DINGTALK = "dingtalk"    # 钉钉消息
    WEBHOOK = "webhook"      # 通用 Webhook

class NotificationMessage:
    """通知消息"""
    title: str
    content: str
    urgency: str             # low / medium / high / critical
    recipient: str           # 接收人标识（邮箱/用户ID/手机号）
    channel: NotificationChannel
    metadata: Dict[str, Any] # 附加数据（如投诉ID、链接等）

class NotificationService:
    """
    通用通知服务。

    复用场景：
    - 投诉升级通知人工客服
    - 合同审核完成通知申请人
    - 审批流节点变更通知
    - 系统告警通知管理员
    """

    async def send(self, message: NotificationMessage) -> bool:
        """发送通知（根据 channel 自动路由到对应发送器）"""

    async def send_batch(self, messages: List[NotificationMessage]) -> List[bool]:
        """批量发送"""

    def register_channel(self, channel: NotificationChannel, sender):
        """注册通知渠道发送器"""
```

**通知渠道实现优先级**：

| 优先级 | 渠道 | 实现方式 | 说明 |
|--------|------|----------|------|
| P0 | email | 复用现有 `email_send` 工具的 SMTP 逻辑 | 已有基础设施 |
| P1 | webhook | 复用现有 `http_api` 工具 | 通用性强，可对接企微/钉钉机器人 |
| P2 | 企业微信 | 复用现有渠道适配器 | 需要应用消息推送权限 |
| P3 | 钉钉 | 复用现有渠道适配器 | 同上 |

**配置设计**：

```yaml
# configs/config.yaml
notification:
  enabled: true
  default_channel: "email"
  channels:
    email:
      enabled: true
      template_dir: "configs/notification_templates/"
    webhook:
      enabled: false
      default_url: ""
    wechat:
      enabled: false
      corp_id: "your_corp_id"          # 从企业微信「我的企业」获取
      agent_id: "your_agent_id"        # 应用详情页 AgentId
```

### 2.3 通用分类服务 (`src/services/classification_service.py`) ✅

**为什么需要**：投诉分类、工单分类、客户意图分类等都是常见的分类需求。抽象为通用服务可避免各智能体重复实现。

**设计**：

```
src/services/
└── classification_service.py  # 新增：通用文本分类服务
```

```python
class ClassificationService:
    """
    通用文本分类服务，基于 LLM + 预定义分类体系。

    复用场景：
    - 投诉智能体的投诉类型分类
    - 工单系统的工单分类
    - 知识库文档自动分类
    - 客户意图识别
    """

    def classify(self, text: str, categories: List[str],
                 context: str = "") -> ClassificationResult:
        """
        对文本进行分类。

        Args:
            text: 待分类文本
            categories: 预定义分类列表（如 ["产品质量", "服务态度", "物流配送"]）
            context: 可选的上下文信息
        """

    def classify_with_confidence(self, text: str, categories: List[str],
                                 context: str = "") -> ClassificationResult:
        """分类并返回置信度，低于阈值时返回 uncertain"""
```

### 2.4 通用案例匹配服务 (`src/services/case_matching_service.py`) ✅

**为什么需要**：投诉处理需要从历史案例中找相似案例，但案例匹配是一种通用的"经验复用"能力。未来的智能体（如技术支持、售前方案推荐）同样需要"找相似案例"。

**设计**：

```
src/services/
└── case_matching_service.py   # 新增：通用案例匹配服务
```

```python
class CaseMatchingService:
    """
    通用案例匹配服务，基于向量相似度 + 结构化属性匹配。

    复用场景：
    - 投诉智能体的历史案例检索
    - 技术支持智能体的历史工单检索
    - 售前方案推荐的历史方案检索
    """

    def find_similar(self, query: str, domain: str,
                     filters: Dict = None, top_k: int = 5) -> List[CaseMatch]:
        """
        查找相似案例。

        Args:
            query: 查询描述（如投诉内容）
            domain: 领域标识（如 "complaint", "tech_support"）
            filters: 结构化过滤条件（如 {"category": "产品质量", "status": "resolved"}）
            top_k: 返回最多 K 条结果
        """

    def index_case(self, case_id: str, domain: str,
                   content: str, metadata: Dict = None) -> bool:
        """将案例索引进向量库，支持后续语义检索"""
```

**实现策略**：

| 阶段 | 方案 | 说明 |
|------|------|------|
| MVP | LLM 直接匹配 | 将历史案例摘要发给 LLM，让其判断相似度。简单，无需向量库 |
| 优化 | 向量检索 + LLM 重排 | 利用现有知识库的向量基础设施（PGVector），先向量召回再 LLM 精排 |

MVP 阶段优先使用 LLM 直接匹配方案，因为：
- 投诉量在初期不会很大，LLM 能直接处理
- 避免过早引入向量索引的复杂度
- 后续可无缝切换到向量方案

### 2.5 基础设施文件变更汇总

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/services/__init__.py` | 新增 | 服务层包 |
| `src/services/sentiment_service.py` | 新增 | 通用情绪分析 |
| `src/services/notification_service.py` | 新增 | 通用通知服务 |
| `src/services/classification_service.py` | 新增 | 通用分类服务 |
| `src/services/case_matching_service.py` | 新增 | 通用案例匹配 |
| `src/config/settings.py` | 修改 | 新增 notification 配置节 |
| `configs/config.yaml` | 修改 | 新增 notification 配置 |
| `deploy/init-postgres.sql` | 修改 | 无需修改（服务层无独立表） |

---

## 3. 投诉智能体本体设计 ✅ 已完成

### 3.1 整体架构

```
subagents/complaint-handling/
    SUBAGENT.md                    # 智能体定义

src/skills/complaint-core-1.0.0/
    SKILL.md                       # 核心技能定义
    scripts/
        complaint_tool.py          # 核心CLI工具（表初始化+业务操作）
```

### 3.2 SUBAGENT.md 设计 ✅

```yaml
---
name: AI投诉处理专员
description: 专业受理客户投诉，智能分类，快速响应安抚，推荐解决方案，升级预警
version: 1.0.0
author: system
capabilities:
  - complaint_handling
  - complaint_classification
  - customer_emotion_analysis
  - case_matching
  - escalation_management
triggers:
  keywords:
    - 投诉
    - 举报
    - 不满意
    - 差评
    - 维权
    - 315
    - 欺骗
    - 虚假宣传
    - 假货
    - 劣质
    - 投诉处理
    - 客户投诉
tools:
  inherit: true
  additional:
    - http_api
skills:
  allowed:
    - complaint-core
context:
  max_input_tokens: 12000
  max_output_tokens: 4000
business_pages:
  - id: complaint-list
    title: 投诉记录
    icon: clipboard-list
    route: /complaint/list
  - id: complaint-stats
    title: 投诉统计
    icon: chart-bar
    route: /complaint/stats
---

（Markdown body: 系统提示词，见下方 3.3 节）
```

### 3.3 系统提示词设计 ✅

系统提示词（SUBAGENT.md 的 Markdown body）需要定义以下内容：

#### 3.3.1 角色身份

```
你是一位专业的AI投诉处理专员。你的职责是：
1. 认真倾听客户的投诉，让客户感受到被尊重和理解
2. 快速准确地分类投诉，评估严重程度
3. 在安抚客户情绪的同时，积极寻找解决方案
4. 对超出处理能力的问题，及时升级到人工处理

你的沟通风格：
- 共情优先：先理解客户的感受，再处理问题
- 专业冷静：不与客户争执，不回避问题
- 积极主动：主动告知处理进度和预期时间
- 诚实透明：不确定的事情不随意承诺
```

#### 3.3.2 工作流程

```
## 工作流程

收到客户投诉后，按以下步骤处理：

### 第一步：情绪识别与安抚（必须首先执行）

使用 complaint-core 的 `analyze-sentiment` 命令分析客户情绪。
根据分析结果采取对应策略：

| 情绪等级 | 客户表现 | 安抚策略 |
|----------|----------|----------|
| 轻微不满 | 建议性反馈，语气平和 | 感谢反馈，承诺改进 |
| 不满 | 明确抱怨，有具体诉求 | 认同感受，快速给出方案 |
| 愤怒 | 情绪激动，可能有人身攻击 | 深度共情，先处理情绪再处理问题 |
| 极度愤怒 | 威胁曝光/法律行动 | 高度重视，立即升级+安抚 |

**安抚原则**：
- 先说"我理解您的感受"，再处理问题
- 不说"您误会了"、"这不是我们的问题"
- 不说"请您冷静"，而是用行动让客户感受到被重视

### 第二步：投诉分类与建档

使用 complaint-core 的 `classify-complaint` 命令对投诉进行分类。
分类后使用 `create-complaint` 命令建档。

投诉分类体系：

| 一级分类 | 二级分类 | 示例 |
|----------|----------|------|
| 产品质量 | 材质缺陷、功能故障、外观瑕疵 | 产品使用一周就坏了 |
| 服务态度 | 响应慢、态度差、推诿扯皮 | 客服态度很差 |
| 物流配送 | 延迟、损坏、丢失、送错 | 快递等了一周还没到 |
| 虚假宣传 | 夸大宣传、虚假承诺、货不对板 | 收到的和宣传的完全不一样 |
| 售后服务 | 退款慢、换货难、维修不及时 | 申请退款一周了还没处理 |
| 价格争议 | 隐形消费、乱收费、价格不透明 | 结账发现多了很多费用 |
| 隐私安全 | 信息泄露、骚扰电话 | 下单后接到大量推销电话 |
| 其他 | 不属于以上分类的投诉 | — |

紧急程度判定：

| 等级 | 条件 | 处理要求 |
|------|------|----------|
| normal | 单一问题，客户情绪稳定 | 24小时内处理 |
| high | 多个问题叠加或客户不满 | 4小时内处理 |
| urgent | 涉及安全/法律风险，或群体性投诉 | 立即处理 |
| critical | 媒体/监管介入风险 | 立即升级+人工接管 |

### 第三步：解决方案推荐

使用 complaint-core 的 `match-cases` 命令检索相似历史案例。
基于历史案例的处理结果，向客户推荐解决方案。

推荐方案时遵循：
1. 优先推荐成功解决的案例方案
2. 方案需具体可行，不泛泛而谈
3. 给出明确的时间预期
4. 如果有多种方案，说明各自的优缺点

### 第四步：记录处理结果

与客户达成一致后，使用 `update-complaint` 命令记录处理结果。
如果需要后续跟进，使用 `create-followup` 创建跟进任务。

### 第五步（条件触发）：升级处理

当满足以下任一条件时，使用 `escalate-complaint` 命令升级：
- 紧急程度为 urgent 或 critical
- 客户情绪为"极度愤怒"且安抚无效
- 投诉涉及法律/监管风险
- 同一问题被多次投诉（>=3次）
- 单次处理无法解决，需要跨部门协调
```

#### 3.3.3 工具使用规则

```
## 工具使用规则

1. **投诉处理的所有数据操作都通过 complaint-core 技能完成**
2. 使用流程：`use_skill("complaint-core")` → 按需调用各命令 → 直接给出最终回复
3. 如果配置了外部投诉系统API，通过 `http_api` 工具同步数据

## 安全规则

- 不向客户暴露内部分类逻辑和升级规则
- 不在回复中提及"系统分析您的情绪为xxx"
- 不向客户展示其他客户的投诉信息
- 涉及法律风险的投诉，建议客户保留证据并引导至正规渠道
```

### 3.4 技能设计（complaint-core） ✅

#### 3.4.1 SKILL.md 设计

```yaml
---
name: complaint-core
description: >
  投诉处理核心技能，提供投诉建档、分类、案例匹配、升级处理等CLI操作。
  所有投诉数据操作都通过本技能完成。
init_script: complaint_tool.py
metadata:
  openclaw:
    emoji: "📋"
    requires:
      bins: ["python"]
---
```

#### 3.4.2 CLI 命令设计

| 命令 | 功能 | 必要参数 | 可选参数 |
|------|------|----------|----------|
| `analyze-sentiment` | 分析客户情绪 | `--text` | `--context` |
| `classify-complaint` | 投诉分类 | `--description` | `--context` |
| `create-complaint` | 创建投诉记录 | `--user-id`, `--description`, `--category` | `--order-id`, `--urgency`, `--customer-emotion`, `--contact-info`, `--tenant-id`, `--session-id` |
| `update-complaint` | 更新投诉状态 | `--complaint-id`, `--status` | `--resolution`, `--assigned-to`, `--notes` |
| `match-cases` | 匹配相似历史案例 | `--description` | `--category`, `--top-k` |
| `escalate-complaint` | 升级投诉 | `--complaint-id`, `--reason` | `--escalate-to`, `--urgency` |
| `list-complaints` | 查询投诉列表 | `--user-id` | `--status`, `--category`, `--urgency`, `--limit` |
| `get-complaint` | 查询单条投诉详情 | `--complaint-id` | — |
| `create-followup` | 创建跟进任务 | `--complaint-id`, `--action`, `--due-date` | `--assigned-to` |
| `stats` | 投诉统计 | — | `--period`, `--group-by` |
| `add-interaction` | 记录交互记录 | `--complaint-id`, `--content`, `--sender-type` | `--interaction-type` |

#### 3.4.3 命令详细设计

**`analyze-sentiment`** — 情绪分析

```bash
python scripts/complaint_tool.py analyze-sentiment \
  --text "你们的产品质量太差了！用了一周就坏了，我要求退款！" \
  --context "客户购买产品7天后反馈故障"
```

返回：
```json
{
  "success": true,
  "sentiment": "angry",
  "intensity": 0.8,
  "urgency": "high",
  "key_emotions": ["愤怒", "失望", "要求赔偿"],
  "suggested_response_tone": "高度共情，快速响应，主动提供补偿方案"
}
```

实现方式：调用 `SentimentService.analyze()`，封装为 CLI 命令。

**`classify-complaint`** — 投诉分类

```bash
python scripts/complaint_tool.py classify-complaint \
  --description "产品使用一周就坏了，联系客服说过了退换期不给处理"
```

返回：
```json
{
  "success": true,
  "category": "产品质量",
  "sub_category": "功能故障",
  "urgency": "high",
  "confidence": 0.92,
  "suggested_tags": ["产品质量", "功能故障", "售后服务", "退款"]
}
```

实现方式：调用 `ClassificationService.classify()` + 紧急程度评估逻辑。

**`create-complaint`** — 创建投诉记录

```bash
python scripts/complaint_tool.py create-complaint \
  --user-id "user_xxx" \
  --description "产品使用一周就坏了" \
  --category "产品质量" \
  --urgency "high" \
  --customer-emotion "angry" \
  --order-id "ORD-2026-001" \
  --tenant-id "tenant_xxx" \
  --session-id "sess_xxx"
```

返回：
```json
{
  "success": true,
  "complaint_id": "cpl_a1b2c3d4e5f6",
  "category": "产品质量",
  "sub_category": "功能故障",
  "urgency": "high",
  "status": "open",
  "created_at": "2026-05-25T14:30:00"
}
```

**`match-cases`** — 相似案例匹配

```bash
python scripts/complaint_tool.py match-cases \
  --description "产品使用一周就出现功能故障" \
  --category "产品质量" \
  --top-k 3
```

返回：
```json
{
  "success": true,
  "matches": [
    {
      "complaint_id": "cpl_xxx",
      "similarity": 0.85,
      "category": "产品质量",
      "description": "...",
      "resolution": "更换新产品+补偿优惠券",
      "customer_satisfied": true,
      "resolution_time_hours": 4
    }
  ],
  "match_count": 3
}
```

实现方式：调用 `CaseMatchingService.find_similar()`。

**`escalate-complaint`** — 升级处理

```bash
python scripts/complaint_tool.py escalate-complaint \
  --complaint-id "cpl_a1b2c3d4e5f6" \
  --reason "客户情绪极度愤怒，涉及法律风险" \
  --urgency "critical"
```

返回：
```json
{
  "success": true,
  "complaint_id": "cpl_a1b2c3d4e5f6",
  "escalated": true,
  "previous_urgency": "high",
  "new_urgency": "critical",
  "notification_sent": true,
  "notification_channel": "email",
  "assigned_to": "投诉主管-张经理"
}
```

实现方式：更新投诉状态 + 调用 `NotificationService.send()`。

### 3.5 数据库表设计 ✅

#### 表 1：投诉主表 `bs_complaint_handling_complaints`

```sql
CREATE TABLE IF NOT EXISTS bs_complaint_handling_complaints (
    id SERIAL PRIMARY KEY,
    complaint_id TEXT UNIQUE NOT NULL,          -- 投诉ID，格式 cpl_xxxxxxxxxxxx
    tenant_id TEXT,                             -- 租户ID
    user_id TEXT NOT NULL,                      -- 用户ID（投诉人）
    session_id TEXT,                            -- 关联的会话ID
    order_id TEXT,                              -- 关联的订单ID（可选）
    customer_name TEXT,                         -- 客户姓名
    contact_info TEXT,                          -- 客户联系方式

    -- 分类信息
    category TEXT NOT NULL,                     -- 一级分类：产品质量/服务态度/物流配送/虚假宣传/售后服务/价格争议/隐私安全/其他
    sub_category TEXT,                          -- 二级分类
    tags TEXT,                                  -- 标签（JSON数组）

    -- 情绪分析
    customer_emotion TEXT,                      -- 客户情绪：neutral/dissatisfied/angry/furious
    emotion_intensity REAL,                     -- 情绪强度 0.0~1.0

    -- 严重程度与状态
    urgency TEXT NOT NULL DEFAULT 'normal',     -- 紧急程度：normal/high/urgent/critical
    status TEXT NOT NULL DEFAULT 'open',        -- 状态：open/classifying/in_progress/resolved/closed/escalated
    escalation_level INT DEFAULT 0,             -- 升级层级：0=未升级, 1=主管, 2=经理, 3=总监

    -- 内容
    description TEXT NOT NULL,                  -- 投诉描述
    resolution TEXT,                            -- 处理结果/解决方案

    -- 升级信息
    escalated_to TEXT,                          -- 升级给谁
    escalation_reason TEXT,                     -- 升级原因
    escalated_at TIMESTAMP,                     -- 升级时间

    -- 时间
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,                      -- 解决时间
    first_response_at TIMESTAMP                 -- 首次响应时间
);

CREATE INDEX IF NOT EXISTS idx_complaints_tenant_status
    ON bs_complaint_handling_complaints (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_complaints_user_status
    ON bs_complaint_handling_complaints (user_id, status);
CREATE INDEX IF NOT EXISTS idx_complaints_urgency
    ON bs_complaint_handling_complaints (urgency, status);
CREATE INDEX IF NOT EXISTS idx_complaints_category
    ON bs_complaint_handling_complaints (category);
CREATE INDEX IF NOT EXISTS idx_complaints_created
    ON bs_complaint_handling_complaints (created_at DESC);
```

#### 表 2：投诉交互记录 `bs_complaint_handling_interactions`

记录投诉处理过程中的每一次交互（客户消息、系统回复、内部备注、升级通知等）。

```sql
CREATE TABLE IF NOT EXISTS bs_complaint_handling_interactions (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    complaint_id TEXT NOT NULL,                 -- 关联的投诉ID
    interaction_type TEXT NOT NULL DEFAULT 'message',  -- message/note/escalation/notification/status_change
    sender_type TEXT NOT NULL,                  -- customer/agent/system/supervisor
    sender_name TEXT,                           -- 发送者名称
    content TEXT NOT NULL,                      -- 交互内容
    metadata TEXT,                              -- 附加数据（JSON）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_interactions_complaint
    ON bs_complaint_handling_interactions (complaint_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_interactions_tenant
    ON bs_complaint_handling_interactions (tenant_id);
```

#### 表 3：历史案例解决方案库 `bs_complaint_handling_case_solutions`

存储已解决投诉的处理方案，作为未来案例匹配的数据源。

```sql
CREATE TABLE IF NOT EXISTS bs_complaint_handling_case_solutions (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    complaint_id TEXT UNIQUE NOT NULL,          -- 关联的原始投诉ID
    category TEXT NOT NULL,                     -- 一级分类
    sub_category TEXT,                          -- 二级分类
    problem_summary TEXT NOT NULL,              -- 问题摘要（用于匹配）
    root_cause TEXT,                            -- 根本原因
    solution TEXT NOT NULL,                     -- 解决方案
    resolution_time_hours REAL,                 -- 处理耗时（小时）
    customer_satisfied BOOLEAN,                 -- 客户是否满意
    compensation_type TEXT,                     -- 补偿类型：none/refund/replace/coupon/apology/other
    compensation_amount NUMERIC(12,2),          -- 补偿金额
    effective BOOLEAN DEFAULT true,             -- 方案是否有效（可用于标记无效方案）
    tags TEXT,                                  -- 标签（JSON数组）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_case_solutions_category
    ON bs_complaint_handling_case_solutions (category);
CREATE INDEX IF NOT EXISTS idx_case_solutions_tenant
    ON bs_complaint_handling_case_solutions (tenant_id);
CREATE INDEX IF NOT EXISTS idx_case_solutions_effective
    ON bs_complaint_handling_case_solutions (effective, category);
```

#### 表 4：跟进任务表 `bs_complaint_handling_followups`

```sql
CREATE TABLE IF NOT EXISTS bs_complaint_handling_followups (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    complaint_id TEXT NOT NULL,                 -- 关联的投诉ID
    action TEXT NOT NULL,                       -- 跟进动作描述
    assigned_to TEXT,                           -- 负责人
    due_date TIMESTAMP,                         -- 截止时间
    status TEXT NOT NULL DEFAULT 'pending',     -- pending/in_progress/done/skipped
    completed_at TIMESTAMP,                     -- 完成时间
    notes TEXT,                                 -- 备注
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_followups_complaint
    ON bs_complaint_handling_followups (complaint_id, status);
CREATE INDEX IF NOT EXISTS idx_followups_due
    ON bs_complaint_handling_followups (due_date, status);
CREATE INDEX IF NOT EXISTS idx_followups_tenant
    ON bs_complaint_handling_followups (tenant_id);
```

### 3.6 数据流全景

```
客户投诉消息到达
    │
    ▼
主智能体识别投诉意图（通过 keywords trigger）
    │
    ▼
委托给 complaint-handling 子智能体
    │
    ├─ Step 1: analyze-sentiment → SentimentService → 情绪分析结果
    │                                         │
    │                                         ├─ 生成安抚回复
    │                                         └─ 确定处理优先级
    │
    ├─ Step 2: classify-complaint → ClassificationService → 分类结果
    │                                           │
    │                                           ├─ 一级分类 + 二级分类
    │                                           └─ 紧急程度评估
    │
    ├─ Step 3: create-complaint → DB 写入 → 投诉记录建档
    │
    ├─ Step 4: match-cases → CaseMatchingService → 相似历史案例
    │                                      │
    │                                      ├─ 提取成功方案
    │                                      └─ 生成推荐解决方案
    │
    ├─ Step 5: 向客户呈现方案，等待反馈
    │
    ├─ [条件] 客户满意:
    │   ├─ update-complaint → status=resolved, 记录 resolution
    │   └─ 记录到 case_solutions 表（供未来匹配）
    │
    ├─ [条件] 需要升级:
    │   ├─ escalate-complaint → status=escalated
    │   ├─ NotificationService.send() → 通知人工
    │   └─ 等待人工接管
    │
    └─ 直接给出最终回复 → 总结处理结果
```

---

## 4. 与现有系统的集成点

### 4.1 与售后智能体的关系

投诉处理和售后服务是**互补关系**，不是替代关系：

| 维度 | 售后智能体 (after-sales) | 投诉智能体 (complaint-handling) |
|------|--------------------------|-------------------------------|
| 触发场景 | 退货/换货/退款/维修等具体诉求 | 不满/投诉/举报等情绪化诉求 |
| 处理重点 | 流程化操作（查订单、建工单、退换货） | 情绪安抚 + 问题解决 + 升级预警 |
| 客户情绪 | 通常较平稳 | 通常情绪激动 |
| 数据关联 | order_id 驱动 | complaint_id 驱动，可关联 order_id |

**协作场景**：客户先找售后处理退货，处理不满意转为投诉 → 售后智能体可委托给投诉智能体。

### 4.2 与知识库的集成

- 投诉处理方案库（`case_solutions` 表）是独立的业务数据
- 但可以**同时**将典型案例索引到知识库向量数据库，利用现有 `knowledge_base_search` 工具进行语义检索
- 这是 Phase 2 的优化方向

### 4.3 与通知系统的集成

- 升级通知通过 `NotificationService` 发送
- 初期使用 email 渠道（复用现有 SMTP 配置）
- 后续可扩展企微/钉钉/短信渠道

---

## 5. 文件变更清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `subagents/complaint-handling/SUBAGENT.md` | 智能体定义 |
| `src/skills/complaint-core-1.0.0/SKILL.md` | 核心技能定义 |
| `src/skills/complaint-core-1.0.0/scripts/complaint_tool.py` | CLI 工具实现 |
| `src/services/__init__.py` | 服务层包 |
| `src/services/sentiment_service.py` | 情绪分析服务（通用） |
| `src/services/notification_service.py` | 通知服务（通用） |
| `src/services/classification_service.py` | 分类服务（通用） |
| `src/services/case_matching_service.py` | 案例匹配服务（通用） |

### 修改文件

| 文件 | 说明 |
|------|------|
| `src/config/settings.py` | 新增 notification 配置节 |
| `configs/config.yaml` | 新增 notification 配置 |
| `deploy/init-postgres.sql` | 添加 4 张新表 |
| `deploy/db_update.sql` | 记录增量变更 |

---

## 6. 开发计划

### Phase 1: 基础设施（预计 3-4 天） ✅ 已完成

| 任务 | 工期 | 优先级 | 说明 | 状态 |
|------|------|--------|------|------|
| 1.1 通用情绪分析服务 | 1 天 | P0 | SentimentService 实现 + 单元测试 | ✅ 已完成 |
| 1.2 通用分类服务 | 0.5 天 | P0 | ClassificationService 实现 + 单元测试 | ✅ 已完成 |
| 1.3 通用案例匹配服务 | 1 天 | P1 | CaseMatchingService（MVP: LLM 方案） | ✅ 已完成 |
| 1.4 通用通知服务 | 1 天 | P0 | NotificationService + email 渠道 | ✅ 已完成 |

### Phase 2: 投诉智能体核心（预计 3-4 天） ✅ 已完成

| 任务 | 工期 | 优先级 | 说明 | 状态 |
|------|------|--------|------|------|
| 2.1 数据库表设计与创建 | 0.5 天 | P0 | 4 张表的 init_tables + SQL 更新 | ✅ 已完成 |
| 2.2 complaint-core CLI 工具 | 2 天 | P0 | 11 个 CLI 命令实现 | ✅ 已完成 |
| 2.3 SUBAGENT.md 编写 | 0.5 天 | P0 | 智能体定义 + 系统提示词 | ✅ 已完成 |
| 2.4 集成测试 | 1 天 | P0 | 完整投诉流程测试 | ⏳ 待测试 |

### Phase 3: 前端页面与优化（预计 2-3 天） ✅ 大部分已完成

| 任务 | 工期 | 优先级 | 说明 | 状态 |
|------|------|--------|------|------|
| 3.1 投诉列表页面 | 1 天 | P1 | 投诉记录查看/筛选 | ✅ 已完成 |
| 3.2 投诉统计页面 | 1 天 | P2 | 分类统计/趋势图表 | ✅ 已完成 |
| 3.3 通知渠道扩展 | 0.5 天 | P2 | 企微/钉钉通知 | ⏳ 待开发 |

---

## 7. 风险与注意事项

1. **LLM 调用成本**：情绪分析、分类、案例匹配都需要调用 LLM，单次投诉处理可能产生 3-5 次 LLM 调用。建议使用较小模型（如 qwen-turbo）执行分析类任务，降低成本。

2. **情绪分析的准确性**：LLM 对中文情绪的分析准确率约 85-90%，可能存在误判。设计上应允许人工修正分类和情绪标签。

3. **案例匹配的冷启动**：初始阶段 `case_solutions` 表为空，无法匹配历史案例。建议预置一批典型案例作为种子数据。

4. **通知的可靠性**：email 通知可能被归为垃圾邮件。建议增加通知状态跟踪（sent/delivered/failed），失败时重试。

5. **投诉数据的敏感性**：投诉内容可能包含客户隐私（姓名、电话、订单信息）。API 返回时需脱敏处理，遵循项目安全原则。

6. **并发投诉处理**：同一客户可能通过不同渠道同时发起投诉。需要去重机制（基于 user_id + 时间窗口 + 内容相似度）。
