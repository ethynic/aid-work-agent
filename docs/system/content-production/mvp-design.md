# AI 视频生成工具 MVP - 技术设计文档

> 本文档为 MVP 的**零歧义技术方案**，供 AI 开发直接执行，无需猜测或回头询问。所有接口签名、字段、文件路径、外部 API 规格、对接点均钉死。
>
> 关联：[PRD v0.4](ai-video-production-prd.md)（第 0 章为产品权威定义）/ [深度调研](../../research/ai-video-production-research.md)

---

## 1. MVP 范围与技术决策（已锁定，不再讨论）

| 决策项 | 锁定值 | 理由 |
|--------|--------|------|
| 产品形态 | 抽卡式工具：选场景→上传1张产品图→填文案→生成2-4条→留用/重新生成→下载 | PRD §0.1（spike后修正：单图非2图） |
| 防变形 | **reference_image 模式**（r2v 模型，1张产品图作 reference_image+first_frame） | spike 实测验证，产品不变形 |
| 图传方式 | **base64 编码直传**（`data:image/jpeg;base64,...`） | spike 发现万相支持base64，无需公网URL/图床/OSS |
| 素材预处理 | **不做程序侧预处理**，用户自行上传正确比例素材 | 由用户保证素材比例（如竖屏 9:16），程序不做裁剪/抠图；长图鬼影属素材问题 |
| 图生成 provider | **通义万相 wan2.7-r2v-2026-06-12**（云 API，spike 已验证） | reference_image 防变形、720P、5s |
| 场景预设 | **硬编码配置常量**（2个场景：产品展示/氛围），不建表 | MVP 最轻 |
| 计费 | **MVP 不计费** | 验证阶段 |
| 数据库 | PostgreSQL（仓库无 sqlite 分支），JSONB 直接用 | 现状 |
| 后台任务 | background_runner 进程跑轮询 job，HTTP worker 不跑 | 现状 |
| **模块归属** | **独立 `src/video_gen/` 模块 + 对接社媒**（不合并进 social_media） | 生成逻辑独立演进，社媒零侵入 |
| **前端入口** | **社媒工作台内加 Tab**（视频生成放"内容创作"Tab） | 合并到社媒智能体，独立页面不进chat |
| **成片去向** | **MVP 先独立下载**（暂不接社媒审核发布链） | 审核发布链目前是stub，先各管各 |

### 1.1 图片传输：base64 直传（spike 发现，无需公网URL/OSS）

> **Spike 重大发现（2026-07-30）**：万相 `media[].url` **支持 base64 编码图片**（`data:image/jpeg;base64,{xxx}`），实测通过（假睫毛+模特图均成功生成）。这意味着**不需要公网URL、不需要OSS、不依赖系统部署在公网**——本地图片直接 base64 传给万相。

**完整链路（钉死）**：
```
运营向导上传产品图 → POST /api/upload → file_id（落本地storage）
→ 生成时：读本地原图 → base64编码 → data:image/jpeg;base64,{b64}
→ 传给 wanx.submit（media.reference_image.url = base64串, first_frame.url = 同）
```
> 注：程序不做任何素材预处理（不裁剪/不抠图）。用户须自行上传正确比例素材（建议竖屏 9:16）；长图/全身图直接生成可能出现鬼影，属素材问题，由用户重传。

**优势**：✅ 零外部依赖（不需 PUBLIC_BASE_URL 公网可达、不需 OSS、不需图床） ✅ 更安全（图片不暴露公网） ✅ 本地开发也能跑

> 之前设计的 `PUBLIC_BASE_URL + file_id 公网地址` 方案**作废**，改用 base64。`MediaRegistry.build_public_url` 不再需要，改为 `read_as_base64(file_id)`。

### 1.2 素材预处理：不做程序侧预处理（由用户保证素材质量）

> **决策（2026-07-31 修正）**：MVP **不做任何程序侧素材预处理**（不裁剪、不抠图、不做人脸检测）。理由：①让用户自己上传正确比例的素材，比程序兜底更简单，避免引入 OpenCV 等重型依赖；②素材质量是运营职责。

**Spike 的相关发现（仅作素材选型参考，程序不处理）**：
- spike 曾实测 352×2048 模特全身长图直接生成出现**鬼影/重复画面**；裁剪到正常比例（如 352×410）后鬼影消失。
- **结论转化为对用户的素材要求**（而非程序逻辑）：用户须上传**主体清晰、比例正常**的素材（建议竖屏 9:16 或接近），避免长图/全身图。若上传不当比例导致鬼影，由用户重传，程序不兜底。

**MVP 无预处理流程**：
```
上传图（用户自行保证比例） → POST /api/upload → file_id
→ 生成时：读本地原图 → base64 → 传给万相（reference_image + first_frame 同图）
```

- 不新增 `preprocess.py`，不引入 `opencv-python` / MediaPipe 等依赖。
- 前端上传处可加一行**轻量提示**（建议竖屏 9:16，避免长图），但不阻断上传、不做校验。

### 1.3 与社媒模块的合并架构（v0.5，入口与模块归属）

> **架构决策（2026-07-30）**：视频生成**合并到社媒营销智能体**，作为其"内容管理"模块的一部分；但后端保持**独立模块**，前端入口在**社媒工作台内加 Tab**。

**三句话定位**：
- 视频生成是社媒"内容创作"的一种（未来还有图文/海报）。
- 它在社媒工作台里有个 Tab 入口，**不进 chat**（工作台本就是独立页面非chat）。
- 后端代码独立（`src/video_gen/`），生成完的成片可登记到 `social_media_assets`，但 **MVP 先独立下载**，暂不接审核发布链。

