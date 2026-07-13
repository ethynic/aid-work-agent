# 企业微信个人账号 RPA 客户端（WeCom Personal RPA Client）

> .NET 8 Windows 桌面客户端：登录个人企业微信账号、监听消息、把消息回调给 aid-work-agent，
> 并执行服务端下发的 `send_text` / `send_image` / `send_file` 等动作。
>
> 与服务端（aid-work-agent）完全解耦：仅通过渠道 callback、WebSocket 下行和标准消息协议协作，
> 不共享进程、不共享数据库事务。

## 与 aid-work-agent 的关系

- **本客户端**：channel runtime，负责企微登录态、桌面自动化、本地队列、限速、健康上报。
- **aid-work-agent 服务端**：推理系统，负责会话路由、agent 推理、生成回复 actions、下发到客户端。
- **协议边界**：所有交互走 `src/channels/wecom_personal_rpa/` 的 callback / WebSocket / outbox 接口，
  客户端**不** import `src/core/agent.py`。

详见 [设计文档](../docs/system/wecom-personal-rpa-design.md) §0 / §2.2 模块边界。

## 目录结构

```
clients/wecom-personal-rpa/
├── WeComPersonalRpaClient.sln          # 解决方案（5 工程）
├── README.md                           # 本文件
├── src/
│   ├── Client.Core/                    # 状态机、队列、协议 DTO、限速、HMAC（net8.0）
│   ├── Client.Automation/              # FlaUI/UIA3、Win32、OpenCvSharp 自动化（net8.0-windows）
│   ├── Client.App/                     # WPF/托盘 UI，交互式桌面运行（net8.0-windows）
│   ├── Client.Supervisor/              # Windows Service/计划任务监督进程（net8.0-windows）
│   └── Client.Tests/                   # xUnit 单元/集成测试（net8.0）
├── assets/
│   ├── wecom_nodes.yaml                # 自动化节点配置（占位，准入验证回填）
│   └── templates/                      # OpenCvSharp 模板图片（准入验证截取）
├── configs/
│   └── client.example.yaml             # 客户端运行配置示例
└── scripts/
    └── publish.ps1                     # 生成可直接运行/复制部署的 EXE 发布目录
```

## 解决方案工程

| 工程 | 目标框架 | 职责 |
|------|----------|------|
| `Client.Core` | net8.0 | 状态机、队列、协议 DTO、限速、HMAC（不依赖 Windows，CI 可跑单测） |
| `Client.Automation` | net8.0-windows | FlaUI/UIA3、Win32 SendInput、OpenCvSharp 三层自动化 |
| `Client.App` | net8.0-windows | WPF/托盘 UI，运行在交互式桌面会话，执行企微 UI 自动化 |
| `Client.Supervisor` | net8.0-windows | Windows Service / 计划任务，监督 App 存活、拉起、离线上报 |
| `Client.Tests` | net8.0 | xUnit 单元/集成测试 |

命名空间与协议 DTO 镜像表见 [协议文档 §C.2 / §C.3](../docs/system/wecom-personal-rpa-protocol.md)。

## 构建与运行

```bash
# 构建（在仓库根目录或本目录均可）
dotnet build clients/wecom-personal-rpa/WeComPersonalRpaClient.sln -c Release

# 仅构建测试工程
dotnet build clients/wecom-personal-rpa/src/Client.Tests/Client.Tests.csproj

# 运行测试（Core/Tests 不依赖 Windows 桌面特性，可在 CI 无 Windows 环境跑单测）
dotnet test clients/wecom-personal-rpa/src/Client.Tests/Client.Tests.csproj

# 生成自包含 EXE 发布目录（唯一交付方式）
powershell clients/wecom-personal-rpa/scripts/publish.ps1 -Configuration Release -Runtime win-x64
```

构建后的调试 EXE 位于 `src/Client.App/bin/Release/.../Client.App.exe`；正式部署使用
`publish/app/Client.App.exe` 及同目录配置、资产和脚本。客户端不再提供安装包，禁止恢复或使用
旧的安装包构建、安装流程。

> `Client.Automation` / `Client.App` / `Client.Supervisor` 目标 `net8.0-windows`，
> 需在 Windows 上构建；`Client.Tests` 引用 `Client.Automation` 时启用了 `EnableWindowsTargeting`。

## 运行环境要求

| 项 | 要求 | 依据 |
|----|------|------|
| 操作系统 | Windows 10 / 11（桌面会话） | Session 0 约束：自动化须在交互式桌面会话运行 |
| .NET 运行时 | .NET 8 Desktop Runtime | App/Supervisor 需桌面运行时 |
| 企业微信 PC 版本 | 准入验证锁定版本，升级前重新验证 | 自动化节点强依赖窗口/控件结构 |
| 屏幕分辨率 | 固定 1920×1080 | 坐标定位基线，变更须重标定 |
| DPI | 100%（不缩放） | 避免坐标/模板匹配漂移 |
| 桌面会话 | 保持可见桌面，禁止锁屏执行自动化 | 锁屏后 SendInput / UIA 不可靠 |
| 输入独占 | 发送任务期间独占鼠标/键盘/剪贴板 | 避免员工本人操作与 RPA 冲突 |

详见 [设计文档 §6.1 Windows 环境约束](../docs/system/wecom-personal-rpa-design.md)。

## ⚠️ 自动化节点常量待准入验证回填

`assets/wecom_nodes.yaml` 中的 `class_name` / `title_contains` / `offset` / `size` / `template`
**全部为占位默认值**，必须在编码前准入验证阶段（[设计文档 §10.1](../docs/system/wecom-personal-rpa-design.md)）
用真实企微版本回填：

- UIA 可见性：FlaUI 能稳定识别主窗口、输入框、部分会话元素
- 模板匹配：OpenCvSharp 可在固定窗口 region 稳定识别发送/搜索/二维码区域
- 桌面保活：远控断开/计划任务重启后交互式会话仍可恢复
- 登录态检测：已登录、未登录、二维码过期、账号异常可区分

**回填前不得进入生产准入**（设计文档 §10.3：误发消息 0 容忍，定位漂移会直接导致误发）。

## 关联文档

- 设计：[docs/system/wecom-personal-rpa-design.md](../docs/system/wecom-personal-rpa-design.md)
- 协议（共享契约）：[docs/system/wecom-personal-rpa-protocol.md](../docs/system/wecom-personal-rpa-protocol.md)
- 开发计划：[plans/plan-wecom-personal-rpa.md](../plans/plan-wecom-personal-rpa.md)
- 调研：[docs/research/wecom-personal-rpa-client-implementation-research.md](../docs/research/wecom-personal-rpa-client-implementation-research.md)
