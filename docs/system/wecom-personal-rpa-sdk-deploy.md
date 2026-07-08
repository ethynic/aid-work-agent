# 企微会话存档 C SDK 部署指南

## 背景

企微官方明确（https://developer.work.weixin.qq.com/document/path/91774）：
**会话存档拉取消息必须用 C 语言 SDK（libWeWorkFinanceSdk_C.so）的 GetChatData 函数，没有 HTTP REST API。**

之前后端 `src/channels/wecom_personal_rpa/archive/http_client.py` 错误地把拉取实现为
HTTP `POST /cgi-bin/msg/get_chat_data`，企微对该路径返回 404，verify 接口永远卡在 Step 2。
现已改为通过 ctypes 调用 C SDK，本文档说明部署步骤。

## SDK 文件

| 文件 | 路径 | 是否入库 | 说明 |
|------|------|---------|------|
| `libWeWorkFinanceSdk_C.so` | `src/channels/wecom_personal_rpa/archive/native_sdk/` | **否**（`.gitignore` 全局 `*.so`） | Linux x86 v3.0，8.9MB |
| `WeWorkFinanceSdk_C.h` | 同上 | 是 | C 头文件（参考） |
| `tool_testSdk.cpp` | 同上 | 是 | C++ 示例代码（参考） |
| `version.txt` | 同上 | 是 | 版本号 |

> **平台限制**：SDK 只提供 Linux x86/x64 二进制。Windows / macOS 无法加载 .so，
> 开发机调试时 verify / fetch 会抛 `SDKLoadError`，需在 Linux 服务器或 WSL 中验证。

## 部署步骤

### 1. 下载 SDK

1. 登录企业微信管理后台（https://work.weixin.qq.com/）
2. 进入「管理后台 → 管理工具 → 会话内容存档」
3. 在页面底部找到「会话内容存档 SDK 下载」，选择 **Linux x86 / x64 v3.0**
4. 解压后得到 `libWeWorkFinanceSdk_C.so`、`WeWorkFinanceSdk_C.h`、`tool_testSdk.cpp`

### 2. 上传 .so 到代码库

把 `libWeWorkFinanceSdk_C.so` 放到：

```
src/channels/wecom_personal_rpa/archive/native_sdk/libWeWorkFinanceSdk_C.so
```

> `.so` 不会进 git（`.gitignore` 全局规则 `*.so` 已覆盖），需在服务器本地手动放置。

### 3. 安装系统依赖

SDK 依赖 libssl 和 libcurl。在服务器上执行：

```bash
# Debian / Ubuntu
apt-get update
apt-get install -y libssl-dev libcurl4-openssl-dev

# CentOS / RHEL
yum install -y openssl-devel libcurl-devel
```

### 4. 重启服务

```bash
# 如果用 Docker
docker restart aid-agent-api

# 如果用 systemd
systemctl restart aid-agent-api
```

### 5. 验证 SDK 加载成功

#### 5.1 用 ldd 检查依赖

```bash
ldd src/channels/wecom_personal_rpa/archive/native_sdk/libWeWorkFinanceSdk_C.so
```

期望输出（关键依赖不应有 `not found`）：

```
libssl.so.x => /lib/x86_64-linux-gnu/libssl.so.x (0x...)
libcurl.so.x => /lib/x86_64-linux-gnu/libcurl.so.x (0x...)
libstdc++.so.6 => /lib/x86_64-linux-gnu/libstdc++.so.6 (0x...)
...
```

如果有 `not found`，回到第 3 步装依赖。

#### 5.2 Python 加载测试

```bash
python -c "
from src.channels.wecom_personal_rpa.archive.wecom_finance_sdk import is_sdk_available
print('SDK available:', is_sdk_available())
"
```

期望输出：`SDK available: True`

如果是 Docker 容器：

```bash
docker exec aid-agent-api python -c "
from src.channels.wecom_personal_rpa.archive.wecom_finance_sdk import is_sdk_available
print('SDK available:', is_sdk_available())
"
```

#### 5.3 端到端验证（verify 接口）

启动后端后，调用 verify 接口（前端「企业微信 RPA 配置 → 验证」按钮，或直接 POST）：

```bash
curl -X POST http://localhost:8000/api/wecom-personal-rpa/verify \
  -H "Content-Type: application/json" \
  -d '{"config_id": "chan_xxx"}'
```

期望响应：`{"success": true, "verified": true, "stages_passed": ["access_token", "chat_data", "private_key", "callback"]}`

如果 `stage=chat_data` 失败且 message 含 `SDKLoadError`，回到第 3 步检查依赖。
如果 message 含 `SDKCallError` 且 code=48002/60011，去企微管理后台开通会话存档权限。

## 故障排查

### 问题 1：`SDKLoadError: libWeWorkFinanceSdk_C.so 不存在`

`.so` 没放到正确路径。检查文件是否存在：

```bash
ls -la src/channels/wecom_personal_rpa/archive/native_sdk/libWeWorkFinanceSdk_C.so
```

### 问题 2：`SDKLoadError: 加载 ... 失败: libssl.so.x: cannot open shared object file`

缺 libssl。执行 `apt-get install -y libssl-dev`。

### 问题 3：`SDKLoadError: 加载 ... 失败: libcurl.so.x: cannot open shared object file`

缺 libcurl。执行 `apt-get install -y libcurl4-openssl-dev`。

### 问题 4：`SDKCallError: Init 调用失败 code=10003`

`10003` = 系统失败，通常是 corpid 或 secret 错误。检查企微管理后台的「会话存档 Secret」
（注意：是会话存档专用 secret，不是自建应用 secret）。

### 问题 5：`SDKCallError: GetChatData 调用失败 code=48002` / `60011`

- `48002` = 应用未获得会话存档 SDK 权限
- `60011` = no privilege

去企微管理后台「管理工具 → 会话内容存档」开通权限，并确认白名单 IP 已配置。

### 问题 6：Windows 开发机无法加载 .so

SDK 只提供 Linux 二进制。Windows 开发机调试时：
- verify / fetcher 相关功能在 Windows 上无法本地验证
- 用 WSL2 跑 Linux 子系统，或在 Linux 服务器上验证
- 单元测试已自动 skip（`sys.platform not in ("linux", "linux2")`）

## SDK 版本

当前使用的版本：v3.0（见 `version.txt`）。

如需升级 SDK：
1. 从企微管理后台下载新版本
2. 替换 `native_sdk/libWeWorkFinanceSdk_C.so` + `WeWorkFinanceSdk_C.h`
3. 对比新旧头文件，如函数签名变化需更新 `wecom_finance_sdk.py` 的 ctypes 配置
4. 跑 `tests/unit/channels/wecom_personal_rpa/archive/test_native_sdk.py` 验证

## 相关代码

| 文件 | 作用 |
|------|------|
| `src/channels/wecom_personal_rpa/archive/wecom_finance_sdk.py` | SDK ctypes 封装（NewSdk/Init/GetChatData/DecryptData/GetMediaData） |
| `src/channels/wecom_personal_rpa/archive/http_client.py` | `get_chat_data()` 调 `wecom_finance_sdk.get_chat_data_raw`，包装为 async |
| `src/channels/wecom_personal_rpa/archive/verifier.py` | verify 路由 Step 2 调 `get_chat_data` |
| `src/channels/wecom_personal_rpa/archive/fetcher.py` | 服务端拉取循环调 `get_chat_data` |
| `tests/unit/channels/wecom_personal_rpa/archive/test_native_sdk.py` | SDK 封装单元测试 |
| `tests/integration/test_archive_sdk_fetch.py` | `get_chat_data` 集成测试 |
