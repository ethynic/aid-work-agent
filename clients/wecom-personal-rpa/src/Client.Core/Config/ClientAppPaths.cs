using System;
using System.IO;

namespace WeCom.PersonalRpa.Core.Config;

/// <summary>
/// 客户端（Client.App）本地路径常量的唯一来源。
///
/// 用途：消除 ConfigTool 写入路径与 Client.App 读取路径不一致的隐患。
/// 2026-06-25 联调事故：ConfigTool 默认写到 %LOCALAPPDATA%\WeComRpa\client_config.enc，
/// 而 Client.App 读 %LOCALAPPDATA%\WeComPersonalRpa\Client.App\data\client_config.enc，
/// 导致用户跑完 ConfigTool 后客户端加载到旧配置（错的 BaseUrl），WebSocket 连不上。
///
/// 此处把客户端实际使用的目录约定集中成一个常量来源，ConfigTool / Client.App / 未来
/// 的迁移工具均从此处取值。仅使用 BCL API（Environment / Path），无 WPF 依赖，可被
/// Client.Core（net8.0）共享给所有引用方。
/// </summary>
public static class ClientAppPaths
{
    /// <summary>客户端根目录名（%LOCALAPPDATA%\WeComPersonalRpa）下，Client.App 子目录名。</summary>
    public const string AppFolderName = "Client.App";

    /// <summary>本地数据子目录名（DataDirectory，存放 client_config.enc / send_queue.db 等）。</summary>
    public const string DataFolderName = "data";

    /// <summary>日志子目录名（LogDirectory，Serilog 按天滚动文件落地处）。</summary>
    public const string LogsFolderName = "logs";

    /// <summary>
    /// 加密配置文件名（与 <see cref="EncryptedClientConfig.FileName"/> 保持一致）。
    /// 这里独立定义一份字符串常量，避免在 net8.0（不限平台）的 Client.Core 里访问
    /// 带 [SupportedOSPlatform("windows")] 的 EncryptedClientConfig 类型成员触发 CA1416。
    /// 若文件名变更，需同步修改 EncryptedClientConfig.FileName。
    /// </summary>
    public const string ConfigFileName = "client_config.enc";

    /// <summary>
    /// Client.App 根目录（%LOCALAPPDATA%\WeComPersonalRpa\Client.App）。
    /// 其下的 data/ 存放配置，logs/ 存放日志。
    /// </summary>
    public static string AppDirectory =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "WeComPersonalRpa", AppFolderName);

    /// <summary>
    /// 客户端运行期数据目录：ClientOptionsLoader 在此读 client_config.enc。
    /// 也是 ConfigTool 默认输出路径必须指向的位置。
    /// </summary>
    public static string DataDirectory => Path.Combine(AppDirectory, DataFolderName);

    /// <summary>客户端日志目录（Serilog 落地、托盘"打开日志目录"指向此处）。</summary>
    public static string LogDirectory => Path.Combine(AppDirectory, LogsFolderName);

    /// <summary>
    /// 加密配置文件绝对路径（%LOCALAPPDATA%\WeComPersonalRpa\Client.App\data\client_config.enc）。
    /// ConfigTool 默认写入路径 = Client.App 读取路径，二者必须指向同一文件。
    /// </summary>
    public static string ConfigFilePath => Path.Combine(DataDirectory, ConfigFileName);
}
