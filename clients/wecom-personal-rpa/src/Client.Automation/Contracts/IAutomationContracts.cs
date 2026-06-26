using WeCom.PersonalRpa.Core.Protocol;

namespace WeCom.PersonalRpa.Automation.Contracts;

// ============================================================================
// 本文件定义 Client.Automation 工程所需的跨工程接口抽象（Phase 2 PowerShell 改造后保留）。
//
// Phase 1 清理：删除了 INodesConfig / IHealthSupervisor / Point / Rect（视觉/UIA 专用，
//   随 UIA3 节点 yaml 一起退役）。保留 IWeComAutomation / IActionExecutor /
//   IClipboardGuard / ConversationNavigateResult / ActionExecResult 作为 Phase 2
//   PowerShell 后端的契约骨架。
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
/// Phase 2 由 PowerShell 后端实现。
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
