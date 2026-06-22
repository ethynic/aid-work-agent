using Serilog;
using WeCom.PersonalRpa.Automation.Contracts;
using WeCom.PersonalRpa.Core.Protocol;

namespace WeCom.PersonalRpa.App.Services.Stubs;

// ============================================================================
// 临时占位实现（仅 Client.App 编译/早期联调用）。
//
// 背景：protocol.md §C.4 规定 IActionExecutor / IWeComAutomation / IHealthSupervisor
//   由 Client.Automation 工程实现。并行开发阶段，Automation 的具体实现类尚未落地，
//   为保证 Client.App 可独立编译与启动，本文件提供最小可运行的占位实现。
//
// TODO（Automation 具体类落地后）：
//   1) 删除本文件；
//   2) 在 App.xaml.cs 的 ConfigureServices 改用 Automation 的真实实现注册。
// ============================================================================

/// <summary>IActionExecutor 占位实现。</summary>
public sealed class StubActionExecutor : IActionExecutor
{
    public Task<ActionExecResult> SendTextAsync(string conversationKey, string text, CancellationToken cancellationToken = default)
    {
        Log.Debug("[Stub] SendTextAsync Conv={Conv} Text={Text}", conversationKey, text);
        return Task.FromResult(new ActionExecResult { Success = true });
    }

    public Task<ActionExecResult> SendImageAsync(string conversationKey, string localImagePath, CancellationToken cancellationToken = default)
    {
        Log.Debug("[Stub] SendImageAsync Conv={Conv} Path={Path}", conversationKey, localImagePath);
        return Task.FromResult(new ActionExecResult { Success = true });
    }

    public Task<ActionExecResult> SendFileAsync(string conversationKey, string localFilePath, CancellationToken cancellationToken = default)
    {
        Log.Debug("[Stub] SendFileAsync Conv={Conv} Path={Path}", conversationKey, localFilePath);
        return Task.FromResult(new ActionExecResult { Success = true });
    }
}

/// <summary>IWeComAutomation 占位实现。</summary>
public sealed class StubWeComAutomation : IWeComAutomation
{
    public bool IsAttached => false;

    public bool AttachMainWindow()
    {
        Log.Debug("[Stub] AttachMainWindow");
        return false;
    }

    public ConversationNavigateResult NavigateToConversation(string keyword)
    {
        Log.Debug("[Stub] NavigateToConversation Key={Key}", keyword);
        return new ConversationNavigateResult { Success = false };
    }

    public bool SendText(string text) { Log.Debug("[Stub] SendText"); return false; }
    public bool SendImage(string localImagePath) { Log.Debug("[Stub] SendImage"); return false; }
    public bool SendFile(string localFilePath) { Log.Debug("[Stub] SendFile"); return false; }

    public void Dispose() { }
}

/// <summary>IHealthSupervisor 占位实现：始终返回 Online。</summary>
public sealed class StubHealthCapture : IHealthSupervisor
{
    public StatusPayload CaptureStatus()
    {
        Log.Debug("[Stub] CaptureStatus -> Online");
        return new StatusPayload { Status = AccountStatus.Online };
    }
}
