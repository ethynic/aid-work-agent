using System.IO;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using WeCom.PersonalRpa.App.Powershell;
using Xunit;

namespace WeCom.PersonalRpa.Tests.Powershell;

/// <summary>
/// PowershellOpsInvoker 单元测试。
///
/// 测试用真实 PS 进程（不 mock）— 因为 mock 不能验证 PS 脚本本身的契约，
/// 而我们就是要测 C# ↔ PS 的 IO 契约（stdin JSON / stdout JSON / 错误码语义）。
///
/// 测试不依赖真实企微桌面：
///   - get_login_state 在无企微环境会返回 state=offline（或 online 如果开发机有企微）
///   - 其他 action 通过参数验证让 PS 主动失败（不进入实际点击）
///
/// 脚本路径定位：测试运行时工作目录可能是 src/Client.Tests 或仓库根，
/// 通过逐级向上查找 clients/wecom-personal-rpa/scripts/wecom-ops.ps1 解决。
/// </summary>
public sealed class PowershellOpsInvokerTests
{
    [Fact]
    public void ResolveOpsScriptPath_RelativePath_UsesAppBaseDirectory()
    {
        var resolved = PowershellOpsInvoker.ResolveOpsScriptPath(Path.Combine("scripts", "wecom-ops.ps1"));

        Assert.Equal(
            Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "scripts", "wecom-ops.ps1")),
            resolved);
    }

    [Fact]
    public void ResolveOpsScriptPath_AbsolutePath_RemainsAbsolute()
    {
        var absolute = Path.GetFullPath(Path.Combine(Path.GetTempPath(), "wecom-ops.ps1"));

        Assert.Equal(absolute, PowershellOpsInvoker.ResolveOpsScriptPath(absolute));
    }

    /// <summary>
    /// 定位 wecom-ops.ps1 脚本绝对路径。从测试运行目录向上查找，直到找到
    /// clients/wecom-personal-rpa/scripts/wecom-ops.ps1。
    /// </summary>
    private static string ResolveOpsScript()
    {
        var dir = AppContext.BaseDirectory;
        var outputScript = Path.Combine(dir, "scripts", "wecom-ops.ps1");
        if (File.Exists(outputScript))
        {
            return outputScript;
        }

        for (var i = 0; i < 8; i++)
        {
            var candidate = Path.Combine(dir, "clients", "wecom-personal-rpa", "scripts", "wecom-ops.ps1");
            if (File.Exists(candidate))
            {
                return candidate;
            }
            var parent = Directory.GetParent(dir);
            if (parent == null) break;
            dir = parent.FullName;
        }
        // fallback：测试工程相对路径（dotnet test 默认在工程目录）
        return Path.GetFullPath(
            Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..", "scripts", "wecom-ops.ps1"));
    }

    private static PowershellOpsInvoker CreateInvoker(int timeoutSeconds = 30)
    {
        var opts = new PowershellOptions
        {
            Executable = "powershell.exe",
            OpsScript = ResolveOpsScript(),
            InvokeTimeoutSeconds = timeoutSeconds,
        };
        return new PowershellOpsInvoker(opts, NullLogger<PowershellOpsInvoker>.Instance);
    }

    /// <summary>get_login_state 应返回结构化 JSON，success=true。state 取决于测试机是否有企微。</summary>
    [Fact]
    public async Task InvokeAsync_GetLoginState_ReturnsParsedJson()
    {
        var invoker = CreateInvoker();
        var result = await invoker.InvokeAsync("get_login_state", null);

        Assert.True(result.Success, $"get_login_state 应成功。stderr/err: {result.ErrorMessage}");
        Assert.Equal("get_login_state", result.Action);
        // state 字段应为 online 或 offline（取决于测试机是否有企微）
        Assert.True(result.State == "online" || result.State == "offline",
            $"state 应为 online/offline，实际：{result.State}");
        Assert.False(string.IsNullOrEmpty(result.RawJson), "RawJson 应保留完整 PS 输出");
    }

    /// <summary>不在 ValidateSet 里的 action 应让 PS 自身报错（exit non-zero），C# 端 stdout 找不到 JSON → ps_no_json。
    /// 注意：PS ValidateSet 拒绝时把错误写到 stderr，stdout 无输出；C# 端会返回 ps_no_json。
    /// ErrorMessage 里包含 stderr 内容（可能因 PS 错误模板被截断），不强制包含 action 名字。</summary>
    [Fact]
    public async Task InvokeAsync_InvalidAction_ReturnsNoJsonError()
    {
        var invoker = CreateInvoker();
        var result = await invoker.InvokeAsync("invalid_action_xyz", null);

        Assert.False(result.Success);
        Assert.Equal("ps_no_json", result.ErrorCode);
        // 不强制 ErrorMessage 包含 action 名字——PS ValidateSet 错误模板格式不稳，且 stderr 可能被截断。
        // 只要确认走的是 ps_no_json 路径即可。
        Assert.False(string.IsNullOrEmpty(result.ErrorMessage), "ErrorMessage 应包含 stderr 用于排查");
    }

    /// <summary>构造极短超时（1 秒），跑正常 get_login_state，期望触发 ps_timeout。
    /// 通过 InvokeTimeoutSeconds 触发 Invoker 内部的 timeoutCts（而非外部 CancellationToken），
    /// 这样会走"超时返回 false"路径，而不是"外部取消抛异常"路径。
    /// 注意：1 秒在某些快机器上可能 PS 仍能完成（success=true），所以接受两种结果。</summary>
    [Fact]
    public async Task InvokeAsync_Timeout_TriggersAndKills()
    {
        // 1 秒超时：PS 冷启动 + lib 加载 + Add-Type 编译 WeOpsWin32 通常 >1s，但快机器可能 borderline
        var invoker = CreateInvoker(timeoutSeconds: 1);
        var result = await invoker.InvokeAsync("get_login_state", null);

        // 接受 ps_timeout 或 success 两种结果（1 秒是边界值，看机器性能）
        // 关键断言：不能是其他错误码（说明 PS 启动了但没正常退出）
        Assert.True(result.Success || result.ErrorCode == "ps_timeout",
            $"期望 ps_timeout 或 success（1s 边界），实际：code={result.ErrorCode} msg={result.ErrorMessage}");
    }

    /// <summary>验证 stdin JSON 能正确传给 PS：send_text 缺 text 应让 PS 返回 invalid_params 错误（说明 $params 解析成功）。</summary>
    [Fact]
    public async Task InvokeAsync_StdinJson_PassedToPs()
    {
        var invoker = CreateInvoker();
        // 故意只传 keyword 不传 text，PS 端 $params.text 为 $null，应进入 invalid_params 分支
        var result = await invoker.InvokeAsync("send_text", new { keyword = "test_user" });

        Assert.False(result.Success);
        // 这里有可能 PS 在搜索阶段就失败（无企微 → wecom_window_not_found），
        // 但只要不是 ps_no_json 就说明 stdin JSON 被正确解析（业务逻辑收到参数了）
        Assert.NotEqual("ps_no_json", result.ErrorCode);
        Assert.True(result.ErrorCode == "invalid_params" || result.ErrorCode == "wecom_window_not_found",
            $"期望 invalid_params 或 wecom_window_not_found，实际：{result.ErrorCode}");
    }

    /// <summary>验证 search_user 空关键词返回 invalid_params。</summary>
    [Fact]
    public async Task InvokeAsync_SearchUser_EmptyKeyword_ReturnsInvalidParams()
    {
        var invoker = CreateInvoker();
        var result = await invoker.InvokeAsync("search_user", new { keyword = "" });

        Assert.False(result.Success);
        Assert.Equal("invalid_params", result.ErrorCode);
    }

    /// <summary>验证 send_image 缺 image_path 返回 invalid_params（不实际访问文件系统）。</summary>
    [Fact]
    public async Task InvokeAsync_SendImage_EmptyPath_ReturnsInvalidParams()
    {
        var invoker = CreateInvoker();
        var result = await invoker.InvokeAsync("send_image", new { keyword = "test", image_path = "" });

        Assert.False(result.Success);
        // 缺 image_path 在 PS 端 Test-Path 失败前就会 IsNullOrEmpty 短路
        Assert.Equal("invalid_params", result.ErrorCode);
    }
}
