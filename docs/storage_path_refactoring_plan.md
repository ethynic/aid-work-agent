# 文件存储路径重构实施计划

## Context

### 问题/需求
用户希望重构项目的文件存储目录结构，有两个主要目标：
1. **统一备份**：将 `uploads`（上传文件）和未来的 `memories`（长期记忆文件）都移到 `storage` 目录下，这样备份业务数据时只需要备份 `storage` 目录即可。
2. **租户文件聚合**：将知识库文档路径从 `uploads/knowledge/{tenant_id}/{filename}` 改为 `uploads/{tenant_id}/knowledge/{filename}`，这样同一个租户的所有文件（对话上传文件 + 知识库文档）都集中在 `uploads/{tenant_id}/` 目录下，便于统计占用空间和整体迁移。

### 当前结构
```
project-root/
├── uploads/
│   ├── {tenant_id}/              # 租户对话上传文件
│   │   └── *.pdf/...
│   ├── knowledge/                # 知识库文档根目录
│   │   └── {tenant_id}/          # 租户知识库文档
│   │       └── kb_*.docx
│   └── wecom/                    # 企业微信媒体文件
├── storage/
│   ├── subagents2/               # 定制子智能体（已存在）
│   └── tenants/                  # 租户技能（已存在）
└── memories/                     # 计划中：长期记忆文件（尚未实现）
```

### 目标结构
```
project-root/
└── storage/                      # 所有业务数据集中于此，备份只需备份此目录
    ├── uploads/                  # 所有上传文件（原根级 uploads 迁移至此）
    │   ├── {tenant_id}/          # 按租户分类：同一个租户的所有文件都在这里
    │   │   ├── conversation/     # 对话上传文件（聊天界面用户上传）
    │   │   └── knowledge/        # 知识库文档（租户知识库）
    │   │       └── kb_*.docx
    │   └── wecom/                # 企业微信媒体文件（全局，保持不变）
    ├── memories/                 # 长期用户记忆文件（未来 Phase 3 使用）
    │   └── memory_{user_id}.md
    ├── subagents2/               # 定制子智能体（保持不变）
    └── tenants/                  # 租户技能（保持不变）
```

### 收益
- 备份简单：只需备份 `storage/` 目录即可包含所有业务数据
- 租户数据聚合：同一个租户的所有文件都在 `storage/uploads/{tenant_id}/` 下，便于：
  - 统计租户总存储空间占用
  - 租户数据整体迁移
  - 租户数据删除/隔离

---

## 实施任务

### 任务 1：统一配置存储根路径

**修改文件**: `src/config/settings.py`

**改动**:
- 在 `Settings` 类中新增 `StorageConfig` 配置模型
- 添加 `storage: StorageConfig = Field(default_factory=StorageConfig)`
- 配置项：
  - `base_dir: str = "storage"` — 存储根目录
  - `uploads_dir: str = "storage/uploads"` — 上传文件根目录
  - `memories_dir: str = "storage/memories"` — 长期记忆目录

**修改文件**: `configs/config.yaml`

**改动**:
- 新增 `storage` 配置节：
```yaml
storage:
  base_dir: "storage"
  uploads_dir: "storage/uploads"
  memories_dir: "storage/memories"
```

---

### 任务 2：修改对话上传文件根路径（main.py）

**修改文件**: `src/main.py` (lines 258-271)

**当前代码**:
```python
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
UPLOAD_DIR = _PROJECT_ROOT / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

def _get_tenant_upload_dir() -> Path:
    from src.saas.context import get_current_tenant_id
    tenant_id = get_current_tenant_id()
    if tenant_id:
        tenant_dir = UPLOAD_DIR / tenant_id
        tenant_dir.mkdir(parents=True, exist_ok=True)
        return tenant_dir
    return UPLOAD_DIR
```

**修改后**:
```python
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
from src.config.settings import settings
# 上传文件根目录改为 storage/uploads
UPLOAD_DIR = _PROJECT_ROOT / settings.storage.uploads_dir
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

def _get_tenant_upload_dir() -> Path:
    """获取当前租户的对话上传目录
    有租户: storage/uploads/{tenant_id}/conversation/
    无租户: storage/uploads/conversation/
    """
    from src.saas.context import get_current_tenant_id
    tenant_id = get_current_tenant_id()
    if tenant_id:
        # 租户对话上传文件放到 tenant_dir/conversation/
        tenant_dir = UPLOAD_DIR / tenant_id / "conversation"
    else:
        # 非租户模式：所有对话上传文件统一放到 conversation/
        tenant_dir = UPLOAD_DIR / "conversation"
    tenant_dir.mkdir(parents=True, exist_ok=True)
    return tenant_dir
```

