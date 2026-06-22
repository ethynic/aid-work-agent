namespace WeCom.PersonalRpa.Core.Observability;

/// <summary>
/// 健康状态上报接口（占位）。Supervisor 实现用于周期性收集 App 存活 / 队列积压 / 账号登录态等指标。
/// </summary>
public interface IHealthReporter
{
    /// <summary>
    /// 采集一次健康指标。返回键值对（值已脱敏）。
    /// </summary>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>健康指标字典。</returns>
    Task<IReadOnlyDictionary<string, object?>> CollectAsync(CancellationToken cancellationToken = default);

    /// <summary>
    /// 上报一次心跳到服务端（占位，实现方调用 IAgentApiClient）。
    /// </summary>
    Task ReportHeartbeatAsync(CancellationToken cancellationToken = default);
}
