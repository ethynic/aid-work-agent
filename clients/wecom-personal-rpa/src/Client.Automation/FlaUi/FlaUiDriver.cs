using FlaUI.Core;
using FlaUI.Core.AutomationElements;
using FlaUI.Core.Definitions;
using FlaUI.UIA3;
using WeCom.PersonalRpa.Automation.Contracts;

namespace WeCom.PersonalRpa.Automation.FlaUi;

/// <summary>
/// FlaUI/UIA3 驱动：主窗口附加、元素查找、点击 / 聚焦 / 取文本。
/// 是 <see cref="WeCom.ActionLocator"/> 三层降级链的第一层（最稳）。
/// 任何 FlaUI 层不可恢复错误都抛 <see cref="AutomationLayerException"/>，由上层降级处理。
/// </summary>
public sealed class FlaUiDriver : IDisposable
{
    private readonly object _lock = new();
    private readonly string _mainWindowClassName;
    private UIA3Automation? _automation;
    private Window? _mainWindow;
    private bool _disposed;

    /// <summary>
    /// 构造驱动。
    /// </summary>
    /// <param name="mainWindowClassName">企微主窗口类名（由 INodesConfig.MainWindowClassName 提供）。</param>
    public FlaUiDriver(string mainWindowClassName)
    {
        _mainWindowClassName = string.IsNullOrWhiteSpace(mainWindowClassName)
            ? "WeWorkWindow"
            : mainWindowClassName;
    }

    /// <summary>当前是否已附加到企微主窗口。</summary>
    public bool IsAttached
    {
        get
        {
            lock (_lock)
            {
                return _mainWindow is not null && _automation is not null;
            }
        }
    }

    /// <summary>当前附加的主窗口元素（可能为 null）。</summary>
    public Window? MainWindow
    {
        get
        {
            lock (_lock)
            {
                return _mainWindow;
            }
        }
    }

    /// <summary>
    /// 初始化 UIA3 session 并附加到企微主窗口。
    /// </summary>
    /// <returns>是否成功附加；找不到窗口返回 false，session 异常抛 AutomationLayerException。</returns>
    public bool AttachMainWindow()
    {
        lock (_lock)
        {
            EnsureAutomation();

            try
            {
                var desktop = _automation!.GetDesktop();
                // 按类名查找顶层窗口（企微主窗口类名 WeWorkWindow）。
                var condition = _automation.ConditionFactory.ByClassName(_mainWindowClassName);
                var element = desktop.FindFirstChild(condition);
                _mainWindow = element?.AsWindow();

                if (_mainWindow is null)
                {
                    // 退一步：枚举所有顶层窗口再按类名匹配（部分企微版本窗口不在 desktop 直接子级）。
                    foreach (var win in desktop.FindAllChildren(_automation.ConditionFactory.ByControlType(ControlType.Window)))
                    {
                        var asWin = win.AsWindow();
                        if (asWin != null && string.Equals(asWin.ClassName, _mainWindowClassName, StringComparison.Ordinal))
                        {
                            _mainWindow = asWin;
                            break;
                        }
                    }
                }

                return _mainWindow is not null;
            }
            catch (Exception ex) when (ex is not AutomationLayerException)
            {
                throw new AutomationLayerException("FlaUI.Attach", "附加企微主窗口失败", ex);
            }
        }
    }

    /// <summary>按 AutomationId 在主窗口范围内查找后代元素（任意层级）。</summary>
    public AutomationElement? FindElementByAutomationId(string automationId)
    {
        lock (_lock)
        {
            if (_mainWindow is null || _automation is null)
            {
                return null;
            }

            try
            {
                var cond = _automation.ConditionFactory.ByAutomationId(automationId);
                return _mainWindow.FindFirstDescendant(cond);
            }
            catch (Exception ex)
            {
                throw new AutomationLayerException("FlaUI.Find", $"按 AutomationId={automationId} 查找失败", ex);
            }
        }
    }

    /// <summary>按 Name 在主窗口范围内查找后代元素。</summary>
    public AutomationElement? FindElementByName(string name)
    {
        lock (_lock)
        {
            if (_mainWindow is null || _automation is null)
            {
                return null;
            }

            try
            {
                var cond = _automation.ConditionFactory.ByName(name);
                return _mainWindow.FindFirstDescendant(cond);
            }
            catch (Exception ex)
            {
                throw new AutomationLayerException("FlaUI.Find", $"按 Name={name} 查找失败", ex);
            }
        }
    }

    /// <summary>点击指定元素并等待焦点稳定。</summary>
    public void Click(AutomationElement element)
    {
        ArgumentNullException.ThrowIfNull(element);
        try
        {
            element.Click();
        }
        catch (Exception ex)
        {
            throw new AutomationLayerException("FlaUI.Click", "FlaUI 点击失败", ex);
        }
    }

    /// <summary>聚焦指定元素（输入框前置）。</summary>
    public void Focus(AutomationElement element)
    {
        ArgumentNullException.ThrowIfNull(element);
        try
        {
            element.Focus();
        }
        catch (Exception ex)
        {
            throw new AutomationLayerException("FlaUI.Focus", "FlaUI 聚焦失败", ex);
        }
    }

    /// <summary>取元素文本（TextBox / Label 的 Value 或 Name）。</summary>
    public string? GetText(AutomationElement element)
    {
        ArgumentNullException.ThrowIfNull(element);
        try
        {
            // 企微多数文本走 ValuePattern，回退到 Name。
            // FlaUI 4.x IValuePattern.Value 直接读属性；读失败（无焦点 / 未实现）回退 Name。
            if (element.Patterns.Value.IsSupported)
            {
                try
                {
                    var pattern = element.Patterns.Value.Pattern;
                    return pattern.Value ?? element.Name;
                }
                catch
                {
                    return element.Name;
                }
            }
            return element.Name;
        }
        catch (Exception ex)
        {
            throw new AutomationLayerException("FlaUI.GetText", "FlaUI 取文本失败", ex);
        }
    }

    /// <summary>向 TextBox 输入文本（模拟键入）。</summary>
    public void EnterText(TextBox textBox, string text)
    {
        ArgumentNullException.ThrowIfNull(textBox);
        if (string.IsNullOrEmpty(text))
        {
            return;
        }
        try
        {
            textBox.Text = text;
        }
        catch (Exception ex)
        {
            throw new AutomationLayerException("FlaUI.Enter", "FlaUI 文本输入失败", ex);
        }
    }

    private void EnsureAutomation()
    {
        if (_automation is null)
        {
            try
            {
                _automation = new UIA3Automation();
            }
            catch (Exception ex)
            {
                throw new AutomationLayerException("FlaUI.Init", "UIA3 session 初始化失败", ex);
            }
        }
    }

    public void Dispose()
    {
        lock (_lock)
        {
            if (_disposed)
            {
                return;
            }
            _disposed = true;
            // FlaUI 4.x Window / AutomationElement 不实现 IDisposable；仅释放 UIA3 session。
            _automation?.Dispose();
            _automation = null;
            _mainWindow = null;
        }
    }
}
