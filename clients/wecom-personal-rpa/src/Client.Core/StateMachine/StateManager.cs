namespace WeCom.PersonalRpa.Core.StateMachine;

/// <summary>
/// 状态机默认实现（protocol.md §C.4）。
/// 管理单账号会话的状态迁移，非法迁移抛 <see cref="InvalidStateTransitionException"/>。
/// 迁移规则保守：仅允许协议显式允许的路径，未知路径一律拒绝。
/// </summary>
public sealed class StateManager : IStateManager
{
    private readonly object _lock = new();

    /// <summary>
    /// 合法迁移表：key = 源状态，value = 允许迁移到的目标状态集合。
    /// 未列入的目标一律拒绝。保守设计：宁可拒绝合法迁移（实现方再放宽），不可接受非法迁移。
    /// </summary>
    private static readonly IReadOnlyDictionary<ClientState, HashSet<ClientState>> Transitions =
        new Dictionary<ClientState, HashSet<ClientState>>
        {
            [ClientState.Starting] = new HashSet<ClientState>
            {
                ClientState.CheckingEnvironment,
                ClientState.PausedError,
                ClientState.Recovering,
            },
            [ClientState.CheckingEnvironment] = new HashSet<ClientState>
            {
                ClientState.NeedLogin,
                ClientState.Running,
                ClientState.PausedError,
                ClientState.Recovering,
            },
            [ClientState.NeedLogin] = new HashSet<ClientState>
            {
                ClientState.Running,
                ClientState.PausedError,
                ClientState.PausedByUser,
                ClientState.Recovering,
            },
            [ClientState.Running] = new HashSet<ClientState>
            {
                ClientState.NeedLogin,
                ClientState.PausedByUser,
                ClientState.PausedByServer,
                ClientState.PausedError,
                ClientState.Recovering,
            },
            [ClientState.PausedByUser] = new HashSet<ClientState>
            {
                ClientState.CheckingEnvironment,
                ClientState.Running,
                ClientState.Recovering,
            },
            [ClientState.PausedByServer] = new HashSet<ClientState>
            {
                ClientState.CheckingEnvironment,
                ClientState.Running,
                ClientState.Recovering,
            },
            [ClientState.PausedError] = new HashSet<ClientState>
            {
                ClientState.Recovering,
                ClientState.CheckingEnvironment,
            },
            [ClientState.Recovering] = new HashSet<ClientState>
            {
                ClientState.CheckingEnvironment,
                ClientState.Running,
                ClientState.NeedLogin,
                ClientState.PausedError,
            },
        };

    /// <inheritdoc />
    public ClientSession Session { get; } = new ClientSession();

    /// <inheritdoc />
    public ClientState CurrentState
    {
        get
        {
            lock (_lock)
            {
                return Session.GetState();
            }
        }
    }

    /// <inheritdoc />
    public void TransitionTo(ClientState to, string? errorCode = null, string? errorMessage = null)
    {
        lock (_lock)
        {
            var from = Session.GetState();
            if (from == to)
            {
                return;
            }

            if (!Transitions.TryGetValue(from, out var allowed) || !allowed.Contains(to))
            {
                throw new InvalidStateTransitionException(from, to);
            }

            Session.SetState(to);

            // 进入 / 离开 PausedError 时更新错误信息
            if (to == ClientState.PausedError)
            {
                Session.LastErrorCode = errorCode;
                Session.LastErrorMessage = errorMessage;
            }
            else if (from == ClientState.PausedError)
            {
                Session.LastErrorCode = null;
                Session.LastErrorMessage = null;
            }
        }
    }

    /// <inheritdoc />
    public bool CanSend()
    {
        lock (_lock)
        {
            return Session.GetState() == ClientState.Running;
        }
    }
}
