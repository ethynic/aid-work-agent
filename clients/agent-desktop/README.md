# Agent Desktop Windows 客户端

本目录是 Windows Electron 客户端。renderer 直接使用 `frontend/dist-desktop` 的 Agent/租户构建，不包含平台 `/portal`、Python 服务或 browser runtime；凭证使用 safeStorage，正式签名包支持受控自动更新。

## Windows 开发运行

Node.js 版本需与项目 CI 保持一致。首次运行先安装两个工程的依赖：

```powershell
cd C:\repos\aid-work-agent\frontend
npm ci
cd ..\clients\agent-desktop
npm ci
```

配置真实服务端地址后启动。生产地址必须为 HTTPS；仅本机开发允许 `localhost`、`127.0.0.1` 或 `[::1]` 的 HTTP。环境变量是开发/运维显式覆盖：

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
$env:AID_AGENT_PACKAGE_API_BASE_URL='https://agent2.aidingyi.cn/api'
npm run package:win:dev
```

该命令要求显式提供打包 API 地址，先执行 typecheck、测试、Desktop build 和 Portal artifact 门禁，再把仅含 `schemaVersion` 和 `apiBaseUrl` 的默认配置写入安装包并生成 Windows x64 NSIS 开发安装包。URL 禁止包含 Token、账号或密码。输出位于 `release/`，文件名和 `release-manifest.json` 会明确标记 `dev/unsigned`；同时生成 CycloneDX SBOM、npm audit JSON 和许可证清单。`release/` 已加入 gitignore，不会提交。

安装后配置文件位于 `%APPDATA%\aid-agent-desktop\desktop-config.json`，首次启动由包内默认值原子初始化，升级不会覆盖。关闭客户端后可编辑：

```json
{
  "schemaVersion": 1,
  "apiBaseUrl": "https://agent2.aidingyi.cn/api"
}
```

加载优先级为 `AID_AGENT_API_BASE_URL`（运维覆盖）→ 用户配置 → 包内默认配置。非法或缺失配置会明确终止启动，不会静默连接其他服务。

生产打包使用 `npm run package:win:release`，并必须同时提供 `AID_AGENT_UPDATE_BASE_URL`。该模式没有 `CSC_LINK`/`WIN_CSC_LINK`、缺少 HTTPS 更新源或签名结果不是 `Valid` 都会立即失败，不允许静默生成 unsigned release。构建生成的 `latest.yml`、签名安装包和 `.blockmap` 必须作为同一发布单元原子上传；构建脚本本身不执行上传。

关闭最后一个窗口会在 Windows 退出应用；再次启动第二实例只会恢复并聚焦已有窗口。窗口位置、尺寸和最大化状态保存在 Electron userData 目录，不保存 Token 或页面内容。

## 安全边界

- `nodeIntegration=false`、`contextIsolation=true`、`sandbox=true`、`webSecurity=true`。
- 默认拒绝新窗口、外部导航、WebView 和全部权限请求。
- preload 只暴露冻结且版本化的只读 runtime，不暴露 `ipcRenderer`。
- CSP 的网络访问仅允许配置的精确 API origin，不允许宽泛 `https:`。
- `/portal` 在 frontend artifact、scheme 路由和导航层均拒绝。

## 尚未覆盖

- macOS 本 Phase 尚未验证，按计划在 Mac 设备单独补验。
- 自动更新代码与左下角交互已实现；正式代码签名证书、真实更新源、灰度和回滚仍需发布环境配置与真机验收。开发包允许 unsigned，但固定禁用真实更新且不可对外发布。
- browser runtime 不属于 Phase 2，未接入且不影响 Agent 主链路。
