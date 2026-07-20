# Windows 桌面客户端编译与打包手册

本文适用于提交 `60ed0d7adb1e8cd4708c07292efef1d5164c2ca1` 引入的 Electron 桌面客户端，也适用于当前 `master` 中相同的构建链路。第 3～10 节的逐步命令可用于该提交；第 11 节的一键脚本是后续新增能力，仅在包含 `scripts/build-win-dev.ps1` 的当前代码中可用。客户端仅支持 Windows x64；开发包未签名，只能用于内部验证，不能作为正式发行包。

## 1. 构建环境

- Windows 10/11 x64。
- Git。
- Node.js `>= 22.12.0`；推荐使用当前 Node.js 22 LTS。当前锁定依赖中有多个包要求至少 Node.js 22.12；Node.js 18 还会因无法加载 `electron-builder` 依赖的 ESM 模块而报 `ERR_REQUIRE_ESM`。
- 可访问 npm 依赖源和 `https://registry.npmjs.org`。打包末尾会从官方源执行 `npm audit`，网络不可达会导致打包失败。
- 生产包还需要有效的 Windows 代码签名证书；内部开发包不需要证书。

以下命令均在 PowerShell 中执行。先确认版本：

```powershell
node --version
npm --version
git --version
```

使用 nvm-windows 时可安装并切换 Node.js 22：

```powershell
nvm install 22
nvm use 22
node --version
```

## 2. 获取指定源码

如果只需编译该提交，可在独立目录或独立分支执行：

```powershell
cd C:\repos\aid-work-agent
git status --short
git switch --detach 60ed0d7adb1e8cd4708c07292efef1d5164c2ca1
```

`git status --short` 有未提交改动时不要切换，先自行保存改动。若要编译当前最新版，不切换提交，留在目标分支即可。

## 3. 安装依赖

桌面壳和 renderer 是两个 npm 工程，两边都必须安装锁定依赖：

```powershell
cd C:\repos\aid-work-agent\frontend
npm ci

cd C:\repos\aid-work-agent\clients\agent-desktop
npm ci
```

不要用 `npm install` 代替 `npm ci`，否则可能改写 lock 文件并引入不可复现的依赖版本。

### 国内网络镜像加速

Electron 打包时还会下载 Electron 主程序以及 NSIS、Windows 签名辅助工具等 electron-builder 二进制文件。国内网络访问默认下载源较慢或超时时，可在执行底层打包命令或一键脚本前，在当前 PowerShell 会话设置镜像：

```powershell
$env:ELECTRON_MIRROR='https://npmmirror.com/mirrors/electron/'
$env:ELECTRON_BUILDER_BINARIES_MIRROR='https://npmmirror.com/mirrors/electron-builder-binaries/'
```

- `ELECTRON_MIRROR` 用于下载 Electron 主程序。
- `ELECTRON_BUILDER_BINARIES_MIRROR` 用于下载 NSIS 等 electron-builder 工具。
- 变量只对当前 PowerShell 进程及其子进程生效，不会写入仓库，也不会固化进安装包。
- `node scripts/package-win.mjs dev` 和第 11 节的一键脚本都会继承这两个变量。

当前项目使用 Electron `43.1.0`，可先检查镜像文件是否能访问：

```powershell
Invoke-WebRequest `
  -Method Head `
  -Uri 'https://npmmirror.com/mirrors/electron/v43.1.0/electron-v43.1.0-win32-x64.zip'
```

若之前的下载已超时，重试仍然异常，可清理 Electron 下载缓存后重新执行打包：

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\electron\Cache" -ErrorAction SilentlyContinue
```

不要把镜像地址误写到 `AID_AGENT_API_BASE_URL`；镜像变量只影响构建期依赖下载，agent2 地址是客户端运行期 API 配置。

## 4. 配置 agent2 后台地址

桌面端的完整 API 基址应为：

```text
https://agent2.aidingyi.cn/api
```

必须包含 `/api`。只配置 `https://agent2.aidingyi.cn` 会使前端请求落到错误路径。

