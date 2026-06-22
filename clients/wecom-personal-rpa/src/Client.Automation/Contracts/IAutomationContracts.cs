using WeCom.PersonalRpa.Core.Protocol;

namespace WeCom.PersonalRpa.Automation.Contracts;

// ============================================================================
// 本文件定义 Client.Automation 工程所需的跨工程接口抽象。
//
// 背景：protocol.md §C.4 规定 IWeComAutomation / IActionExecutor / IHealthSupervisor /
//   IClipboardGuard / INodesConfig 由 Client.Core 定义、由本工程（Automation）实现。
//   但在并行开发阶段，Core 工程的接口定义文件由另一个 agent 同步落地；
//   为保证本工程独立可编译、且实现类名逐字一致（FlaUiDriver / SendMessageService /
//   HealthSupervisor / ClipboardGuard / WeComNodesConfig），本文件先在 Automation 命名空间
//   下给出等价的接口契约（成员签名与设计文档 / protocol.md §C.4 对齐）。
//
// TODO（Core 接口落地后）：把以下接口的命名空间从 WeCom.PersonalRpa.Automation.Contracts
//   迁回 WeCom.PersonalRpa.Core，并删除本文件；实现类仅改 using，类名与成员不变。
// ============================================================================

/// <summary>
/// 单个出站动作执行器（protocol.md §C.4：Core 定义 / Automation 实现）。
/// 仅在 ClientState.Running 状态下由 ClientSession 调用。
/// </summary>
public interface IActionExecutor
{
    /// <summary>发送文本消息。</summary>
    /// <param name="conversationKey">会话定位键（搜索关键词 / 稳定 ID）。</param>
    /// <param name="text">待发送文本。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>是否成功；失败时 <paramref name="errorCode"/> 填充脱敏错误码。</returns>
    Task<ActionExecResult> SendTextAsync(string conversationKey, string text, CancellationToken cancellationToken = default);

    /// <summary>发送图片（本地路径）。</summary>
    Task<ActionExecResult> SendImageAsync(string conversationKey, string localImagePath, CancellationToken cancellationToken = default);

    /// <summary>发送文件（本地路径）。</summary>
    Task<ActionExecResult> SendFileAsync(string conversationKey, string localFilePath, CancellationToken cancellationToken = default);
}

/// <summary>动作执行结果（脱敏）。</summary>
public sealed class ActionExecResult
{
    public bool Success { get; init; }
    public string? ErrorCode { get; init; }
    public string? ErrorMessage { get; init; }
    public bool NeedsReview { get; init; }
}

/// <summary>
/// 企微自动化三层定位与会话操作（protocol.md §C.4：Core 定义 / Automation 实现）。
/// 封装 FlaUI/UIA3 + Win32 坐标 + OpenCV 模板匹配的降级链。
/// </summary>
public interface IWeComAutomation : IDisposable
{
    /// <summary>当前主窗口是否已附加（FlaUI session 可用）。</summary>
    bool IsAttached { get; }

    /// <summary>附加到企微主窗口；失败抛 <see cref="AutomationLayerException"/>。</summary>
    bool AttachMainWindow();

    /// <summary>在会话搜索框中输入关键词，定位会话；歧义返回 NeedsReview=true。</summary>
    ConversationNavigateResult NavigateToConversation(string keyword);

    /// <summary>对已激活会话发送文本（走剪贴板 + Ctrl+V + Enter，经 ClipboardGuard）。</summary>
    bool SendText(string text);

    /// <summary>对已激活会话发送图片（本地路径）。</summary>
    bool SendImage(string localImagePath);

    /// <summary>对已激活会话发送文件（本地路径）。</summary>
    bool SendFile(string localFilePath);
}

/// <summary>会话定位结果。</summary>
public sealed class ConversationNavigateResult
{
    public bool Success { get; init; }
    public bool NeedsReview { get; init; }
    public string? CandidateDisplayName { get; init; }
    public string? ErrorCode { get; init; }
}

/// <summary>
/// 输入独占：在执行自动化操作期间，备份/清空/恢复剪贴板，
/// 避免与用户其它复制行为互相覆盖（protocol.md §C.4：Core 定义 / Automation 实现）。
/// </summary>
public interface IClipboardGuard : IDisposable
{
    /// <summary>备份当前剪贴板内容（文本 + 文件 drop），随后清空。</summary>
    void BackupAndEmpty();

    /// <summary>恢复 <see cref="BackupAndEmpty"/> 时备份的内容。</summary>
    void Restore();
}

/// <summary>
/// 监督 App 存活 / 拉起 / 离线上报（protocol.md §C.4：Core 定义 / Supervisor 或 Automation 实现）。
/// 本工程提供组合 DesktopState + WeComMainWindow + LoginStateDetector 的实现，
/// 产出 <see cref="StatusPayload"/>。
/// </summary>
public interface IHealthSupervisor
{
    /// <summary>采一次当前账号 / 桌面健康，产出可上报的 StatusPayload。</summary>
    StatusPayload CaptureStatus();
}

/// <summary>
/// 节点配置契约：窗口 / 控件 AutomationId、Name、坐标 offset、模板路径、阈值
/// （protocol.md §C.4：Core 定义 / Core 实现；Automation 实现一份用于本地绑定）。
/// </summary>
public interface INodesConfig
{
    /// <summary>企微主窗口类名（如 WeWorkWindow）。</summary>
    string MainWindowClassName { get; }

    /// <summary>搜索框 AutomationId（FlaUI 定位）。</summary>
    string SearchBoxAutomationId { get; }

    /// <summary>消息输入框 AutomationId。</summary>
    string MessageInputAutomationId { get; }

    /// <summary>发送按钮 Name。</summary>
    string SendButtonName { get; }

    /// <summary>二维码区域截图 offset（相对主窗口左上角的 x,y,w,h）。</summary>
    Rect QrRegion { get; }

    /// <summary>搜索框点击坐标 offset（Win32 降级路径用，相对主窗口左上角）。</summary>
    Point SearchBoxClickOffset { get; }

    /// <summary>消息输入框点击坐标 offset。</summary>
    Point MessageInputClickOffset { get; }

    /// <summary>模板匹配默认阈值（0..1）。</summary>
    double TemplateMatchThreshold { get; }

    /// <summary>模板根目录（相对工程 assets/templates/）。</summary>
    string TemplatesRoot { get; }
}

/// <summary>简单 2D 点。</summary>
public readonly struct Point
{
    public Point(int x, int y) { X = x; Y = y; }
    public int X { get; }
    public int Y { get; }
}

/// <summary>简单矩形。</summary>
public readonly struct Rect
{
    public Rect(int x, int y, int width, int height) { X = x; Y = y; Width = width; Height = height; }
    public int X { get; }
    public int Y { get; }
    public int Width { get; }
    public int Height { get; }
}
