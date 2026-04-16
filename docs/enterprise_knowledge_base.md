# 企业知识库功能设计文档

## 1. 概述

### 1.1 目标

为企业用户提供企业知识库功能，支持上传多种格式文档（Word、Excel、PPT、PDF、图片、视频）和网址，实现内容提取、向量化、检索召回，让 Agent 能够基于企业内部知识回答问题。

### 1.2 设计原则

- **稳定生产**：采用 SQLite + sqlite-vec 方案，零额外基础设施，部署极简
- **规范工作流**：标准化文档解析、分块、向量化、检索流程
- **快捷响应**：混合检索（向量 + FTS5），毫秒级响应
- **可扩展**：预留替换为 LanceDB/Milvus/Weaviate 的接口

### 1.3 目标规模

| 指标 | 估算值 |
|------|--------|
| 文档数量 | 500 份 |
| 每份文档字数 | ~30,000 字 |
| 总向量数 | ~30,000 个 |
| 存储体积（1024维）| ~175 MB |

---

## 2. 技术选型

### 2.1 向量数据库

#### 当前方案：SQLite + sqlite-vec

**理由：**
- 项目已使用 SQLite，零新增基础设施
- 30,000 个向量在 sqlite-vec 舒适区内
- 混合检索（sqlite-vec + FTS5）开箱即用
- 部署极简，只需增加一个 Python 包

#### 未来升级路径

```python
# TODO: 后期替换为 LanceDB（轻量级，文件型，无服务器）
# 适用场景：向量数 > 100 万
# 迁移方式：实现 IVectorDatabase 接口，替换 VectorDBSQLite 实现

# TODO: 后期替换为 Milvus（分布式，有服务器）
# 适用场景：需要高可用、分布式部署
# 迁移方式：实现 IVectorDatabase 接口，替换 VectorDBSQLite 实现

# TODO: 后期替换为 Weaviate（多模态）
# 适用场景：需要图像/多模态向量
# 迁移方式：实现 IVectorDatabase 接口，替换 VectorDBSQLite 实现
```

### 2.2 Embedding 模型

#### 当前方案：通义 text-embedding-v3

- **定价**：0.5 元 / 百万 Token（Batch 半价 0.25 元/百万）
- **维度**：1024
- **最大输入**：8192 tokens / 次
- **中文效果**：⭐⭐⭐⭐
- **理由**：与 Qwen 同源，账单合并，免费额度 50 万 Token（90 天）

#### 未来升级路径

```python
# TODO: 后期替换为智谱 embedding-3
# 适用场景：主用智谱 GLM 时
# 迁移方式：修改 embedding_factory.py 的工厂方法，替换 TextEmbeddingV3 实现

# TODO: 后期替换为 bge-m3（本地）
# 适用场景：数据敏感场景，需要完全离线
# 迁移方式：修改 embedding_factory.py 的工厂方法，替换 LocalBGE3 实现
```

### 2.3 文档解析库

| 文档格式 | 解析库 | 说明 |
|---------|-------|------|
| Word | `python-docx>=1.1.0` | 已有 |
| Excel | `openpyxl>=3.1.0` | 已有 |
| PPT | `python-pptx>=0.6.23` | 已有 |
| PDF | `pypdf>=4.0.0` | 需新增 |
| 图片 | `Pillow>=10.0.0` + 多模态 LLM | OCR 文字提取 + 图片描述生成 |
| 视频 | `ffmpeg-python>=0.2.0` + 多模态 LLM | 关键帧提取 + 语音转文字 |
| 网址 | `playwright>=1.40.0` 或 `requests>=2.31.0` + BeautifulSoup | 网页内容抓取 |

### 2.4 增量依赖

```txt
# 向量检索
sqlite-vec>=0.1.6

# 文本处理
# tiktoken>=0.7.0  # Token 计数/分块（暂不使用，改用字符数估算）

# PDF 解析
pypdf>=4.0.0

# 图片处理
Pillow>=10.0.0  # 缩略图生成、EXIF 提取

# 视频处理（TODO：后续阶段实现）
# ffmpeg-python>=0.2.0  # 视频转码、关键帧提取
# opencv-python>=4.8.0  # 视频帧处理

# 网页抓取（TODO：后续阶段实现）
playwright>=1.40.0  # JavaScript 渲染页面抓取
beautifulsoup4>=4.12.0  # HTML 解析
requests>=2.31.0  # HTTP 请求
lxml>=4.9.0  # XML/HTML 解析器
```

---

## 3. 系统架构

### 3.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        前端（Vue 3）                              │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────────┐ │
│  │  知识库页面  │  │  文件/网址上传 │  │      文档管理列表       │ │
│  └──────┬──────┘  └──────┬───────┘  └───────────┬─────────────┘ │
└─────────┼─────────────────┼──────────────────────┼──────────────┘
          │                 │                      │
          │ HTTP/SSE        │                      │
