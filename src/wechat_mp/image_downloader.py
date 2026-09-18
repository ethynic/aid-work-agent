"""微信 CDN 图片下载转存（WP10，设计 §6.1/§13，P2 图片 VL 解析前置步骤）。

职责边界：只做「图片 URL 列表 → 校验 → 下载 → 解码限幅 → 转存本地」，不做 VL
解析（vision.py）与计费（service.py 在业务提交后按张落账）。

安全约束（设计 §13 门禁，与 fetcher.py 逐跳校验同源但域名白名单不同）：
- 仅接受 HTTPS 且 host 为微信 CDN 白名单（``*.qpic.cn`` 精确后缀匹配，含
  mmbiz/mmecoa 等子域；``evil-qpic.cn`` 这类以连字符伪装的域名不命中）
- 禁 userinfo / IP 字面量 / 非常规端口；DNS 全量解析必须全部为公网 IP
  （防 DNS 重绑定到内网/回环），重定向**逐跳重新校验**（上限 3 跳）
- Referer 固定 ``https://mp.weixin.qq.com/``（微信 CDN 防盗链要求）
- 单张 ≤10MB（流式累计字节超限即中断）、超时 15s、每文章图片 200 张硬护栏
  （防失控文章，非产品限制，WP13-r2 移除原 30 张产品上限）
- 解码像素上限（防解压炸弹）：像素面积超限拒绝；长边超限等比缩小到 2000px

转存路径对齐 src/core/storage.py 惯例：
    storage/tenants/{tid}/knowledge/wechat_mp/{article_row_id}/img_{n}.{ext}

幂等：下载前先查本地已转存文件（img_{n}.* 非空即复用），不重复下载
（hash 未变的重复处理零下载）。

失败分类逐张记录（domain_rejected/timeout/size_exceeded/http_404 等），不中断
其余图片；调用方凭 DownloadOutcome 自行决定 deferred 或部分入库。
"""
from __future__ import annotations

import glob
import ipaddress
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import httpx
from loguru import logger

from src.core.storage import get_tenant_storage_dir
from src.wechat_mp.fetcher import BROWSER_UA, _resolve_all_public

# ------------------------------- 常量 -------------------------------

DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 单张 10MB（设计 §6.1）
MAX_REDIRECTS = 3  # 重定向上限（逐跳重新校验）
# WP13-r2：单篇图片防失控硬护栏（非产品限制）——原 30 张产品上限移除（奢石图集
# 等 53 图长图集在 30 张处截断丢图）；200 仅防失控/异常超大文章拖垮下载与计费，
# 超出部分不请求、记 skipped_over_limit（语义不变）
MAX_IMAGES_PER_ARTICLE = 200
REFERER = "https://mp.weixin.qq.com/"

# 微信 CDN 白名单：host 必须以 .qpic.cn 结尾（WP0 实测 mmbiz/mmecoa.qpic.cn）
ALLOWED_HOST_SUFFIX = ".qpic.cn"
# 转存目录内的 src 清单文件名（幂等复用校验：n → 源 URL，防内容变更后编号错位复用）
_MANIFEST_NAME = "images.json"
# 长边上限：超过等比缩小（解码后的展示/解析尺寸，非解码面积上限）
MAX_LONG_EDGE = 2000
# 解码像素面积上限（防解压炸弹；超限拒绝——缩小也需要先解码全图）
MAX_PIXELS = 25_000_000  # 约 5000×5000

# Content-Type / URL 后缀 → 扩展名白名单（均未命中的图片拒绝转存）
_CONTENT_TYPE_EXT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}
_URL_SUFFIX_EXT = {"jpg": "jpg", "jpeg": "jpg", "png": "png", "gif": "gif", "webp": "webp"}

_REDIRECT_STATUSES = (301, 302, 303, 307, 308)

# 请求头按请求注入（而非仅挂在内建 client 上）：注入测试 client 时行为一致
_REQUEST_HEADERS = {"User-Agent": BROWSER_UA, "Referer": REFERER}


