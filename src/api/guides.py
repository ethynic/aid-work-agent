"""
使用教程文档中心 API

读取 docs/guides/ 下的教程/手册/指南文档，供 Portal「使用教程」页面展示：
- GET /api/guides/catalog  文档目录（按 manifest.yaml 分类分组）
- GET /api/guides/doc      单篇文档内容

仅平台管理员可访问。文档内容不落库，实时读文件；路径经白名单校验（仅 docs/guides
下的 .md），防目录穿越。
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel

from src.api.auth import get_current_user
from src.saas.permissions.checker import is_platform_admin

router = APIRouter(prefix="/api/guides", tags=["使用教程"])

# 与 settings.py 解析 configs/config.yaml 同款的仓库根定位方式
GUIDES_ROOT = Path(__file__).resolve().parent.parent.parent / "docs" / "guides"
MANIFEST_PATH = GUIDES_ROOT / "manifest.yaml"

# 文档内相对图片支持的扩展名 → 响应 Content-Type
IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
}


# ============== 响应模型 ==============

class GuideDocMeta(BaseModel):
    path: str
    title: str
    updated_at: str
    size_bytes: int


class GuideCategory(BaseModel):
    dir: str
    name: str
    docs: list[GuideDocMeta]


class GuideCatalogResponse(BaseModel):
    categories: list[GuideCategory]


class GuideDocResponse(BaseModel):
    path: str
    title: str
    content: str
    updated_at: str


# ============== 工具函数 ==============

def _require_platform_admin(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    if not is_platform_admin(user):
        logger.warning(f"User is not platform admin: {user.get('user_id')}")
        raise HTTPException(status_code=403, detail="仅平台管理员可访问使用教程")
    return user


def _load_categories() -> list[dict]:
    """读取 manifest.yaml 的分类清单；缺失或为空时按子目录名兜底。"""
    try:
        data = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8")) or {}
        categories = data.get("categories") or []
    except FileNotFoundError:
        categories = []
    except Exception as e:
        logger.warning(f"解析 guides manifest 失败，按子目录兜底: {e}")
        categories = []
    if categories:
        return categories
    return [
        {"dir": p.name, "name": p.name}
        for p in sorted(GUIDES_ROOT.iterdir())
        if p.is_dir() and not p.name.startswith(".")
    ]


def _first_h1(text: str) -> Optional[str]:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("#!"):
            return stripped[2:].strip()
    return None


def _doc_title(rel_path: str, text: str) -> str:
    h1 = _first_h1(text)
    if h1:
        return h1
    return Path(rel_path).stem


def _datetime_from_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _resolve_guides_file(rel_path: str, allowed_suffix: str) -> Path:
    """校验相对路径落在 guides 根内且为指定后缀，返回绝对路径；否则 404。"""
    if not rel_path or rel_path.startswith(("/", "\\")) or "\\" in rel_path:
        raise HTTPException(status_code=404, detail="文档不存在")
    try:
        candidate = (GUIDES_ROOT / rel_path).resolve()
        candidate.relative_to(GUIDES_ROOT.resolve())
    except (ValueError, OSError):
        # ValueError：空字节等非法路径；OSError：非法路径字符；统一按不存在处理
        raise HTTPException(status_code=404, detail="文档不存在")
    if candidate.suffix != allowed_suffix or not candidate.is_file():
        raise HTTPException(status_code=404, detail="文档不存在")
    return candidate


def _resolve_doc_path(rel_path: str) -> Path:
    """校验相对路径落在 guides 根内且为 .md，返回绝对路径；否则 404。"""
    return _resolve_guides_file(rel_path, ".md")


# ============== API 端点 ==============

@router.get("/catalog", response_model=GuideCatalogResponse)
async def get_guides_catalog(request: Request):
    """教程文档目录：按 manifest.yaml 分类分组，组内按文件名排序。"""
    _require_platform_admin(request)

    categories: list[GuideCategory] = []
    seen_dirs: set[str] = set()
    for cat in _load_categories():
        cat_dir = cat.get("dir")
        if not cat_dir or cat_dir in seen_dirs:
            continue
        seen_dirs.add(cat_dir)
        base = GUIDES_ROOT / cat_dir
        docs: list[GuideDocMeta] = []
        if base.is_dir():
            for f in sorted(base.rglob("*.md")):
                rel = f.relative_to(GUIDES_ROOT).as_posix()
                try:
                    text = f.read_text(encoding="utf-8")
                except Exception:
                    text = ""
                stat = f.stat()
                docs.append(GuideDocMeta(
                    path=rel,
                    title=_doc_title(rel, text),
                    updated_at=_datetime_from_ts(stat.st_mtime),
                    size_bytes=stat.st_size,
                ))
        categories.append(GuideCategory(dir=cat_dir, name=cat.get("name") or cat_dir, docs=docs))
    return GuideCatalogResponse(categories=categories)


@router.get("/doc", response_model=GuideDocResponse)
async def get_guide_doc(request: Request, path: str = Query(..., description="相对 docs/guides 的路径")):
    """读取单篇教程文档内容。"""
    _require_platform_admin(request)

    file_path = _resolve_doc_path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"读取教程文档失败 {path}: {e}")
        raise HTTPException(status_code=404, detail="文档不存在")
    rel = file_path.relative_to(GUIDES_ROOT).as_posix()
    return GuideDocResponse(
        path=rel,
        title=_doc_title(rel, text),
        content=text,
        updated_at=_datetime_from_ts(file_path.stat().st_mtime),
    )


@router.get("/image")
async def get_guide_image(request: Request, path: str = Query(..., description="相对 docs/guides 的图片路径")):
    """读取文档内引用的相对图片（仅限图片扩展名，路径白名单同 doc）。"""
    _require_platform_admin(request)

    try:
        candidate = (GUIDES_ROOT / path).resolve()
        candidate.relative_to(GUIDES_ROOT.resolve())
    except (ValueError, OSError):
        raise HTTPException(status_code=404, detail="文件不存在")
    media_type = IMAGE_MEDIA_TYPES.get(candidate.suffix.lower())
    if media_type is None or not candidate.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    headers = {"Cache-Control": "private, max-age=600"}
    if candidate.suffix.lower() == ".svg":
        # svg 可携带脚本，与分享下载点同款 sandbox 防护
        headers["Content-Security-Policy"] = "sandbox"
    return FileResponse(candidate, media_type=media_type, headers=headers)
