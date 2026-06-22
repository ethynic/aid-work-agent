using Serilog;
using WeCom.PersonalRpa.Automation.Contracts;
using WeCom.PersonalRpa.Automation.Vision;
using WeCom.PersonalRpa.Automation.WeCom;
using WeCom.PersonalRpa.Automation.Win32;

namespace WeCom.PersonalRpa.Automation;

/// <summary>
/// 企微自动化门面（IWeComAutomation 实现）。
/// 组合 FlaUiDriver / Win32 / TemplateMatcher / ActionLocator / ConversationNavigator /
/// ClipboardGuard / SendMessageService，对外提供统一的会话定位 + 消息发送入口。
/// </summary>
public sealed class WeComAutomation : IWeComAutomation
{
    private readonly INodesConfig _nodes;
    private readonly FlaUi.FlaUiDriver _flaUi;
    private readonly WeComMainWindow _mainWindow;
    private readonly TemplateMatcher _templates;
    private readonly ActionLocator _actionLocator;
    private readonly ConversationNavigator _navigator;
    private readonly ClipboardGuard _clipboardGuard;
    private bool _disposed;

    public WeComAutomation(INodesConfig? nodes = null)
    {
        _nodes = nodes ?? new Nodes.WeComNodesConfig();

        _flaUi = new FlaUi.FlaUiDriver(_nodes.MainWindowClassName);
        _mainWindow = new WeComMainWindow(_nodes.MainWindowClassName);
        _templates = new TemplateMatcher(_nodes.TemplatesRoot);
        _templates.LoadTemplates();

        _actionLocator = new ActionLocator(_nodes, _flaUi, _mainWindow, _templates);
        _navigator = new ConversationNavigator(_nodes, _flaUi);
        _clipboardGuard = new ClipboardGuard();
    }

    /// <inheritdoc />
    public bool IsAttached => _flaUi.IsAttached && _mainWindow.Handle != IntPtr.Zero;

    /// <inheritdoc />
    public bool AttachMainWindow()
    {
        // 双通道：FlaUI session + Win32 句柄都尝试就绪，保证后续降级链任一层可用。
        bool win32Ok = _mainWindow.TryFind();
        _mainWindow.BringToForeground();
        bool flaUiOk = _flaUi.AttachMainWindow();

        if (!win32Ok && !flaUiOk)
        {
            Log.Warning("后端日志：WeComAutomation.AttachMainWindow 两通道均失败");
            return false;
        }
        return true;
    }

    /// <inheritdoc />
    public ConversationNavigateResult NavigateToConversation(string keyword)
    {
        return _navigator.Navigate(keyword);
    }

    /// <inheritdoc />
    public bool SendText(string text)
    {
        if (string.IsNullOrEmpty(text))
        {
            return false;
        }

        _actionLocator.LocateMessageInput();

        _clipboardGuard.BackupAndEmpty();
        try
        {
            Win32Input.SendTextViaClipboard(text);
            return true;
        }
        catch (AutomationLayerException ex)
        {
            Log.Warning(ex, "后端日志：WeComAutomation.SendText 失败，Layer={Layer}", ex.Layer);
            return false;
        }
        finally
        {
            _clipboardGuard.Restore();
        }
    }

    /// <inheritdoc />
    public bool SendImage(string localImagePath)
    {
        return SendFileInternal(localImagePath);
    }

    /// <inheritdoc />
    public bool SendFile(string localFilePath)
    {
        return SendFileInternal(localFilePath);
    }

    private bool SendFileInternal(string localFilePath)
    {
        if (string.IsNullOrEmpty(localFilePath) || !File.Exists(localFilePath))
        {
            return false;
        }

        _actionLocator.LocateMessageInput();

        _clipboardGuard.BackupAndEmpty();
        try
        {
            ClipboardFileDrop.SetFileDrop(new[] { localFilePath });
            Win32Input.SendPaste();
            Win32Input.SendEnter();
            return true;
        }
        catch (AutomationLayerException ex)
        {
            Log.Warning(ex, "后端日志：WeComAutomation.SendFile 失败，Layer={Layer}", ex.Layer);
            return false;
        }
        finally
        {
            _clipboardGuard.Restore();
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        _clipboardGuard.Dispose();
        _templates.Dispose();
        _flaUi.Dispose();
    }
}
