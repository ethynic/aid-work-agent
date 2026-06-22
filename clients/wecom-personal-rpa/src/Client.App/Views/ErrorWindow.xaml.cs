using System.Diagnostics;
using System.IO;
using System.Windows;
using Serilog;

namespace WeCom.PersonalRpa.App.Views;

/// <summary>
/// 错误窗口。展示脱敏错误信息与堆栈，提供"导出诊断包"按钮。
/// 诊断包首版为占位：将日志目录打包为 zip 的实现在阶段4补充（TODO）。
/// </summary>
public partial class ErrorWindow : Window
{
    private new const string Tag = "ErrorWindow";

    public ErrorWindow()
    {
        InitializeComponent();
        DetailText.Text = "(无详细信息)";
    }

    public ErrorWindow(string message, Exception? ex) : this()
    {
        MessageText.Text = message;
        DetailText.Text = BuildSafeDetail(ex);
    }

    /// <summary>构建脱敏错误详情（剔除绝对路径/密钥，对齐 backend_dev.md）。</summary>
    private static string BuildSafeDetail(Exception? ex)
    {
        if (ex is null) return "(无异常对象)";
        try
        {
            var text = ex.ToString();
            // 剔除 Windows 绝对路径
            text = System.Text.RegularExpressions.Regex.Replace(
                text, @"[A-Za-z]:\\[^\s""']+", "<path>");
            return text;
        }
        catch
        {
            return ex.GetType().FullName ?? "Unknown";
        }
    }

    /// <summary>导出诊断包（占位）：当前仅打开日志目录，zip 打包 TODO。</summary>
    private void Export_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            // TODO: 将 LogDirectory + DataDirectory 下的脱敏快照打包为 zip 放到桌面
            //       诊断包内容需经脱敏（剔除 secret/token/signature/绝对路径）
            Directory.CreateDirectory(App.LogDirectory);
            Process.Start(new ProcessStartInfo("explorer.exe", App.LogDirectory) { UseShellExecute = true });
            Log.Information("[{Tag}] 用户请求导出诊断包（当前占位：打开日志目录）", Tag);
        }
        catch (Exception ex)
        {
            Log.Error(ex, "[{Tag}] 导出诊断包失败", Tag);
        }
    }

    private void Close_Click(object sender, RoutedEventArgs e) => Close();
}
