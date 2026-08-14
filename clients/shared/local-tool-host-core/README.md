# Local Tool Host Core

Desktop Host 与 headless Runtime 未来共用的无 UI、无 Electron 执行核心边界。Phase B 只定义 invocation/生命周期端口，不抽取现有 Runtime 实现。

禁止反向依赖 `agent-coordinator-core`；凭证后端、进程监管和平台实现必须由 Host 壳注入。
