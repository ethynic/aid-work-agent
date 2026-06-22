// ============================================================================
// 仅诊断可见性：把 Win32 / WeCom 内部类型（DesktopState / WeComMainWindow /
// NativeMethods 等）暴露给「编码前准入验证」探测工具 Client.Probe 复用，
// 避免在诊断工程里复制粘贴 P/Invoke 与窗口枚举逻辑。
//
// Client.Probe 是按需运行的独立控制台（不属于 WeComPersonalRpaClient.sln 发布产物，
// 不加入解决方案），见 clients/wecom-personal-rpa/scripts/run-probe.ps1 与
// docs/准入验证手册.md。探测只读，不发送任何输入到企微。
//
// 任何生产工程（Client.App / Client.Supervisor / Client.Tests）都不应依赖这些内部类型；
// 若需要正式对外暴露某类型，请先在 Contracts/ 下抽象公开接口，再删除这里的暴露。
// ============================================================================

using System.Runtime.CompilerServices;

[assembly: InternalsVisibleTo("Client.Probe")]
