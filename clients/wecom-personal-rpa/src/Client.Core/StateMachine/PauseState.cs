namespace WeCom.PersonalRpa.Core.StateMachine;

/// <summary>
/// 暂停作用域（与服务端 protocol.md §A.6 paused_scope 对齐）。
/// tenant=整租户停；account=单账号停；conversation=单会话停。
/// </summary>
public enum PauseScope
{
    /// <summary>租户级（全部动作停）。</summary>
    Tenant,

    /// <summary>账号级（该账号停）。</summary>
    Account,

    /// <summary>会话级（仅该会话停）。</summary>
    Conversation,
}

/// <summary>
/// 客户端暂停状态协调器（设计文档 §9.3）。
///
/// 设计：
///   - 单例 DI 注册，所有 IHostedService（OutboundActionDispatcher / ChatArchiveListener /
///     QrCodeWatcher / DesktopHealthSupervisor / InboundEventReporter）共享同一份状态。
///   - 服务端通过 WebSocket 推 <c>paused</c> / <c>resumed</c> 事件（payload 含 scope），
///     ClientSession.PauseAsync / ResumeAsync 把状态写入此处。
///   - 各工作循环在关键操作前调 <see cref="IsPaused"/> / <see cref="IsConversationPaused"/> 自检。
///   - 全部字段 volatile / lock 保护，避免竞态。
///
/// 简化（首版）：
///   - Conversation 级用 HashSet+lock；Tenant/Account 用 volatile bool。
///   - 不持久化（重启后重新由服务端推或重新评估）。
/// </summary>
public sealed class PauseState
{
    private readonly object _convLock = new();
    private volatile bool _tenantPaused;
    private volatile bool _accountPaused;
    private HashSet<string> _pausedConversations = new(StringComparer.Ordinal);

    /// <summary>租户级是否暂停（最高优先级，覆盖其他）。</summary>
    public bool TenantPaused => _tenantPaused;

    /// <summary>账号级是否暂停（当前单账号 = ClientId 对应账号）。</summary>
    public bool AccountPaused => _accountPaused;

    /// <summary>
    /// 当前是否对外动作执行被禁。
    /// TenantPaused / AccountPaused 任一为真则禁。
    /// </summary>
    public bool IsPaused => _tenantPaused || _accountPaused;

    /// <summary>设置租户级暂停状态。</summary>
    public void SetTenantPaused(bool paused) => _tenantPaused = paused;

    /// <summary>设置账号级暂停状态。</summary>
    public void SetAccountPaused(bool paused) => _accountPaused = paused;

    /// <summary>标记某个 conversation 暂停。</summary>
    public void PauseConversation(string conversationId)
    {
        if (string.IsNullOrEmpty(conversationId)) return;
        lock (_convLock)
        {
            // 重新构建 HashSet 避免枚举期间修改
            var next = new HashSet<string>(_pausedConversations, StringComparer.Ordinal) { conversationId };
            _pausedConversations = next;
        }
    }

    /// <summary>清除某个 conversation 的暂停。</summary>
    public void ResumeConversation(string conversationId)
    {
        if (string.IsNullOrEmpty(conversationId)) return;
        lock (_convLock)
        {
            if (_pausedConversations.Count == 0) return;
            var next = new HashSet<string>(_pausedConversations, StringComparer.Ordinal);
            next.Remove(conversationId);
            _pausedConversations = next;
        }
    }

    /// <summary>清除所有 conversation 级暂停（账号 / 租户级不动）。</summary>
    public void ResumeAllConversations()
    {
        lock (_convLock)
        {
            _pausedConversations = new HashSet<string>(StringComparer.Ordinal);
        }
    }

    /// <summary>判断某个 conversation 是否被暂停（综合 Tenant / Account 级）。</summary>
    public bool IsConversationPaused(string? conversationId)
    {
        if (_tenantPaused || _accountPaused) return true;
        if (string.IsNullOrEmpty(conversationId)) return false;
        lock (_convLock)
        {
            return _pausedConversations.Contains(conversationId);
        }
    }

    /// <summary>全量重置（恢复默认）。Stop 时调用，避免状态残留。</summary>
    public void Reset()
    {
        _tenantPaused = false;
        _accountPaused = false;
        lock (_convLock)
        {
            _pausedConversations = new HashSet<string>(StringComparer.Ordinal);
        }
    }
}
