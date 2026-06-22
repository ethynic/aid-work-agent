using System.Windows;
using System.Windows.Media.Imaging;
using Microsoft.Extensions.DependencyInjection;
using Serilog;
using WeCom.PersonalRpa.App.Services;

namespace WeCom.PersonalRpa.App.Views;

/// <summary>
/// 二维码登录窗口。展示 <see cref="LoginStateDetector"/> 采集到的二维码图片。
/// 后台周期轮询采集结果，更新 <see cref="QrImage"/>。
/// </summary>
public partial class LoginQrWindow : Window
{
    private new const string Tag = "LoginQrWindow";
    private readonly LoginStateDetector _detector;
    private System.Windows.Threading.DispatcherTimer? _timer;

    public LoginQrWindow(LoginStateDetector detector)
    {
        _detector = detector;
        InitializeComponent();
        Loaded += OnLoaded;
        Unloaded += OnUnloaded;
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        _timer = new System.Windows.Threading.DispatcherTimer
        {
            Interval = TimeSpan.FromSeconds(2),
        };
        _timer.Tick += (_, _) => RefreshQr();
        _timer.Start();
        RefreshQr();
    }

    private void OnUnloaded(object sender, RoutedEventArgs e)
    {
        _timer?.Stop();
    }

    private void RefreshQr()
    {
        try
        {
            var bytes = _detector.LastQrImageBytes;
            if (bytes is null || bytes.Length == 0)
            {
                HintText.Text = "等待二维码采集...";
                QrImage.Source = null;
                return;
            }

            var image = new BitmapImage();
            using (var ms = new System.IO.MemoryStream(bytes))
            {
                image.BeginInit();
                image.CacheOption = BitmapCacheOption.OnLoad;
                image.StreamSource = ms;
                image.EndInit();
            }
            image.Freeze();
            QrImage.Source = image;
            HintText.Text = "请使用企业微信扫码";
        }
        catch (Exception ex)
        {
            Log.Warning(ex, "[{Tag}] 渲染二维码失败", Tag);
            HintText.Text = "二维码渲染失败，请刷新";
        }
    }

    private async void RefreshButton_Click(object sender, RoutedEventArgs e)
    {
        HintText.Text = "正在重新采集二维码...";
        try
        {
            if (App.Services is { } sp)
            {
                var host = sp.GetRequiredService<RpaHost>();
                await host.ReloginAsync();
            }
        }
        catch (Exception ex)
        {
            Log.Warning(ex, "[{Tag}] 手动刷新二维码失败", Tag);
        }
        RefreshQr();
    }
}