该地址不是写入前端 `.env`。打包时必须显式传入，安装包只保存 API 基址，不保存 Token、账号、密码或其他敏感凭证：

```powershell
$env:AID_AGENT_PACKAGE_API_BASE_URL='https://agent2.aidingyi.cn/api'
```

安装后首次启动会把包内默认值原子写入以下用户配置文件，升级不会覆盖，关闭客户端后可直接编辑：

```text
%APPDATA%\aid-agent-desktop\desktop-config.json
```

```json
{
  "schemaVersion": 1,
  "apiBaseUrl": "https://agent2.aidingyi.cn/api"
}
```

配置优先级固定为：`AID_AGENT_API_BASE_URL` 环境变量（仅供开发或运维显式覆盖）→ 用户配置文件 → 安装包内默认配置。环境变量和配置文件都会执行相同的 HTTPS、凭证、query/fragment 校验；选中的配置非法时客户端明确退出，不会静默降级到其他地址。

如需撤销当前用户配置：

```powershell
[Environment]::SetEnvironmentVariable('AID_AGENT_API_BASE_URL', $null, 'User')
```

### 服务端 CORS 配置

桌面 renderer 的 Origin 是 `aidagent://app`。agent2 后台必须精确允许该 Origin。服务端的 `CORS_ORIGINS` 环境变量会替换默认列表，因此应在保留现有 Web 来源的基础上追加，例如：

```text
CORS_ORIGINS=https://agent2.aidingyi.cn,aidagent://app
```

该配置应写入 agent2 服务器部署目录的环境文件；当前部署目录通常是 `/var/www/agent2`，因此通常编辑 `/var/www/agent2/.env`。如果实际部署通过其他环境文件或平台注入变量，应修改对应的真实来源，避免在仓库内提交生产 `.env`。

若 agent2 还服务其他前端域名，也要逐项保留并用英文逗号分隔。修改服务端环境变量后必须重新创建 API 容器以重新注入环境变量，仅执行 `docker compose restart` 不够。当前 agent2 更新脚本会照常编译 `frontend/dist` 并重建容器；增加 `aidagent://app` 只是追加桌面端允许来源，不改变 Web 前端构建产物和访问方式，原 Web 端仍可直接使用。不要使用 `*`，也不要把 `https://*.aidingyi.cn` 当作 `aidagent://app` 的替代项。

手工重新创建容器的示例：

```bash
cd /var/www/agent2
docker compose -f docker-compose.test.yml up -d --force-recreate
```

## 5. 开发运行与验证

进入桌面工程：

```powershell
cd C:\repos\aid-work-agent\clients\agent-desktop
$env:AID_AGENT_API_BASE_URL='https://agent2.aidingyi.cn/api'
```

按以下步骤逐项验证。类型检查只检查 TypeScript 类型，不生成文件：

```powershell
npm run typecheck
```

构建 renderer、Electron 主进程和测试代码，并执行桌面内容门禁：

```powershell
npm run build
```

在 Windows PowerShell 中显式枚举编译后的测试文件再运行测试：

```powershell
$tests = @(Get-ChildItem .\dist\tests\*.test.js -File | ForEach-Object FullName)
if ($tests.Count -eq 0) { throw '未找到已编译的测试文件' }
node --test $tests
```

不要使用 `node --test dist/tests/*.test.js`，Windows 不会为 Node 展开通配符；也不要使用 `node --test dist/tests`，Node.js 22 会把该目录当作模块入口。

可选 smoke 检查会再次执行构建，适合开发验收，但不是打包前的必需步骤：

```powershell
npm run smoke
```

再以开发方式打开桌面客户端：

```powershell
npm start
```

`npm start` 会重新构建 renderer 和 Electron 主进程。能打开本地 Agent 页面、登录并访问 agent2 API，说明客户端地址和服务端 CORS 均已生效。

## 6. 生成内部开发安装包

上述检查通过后，可直接调用底层打包器生成未签名开发包。此命令不会重复类型检查、构建和测试：

```powershell
cd C:\repos\aid-work-agent\clients\agent-desktop
$env:AID_AGENT_PACKAGE_API_BASE_URL='https://agent2.aidingyi.cn/api'
node .\scripts\package-win.mjs dev
```

