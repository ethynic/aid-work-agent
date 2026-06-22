namespace WeCom.PersonalRpa.Core.StateMachine;

/// <summary>
/// 客户端状态枚举（protocol.md §C.5）。
/// 唯一允许执行出站 action 的状态是 Running；非 Running 一律禁止发送，仅允许上报 status 与健康。
/// </summary>
public enum ClientState
{
    /// <summary>启动中。</summary>
    Starting,

    /// <summary>环境自检（分辨率 / DPI / 企微进程 / 窗口）。</summary>
    CheckingEnvironment,

    /// <summary>需扫码登录。</summary>
    NeedLogin,

    /// <summary>运行中（唯一允许执行出站 action 的状态）。</summary>
    Running,

    /// <summary>用户手动暂停。</summary>
    PausedByUser,

    /// <summary>服务端下发暂停。</summary>
    PausedByServer,

    /// <summary>异常自动暂停（登录态 / 桌面 / 绑定 / 连续失败）。</summary>
    PausedError,

    /// <summary>崩溃 / 断网恢复中。</summary>
    Recovering,
}