**说明**:
- 对话上传文件放到 `conversation` 子目录，与 `knowledge` 区分开
- **有租户**: `storage/uploads/{tenant_id}/conversation/`
- **无租户**: `storage/uploads/conversation/`（保持对齐结构）
- 保持租户隔离逻辑不变，只是调整了层级结构

---

### 任务 3：修改知识库文档路径（KnowledgeBaseService）

**修改文件**: `src/knowledge/service.py` (lines 29-44)

**当前代码**:
```python
self.base_upload_path = Path(getattr(settings, 'knowledge_upload_path', 'uploads/knowledge'))

def _get_upload_path(self, tenant_id: Optional[str] = None) -> Path:
    if tenant_id:
        path = self.base_upload_path / tenant_id
    else:
        path = self.base_upload_path
    path.mkdir(parents=True, exist_ok=True)
    return path
```

**修改后**:
```python
# 知识库文档路径改为: storage/uploads/{tenant_id}/knowledge/
from src.config.settings import settings
base_path = Path(settings.storage.uploads_dir)
if hasattr(settings, 'knowledge_upload_path'):
    # 向后兼容：如果配置文件中定义了 knowledge_upload_path，使用配置值
    self.base_upload_path = Path(getattr(settings, 'knowledge_upload_path'))
else:
    # 默认：storage/uploads/{tenant_id}/knowledge/
    # base_path 是 storage/uploads，_get_upload_path 会拼接 tenant_id/knowledge
    self.base_upload_path = base_path

def _get_upload_path(self, tenant_id: Optional[str] = None) -> Path:
    """获取知识库文档上传路径
    有租户: storage/uploads/{tenant_id}/knowledge/
    无租户: storage/uploads/knowledge/
    """
    if tenant_id:
        path = self.base_upload_path / tenant_id / "knowledge"
    else:
        path = self.base_upload_path / "knowledge"
    path.mkdir(parents=True, exist_ok=True)
    return path
```

**关键点**:
- 默认路径从 `uploads/knowledge` → `storage/uploads`（由上层拼接 `tenant_id/knowledge`）
- 保持向后兼容：如果用户在 `config.yaml` 中自定义了 `knowledge_upload_path`，仍然尊重自定义配置

---

### 任务 4：修改企业微信媒体上传路径

**修改文件**: `src/config/settings.py` (line 61)

**当前**:
```python
upload_dir: str = "./uploads/wecom"
```

**修改后**:
```python
upload_dir: str = "./storage/uploads/wecom"
```

**说明**: 企业微信媒体文件跟随 uploads 目录迁移到 storage 下。

---

### 任务 5：修改其他代码中硬编码的 uploads 路径

| 文件 | 当前硬编码 | 修改为 |
|------|------------|--------|
| `src/core/skill_executor.py:724` | `Path.cwd() / "uploads"` | `Path(settings.storage.uploads_dir) / ...` |
| `src/tools/file/upload_to_remote.py:248` | `os.path.join('uploads', file_path)` | 需要更新查找逻辑：先在 `storage/uploads/{tenant_id}/conversation/` 找，再 fallback |
| `src/skills/word-processing/scripts/word_lib.py:63` | `DEFAULT_UPLOAD_DIR = Path("./uploads")` | `DEFAULT_UPLOAD_DIR = Path(settings.storage.uploads_dir) / get_current_tenant_id() / "conversation"` |

**`src/tools/file/upload_to_remote.py` 详细改动**:
- 当前查找顺序：`current_dir` → `test_uploads/` → `uploads/` → `test_uploads/`
- 修改为：`current_dir` → `test_uploads/` → `storage/uploads/{tenant_id}/conversation/` → `storage/uploads/` → `(legacy) uploads/`（兼容已有文件）

---

### 任务 6：添加长期记忆目录配置（预留给 Phase 3）

**修改文件**: `src/memory/long_term.py`（未来新增文件，但需要提前配置）

在 `MemoryConfig` 或 `StorageConfig` 中添加 `long_term_memories_dir`，使用 `storage/memories` 默认值。

