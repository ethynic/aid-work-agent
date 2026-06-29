using WeCom.PersonalRpa.Core.Protocol;

namespace WeCom.PersonalRpa.App.Outbound;

/// <summary>
/// ActionEnvelope 数据来源抽象。
///
/// 设计目的：让 OutboundActionDispatcher 不直接依赖具体 WebSocket 客户端，
/// 便于单元测试（mock source）和后续替换传输层（WebSocket / 长轮询 outbox / 双源）。
///
/// 实现者：
///   - 真实环境：可由负责 WebSocket 接收的组件（Phase 4 推出）实现，调用 EnqueueAsync 入队。
///   - 单测：直接 mock 该接口或构造 stub。
/// </summary>
public interface IActionSource
{
    /// <summary>
    /// 等待下一个 ActionEnvelope；返回 null 表示当前没有更多数据（订阅被关闭或断开）。
    /// 取消令牌触发时抛 OperationCanceledException。
    /// </summary>
    Task<ActionEnvelope?> ReadAsync(CancellationToken ct = default);
}

/// <summary>
/// 简单 stub 实现：内存 Channel。生产者直接调用 PushAsync 入队，消费者通过 ReadAsync 出队。
/// 单测与早期接入（Phase 3 不依赖 WebSocket 时）使用。
/// </summary>
public sealed class ChannelActionSource : IActionSource
{
    private readonly System.Threading.Channels.Channel<ActionEnvelope> _ch =
        System.Threading.Channels.Channel.CreateUnbounded<ActionEnvelope>(
            new System.Threading.Channels.UnboundedChannelOptions
            {
                SingleReader = true,
                SingleWriter = false,
            });

    /// <summary>推送一个信封到通道。</summary>
    public Task PushAsync(ActionEnvelope env)
    {
        _ch.Writer.TryWrite(env);
        return Task.CompletedTask;
    }

    /// <inheritdoc />
    public async Task<ActionEnvelope?> ReadAsync(CancellationToken ct = default)
    {
        try
        {
            return await _ch.Reader.ReadAsync(ct).ConfigureAwait(false);
        }
        catch (System.Threading.Channels.ChannelClosedException)
        {
            return null;
        }
    }

    /// <summary>关闭通道（让 Worker 退出）。</summary>
    public void Complete() => _ch.Writer.TryComplete();
}
