using Xunit;

namespace WeCom.PersonalRpa.Tests.StateMachine;

// ====================================================================================
// 契约来源：docs/system/wecom-personal-rpa-protocol.md §C.5（ClientState 枚举）。
//
// protocol.md 明确：
//   ClientState = Starting | CheckingEnvironment | NeedLogin | Running
//               | PausedByUser | PausedByServer | PausedError | Recovering
//   且「非 Running 状态一律禁止发送，仅允许上报 status 与健康」。
//   IStateManager / ClientSession 由 Client.Core 定义、Client.App 实现。
//
// 本测试验证两个不变式（任务要求）：
//   1) 仅 Running 允许出站 action；
//   2) 非法迁移抛异常。
//
// Client.Core 的状态机实现由并行 agent 编写。为让本测试独立可编译，这里在
// 测试本地镜像契约的 ClientState 与一个最小 StateManager 合约，覆盖状态机
// 语义。待 Client.Core 的 ClientState / IStateManager 落地后，应把本地的
// LocalClientState 替换为契约枚举（命名与取值逐字一致），测试逻辑保持不变。
// ====================================================================================

/// <summary>
/// 测试本地镜像的 ClientState（protocol.md §C.5 逐字对齐）。
/// 命名/取值与契约一致，便于将来直接替换为 WeCom.PersonalRpa.Core.ClientState。
/// 注：public 以便在 [Theory]+[InlineData] 参数中引用（xunit 成员数据要求可访问性一致）。
/// </summary>
public enum LocalClientState
{
    Starting,
    CheckingEnvironment,
    NeedLogin,
    Running,
    PausedByUser,
    PausedByServer,
    PausedError,
    Recovering,
}

/// <summary>
/// 状态机迁移规则的契约镜像：仅记录 protocol.md §C.5 允许的合法迁移。
/// 非法迁移抛 <see cref="InvalidOperationException"/>（与服务端状态机语义一致）。
/// </summary>
internal sealed class LocalStateMachine
{
    public LocalClientState State { get; private set; } = LocalClientState.Starting;

    private static readonly Dictionary<LocalClientState, HashSet<LocalClientState>> AllowedTransitions =
        new()
        {
            [LocalClientState.Starting] = new() { LocalClientState.CheckingEnvironment, LocalClientState.PausedError },
            [LocalClientState.CheckingEnvironment] = new() { LocalClientState.NeedLogin, LocalClientState.Running, LocalClientState.PausedError },
            [LocalClientState.NeedLogin] = new() { LocalClientState.Running, LocalClientState.PausedError, LocalClientState.CheckingEnvironment },
            [LocalClientState.Running] = new()
            {
                LocalClientState.PausedByUser, LocalClientState.PausedByServer,
                LocalClientState.PausedError, LocalClientState.Recovering,
            },
            [LocalClientState.PausedByUser] = new() { LocalClientState.Running, LocalClientState.Recovering },
            [LocalClientState.PausedByServer] = new() { LocalClientState.Running, LocalClientState.Recovering },
            [LocalClientState.PausedError] = new() { LocalClientState.Recovering, LocalClientState.CheckingEnvironment },
            [LocalClientState.Recovering] = new() { LocalClientState.Running, LocalClientState.CheckingEnvironment, LocalClientState.PausedError },
        };

    public void TransitionTo(LocalClientState next)
    {
        if (State == next)
        {
            return; // 幂等：同状态不抛
        }

        if (!AllowedTransitions.TryGetValue(State, out var targets) || !targets.Contains(next))
        {
            throw new InvalidOperationException(
                $"非法状态迁移：{State} -> {next}（protocol.md §C.5 未允许该路径）");
        }

        State = next;
    }

    /// <summary>
    /// 出站 action 准入：protocol.md §C.5「唯一允许执行出站 action 的状态是 Running」。
    /// </summary>
    public bool CanSendOutbound => State == LocalClientState.Running;
}

