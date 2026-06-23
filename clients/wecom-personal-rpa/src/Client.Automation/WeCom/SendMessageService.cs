using Serilog;
using WeCom.PersonalRpa.Automation.Contracts;
using WeCom.PersonalRpa.Automation.Vision;
using WeCom.PersonalRpa.Automation.Win32;

namespace WeCom.PersonalRpa.Automation.WeCom;

/// <summary>
/// 消息发送服务（IActionExecutor 的核心实现，阶段 2B 视觉路径重构）。
/// 流程：导航会话 → 视觉定位消息输入框 → 点击 → 剪贴板备份 → 写入文本/文件 → 粘贴 → 发送按钮（文本）→ 恢复剪贴板。
/// 仅在 ClientState.Running 下被调用（调用方保证）。
/// </summary>
public sealed class SendMessageService : IActionExecutor
{
    private readonly ConversationNavigator _navigator;
    private readonly IVisionLocator _visionLocator;
    private readonly InputExecutor _inputExecutor;
    private readonly ClipboardGuard _clipboardGuard;
    private readonly WeComMainWindow _mainWindow;

    public SendMessageService(
        ConversationNavigator navigator,
        IVisionLocator visionLocator,
        InputExecutor inputExecutor,
        ClipboardGuard clipboardGuard,
        WeComMainWindow mainWindow)
    {
        _navigator = navigator ?? throw new ArgumentNullException(nameof(navigator));
        _visionLocator = visionLocator ?? throw new ArgumentNullException(nameof(visionLocator));
        _inputExecutor = inputExecutor ?? throw new ArgumentNullException(nameof(inputExecutor));
        _clipboardGuard = clipboardGuard ?? throw new ArgumentNullException(nameof(clipboardGuard));
        _mainWindow = mainWindow ?? throw new ArgumentNullException(nameof(mainWindow));
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

                var origin = ResolveWindowOrigin();
                if (origin is null)
                {
                    return new ActionExecResult
                    {
                        Success = false,
                        ErrorCode = "automation_layer_error",
                        ErrorMessage = "主窗口未就绪",
                    };
                }

                // 定位消息输入框并点击
                var inputProbe = _visionLocator.LocateAsync("input", "消息输入框").GetAwaiter().GetResult();
                if (inputProbe is null)
                {
                    Log.Warning("后端日志：SendText 定位消息输入框失败（elementType=input, kw=消息输入框）");
                    return new ActionExecResult
                    {
                        Success = false,
                        ErrorCode = "vision_locate_failed",
                        ErrorMessage = "消息输入框定位失败",
                    };
                }
                _inputExecutor.ClickElement(inputProbe.Bbox, origin.Value);
                Thread.Sleep(400);

                // 剪贴板备份 → 写入文本（不按 Enter）→ 还原
                _clipboardGuard.BackupAndEmpty();
                try
                {
                    _inputExecutor.TypeText(text, pressEnterAfter: false);
                }
                finally
                {
                    _clipboardGuard.Restore();
                }

                // 定位发送按钮并点击
                var sendProbe = _visionLocator.LocateAsync("button", "发送").GetAwaiter().GetResult();
                if (sendProbe is null)
                {
                    Log.Warning("后端日志：SendText 定位发送按钮失败（elementType=button, kw=发送），text_length={Len}", text.Length);
                    return new ActionExecResult
                    {
                        Success = false,
                        ErrorCode = "vision_locate_failed",
                        ErrorMessage = "发送按钮定位失败",
                    };
                }
                _inputExecutor.ClickElement(sendProbe.Bbox, origin.Value);

                Log.Information("后端日志：SendText 成功 conversationKey={Key} text_length={Len}",
                    conversationKey, text.Length);
                return new ActionExecResult { Success = true };
            }
            catch (AutomationLayerException ex)
            {
                Log.Warning(ex, "后端日志：SendText 失败 Layer={Layer}", ex.Layer);
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
        => await SendFileAsync(conversationKey, localImagePath, cancellationToken);

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

                var origin = ResolveWindowOrigin();
                if (origin is null)
                {
                    return new ActionExecResult
                    {
                        Success = false,
                        ErrorCode = "automation_layer_error",
                        ErrorMessage = "主窗口未就绪",
                    };
                }

                var inputProbe = _visionLocator.LocateAsync("input", "消息输入框").GetAwaiter().GetResult();
                if (inputProbe is null)
                {
                    Log.Warning("后端日志：SendFile 定位消息输入框失败");
                    return new ActionExecResult
                    {
                        Success = false,
                        ErrorCode = "vision_locate_failed",
                        ErrorMessage = "消息输入框定位失败",
                    };
                }
                _inputExecutor.ClickElement(inputProbe.Bbox, origin.Value);
                Thread.Sleep(400);

                _clipboardGuard.BackupAndEmpty();
                try
                {
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
                Log.Warning(ex, "后端日志：SendFile 失败 Layer={Layer}", ex.Layer);
                return new ActionExecResult
                {
                    Success = false,
                    ErrorCode = "automation_layer_error",
                    ErrorMessage = "自动化层不可用",
                };
            }
        }, cancellationToken);
    }

    private (int Left, int Top)? ResolveWindowOrigin()
    {
        try
        {
            if (_mainWindow.Handle == IntPtr.Zero)
            {
                _mainWindow.TryFind();
            }
            if (_mainWindow.Handle == IntPtr.Zero)
            {
                return null;
            }
            var (left, top, _, _) = _mainWindow.GetRect();
            return (left, top);
        }
        catch (Exception ex)
        {
            Log.Warning(ex, "后端日志：SendMessageService 解析主窗口原点失败");
            return null;
        }
    }
}
