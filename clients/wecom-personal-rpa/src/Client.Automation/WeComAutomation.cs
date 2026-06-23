using FlaUI.Core.AutomationElements;
using Serilog;
using WeCom.PersonalRpa.Automation.Contracts;
using WeCom.PersonalRpa.Automation.Vision;
using WeCom.PersonalRpa.Automation.WeCom;
using WeCom.PersonalRpa.Automation.Win32;

namespace WeCom.PersonalRpa.Automation;

/// <summary>
/// 企微自动化门面（IWeComAutomation 实现）。
/// 阶段 2B 重构后：视觉定位（IVisionLocator）为主路径，InputExecutor 负责坐标点击 + 剪贴板粘贴，
/// ClipboardGuard 负责剪贴板备份/还原。ActionLocator 保留为兜底类型（项目维护者后续决定是否清理）。
/// </summary>
public sealed class WeComAutomation : IWeComAutomation
{
    private readonly INodesConfig _nodes;
    private readonly FlaUi.FlaUiDriver _flaUi;
    private readonly WeComMainWindow _mainWindow;
    private readonly TemplateMatcher _templates;
    private readonly ActionLocator _actionLocator; // 保留兜底（design §0.3）
    private readonly ConversationNavigator _navigator;
    private readonly ClipboardGuard _clipboardGuard;

    private readonly IVisionLocator _visionLocator;
    private readonly InputExecutor _inputExecutor;

    private bool _disposed;

    /// <summary>
    /// 兼容旧签名的构造（FlaUI/Template 路径，视觉路径不可用，所有视觉相关方法会失败）。
    /// 仅用于 DI 未注入视觉组件的过渡场景，正式环境请用带视觉注入的构造。
    /// </summary>
    public WeComAutomation(INodesConfig? nodes = null)
    {
        _nodes = nodes ?? new Nodes.WeComNodesConfig();

        _flaUi = new FlaUi.FlaUiDriver(_nodes.MainWindowClassName);
        _mainWindow = new WeComMainWindow(_nodes.MainWindowClassName);
        _templates = new TemplateMatcher(_nodes.TemplatesRoot);
        _templates.LoadTemplates();

        _actionLocator = new ActionLocator(_nodes, _flaUi, _mainWindow, _templates);
        _clipboardGuard = new ClipboardGuard();

        // 视觉路径缺省：null 占位。所有视觉相关方法会返回 false 并 log warning。
        // 真实生产由 Client.App 注入 QwenVisionLocator。
        _visionLocator = null!;
        _inputExecutor = null!;
        _navigator = null!;
    }

    /// <summary>
    /// 视觉路径主构造（阶段 2B 起的推荐入口）。
    /// </summary>
    /// <param name="visionLocator">视觉定位器（阶段 2A 产出 IVisionLocator）。</param>
    /// <param name="inputExecutor">屏幕输入执行器（bbox→坐标点击 + 剪贴板粘贴）。</param>
    /// <param name="clipboardGuard">剪贴板备份/还原守卫。</param>
    /// <param name="mainWindow">企微主窗口句柄封装（可选，便于测试注入）。</param>
    /// <param name="nodes">节点配置（兼容旧路径）。</param>
    public WeComAutomation(
        IVisionLocator visionLocator,
        InputExecutor inputExecutor,
        ClipboardGuard clipboardGuard,
        WeComMainWindow? mainWindow = null,
        INodesConfig? nodes = null)
    {
        _visionLocator = visionLocator ?? throw new ArgumentNullException(nameof(visionLocator));
        _inputExecutor = inputExecutor ?? throw new ArgumentNullException(nameof(inputExecutor));
        _clipboardGuard = clipboardGuard ?? throw new ArgumentNullException(nameof(clipboardGuard));
        _nodes = nodes ?? new Nodes.WeComNodesConfig();

        _flaUi = new FlaUi.FlaUiDriver(_nodes.MainWindowClassName);
        _mainWindow = mainWindow ?? new WeComMainWindow(_nodes.MainWindowClassName);
        _templates = new TemplateMatcher(_nodes.TemplatesRoot);
        _templates.LoadTemplates();

        _actionLocator = new ActionLocator(_nodes, _flaUi, _mainWindow, _templates);
        _navigator = new ConversationNavigator(_visionLocator, _inputExecutor, _mainWindow);
    }

