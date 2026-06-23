// ============================================================================
// 仅诊断可见性：把 Win32 / WeCom 内部类型（DesktopState / WeComMainWindow /
// NativeMethods 等）暴露给按需运行的诊断工具复用，避免复制粘贴 P/Invoke 与窗口枚举逻辑。
//
// 当前可见的诊断工具（都不加入 WeComPersonalRpaClient.sln，不属发布产物）：
//   - Client.VisionRegression：真机视觉回归（PowerShell 截图 + Qwen3-VL 视觉定位验证），
//     见 scripts/run-vision-regression.ps1
//
// 探测均只读：只截图 / 跑视觉定位，不发送任何键鼠或剪贴板输入到企微。
//
// 任何生产工程（Client.App / Client.Supervisor / Client.Tests）都不应依赖这些内部类型；
// 若需要正式对外暴露某类型，请先在 Contracts/ 下抽象公开接口，再删除这里的暴露。
// ============================================================================

using System.Runtime.CompilerServices;

[assembly: InternalsVisibleTo("Client.VisionRegression")]
[assembly: InternalsVisibleTo("Client.Tests")]