┌─────────┼─────────────────┼──────────────────────┼──────────────┐
│         ↓                 ↓                      ↓              │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                     FastAPI 后端                          │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐│  │
│  │  │ 知识库 API    │  │  文档上传 API │  │  文档管理 API   ││  │
│  │  │ /api/kb/*   │  │ /api/upload  │  │ /api/docs/*     ││  │
│  │  └──────┬───────┘  └──────┬───────┘  └────────┬────────┘│  │
│  │         │                 │                   │         │  │
│  │  ┌──────▼─────────────────▼───────────────────▼───────┐│  │
│  │  │           KnowledgeBaseService                      ││  │
│  │  │  - 文档解析器（Word/Excel/PPT/PDF Parser）         ││  │
│  │  │  - 图片解析器（ImageParser: OCR + 多模态描述）     ││  │
│  │  │  - 视频解析器（VideoParser: 关键帧 + ASR）        ││  │
│  │  │  - 网址解析器（UrlParser: 网页抓取）              ││  │
│  │  │  - 分块器（TextChunker）                          ││  │
│  │  │  - 向量化器（EmbeddingClient）                    ││  │
│  │  │  - 向量数据库（VectorDatabase）                   ││  │
│  │  │  - 检索器（Retriever）                            ││  │
│  │  └───────────────────────────┬───────────────────────┘│  │
│  └──────────────────────────────┼────────────────────────┘  │
│                                 │                             │
│  ┌──────────────────────────────▼────────────────────────┐   │
│  │                    SQLite 数据库                      │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐  │   │
│  │  │ documents   │  │   chunks     │  │ sqlite-vec  │  │   │
│  │  │ 表（文档元数据）│  │ 表（文本块）  │  │ 虚拟表      │  │   │
│  │  │ + 多媒体元数据 │  └──────┬───────┘  └─────────────┘  │   │
│  │  └──────────────┘          │                           │   │
│  │                   ┌────────▼─────────┐                  │   │
│  │                   │   FTS5 虚拟表     │                  │   │
│  │                   │   (全文检索)      │                  │   │
│  │                   └──────────────────┘                  │   │
│  └────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────┘

                    ↓
┌─────────────────────────────────────────────────────────────────┐
│                   通义 text-embedding-v3                        │
│                    (Embedding API)                              │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 核心模块

#### 3.2.1 后端模块

| 模块 | 路径 | 说明 |
|------|------|------|
| 知识库工具 | `src/tools/knowledge/` | 内置工具，提供给 Agent 调用 |
| 知识库服务 | `src/knowledge/` | 业务逻辑层 |
| 文档解析器 | `src/knowledge/parsers/` | Word/Excel/PPT/PDF 解析 |
| 图片解析器 | `src/knowledge/parsers/image_parser.py` | OCR + 多模态描述 |
| 视频解析器 | `src/knowledge/parsers/video_parser.py` | 关键帧 + 语音转文字 |
| 网址解析器 | `src/knowledge/parsers/url_parser.py` | 网页抓取与解析 |
| 向量数据库 | `src/knowledge/vector_db/` | sqlite-vec 实现 |
| Embedding 客户端 | `src/knowledge/embedding/` | text-embedding-v3 封装 |
| 检索器 | `src/knowledge/retriever/` | 混合检索（向量 + FTS5）|
| 知识库 API | `src/api/knowledge.py` | FastAPI 路由 |

#### 3.2.2 前端模块

| 模块 | 路径 | 说明 |
|------|------|------|
| 知识库页面 | `frontend/src/views/KnowledgeView.vue` | 主页面 |
| 文档上传 | `frontend/src/components/DocumentUpload.vue` | 上传组件 |
| 文档管理 | `frontend/src/components/DocumentList.vue` | 文档列表 |
| API 封装 | `frontend/src/api/knowledge.ts` | 知识库接口 |

---

## 4. 数据库设计

### 4.1 Schema 设计

```sql
-- 文档表
CREATE TABLE documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,                -- 上传用户 ID
    title TEXT NOT NULL,                     -- 文档标题
    source_type TEXT NOT NULL,               -- 来源类型（file/url）
    file_type TEXT NOT NULL,                 -- 文件类型（docx/xlsx/pptx/pdf/jpg/png/gif/mp4/avi/mov/mkv/webm/html）
    file_path TEXT NOT NULL,                 -- 文件存储路径（文件类型）或 URL（网址类型）
    file_size INTEGER,                       -- 文件大小（字节），URL 类型为空
    total_chunks INTEGER NOT NULL,           -- 总分块数
    embedding_model TEXT NOT NULL,           -- 使用的 Embedding 模型
    -- 多媒体元数据
    thumbnail_path TEXT,                      -- 缩略图路径（图片/视频）
    duration INTEGER,                        -- 时长（秒，视频/音频）
    width INTEGER,                           -- 宽度（图片/视频）
    height INTEGER,                          -- 高度（图片/视频）
    mime_type TEXT,                          -- MIME 类型
    -- 文本内容（图片/视频/网址的 OCR/描述/网页文本）
    raw_text TEXT,                           -- 原始文本内容
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_documents_user ON documents(user_id);

-- 文本块表
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,                -- 关联 documents.id
    chunk_index INTEGER NOT NULL,           -- 块序号（从 0 开始）
    text TEXT NOT NULL,                      -- 文本内容
    tokens INTEGER NOT NULL,                -- Token 数量
    metadata TEXT,                          -- JSON 元数据（页码、标题等）
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX idx_chunks_doc ON chunks(doc_id);

-- 向量表（sqlite-vec 虚拟表）
-- 注意：sqlite-vec 需要动态加载，详见实现
CREATE VIRTUAL TABLE chunks_vec USING vec0(
    chunk_id INTEGER PRIMARY KEY,
    embedding float[1024]  -- 向量维度，与 text-embedding-v3 一致
);

-- 全文检索表（FTS5）
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    text,                  -- 全文检索字段
    content_rowid = chunks.id,  -- 关联 chunks.id
    tokenize = 'unicode61'  -- 支持 Unicode（含中文）分词
);

-- 触发器：自动同步 FTS5
CREATE TRIGGER chunks_fts_insert AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER chunks_fts_delete AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.id, old.text);
END;

CREATE TRIGGER chunks_fts_update AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.id, old.text);
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
```

### 4.2 数据流程

```
1. 用户上传文档/图片/视频 或提供网址
   ↓
2. 文件类型：
   - 保存到文件系统（uploads/knowledge/{doc_id}.{ext}）
   URL 类型：
   - 保存 URL，记录到 file_path
   ↓
3. 插入 documents 表记录
   ↓
4. 文档解析器提取文本
   ├─ 文本文件（docx/xlsx/pptx/pdf）：直接提取文本
   ├─ 图片：OCR 文字提取 + 多模态 LLM 描述生成
   ├─ 视频：关键帧提取 + 语音转文字 + 多模态描述
   └─ 网址：Playwright/requests 抓取网页文本
   ↓
5. 文本分块（chunk_size=512, overlap=64）
   ↓
6. 分批调用 Embedding API
   ↓
7. 写入 chunks 表 + chunks_vec 表 + chunks_fts 表
```

---

## 5. 核心功能设计

### 5.1 文档解析

#### 5.1.1 解析器接口

```python
# src/knowledge/parsers/base.py
from abc import ABC, abstractmethod
from typing import List, Dict, Optional
from dataclasses import dataclass

@dataclass
class ParseResult:
    """解析结果"""
    text: str                              # 提取的文本内容
    metadata: Dict[str, any]               # 元数据（页码、时长、分辨率等）
    thumbnail_path: Optional[str] = None   # 缩略图路径（图片/视频）
    raw_text: Optional[str] = None        # 原始文本（未处理的 OCR 结果等）

class BaseParser(ABC):
    """文档解析器基类"""

    @abstractmethod
    async def parse(self, file_path: str) -> ParseResult:
        """
        解析文档，提取文本和元数据

        Args:
            file_path: 文档路径或 URL

        Returns:
            ParseResult 对象（包含文本、元数据、缩略图等）
        """
        pass

    @abstractmethod
    async def get_metadata(self, file_path: str) -> Dict:
        """
        提取文档元数据

        Returns:
            元数据字典（标题、作者、页数、时长、分辨率等）
        """
        pass

    @abstractmethod
    def supported_extensions(self) -> List[str]:
        """支持的文件扩展名"""
        pass

    @property
    def source_type(self) -> str:
        """来源类型：file 或 url"""
        return "file"
```

#### 5.1.2 多模态解析器接口扩展

```python
# 图片解析器
class ImageParser(BaseParser):
    """图片解析器"""

    @abstractmethod
    async def parse(self, file_path: str) -> ParseResult:
        """
        解析图片，提取 OCR 文字和描述

        Returns:
            ParseResult:
                - text: OCR 文字 + 图片描述的组合文本
                - metadata: {"width": int, "height": int, "format": str, "mode": str}
                - thumbnail_path: 缩略图路径
        """
        pass

# 视频解析器
class VideoParser(BaseParser):
    """视频解析器"""

    @abstractmethod
    async def parse(self, file_path: str) -> ParseResult:
        """
        解析视频，提取关键帧描述和语音转文字

        Returns:
            ParseResult:
                - text: 语音转文字 + 关键帧描述的组合文本
                - metadata: {"duration": int, "width": int, "height": int, "fps": float}
                - thumbnail_path: 视频缩略图路径
        """
        pass

# 网址解析器
class UrlParser(BaseParser):
    """网址解析器"""

    @property
    def source_type(self) -> str:
        return "url"

    @abstractmethod
    async def parse(self, url: str) -> ParseResult:
        """
        抓取网址内容

        Returns:
            ParseResult:
                - text: 网页标题 + meta 描述 + 正文文本
                - metadata: {"title": str, "description": str, "links": List[str]}
        """
        pass
```

#### 5.1.2 各格式解析器实现

| 格式 | 解析器 | 实现要点 |
|------|--------|---------|
| Word | `WordParser` | 遍历 `document.paragraphs` 提取文本 |
| Excel | `ExcelParser` | 遍历所有 `sheet.rows`，跳过空行 |
| PPT | `PPTParser` | 遍历 `slide.shapes`，提取 `text_frame.text` |
| PDF | `PDFParser` | 使用 `pypdf`，遍历 `PdfReader.pages` |
| 图片 | `ImageParser` | OCR（PaddleOCR）+ 多模态描述（Qwen-VL） |
| 视频 | `VideoParser` | 关键帧提取（OpenCV）+ 语音转文字（Whisper）+ 多模态描述 |
| 网址 | `UrlParser` | Playwright/requests 抓取 + BeautifulSoup 解析 |

##### 图片解析器实现要点

```python
# src/knowledge/parsers/image_parser.py
class ImageParser(ImageParser):
    """图片解析器"""

    def __init__(self, llm_client=None):
        self.llm_client = llm_client  # 多模态 LLM 客户端

    async def parse(self, file_path: str) -> ParseResult:
        # 1. 提取基本信息（尺寸、格式等）
        metadata = await self.get_metadata(file_path)

        # 2. 生成缩略图
        thumbnail_path = await self._generate_thumbnail(file_path)

        # 3. OCR 文字提取（使用 PaddleOCR）
        ocr_text = await self._extract_ocr(file_path)

        # 4. 图片描述（使用多模态 LLM）
        description = await self._generate_description(file_path)

        # 5. 组合文本
        text = f"图片描述：{description}\n\nOCR 文字：{ocr_text}"

        return ParseResult(
            text=text,
            metadata=metadata,
            thumbnail_path=thumbnail_path,
            raw_text=ocr_text
        )
```

##### 视频解析器（TODO：后续阶段实现）

```python
# src/knowledge/parsers/video_parser.py
# TODO: 后续阶段实现视频解析
# 实现要点：
# 1. 语音转文字：使用 Whisper（需引入 openai-whisper + PyTorch，体积 ~2GB）
# 2. 关键帧提取：使用 OpenCV 按固定间隔提取视频帧
# 3. 关键帧描述：使用多模态 LLM 生成每个关键帧的描述
# 4. 文本组合：语音转文字 + 关键帧描述
# 5. 缩略图生成：提取视频中间帧作为缩略图
#
# 注意事项：
# - 视频处理耗时长，建议异步处理
# - 需要服务器安装 ffmpeg
# - 视频文件较大，上传可能受带宽限制
# - 建议限制视频最大时长（如 10 分钟）
```

##### 网址解析器实现要点

```python
# src/knowledge/parsers/url_parser.py
class UrlParser(UrlParser):
    """网址解析器"""

    def __init__(self, use_playwright: bool = True):
        self.use_playwright = use_playwright  # 是否使用 Playwright（支持 JS 渲染）

    async def parse(self, url: str) -> ParseResult:
        # 1. 获取网页内容
        if self.use_playwright:
            html_content = await self._fetch_with_playwright(url)
        else:
            html_content = await self._fetch_with_requests(url)

        # 2. 解析 HTML
        parsed = await self._parse_html(html_content, url)

        # 3. 组合文本
        text = (
            f"网页标题：{parsed['title']}\n\n"
            f"网页描述：{parsed['description']}\n\n"
            f"网页内容：\n{parsed['content']}"
        )

        return ParseResult(
            text=text,
            metadata={
                "title": parsed['title'],
                "description": parsed['description'],
                "links": parsed['links'],
                "url": url
            }
        )

    async def _fetch_with_playwright(self, url: str) -> str:
        """使用 Playwright 抓取动态网页"""
        # 使用 src/tools/browser/ 中的 Playwright 工具
        pass

    async def _fetch_with_requests(self, url: str) -> str:
        """使用 requests 抓取静态网页"""
        pass

    async def _parse_html(self, html: str, base_url: str) -> Dict:
        """使用 BeautifulSoup 解析 HTML"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, 'lxml')

        # 移除 script 和 style 标签
        for tag in soup(['script', 'style']):
            tag.decompose()

        # 提取标题
        title = soup.title.string if soup.title else ""

        # 提取 meta 描述
        description = ""
        if soup.find('meta', attrs={'name': 'description'}):
            description = soup.find('meta', attrs={'name': 'description'})['content']

        # 提取正文内容（简单实现，实际可更复杂）
        # 优先查找 article 或 main 标签
        main_content = soup.find('article') or soup.find('main') or soup.find('body')
        content = main_content.get_text(separator="\n", strip=True) if main_content else ""

        # 提取链接
        links = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            # 转为绝对 URL
            if href.startswith('/'):
                from urllib.parse import urljoin
                href = urljoin(base_url, href)
            links.append(href)

        return {
            "title": title,
            "description": description,
            "content": content[:10000],  # 限制内容长度
            "links": links[:100]  # 限制链接数量
        }
```

### 5.2 文本分块

```python
# src/knowledge/chunker.py
class TextChunker:
    """文本分块器"""

    def __init__(
        self,
        chunk_size: int = 512,      # 每块字符数
        overlap: int = 64            # 重叠字符数
    ):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def _estimate_tokens(self, text: str) -> int:
        """估算 Token 数量（中文约 1.5 字/Token，英文约 4 字/Token）"""
        # 简单估算：中文每字约 1 Token，英文每 4 字符约 1 Token
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other_chars = len(text) - chinese_chars
        return chinese_chars + other_chars // 4

    def chunk(self, text: str) -> List[Dict[str, any]]:
        """
        将文本切分成块（按字符数分割，估算 Token）

        Returns:
            List[{"text": str, "tokens": int, "index": int}]
        """
        # 1. 按段落分割
        paragraphs = text.split("\n\n")

        # 2. 合并段落直到达到 chunk_size
        chunks = []
        current_chunk = ""
        current_chars = 0
        chunk_index = 0

        for para in paragraphs:
            para_chars = len(para)

            if current_chars + para_chars > self.chunk_size:
                if current_chunk:
                    chunks.append({
                        "text": current_chunk.strip(),
                        "tokens": self._estimate_tokens(current_chunk),
                        "index": chunk_index
                    })
                    chunk_index += 1

                # 保留重叠部分
                overlap_text = current_chunk[-self.overlap:]
                current_chunk = overlap_text + "\n\n" + para
                current_chars = len(current_chunk)
            else:
                current_chunk += "\n\n" + para if current_chunk else para
                current_chars += para_chars

        # 最后一块
        if current_chunk:
            chunks.append({
                "text": current_chunk.strip(),
                "tokens": self._estimate_tokens(current_chunk),
                "index": chunk_index
            })

        return chunks
```

### 5.3 Embedding 客户端

```python
# src/knowledge/embedding/embedding_client.py
import dashscope
from dashscope import TextEmbedding
from typing import List
import logging

logger = logging.getLogger(__name__)

class TextEmbeddingV3Client:
    """通义 text-embedding-v3 客户端"""

    def __init__(self, api_key: str):
        dashscope.api_key = api_key
        self.model = "text-embedding-v3"
        self.dimension = 1024

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        批量向量化

        Args:
            texts: 文本列表

        Returns:
            向量列表，每个向量维度为 1024

        Raises:
            Exception: API 调用失败
        """
        import asyncio

        try:
            # TextEmbedding.call 是同步 SDK，使用 to_thread 包装
            resp = await asyncio.to_thread(
                TextEmbedding.call,
                model=self.model,
                input=texts,
                text_type="document"  # document 类型，适合知识库
            )

            if resp.status_code != 200:
                raise Exception(f"Embedding API 失败: {resp.message}")

            embeddings = [item["embedding"] for item in resp.output["embeddings"]]
            return embeddings

        except Exception as e:
            logger.error(f"Embedding 批量调用失败: {e}")
            raise

    async def embed(self, text: str) -> List[float]:
        """单文本向量化"""
        embeddings = await self.embed_batch([text])
        return embeddings[0]
```

```python
# src/knowledge/embedding/embedding_factory.py
from .embedding_client import TextEmbeddingV3Client

# TODO: 后期替换为智谱 embedding-3
# from .zhipu_embedding_client import ZhipuEmbedding3Client

# TODO: 后期替换为 bge-m3（本地）
# from .bge3_client import BGE3Client

def create_embedding_client(provider: str = "qwen", **kwargs):
    """
    Embedding 客户端工厂

    Args:
        provider: 提供商（qwen/zhipu/bge3）
        **kwargs: 配置参数

    Returns:
        Embedding 客户端实例
    """
    # TODO: 后期根据 provider 切换不同实现
    # if provider == "zhipu":
    #     return ZhipuEmbedding3Client(api_key=kwargs["api_key"])
    # elif provider == "bge3":
    #     return BGE3Client(model_path=kwargs.get("model_path", "BAAI/bge-m3"))
    # else:
    return TextEmbeddingV3Client(api_key=kwargs["api_key"])
```

### 5.4 向量数据库（SQLite + sqlite-vec）

```python
# src/knowledge/vector_db/vector_db.py
import sqlite3
import sqlite3.dbapi2
import sqlite_vec  # 需要动态加载扩展
from typing import List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)

class VectorDatabase:
    """向量数据库接口"""

    async def insert(self, chunk_ids: List[int], embeddings: List[List[float]]) -> None:
        """插入向量"""
        raise NotImplementedError

    async def search(
        self,
        query_embedding: List[float],
        top_k: int = 10
    ) -> List[Tuple[int, float]]:
        """
        向量相似度搜索

        Returns:
            List[(chunk_id, similarity)]
        """
        raise NotImplementedError

    async def delete_by_doc(self, doc_id: int) -> None:
        """删除文档的所有向量"""
        raise NotImplementedError


class VectorDBSQLite(VectorDatabase):
    """SQLite + sqlite-vec 实现"""

    def __init__(self, db_path: str, dimension: int = 1024):
        self.db_path = db_path
        self.dimension = dimension
        self.conn = None

        # 加载 sqlite-vec 扩展
        sqlite_vec.load()

        self._initialize_db()

    def _initialize_db(self):
        """初始化数据库和表结构"""
        self.conn = sqlite3.connect(self.db_path)
        cursor = self.conn.cursor()

        # 创建 chunks_vec 虚拟表（如果不存在）
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
                chunk_id INTEGER PRIMARY KEY,
                embedding float[?]
            )
        """, (self.dimension,))

        self.conn.commit()

    async def insert(self, chunk_ids: List[int], embeddings: List[List[float]]) -> None:
        """批量插入向量"""
        cursor = self.conn.cursor()

        data = [
            (chunk_id, list(embedding))
            for chunk_id, embedding in zip(chunk_ids, embeddings)
        ]

        cursor.executemany(
            "INSERT INTO chunks_vec(chunk_id, embedding) VALUES (?, ?)",
            data
        )

        self.conn.commit()
        logger.info(f"插入了 {len(chunk_ids)} 个向量")

    async def search(
        self,
        query_embedding: List[float],
        top_k: int = 10
    ) -> List[Tuple[int, float]]:
        """向量相似度搜索（余弦相似度）"""
        cursor = self.conn.cursor()

        # sqlite-vec 距离是 L2 距离，需要转换为余弦相似度
        # 这里简化处理，返回 chunk_id 和 L2 距离（越小越相似）
        cursor.execute("""
            SELECT chunk_id, distance
            FROM chunks_vec
            WHERE embedding MATCH ?
            ORDER BY distance
            LIMIT ?
        """, (query_embedding, top_k))

        results = cursor.fetchall()

        # 将 L2 距离转换为相似度（归一化到 0~1）
        # 简单起见，这里直接返回 chunk_id 和负距离（越大越相似）
        return [(chunk_id, -distance) for chunk_id, distance in results]

    async def delete_by_doc(self, doc_id: int) -> None:
        """删除文档的所有向量"""
        cursor = self.conn.cursor()

        cursor.execute("""
            DELETE FROM chunks_vec
            WHERE chunk_id IN (
                SELECT id FROM chunks WHERE doc_id = ?
            )
        """, (doc_id,))

        self.conn.commit()
        logger.info(f"删除了文档 {doc_id} 的所有向量")
```

### 5.5 检索器（混合检索）

```python
# src/knowledge/retriever/hybrid_retriever.py
from typing import List, Dict, Tuple
import logging

logger = logging.getLogger(__name__)

class HybridRetriever:
    """混合检索器（向量 + FTS5 + RRF 融合）"""

    def __init__(
        self,
        vector_db: VectorDatabase,
        embedding_client: TextEmbeddingV3Client,
        conn: sqlite3.Connection
    ):
        self.vector_db = vector_db
        self.embedding_client = embedding_client
        self.conn = conn

    async def retrieve(
        self,
        query: str,
        top_k: int = 10,
        user_id: Optional[int] = None
    ) -> List[Dict[str, any]]:
        """
        混合检索

        Args:
            query: 用户查询
            top_k: 返回结果数量
            user_id: 用户 ID（权限控制）

        Returns:
            检索结果列表：[{
                "chunk_id": int,
                "doc_id": int,
                "text": str,
                "tokens": int,
                "metadata": dict,
                "score": float
            }]
        """
        # 1. 向量检索
        query_embedding = await self.embedding_client.embed(query)
        vector_results = await self.vector_db.search(query_embedding, top_k=top_k * 2)

        # 2. FTS5 全文检索
        fts_results = self._fts_search(query, top_k=top_k * 2)

        # 3. RRF（Reciprocal Rank Fusion）融合
        fused = self._rrf_fusion(vector_results, fts_results, k=top_k)

        # 4. 构建完整结果
        results = self._build_results(fused[:top_k])

        # 5. 权限过滤（如果指定了 user_id）
        if user_id is not None:
            results = self._filter_by_user(results, user_id)

        return results

    def _fts_search(self, query: str, top_k: int) -> List[Tuple[int, float]]:
        """FTS5 全文检索"""
        cursor = self.conn.cursor()

        cursor.execute("""
            SELECT chunk_id, bm25(chunks_fts) as score
            FROM chunks_fts
            WHERE chunks_fts MATCH ?
            ORDER BY score
            LIMIT ?
        """, (query, top_k))

        return [(row[0], row[1]) for row in cursor.fetchall()]

    def _rrf_fusion(
        self,
        vector_results: List[Tuple[int, float]],
        fts_results: List[Tuple[int, float]],
        k: int = 60
    ) -> List[Tuple[int, float]]:
        """
        RRF 融合

        Args:
            vector_results: [(chunk_id, score), ...]
            fts_results: [(chunk_id, score), ...]
            k: RRF 参数（通常 60）

        Returns:
            [(chunk_id, fused_score), ...]
        """
        scores = {}

        # 向量检索的排名
        for rank, (chunk_id, _) in enumerate(vector_results):
            scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (k + rank + 1)

        # FTS5 检索的排名
        for rank, (chunk_id, _) in enumerate(fts_results):
            scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (k + rank + 1)

        # 按融合分数排序
        sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_results

    def _build_results(self, fused: List[Tuple[int, float]]) -> List[Dict[str, any]]:
        """构建完整的检索结果"""
        chunk_ids = [chunk_id for chunk_id, _ in fused]

        cursor = self.conn.cursor()
        cursor.execute(f"""
            SELECT id, doc_id, text, tokens, metadata
            FROM chunks
            WHERE id IN ({','.join(['?'] * len(chunk_ids))})
        """, chunk_ids)

        rows = cursor.fetchall()

        # 按 fused 顺序排序
        id_to_row = {row[0]: row for row in rows}
        results = []
        for chunk_id, score in fused:
            row = id_to_row[chunk_id]
            results.append({
                "chunk_id": row[0],
                "doc_id": row[1],
                "text": row[2],
                "tokens": row[3],
                "metadata": row[4] if row[4] else {},
                "score": score
            })

        return results

    def _filter_by_user(self, results: List[Dict], user_id: int) -> List[Dict]:
        """按用户过滤（可选：如果实现用户级别的文档权限）"""
        # TODO: 实现用户级别权限过滤
        return results
```

### 5.6 知识库工具（提供给 Agent 调用）

```python
# src/tools/knowledge/knowledge_base_tool.py
from ..base import BaseTool
from src.knowledge.retriever.hybrid_retriever import HybridRetriever
from src.config.settings import settings
import logging

logger = logging.getLogger(__name__)

class KnowledgeBaseTool(BaseTool):
    """知识库检索工具"""

    name = "knowledge_base_search"
    description = "从企业知识库中检索相关信息，回答用户问题"

    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "用户问题或查询关键词"
            },
            "top_k": {
                "type": "integer",
                "description": "返回的相关段落数量，默认 10",
                "default": 10
            }
        },
        "required": ["query"]
    }

    def __init__(self):
        # 初始化检索器
        from src.knowledge.vector_db.vector_db import VectorDBSQLite
        from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client

        import sqlite3
        conn = sqlite3.connect(settings.database_url.replace("sqlite:///", ""))

        vector_db = VectorDBSQLite(
            db_path=settings.database_url.replace("sqlite:///", ""),
            dimension=1024
        )
        embedding_client = TextEmbeddingV3Client(api_key=settings.qwen_api_key)

        self.retriever = HybridRetriever(
            vector_db=vector_db,
            embedding_client=embedding_client,
            conn=conn
        )

    async def execute(self, args: dict) -> dict:
        """
        执行知识库检索

        Args:
            args: {"query": str, "top_k": int}

        Returns:
            {
                "results": [
                    {
                        "text": str,
                        "doc_title": str,
                        "score": float,
                        "metadata": dict
                    }
                ]
            }
        """
        query = args["query"]
        top_k = args.get("top_k", 10)

        try:
            results = await self.retriever.retrieve(
                query=query,
                top_k=top_k
                # TODO: 从 context 中获取 user_id
            )

            # 提取文档标题
            doc_ids = {r["doc_id"] for r in results}

            import sqlite3
            conn = sqlite3.connect(settings.database_url.replace("sqlite:///", ""))
            cursor = conn.cursor()

            cursor.execute(f"""
                SELECT id, title FROM documents
                WHERE id IN ({','.join(['?'] * len(doc_ids))})
            """, list(doc_ids))

            doc_titles = {row[0]: row[1] for row in cursor.fetchall()}

            # 格式化结果
            formatted_results = [
                {
                    "text": r["text"],
                    "doc_title": doc_titles[r["doc_id"]],
                    "score": r["score"],
                    "metadata": r["metadata"]
                }
                for r in results
            ]

            return {
                "results": formatted_results,
                "count": len(formatted_results)
            }

        except Exception as e:
            logger.error(f"知识库检索失败: {e}")
            return {
                "error": str(e),
                "results": [],
                "count": 0
            }
```

---

## 6. API 设计

### 6.1 知识库文档管理 API

```python
# src/api/knowledge.py
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from typing import List, Optional
from pydantic import BaseModel

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class DocumentResponse(BaseModel):
    id: int
    title: str
    source_type: str          # file 或 url
    file_type: str            # 文件类型
    file_size: Optional[int]   # 文件大小（URL 类型为空）
    file_path: str            # 文件路径或 URL
    total_chunks: int
    thumbnail_path: Optional[str]  # 缩略图路径
    duration: Optional[int]       # 时长（秒，视频/音频）
    width: Optional[int]          # 宽度（图片/视频）
    height: Optional[int]          # 高度（图片/视频）
    created_at: str


class UploadResponse(BaseModel):
    document_id: int
    status: str
    message: str


@router.post("/upload", response_model=UploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    user_id: Optional[int] = None
):
    """
    上传知识库文档

    - 支持格式：docx, xlsx, pptx, pdf, jpg, jpeg, png, gif, webp, bmp, mp4, avi, mov, mkv, webm
    - 自动解析、分块、向量化
    - 返回文档 ID
    """
    # 1. 验证文件格式
    allowed_extensions = [
        "docx", "xlsx", "pptx", "pdf",  # 文档
        "jpg", "jpeg", "png", "gif", "webp", "bmp",  # 图片
        "mp4", "avi", "mov", "mkv", "webm"  # 视频
    ]
    ext = file.filename.split(".")[-1].lower()
    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"不支持的文件格式: {ext}")

    # 2. 保存文件
    # 3. 解析文档
    # 4. 分块
    # 5. 向量化
    # 6. 写入数据库

    pass


class UrlUploadRequest(BaseModel):
    """网址上传请求"""
    url: str                               # 网址 URL
    title: Optional[str] = None            # 可选的标题（不提供则自动提取）
    user_id: Optional[int] = None


@router.post("/upload-url", response_model=UploadResponse)
async def upload_url(request: UrlUploadRequest):
    """
    上传网址到知识库

    - 支持公司官网、文档页面、新闻文章等网页内容
    - 自动抓取网页文本、标题、描述
    - 使用 Playwright 支持 JavaScript 渲染的页面
    - 自动解析、分块、向量化
    - 返回文档 ID
    """
    from urllib.parse import urlparse

    # 1. 验证 URL 格式
    parsed = urlparse(request.url)
    if not all([parsed.scheme, parsed.netloc]):
        raise HTTPException(status_code=400, detail=f"无效的网址格式: {request.url}")

    # 2. 检查是否为支持的协议
    if parsed.scheme not in ["http", "https"]:
        raise HTTPException(status_code=400, detail=f"不支持的协议: {parsed.scheme}")

    # 3. 抓取网页内容（UrlParser）
    # 4. 提取标题（自动从网页或使用提供的标题）
    # 5. 分块
    # 6. 向量化
    # 7. 写入数据库

    pass


@router.get("/documents", response_model=List[DocumentResponse])
async def list_documents(
    user_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0
):
    """
    获取知识库文档列表

    - 支持按用户过滤
    - 分页查询
    """
    pass


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: int):
    """
    删除知识库文档

    - 级联删除所有 chunks
    - 级联删除所有向量
    - 删除文件
    """
    pass


@router.get("/documents/{doc_id}/chunks")
async def get_document_chunks(doc_id: int):
    """
    获取文档的所有分块（用于调试）
    """
    pass


@router.post("/search")
async def search_knowledge(
    query: str,
    top_k: int = 10,
    user_id: Optional[int] = None
):
    """
    知识库检索接口

    - 调用混合检索器
    - 返回 Top-K 相关段落
    """
    pass
```

---

## 7. 前端设计

### 7.1 知识库页面结构

```vue
<!-- frontend/src/views/KnowledgeView.vue -->
<template>
  <div class="knowledge-container">
    <!-- 顶部：上传区域 -->
    <div class="upload-section">
      <DocumentUpload @upload-success="refreshDocuments" />
    </div>

    <!-- 中部：文档列表 -->
    <div class="documents-section">
      <DocumentList
        :documents="documents"
        @delete="handleDelete"
        @view-chunks="handleViewChunks"
      />
    </div>

    <!-- 底部：检索测试（可选）-->
    <div class="search-section">
      <SearchTest />
    </div>
  </div>
</template>
```

### 7.2 文档上传组件

```vue
<!-- frontend/src/components/DocumentUpload.vue -->
<template>
  <div class="upload-container">
    <h3>上传文档</h3>

    <!-- 模式切换 -->
    <div class="upload-mode-tabs">
      <button
        :class="{ active: uploadMode === 'file' }"
        @click="uploadMode = 'file'"
      >
        📄 文件上传
      </button>
      <button
        :class="{ active: uploadMode === 'url' }"
        @click="uploadMode = 'url'"
      >
        🔗 网址输入
      </button>
    </div>

    <!-- 文件上传模式 -->
    <div v-if="uploadMode === 'file'" class="upload-area" @click="triggerFileInput">
      <input
        ref="fileInput"
        type="file"
        accept=".docx,.xlsx,.pptx,.pdf,.jpg,.jpeg,.png,.gif,.webp,.bmp,.mp4,.avi,.mov,.mkv,.webm"
        @change="handleFileChange"
        style="display: none"
      />
      <div v-if="!uploading">
        <div class="icon">📄</div>
        <p>点击或拖拽上传文档</p>
        <p class="hint">支持格式：Word、Excel、PPT、PDF、图片（jpg/png/gif/webp）、视频（mp4/mov/avi）</p>
      </div>
      <div v-else>
        <div class="loader"></div>
        <p>正在上传和处理...</p>
        <p class="hint" v-if="processingStep">{{ processingStep }}</p>
      </div>
    </div>

    <!-- 网址输入模式 -->
    <div v-if="uploadMode === 'url'" class="url-upload-area">
      <input
        v-model="urlInput"
        type="url"
        placeholder="请输入网址，如 https://example.com"
        class="url-input"
        @keyup.enter="handleUrlSubmit"
      />
      <button @click="handleUrlSubmit" :disabled="!urlInput || uploading" class="submit-btn">
        提交
      </button>
      <p class="hint" v-if="uploading && processingStep">{{ processingStep }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import axios from 'axios'

const emit = defineEmits(['upload-success', 'upload-error'])

const uploading = ref(false)
const uploadMode = ref<'file' | 'url'>('file')
const urlInput = ref('')
const processingStep = ref('')

const triggerFileInput = () => {
  document.querySelector('input[type="file"]').click()
}

const handleFileChange = async (event) => {
  const file = event.target.files[0]
  if (!file) return

  uploading.value = true
  processingStep.value = '正在上传文件...'

  const formData = new FormData()
  formData.append('file', file)

  try {
    processingStep.value = '正在解析文档...'
    const response = await axios.post('/api/knowledge/upload', formData)
    console.log('上传成功:', response.data)
    processingStep.value = '完成！'
    emit('upload-success')
  } catch (error) {
    console.error('上传失败:', error)
    emit('upload-error', error)
  } finally {
    uploading.value = false
    processingStep.value = ''
  }
}

const handleUrlSubmit = async () => {
  if (!urlInput.value) return

  uploading.value = true
  processingStep.value = '正在抓取网页内容...'

  try {
    processingStep.value = '正在解析网页...'
    const response = await axios.post('/api/knowledge/upload-url', {
      url: urlInput.value
    })
    console.log('网址上传成功:', response.data)
    processingStep.value = '完成！'
    urlInput.value = ''
    emit('upload-success')
  } catch (error) {
    console.error('网址上传失败:', error)
    emit('upload-error', error)
  } finally {
    uploading.value = false
    processingStep.value = ''
  }
}
</script>
```

### 7.3 文档列表组件

```vue
<!-- frontend/src/components/DocumentList.vue -->
<template>
  <div class="document-list">
    <table>
      <thead>
        <tr>
          <th>缩略图</th>
          <th>标题</th>
          <th>类型</th>
          <th>大小/时长</th>
          <th>分块数</th>
          <th>上传时间</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="doc in documents" :key="doc.id">
          <td>
            <img
              v-if="doc.thumbnail_path"
              :src="doc.thumbnail_path"
              class="thumbnail"
              alt="缩略图"
            />
            <span v-else class="no-thumbnail">-</span>
          </td>
          <td>{{ doc.title }}</td>
          <td>
            <span class="file-type-badge">{{ doc.file_type }}</span>
            <span v-if="doc.source_type === 'url'" class="source-type-badge">网址</span>
          </td>
          <td>
            <span v-if="doc.duration">{{ formatDuration(doc.duration) }}</span>
            <span v-else>{{ formatFileSize(doc.file_size) }}</span>
          </td>
          <td>{{ doc.total_chunks }}</td>
          <td>{{ formatDate(doc.created_at) }}</td>
          <td>
            <button @click="handleDelete(doc.id)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
```

---

## 8. 实现步骤

### 阶段一：MVP - 文档解析 + 向量化 + 检索（优先）

1. **新增依赖**
   - 修改 `requirements.txt`，添加：
     - `sqlite-vec>=0.1.6`、`pypdf>=4.0.0`
     - 图片处理：`Pillow>=10.0.0`

2. **数据库初始化**
   - 创建 `src/knowledge/__init__.py`
   - 创建 `src/knowledge/vector_db/vector_db.py`
   - 创建数据库迁移脚本，创建 documents、chunks、chunks_vec、chunks_fts 表

3. **文档解析器（MVP：仅 Word/Excel/PPT/PDF）**
   - 创建 `src/knowledge/parsers/base.py`
   - 创建 `src/knowledge/parsers/word_parser.py`
   - 创建 `src/knowledge/parsers/excel_parser.py`
   - 创建 `src/knowledge/parsers/ppt_parser.py`
   - 创建 `src/knowledge/parsers/pdf_parser.py`
   - 创建 `src/knowledge/parsers/parser_factory.py`
   - 创建 `src/knowledge/parsers/image_parser.py`（TODO 占位）
   - 创建 `src/knowledge/parsers/video_parser.py`（TODO 占位）
   - 创建 `src/knowledge/parsers/url_parser.py`（TODO 占位）

4. **文本分块器**
   - 创建 `src/knowledge/chunker.py`

5. **Embedding 客户端**
   - 创建 `src/knowledge/embedding/embedding_client.py`
   - 创建 `src/knowledge/embedding/embedding_factory.py`

6. **检索器**
   - 创建 `src/knowledge/retriever/hybrid_retriever.py`

7. **知识库工具**
   - 创建 `src/tools/knowledge/__init__.py`
   - 创建 `src/tools/knowledge/knowledge_base_tool.py`
   - 在 `src/core/agent.py` 的 `AGENT_TOOLS` 中注册

### 阶段二：API 层

8. **知识库 API**
   - 创建 `src/api/knowledge.py`
   - 实现文档上传、列表、删除、检索接口
   - 在 `src/main.py` 中注册路由

### 阶段三：前端

9. **API 封装**
   - 创建 `frontend/src/api/knowledge.ts`

10. **知识库页面**
    - 创建 `frontend/src/views/KnowledgeView.vue`
    - 创建 `frontend/src/components/DocumentUpload.vue`
    - 创建 `frontend/src/components/DocumentList.vue`
    - 在路由中注册

### 阶段四：测试

11. **单元测试**
    - 测试文档解析器
    - 测试分块器
    - 测试 Embedding 客户端
    - 测试检索器

12. **集成测试**
    - 测试完整上传流程
    - 测试检索准确性

### 阶段五：后续扩展（TODO）

- 图片解析（OCR + 多模态描述）
- 视频解析（关键帧 + 语音转文字）
- 网址解析（网页抓取）
- 网址上传 API
- 文档处理进度反馈（SSE 推送）
- 用户级别权限过滤
- 文档更新（增量更新向量）
- 批量导入

---

## 9. 配置项

### 9.1 环境变量

```env
# Embedding 配置
API_KEYS=xxx  # LLM API Key

# 知识库配置
KNOWLEDGE_UPLOAD_PATH=uploads/knowledge  # 文档存储路径
KNOWLEDGE_CHUNK_SIZE=512  # 分块大小（Token）
KNOWLEDGE_CHUNK_OVERLAP=64  # 分块重叠（Token）
KNOWLEDGE_SEARCH_TOP_K=10  # 默认检索数量

# 多媒体处理配置
KNOWLEDGE_THUMBNAIL_SIZE=200x200  # 缩略图尺寸
KNOWLEDGE_VIDEO_MAX_DURATION=600  # 视频最大时长（秒），超过则截断
KNOWLEDGE_VIDEO_KEY_FRAMES=30  # 视频关键帧数量
KNOWLEDGE_IMAGE_MAX_SIZE=10485760  # 图片最大大小（字节），默认 10MB
KNOWLEDGE_VIDEO_MAX_SIZE=104857600  # 视频最大大小（字节），默认 100MB

# 网址抓取配置
KNOWLEDGE_URL_USE_PLAYWRIGHT=true  # 是否使用 Playwright（支持 JS 渲染）
KNOWLEDGE_URL_TIMEOUT=30  # 网址抓取超时（秒）
KNOWLEDGE_URL_MAX_CONTENT_LENGTH=10000  # 网页内容最大长度（字符）
```

### 9.2 配置文件（`configs/config.yaml`）

```yaml
knowledge:
  enabled: true
  upload_path: uploads/knowledge
  chunk_size: 512
  chunk_overlap: 64
  search_top_k: 10

  # 多媒体配置
  thumbnail:
    size: "200x200"  # 缩略图尺寸
  video:
    max_duration: 600  # 最大时长（秒）
    key_frames: 30    # 关键帧数量
  media:
    image_max_size: 10485760   # 10MB
    video_max_size: 104857600  # 100MB

  # 网址抓取配置
  url_fetch:
    use_playwright: true  # 使用 Playwright（支持 JS 渲染页面）
    timeout: 30           # 超时（秒）
    max_content_length: 10000  # 内容最大长度

  embedding:
    provider: qwen  # TODO: 后期支持 zhipu/bge3
    model: text-embedding-v3
    dimension: 1024

  vector_db:
    type: sqlite  # TODO: 后期支持 lancedb/milvus/weaviate
    path: sqlite:///./aid_work_agent.db

  retrieval:
    method: hybrid  # hybrid / vector / fts5
    rrf_k: 60
```

---

## 10. 费用估算

### 10.1 Embedding 费用

```
文档：
  总字数：500 × 30,000 = 1,500 万字
  总 Token：约 2,000 万 Token

  建库一次性费用：
    2,000 万 ÷ 100 万 × 0.5 元 = 10 元

图片（OCR + 描述）：
  假设 100 张图片，平均每张处理 500 Token
  总 Token：100 × 500 = 5 万 Token
  费用：5 万 ÷ 100 万 × 0.5 元 = 0.025 元

视频（语音转文字 + 关键帧描述）：
  假设 20 个视频，平均每个处理 2000 Token
  总 Token：20 × 2000 = 4 万 Token
  费用：4 万 ÷ 100 万 × 0.5 元 = 0.02 元

网址（网页抓取文本）：
  假设 200 个网址，平均每个处理 1000 Token
  总 Token：200 × 1000 = 20 万 Token
  费用：20 万 ÷ 100 万 × 0.5 元 = 0.1 元

查询费用（1000 次提问）：
  1000 × 50 ÷ 1,000,000 × 0.5 元 ≈ 0.025 元
```

### 10.2 存储费用

```
文档存储：500 × 平均 2 MB = 1 GB
图片存储：100 × 平均 2 MB = 200 MB
视频存储：20 × 平均 50 MB = 1 GB
缩略图存储：120 × 50 KB = 6 MB
向量存储：30,000 × 1024 × 4 字节 = 175 MB
总存储：~2.4 GB
```

### 10.3 多模态 LLM 费用（可选）

如果使用 Qwen-VL 进行图片描述生成：
```
100 张图片 × 500 Token/张 × 0.02 元/千 Token = 1 元
```

---

## 11. 后续扩展

### 11.1 权限控制

- 实现文档级别权限（哪些用户可以访问哪些文档）
- 管理员可以上传、删除文档，普通用户只能查看

### 11.2 智能问答

- 基于 RAG 的智能问答，自动检索 + 生成回答
- 支持引用来源，展示相关文档段落

### 11.3 数据更新

- 支持文档更新（增量更新向量）
- 支持批量导入

### 11.4 多语言

- 支持英文等其他语言文档
- 多语言混合检索

### 11.5 网址/网页内容支持

**功能说明**：
用户可以直接输入公司官网、文档页面、新闻文章等网址，系统自动抓取网页文本内容并向量化。

**技术实现**：
1. **静态网页抓取**：使用 `requests` + `BeautifulSoup`，轻量快速
2. **动态网页抓取**：使用 `Playwright`，支持 JavaScript 渲染的页面（如 React/Vue SPA）
3. **内容提取**：
   - 标题：`<title>` 标签或 `<meta property="og:title">`
   - 描述：`<meta name="description">` 或 `<meta property="og:description">`
   - 正文：优先提取 `<article>`、`<main>`、`<body>` 标签内容
4. **链接处理**：
   - 提取页面中的内部链接（相对路径转绝对路径）
   - 可选的递归抓取（限制深度，避免爬取失控）

**使用场景**：
- 导入公司官网产品介绍
- 导入外部文档页面（如 Notion、Confluence）
- 导入新闻文章、行业报告
- 导入技术博客文章

**注意事项**：
- 需要遵守网站的 `robots.txt` 协议
- 避免频繁抓取同一网站（建议添加请求间隔）
- 部分网站可能需要登录或验证码，无法抓取
- 网页内容变化时，已入库的内容不会自动更新（需要重新抓取）

### 11.6 图片支持

**功能说明**：
支持上传 jpg、jpeg、png、gif、webp、bmp 等常见图片格式，自动提取图片中的文字（OCR）和图片描述。

**技术实现**：
1. **OCR 文字提取**：使用 PaddleOCR（本地部署）或通义 OCR API
2. **图片描述生成**：使用多模态 LLM（如 Qwen-VL、GPT-4V）生成图片描述
3. **文本组合**：`图片描述 + OCR 文字` 作为图片的完整文本表示
4. **缩略图生成**：使用 Pillow 生成缩略图，便于前端展示

**使用场景**：
- 导入产品图片及其说明文字
- 导入扫描件、截图
- 导入包含文字信息的图表图片

**注意事项**：
- OCR 效果受图片质量影响（模糊、低分辨率会影响识别准确率）
- 手写文字识别准确率较低
- 图片描述依赖多模态 LLM，可能产生误差

### 11.7 视频支持

**功能说明**：
支持上传 mp4、avi、mov、mkv、webm 等常见视频格式，自动提取视频中的语音（ASR）和关键帧描述。

**技术实现**：
1. **语音转文字**：使用 Whisper（本地或 API）提取视频中的语音内容
2. **关键帧提取**：使用 OpenCV 按固定间隔提取视频帧（默认每秒 1 帧，最多 30 帧）
3. **关键帧描述**：使用多模态 LLM 生成每个关键帧的描述
4. **文本组合**：`语音转文字 + 关键帧描述` 作为视频的完整文本表示
5. **缩略图生成**：提取视频中间帧作为缩略图

**使用场景**：
- 导入产品演示视频
- 导入培训课程录像
- 导入会议记录视频
- 导入视频形式的公告、通知

**注意事项**：
- 视频处理耗时较长，建议异步处理并添加进度提示
- 语音转文字效果受音频质量影响（背景噪音会影响识别）
- 视频时长建议控制在 10 分钟以内（可通过配置调整）
- 视频文件较大，上传可能受网络带宽限制

---

## 12. 参考资料

- [sqlite-vec 官方文档](https://github.com/asg017/sqlite-vec)
- [通义 text-embedding-v3 API 文档](https://help.aliyun.com/zh/model-studio/developer-reference/text-embedding-v3-api)
- [FTS5 全文检索文档](https://www.sqlite.org/fts5.html)
- [RRF（Reciprocal Rank Fusion）论文](https://plg.uwaterloo.ca/~gvcormac/cormacksigir08-rrf.pdf)
- [Pillow 图像处理库](https://pillow.readthedocs.io/)
- [OpenCV 计算机视觉库](https://opencv.org/)
- [Playwright 浏览器自动化](https://playwright.dev/)
- [BeautifulSoup HTML 解析库](https://www.crummy.com/software/BeautifulSoup/)
- [Whisper 语音转文字](https://github.com/openai/whisper)
- [PaddleOCR 文字识别](https://github.com/PaddlePaddle/PaddleOCR)
