"""
PPT 图片资产：images 入参的校验与大纲回收

设计：docs/tools/ppt/ppt-image-assets-design.md

- validate_images：租户根域 + 扩展名/存在/大小校验，产出绝对路径形态
- reconcile_image_slides：planner 大纲 image 页确定性回收（路径自愈/未命中丢弃/漏图补页）
- normalize_spec_image_paths：spec 模式 image/raster 节点同样校验与绝对化

安全约束：path 来自 LLM 可控入参，必须经 normcase(realpath) 包含检查限定在
当前租户存储目录内，防路径穿越与跨租户引用（模式同 analysis_artifacts.py）。
"""

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
MAX_IMAGE_COUNT = 20
MAX_IMAGE_SIZE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class ImageAsset:
    """校验通过后的图片资产（path 为绝对路径）。"""

    path: str
    title: str
    caption: str


def resolve_tenant_root() -> str:
    """当前租户存储根 storage/tenants/{tid}；无租户上下文时 _anonymous。

    与 _get_tenant_upload_dir 的租户解析口径一致：tool 执行上下文优先，
    退化 saas request context，最终 _anonymous。
    """
    tenant_id = None
    from src.tools.context import current_tool_execution_context

    context = current_tool_execution_context()
    if context is not None and context.tenant_id:
        tenant_id = context.tenant_id
    if not tenant_id:
        try:
            from src.saas.context import get_current_tenant_id

            tenant_id = get_current_tenant_id()
        except Exception:
            tenant_id = None

    from src.core.storage import get_tenants_storage_root, normalize_tenant_id

    return os.path.join(
        get_tenants_storage_root(), normalize_tenant_id(tenant_id or "_anonymous")
    )


def _within_root(path: str, root: str) -> bool:
    """realpath 包含检查；normcase 归一 Windows 大小写/斜杠差异。"""
    root_real = os.path.normcase(os.path.realpath(root))
    path_real = os.path.normcase(os.path.realpath(path))
    return path_real.startswith(root_real + os.sep)


def validate_images(
    raw_images: List[Dict[str, Any]], tenant_root: Optional[str] = None
) -> Tuple[List[ImageAsset], List[str]]:
    """校验 images 入参，返回 (合法资产, 错误清单)。存在任何错误时调用方整体打回。"""
    root = tenant_root or resolve_tenant_root()
    errors: List[str] = []
    validated: List[ImageAsset] = []
    if len(raw_images) > MAX_IMAGE_COUNT:
        return validated, [f"images 数量超过上限 {MAX_IMAGE_COUNT} 张"]

    for index, item in enumerate(raw_images, start=1):
        raw_path = str(item.get("path", "") or "").strip()
        title = str(item.get("title", "") or "").strip()
        caption = str(item.get("caption", "") or "").strip()
        if not raw_path:
            errors.append(f"第 {index} 张图片缺少 path")
            continue
        if not title:
            errors.append(f"第 {index} 张图片缺少 title（图片标题）")
            continue
        if not caption:
            errors.append(f"第 {index} 张图片《{title}》缺少 caption（需说明图片含义）")
            continue
        if not _within_root(raw_path, root):
            errors.append(f"第 {index} 张图片《{title}》路径不在当前租户存储目录内")
            continue
        # realpath 归一化（含 ../ 往返路径）：_within_root 已按归一化结果放行，
        # 存在性/大小检查与最终资产路径必须用同一归一化路径，避免
        # 「根内合法路径因中间目录不存在被误报文件不存在」
        real_path = os.path.realpath(raw_path)
        if os.path.splitext(raw_path)[1].lower() not in ALLOWED_IMAGE_EXTENSIONS:
            errors.append(f"第 {index} 张图片《{title}》格式不支持，仅支持 png/jpg/jpeg")
            continue
        if not os.path.isfile(real_path):
            errors.append(f"第 {index} 张图片《{title}》文件不存在")
            continue
        size = os.path.getsize(real_path)
        if size == 0:
            errors.append(f"第 {index} 张图片《{title}》文件为空")
            continue
        if size > MAX_IMAGE_SIZE_BYTES:
            errors.append(f"第 {index} 张图片《{title}》超过 20MB 大小限制")
            continue
        validated.append(
            ImageAsset(path=real_path, title=title, caption=caption)
        )
    return validated, errors


def format_image_resources(images: List[ImageAsset]) -> str:
    """构造注入 planner prompt 的图片资源清单。"""
    lines = [
        "可用图片资源（每项含标题、说明与本地路径；"
        "将合适的图片编排为 image 页，image_path 必须原样使用给定路径）："
    ]
    for index, asset in enumerate(images, start=1):
        lines.append(f"{index}. 《{asset.title}》——{asset.caption}")
        lines.append(f"   路径: {asset.path}")
    return "\n".join(lines)


