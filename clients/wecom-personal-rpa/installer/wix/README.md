# 安装包（WiX Toolset）

本目录用于承载企业微信个人账号 RPA 客户端的 WiX 打包与代码签名工程。

## 当前状态：本会话 OUT（不在范围内）

打包/签名/安装器实现归属开发计划 **第 5 节**（部署与发布），当前会话不实现。
本 README 仅作占位，确保目录结构与设计文档一致。

## 计划范围（后续会话实现）

依据 [设计文档 §10.2 首版必须包含 — 部署](../../docs/system/wecom-personal-rpa-design.md)：

| 项 | 说明 |
|----|------|
| 打包工具 | WiX Toolset（生成 MSI） |
| 代码签名 | 代码签名证书签名 Client.App / Client.Supervisor 二进制与 MSI |
| 开机自启 | Client.Supervisor 注册为 Windows Service / 计划任务，开机自启拉起 Client.App |
| 升级回滚 | MSI 支持升级与回滚；版本号与服务端 `min_client_version` 协同（protocol.md §A.7） |
| 标准镜像 | 提供标准 Windows 镜像基线（分辨率/DPI/企微版本固定） |

## 关联文档

- 设计：[docs/system/wecom-personal-rpa-design.md](../../docs/system/wecom-personal-rpa-design.md)
- 协议：[docs/system/wecom-personal-rpa-protocol.md](../../docs/system/wecom-personal-rpa-protocol.md)
- 开发计划：[plans/plan-wecom-personal-rpa.md](../../plans/plan-wecom-personal-rpa.md)
