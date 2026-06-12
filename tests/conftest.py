"""
根级测试配置和共享 fixtures

作用域说明：
- session: 昂贵资源，整个测试会话创建一次
- function: 每个测试函数独立使用，保证隔离
"""

import sys
import types
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

# ============================================================
# 跳过旧根级测试（tests/test_*.py）的收集
# 这些是早期手动运行的脚本式测试，依赖已废弃的旧工具 API，
# 不再维护。新测试统一放在 tests/unit/、tests/integration/、tests/e2e/。
# 参考 .claude/rules/testing.md
# ============================================================
collect_ignore_glob = ["test_*.py"]


# ============================================================
# 在导入 src 模块之前 mock 外部依赖，防止 master_agent 初始化失败
# ============================================================

# mock dashscope（embedding 模块需要）
dashscope_mock = types.ModuleType("dashscope")
dashscope_mock.TextEmbedding = MagicMock()
sys.modules["dashscope"] = dashscope_mock

# mock vector_db 包（防止 agent 初始化失败）
vector_db_pkg = types.ModuleType("src.knowledge.vector_db")
vector_db_pkg.__path__ = []  # 标记为包
vector_db_pkg.__file__ = "src/knowledge/vector_db/__init__.py"
vector_db_mod = types.ModuleType("src.knowledge.vector_db.vector_db")
vector_db_mod.VectorDBSQLite = MagicMock()
vector_db_mod.get_vector_db = MagicMock()
vector_db_pkg.vector_db = vector_db_mod
sys.modules["src.knowledge.vector_db"] = vector_db_pkg
sys.modules["src.knowledge.vector_db.vector_db"] = vector_db_mod

# 忽略旧的根目录测试文件（已迁移到 unit/integration/e2e 子目录）
collect_ignore = sorted(str(p) for p in Path(__file__).parent.glob("test_*.py"))

# 在导入 src 模块之前设置测试环境变量
os.environ.setdefault("LLM_PROVIDER", "qwen")
os.environ.setdefault("API_KEYS", "api-key-1,api-key-2")

# 尝试从 .env 文件加载 DATABASE_URL，如果不存在则使用 PostgreSQL 默认值
from pathlib import Path
from dotenv import load_dotenv
project_root = Path(__file__).parent.parent
env_path = project_root / ".env"
if env_path.exists():
    load_dotenv(env_path)

# 如果 .env 中没有 DATABASE_URL，使用默认值
os.environ.setdefault("DATABASE_URL", os.getenv("DATABASE_URL", "postgresql://aid_user:Aid_2026@192.168.195.89:5433/aid_work_agent"))


# ============================================================
# 临时目录 fixtures
# ============================================================


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    """创建临时 skills 目录，含示例 SKILL.md"""
    skill_dir = tmp_path / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: test-skill\n"
        "description: 测试技能\n"
        "version: 1.0.0\n"
        "---\n"
        "# 测试技能\n"
        "这是测试技能的正文内容。",
        encoding="utf-8",
    )
    return tmp_path / "skills"


@pytest.fixture
def plans_dir(tmp_path: Path) -> Path:
    """创建临时 plans 目录"""
    plans = tmp_path / "plans"
    plans.mkdir()
    return plans


# ============================================================
# Mock 工厂 fixtures
# ============================================================


@pytest.fixture
def mock_llm_gateway():
    """完全 mock 的 LLMGateway"""
    gateway = MagicMock()
    gateway.chat = AsyncMock(return_value={
        "content": "Mock response",
        "tool_calls": None,
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    })
    gateway.stream_chat = AsyncMock()
    gateway.stream_chat.return_value.__aiter__ = MagicMock(return_value=iter(["Mock", " chunk"]))
    gateway.chat_with_tools = AsyncMock(return_value={
        "content": None,
        "tool_calls": [],
        "finish_reason": "tool_calls",
    })
    gateway.get_model_name = MagicMock(return_value="test-model")
    gateway.get_provider_name = MagicMock(return_value="test-provider")
    gateway.key_pool_stats = MagicMock(return_value=[])
    return gateway


@pytest.fixture
def mock_llm_response():
    """工厂 fixture：快速构建 canned LLM 响应"""

    def _make(
        content: str = "Test response",
        tool_calls: Optional[list] = None,
        finish_reason: str = "stop",
    ) -> Dict[str, Any]:
        return {
            "content": content,
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }

    return _make


@pytest.fixture
def mock_tool():
    """工厂 fixture：创建 mock BaseTool 实例"""

    def _make(
        name: str = "mock_tool",
        description: str = "A mock tool",
        category: str = "general",
        execute_return: Optional[Dict[str, Any]] = None,
    ) -> MagicMock:
        tool = MagicMock()
        tool.name = name
        tool.description = description
        tool.category = category
        tool.display_name = name
        tool.execute = AsyncMock(return_value=execute_return or {"success": True})
        tool.validate_parameters = MagicMock(return_value=True)
        tool.get_missing_parameters = MagicMock(return_value=[])
        tool.to_tool_definition = MagicMock(return_value={
            "name": name,
            "description": description,
            "input_schema": {"type": "object", "properties": {}},
        })
        tool.get_display_name = MagicMock(return_value=name)
        return tool

    return _make


# ============================================================
# 真实实例 fixtures（无外部依赖）
# ============================================================


@pytest.fixture
def memory():
    """新鲜的 ShortTermMemory 实例"""
    from src.memory.short_term import ShortTermMemory

    return ShortTermMemory(max_messages=100, ttl=3600)


@pytest.fixture
def tool_registry():
    """空的 ToolRegistry 实例"""
    from src.tools.registry import ToolRegistry

    return ToolRegistry()


@pytest.fixture
def tool_executor(tool_registry):
    """ToolExecutor + 空 ToolRegistry"""
    from src.tools.executor import ToolExecutor

    return ToolExecutor(registry=tool_registry)


@pytest.fixture
def user_email_config():
    """测试用邮箱配置"""
    from src.models.user import UserEmail, EncryptionType

    return UserEmail(
        email_address="test@example.com",
        smtp_server="smtp.example.com",
        smtp_port=465,
        smtp_user="test@example.com",
        smtp_password="test_password",
        smtp_encryption=EncryptionType.SSL,
        imap_server="imap.example.com",
        imap_port=993,
        imap_encryption=EncryptionType.SSL,
    )
