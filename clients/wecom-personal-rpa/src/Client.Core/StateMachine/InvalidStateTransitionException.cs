namespace WeCom.PersonalRpa.Core.StateMachine;

/// <summary>
/// 非法状态迁移异常。当尝试执行协议未允许的状态迁移时抛出。
/// 例如：从 PausedError 直接迁移到 Running（应先经过 Recovering），或在 Paused 状态下尝试出站 action。
/// </summary>
public sealed class InvalidStateTransitionException : InvalidOperationException
{
    /// <summary>迁移前的状态。</summary>
    public ClientState FromState { get; }

    /// <summary>尝试迁移到的目标状态。</summary>
    public ClientState ToState { get; }

    /// <summary>构造非法状态迁移异常。</summary>
    public InvalidStateTransitionException(ClientState from, ClientState to)
        : base($"非法状态迁移：{from} -> {to}")
    {
        FromState = from;
        ToState = to;
    }

    /// <summary>构造非法状态迁移异常（带自定义消息）。</summary>
    public InvalidStateTransitionException(ClientState from, ClientState to, string message)
        : base(message)
    {
        FromState = from;
        ToState = to;
    }
}
