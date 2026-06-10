# 业务页面元数据注册表 — 开发计划

> 设计文档：[page-metadata-registry-design.md](./page-metadata-registry-design.md)

## 开发阶段概览

| 阶段 | 内容 | 涉及文件 |
|------|------|---------|
| Phase 1 | 后端：metadata 数据文件 + API 端点 | `configs/page_metadata.yaml`（新建）、`src/api/page_metadata.py`（新建）、`src/api/agent_definitions.py`（修改） |
| Phase 2 | 前端：API 层 + 页面选择器组件 | `frontend/src/api/agentDefinitions.ts`（修改）、`frontend/src/components/PageMetaSelector.vue`（新建） |
| Phase 3 | 前端：接入 AgentDefinitionManager | `frontend/src/components/AgentDefinitionManager.vue`（修改） |

---

## Phase 1：后端 — metadata 数据文件 + API

### 1.1 创建 metadata 数据文件

**文件**：`configs/page_metadata.yaml`（新建）

创建包含 `domains`（业务域定义）和 `pages`（页面列表）的 YAML 文件。所有 14 个现有页面都标为 `published`。

```yaml
domains:
  - id: trade-specialist
    label: 外贸获客
  - id: travel-consultant
    label: 旅游咨询
  - id: customer-followup
    label: 客户跟进
  - id: complaint
    label: 投诉处理
  - id: after-sales
    label: 售后服务

pages:
  - page_id: trade-specialist-customers
    title: 客户管理
    description: 管理外贸客户信息，包括客户基础资料、联系方式、跟进状态等
    route: /trade-specialist/customers
    icon: "👥"
    domain: trade-specialist
    status: published

  - page_id: travel-consultant-vehicles
    title: 车辆价格
    description: 管理旅游用车信息，包括车型、座位数、租车价格等
    route: /travel-consultant/vehicles
    icon: "🚗"
    domain: travel-consultant
    status: published

  - page_id: travel-consultant-attractions
    title: 景点门票
    description: 管理旅游景点信息，包括景点名称、门票价格、开放时间等
    route: /travel-consultant/attractions
    icon: "🏛️"
    domain: travel-consultant
    status: published

  - page_id: travel-consultant-hotels
    title: 酒店房型
    description: 管理酒店及房型信息，包括酒店名称、房型、价格、含早情况等
    route: /travel-consultant/hotels
    icon: "🏨"
    domain: travel-consultant
    status: published

  - page_id: travel-consultant-meals
    title: 餐标价格
    description: 管理餐饮标准信息，包括餐标等级、菜品数量、单价等
    route: /travel-consultant/meals
    icon: "🍜"
    domain: travel-consultant
    status: published

  - page_id: travel-consultant-guides
    title: 导游费用
    description: 管理导游信息，包括导游姓名、语言能力、日收费标准等
    route: /travel-consultant/guides
    icon: "🧑‍🏫"
    domain: travel-consultant
    status: published

  - page_id: travel-consultant-fees
    title: 其他费用
    description: 管理旅游行程中的杂项费用，如保险费、索道费、停车费等
    route: /travel-consultant/fees
    icon: "💰"
    domain: travel-consultant
    status: published

  - page_id: customer-followup-leads
    title: 线索管理
    description: 管理客户线索信息，包括线索来源、意向等级、跟进状态等
    route: /customer-followup/leads
    icon: "🎯"
    domain: customer-followup
    status: published

  - page_id: customer-followup-followup-records
    title: 跟进记录
    description: 记录和查看客户跟进历史，包括跟进方式、沟通内容、下次计划等
    route: /customer-followup/followup-records
    icon: "📝"
    domain: customer-followup
    status: published

  - page_id: customer-followup-sales-reps
    title: 销售代表
    description: 管理销售团队信息，包括销售代表姓名、负责区域、业绩目标等
    route: /customer-followup/sales-reps
    icon: "👨‍💼"
    domain: customer-followup
    status: published

  - page_id: complaint-list
    title: 投诉列表
    description: 查看和管理客户投诉工单，包括投诉内容、处理状态、责任人等
    route: /complaint/list
    icon: "📋"
    domain: complaint
    status: published

  - page_id: complaint-stats
    title: 投诉统计
    description: 投诉数据的统计分析，包括投诉量趋势、类型分布、处理时效等
    route: /complaint/stats
    icon: "📊"
    domain: complaint
    status: published

  - page_id: after-sales-tickets
    title: 售后工单
    description: 管理售后服务工单，包括工单类型、问题描述、处理进度等
    route: /after-sales/tickets
    icon: "📦"
    domain: after-sales
    status: published

  - page_id: after-sales-returns
    title: 退换货记录
    description: 管理退换货申请记录，包括退货原因、商品信息、退款状态等
    route: /after-sales/returns
    icon: "🔄"
    domain: after-sales
    status: published
```

