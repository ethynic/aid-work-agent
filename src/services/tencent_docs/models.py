"""腾讯文档适配层数据模型（纯数据，无 IO）"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TencentDocsCredentials:
    """OpenAPI 调用三元组。

    - 个人开发者 Token（开发测试）与 OAuth 换取的用户级 token（多租户）
      均以此结构注入 client，调用方无感。
    - open_id 即 OAuth token 响应中的 user_id 字段。
    """

    client_id: str
    access_token: str
    open_id: str


@dataclass
class DriveFile:
    """drive 文件列表项（GET /openapi/drive/v2/folders/{folderID}）。

    shortcut_id 非空表示快捷方式文档：内容操作必须传 shortcut_id
    而非 ID（官方文档 §2.4 ID 体系）。
    """

    file_id: str
    title: str
    type: str
    url: str = ""
    status: str = "normal"
    shortcut_id: str = ""
    is_owner: bool = False
    create_time: int = 0
    last_modify_time: int = 0
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_api(cls, item: dict) -> "DriveFile":
        return cls(
            file_id=item.get("ID") or "",
            title=item.get("title") or "",
            type=item.get("type") or "",
            url=item.get("url") or "",
            status=item.get("status") or "normal",
            shortcut_id=item.get("shortcutID") or "",
            is_owner=bool(item.get("isOwner")),
            create_time=int(item.get("createTime") or 0),
            last_modify_time=int(item.get("lastModifyTime") or 0),
            raw=item,
        )

    def content_target_id(self) -> str:
        """内容操作（读取/导出）应使用的 ID：快捷方式文档用 shortcutID。"""
        return self.shortcut_id or self.file_id


@dataclass
class ExportResult:
    """导出兜底结果。"""

    file_id: str
    export_type: str  # "docx" | "pdf"
    file_path: str
    size_bytes: int = 0
    operation_id: str = ""


@dataclass
class QuotaInfo:
    """导出配额状态（每 openID 每日，官方默认 9 次）。"""

    used: int
    limit: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


def parse_v2_file_id(file_id: str) -> str:
    """URL 路径中的 fileID 编码：`$` 必须转义为 %24（2026-10-20 实测）。

    本函数返回可直接拼进 URL 路径的转义形式。
    """
    return file_id.replace("$", "%24")


def extract_encoded_id_from_url(url: str) -> Optional[str]:
    """从文档分享链接提取 encodedID（如 https://docs.qq.com/doc/DXXXX?tab=bb -> DXXXX）。"""
    if not url:
        return None
    tail = url.split("#", 1)[0].split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    return tail or None
