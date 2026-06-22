namespace WeCom.PersonalRpa.Core.StateMachine;

/// <summary>
/// 单账号运行时会话（protocol.md §C.4，Core 定义 / App 实现）。
/// 承载当前状态、账号信息、最后错误。
/// </summary>
public sealed class ClientSession
{
    private readonly object _lock = new();

    /// <summary>当前状态（线程安全读写）。</summary>
    public ClientState CurrentState { get; private set; } = ClientState.Starting;

    /// <summary>账号 ID。</summary>
    public string AccountId { get; set; } = string.Empty;

    /// <summary>账号显示名（在线 / 扫码时设置）。</summary>
    public string? AccountDisplayName { get; set; }

    /// <summary>最后错误码（脱敏），进入 PausedError 时设置。</summary>
    public string? LastErrorCode { get; set; }

    /// <summary>最后错误说明（脱敏），进入 PausedError 时设置。</summary>
    public string? LastErrorMessage { get; set; }

    /// <summary>最近一次心跳时间。</summary>
    public DateTimeOffset LastHeartbeatAt { get; set; }

    /// <summary>线程安全地设置当前状态。</summary>
    public void SetState(ClientState state)
    {
        lock (_lock)
        {
            CurrentState = state;
        }
    }

    /// <summary>线程安全地读取当前状态。</summary>
    public ClientState GetState()
    {
        lock (_lock)
        {
            return CurrentState;
        }
    }
}