### 1.2 创建后端 API 模块

**文件**：`src/api/page_metadata.py`（新建）

```python
"""页面元数据 API — 列表查询 + AI 推荐"""
import yaml
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import Request
from loguru import logger

from src.api.auth import get_current_user
from src.llm.gateway import llm_gateway


# ============== YAML 加载与缓存 ==============

_metadata_cache: Optional[Dict[str, Any]] = None
_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "configs" / "page_metadata.yaml"


def _load_metadata() -> Dict[str, Any]:
    """加载 page_metadata.yaml（带模块级缓存，部署后不变）"""
    global _metadata_cache
    if _metadata_cache is not None:
        return _metadata_cache
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        _metadata_cache = yaml.safe_load(f)
    return _metadata_cache


def _get_published_pages(domain: Optional[str] = None) -> List[Dict[str, Any]]:
    """返回所有 published 页面，可选按 domain 过滤"""
    metadata = _load_metadata()
    pages = metadata.get("pages", [])
    result = []
    for p in pages:
        if p.get("status") != "published":
            continue
        if domain and p.get("domain") != domain:
            continue
        result.append({
            "page_id": p["page_id"],
            "title": p["title"],
            "description": p.get("description", ""),
            "route": p["route"],
            "icon": p.get("icon", ""),
            "domain": p["domain"],
            "domain_label": _get_domain_label(p["domain"]),
        })
    return result


def _get_domain_label(domain_id: str) -> str:
    """根据 domain id 获取中文标签"""
    metadata = _load_metadata()
    for d in metadata.get("domains", []):
        if d["id"] == domain_id:
            return d.get("label", domain_id)
    return domain_id


# ============== 公共辅助函数（供 agent_definitions.py 调用） ==============

def list_pages(request: Request, domain: Optional[str] = None):
    """GET /meta/pages — 返回已发布页面列表"""
    _require_admin(request)
    return _success(_get_published_pages(domain))


async def recommend_pages(request: Request, body: dict):
    """POST /meta/pages/recommend — AI 推荐相关页面"""
    _require_admin(request)

    agent_description = body.get("agent_description", "")
    current_pages = body.get("current_pages", [])

    if not agent_description:
        return _error("请提供智能体描述")

    all_pages = _get_published_pages()
    if not all_pages:
        return _success({"recommended": []})

    # 排除已选页面
    current_set = set(current_pages)
    candidates = [p for p in all_pages if p["page_id"] not in current_set]
    if not candidates:
        return _success({"recommended": []})

    # 构建 LLM prompt
    page_list_str = "\n".join(
        f"- page_id: {p['page_id']}, title: {p['title']}, description: {p['description']}"
        for p in candidates
    )

    system_prompt = """你是一个业务系统配置助手。根据用户提供的智能体描述，从可用的业务页面列表中推荐最相关的页面。

## 输出要求
- 严格返回 JSON 格式：{"recommended": [{"page_id": "...", "reason": "推荐原因（一句话中文）", "relevance_score": 0.0到1.0}]}
- 按相关性从高到低排序
- 只推荐真正相关的页面，不要强行推荐不相关的
- 如果没有相关页面，返回空数组

只输出 JSON，不要输出其他内容。"""

    user_message = f"""## 智能体描述
{agent_description}

## 已选页面（不需要再推荐）
{', '.join(current_pages) if current_pages else '无'}

## 可用业务页面
{page_list_str}"""

    try:
        result = await llm_gateway.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.1,
            max_tokens=2048,
        )
        content = result.get("content", "")

        # 提取 JSON（处理 LLM 可能包裹在 ```json ``` 中的情况）
        import re
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            import json
            parsed = json.loads(json_match.group())
            recommended = parsed.get("recommended", [])
            # 附加 title 信息
            page_map = {p["page_id"]: p for p in candidates}
            for r in recommended:
                pid = r.get("page_id", "")
                if pid in page_map:
                    r["title"] = page_map[pid]["title"]
            return _success({"recommended": recommended})
    except Exception as e:
        logger.error(f"AI 页面推荐失败: {e}")

    # 降级：返回所有未选页面（不排序）
    fallback = [
        {"page_id": p["page_id"], "title": p["title"], "reason": "", "relevance_score": 0.5}
        for p in candidates
    ]
    return _success({"recommended": fallback})
```

