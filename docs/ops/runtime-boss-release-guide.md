# Runtime 与 BOSS 插件构建及 ZIP 交付手册

> 维护日期：2026-10-30。当前入口是 [release-batch.ps1](../../clients/agent-desktop/scripts/release-batch.ps1)。
> 本文覆盖源码出包，不授予安装、部署或提交权限。旧批次报告是历史证据，不拼接成新发布步骤。

## 1. 交付形态和目录

Runtime 与 BOSS 独立发布。Runtime 安装器包含 UI、Host、Core、固定 Node 与监管程序；BOSS 是另行导入的签名插件。只更新 BOSS 不需要重建 Runtime，只更新 Runtime 不需要给 BOSS 升版本。旧客户端满足原需求即可继续使用，不随服务端发布强制升级。

最终交付仍位于 `clients/release/<批次>/`，旁边生成组合 ZIP 及 `.sha256`。所选组件的安装器/插件、公开信任根、验包报告、build-info、说明和 SHA256SUMS 一并保存；未选择的组件不构建、不复制，也不标记为验证通过。

## 2. 构建机条件

Windows 10/11 x64，Node/npm、Git、Windows 自带 tar.exe、PowerShell 5.1、现有离线验包器所需 Python 3。Runtime 编译监管程序使用 Windows .NET Framework csc.exe。包内 Node 仍固定 **22.23.3 / win-x64 / ABI127**；不是任意 PATH Node。客户机无需安装这些构建工具。

同一 checkout 不并行出包，prepare/package 使用共用构建目录。shared 是 npm workspace 根，脚本从该根一次安装，避免逐成员安装破坏 hoisted 依赖。脚本保持原 tar 路径修正、受管构建输出与压缩占用重试。

## 3. 选择组件与独立版本

以下先用 `-Plan` 查看步骤：它只读取公开版本信息并输出计划，不运行 npm、读取私钥、修改版本/信任根或创建交付产物。

```powershell
# 仅检查将发布的 BOSS；此路径只是签名者配置路径，Plan 不会读取它。
powershell.exe -NoProfile -File clients/agent-desktop/scripts/release-batch.ps1 -Component Boss -Signer C:\secure-build\boss-signer.json -BossVersion 0.3.28 -Plan

# 仅更新 Runtime，保留当前 BOSS。版本按实际变更填写。
powershell.exe -NoProfile -File clients/agent-desktop/scripts/release-batch.ps1 -Component Runtime -RuntimeVersion 0.0.32 -Plan
```

确认需要出包后删除 `-Plan`。`-Component` 可选 `Runtime`、`Boss`、`Both`，默认 Both。`-RuntimeVersion` / `-BossVersion` 仅修改所选组件；省略时读取当前版本，不自动 patch，不创建 Git tag/commit。新代码内容不能冒充已交付同版本包；版本已更新时无需再 bump。Host/Core 等版本仍按其真实源码变更维护，不与 UI/BOSS 绑在一起。

同版本重建用于修复分发时，发行ID与内容仍须一致；确需不同发行内容应使用新的版本/发行ID。脚本不覆盖已有批次目录和交付文件。

## 4. 稳定签名者与公开信任

BOSS 正常发布要求 `-Signer`，复用现有 `package.mjs --signer`。外部配置包含稳定 `key_id` 和指向私钥的 `private_key_path`；私钥存构建机安全位置，不入库、不复制到发行目录、不打印。`-ReleaseId` 指定不可变发行ID，省略时为 `boss-<BOSS版本>`。

普通补丁继续使用同一发布者，不每批生成随机key、不每批追加永久信任根或历史快照。初次采用稳定签名者时，沿既有[信任登记脚本](../../clients/runtime-plugin-packaging/register-publisher-trust.mjs)明确登记公开根一次，权限范围只包含实际负责的Provider。脚本本身不替你登记新发布者。

`-TrustRoots` 指向已有公开配置：acceptance 省略时读取仓库已登记集合，并保持已有历史交付根完整；production 必须显式提供正式根，且拒绝任何 `test_only` 根。本轮不删除或改写任何已登记历史根。历史快照供审计和已交付兼容核对，不把未交付试验批次变成新的永久依赖。

旧 Runtime 能继续运行其已交付的旧 BOSS；这不意味着它认识任意新发布者。要使用新包的客户应有信任该发布者且协议兼容的 Runtime；没有选择新包的客户无需升级。密钥轮换/真实撤销另行明确处理，不按普通补丁自动换根。

## 5. 常用出包命令

