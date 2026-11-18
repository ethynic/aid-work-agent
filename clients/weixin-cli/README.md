# aid-weixin（weixin-cli）

第一方微信操作 CLI / MCP Provider。设计文档见 [docs/design/weixin/weixin-cli-design.md](../../docs/design/weixin/weixin-cli-design.md)。

## 配置

### 服务端代理模式（默认，计费）

UI 元素的计费视觉定位通过现有服务端 LLM 网关调用 `GLM-5.3-Flash`。
配置服务端地址和一次性激活码即可绑定客户端。

```powershell
$env:AID_WEIXIN_SERVER_URL = "https://<服务端地址>"
$env:AID_WEIXIN_ACTIVATION_CODE = "AC-XXXX..."   # 一次性激活码（租户管理员在 portal 生成）
```

首次需要视觉调用时自动激活（`POST /api/client/v1/activate`）换取 access_token，
token 经 DPAPI（CurrentUser）加密存于 `%APPDATA%\aid-weixin\binding.json`，之后复用。
余额不足时操作以 `INSUFFICIENT_CREDIT` 失败；凭据失效/缺失以 `CONFIG_MISSING` 失败
（删除 binding.json 后重设激活码即可重新激活）。

### 本机直调降级（仅开发调试，不计费）

```powershell
$env:AID_WEIXIN_KIMI_API_KEY = "sk-..."   # 显式设置时优先于代理模式
```

> 安全提示：仓库 git 历史（commit `970b3961` 及 `experiments/probes/` 下脚本）曾内嵌过真实 key，该 key 视为已泄漏，必须轮换后再使用。

## 常用命令

```bash
npm run build      # 编译 TS → dist/
npm test           # 构建 + 契约/单元测试
npm run cli        # 构建后启动 human CLI
```

便携归档在编译后用 `npm run pack:portable -- <已存在的输出绝对目录>` 生成。通过 `npm install <tgz>` 安装，在目标 Windows 上使用 Node 22 或更高版本启动。归档包含当前版本的编译文件、清单和 PowerShell 驱动。

### 本机会话历史和监控

```powershell
aid-weixin read --domain chat-history --target-name "<当前已打开的会话全名>" --max-pages 3 --json
aid-weixin read --domain chat-latest --target-name "<当前已打开的会话全名>" --json
aid-weixin watch --target-name "<当前已打开的会话全名>" --interval 10
```

在微信中打开要读取或监控的单聊或群聊。旧 `target_ref` 只包含会话名，读取历史时请改用 `--target-name`。

`chat-latest` 首次最多读取十页并保存本机 DPAPI 副本，此后默认以 JSON 返回锚点之后的新增记录。副本位于 `%LOCALAPPDATA%\aid-weixin\chat-latest`。微信重启后按新的原生会话身份建立副本。签名工具 `weixin_chat_latest` 返回同步状态、扫描页数和新增条数。

监控第一轮建立本机水位，之后确认有新增消息时输出一行 `{"event":"new_messages","count":1}`。保持目标会话打开。会话切换或消息窗口无法对齐时保留原水位，按 Ctrl+C 退出。

### 环境诊断与恢复微信窗口

```powershell
aid-weixin doctor --json
aid-weixin doctor --restore-window --json
```

`doctor` 报告桌面条件、微信进程、主窗口状态与登录就绪情况。`weixin_probe` 使用相同的只读诊断。需要恢复时显式加 `--restore-window`。若微信已缩到托盘，命令通过 Windows 通知区打开原会话窗口。

### 添加好友

```powershell
aid-weixin add --domain friend --identifier "<手机号或微信号>" --verify-message "你好，我想添加你为好友" --json
```

请在微信已登录、桌面未锁屏时执行。每次处理一个标识，省略 `--verify-message` 时使用微信预填的验证语。此命令的视觉定位使用上述服务端计费配置。

`request_sent` 表示已提交申请，`friend_added` 表示微信直接添加为好友，`already_friend` 和 `already_requested` 表示本次未发送。遇到 `effect=unknown` 时先在微信中核对申请状态。