class ImageDownloadError(Exception):
    """单张图片失败（携带脱敏 reason，由 download 收敛进 outcome.failures）。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _TooLargeError(Exception):
    """单张图片超过 MAX_IMAGE_BYTES（流式累计/Content-Length 超限）。"""


# ------------------------------- 结果结构 -------------------------------


@dataclass
class ImageDownload:
    """单张图片下载成功结果。"""

    n: int                 # 图片序号（1 起，与 [图片N] 占位编号一致）
    local_path: str        # 转存相对路径（storage/tenants/...，统一 / 分隔）
    ext: str               # 扩展名（jpg/png/gif/webp）
    bytes_written: int
    reused: bool = False   # 是否命中本地已有文件（幂等复用）


@dataclass
class ImageFailure:
    """单张图片失败记录（不中断其余图片）。"""

    n: int
    reason: str            # 脱敏错误类别（domain_rejected/timeout/size_exceeded/...）


@dataclass
class DownloadOutcome:
    """一次批量下载结果：成功/失败逐张分类 + 超上限跳过数。"""

    images: List[ImageDownload] = field(default_factory=list)
    failures: List[ImageFailure] = field(default_factory=list)
    skipped_over_limit: int = 0  # 超出每文章上限而未尝试的图片数

    @property
    def ok_count(self) -> int:
        return len(self.images)


# ------------------------------- 校验 -------------------------------


def validate_image_url(url: str, resolver=_resolve_all_public) -> Optional[str]:
    """校验单个微信 CDN 图片 URL；合法返回 None，否则返回脱敏拒收原因。

    规则（设计 §13）：仅 HTTPS；host 精确后缀命中 ``*.qpic.cn``；禁 userinfo /
    IP 字面量 / 非常规端口；DNS 全量解析必须全为公网 IP。resolver 可注入隔离真实 DNS。
    """
    split = urlsplit(url)
    if split.scheme != "https":
        return "scheme_not_https"
    if split.username or split.password:
        return "userinfo_not_allowed"
    host = (split.hostname or "").lower().rstrip(".")
    if not host:
        return "host_missing"
    try:
        ipaddress.ip_address(host)
        return "ip_literal_not_allowed"
    except ValueError:
        pass
    # 精确后缀匹配：evil-qpic.cn 以连字符拼接，不命中 .qpic.cn 后缀
    if not host.endswith(ALLOWED_HOST_SUFFIX):
        return "host_not_allowed"
    try:
        port = split.port
    except ValueError:
        return "port_invalid"
    if port not in (None, 443):
        return "port_not_allowed"
    if not resolver(host):
        return "dns_not_public"
    return None


def _ext_from_response(content_type: str, url_path: str) -> Optional[str]:
    """扩展名推断：优先 Content-Type，回退 URL 后缀；均不在白名单返回 None。"""
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype in _CONTENT_TYPE_EXT:
        return _CONTENT_TYPE_EXT[ctype]
    filename = url_path.rsplit("/", 1)[-1]
    if "." in filename:
        return _URL_SUFFIX_EXT.get(filename.rsplit(".", 1)[-1].lower())
    return None


# ------------------------------- 下载器 -------------------------------


class MPImageDownloader:
    """微信 CDN 图片下载转存器（同步实现，调用方用 asyncio.to_thread 包裹）。

    测试可注入 ``client``（httpx.Client 兼容对象，如 httpx.MockTransport）与
    ``resolver``（hostname → bool）替换真实网络/DNS。
    """

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = MAX_IMAGE_BYTES,
        max_images: int = MAX_IMAGES_PER_ARTICLE,
        max_long_edge: int = MAX_LONG_EDGE,
        max_pixels: int = MAX_PIXELS,
        client: Optional[httpx.Client] = None,
        resolver=None,
    ):
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._max_images = max_images
        self._max_long_edge = max_long_edge
        self._max_pixels = max_pixels
        self._client = client
        self._owns_client = client is None
        self._resolver = resolver or _resolve_all_public

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._timeout,
                follow_redirects=False,  # 重定向逐跳手动校验（设计 §13）
                headers={"User-Agent": BROWSER_UA, "Referer": REFERER},
            )
        return self._client

    # ---------------- 批量入口 ----------------

    def download(
        self,
        tenant_id: str,
        article_row_id: int,
        image_srcs: List[Tuple[int, str]],
    ) -> DownloadOutcome:
        """按序号下载转存图片列表 [(n, src)]；逐张独立，失败不中断。

        超出每文章上限的图片不发起请求，计入 skipped_over_limit。
        目录：storage/tenants/{tid}/knowledge/wechat_mp/{article_row_id}/
        """
        outcome = DownloadOutcome()
        items = sorted(image_srcs, key=lambda x: x[0])
        if len(items) > self._max_images:
            outcome.skipped_over_limit = len(items) - self._max_images
            items = items[: self._max_images]

        target_dir = os.path.join(
            get_tenant_storage_dir(tenant_id, "knowledge"),
            "wechat_mp",
            str(article_row_id),
        )

        client = self._get_client()
        manifest = self._load_manifest(target_dir)
        manifest_dirty = False
        for n, src in items:
            # 幂等复用：本地已转存且 manifest 记录的同序号 src 一致（hash 未变语义
            # 在文件层兜底）；内容变更导致编号错位时不会误复用旧图
            reused = self._find_existing(target_dir, n) if manifest.get(str(n)) == src else None
            if reused is not None:
                outcome.images.append(
                    ImageDownload(
                        n=n, local_path=reused.replace("\\", "/"),
                        ext=reused.rsplit(".", 1)[-1],
                        bytes_written=os.path.getsize(reused), reused=True,
                    )
                )
                continue
            try:
                image = self._download_one(client, n, src, target_dir)
                outcome.images.append(image)
                manifest[str(n)] = src
                manifest_dirty = True
            except ImageDownloadError as exc:
                logger.bind(module="wechat_mp").info(
                    "wechat_mp 图片下载失败 n={} article_id={} reason={}",
                    n, article_row_id, exc.reason,
                )
                outcome.failures.append(ImageFailure(n=n, reason=exc.reason))
            except Exception as exc:  # noqa: BLE001 单张异常收敛为失败记录
                logger.bind(module="wechat_mp").warning(
                    "wechat_mp 图片下载异常 n={} article_id={} reason={}",
                    n, article_row_id, type(exc).__name__,
                )
                outcome.failures.append(ImageFailure(n=n, reason="download_error"))

        if manifest_dirty:
            self._save_manifest(target_dir, manifest)
        if outcome.skipped_over_limit:
            logger.bind(module="wechat_mp").info(
                "wechat_mp 图片超上限跳过 article_id={} skipped={} limit={}",
                article_row_id, outcome.skipped_over_limit, self._max_images,
            )
        return outcome

    # ---------------- 单张 ----------------

    @staticmethod
    def _find_existing(target_dir: str, n: int) -> Optional[str]:
        """查找本地已转存的 img_{n}.*（非空即复用）；无则返回 None。"""
        for path in glob.glob(os.path.join(target_dir, f"img_{n}.*")):
            if os.path.getsize(path) > 0:
                return path
        return None

    @staticmethod
    def _load_manifest(target_dir: str) -> Dict[str, str]:
        """读取转存目录 src 清单（n → 源 URL）；缺失/损坏返回空 dict。"""
        path = os.path.join(target_dir, _MANIFEST_NAME)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _save_manifest(target_dir: str, manifest: Dict[str, str]) -> None:
        try:
            os.makedirs(target_dir, exist_ok=True)
            with open(os.path.join(target_dir, _MANIFEST_NAME), "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
        except OSError as exc:
            logger.bind(module="wechat_mp").warning(
                "wechat_mp 图片清单写入失败 reason={}", type(exc).__name__
            )

    def _download_one(
        self, client: httpx.Client, n: int, src: str, target_dir: str
    ) -> ImageDownload:
        """下载并转存单张；失败抛 ImageDownloadError（脱敏 reason）。"""
        url = src
        hops = 0
        while True:
            reject = validate_image_url(url, resolver=self._resolver)
            if reject:
                raise ImageDownloadError(reject)
            if hops > MAX_REDIRECTS:
                raise ImageDownloadError("too_many_redirects")
            try:
                location, status, data, content_type = self._request_once(client, url)
            except _TooLargeError:
                raise ImageDownloadError("size_exceeded")
            except httpx.TimeoutException:
                raise ImageDownloadError("timeout")
            except httpx.HTTPError:
                raise ImageDownloadError("network_error")
            if location is not None:
                # 重定向逐跳重新校验主机/IP（urljoin 覆盖相对 Location）
                url = urljoin(url, location)
                hops += 1
                continue
            if status != 200 or data is None:
                raise ImageDownloadError(f"http_{status}")
            break

        ext = _ext_from_response(content_type, urlsplit(url).path)
        if ext is None:
            raise ImageDownloadError("unsupported_content_type")

        # 解码限幅（防解压炸弹 + 长边缩小）；损坏图片按失败处理
        data, ext, resized = self._normalize_pixels(data, ext)
        if data is None:
            raise ImageDownloadError("decode_failed")

        os.makedirs(target_dir, exist_ok=True)
        local_path = os.path.join(target_dir, f"img_{n}.{ext}")
        with open(local_path, "wb") as f:
            f.write(data)
        logger.bind(module="wechat_mp").debug(
            "wechat_mp 图片转存 n={} bytes={} resized={}", n, len(data), resized
        )
        return ImageDownload(
            n=n, local_path=local_path.replace("\\", "/"), ext=ext, bytes_written=len(data)
        )

    def _request_once(self, client: httpx.Client, url: str):
        """单跳请求。返回 (location|None, status, bytes|None, content_type)。

        Content-Length 或流式累计超过 _max_bytes 抛 _TooLargeError（中断读取）。
        """
        with client.stream("GET", url, headers=_REQUEST_HEADERS) as resp:
            if resp.status_code in _REDIRECT_STATUSES:
                return resp.headers.get("location"), resp.status_code, None, ""
            content_length = resp.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > self._max_bytes:
                raise _TooLargeError()
            chunks: List[bytes] = []
            total = 0
            for chunk in resp.iter_bytes(chunk_size=65536):
                total += len(chunk)
                if total > self._max_bytes:
                    raise _TooLargeError()
                chunks.append(chunk)
            data = b"".join(chunks) if chunks else b""
            return None, resp.status_code, (data or None), resp.headers.get("content-type", "")

    def _normalize_pixels(self, data: bytes, ext: str) -> Tuple[Optional[bytes], str, bool]:
        """Pillow 解码校验：像素面积超限拒绝；长边超限等比缩小。

        GIF 帧动画重编码时取首帧（VL 解析静态语义可接受）。
        返回 (bytes|None, ext, resized)；解码失败返回 (None, ext, False)。
        面积/尺寸均在限内且扩展名在白名单时原样返回（零重编码）。
        """
        try:
            from io import BytesIO

            from PIL import Image
        except ImportError:  # pragma: no cover - Pillow 在 requirements 必装
            logger.bind(module="wechat_mp").error("Pillow 不可用，跳过像素校验")
            return data, ext, False

        try:
            with Image.open(BytesIO(data)) as img:
                img.load()
                width, height = img.size
                if width * height > self._max_pixels:
                    logger.bind(module="wechat_mp").warning(
                        "wechat_mp 图片解码像素超限 pixels={}x{}", width, height
                    )
                    return None, ext, False
                if max(width, height) <= self._max_long_edge:
                    return data, ext, False
                # 等比缩小（重编码为 JPEG；透明通道以 RGB 白底语义可接受）
                scale = self._max_long_edge / float(max(width, height))
                new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
                resized = img.convert("RGB").resize(new_size)
                buf = BytesIO()
                resized.save(buf, format="JPEG", quality=85)
                return buf.getvalue(), "jpg", True
        except Exception as exc:  # noqa: BLE001 损坏/不支持图片
            logger.bind(module="wechat_mp").warning(
                "wechat_mp 图片解码失败 reason={}", type(exc).__name__
            )
            return None, ext, False