底层打包器会执行 NSIS 打包、ASAR 校验、SBOM、许可证清单和 npm audit。成功后输出目录为：

```text
C:\repos\aid-work-agent\clients\agent-desktop\release\
```

主要产物包括：

- `AID-Work-Agent-<版本>-win-x64-dev-unsigned.exe`：Windows x64 用户级 NSIS 安装包。
- `release-manifest.json`：版本、提交、dirty 状态、文件大小和 SHA-256。
- `sbom.cdx.json`：CycloneDX SBOM。
- `npm-audit.json`：依赖漏洞报告。
- `licenses.json`：依赖许可证清单。
- `win-unpacked\`：未封装程序目录，仅用于检查和测试。

再次校验现有开发包：

```powershell
node .\scripts\verify-package.mjs release dev
Get-AuthenticodeSignature .\release\AID-Work-Agent-*-dev-unsigned.exe
Get-FileHash .\release\AID-Work-Agent-*-dev-unsigned.exe -Algorithm SHA256
```

开发包的签名状态应为 `NotSigned`，Windows SmartScreen 可能告警，这是预期行为。

## 7. 生成正式签名安装包

正式包禁止未签名构建。先把 `package.json` 中的 `version` 调整为待发布版本，并配置 electron-builder 支持的证书变量。以下以受密码保护的 PFX 为例：

```powershell
cd C:\repos\aid-work-agent\clients\agent-desktop
$env:AID_AGENT_PACKAGE_API_BASE_URL='https://agent2.aidingyi.cn/api'
$env:AID_AGENT_UPDATE_BASE_URL='https://downloads.example.com/aid-agent/windows/x64'
$env:CSC_LINK='C:\secure\codesign.pfx'
$env:CSC_KEY_PASSWORD='<从安全渠道取得的证书密码>'
npm run package:win:release
```

不要把证书、密码或包含凭证的 `.env` 文件提交到仓库。更新地址必须是无账号密码、query、fragment 的 HTTPS 目录。正式命令要求安装包 Authenticode 状态为 `Valid`，并核对 `app-update.yml` 的 Generic Provider、`publisherName` 以及 `latest.yml` 中的版本、文件名、大小和 SHA-512；否则打包失败。

成功后应把下列同一次构建产物原子发布到 `AID_AGENT_UPDATE_BASE_URL` 对应目录：

- `latest.yml`
- `AID-Work-Agent-<版本>-win-x64.exe`
- `AID-Work-Agent-<版本>-win-x64.exe.blockmap`

客户端启动约 30 秒后检查更新，此后每 6 小时检查一次。发现新版本时左下角显示更新提示；用户点击后下载，下载完成后再次确认重启安装。每次发布必须递增 `package.json` 的 SemVer，同版本重新打包不会触发更新。

## 8. 安装后验证

1. 确认安装包使用正确 API 基址构建；需要变更时编辑 `%APPDATA%\aid-agent-desktop\desktop-config.json`。
2. 运行安装包，选择当前用户安装目录。
3. 从桌面快捷方式启动 `AID Work Agent`。
4. 完成登录，检查会话列表、聊天发送、流式响应、上传和下载。
5. 在浏览器或 PowerShell 确认 `https://agent2.aidingyi.cn/health` 可访问。
6. 若客户端打开后 API 全部失败，优先检查服务端响应是否包含 `Access-Control-Allow-Origin: aidagent://app`。

可用下面的预检请求检查 CORS；响应应包含精确的 `Access-Control-Allow-Origin: aidagent://app`：

```powershell
$headers = @{
  Origin = 'aidagent://app'
  'Access-Control-Request-Method' = 'GET'
}
Invoke-WebRequest `
  -Method Options `
  -Uri 'https://agent2.aidingyi.cn/api/sessions' `
  -Headers $headers
