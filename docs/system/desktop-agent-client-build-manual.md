# Windows 桌面客户端编译与打包手册

> 新版产品设计与后续实施见[桌面客户端 v3](desktop-agent-client-design.md)和[开发计划](../plans/plan-desktop-agent-client.md)。本手册描述现有 Shell 的构建链路；构建成功不表示 Runner 对话或本地工具已接入。

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

## 12. 独立Runtime首期验收包

本节描述H3新增构建链路。2026-10-09提交前UI握手revision竞态已由Runtime唯一作者修复，独立52项UI测试/typecheck/CR通过后，按标准流程重新构建；新包68b855通过标准验包、独立复验及包内EXE实际启动/退出。以下为当前交付，替代旧a8b0安装器。证据见[桌面计划9.7](../plans/plan-desktop-agent-client.md#97-第一部分h3实施登记2026-10-09)。第一部分只包含配对、执行实例管理与第一方BOSS/微信/企业微信插件管理，第三方skill和完整桌面对话不在此包范围。

本次可人工验收的文件：

| 文件 | 位置 / 核验值 |
|---|---|
| Windows x64安装器 | [AID-Work-Runtime-0.0.2-win-x64-acceptance-unsigned.exe](../../clients/agent-desktop/build/runtime-release/acceptance/AID-Work-Runtime-0.0.2-win-x64-acceptance-unsigned.exe) |
| 安装器 SHA-256 | `68b855010c8f39798a8822df83191e3044bdbb7860d46123345fe1dfbee9d3bd`，126241075字节 |
| 构建输入 SHA-256 | `d12b14e1758bdba88ffaff6d79d4e17a68181e3f3d947e21e3f6616bb3e76aff` |
| 完整资源/构建记录 | [runtime-release-manifest.json](../../clients/agent-desktop/build/runtime-release/acceptance/runtime-release-manifest.json) |
| 本批离线插件包 | [BOSS](../../clients/runtime-plugin-packaging/release/final-acceptance/boss-0.3.0-dev.aidplugin.zip)、[微信](../../clients/runtime-plugin-packaging/release/final-acceptance/weixin-0.1.0-dev.aidplugin.zip)、[企业微信](../../clients/runtime-plugin-packaging/release/final-acceptance/wecom-0.1.0-dev.aidplugin.zip) |

三包实际manifest版本为0.3.0/0.1.0/0.1.0，文件名-dev为验收资产名称。最终Host摘要为`f52b056be3b729ebfef1cc9786079b34d16eb9092312d19076269bed527fc13f`，验收公钥根摘要为`cdc5d4b0f5f27f62f23c06a43436217306525f3f1c16fbe16a6396eca28c3cec`；不得混用前期候选tgz。资源准备完成后下列package/verify使用同一冻结输入。输出目录属于本机构建产物，不进入Git提交。

Runtime作者交付新Host tgz及SHA-256、wrapped验收信任根、对应三份签名离线包后，先在桌面工程执行以下命令。路径与摘要必须替换为该次正式交接值，不能使用第9.6节旧A1资产声称插件安装已接通。

```powershell
cd C:\repos\aid-work-agent\clients\agent-desktop
node scripts/prepare-runtime.mjs `
  --profile acceptance `
  --host-package 'C:\交付目录\runtime-a2.tgz' `
  --host-sha256 '<交接的64位SHA-256>' `
  --trust-roots 'C:\交付目录\trust-roots.json'
npm run runtime:package:acceptance
npm run runtime:verify
```

资源准备使用固定Node22.23.3 Windows x64及官方摘要，不取latest；Host完整编译目录与预构建依赖随包复制，构建期间不安装插件依赖。信任输入固定为`schemaVersion/profile/roots`，每根必须显式包含`test_only:boolean`，只包含发布者公钥，不包含签名私钥。production拒绝测试根；验收根不会转换为正式根。

验收输出位于`clients/agent-desktop/build/runtime-release/acceptance/`，包括NSIS安装器、`win-unpacked`与`runtime-release-manifest.json`。验收安装器未签Windows证书，仅用于内部验收；插件包自身的发布者签名仍须验证。正式发行另用production输入与有效Windows证书，不能将验收包直接改名发行。

| 验收项 | 预期行为 |
|---|---|
| 身份与目录 | 标题显示“AID Work Runtime 验收版”；独立appId/userData，Runtime home为`%APPDATA%\aidwork-tool-runtime-acceptance`，不覆盖生产home |
| 初始状态 | 打开“本机执行环境”，未配对时禁止启动；不要求先登录完整Desktop账号 |
| 配对 | 输入管理员提供的服务地址、设备名和一次性码；UI不展示device token，未核对旧事实时拒绝替换身份 |
| 安装与就绪 | 原生选择器导入交接的签名离线包，安装成功默认启用；应用缺失或未登录可显示未就绪及原因 |
| 错包 | 签名/内容/平台错误返回明确失败，原已安装版本不被损坏；不靠重新选择同名文件改变已接受意图 |
| 停用/卸载 | 核对旧执行后切换；旧执行/回执记录保留，不能静默回退到legacy入口 |
| 窗口与退出 | 关窗收至托盘；托盘可重新打开，显式退出等待收尾。待核对时保留进程，不以强杀当完成 |
| 恢复 | 重开查询真实当前实例及持久operation；不自动重放未知业务动作 |

业务软件的真实登录与操作在人工验收阶段按实际环境确认。构建、空Host smoke和UI fixture通过不能代替这一验收；服务端部署仍须用户明确授权。

人工先运行上表安装器并打开“AID Work Runtime 验收版”，依次用“安装 / 升级离线包”的原生选择器导入上表三包；安装后应显示默认启用。实际软件未安装、未登录或依赖条件未满足时，显示未就绪及原因是预期结果，按提示准备环境后刷新状态。随后检查关窗留托盘、托盘重开与退出、重开后插件/操作记录保持。真实配对使用管理员提供的现有服务地址及一次性码，核对未配对禁止启动与已配对执行状态；不得为通过验收自行部署服务或绕过旧执行核对。

自动化三包安装使用了原生dialog返回值替代，其后Main/可信文件快照/私有callback/Host签名验证均真实；实际NSIS安装、OS选择框交互、软件登录和服务端配对/业务执行尚未由本批自动化验证。包内EXE已实际启动并安全退出，不能把这个结果写成NSIS安装通过。

### 12.1 BOSS完整人工验收：构建到招聘智能体调用

适用源码：`f1087289`或包含该提交的版本。这里只写本机构建与验收，不要求部署服务器或执行旧M0.7指南的建表SQL。云端使用已有本地工具/招聘操作智能体服务；以下以`https://agent2.aidingyi.cn`为例，换成实际已运行环境。Web配对、设备选定和招聘对话必须使用同一租户、同一登录用户。

#### 路径选择与前置条件

快速测试可以直接使用12节已交付的`68b855…`安装器和`final-acceptance/boss-0.3.0-dev.aidplugin.zip`，跳过本节构建步骤。这两个文件已经匹配信任根。客户机只需Windows10/11 x64、交互桌面、Chrome和BOSS测试账号，不需要源码、Node/npm/Python/Git；安装插件也不会生成全局`boss-cli`命令。

完整源码出包按下方执行：构建机需要Windows x64、Node22或更高、npm、仓库及下载构建依赖的网络。运行`--test`每次都会生成新签名公钥根，必须先出插件，再把同一次输出的根写入Runtime安装器。新插件不能混用12节旧安装器；新安装器也不能配旧签名包。输出目录必须新建。重新生成根的验收宜使用未使用本验收profile的Windows测试账户/测试机，避免旧插件仍使用上次签名根；不要删除凭证或执行记录绕过核对。

开始前正常停止旧`aid-runtime`/旧执行节点，避免同一桌面并行自动化；未完成业务先正常收尾。使用受控BOSS测试账号，不进行批量招呼/发送或简历处理。已有Chrome调试profile可继续使用，不新建临时profile轮换登录。

#### 第一步：构建单个BOSS签名插件与Runtime安装器

以下PowerShell命令逐条执行；任一步非零退出或报错先停止，不能继续用不完整产物。路径按当前构建机写，换机器时调整repo/npm-cli路径。

```powershell
$repo = 'C:\repos\aid-work-agent'
Set-Location $repo
git log -1 --oneline

# 构建依赖；只构建BOSS，不需要微信/企微的OCR资产
npm --prefix clients/shared ci
npm --prefix clients/agent-tool-runtime ci --ignore-scripts
npm --prefix clients/boss-resume-assistant ci --ignore-scripts
npm --prefix clients/runtime-plugin-packaging ci --ignore-scripts
npm --prefix frontend ci
npm --prefix clients/agent-desktop ci

# 准备项目锁定的Node22.23.3（只下载/校验Node，不生成产品信任配置）
node clients/agent-desktop/scripts/prepare-runtime.mjs --node-only
$fixedNode = Join-Path $repo 'clients\agent-desktop\build\runtime-cache\node-v22.23.3-win-x64\node.exe'
$npmCli = Join-Path (Split-Path (Get-Command npm).Source) 'node_modules\npm\bin\npm-cli.js'
if (!(Test-Path $npmCli)) { throw '请将npmCli改为构建机实际npm/bin/npm-cli.js路径' }
& $fixedNode -v
# 预期v22.23.3

$batchDir = Join-Path $repo ('clients\agent-desktop\build\manual-boss-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $batchDir | Out-Null
$pluginDir = Join-Path $batchDir 'plugins'

npm --prefix clients/boss-resume-assistant run build:main
node clients/runtime-plugin-packaging/package.mjs --test --provider boss --node $fixedNode --npm-cli $npmCli --output $pluginDir
# plugins内应有boss-0.3.0-dev.aidplugin.zip及ISOLATED-TEST-TRUST-ROOT.json

# Host打包走新入口，不使用clients/pack.sh
npm --prefix clients/agent-tool-runtime pack --pack-destination $batchDir
$hostVersion = (Get-Content clients/agent-tool-runtime/package.json -Raw | ConvertFrom-Json).version
$hostPackage = Join-Path $batchDir "agent-tool-runtime-$hostVersion.tgz"
$hostSha = (Get-FileHash -LiteralPath $hostPackage -Algorithm SHA256).Hash.ToLowerInvariant()
$trustRoots = Join-Path $pluginDir 'ISOLATED-TEST-TRUST-ROOT.json'

node clients/agent-desktop/scripts/prepare-runtime.mjs --profile acceptance --host-package $hostPackage --host-sha256 $hostSha --trust-roots $trustRoots
npm --prefix clients/agent-desktop run runtime:package:acceptance
npm --prefix clients/agent-desktop run runtime:verify

$installer = Join-Path $repo 'clients\agent-desktop\build\runtime-release\acceptance\AID-Work-Runtime-0.0.2-win-x64-acceptance-unsigned.exe'
Get-FileHash -LiteralPath $installer -Algorithm SHA256
Get-FileHash -LiteralPath (Join-Path $pluginDir 'boss-0.3.0-dev.aidplugin.zip') -Algorithm SHA256
```

通过标准：构建均exit0，出现`RUNTIME_PACKAGE_PASS`与`RUNTIME_VERIFY_PASS`。交给客户机的是安装器和同次`pluginDir`中的BOSS包；公钥根已嵌入安装器，不让客户机手工改信任文件。构建记录位于安装器同目录`runtime-release-manifest.json`，记录该次实际摘要。重新打包的文件不要求摘要等于12节旧交付摘要；新根、新签名和重建安装器会改变字节。

#### 第二步：安装Runtime

1. 把该次安装器与BOSS `.aidplugin.zip`复制到验收电脑，保留原文件名，插件不解压。
2. 双击NSIS安装器，按向导进行当前Windows用户安装。该包为内部未签Windows证书的验收版；若系统策略阻止安装，先记录提示，不把`win-unpacked`启动冒充安装通过。
3. 从开始菜单或快捷方式打开“AID Work Runtime 验收版”。应显示“本机执行环境”；初始未配对、执行已停止、插件列表为空，“启动执行”不可用。
4. 验收目录为`%APPDATA%\aidwork-runtime-app-acceptance`与`%APPDATA%\aidwork-tool-runtime-acceptance`；不要修改生产Runtime目录。

通过标准：实际安装成功，启动窗口正确，没有资源缺失/管理版本不兼容错误。首次旧实例或旧事实阻断应保留现场，不手改JSON/删目录。

#### 第三步：准备BOSS调试Chrome

使用稳定的非默认Chrome profile和默认CDP端口9222。下面示例首次建立验收专用的持久profile；已有受控profile时改用其原路径，后续重复使用。

```powershell
$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
# 若安装在其他目录，替换chrome路径
$bossProfile = Join-Path $env:LOCALAPPDATA 'AidWorkRuntimeBossChrome'
& $chrome --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="$bossProfile"
Invoke-RestMethod 'http://127.0.0.1:9222/json/version' | Select-Object Browser
```

在这一个Chrome窗口打开BOSS直聘，登录受控招聘者账号，进入“推荐牛人”，确认存在可读取的筛选面板和职位下拉。保持窗口可见、桌面不锁屏；RDP不能断开或最小化。测试调用借用真实鼠标，发出指令后停止鼠标键盘输入，避免遮挡业务窗口。Web对话可从另一台设备用同一账号操作。

通过标准：9222返回Browser字段，BOSS推荐牛人页面为已登录招聘者视图。普通Chrome已登录但该调试profile未登录不算通过。参考M0.7指南仅采用其Chrome说明，不采用旧npm全局安装/服务器部署/建表步骤。

#### 第四步：通过Web生成配对码，配置Runtime

1. 打开现有企业Web，登录将要测试招聘智能体的用户；进入“本地工具”，也可访问`<服务地址>/t/<租户ID>/local-tools`。
2. 点击“生成配对码”。有效期5分钟，一次性使用；过期重新生成，不复用旧码。
3. Runtime填写：服务地址=`https://agent2.aidingyi.cn`或实际企业服务根地址（不附加`/api`）；设备名称=`BOSS插件验收机`；一次性配对码=刚生成的码。
4. 点击“确认配对”，等待管理操作“已完成”；设备栏显示配对身份，执行仍为已停止。未启动时离线/连接中不单独判失败。

通过标准：Web设备列表出现同名设备，Runtime保留设备身份；界面不展示device token。不需要手写config.json或Provider entry路径。

#### 第五步：安装并确认BOSS插件就绪

1. Runtime“第一方插件”区域点击“安装 / 升级离线包”。
2. 在Windows原生选择器选中同次构建的`boss-0.3.0-dev.aidplugin.zip`。
3. 等待管理操作完成，再点“刷新状态”。应出现BOSS插件、版本0.3.0、“已启用”。
4. 在已启动调试Chrome、已登录并处于推荐牛人页的条件下，刷新后还应出现“已就绪”。

“安装成功/已启用”和“已就绪”分别验收：没开Chrome、没登录或不在适当页面时，安装可成功但显示“未就绪”及原因；准备好页面再刷新，不反复重装。不使用npm全局boss-cli来替代安装插件，不填旧`bossCliEntry`，也不手改插件manifest/信任根。签名失败优先检查是否把新测试包和旧安装器混用。

#### 第六步：启动执行，选定同一设备

1. Runtime点“启动执行”，刷新后应显示“运行中”与“在线”。
2. Web“本地工具”页刷新，找到“BOSS插件验收机”，点击“选定”。
3. Web应显示设备在线并有“使用中”标记。

通过标准：Runtime在线、BOSS已启用且已就绪、Web同一用户选定本机设备三项齐备。只配对未启动或启动但未选定都不能验证云端调用。

#### 第七步：招聘操作智能体实际调用

打开Web“招聘操作智能体”，常见路由为`<服务地址>/t/<租户ID>/chat/recruiting-operator`。若该智能体未提供给此用户，先由管理员确认现有账号权限，不在本流程部署服务器。

用例1：查询页面筛选档位，发送：

> 请只调用一次 boss_filter_options，读取我本机BOSS“推荐牛人”页的筛选可选档位。返回经验、学历、薪资选项；不要设置筛选、打招呼、发消息或处理简历。

预期：本机BOSS筛选面板短暂打开后收起；智能体返回实际可选档位。没有可见工具调用/本机日志，只有口头回复不算通过。

用例2：查询BOSS页面职位，发送：

> 请只调用一次 boss_list_jobs，列出我当前BOSS页面中的招聘职位。使用BOSS页面工具，不要查询云端职位库boss_jobs_list，不要切换职位或打招呼。

预期：本机职位下拉被短暂打开，返回职位名及相应属性，与页面核对一致。该“只读”工具会点击下拉并使用真实鼠标，但不改变职位业务状态。

用例3：改变页面筛选状态。先从用例1选择一个实际存在的档位，再发送（替换占位内容）：

> 请只调用boss_filter，把学历设置为“<刚查到的精确学历选项>”。只改变页面筛选状态，不打开候选人详情、不读取简历、不打招呼、不发送消息。

预期：页面筛选条件实际改变，智能体报告执行成功。随后可单独请求`boss_clear_filter`清除筛选，核对页面恢复。此次用例验证受控操作执行，不能据此宣称真实打招呼/消息发送已经验收。

当前已知限制：插件MCP完整工具21项，而Host既有BOSS受信子集18项；`boss_open_detail`、`boss_greet_detail`、`boss_close_detail`仍不在Host白名单。新的详情页筛选/打招呼主流程可能报`TOOL_NOT_ALLOWED`；这就是计划10.4登记的既有清单差异，不把该流程当作本轮应通过用例，也不通过手改白名单绕过。上述两项查询与页面筛选工具在当前子集内。

#### 第八步：用事实确认链路及记录结果

可在验收电脑PowerShell查看当前Runtime日志：

```powershell
$runtimeLog = Join-Path $env:APPDATA 'aidwork-tool-runtime-acceptance\logs\runtime.log'
Get-Content -LiteralPath $runtimeLog -Tail 100
# 需要实时观察时：Get-Content -LiteralPath $runtimeLog -Tail 20 -Wait
# 结束观察用Ctrl+C，只退出日志查看，不停止Runtime
```

核对同一次调用出现相应`tool=boss_filter_options`或`tool=boss_list_jobs`的领取记录、Provider工具结果以及`invocation … 终态已回传 success=true`。结合Web工具执行记录、本机页面动作、返回数据人工比对；最终判据是云端下发→Runtime领取→插件执行→结果回传均有证据，不能只看安装“已就绪”或智能体声称成功。

| 检查点 | 必须观察到 |
|---|---|
| 出包 | package/verify成功，同一次根与插件匹配 |
| 安装 | 真实NSIS完成，Runtime能打开 |
| 配对/路由 | 同用户设备在线且已选定 |
| 插件 | BOSS0.3.0已启用且已就绪 |
| 只读调用 | 两个指定工具执行，返回与BOSS页面一致 |
| 页面操作 | 可选档位实际筛选生效，清除可恢复 |
| 收尾 | 停止执行收尾完成、关窗托盘恢复、显式退出后可重开 |

排查顺序：管理连接失败→安装资源/旧实例；验签失败→同批根与包；未就绪→9222/登录/推荐牛人页；Web设备离线→Runtime启动/服务地址/网络；LOCAL_DEVICE_NOT_FOUND→同用户与选定设备；PROVIDER_NOT_AVAILABLE→BOSS插件启用/就绪/刷新后能力上报；WRONG_PAGE→回推荐牛人页；指定只读工具仍TOOL_NOT_ALLOWED→记录实际工具名/版本，不修改清单；effect=unknown/partial→先核对页面与原操作，不能重复写动作。

反馈提供失败步骤、测试时间、设备是否在线/选定、插件状态、实际工具名与错误码、相关脱敏截图/日志片段；不要发送配对码、token、credentials.bin或完整个人简历内容。退出核对受阻保留原身份和事实，不强杀或删记录作通过。
