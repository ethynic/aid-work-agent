# Agent Desktop Windows 客户端

本目录是 Windows Electron 基础壳。renderer 直接使用 `frontend/dist-desktop` 的 Agent/租户构建，不包含平台 `/portal`、Python 服务、safeStorage、自动更新或 browser runtime。

## Windows 开发运行

Node.js 版本需与项目 CI 保持一致。首次运行先安装两个工程的依赖：

```powershell
cd C:\repos\aid-work-agent\frontend
npm ci
cd ..\clients\agent-desktop
npm ci
```

配置真实服务端地址后启动。生产地址必须为 HTTPS；仅本机开发允许 `localhost`、`127.0.0.1` 或 `[::1]` 的 HTTP：

```powershell
$env:AID_AGENT_API_BASE_URL='https://agent-api.example.com/api'
npm start
```

FastAPI CORS 必须精确允许 `aidagent://app`，不能使用通配 origin。API 不可达时客户端仍加载本地 Agent 页面，并显示可重试的离线提示。

## Windows 验证

```powershell
npm run typecheck
npm test
npm run build
npm run smoke
```

- `build` 会先执行 frontend Desktop build 与 Portal artifact 门禁，再复制真实 renderer。
- `test` 覆盖 scheme/Portal/traversal、安全配置、精确 CSP、窗口边界、缩放、第二实例聚焦和真实 renderer 接入。
- `smoke` 启动隐藏 Electron 窗口与本机 fixture，验证真实 Agent renderer、`aidagent://app` history、精确 CORS、流式 Fetch、FormData、下载和 preload runtime。

## Windows 开发安装包

```powershell
npm run package:win:dev
```

该命令先执行 typecheck、测试、Desktop build 和 Portal artifact 门禁，再生成 Windows x64 NSIS 开发安装包。输出位于 `release/`，文件名和 `release-manifest.json` 会明确标记 `dev/unsigned`；同时生成 CycloneDX SBOM、npm audit JSON 和许可证清单。`release/` 已加入 gitignore，不会提交。

生产打包使用 `npm run package:win:release`。该模式没有 `CSC_LINK`/`WIN_CSC_LINK` 会立即失败，签名结果不是 `Valid` 也会失败，不允许静默生成 unsigned release。本阶段不上传发布源，也不配置或连接自动更新 URL。

关闭最后一个窗口会在 Windows 退出应用；再次启动第二实例只会恢复并聚焦已有窗口。窗口位置、尺寸和最大化状态保存在 Electron userData 目录，不保存 Token 或页面内容。

## 安全边界

- `nodeIntegration=false`、`contextIsolation=true`、`sandbox=true`、`webSecurity=true`。
- 默认拒绝新窗口、外部导航、WebView 和全部权限请求。
- preload 只暴露冻结且版本化的只读 runtime，不暴露 `ipcRenderer`。
- CSP 的网络访问仅允许配置的精确 API origin，不允许宽泛 `https:`。
- `/portal` 在 frontend artifact、scheme 路由和导航层均拒绝。

## 尚未覆盖

- macOS 本 Phase 尚未验证，按计划在 Mac 设备单独补验。
- 正式代码签名证书、真实更新源、灰度和回滚仍需发布环境配置；开发包允许 unsigned，但不可对外发布。
- browser runtime 不属于 Phase 2，未接入且不影响 Agent 主链路。