**前端入口（钉死）**：
- 改造 `SocialMediaWorkbench.vue`（`frontend/src/components/social-media/`），加 Tab 切换（如：「账号与发布」「内容创作」「运营数据」）。
- 视频生成放"内容创作"Tab，作为子组件 `VideoGeneration.vue`。
- 路由不变（仍是 `/social-media` 和 `/t/:tenant_id/social-media`），不新增菜单项、不新增 business_pages。
- 视频生成的独立前端组件和 API 客户端仍单独建文件（`VideoGeneration.vue` + `videoGen.ts`），只是被工作台 Tab 引用。

**后端模块归属（钉死）**：
- 视频生成后端**独立**：`src/video_gen/`（service/provider/media/db/scenes）、`src/api/video_gen.py`、独立路由 `/api/video-gen/*`。
- **不合并进 `src/social_media/`**（避免生成逻辑污染社媒的纯CRUD服务）。
- 社媒模块零侵入：不改 social_media 任何代码。

**成片对接社媒（预留，MVP 不实现）**：
- 生成完成后，未来可调 `SocialMediaService.create_asset(asset_type='video', storage_file_id=...)` 登记成片到 `social_media_assets`，再通过 `social_content_asset_links` 关联母版进入审核发布链。
- MVP 阶段成片只落 video_gen 自己的 `gen_cards.output_fid`，运营直接下载。
- 社媒数据模型已为对接预留：`social_content_variants.content_type` 支持 `'video'`、`prompt_version` 字段、`social_media_assets.asset_type` 可为 `'video'`。

> **注意**：社媒智能体现在是 `.disabled`（`subagents/social-media-operations/SUBAGENT.md.disabled`）。工作台入口当前实际不出现。做这个功能时需确认工作台路由是否可独立访问（即使 subagent disabled，路由 `/social-media` 仍注册在 agentRoutes.ts，应可直接访问）。

---

## 2. 系统架构

### 2.1 模块分层

```
┌─────────────────────────────────────────────────────────┐
│  前端  VideoGenWorkbench.vue（单页面：向导+抽卡+留用+下载）│
│           api/videoGen.ts                                 │
└──────────────────────────┬──────────────────────────────┘
                           │ REST /api/video-gen/*
┌──────────────────────────┴──────────────────────────────┐
│  API 层  src/api/video_gen.py                             │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────────┐
│  Service 层  src/video_gen/service.py (VideoGenService)    │
│   创建会话/抽卡/查状态/留用/重新生成/下载                   │
│   场景预设常量 src/video_gen/scenes.py                     │
└──────┬───────────────────────┬───────────────────────────┘
       │                       │
       │ 通义万相(提交)          │ 文件资产
       ↓                       ↓
┌──────────────┐   ┌──────────────────────────┐
│ 万相 provider │   │ MediaRegistry(新建)       │
│ src/video_gen/│   │ src/video_gen/media.py    │
│ wanx_provider │   │ (注册视频/图片file_id,    │
│ .py           │   │  OSS上传)                 │
└──────────────┘   └──────────────────────────┘
       │
       │ task_id 异步
       ↓
┌──────────────────────────────────────────────┐
│  轮询 job（background_runner 进程）            │
│  src/scheduler/manager.py 每30s poll          │
│  → 成功下载成片 → 注册file_id → 更新card状态   │
└──────────────────────────────────────────────┘
```

### 2.2 新增文件清单

| 文件 | 职责 |
|------|------|
| `src/video_gen/__init__.py` | 包标识 |
| `src/video_gen/scenes.py` | **场景预设硬编码常量**（2个场景） |
| `src/video_gen/db.py` | 建表 `init_video_gen_tables(conn)` |
| `src/video_gen/media.py` | MediaRegistry：视频/图片注册 file_id + **读图为base64** |
| `src/video_gen/wanx_provider.py` | 通义万相 r2v 调用（提交任务+查状态） |
| `src/video_gen/service.py` | VideoGenService 业务逻辑 |
| `src/api/video_gen.py` | API 路由（`/api/video-gen/*`，独立） |
| `frontend/src/api/videoGen.ts` | 前端 API 客户端 |
| `frontend/src/components/social-media/VideoGeneration.vue` | **视频生成子组件**（嵌入社媒工作台Tab） |

### 2.3 修改文件清单（最小侵入）

| 文件 | 修改点 | 参考行 |
|------|--------|--------|
| `src/main.py` | import + include_router | L1679 后 |
| `src/db/database.py` | 调用 init_video_gen_tables | L1306 后 |
| `src/scheduler/manager.py` | 注册 30s 轮询 job | _register_system_jobs 末尾 |
| `src/config/settings.py` | 新增 wanx 配置项 | LLMConfig |
| `frontend/src/components/social-media/SocialMediaWorkbench.vue` | **加 Tab 容器，"内容创作"Tab 引入 VideoGeneration** | L42-161 主体section |

> v0.5：**不新增路由**（复用 /social-media），**不改 social_media 后端**（零侵入），**不改 agentRoutes.ts**。

---

## 3. 数据模型（PostgreSQL，2 张表）

> 命名约定：`snake_case`；主键 TEXT + 业务前缀；强制 `tenant_id`；`created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`；JSON 用 JSONB。建表函数 `init_video_gen_tables(conn)` 全部 `CREATE TABLE IF NOT EXISTS`（幂等）。

### 3.1 `gen_sessions`（一次抽卡会话）