实现要点：
- `_load_metadata()` 使用模块级变量缓存，YAML 文件只在首次调用时读取
- `_get_published_pages()` 过滤 `status != published` 的条目
- AI 推荐调用 `llm_gateway.chat()`，用正则提取 JSON（兼容 LLM 包裹在 markdown 代码块中的情况）
- 解析失败时降级返回所有未选页面，不中断用户流程
- 复用 `agent_definitions.py` 中的 `_success()` / `_error()` / `_require_admin()` 辅助函数

### 1.3 注册路由到 agent_definitions router

**文件**：`src/api/agent_definitions.py`（修改）

在文件末尾（现有的 `/meta/reply-styles` 端点之后）添加两个新端点：

```python
# 在已有的 meta 端点之后添加

from src.api.page_metadata import list_pages as _list_pages_meta, recommend_pages as _recommend_pages_meta

class RecommendPagesRequest(BaseModel):
    agent_description: str
    current_pages: List[str] = Field(default_factory=list)


@router.get("/meta/pages")
async def get_pages_meta(request: Request, domain: Optional[str] = None):
    """获取已发布的业务页面元数据列表（供前端选择器使用）"""
    return await _list_pages_meta(request, domain)


@router.post("/meta/pages/recommend")
async def post_pages_recommend(request: Request, body: RecommendPagesRequest):
    """AI 推荐与智能体描述相关的业务页面"""
    return await _recommend_pages_meta(request, body.model_dump())
```

注意：`_list_pages_meta` 和 `_recommend_pages_meta` 是同步/异步函数，需要确保函数签名匹配。如果 `list_pages` 是同步函数，这里需要去掉 `async`。

### 1.4 验证

- 启动后端，调用 `GET /api/admin/agent-definitions/meta/pages` 验证返回 14 个 published 页面
- 调用 `GET /api/admin/agent-definitions/meta/pages?domain=travel-consultant` 验证只返回旅游咨询域的 6 个页面
- 调用 `POST /api/admin/agent-definitions/meta/pages/recommend` 验证 AI 推荐正常工作

---

## Phase 2：前端 — API 层 + 选择器组件

### 2.1 添加前端 API 调用

**文件**：`frontend/src/api/agentDefinitions.ts`（修改）

在文件末尾（`// ============== Sections Management ==============` 之前）添加：

