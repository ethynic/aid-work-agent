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

    /// <summary>
    /// 暂停状态协调器（Phase 4 块 G）。null 表示未注入 PauseState（典型 Client.Core 单测场景），
    /// 此时 PauseAsync/ResumeAsync 走 ClientState-only 兜底（设状态位但无 PauseState 单例）。
    /// </summary>
    public PauseState? PauseState { get; set; }

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

    // ============================================================
    // Phase 4 块 G：服务端 paused / resumed 响应
    // ============================================================

    /// <summary>
    /// 服务端推送 paused 事件时调用（由 WebSocketConnectionManager 的事件转发）。
    /// 根据 scope 设置 PauseState 单例对应标志位 + ClientState。
    /// </summary>
    /// <param name="scope">暂停作用域。</param>
    /// <param name="conversationId">scope=conversation 时必填，否则忽略。</param>
    public Task PauseAsync(PauseScope scope, string? conversationId = null)
    {
        switch (scope)
        {
            case PauseScope.Tenant:
                PauseState?.SetTenantPaused(true);
                SetState(ClientState.PausedByServer);
                break;
            case PauseScope.Account:
                PauseState?.SetAccountPaused(true);
                SetState(ClientState.PausedByServer);
                break;
            case PauseScope.Conversation:
                if (!string.IsNullOrEmpty(conversationId))
                {
                    PauseState?.PauseConversation(conversationId);
                }
                // 会话级暂停不切 ClientState（其他会话仍可工作）
                break;
        }
        return Task.CompletedTask;
    }

    /// <summary>
    /// 服务端推送 resumed 事件时调用。清除对应 scope 的暂停。
    /// </summary>
    public Task ResumeAsync(PauseScope scope, string? conversationId = null)
    {
        switch (scope)
        {
            case PauseScope.Tenant:
                PauseState?.SetTenantPaused(false);
                // 仅当 Account 也未暂停时恢复 Running
                if (PauseState is null || !PauseState.AccountPaused)
                {
                    SetState(ClientState.Running);
                }
                break;
            case PauseScope.Account:
                PauseState?.SetAccountPaused(false);
                if (PauseState is null || !PauseState.TenantPaused)
                {
                    SetState(ClientState.Running);
                }
                break;
            case PauseScope.Conversation:
                if (!string.IsNullOrEmpty(conversationId))
                {
                    PauseState?.ResumeConversation(conversationId);
                }
                break;
        }
        return Task.CompletedTask;
    }
}