---

### 任务 7：数据迁移（可选，为已有部署提供迁移脚本）

**新增文件**: `scripts/migrate_storage.py`

**功能**:
1. 将现有 `uploads/` 目录下的所有文件迁移到 `storage/uploads/` 对应位置：
   - `uploads/{tenant_id}/*` → `storage/uploads/{tenant_id}/conversation/*`
   - `uploads/knowledge/{tenant_id}/*` → `storage/uploads/{tenant_id}/knowledge/*`
   - `uploads/wecom/*` → `storage/uploads/wecom/*`
2. 创建 `storage/memories/` 空目录
3. 迁移完成后，原 `uploads/` 可以保留为空或重命名为 `uploads.old` 备份

**迁移逻辑**:
```python
# 映射关系
OLD_TO_NEW = [
    ("uploads/wecom", "storage/uploads/wecom"),
    ("uploads/knowledge", "storage/uploads/*/knowledge", tenant_level=True),
    ("uploads/*", "storage/uploads/*/conversation", tenant_level=True),
]
```

---

## 涉及文件汇总

### 新增文件
| 文件 | 说明 |
|------|------|
| `scripts/migrate_storage.py` | 数据迁移脚本（可选）|

### 修改文件
| 文件 | 改动说明 |
|------|----------|
| `src/config/settings.py` | 新增 StorageConfig，修改 WecomMediaConfig.upload_dir 默认值 |
| `configs/config.yaml` | 新增 storage 配置节 |
| `src/main.py` | 修改 UPLOAD_DIR 根路径和 _get_tenant_upload_dir 拼接逻辑 |
| `src/knowledge/service.py` | 修改知识库文档路径拼接逻辑 |
| `src/core/skill_executor.py` | 修改文件查找路径 |
| `src/tools/file/upload_to_remote.py` | 修改文件查找路径 |
| `src/skills/word-processing/scripts/word_lib.py` | 修改 DEFAULT_UPLOAD_DIR 默认路径 |

---

## 向后兼容策略

1. **配置兼容**：如果用户已在 `config.yaml` 中自定义 `knowledge_upload_path`，代码会继续使用自定义路径，不做强制修改。
2. **文件查找兼容**：`upload_to_remote.py` 在查找文件时，会先找新路径 `storage/uploads/`，找不到再找旧路径 `uploads/`，确保已有文件仍能被访问。
3. **增量运行**：新上传的文件直接写到新路径，不影响已存在的旧文件。

---

## 验证测试

### 手动测试清单

1. **对话上传文件**：
   - [ ] 用户在聊天界面上传文件，文件保存到 `storage/uploads/{tenant_id}/conversation/`（多租户模式）
   - [ ] Agent 能正确读取文件内容
   - [ ] 无租户模式下保存到 `storage/uploads/conversation/`

2. **知识库文档上传**：
   - [ ] 上传知识库文档保存到 `storage/uploads/{tenant_id}/knowledge/`（多租户模式）
   - [ ] 无租户模式下保存到 `storage/uploads/knowledge/`
   - [ ] 知识库搜索能正确找到文件
   - [ ] 删除文档能正确删除文件

3. **企业微信媒体上传**：
   - [ ] 企业微信接收的媒体文件保存到 `storage/uploads/wecom/`
   - [ ] 能正常上传到企业微信服务器

4. **技能输出文件**：
   - [ ] 文字处理技能输出的文件保存到正确路径
   - [ ] Excel 数据助手输出文件保存到正确路径

5. **文件下载工具**：
   - [ ] 下载工具下载的文件保存到正确路径
   - [ ] 能正确找到已上传的文件

6. **数据迁移脚本**（如果执行迁移）：
   - [ ] 原有文件正确迁移到新位置
   - [ ] 迁移后仍能正常访问原有文件

### 自动化测试

无需新增测试用例，只需确保现有测试能通过：
```bash
pytest tests/integration/test_knowledge_endpoints.py
pytest tests/unit/test_memory.py
```

---

## 回滚方案

如果需要回滚：
1. 修改 `config.yaml` 中的 `storage` 配置，将 `uploads_dir` 改回 `uploads`
2. 修改代码前会自动创建目录，不删除原有文件，所以数据不会丢失
3. 原有文件仍留在旧位置，回滚后可继续使用