public class StateManagerTests
{
    [Theory(DisplayName = "仅 Running 允许出站 action，其余全部禁止")]
    [InlineData(LocalClientState.Starting, false)]
    [InlineData(LocalClientState.CheckingEnvironment, false)]
    [InlineData(LocalClientState.NeedLogin, false)]
    [InlineData(LocalClientState.Running, true)]
    [InlineData(LocalClientState.PausedByUser, false)]
    [InlineData(LocalClientState.PausedByServer, false)]
    [InlineData(LocalClientState.PausedError, false)]
    [InlineData(LocalClientState.Recovering, false)]
    public void CanSendOutbound_OnlyTrue_WhenRunning(LocalClientState state, bool expected)
    {
        var sm = new LocalStateMachine();
        // 把状态机驱动到目标状态（通过合法路径），再断言出站准入。
        DriveTo(sm, state);

        Assert.Equal(expected, sm.CanSendOutbound);
    }

    [Fact(DisplayName = "合法迁移路径不抛异常")]
    public void LegalTransition_DoesNotThrow()
    {
        var sm = new LocalStateMachine();
        sm.TransitionTo(LocalClientState.CheckingEnvironment);
        sm.TransitionTo(LocalClientState.NeedLogin);
        sm.TransitionTo(LocalClientState.Running);

        Assert.Equal(LocalClientState.Running, sm.State);
    }

    [Theory(DisplayName = "非法迁移抛 InvalidOperationException")]
    [InlineData(LocalClientState.Starting, LocalClientState.Running)]
    [InlineData(LocalClientState.PausedByUser, LocalClientState.NeedLogin)]
    [InlineData(LocalClientState.PausedError, LocalClientState.Running)]
    [InlineData(LocalClientState.NeedLogin, LocalClientState.PausedByServer)]
    public void IllegalTransition_Throws(LocalClientState from, LocalClientState to)
    {
        var sm = new LocalStateMachine();
        DriveTo(sm, from);

        Assert.Throws<InvalidOperationException>(() => sm.TransitionTo(to));
    }

    [Fact(DisplayName = "同状态幂等迁移不抛")]
    public void SameState_TransitionIsIdempotent()
    {
        var sm = new LocalStateMachine();
        DriveTo(sm, LocalClientState.Running);

        sm.TransitionTo(LocalClientState.Running); // 幂等
        Assert.Equal(LocalClientState.Running, sm.State);
    }

    [Fact(DisplayName = "Running → PausedByServer 后立即禁止出站（服务端暂停生效）")]
    public void PausedByServer_BlocksOutboundImmediately()
    {
        var sm = new LocalStateMachine();
        DriveTo(sm, LocalClientState.Running);
        Assert.True(sm.CanSendOutbound);

        sm.TransitionTo(LocalClientState.PausedByServer);

        Assert.False(sm.CanSendOutbound);
    }

    /// <summary>
    /// 通过合法路径把状态机驱动到目标状态，用于在每个测试里准备前置条件。
    /// </summary>
    private static void DriveTo(LocalStateMachine sm, LocalClientState target)
    {
        // 已在 Starting
        if (target == LocalClientState.Starting) return;

        sm.TransitionTo(LocalClientState.CheckingEnvironment);
        if (target == LocalClientState.CheckingEnvironment) return;

        sm.TransitionTo(LocalClientState.NeedLogin);
        if (target == LocalClientState.NeedLogin) return;

        sm.TransitionTo(LocalClientState.Running);
        switch (target)
        {
            case LocalClientState.Running:
                return;
            case LocalClientState.PausedByUser:
                sm.TransitionTo(LocalClientState.PausedByUser);
                return;
            case LocalClientState.PausedByServer:
                sm.TransitionTo(LocalClientState.PausedByServer);
                return;
            case LocalClientState.PausedError:
                sm.TransitionTo(LocalClientState.PausedError);
                return;
            case LocalClientState.Recovering:
                sm.TransitionTo(LocalClientState.Recovering);
                return;
            default:
                throw new ArgumentOutOfRangeException(nameof(target), target, "测试不支持的目标状态");
        }
    }
}
