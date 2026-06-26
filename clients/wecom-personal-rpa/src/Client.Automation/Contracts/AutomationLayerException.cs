namespace WeCom.PersonalRpa.Automation.Contracts;

/// <summary>
/// 自动化层（Win32 P/Invoke）不可恢复异常。
/// 任意自动化子层抛出此异常表示「这一层已无法继续」，由调用方上报 PausedError。
/// </summary>
public class AutomationLayerException : Exception
{
    /// <summary>脱敏错误码（不包含绝对路径 / 密钥）。</summary>
    public string Layer { get; }

    public AutomationLayerException(string layer, string message)
        : base(message)
    {
        Layer = layer;
    }

    public AutomationLayerException(string layer, string message, Exception inner)
        : base(message, inner)
    {
        Layer = layer;
    }
}