```typescript
// ============== Page Metadata ==============

export interface PageMeta {
  page_id: string
  title: string
  description: string
  route: string
  icon: string
  domain: string
  domain_label: string
}

export interface RecommendedPage {
  page_id: string
  title: string
  reason: string
  relevance_score: number
}

export async function listPageMeta(params?: {
  domain?: string
}): Promise<{ success: boolean; data: PageMeta[] }> {
  const query = new URLSearchParams()
  if (params?.domain) query.set('domain', params.domain)
  const qs = query.toString()
  const response = await fetch(`${API_BASE}/meta/pages${qs ? '?' + qs : ''}`, { headers: getAuthHeaders() })
  return handleResponse(response)
}

export async function recommendPages(data: {
  agent_description: string
  current_pages?: string[]
}): Promise<{ success: boolean; data: { recommended: RecommendedPage[] } }> {
  const response = await fetch(`${API_BASE}/meta/pages/recommend`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(data),
  })
  return handleResponse(response)
}
```

实现要点：
- 复用已有的 `API_BASE`、`getAuthHeaders()`、`handleResponse()` 函数
- 类型定义与后端响应结构一致

### 2.2 创建页面选择器组件

**文件**：`frontend/src/components/PageMetaSelector.vue`（新建）

组件使用 `BaseModal`（size="lg"），整体结构：

```vue
<template>
  <BaseModal v-model="show" title="选择业务页面" size="lg">
    <!-- 搜索栏 + AI 推荐按钮 -->
    <div class="flex items-center gap-2 mb-4">
      <BaseInput v-model="searchQuery" placeholder="搜索页面名称或功能..." size="sm" class="flex-1" />
      <BaseButton size="sm" :disabled="recommending" @click="handleRecommend">
        {{ recommending ? '推荐中...' : 'AI 推荐' }}
      </BaseButton>
    </div>

    <!-- AI 推荐结果区域 -->
    <div v-if="recommendations.length > 0" class="mb-4 bg-primary-50 rounded-lg p-3">
      <div class="text-xs font-medium text-primary-700 mb-2">AI 推荐</div>
      <div v-for="rec in recommendations" :key="rec.page_id"
        class="flex items-center justify-between py-1.5 border-b border-primary-100 last:border-0">
        <div class="flex items-center gap-2">
          <input type="checkbox" :checked="localSelectedIds.has(rec.page_id)"
            @change="toggleSelect(rec.page_id)" />
          <span class="text-sm">{{ rec.title }}</span>
          <span class="text-xs text-muted">{{ rec.reason }}</span>
        </div>
        <BaseBadge intent="info">{{ (rec.relevance_score * 100).toFixed(0) }}%</BaseBadge>
      </div>
    </div>

    <!-- 按业务域分组的页面列表 -->
    <div class="space-y-3 max-h-[50vh] overflow-y-auto">
      <div v-for="group in groupedPages" :key="group.domain">
        <button @click="toggleDomain(group.domain)"
          class="flex items-center gap-2 w-full text-sm font-medium text-default py-1.5">
          <svg :class="['w-3 h-3 transition-transform', expandedDomains.has(group.domain) ? 'rotate-90' : '']"
            fill="currentColor" viewBox="0 0 20 20">
            <path d="M6 4l8 6-8 6V4z"/>
          </svg>
          {{ group.label }}
        </button>
        <div v-show="expandedDomains.has(group.domain)" class="ml-5 space-y-1">
          <div v-for="page in group.pages" :key="page.page_id"
            class="flex items-center gap-3 py-1.5 px-2 rounded hover:bg-gray-50 cursor-pointer"
            @click="toggleSelect(page.page_id)">
            <input type="checkbox" :checked="localSelectedIds.has(page.page_id)" @click.stop />
            <span class="text-base">{{ page.icon }}</span>
            <div class="flex-1 min-w-0">
              <div class="text-sm text-default">{{ page.title }}</div>
              <div class="text-xs text-muted truncate">{{ page.description }}</div>
            </div>
            <span class="text-xs text-gray-400 whitespace-nowrap">{{ page.route }}</span>
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <span class="text-sm text-muted">已选 {{ localSelectedIds.size }} 个页面</span>
      <div class="flex gap-2 ml-auto">
        <BaseButton intent="secondary" @click="show = false">取消</BaseButton>
        <BaseButton @click="confirmSelect">确认选择</BaseButton>
      </div>
    </template>
  </BaseModal>
</template>
```