```sql
CREATE TABLE IF NOT EXISTS gen_sessions (
    session_id     TEXT PRIMARY KEY,          -- sess_<12hex>
    tenant_id      TEXT,                       -- 租户（可空，demo模式）
    user_id        TEXT,                       -- 发起用户
    scene_id       TEXT NOT NULL,             -- 场景预设id（硬编码，如 "product_showcase"）
    product_image_fid TEXT NOT NULL,          -- 产品图 file_id（用户上传的原图，r2v 作 reference_image+first_frame，base64 直传）
    copywriting    TEXT NOT NULL,             -- 运营填写的文案
    expanded_prompt TEXT,                      -- 提示词引擎扩展后的完整prompt（可微调，见§6）
    card_count     INT NOT NULL DEFAULT 3,    -- 本次抽卡条数（2-4）
    status         TEXT NOT NULL DEFAULT 'generating',  -- generating/done/failed
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3.2 `gen_cards`（抽出来的视频卡片）

```sql
CREATE TABLE IF NOT EXISTS gen_cards (
    card_id        TEXT PRIMARY KEY,          -- card_<12hex>
    tenant_id      TEXT,
    session_id     TEXT NOT NULL,             -- 逻辑外键 → gen_sessions
    variant_idx    INT NOT NULL,              -- 第几条（0-based）
    seed           BIGINT,                    -- 万相随机种子（差异化来源）
    variant_prompt TEXT,                      -- 本条差异化后的prompt
    provider_task_id TEXT,                    -- 万相返回的 task_id
    provider_status TEXT,                     -- PENDING/RUNNING/SUCCEEDED/FAILED/CANCELED/UNKNOWN
    output_fid     TEXT,                      -- 成片 file_id（成功后回填）
    output_url     TEXT,                      -- 万相返回的临时video_url（下载后可清）
    output_duration INT,                      -- 成片时长(秒)
    kept           BOOLEAN NOT NULL DEFAULT FALSE,  -- 是否被运营留用
    parent_card_id TEXT,                      -- 精修溯源（从哪张card重新生成）
    error_msg      TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3.3 建表触发

在 `src/db/database.py` 的 `_init_postgresql()` 内，L1306 社媒建表块之后，照抄一个 try/except 块：

```python
try:
    from src.video_gen.db import init_video_gen_tables
    init_video_gen_tables(conn)
except Exception as e:
    logger.warning(f"Failed to initialize video_gen tables: {e}")
    try:
        conn.rollback()
    except Exception as r:
        logger.warning(f"rollback failed: {r}")
```

---

## 4. 配置项（settings.py）

### 4.1 万相配置

在 `src/config/settings.py` 的 `LLMConfig`（L81-87）新增：

```python
class LLMConfig(BaseSettings):
    # ... 现有字段 ...
    wanx: "WanxConfig" = Field(default_factory=lambda: WanxConfig())
```

新增 `WanxConfig`（放在 LLMConfig 定义之前）：

```python
class WanxConfig(BaseSettings):
    api_key: str = ""                          # 复用阿里云 DashScope key（默认回退 settings.llm.qwen.api_keys[0]）
    model: str = "wan2.7-r2v-2026-06-12"
    poll_interval_seconds: int = 30
    task_max_age_hours: int = 24               # task_id 查询有效期
```

> **api_key 来源（v0.4 简化）**：万相与 Qwen 同属阿里云百炼，**同一个 API key 通用**。优先读 `WANX_API_KEY` 环境变量；若空，回退 `settings.llm.qwen.api_keys[0]`。**无需 workspace_id**（MVP 用旧域名 `dashscope.aliyuncs.com`，见 §5.1）。在 settings.py 的 env 加载块（L429 区域）加：
> ```python
> if os.getenv("WANX_API_KEY"):
>     llm_cfg.wanx.api_key = os.getenv("WANX_API_KEY")
> ```
> **多数情况下无需新增任何环境变量**——直接复用 `.env` 里已有的 `QWEN_API_KEYS`。

### 4.2 图片传输配置（无需）

> v0.5：图片用 **base64 直传**万相（§1.1），**不需要 PUBLIC_BASE_URL、不需要 OSS、不需要任何图片传输配置**。`read_as_base64(file_id)` 直接读本地文件转 base64。零新增配置。

> **无需 OSS 配置**。图片公网 URL 通过 `{public_base_url}/api/files/{file_id}/download` 拼接（见 §1.1）。

---

## 5. 通义万相 2.7 Provider（src/video_gen/wanx_provider.py）

### 5.1 API 规格（钉死，照抄）

> **Spike 验证结论（2026-07-30，已实测通过）**：
> - **模型名 `wan2.7-r2v-2026-06-12`**（r2v = reference-to-video，不是文档写的 i2v）。账号开通的是 r2v 版。
> - **media 格式**：用 `type: reference_image`（锁定产品外观，防变形），**不是** `first_frame`/`last_frame`（r2v 不支持后者）。
> - **防变形方案（已验证成功）**：`reference_image`（锁定产品）+ `first_frame`（控制起始画面）组合，生成成功且产品稳定。
> - **必须用 Python httpx**，不能用 curl（curl 的 JSON 编码会触发 `Required body invalid`）。
> - 复用 `.env` 的 `QWEN_API_KEYS`（同百炼账号，万相通用，key 已验证有效）。
> - 生成耗时约 3 分钟，输出 5 秒 720P mp4（~6MB）。

**提交任务**：
- Method: `POST`
- URL: `https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis`
- Headers:
  - `Content-Type: application/json`
  - `Authorization: Bearer {api_key}`（复用 `settings.llm.qwen.api_keys[0]`）
  - `X-DashScope-Async: enable`（**缺少必报错**）
- Body:
```json
{
    "model": "wan2.7-r2v-2026-06-12",
    "input": {
        "prompt": "{expanded_prompt}",
        "media": [
            {"type": "reference_image", "url": "{product_image_url}"},
            {"type": "first_frame", "url": "{product_image_url}"}
        ]
    },
    "parameters": {
        "resolution": "720P",
        "duration": 5,
        "negative_prompt": "{negative_prompt}",
        "prompt_extend": false,
        "watermark": false,
        "seed": {seed}
    }
}
```
> **media 说明**：`reference_image` 是防变形核心（锁定产品外观），`first_frame` 控制起始画面。MVP 用同一张产品图（`reference_image` 和 `first_frame` 的 url 都传同一张图）。`seed` 是差异化来源（同一次抽卡的 2-4 条用不同 seed）。`negative_prompt` 在 **parameters 下**（非 input，spike 实测确认），值取自场景预设 `scene.negative_prompt`。
- 返回：
```json
{"output": {"task_status": "PENDING", "task_id": "ce89fd84-..."}, "request_id": "..."}
```
- `task_id` 在 `output.task_id`。

**查询任务状态**：
- Method: `GET`
- URL: `https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}`
- Headers: `Authorization: Bearer {api_key}`
- 返回（成功）：
```json
{
    "output": {
        "task_id": "...",
        "task_status": "SUCCEEDED",
        "video_url": "https://dashscope-result-sh.oss-cn-shanghai.aliyuncs.com/xxx.mp4?Expires=..."
    },
    "usage": {"duration": 5, "output_video_duration": 5, "video_count": 1, "SR": 1080}
}
```
- `task_status` 枚举：`PENDING` / `RUNNING` / `SUCCEEDED` / `FAILED` / `CANCELED` / `UNKNOWN`
- 成片 URL 在 `output.video_url`（**24h 有效，必须及时下载**）。

### 5.2 Provider 接口签名

```python
# src/video_gen/wanx_provider.py
from dataclasses import dataclass

@dataclass
class WanxSubmitResult:
    task_id: str
    task_status: str          # 通常 "PENDING"

@dataclass
class WanxPollResult:
    task_status: str          # SUCCEEDED/RUNNING/PENDING/FAILED/CANCELED/UNKNOWN
    video_url: str | None     # 仅 SUCCEEDED 有值
    duration: int | None      # 来自 usage.output_video_duration
    error: str | None

class WanxProvider:
    def __init__(self, api_key: str, model: str = "wan2.7-r2v-2026-06-12"):
        self._api_key = api_key
        self._model = model
        self._submit_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
        self._poll_base = "https://dashscope.aliyuncs.com/api/v1/tasks/"

    async def submit(
        self,
        prompt: str,
        product_image_url: str,    # 产品图公网URL（同时作reference_image和first_frame）
        seed: int,
        negative_prompt: str = "", # 取自场景预设，放到 parameters.negative_prompt
        duration: int = 5,
        resolution: str = "720P",
    ) -> WanxSubmitResult:
        """提交参考图生视频任务（r2v），返回 task_id。
        media 固定格式：reference_image（防变形锁定）+ first_frame（控制起始），
        两者 url 都传同一张产品图。negative_prompt 放 parameters 下。"""
        # body 见 §5.1，用 httpx.AsyncClient POST

    async def poll(self, task_id: str) -> WanxPollResult:
        """查询任务状态"""
```

**实现要点**：
- 用 `httpx.AsyncClient(timeout=60.0)`（提交）/ `timeout=30.0`（查询）。**必须用 httpx，不能用 curl**（curl JSON 编码会触发 Required body invalid）。
- 提交 endpoint（旧域名，无需 workspace_id）：`https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis`。
- 查询 URL：`https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}`。
- api_key 复用 `settings.llm.qwen.api_keys[0]`（万相与 Qwen 同百炼账号通用）。
- 提交的 `prompt_extend` 设 `false`（我们自己用提示词引擎扩展 prompt，不让万相再改写）。
- `watermark` 设 `false`（我们自己烧录合规 AI 标识，不用万相水印）。
- seed 是差异化来源：同一次抽卡的 2-4 条用不同 seed。
- 错误处理：httpx 非 2xx 抛异常并带 response.text，由 service 层捕获写 error_msg。

---

## 6. 场景预设硬编码常量（src/video_gen/scenes.py）

2 个场景，纯 Python 常量，不建表。

```python
# src/video_gen/scenes.py

@dataclass
class ScenePreset:
    scene_id: str
    name: str                   # 展示名
    description: str
    prompt_template: str        # 含 {copywriting} 变量槽
    negative_prompt: str        # 负面词
    default_duration: int       # 默认时长（秒）

SCENES: dict[str, ScenePreset] = {
    "product_showcase": ScenePreset(
        scene_id="product_showcase",
        name="产品展示",
        description="产品主体居中，缓慢运镜展示全貌与细节，纯净背景突出产品",
        prompt_template=(
            "写实产品广告风格，{copywriting}。"
            "产品主体居中稳定展示，纯净柔和的摄影棚背景，"
            "缓慢的推近镜头（slow dolly in）突出产品细节与质感，"
            "专业打光，高细节，8k画质，商业广告质感。"
        ),
        negative_prompt="变形，扭曲，模糊，morphing，手指畸形，低质量，水印，文字，多余物体",
        default_duration=5,
    ),
    "atmosphere": ScenePreset(
        scene_id="atmosphere",
        name="场景氛围",
        description="产品置于使用场景中，氛围光感呈现，营造使用联想",
        prompt_template=(
            "写实氛围场景风格，{copywriting}。"
            "产品自然置于使用场景中，温暖自然的光线，"
            "缓慢的环绕镜头（slow orbit）展示产品与场景的融合，"
            "浅景深，电影感氛围，高细节，8k画质。"
        ),
        negative_prompt="变形，扭曲，模糊，morphing，手指畸形，低质量，水印，文字，杂乱背景",
        default_duration=5,
    ),
}

def get_scene(scene_id: str) -> ScenePreset | None:
    return SCENES.get(scene_id)

def list_scenes() -> list[ScenePreset]:
    return list(SCENES.values())
```

**提示词引擎（极简版，MVP）**：`expanded_prompt = scene.prompt_template.format(copywriting=copywriting)`。即把运营填的文案填入模板。MVP 不做可灵8层那种复杂结构化扩展（那是 Phase 1），就是模板填空 + 场景预设自带的运镜/光影/负面词。运营可在向导里微调 expanded_prompt。

---

## 7. 文件资产对接（src/video_gen/media.py）

### 7.1 问题与方案

现有 file_id 体系（`src/core/image_asset.py` 的 ImageRegistry）**不支持视频**：
- `_guess_mime_type`（L589-603）mapping 无 .mp4，视频会被标 `image/*`。
- `_read_image_size`（L575-587）用 PIL，对视频抛异常（被吞，width/height=None）。
- scene 写死 `images/{yyyy-mm}`。
- `fetch_to_local` 有 10MB 上限，明确挡视频。

**MVP 方案（锁定）**：新建独立的 `MediaRegistry`，**不改 ImageRegistry**，直接写 Redis hash（复用 `uploaded_file:{file_id}` 命名空间 + `/api/files/{file_id}/download` 路由，无需新路由）。

### 7.2 MediaRegistry 接口签名

```python
# src/video_gen/media.py
import shutil, uuid, mimetypes
from datetime import datetime
from pathlib import Path
from src.core.storage import ensure_tenant_storage_dir, get_tenant_storage_abs_path
from src.core.redis_client import redis_client

class MediaRegistry:
    """
    注册视频/图片文件为 file_id，复用 uploaded_file:{file_id} Redis 命名空间
    和 /api/files/{file_id}/download 路由（main.py L1152）。
    与 ImageRegistry 并存，互不干扰。
    """

    async def register_local(
        self,
        source_path: str | Path,
        tenant_id: str,
        mime_type: str,                    # 显式传，如 "video/mp4" / "image/png"
        user_id: str | None = None,
        display_name: str | None = None,
        scene_subdir: str = "videos",     # "videos" 或 "images" 或 "videos/2026-07"
    ) -> str:
        """
        将本地文件复制到租户storage并注册file_id。
        返回 file_id（格式 file_<12hex>）。
        下载自动可用：GET /api/files/{file_id}/download
        """
        # 1. 生成 file_id
        file_id = f"file_{uuid.uuid4().hex[:12]}"
        # 2. 落盘到 storage/tenants/{tenant_id}/{scene_subdir}/
        ext = Path(source_path).suffix
        ensure_tenant_storage_dir(tenant_id, scene_subdir)
        dest = get_tenant_storage_abs_path(tenant_id, scene_subdir, f"{file_id}{ext}")
        shutil.copy2(source_path, dest)
        # 3. 写 Redis hash（字段集与 main.py _get_file_info 读取端兼容）
        size = dest.stat().st_size
        redis_client.hset(
            redis_client.make_key("uploaded_file", file_id),
            mapping={
                "file_id": file_id,
                "name": display_name or Path(source_path).name,
                "path": str(dest),
                "size": str(size),
                "mime_type": mime_type,
                "type": "video" if mime_type.startswith("video/") else "image",
                "registered_at": datetime.now().isoformat(),
            },
        )
        # 4. TTL 24h（与现有一致）；成片如需长期保留，调用方传 ttl_seconds=None 跳过
        redis_client.expire(redis_client.make_key("uploaded_file", file_id), 86400)
        return file_id

    async def download_and_register(
        self,
        url: str,
        tenant_id: str,
        display_name: str | None = None,
    ) -> str:
        """
        下载万相返回的临时 video_url（24h有效）到本地并注册。
        自动识别 mime（按扩展名/.mp4→video/mp4）。
        """
        # httpx 下载到临时文件 → register_local(..., mime_type="video/mp4", scene_subdir="videos")
        ...

    @staticmethod
    def read_as_base64(file_id: str) -> str:
        """
        读本地图片文件转base64 data URL（spike验证万相支持base64，无需公网URL）。
        1. 从 Redis hget uploaded_file:{file_id} 取 path
        2. 读文件 → base64编码
        返回 data:image/jpeg;base64,{b64}
        """
        from src.core.redis_client import redis_client
        import base64
        path = redis_client.hget(redis_client.make_key("uploaded_file", file_id), "path")
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return f"data:image/jpeg;base64,{b64}"
```

> v0.5：`build_public_url` 已删除（改用 base64）。`read_as_base64` 是图传万相的唯一方式，零外部依赖。

### 7.3 关键对接点（钉死）

- **Redis key**：`redis_client.make_key("uploaded_file", file_id)`（与 ImageRegistry、main.py 同命名空间）。
- **Redis 字段**：必须含 `file_id/name/path/size/mime_type/type`（main.py `_get_file_info` L1060 读取端要求）。
- **下载路由**：复用 `GET /api/files/{file_id}/download`（main.py L1152），**无需新增路由**。
- **预览路由**：复用 `GET /api/files/{file_id}`（main.py L1131，inline，视频可在浏览器播放）。
- **存储路径**：`storage/tenants/{tenant_id}/videos/{file_id}.mp4`（storage.py scene 机制支持，零改动）。
- **mime_type 必须显式传**（不依赖扩展名猜测），视频统一 `video/mp4`，图片按实际（image/png 等）。

---

## 8. Service 层（src/video_gen/service.py）

### 8.1 VideoGenService 接口

```python
# src/video_gen/service.py
class VideoGenService:
    def __init__(self):
        self.wanx = WanxProvider(
            api_key=settings.llm.wanx.api_key or settings.llm.qwen.api_keys[0],
            model=settings.llm.wanx.model,
        )
        self.media = MediaRegistry()

    async def list_scenes(self) -> list[dict]:
        """返回可用场景列表（给前端下拉）"""

    async def create_session(
        self,
        tenant_id: str | None,
        user_id: str | None,
        scene_id: str,
        first_frame_fid: str,
        last_frame_fid: str,
        copywriting: str,
        card_count: int = 3,
        expanded_prompt: str | None = None,
    ) -> dict:
        """
        创建抽卡会话 + 立即向万相提交 card_count 条任务。
        - 校验 scene_id 有效
        - expanded_prompt 为空时用 scene.prompt_template.format(copywriting=copywriting)
        - 解析 first/last_frame_fid 的本地路径，上传OSS拿公网URL
        - 为每条card生成不同seed（差异化），调 wanx.submit()
        - 写 gen_sessions + gen_cards（status=generating, provider_status=PENDING）
        - 返回 session 详情（含cards）
        """

    async def get_session(self, tenant_id: str | None, session_id: str) -> dict | None:
        """查会话+其下所有cards状态"""

    async def list_sessions(self, tenant_id: str | None, limit: int = 20) -> list[dict]:
        """会话历史列表"""

    async def set_card_kept(self, tenant_id: str | None, card_id: str, kept: bool) -> dict:
        """标记card留用/取消留用"""

    async def regenerate_card(
        self,
        tenant_id: str | None,
        card_id: str,
        prompt_override: str | None = None,
        seed_override: int | None = None,
    ) -> dict:
        """
        重新生成式编辑：基于某张card的session，用新prompt/seed重新提交一条任务。
        - 新card的 parent_card_id = 原 card_id
        - 复用原session的首尾帧
        - 返回新card
        """

    async def poll_pending_cards(self) -> int:
        """
        【轮询job调用】扫描所有 provider_status in (PENDING,RUNNING) 的cards，
        调 wanx.poll()，更新状态。
        - SUCCEEDED：下载video_url → register_local → 更新 output_fid
        - FAILED/CANCELED：写 error_msg
        返回处理的card数。
        """
```

### 8.2 id 生成

复用 social_media 的 `new_id` 模式（`src/social_media/services.py:53`）：`f"sess_{uuid.uuid4().hex[:12]}"` / `f"card_{uuid.uuid4().hex[:12]}"`。

### 8.3 租户过滤

所有查询用 `tenant_id IS NOT DISTINCT FROM %s`（兼容 NULL，参考 `src/social_media/services.py:47`）。

### 8.4 create_session 完整流程（钉死）

```
1. 校验 get_scene(scene_id) 非空，否则 raise ValueError("未知场景")
2. expanded_prompt = expanded_prompt or scene.prompt_template.format(copywriting=copywriting)
3. 读用户上传的产品图 base64（spike 验证万相支持 base64 直传，§1.1）：
   - image_data_url = MediaRegistry.read_as_base64(product_image_fid)
   （不做任何素材预处理，直接用用户上传的原图）
4. 生成 card_count 个不同 seed（如 base_seed + i，base_seed=random.randint(0,2147483647)）
5. 对每个 seed 调 wanx.submit(prompt=expanded_prompt, image_data_url, seed,
      negative_prompt=scene.negative_prompt, duration=scene.default_duration)
   → 拿 task_id（media 内部 reference_image + first_frame 都用 image_data_url）
6. 写 gen_sessions（status=generating, product_image_fid=用户原图file_id）
7. 写 gen_cards（每条 provider_task_id, provider_status=PENDING, seed, variant_prompt=expanded_prompt）
8. 返回 session + cards
```

---

## 9. API 层（src/api/video_gen.py）

### 9.1 路由声明（照抄 social_media 模式）

```python
# src/api/video_gen.py
from fastapi import APIRouter, Request
from src.api.social_media import JsonResponse, _ok, _fail, _tenant_id, _user_id
# ↑ JsonResponse/_ok/_fail/_tenant_id/_user_id 复用 social_media.py 的（或复制一份）

router = APIRouter(prefix="/api/video-gen", tags=["视频生成"])
_service = VideoGenService()
```

> 若不想跨模块 import 私有函数，则在 video_gen.py 内复制 `_ok/_fail/_tenant_id/_user_id`（约 20 行，参考 social_media.py L97-112）。

### 9.2 端点清单

| Method | Path | 功能 | 请求体 | 返回 data |
|--------|------|------|--------|-----------|
| GET | `/scenes` | 场景列表 | - | `[{scene_id,name,description}]` |
| POST | `/sessions` | 创建抽卡会话 | `{scene_id, product_image_fid, copywriting, card_count?, expanded_prompt?}` | session详情(含cards) |
| GET | `/sessions` | 会话历史 | query: `?limit=20` | `[session]` |
| GET | `/sessions/{session_id}` | 会话详情(含cards状态) | - | session详情 |
| PATCH | `/cards/{card_id}/kept` | 标记留用 | `{kept: bool}` | card |
| POST | `/cards/{card_id}/regenerate` | 重新生成 | `{prompt_override?, seed_override?}` | 新card |
| GET | `/cards/{card_id}/download-url` | 获取成片下载URL | - | `{download_url, file_id}` |

### 9.3 统一响应格式（钉死，前后端契约）

```json
{"success": true, "data": {...}, "error": null, "debug": null}
```
失败：`{"success": false, "data": null, "error": "错误描述", "debug": null}`（error 经 `sanitize_error_info` 脱敏）。

### 9.4 路由注册（src/main.py）

在 L1679 `social_media_api` include 之后加：

```python
from src.api import video_gen as video_gen_api
app.include_router(video_gen_api.router)
```

---

## 10. 轮询 Job（src/scheduler/manager.py）

在 `_register_system_jobs()` 末尾（L208 后）追加：

```python
try:
    self._scheduler.add_job(
        self._run_video_gen_poll,
        IntervalTrigger(seconds=settings.llm.wanx.poll_interval_seconds),  # 30
        id="job_system_video_gen_poll",
        name="Video Generation Status Poller",
        max_instances=1,
        coalesce=True,
    )
    logger.info("已注册视频生成状态轮询任务 (interval=%ss)", settings.llm.wanx.poll_interval_seconds)
except Exception as e:
    logger.error(f"注册视频生成轮询任务失败: {e}")
```

异步回调方法（照抄 manager.py L359-376 的 event loop 模式）：

```python
def _run_video_gen_poll(self):
    """视频生成状态轮询（async 回调，新建 event loop）"""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(self._video_gen_poll_tick())
    except Exception as e:
        logger.error(f"视频生成轮询异常: {e}")
    finally:
        loop.close()

async def _video_gen_poll_tick(self):
    from src.video_gen.service import VideoGenService
    try:
        svc = VideoGenService()
        n = await svc.poll_pending_cards()
        if n > 0:
            logger.info(f"视频生成轮询: 处理 {n} 条card")
    except Exception as e:
        logger.error(f"视频生成轮询tick异常: {e}")
```

**关键约束**：
- 此 job 只在 background_runner 进程跑（调度器有 manager 级 Redis 锁，manager.py L48，多 worker 只一个生效）。
- HTTP worker 不跑此 job（main.py L463 注释明确 HTTP worker 不承载后台任务）。
- `poll_pending_cards` 内部对每批 pending cards 可选加细粒度 Redis 锁（防 job 重叠），但 max_instances=1 + coalesce=True 已基本够。

---

## 11. 合规标识烧录（AI 内容标识，2025.9.1 法规）

### 11.1 方案

成片下载落本地后、注册 file_id 前，用 **FFmpeg 烧录醒目 AI 标识**。

- 标识内容：画面右下角常驻文字"AI 生成内容"（白色半透明背景，长期可见）。
- 实现：FFmpeg `drawtext` 滤镜。
- FFmpeg 已在 Docker 环境（PDF 工具用了 LibreOffice，FFmpeg 通常预装；若无需 Dockerfile 加装）。

### 11.2 实现位置

在 `MediaRegistry.download_and_register()` 内部，下载 mp4 后、register_local 前插入烧录步骤：

```python
# 伪代码
raw_path = download(video_url)                           # 原始mp4
labeled_path = burn_ai_label(raw_path)                   # 烧录标识
file_id = register_local(labeled_path, mime_type="video/mp4")  # 注册
```

`burn_ai_label` 用 FFmpeg subprocess：`ffmpeg -i input -vf "drawtext=text='AI 生成内容':x=w-tw-20:y=h-th-20:fontcolor=white:fontsize=h/20:box=1:boxcolor=black@0.5:boxborderw=10"`。

> **降级**：若 FFmpeg 不可用，MVP 可先用万相自带 watermark=true（但位置/样式不可控，法规要求"醒目长期可见"，万相水印是否合规需确认）。**优先实现 FFmpeg 方案**。

---

## 12. 前端（frontend/src/）

### 12.1 API 客户端（frontend/src/api/videoGen.ts）

照抄 `socialMedia.ts` 结构，prefix 改 `/video-gen`：

```typescript
import { getAuthHeader } from './auth'
const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/video-gen`
export interface ApiResponse<T> { success: boolean; data: T; error: string | null; debug: any }

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: { 'Content-Type': 'application/json', ...getAuthHeader(), ...options.headers },
    })
    const json: ApiResponse<T> = await res.json()
    if (!json.success) throw new Error(json.error || '请求失败')
    return json.data
}

