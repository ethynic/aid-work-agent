namespace WeCom.PersonalRpa.Core.StateMachine;

/// <summary>
/// 状态机驱动接口（protocol.md §C.4，Core 定义 / App 实现）。
/// 唯一允许执行出站 action 的状态是 Running。
/// </summary>
public interface IStateManager
{
    /// <summary>当前会话（单账号运行时）。</summary>
    ClientSession Session { get; }

    /// <summary>获取当前状态。</summary>
    ClientState CurrentState { get; }

    /// <summary>
    /// 尝试迁移状态。非法迁移抛 <see cref="InvalidStateTransitionException"/>。
    /// </summary>
    /// <param name="to">目标状态。</param>
    /// <param name="errorCode">进入暂停态时的错误码（脱敏，可空）。</param>
    /// <param name="errorMessage">进入暂停态时的错误说明（脱敏，可空）。</param>
    void TransitionTo(ClientState to, string? errorCode = null, string? errorMessage = null);

    /// <summary>
    /// 是否允许出站 action（仅 Running 返回 true）。
    /// </summary>
    bool CanSend();

    /// <summary>
    /// 静态判定：给定状态是否允许出站 action。
    /// </summary>
    static bool CanSend(ClientState state) => state == ClientState.Running;
}
