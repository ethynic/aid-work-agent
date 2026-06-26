// ============================================================================
// 仅诊断可见性：把 Win32 内部类型（DesktopState / NativeMethods 等）暴露给测试工程复用，
// 避免复制粘贴 P/Invoke 与窗口枚举逻辑。
//
// Phase 1 清理：移除了 Client.VisionRegression（视觉诊断工具，已退役）的 InternalsVisibleTo。
//
// 任何生产工程（Client.App / Client.Supervisor）都不应依赖这些内部类型；
// 若需要正式对外暴露某类型，请先在 Contracts/ 下抽象公开接口，再删除这里的暴露。
// ============================================================================

using System.Runtime.CompilerServices;

[assembly: InternalsVisibleTo("Client.Tests")]