export const videoGenAPI = {
    listScenes: () => request<any[]>('/scenes'),
    createSession: (body: any) => request<any>('/sessions', { method: 'POST', body: JSON.stringify(body) }),
    listSessions: (limit = 20) => request<any[]>(`/sessions?limit=${limit}`),
    getSession: (id: string) => request<any>(`/sessions/${id}`),
    setKept: (cardId: string, kept: boolean) => request<any>(`/cards/${cardId}/kept`, { method: 'PATCH', body: JSON.stringify({ kept }) }),
    regenerate: (cardId: string, body: any) => request<any>(`/cards/${cardId}/regenerate`, { method: 'POST', body: JSON.stringify(body) }),
    getDownloadUrl: (cardId: string) => request<any>(`/cards/${cardId}/download-url`),
}
```

### 12.2 视频生成子组件（frontend/src/components/social-media/VideoGeneration.vue）

> v0.5：视频生成**不是独立页面**，而是 `SocialMediaWorkbench.vue` 的"内容创作"Tab 内的子组件。不新增路由。

**子组件分三区**：

**① 向导区（创建会话）**
- 场景下拉（listScenes）
- 产品图上传（POST /api/upload 拿 file_id，**只需1张**）
- 文案文本框
- 抽卡条数选择（2/3/4）
- prompt 预览框（可微调 expanded_prompt，留空则用场景默认）
- 「开始抽卡」按钮 → createSession

> 注：不做程序侧素材预处理（不裁剪/不抠图）。上传处可加一行轻量提示「建议竖屏 9:16，避免长图」，但不阻断上传、不做校验。

**② 抽卡结果区（cards 网格）**
- v-for 渲染 cards，每个卡片：视频预览（`/api/files/{output_fid}` inline）/ 生成中占位 / 失败提示
- 每张卡：「留用」开关（setKept）、「重新生成」按钮（regenerate，可改prompt）、「下载」按钮（getDownloadUrl）
- 轮询刷新：onMounted 后对 generating 的 session 每 5s 调 getSession 刷新状态（前端轮询，非后端推）

**③ 历史会话区**
- listSessions 渲染历史，点击切换到该会话的抽卡结果区

### 12.3 工作台 Tab 改造（SocialMediaWorkbench.vue）

改造现有 `SocialMediaWorkbench.vue`（`frontend/src/components/social-media/`），把 L42-161 的主体 `<section>` 改成 Tab 容器：

- 新增 `ref activeTab`（默认 'accounts'），Tab 项：「账号与发布」「内容创作」「运营数据」。
- "内容创作"Tab 内 `<VideoGeneration />`（import 子组件）。
- "账号与发布"Tab 放现有的账号绑定/发布队列。
- "运营数据"Tab 放现有的 overview 统计。
- Tab 切换用按钮组 + `v-if`（无需引入新 UI 库，用现有 BaseButton）。

### 12.4 文件上传对接

上传图片用现有 `POST /api/upload`（main.py L909，multipart 字段 `file`），返回 `{file_id, ...}`。前端拿到 file_id 传给 createSession。

### 12.5 路由（不新增）

> v0.5：**不新增路由**。视频生成在社媒工作台 Tab 内，复用现有 `/social-media` 和 `/t/:tenant_id/social-media` 路由（agentRoutes.ts L24/L100）。无需改 agentRoutes.ts，无需更新路由快照测试。

---

## 13. 错误处理与边界（钉死）

| 场景 | 处理 |
|------|------|
| 万相提交失败（非2xx） | card 写 error_msg，provider_status=FAILED，不重试（用户可 regenerate） |
| 万相轮询 FAILED | card 写 error_msg，provider_status=FAILED |
| 万相 video_url 过期（24h） | poll 时若下载失败，card 标 FAILED + error_msg="成片下载失败，请重新生成" |
| file_id Redis 过期 | main.py _get_file_info 有磁盘扫描恢复（L1077），但仅扫 UPLOAD_DIR 不覆盖 storage/tenants；video_gen 成片靠 Redis TTL（默认7天）维持可访问，过期需重新生成 |
| tenant_id 为空（demo） | 允许，过滤用 IS NOT DISTINCT FROM NULL |
| FFmpeg 不可用 | burn_ai_label 抛异常，card 标 FAILED；记录待补装 FFmpeg |
| 用户上传非图片文件作产品图 | createSession 校验 mime_type startswith image/，否则报错 |
| 用户上传不当比例素材（长图/全身图） | 程序不预处理，直接传万相；若出鬼影属素材问题，由用户重传正确比例图 |

---

## 14. 环境变量清单（.env）

```
# 万相（多数情况无需配置，自动复用 QWEN_API_KEYS）
WANX_API_KEY=sk-xxx                    # 可空，空则自动用 QWEN_API_KEYS[0]（推荐，零配置）
# 注：MVP 用旧域名 dashscope.aliyuncs.com，无需 workspace_id