```

## 9. 常见问题

| 现象 | 原因与处理 |
|---|---|
| 启动后立即退出 | 用户配置或包内配置缺失/非法。检查 `%APPDATA%\aid-agent-desktop\desktop-config.json` 是否符合 schema 且使用 HTTPS。 |
| 登录等请求返回 404 | API 基址漏写 `/api`。 |
| 页面能打开但所有 API 报 CORS | agent2 未精确允许 `aidagent://app`。修正服务端 `CORS_ORIGINS` 后用 `docker compose ... up -d --force-recreate` 重新创建 API 容器。 |
| `npm ci` 失败 | Node/npm 版本或依赖源不一致；先对齐 CI 版本并检查网络。 |
| Windows 下提示找不到 `dist/tests/*.test.js` | Windows 不展开 Node 命令中的通配符。先执行 `npm run build`，再用本手册第 5 节的 PowerShell 命令枚举测试文件。不要改用 `node --test dist/tests`。 |
| `ERR_REQUIRE_ESM` 指向 `@noble/hashes/blake2.js` | Node.js 版本过低。切换到 Node.js 22，删除桌面端和前端的 `node_modules` 后分别重新执行 `npm ci`。 |
| 下载 `electron-v*-win32-x64.zip` 时出现 `Timeout awaiting 'request'` | Electron 下载源访问超时。按第 3 节设置 `ELECTRON_MIRROR` 和 `ELECTRON_BUILDER_BINARIES_MIRROR`，必要时清理 Electron Cache 后重试。 |
| `npm run package:win:dev` 在 audit 阶段失败 | 无法访问 npm 官方安全审计接口，恢复网络后重新执行标准打包命令。 |
| release 模式提示缺少 `CSC_LINK` | 正式包强制签名；配置证书和密码，不能绕过。 |
| release 模式提示更新配置或 metadata 失败 | 配置合法的 `AID_AGENT_UPDATE_BASE_URL`，并确认 `latest.yml`、EXE、`.blockmap` 来自同一次构建；不要手工改写 metadata。 |
| SmartScreen 警告 | 开发包未签名的正常现象；外部分发必须使用有效证书生成正式包。 |

## 10. 当前限制

- 后台 API 基址会写入安装包默认配置；它不是敏感凭证。Token 等敏感信息仍不得进入打包参数或配置文件。
- 当前仅验证 Windows x64，macOS 尚未完成验证。
- 自动更新客户端代码已实现，但当前没有生产更新源、签名证书、灰度发布和自动回滚的真机验收；unsigned 开发包固定禁用真实更新。
- 正式图标、发行证书和发布流程仍需发行环境提供。

## 11. 使用一键脚本完成开发包构建

完成前述环境和服务端配置后，日常构建无需手动重复每个步骤。国内网络环境建议先按第 3 节设置两个下载镜像变量，然后运行：

```powershell
cd C:\repos\aid-work-agent\clients\agent-desktop
$env:AID_AGENT_PACKAGE_API_BASE_URL='https://agent2.aidingyi.cn/api'
npm run package:win:dev
```

该命令调用 `scripts/build-win-dev.ps1`，自动完成以下工作：

1. 检查 Node.js 是否至少为 `22.12.0`。
2. 在 `frontend` 和 `clients/agent-desktop` 中分别执行 `npm ci`，确保依赖与 lock 文件一致，并清理升级 Node 后可能遗留的不兼容依赖。
3. 依次执行类型检查和一次完整构建。
4. 显式枚举 `dist/tests` 下的测试文件并运行，规避 Windows 通配符和 Node.js 22 目录参数问题。
5. 调用底层 Windows 开发包打包器；任一步骤失败都会立即停止，不会留下“成功”的误导提示。

脚本可从任意工作目录直接运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File C:\repos\aid-work-agent\clients\agent-desktop\scripts\build-win-dev.ps1 `
  -ApiBaseUrl 'https://agent2.aidingyi.cn/api'
```

一键脚本必须通过 `-ApiBaseUrl` 参数或 `AID_AGENT_PACKAGE_API_BASE_URL` 环境变量提供其中一种 API 地址。通过 npm 入口时使用环境变量，直接调用 PowerShell 脚本时两种方式均可；`AID_AGENT_API_BASE_URL` 只保留为开发/运维运行期覆盖，普通安装者无需设置环境变量。
