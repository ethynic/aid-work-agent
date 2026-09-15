"""受信清单三方一致性（P5 R59①：catalog.py vs Runtime providers.ts vs 安装文档）

三方锚点：
- src/local_tools/catalog.py TRUSTED_PROVIDERS['weixin'].tools（服务端受信清单，import 读取）
- clients/agent-tool-runtime/src/providers.ts WEIXIN_TOOLS（Runtime manifest，读文件
  正则解析——不 import TS，避免构建依赖）
- clients/README.md 微信 Provider 配置段（安装手册清单，文档内工具名逐一出现）

断言：catalog weixin 工具集 ⊆ Runtime providers.ts weixin tools（服务端批准的
每个设备侧工具名必须在 Runtime manifest 白名单内，否则设备侧 TOOL_NOT_ALLOWED
与服务端放行不一致）；provider_id 两侧一致；安装文档覆盖 catalog weixin 全部
工具名与启用前提。boss 侧不做 ⊆ 断言（catalog 含 boss_interview_notify/
boss_jobs_list 两个云端代理工具，注释已声明不进 Runtime manifestVerifier）。
"""

import re
from pathlib import Path

import pytest

from src.local_tools.catalog import TRUSTED_PROVIDERS

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).parents[3]
_PROVIDERS_TS = _REPO_ROOT / "clients" / "agent-tool-runtime" / "src" / "providers.ts"
_INSTALL_README = _REPO_ROOT / "clients" / "README.md"


def _ts_tool_list(source: str, const_name: str) -> set:
    """解析 providers.ts 的 `const NAME = [ 'a', 'b', ... ] as const` 工具集"""
    match = re.search(
        rf"const {const_name}\s*=\s*\[(.*?)\]\s*as\s*const", source, re.DOTALL
    )
    assert match, f"providers.ts 未找到 {const_name}"
    return set(re.findall(r"'([^']+)'", match.group(1)))


@pytest.fixture(scope="module")
def providers_ts() -> str:
    assert _PROVIDERS_TS.is_file(), f"缺少 Runtime providers.ts: {_PROVIDERS_TS}"
    return _PROVIDERS_TS.read_text(encoding="utf-8")


class TestWeixinManifestConsistency:
    def test_catalog_weixin_tools_subset_of_runtime_manifest(self, providers_ts):
        """catalog weixin **设备侧 v1 工具** ⊆ Runtime WEIXIN_TOOLS（服务端放行的
        只读工具必须在 Runtime manifest 白名单内）。

        weixin_message_send_v2 是底座 v2 统一操作名，**有意不在** v1 manifest——
        v2 invocation 走 protocol_version/shared_lock_capable 门禁
        （PROTOCOL_NOT_SUPPORTED），混入 v1 工具白名单反而会让 v2 降级旧链路。"""
        runtime_weixin = _ts_tool_list(providers_ts, "WEIXIN_TOOLS")
        catalog_weixin = set(TRUSTED_PROVIDERS["weixin"]["tools"])
        v2_operation = "weixin_message_send_v2"
        assert v2_operation in catalog_weixin  # v2 操作名在服务端受信清单
        device_tools = catalog_weixin - {v2_operation}
        missing = device_tools - runtime_weixin
        assert not missing, f"catalog weixin 设备工具不在 Runtime manifest: {missing}"
        # v2 操作名不得混入 v1 manifest（保持 v2 门禁语义，P0 交付前拒绝不降级）
        assert v2_operation not in runtime_weixin

    def test_weixin_provider_id_identical(self, providers_ts):
        """provider_id 两侧一致（ai.aidwork.weixin）"""
        assert TRUSTED_PROVIDERS["weixin"]["provider_id"] == "ai.aidwork.weixin"
        assert "'ai.aidwork.weixin'" in providers_ts

    def test_weixin_provider_key_registered_in_runtime(self, providers_ts):
        """Runtime TRUSTED_MANIFESTS 含 weixin 条目（key 一致）"""
        assert re.search(r"^\s{2}weixin:\s*{", providers_ts, re.MULTILINE)

    def test_install_doc_covers_weixin_tools_and_prerequisites(self):
        """安装文档多 Provider 段覆盖 catalog weixin 全部工具名 + 启用前提三要素
        （安装 entry 配置 / 受信清单 / capability 上报）"""
        assert _INSTALL_README.is_file()
        doc = _INSTALL_README.read_text(encoding="utf-8")
        catalog_weixin = set(TRUSTED_PROVIDERS["weixin"]["tools"])
        missing = [tool for tool in sorted(catalog_weixin) if tool not in doc]
        assert not missing, f"安装手册缺少 weixin 工具说明: {missing}"
        # providers 配置入口与启用前提（人工核对语义由文档段落承载，测试锚定关键词）
        for keyword in ("providers", "bossCliEntry", "weixin"):
            assert keyword in doc, f"安装手册缺少 {keyword} 配置说明"

    def test_runtime_boss_tools_cover_catalog_device_tools(self, providers_ts):
        """boss 侧设备执行工具（排除 catalog 注释声明的两个云端代理工具）⊆
        Runtime BOSS_TOOLS——历史兼容锚，防 Runtime 清单误删设备工具"""
        runtime_boss = _ts_tool_list(providers_ts, "BOSS_TOOLS")
        cloud_only = {"boss_interview_notify", "boss_jobs_list"}
        catalog_boss = set(TRUSTED_PROVIDERS["boss-recruiting"]["tools"]) - cloud_only
        missing = catalog_boss - runtime_boss
        assert not missing, f"catalog boss 设备工具不在 Runtime manifest: {missing}"
