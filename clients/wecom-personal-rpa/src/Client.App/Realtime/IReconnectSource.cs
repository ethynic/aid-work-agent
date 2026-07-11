namespace WeCom.PersonalRpa.App.Realtime;

/// <summary>
/// 暴露 WebSocket 重连事件，供 <see cref="Outbound.OutboxPoller"/> 订阅以触发立即 outbox 拉取。
/// 抽成接口便于 OutboxPoller 单测（用假源模拟重连，无需构造真实 WebSocketConnectionManager 及其依赖）。
/// </summary>
public interface IReconnectSource
{
    /// <summary>连接（首次建立或重连）成功时触发。</summary>
    event EventHandler<EventArgs>? Reconnected;
}
