using FlaUI.Core.AutomationElements;
using Serilog;
using WeCom.PersonalRpa.Automation.Contracts;
using WeCom.PersonalRpa.Automation.Win32;

namespace WeCom.PersonalRpa.Automation.WeCom;

/// <summary>
/// 消息发送服务（IActionExecutor 的核心实现）。
/// 流程：定位会话 → 聚焦消息输入框 → 备份剪贴板 → 写入文本 / 文件 → 粘贴（Ctrl+V）→ 回车发送 → 恢复剪贴板。
/// 仅在 ClientState.Running 下被调用（调用方保证）。
/// </summary>
public sealed class SendMessageService : IActionExecutor
{
    private readonly ConversationNavigator _navigator;
    private readonly ActionLocator _actionLocator;
    private readonly ClipboardGuard _clipboardGuard;

    internal SendMessageService(
        ConversationNavigator navigator,
        ActionLocator actionLocator,
        ClipboardGuard clipboardGuard)
    {
        _navigator = navigator ?? throw new ArgumentNullException(nameof(navigator));
        _actionLocator = actionLocator ?? throw new ArgumentNullException(nameof(actionLocator));
        _clipboardGuard = clipboardGuard ?? throw new ArgumentNullException(nameof(clipboardGuard));
    }

    /// <inheritdoc />
    public async Task<ActionExecResult> SendTextAsync(string conversationKey, string text, CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrEmpty(text))
        {
            return new ActionExecResult { Success = false, ErrorCode = "bad_request", ErrorMessage = "文本为空" };
        }

        return await Task.Run(() =>
        {
            try
            {
                var nav = _navigator.Navigate(conversationKey);
                if (!nav.Success)
                {
                    return new ActionExecResult
                    {
                        Success = false,
                        ErrorCode = nav.ErrorCode ?? "navigate_failed",
                        ErrorMessage = nav.NeedsReview ? "会话需人工绑定" : "会话定位失败",
                        NeedsReview = nav.NeedsReview,
                    };
                }

                _actionLocator.LocateMessageInput();

                _clipboardGuard.BackupAndEmpty();
                try
                {
                    Win32Input.SendTextViaClipboard(text);
                }
                finally
                {
                    _clipboardGuard.Restore();
                }

                return new ActionExecResult { Success = true };
            }
            catch (AutomationLayerException ex)
            {
                Log.Warning(ex, "后端日志：SendText 失败，Layer={Layer}", ex.Layer);
                return new ActionExecResult
                {
                    Success = false,
                    ErrorCode = "automation_layer_error",
                    ErrorMessage = "自动化层不可用",
                };
            }
        }, cancellationToken);
    }

    /// <inheritdoc />
    public async Task<ActionExecResult> SendImageAsync(string conversationKey, string localImagePath, CancellationToken cancellationToken = default)
    {
        return await SendFileAsync(conversationKey, localImagePath, cancellationToken);
    }

    /// <inheritdoc />
    public async Task<ActionExecResult> SendFileAsync(string conversationKey, string localFilePath, CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrEmpty(localFilePath) || !File.Exists(localFilePath))
        {
            return new ActionExecResult
            {
                Success = false,
                ErrorCode = "bad_request",
                ErrorMessage = "文件不存在",
            };
        }

        return await Task.Run(() =>
        {
            try
            {
                var nav = _navigator.Navigate(conversationKey);
                if (!nav.Success)
                {
                    return new ActionExecResult
                    {
                        Success = false,
                        ErrorCode = nav.ErrorCode ?? "navigate_failed",
                        ErrorMessage = nav.NeedsReview ? "会话需人工绑定" : "会话定位失败",
                        NeedsReview = nav.NeedsReview,
                    };
                }

                // 文件发送：定位输入框 → 剪贴板放置文件 drop → Ctrl+V → 回车。
                _actionLocator.LocateMessageInput();

                _clipboardGuard.BackupAndEmpty();
                try
                {
                    // 把本地文件以 drop 形式写入剪贴板（CF_HDROP）。
                    ClipboardFileDrop.SetFileDrop(new[] { localFilePath });
                    Win32Input.SendPaste();
                    Win32Input.SendEnter();
                }
                finally
                {
                    _clipboardGuard.Restore();
                }

                return new ActionExecResult { Success = true };
            }
            catch (AutomationLayerException ex)
            {
                Log.Warning(ex, "后端日志：SendFile 失败，Layer={Layer}", ex.Layer);
                return new ActionExecResult
                {
                    Success = false,
                    ErrorCode = "automation_layer_error",
                    ErrorMessage = "自动化层不可用",
                };
            }
        }, cancellationToken);
    }
}