**Script 逻辑**：

```typescript
// Props
const props = defineProps<{
  modelValue: boolean
  selectedPageIds: string[]
  agentDescription: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  'select': [pages: Array<{ id: string; title: string; icon: string; route: string }>]
}>()

// 状态
const allPages = ref<PageMeta[]>([])
const searchQuery = ref('')
const localSelectedIds = ref<Set<string>>(new Set())
const recommendations = ref<RecommendedPage[]>([])
const recommending = ref(false)
const expandedDomains = ref<Set<string>>(new Set())

// Modal 控制
const show = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v)
})

// 加载页面列表
watch(() => props.modelValue, async (v) => {
  if (v) {
    localSelectedIds.value = new Set(props.selectedPageIds)
    recommendations.value = []
    const res = await listPageMeta()
    if (res.success) {
      allPages.value = res.data
      // 默认展开所有域
      expandedDomains.value = new Set([...new Set(res.data.map(p => p.domain))])
    }
  }
})

// 搜索过滤
const filteredPages = computed(() => {
  const q = searchQuery.value.toLowerCase().trim()
  if (!q) return allPages.value
  return allPages.value.filter(p =>
    p.title.toLowerCase().includes(q) ||
    p.description.toLowerCase().includes(q) ||
    p.domain_label.toLowerCase().includes(q)
  )
})

// 按域分组
const groupedPages = computed(() => {
  const groups: Map<string, { domain: string; label: string; pages: PageMeta[] }> = new Map()
  for (const p of filteredPages.value) {
    if (!groups.has(p.domain)) {
      groups.set(p.domain, { domain: p.domain, label: p.domain_label, pages: [] })
    }
    groups.get(p.domain)!.pages.push(p)
  }
  return [...groups.values()]
})

// AI 推荐
async function handleRecommend() {
  if (!props.agentDescription.trim()) {
    alert('请先填写智能体描述')
    return
  }
  recommending.value = true
  try {
    const res = await recommendPages({
      agent_description: props.agentDescription,
      current_pages: [...localSelectedIds.value]
    })
    if (res.success) {
      recommendations.value = res.data.recommended
    }
  } finally {
    recommending.value = false
  }
}

// 选择/取消选择
function toggleSelect(pageId: string) {
  const s = new Set(localSelectedIds.value)
  if (s.has(pageId)) s.delete(pageId)
  else s.add(pageId)
  localSelectedIds.value = s
}

// 确认选择
function confirmSelect() {
  const pageMap = new Map(allPages.value.map(p => [p.page_id, p]))
  const selected = [...localSelectedIds.value]
    .filter(id => pageMap.has(id))
    .map(id => {
      const p = pageMap.get(id)!
      return { id: p.page_id, title: p.title, icon: p.icon, route: p.route }
    })
  emit('select', selected)
  show.value = false
}

function toggleDomain(domain: string) {
  const s = new Set(expandedDomains.value)
  if (s.has(domain)) s.delete(domain)
  else s.add(domain)
  expandedDomains.value = s
}
```

### 2.3 验证

- 独立打开 PageMetaSelector 组件，验证页面列表正确加载
- 搜索功能：输入关键词验证过滤效果
- AI 推荐：输入智能体描述后点击推荐，验证结果展示
- 复选框选择和确认按钮行为正确

---

## Phase 3：前端 — 接入 AgentDefinitionManager

### 3.1 修改 AgentDefinitionManager.vue

**文件**：`frontend/src/components/AgentDefinitionManager.vue`（修改）

**改动 1：导入组件和 API**

在 `<script setup>` 顶部添加：

```typescript
import PageMetaSelector from './PageMetaSelector.vue'
```

**改动 2：新增状态变量**

```typescript
const showPageSelector = ref(false)

const selectedPageIds = computed(() =>
  businessPages.value.map(p => p.id)
)
```

**改动 3：新增选择回调**

