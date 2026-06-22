using Serilog;
using WeCom.PersonalRpa.Automation.Contracts;
using WeCom.PersonalRpa.Core.Protocol;
using WeCom.PersonalRpa.Core.StateMachine;

namespace WeCom.PersonalRpa.App.Services;

/// <summary>
/// 登录态检测与二维码采集。
///
/// 流程：
///   1) 通过 <see cref="IWeComAutomation.AttachMainWindow"/> + DesktopState 判断是否需要扫码登录
///   2) 需登录时，截取二维码区域（OpenCvSharp 模板定位），上报 status=need_login + qr_image_ref
///   3) 推送 <see cref="Views.LoginQrWindow"/> 展示二维码给用户扫码
///
/// 二维码是短期凭证：protocol §A.4 规定日志禁止打印 qr_image_ref 内容。
/// </summary>
public sealed class LoginStateDetector
{
    private const string Tag = "LoginStateDetector";

    private readonly IWeComAutomation _automation;
    private readonly InboundReporter _reporter;
    private readonly IStateManager _state;
    private readonly ClientSession _session;
    private byte[]? _lastQrBytes;

    public LoginStateDetector(IWeComAutomation automation, InboundReporter reporter, IStateManager state, ClientSession session)
    {
        _automation = automation;
        _reporter = reporter;
        _state = state;
        _session = session;
    }

    /// <summary>最近一次采集的二维码图片字节（供 LoginQrWindow 绑定）。</summary>
    public byte[]? LastQrImageBytes => _lastQrBytes;

    /// <summary>检测当前是否需要登录；若需要则采集二维码并上报。</summary>
    public async Task<bool> DetectAndCollectAsync(CancellationToken ct)
    {
        var needLogin = await DetectNeedLoginAsync(ct);
        if (!needLogin)
        {
            _lastQrBytes = null;
            return false;
        }

        _lastQrBytes = await CaptureQrAsync(ct);
        var payload = new StatusPayload
        {
            Status = AccountStatus.NeedLogin,
            AccountDisplayName = _session.AccountDisplayName,
            Detail = _lastQrBytes is null ? "二维码采集失败" : "二维码已展示，等待扫码",
            QrImageRef = _lastQrBytes is null ? null : $"tmp://qr/{Guid.NewGuid():N}.png",
        };
        await _reporter.ReportStatusAsync(payload, _session.AccountId, ct);

        try { _state.TransitionTo(ClientState.NeedLogin, errorMessage: "需要扫码登录"); }
        catch (Exception ex) { Log.Warning(ex, "[{Tag}] 迁移 NeedLogin 失败", Tag); }

        Log.Information("[{Tag}] 已采集二维码并展示（引用已上报）", Tag);
        return true;
    }

    /// <summary>占位：检测企微是否处于登录态。</summary>
    private Task<bool> DetectNeedLoginAsync(CancellationToken ct)
    {
        // TODO: 委托 _automation.AttachMainWindow()，若登录窗口存在则 need_login=true
        return Task.FromResult(false);
    }

    /// <summary>占位：采集二维码图像字节。</summary>
    private Task<byte[]?> CaptureQrAsync(CancellationToken ct)
    {
        // TODO: 截取 INodesConfig.QrRegion 区域图像（OpenCvSharp）
        return Task.FromResult<byte[]?>(null);
    }
}