```powershell
# 只发 BOSS，使用已登记稳定签名者；不重建 Runtime。
powershell.exe -NoProfile -File clients/agent-desktop/scripts/release-batch.ps1 -Component Boss -Signer C:\secure-build\boss-signer.json -ReleaseId boss-0.3.28 -BossVersion 0.3.28

# 只发 Runtime，使用当前已登记公开根；不改 BOSS 版本。
powershell.exe -NoProfile -File clients/agent-desktop/scripts/release-batch.ps1 -Component Runtime -RuntimeVersion 0.0.32

# 本次两者确实都更新，仍分别指定版本。
powershell.exe -NoProfile -File clients/agent-desktop/scripts/release-batch.ps1 -Component Both -Signer C:\secure-build\boss-signer.json -RuntimeVersion 0.0.32 -BossVersion 0.3.28

# 正式产品使用原生产构建链，需原有有效 Windows 代码签名配置及正式公开根。
powershell.exe -NoProfile -File clients/agent-desktop/scripts/release-batch.ps1 -Component Both -Profile production -Signer C:\secure-build\boss-signer.json -TrustRoots C:\secure-build\production-roots.json -RuntimeVersion 0.0.32 -BossVersion 0.3.28
```

acceptance Windows 安装器仍为未正式代码签名的验收产品，插件仍有Ed25519签名。production 不因证书缺失而降级为 acceptance。是否发行安装器由组件选择决定，和当前客户必须升级是两件事。

## 6. 显式隔离试验包

需要随机测试签名者时继续使用原独立入口，不通过普通发布脚本：

```powershell
node clients/runtime-plugin-packaging/package.mjs --test --test-key-id aid-isolated-local-example --provider boss --node <固定Node路径> --npm-cli <npm-cli.js路径> --output <自有隔离目录>
```

测试输出含 `ISOLATED-TEST-TRUST-ROOT.json`，只能在明确隔离的受信测试环境使用；该命令不会登记到共享根/历史快照，不使已有 Runtime 自动认识这个随机根。测试公钥ID不能复用已经登记的交付ID，避免同ID异密钥。不给随机测试包配套强制客户升级。

## 7. 验包与证据

正常脚本只对所选组件执行原验证：插件签名与完整清单、重定位后的 version/doctor、原生模块加载，以及 Runtime 安装器资源/签名配置和源码输入核对。离线插件验包可显式选择稳定公开根：

```powershell
python clients/runtime-plugin-packaging/verify-offline.py --directory <插件目录> --node <固定Node路径> --trust-roots <公开信任根.json>
```

省略 `--trust-roots` 仍兼容旧隔离包目录中的 `ISOLATED-TEST-TRUST-ROOT.json`。production 公开根不能带测试签名者。验签不等于业务登录或实际安装通过。

build-info 分别记录所选组件验证与 `not_selected` / `not_run`。运行 NSIS、客户机升级、配对和真实招聘动作应另有授权与独立证据；普通出包脚本不会运行安装器、后台部署或业务调用。

## 8. 安装和升级说明

包内说明根据所选组件生成。Runtime安装器不自动更新BOSS；插件从现有离线包入口安装，不解压。兼容覆盖安装沿用配置/身份；确有损坏再选择干净重装，实际安装行为以安装设计与验收为准。

服务端单独发布仍需保持 Runtime **0.0.29＋BOSS0.3.27** 原功能与原任务恢复可用。新签名/新功能的包没有改变这条已交付兼容要求。

## 9. SHA-256与交付检查

构建与验收临时文件不放主工作区：沿已有脚本在仓库外Temp独立副本构建，最终安装文件、JSON清单/脱敏报告及ZIP校验和集中保存至`%LOCALAPPDATA%/AidWorkBuild/deliveries/`，不提交生成产物。主仓库保留脚本、设计/计划与必要公开发行根；不要按`.json`扩展名批量删除配置或凭据。S5已按此归档，产物摘要及证据见[统一计划12.8](../plans/plan-runtime-plugin-host.md#128-s5打包与隔离安装验收2026-10-30)。

脚本仍为交付文件生成 SHA256SUMS，压缩后逐项核对 ZIP 内容长度/摘要，再生成 ZIP 的 `.sha256`。文件短暂占用时仅诊断与重试自己的输出，不终止编辑器或客户应用。checksum/验包通过不能替代真实安装与业务验证。

## 10. 常见问题

| 情况 | 处理 |
|---|---|
| 缺少Signer | 普通BOSS发布提供已有稳定签名者；仅隔离试验才显式用--test，不自动生成永久新根 |
| 新发布者不受信任 | 明确登记一次正确Provider范围的公开根；检查目标Runtime，不要求所有旧客户升级 |
| production含test_only | 使用正式根与证书，不静默降级或把测试根改称正式根 |
| npm ci锁文件不一致 | 只在受影响消费工程同步lockfile并审阅，不删除整份锁 |
| GNU tar误处理C盘路径 | 脚本优先Windows System32 tar；不替换包格式 |
| Compress-Archive文件占用 | 保持现有Restart Manager只读诊断和有限退避重试 |
| 只更新插件 | 选择Component Boss，不重建/升版Runtime；只使用目标Runtime已支持的协议与发布者 |