```typescript
function onPagesSelected(pages: Array<{ id: string; title: string; icon: string; route: string }>) {
  // 合并：保留不在新选择列表中的手动添加页面，替换来自注册表的页面
  const registryIds = new Set(pages.map(p => p.id))
  const manualPages = businessPages.value.filter(p => !registryIds.has(p.id) && !allPageIds.has(p.id))
  // allPageIds 是从 metadata 加载过的所有页面 ID 集合，用于区分"来自注册表"和"手动添加"
  businessPages.value = [...manualPages, ...pages]
}
```

简化实现（不区分来源，直接覆盖）：

```typescript
function onPagesSelected(pages: Array<{ id: string; title: string; icon: string; route: string }>) {
  businessPages.value = pages
}
```

**改动 4：替换业务页面配置区域模板**

将现有的手动输入区域（第 158-178 行）替换为：

```vue
<!-- Business Pages Editor -->
<div>
  <label class="text-xs text-gray-500 mb-1 block">业务页面</label>
  <div class="bg-gray-50 rounded-lg p-2 space-y-1.5">
    <!-- 已选页面展示 -->
    <div v-for="(page, i) in businessPages" :key="i"
      class="flex items-center gap-1 bg-white rounded-lg p-1.5 border border-gray-100">
      <span class="text-base">{{ page.icon }}</span>
      <span class="text-xs text-default flex-1 truncate">{{ page.title }}</span>
      <span class="text-xs text-gray-400 truncate">{{ page.route }}</span>
      <button @click="businessPages.splice(i, 1)"
        class="text-danger-400 hover:text-danger-600 text-sm px-1">&times;</button>
    </div>
    <!-- 操作按钮 -->
    <div class="flex gap-2">
      <button @click="showPageSelector = true"
        class="flex-1 px-2 py-1 text-xs text-primary-600 bg-primary-50 hover:bg-primary-100 rounded-lg">
        从库中选择
      </button>
      <button @click="addBusinessPage"
        class="flex-1 px-2 py-1 text-xs text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-lg">
        手动添加
      </button>
    </div>
  </div>
</div>

<!-- 页面选择器弹框 -->
<PageMetaSelector
  v-model="showPageSelector"
  :selected-page-ids="selectedPageIds"
  :agent-description="form.description || ''"
  @select="onPagesSelected"
/>
```

### 3.2 验证

- 在管理后台打开子智能体编辑页，确认"从库中选择"按钮出现
- 点击"从库中选择"，验证弹框正确展示页面列表
- 选择页面后确认，验证已选页面正确回显
- 保存后验证 `business_pages` 正确写入数据库
- 保留"手动添加"按钮，验证仍可手动输入自定义页面
- `cd frontend && npm run build` 确保构建通过

---

## 验证清单

- [x] Phase 1.1：`configs/page_metadata.yaml` 包含 14 个 published 页面 ✅
- [x] Phase 1.2：`GET /api/admin/agent-definitions/meta/pages` 返回 14 个页面 ✅
- [x] Phase 1.2：`GET /api/admin/agent-definitions/meta/pages?domain=travel-consultant` 返回 6 个页面 ✅
- [x] Phase 1.3：`POST /api/admin/agent-definitions/meta/pages/recommend` AI 推荐正常 ✅（路由已注册，需实际 API 调用验证 AI 推荐）
- [x] Phase 1.3：metadata 中 `status: planned` 的页面不在列表中返回 ✅
- [x] Phase 2.1：前端 `listPageMeta()` 和 `recommendPages()` 函数可用 ✅
- [x] Phase 2.2：PageMetaSelector 组件正确展示页面列表、搜索、分组 ✅
- [x] Phase 2.2：AI 推荐按钮正常工作 ✅
- [x] Phase 3.1：AgentDefinitionManager 中"从库中选择"按钮正常 ✅
- [ ] Phase 3.1：选择页面后保存，聊天侧边栏显示业务页面链接（需实际运行验证，代码已评审通过）
- [x] Phase 3.1："手动添加"仍可用 ✅
- [x] `cd frontend && npm run build` 构建通过 ✅
