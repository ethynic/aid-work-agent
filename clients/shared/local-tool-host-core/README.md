# Local Tool Host Core

普通 Node 的执行核心，已从 `agent-tool-runtime` 提取配置、凭证、Device API、心跳/领取、执行器、Provider、桌面锁和原结果 outbox。Runtime 原模块路径保留兼容导出；不依赖 Electron、UI、Coordinator 或第一方业务包。

禁止反向依赖 `agent-coordinator-core`；凭证后端、进程监管和平台实现必须由 Host 壳注入。

`npm run build` 生成 `dist/index.js`/`.d.ts` 及 `legacy/*` exports。`RuntimeHost.open` 接收可信 `home/version/supervisor/adapter`，先取得规范化 home/用户的 OS lease，再初始化平台适配与 DPAPI 管理 secret；管理调用采用 H1 候选0.3，不能用 IPC 联通代替设备就绪。`request`/`observe` 不提供任意文件或业务执行入口。`invoke` 类型是内部执行端口，尚未接入新 H2 binding。

`agent-tool-runtime/src/runtimeHost.ts` 组合原普通调用、sessionTasks/nameBridge 和 Windows DPAPI；CLI 与受管入口共用该组合。只允许一个组合实例占用一个 Node 进程。停止关闭领取/观察/新决策，等待已接受工作、结果回传和 Provider 实际退出，无观察超时强杀。未知启动/停止和崩溃 marker 保持 reconciling，不自动重放业务或删除事实。

H3 接收资产：在 `clients/agent-tool-runtime` 目录运行 `npm pack`，tgz 包含 `dist/src/**` 与 bundled core/SDK/zod 运行依赖，无 BOSS 和 Runtime 测试。公共构建把 `dist/src/**` 完整复制到 `resources/runtime/host/`，保留 `sessionTasks/` 相对结构、ESM package 元数据及相邻 `node_modules`；入口为 `managed-entry.js`。Main 用固定 Node 22.23.3、私有继承 IPC、可信 home 启动；可传 `--supervisor=desktop`，缺省 `runtime_app`。`process.send` 传 H1 响应/事件；parent disconnect 进入相同 drain，成功释放 lease 后 child 实际退出。不能直接用 Electron exe 启动 Provider。

A1 限制：第一方 plugins mutation 尚返回 FEATURE_UNSUPPORTED，第三方 B1/B2 暂不开发；旧 journal/session 缺可信结案证明时拒绝配对/解绑而保留原身份下恢复。标准 Windows Node 旧 CLI 入口可识别并拒绝并行；改名或自定义包装须 H3/迁移核验，不能承诺任意旧进程识别。正式 Node/H3 安装包、签名信任根与三 CLI 实包尚未验收，H1 wire 未冻结。进度与验证见 [开发计划](../../../docs/plans/plan-runtime-plugin-host.md)。