def _is_image_slide(slide: Any) -> bool:
    return isinstance(slide, dict) and str(
        slide.get("layout") or slide.get("type") or ""
    ).lower() == "image"


def reconcile_image_slides(
    plan: Dict[str, Any], images: List[ImageAsset]
) -> Tuple[Dict[str, Any], List[str]]:
    """planner 大纲 image 页回收。

    LLM 转抄路径不可信，返回后统一处理：
    1. image 页 image_path 与清单匹配（normcase 全路径相等 → basename 相等且唯一），
       命中回写为校验后绝对路径（自愈转抄误差）；未命中的 image 页丢弃并记 warning。
    2. 未被引用的图片在最后一个 summary 页前强制补 image 页，保证「传了必进」。
    """
    warnings: List[str] = []
    if not images:
        return plan, warnings

    by_norm_path = {os.path.normcase(asset.path): asset for asset in images}
    basename_counts: Dict[str, int] = {}
    for asset in images:
        name = os.path.basename(asset.path)
        basename_counts[name] = basename_counts.get(name, 0) + 1
    by_basename = {
        os.path.basename(asset.path): asset
        for asset in images
        if basename_counts[os.path.basename(asset.path)] == 1
    }

    used_norm_paths = set()
    kept_slides: List[Any] = []
    for slide in plan.get("slides") or []:
        if not _is_image_slide(slide):
            kept_slides.append(slide)
            continue
        raw_path = str(slide.get("image_path", "") or "").strip()
        asset = None
        if raw_path:
            asset = by_norm_path.get(os.path.normcase(raw_path))
            if asset is None:
                asset = by_basename.get(os.path.basename(raw_path))
        if asset is None:
            warnings.append(
                f"image 页 image_path 未匹配到传入图片，已丢弃: {os.path.basename(raw_path) or '(空)'}"
            )
            continue
        asset_norm = os.path.normcase(asset.path)
        if asset_norm in used_norm_paths:
            warnings.append(
                f"image 页重复引用同一图片，已去重: {os.path.basename(raw_path)}"
            )
            continue
        merged = dict(slide)
        merged["image_path"] = asset.path
        used_norm_paths.add(asset_norm)
        kept_slides.append(merged)

    missing = [
        asset for asset in images if os.path.normcase(asset.path) not in used_norm_paths
    ]
    if missing:
        insert_at = len(kept_slides)
        for position in range(len(kept_slides) - 1, -1, -1):
            slide = kept_slides[position]
            if isinstance(slide, dict) and str(
                slide.get("layout") or slide.get("type") or ""
            ).lower() == "summary":
                insert_at = position
                break
        kept_slides[insert_at:insert_at] = [
            {
                "layout": "image",
                "title": asset.title,
                "image_path": asset.path,
                "caption": asset.caption,
            }
            for asset in missing
        ]
        warnings.append(
            f"{len(missing)} 张传入图片未被大纲引用，已在总结页前补图片页"
        )

    result = dict(plan)
    result["slides"] = kept_slides
    if warnings:
        result["warnings"] = [
            str(item) for item in (plan.get("warnings") or []) if str(item).strip()
        ] + warnings
    return result, warnings


def normalize_spec_image_paths(spec: Any, tenant_root: Optional[str] = None) -> List[str]:
    """spec 模式：校验并规范化 image/raster 节点路径，返回错误清单（非空即整体打回）。"""
    root = tenant_root or resolve_tenant_root()
    errors: List[str] = []
    for slide in spec.slides:
        for node in slide.nodes:
            if node.type not in {"image", "raster"}:
                continue
            raw = str(node.path).strip()
            if (
                os.path.splitext(raw)[1].lower() not in ALLOWED_IMAGE_EXTENSIONS
                or not _within_root(raw, root)
                or not os.path.isfile(raw)
            ):
                errors.append(
                    f"slide[{slide.id}] 图片路径不合法或不可读: {os.path.basename(raw) or '(空)'}"
                )
                continue
            size = os.path.getsize(raw)
            if size == 0 or size > MAX_IMAGE_SIZE_BYTES:
                errors.append(
                    f"slide[{slide.id}] 图片文件为空或超过 20MB 限制: {os.path.basename(raw)}"
                )
                continue
            node.path = os.path.realpath(raw)
    if errors:
        logger.warning(f"[image_assets] spec 图片路径校验失败: {errors}")
    return errors
