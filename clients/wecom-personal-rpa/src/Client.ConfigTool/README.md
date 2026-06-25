# Client.ConfigTool

企业微信个人账号 RPA 客户端 - **配置写入 CLI**。

填补「平台后台给用户复制的 yaml 片段」和「客户端只读 `client_config.enc`（DPAPI 加密文件）」之间的空白。
yaml 片段对客户端无效；必须用本工具把配置加密写入 `%LOCALAPPDATA%\WeComRpa\client_config.enc`。

## 使用场景

- 首次部署：用户拿到平台后台「RPA 绑定管理」→「新增绑定」下发的 client_id / client_secret / agent_base_url / tenant_id，写入本机加密配置。
- 密钥轮换：平台后台「轮换密钥」后用新 client_secret 覆盖现有 client_config.enc。

## 编译

```powershell
cd clients\wecom-personal-rpa
dotnet build src\Client.ConfigTool\Client.ConfigTool.csproj -c Release
# 或整个解决方案
dotnet build WeComPersonalRpaClient.sln -c Release
```

产出：`src\Client.ConfigTool\bin\Debug\net8.0-windows10.0.19041.0\Client.ConfigTool.exe`

发布单文件 exe：见 `scripts\publish.ps1`（会生成 `publish\config-tool\Client.ConfigTool.exe`）。

## 运行

### 交互模式（推荐）

```powershell
.\Client.ConfigTool.exe
# → 逐项提示输入 client_id / client_secret（不回显）/ agent_base_url / tenant_id / poll_interval_seconds
# → 默认写入 %LOCALAPPDATA%\WeComRpa\client_config.enc
```

### 参数模式（自动化）

```powershell
.\Client.ConfigTool.exe `
    --client-id rpa_client_xxxxxxxxxxxxxxxx `
    --client-secret xxxxxxxxxxxxxxxxxxxxxxxxxx `
    --agent-base-url http://localhost:8000 `
    --tenant-id tenant_xxxxxxxxxxxx `
    --yes
```

### 环境变量模式

```powershell
$env:CLIENT_ID="rpa_client_xxx"
$env:CLIENT_SECRET="xxx"
$env:AGENT_BASE_URL="http://localhost:8000"
$env:TENANT_ID="tenant_xxx"
.\Client.ConfigTool.exe --from-env --yes
```

### 自定义输出路径（测试用）

```powershell
.\Client.ConfigTool.exe --output D:\tmp\client_config.enc ...
```

## 参数清单

| 参数 | 说明 | 默认 |
|---|---|---|
| `--client-id` | 客户端 ID | 必填 |
| `--client-secret` | 客户端密钥（明文，回显不显示） | 必填 |
| `--agent-base-url` | Agent 服务端 URL | `http://localhost:8000` |
| `--tenant-id` | 租户 ID | 必填 |
| `--poll-interval-seconds` | 轮询间隔秒 | 30 |
| `--output` | 输出路径 | `%LOCALAPPDATA%\WeComRpa\client_config.enc` |
| `--from-env` | 从环境变量读取（CLIENT_ID / CLIENT_SECRET / AGENT_BASE_URL / TENANT_ID / POLL_INTERVAL_SECONDS） | - |
| `--yes` / `-y` | 覆盖时不询问确认 | false |
| `--help` / `-h` | 显示帮助 | - |

## 安全说明

- **DPAPI CurrentUser scope**：加密后仅当前 Windows 用户可解密，换用户/换机自动失效。
- **client_secret 不回显**：交互式输入时字符显示为 `*`，参数模式不打印。
- **不写明文临时文件**：直接在内存构造 ClientOptions，经 EncryptedClientConfig 加密后落盘。
- **覆盖前 confirm**：文件已存在时默认询问确认（除非 `--yes`）。
- **stdout 不含 secret**：成功提示只输出 ClientId / BaseUrl / TenantId / PollInterval / StoragePath。

## 故障排查

| 退出码 | 含义 |
|---|---|
| 0 | 写入成功 |
| 1 | 用户取消覆盖 |
| 2 | IO 或加密异常 |
| 3 | Ctrl+C 中断 |
| 4 | 参数错误 |
| 5 | 必填项缺失（非交互模式） |

## 相关文件

- 加密组件：`src/Client.Core/Config/EncryptedClientConfig.cs`
- 配置模型：`src/Client.Core/Config/ClientOptions.cs`
- 客户端加载器：`src/Client.App/Services/ClientOptionsLoader.cs`（客户端启动时优先读 `client_config.enc`，无则回退 `client_options.json`）