    /// <inheritdoc />
    public bool IsAttached => _flaUi.IsAttached && _mainWindow.Handle != IntPtr.Zero;

    /// <inheritdoc />
    public bool AttachMainWindow()
    {
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
        if (_navigator is null)
        {
            Log.Warning("后端日志：WeComAutomation 未注入视觉定位器，NavigateToConversation 不可用");
            return new ConversationNavigateResult
            {
                Success = false,
                NeedsReview = false,
                ErrorCode = "automation_layer_error",
            };
        }
        return _navigator.Navigate(keyword);
    }

    /// <inheritdoc />
    public bool SendText(string text)
    {
        if (string.IsNullOrEmpty(text))
        {
            return false;
        }

        if (_visionLocator is null || _inputExecutor is null)
        {
            Log.Warning("后端日志：WeComAutomation 未注入视觉定位器，SendText 不可用");
            return false;
        }

        // 阶段 2B：SendText 仅负责对已激活会话发文本（不再内嵌导航，调用方需先 NavigateToConversation）。
        // 流程：定位消息输入框 → 点击 → 剪贴板备份 → 写入文本（不按 Enter）→ 还原剪贴板。
        try
        {
            var origin = ResolveWindowOrigin();
            if (origin is null)
            {
                Log.Warning("后端日志：WeComAutomation.SendText 无法解析主窗口原点，跳过");
                return false;
            }

            var inputProbe = _visionLocator.LocateAsync("input", "消息输入框").GetAwaiter().GetResult();
            if (inputProbe is null)
            {
                Log.Warning("后端日志：WeComAutomation.SendText 定位消息输入框失败（elementType=input, kw=消息输入框）");
                return false;
            }

            _inputExecutor.ClickElement(inputProbe.Bbox, origin.Value);
            Thread.Sleep(400);

            _clipboardGuard.BackupAndEmpty();
            try
            {
                _inputExecutor.TypeText(text, pressEnterAfter: false);
            }
            finally
            {
                _clipboardGuard.Restore();
            }

            Log.Information("后端日志：WeComAutomation.SendText 完成 text_length={Len}", text.Length);
            return true;
        }
        catch (Exception ex)
        {
            Log.Warning(ex, "后端日志：WeComAutomation.SendText 异常");
            return false;
        }
    }

    /// <inheritdoc />
    public bool SendImage(string localImagePath) => SendFileInternal(localImagePath);

    /// <inheritdoc />
    public bool SendFile(string localFilePath) => SendFileInternal(localFilePath);

    private bool SendFileInternal(string localFilePath)
    {
        if (string.IsNullOrEmpty(localFilePath) || !File.Exists(localFilePath))
        {
            return false;
        }

        if (_visionLocator is null || _inputExecutor is null)
        {
            Log.Warning("后端日志：WeComAutomation 未注入视觉定位器，SendFile 不可用");
            return false;
        }

        try
        {
            var origin = ResolveWindowOrigin();
            if (origin is null)
            {
                Log.Warning("后端日志：WeComAutomation.SendFile 无法解析主窗口原点，跳过");
                return false;
            }

            var inputProbe = _visionLocator.LocateAsync("input", "消息输入框").GetAwaiter().GetResult();
            if (inputProbe is null)
            {
                Log.Warning("后端日志：WeComAutomation.SendFile 定位消息输入框失败");
                return false;
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

            return true;
        }
        catch (Exception ex)
        {
            Log.Warning(ex, "后端日志：WeComAutomation.SendFile 异常");
            return false;
        }
    }

    /// <summary>
    /// 解析企微主窗口左上角屏幕坐标，供 InputExecutor.ClickElement 用。
    /// 窗口未就绪返回 null。
    /// </summary>
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
            Log.Warning(ex, "后端日志：解析主窗口原点失败");
            return null;
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
