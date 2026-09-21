"""租户定制插件目录（tenant-custom plugins）。

存放**为特定租户定制、其他租户不可复用**的专有模块（如 hongtao_shop：
宏陶商城产品知识库同步）。与平台通用代码的边界：

- 平台入口不静态 import 本目录：main.py 路由块与 background_runner 调度块均为
  通用按需加载循环（src/core/optional_modules.py），由 configs/config.yaml 的
  tenant_custom_modules 清单控制加载哪些模块——未配置的部署完全不加载定制代码。
- 模块对通用层只**调用**通用函数/通用表（如 content_sync 五表、计费链路），
  不改通用服务代码；模块约定：api.py 暴露 router、scheduler.py 暴露
  create_scheduler()（供按需加载器发现）。
- 平台级功能（多租户复用）放 src/ 对应功能目录，不进本目录。
- 新增租户定制模块时在设计文档声明「租户专属」并在本目录建子包。
"""