# 公网 base url（图片公网地址来源，已存在，无需新增）
PUBLIC_BASE_URL=https://agent2.aidingyi.cn   # 万相 media.url 用：{PUBLIC_BASE_URL}/api/files/{file_id}/download
```

---

## 15. 验收对照（对应 PRD §0.6）

| PRD 验收项 | 本设计实现位置 |
|-----------|--------------|
| 选场景（1-2个） | §6 SCENES 常量 + `/scenes` API |
| 上传素材 | `/api/upload`（§12.3）；**注：MVP 不做任何程序侧素材预处理**（不抠图、不裁剪、不人脸检测），直接用用户上传的原图（§1.2） |
| 填文案+prompt | createSession copywriting + expanded_prompt（§8.4） |
| 抽卡2-4条 | createSession card_count + 不同 seed（§8.4） |
| 主体一致性 | reference_image + first_frame 同图模式（§1，spike 验证防变形） |
| 合规标识 | FFmpeg burn_ai_label（§11） |
| 留用 | set_card_kept（§9.2） |
| 重新生成式编辑 | regenerate_card（§8.1） |
| 下载 | `/cards/{id}/download-url` → `/api/files/{fid}/download`（§9.2） |

> **MVP 范围澄清（素材预处理）**：PRD §0.6 提到"自动抠图"。MVP **不做任何程序侧素材预处理**（不抠图、不裁剪、不人脸检测），直接用用户上传的原图——素材质量（比例/主体清晰度）由用户保证。抠图/裁剪等预处理列 Phase 1。本设计文档以此为准。
