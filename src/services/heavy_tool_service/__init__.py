"""重工具服务（B1，2026-11-13 设计 §2/§4）：重工具剥离 LLM 后放入独立容器。

- 轻容器（runner worker）经 HeavyToolClient 同步长调用重容器；
- 重容器为纯计算服务：无 LLM gateway、无 DB 连接、无租户模型配置（§2）；
- 文件经共享 storage 卷以 workspace 绝对路径交接，不走 HTTP 内容（§4.1）；
- 模式开关 HEAVY_TOOLS_MODE（local 默认 / remote），零代码回滚（§4.2）。

凭证例外登记：重容器注入 PADDLEOCR_* 服务级凭据（paddleocr 工具整体远程后
在重容器调用 PaddleOCR 云 API 所需，属服务自身的外呼凭据，非租户/LLM 凭证），
见 docker-compose.agent-runner.yml heavy-tools 服务注释。

模块与入口：
- config：环境变量读取（mode/url/token/并发/storage 根）
- client：轻容器侧代理调用（仅连接阶段失败重试一次；busy 透传不重试，§8）
- server：重容器 FastAPI 面（认证 / 路径 allowlist / 信号量 busy / job 登记）
- handlers：远程 handler 实现（excel handlers / paddleocr / upload 解析）
"""

from .config import heavy_tools_mode, heavy_tools_url


__all__ = ["heavy_tools_mode", "heavy_tools_url"]
