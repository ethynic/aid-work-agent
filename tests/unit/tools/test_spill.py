"""
_spill 落盘管理器单元测试

覆盖：
- spill_large_content 成功落盘、内容完整、preview 截断正确、命名规范、meta 写入
- full_size / truncated 字段正确
- cleanup_stale_spill_files 清理旧文件、保留新文件、返回删除数
- 不存在的 spill 目录不报错
"""

import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.tools]


@pytest.fixture
def isolated_spill_dir(tmp_path, monkeypatch):
    """把落盘目录隔离到 tmp_path，避免污染真实临时目录 / 串扰其他测试。"""
    spill_dir = tmp_path / "aid_agent_spill"
    # patch tempfile.gettempdir 让 _spill 用隔离目录
    monkeypatch.setattr(
        "src.tools._spill.tempfile.gettempdir", lambda: str(tmp_path)
    )
    return spill_dir


# ---------------------------------------------------------------------------
# spill_large_content
# ---------------------------------------------------------------------------

class TestSpillLargeContent:
    """spill_large_content 核心行为测试"""

    def test_spill_writes_file_and_returns_path(self, isolated_spill_dir):
        """落盘成功：文件存在、返回绝对路径、内容完整"""
        from src.tools._spill import spill_large_content

        content = "hello world\n" * 1000  # > 5000 字符
        result = spill_large_content(content, prefix="test_")

        assert result["truncated"] is True
        assert result["full_size"] == len(content)

        file_path = Path(result["file_path"])
        assert file_path.is_absolute()
        assert file_path.exists()
        # 内容完整落盘
        assert file_path.read_text(encoding="utf-8") == content
        # 文件在隔离的 spill 目录下
        assert isolated_spill_dir in file_path.parents or file_path.parent == isolated_spill_dir

    def test_preview_truncated_to_5000(self, isolated_spill_dir):
        """preview 截断到 5000 字符（含截断后缀）"""
        from src.tools._spill import spill_large_content, PREVIEW_LIMIT

        content = "x" * 10000
        result = spill_large_content(content)

        preview = result["preview"]
        # truncate_text 截断到 5000 + 后缀 "..."
        assert preview.endswith("...")
        # preview 去掉后缀后应是前 5000 字符
        assert preview[:-3] == "x" * PREVIEW_LIMIT
        assert len(preview[:-3]) == PREVIEW_LIMIT

    def test_small_content_preview_not_truncated(self, isolated_spill_dir):
        """小内容也能落盘（本函数不判断大小），preview 不截断，truncated 反映 preview 是否截断"""
        from src.tools._spill import spill_large_content

        # 本函数不判断内容是否够大，调用方判断；小内容也能落盘
        content = "small content"
        result = spill_large_content(content)

        # truncated 反映 preview 是否被截断（小内容未超 5000，应为 False）
        assert result["truncated"] is False
        assert result["full_size"] == len(content)
        # preview 与原文一致（未超 5000，不截断）
        assert result["preview"] == content

    def test_filename_naming_convention(self, isolated_spill_dir):
        """文件命名规范：{prefix}{YYYYMMDDHHmmss}_{8位hex}{suffix}"""
        from src.tools._spill import spill_large_content

        result = spill_large_content("data" * 2000, prefix="httpapi_", suffix=".json")
        file_path = Path(result["file_path"])
        name = file_path.name

        # 匹配命名正则：prefix + 14位时间戳 + _ + 8位hex + suffix
        pattern = r"^httpapi_(\d{14})_([0-9a-f]{8})\.json$"
        m = re.match(pattern, name)
        assert m is not None, f"文件名不符合规范: {name}"

        # 时间戳部分应是合法的 YYYYMMDDHHmmss
        ts_str = m.group(1)
        ts = datetime.strptime(ts_str, "%Y%m%d%H%M%S")
        # 时间戳与当前时间差应在合理范围（60s 内，允许跨秒）
        assert abs((datetime.now() - ts).total_seconds()) < 60

    def test_custom_prefix_and_suffix(self, isolated_spill_dir):
        """自定义 prefix / suffix 生效"""
        from src.tools._spill import spill_large_content

        result = spill_large_content("abc", prefix="ocr_", suffix=".txt")
        name = Path(result["file_path"]).name
        assert name.startswith("ocr_")
        assert name.endswith(".txt")

    def test_full_size_reflects_char_count(self, isolated_spill_dir):
        """full_size 反映字符数（非字节）"""
        from src.tools._spill import spill_large_content

        # 含中文：3 个中文字符 = 3 字符（非 9 字节）
        content = "你好世界" * 2000
        result = spill_large_content(content)
        assert result["full_size"] == len(content)
        # 落盘内容完整
        assert Path(result["file_path"]).read_text(encoding="utf-8") == content

    def test_meta_written_to_sidecar_json(self, isolated_spill_dir):
        """meta 提供时写入同名 .meta.json"""
        from src.tools._spill import spill_large_content

        meta = {"url": "https://example.com/api", "page": 5}
        result = spill_large_content("data" * 2000, meta=meta)

        file_path = Path(result["file_path"])
        meta_path = file_path.with_name(file_path.name + ".meta.json")

        assert meta_path.exists()
        loaded = json.loads(meta_path.read_text(encoding="utf-8"))
        assert loaded["url"] == meta["url"]
        assert loaded["page"] == meta["page"]

    def test_no_meta_file_when_meta_none(self, isolated_spill_dir):
        """meta 为空时不写 meta 文件"""
        from src.tools._spill import spill_large_content

        result = spill_large_content("data" * 2000, meta=None)
        file_path = Path(result["file_path"])
        meta_path = file_path.with_name(file_path.name + ".meta.json")
        assert not meta_path.exists()

    def test_spill_dir_created_if_not_exists(self, isolated_spill_dir):
        """spill 目录不存在时自动创建"""
        from src.tools._spill import spill_large_content

        # 隔离目录此时不应存在
        assert not isolated_spill_dir.exists()

        result = spill_large_content("data" * 2000)

        # 调用后目录存在
        assert isolated_spill_dir.exists()
        assert Path(result["file_path"]).exists()

    def test_empty_content(self, isolated_spill_dir):
        """空内容也能落盘（边界）"""
        from src.tools._spill import spill_large_content

        result = spill_large_content("")
        assert result["full_size"] == 0
        assert result["preview"] == ""
        assert result["truncated"] is False  # 空内容未截断
        assert Path(result["file_path"]).exists()
        assert Path(result["file_path"]).read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# cleanup_stale_spill_files
