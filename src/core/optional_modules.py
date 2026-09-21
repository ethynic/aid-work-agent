"""租户定制模块按需加载（平台通用机制，2026-09-21 用户决议）。

平台入口（main.py 路由块 / background_runner 调度块）**零租户专名**：只含通用
加载循环；加载哪些租户定制模块由 `configs/config.yaml` 的 `tenant_custom_modules`
清单决定——未列出的部署完全不 import 定制代码，列出但模块缺失仅告警不阻断启动。

约定：清单中每个名字 `<name>` 对应 `src/tenant_custom/<name>/`——
- 有 `api.py` 且暴露 `router` → 其 APIRouter 被挂载；
- 有 `scheduler.py` 且暴露 `create_scheduler()` → 调度器实例被启动。

配置示例（configs/config.yaml）：

    tenant_custom_modules: ["hongtao_shop"]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, List

import yaml
from loguru import logger

CONFIG_KEY = "tenant_custom_modules"
_BASE_PACKAGE = "src.tenant_custom"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _enabled_module_names() -> List[str]:
    """读 configs/config.yaml 的启用清单（缺 key/文件/非法值 → 空清单不加载）。"""
    try:
        path = _PROJECT_ROOT / "configs" / "config.yaml"
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001 配置读失败按未启用处理（不阻断启动）
        logger.warning("optional_modules 配置读取失败（按未启用处理）: {}", type(e).__name__)
        return []
    names = data.get(CONFIG_KEY)
    if not isinstance(names, list):
        return []
    return [str(n).strip() for n in names if str(n or "").strip()]


def _import_module_attr(name: str, submodule: str, attr: str) -> Any:
    """动态导入定制子模块属性；模块/属性缺失返回 None（仅告警）。"""
    import importlib

    full = f"{_BASE_PACKAGE}.{name}.{submodule}"
    try:
        mod = importlib.import_module(full)
    except ImportError as e:
        # 区分「清单配了但模块不存在」（自身缺失，WARNING）与「模块内部依赖拼错」
        # （传递性 ImportError，ERROR——静默吞掉会让路由无解释缺席）
        missing = str(getattr(e, "name", "") or "")
        is_target_missing = (
            missing == full or missing.startswith(full + ".")
            or missing == _BASE_PACKAGE or missing.startswith(_BASE_PACKAGE + ".")
        )
        if is_target_missing:
            logger.warning("optional_modules：清单配置了 {} 但 {} 不存在（跳过）", name, full)
        else:
            logger.opt(exception=True).error(
                "optional_modules：{} 依赖加载失败（{} 缺失，跳过）", full, missing or "未知模块"
            )
        return None
    except Exception as e:  # noqa: BLE001 模块自身加载失败不阻断应用启动
        logger.opt(exception=True).error("optional_modules：{} 加载失败（跳过）: {}", full, e)
        return None
    value = getattr(mod, attr, None)
    if value is None:
        logger.info("optional_modules：{} 无属性 {}（跳过）", full, attr)
    return value


def load_optional_routers() -> List[Any]:
    """加载启用模块的 APIRouter 列表（供 main.py 通用挂载循环）。"""
    routers = []
    for name in _enabled_module_names():
        router = _import_module_attr(name, "api", "router")
        if router is not None:
            routers.append(router)
            logger.info("optional_modules：已加载租户定制路由 {}", name)
    return routers


def load_optional_scheduler_factories() -> List[Callable[[], Any]]:
    """加载启用模块的调度器工厂列表（供 background_runner 通用注册循环）。

    约定：模块 scheduler.py 暴露无参 `create_scheduler()` 返回带 start()/stop()
    的实例。
    """
    factories = []
    for name in _enabled_module_names():
        factory = _import_module_attr(name, "scheduler", "create_scheduler")
        if factory is not None and callable(factory):
            factories.append(factory)
            logger.info("optional_modules：已加载租户定制调度器 {}", name)
    return factories