# ---------------------------------------------------------------------------

class TestCleanupStaleSpillFiles:
    """cleanup_stale_spill_files 清理逻辑测试"""

    def test_cleanup_deletes_old_files(self, isolated_spill_dir):
        """删除超过 max_age_hours 的文件"""
        from src.tools._spill import cleanup_stale_spill_files

        isolated_spill_dir.mkdir(parents=True, exist_ok=True)

        # 创建一个「旧」文件（mtime 设为 25 小时前）
        old_file = isolated_spill_dir / "old.txt"
        old_file.write_text("old", encoding="utf-8")
        old_ts = time.time() - 25 * 3600
        os.utime(old_file, (old_ts, old_ts))

        # 创建一个 meta 旧文件
        old_meta = isolated_spill_dir / "old.txt.meta.json"
        old_meta.write_text("{}", encoding="utf-8")
        os.utime(old_meta, (old_ts, old_ts))

        deleted = cleanup_stale_spill_files(max_age_hours=24)

        assert deleted == 2
        assert not old_file.exists()
        assert not old_meta.exists()

    def test_cleanup_keeps_new_files(self, isolated_spill_dir):
        """保留未过期的文件"""
        from src.tools._spill import cleanup_stale_spill_files

        isolated_spill_dir.mkdir(parents=True, exist_ok=True)

        # 新文件（当前时间）
        new_file = isolated_spill_dir / "new.txt"
        new_file.write_text("new", encoding="utf-8")

        deleted = cleanup_stale_spill_files(max_age_hours=24)

        assert deleted == 0
        assert new_file.exists()

    def test_cleanup_respects_max_age_boundary(self, isolated_spill_dir):
        """边界：恰好 24 小时（=max_age）的文件被删，略小于则保留"""
        from src.tools._spill import cleanup_stale_spill_files

        isolated_spill_dir.mkdir(parents=True, exist_ok=True)

        # 恰好 24 小时（> 阈值触发删除，因为用 > 比较）
        boundary_file = isolated_spill_dir / "boundary.txt"
        boundary_file.write_text("b", encoding="utf-8")
        boundary_ts = time.time() - 24 * 3600 - 1  # 略超过 24h
        os.utime(boundary_file, (boundary_ts, boundary_ts))

        # 23 小时（未超）
        fresh_file = isolated_spill_dir / "fresh.txt"
        fresh_file.write_text("f", encoding="utf-8")
        fresh_ts = time.time() - 23 * 3600
        os.utime(fresh_file, (fresh_ts, fresh_ts))

        deleted = cleanup_stale_spill_files(max_age_hours=24)

        assert deleted == 1
        assert not boundary_file.exists()
        assert fresh_file.exists()

    def test_cleanup_returns_count(self, isolated_spill_dir):
        """返回删除数正确"""
        from src.tools._spill import cleanup_stale_spill_files

        isolated_spill_dir.mkdir(parents=True, exist_ok=True)
        old_ts = time.time() - 100 * 3600

        for i in range(3):
            f = isolated_spill_dir / f"old_{i}.txt"
            f.write_text("x", encoding="utf-8")
            os.utime(f, (old_ts, old_ts))

        deleted = cleanup_stale_spill_files(max_age_hours=24)
        assert deleted == 3

    def test_cleanup_no_dir_returns_zero(self, tmp_path, monkeypatch):
        """spill 目录不存在时返回 0，不报错"""
        from src.tools._spill import cleanup_stale_spill_files

        # 指向一个不存在的临时根
        monkeypatch.setattr(
            "src.tools._spill.tempfile.gettempdir",
            lambda: str(tmp_path / "nonexistent_root"),
        )
        deleted = cleanup_stale_spill_files(max_age_hours=24)
        assert deleted == 0

    def test_cleanup_empty_dir_returns_zero(self, isolated_spill_dir):
        """空 spill 目录返回 0"""
        from src.tools._spill import cleanup_stale_spill_files

        isolated_spill_dir.mkdir(parents=True, exist_ok=True)
        deleted = cleanup_stale_spill_files(max_age_hours=24)
        assert deleted == 0

    def test_cleanup_skips_subdirectories(self, isolated_spill_dir):
        """不递归子目录（spill 目录结构是扁平的）"""
        from src.tools._spill import cleanup_stale_spill_files

        isolated_spill_dir.mkdir(parents=True, exist_ok=True)
        old_ts = time.time() - 100 * 3600

        # 子目录里的旧文件不应被删（只删顶层文件）
        sub = isolated_spill_dir / "subdir"
        sub.mkdir()
        sub_file = sub / "old.txt"
        sub_file.write_text("x", encoding="utf-8")
        os.utime(sub_file, (old_ts, old_ts))

        deleted = cleanup_stale_spill_files(max_age_hours=24)
        # 子目录本身是目录条目，is_file() 为 False 被跳过；sub_file 不在顶层
        assert deleted == 0
        assert sub_file.exists()
