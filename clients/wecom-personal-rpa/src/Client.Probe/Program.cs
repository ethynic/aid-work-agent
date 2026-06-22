// ============================================================================
// 企业微信个人账号 RPA —— 编码前准入验证探测工具（Client.Probe）
// ============================================================================
// 关联：
//   - plans/plan-wecom-personal-rpa.md 第 0 节（6 项准入验证）
//   - docs/system/wecom-personal-rpa-design.md §6（Windows 环境约束）、§10.1（准入验证表）
//   - clients/wecom-personal-rpa/docs/准入验证手册.md（运维照着做的手册）
//
// 定位：按需运行的诊断控制台，不加入 WeComPersonalRpaClient.sln。
// 复用 Client.Automation 的 DesktopState / WeComMainWindow / NativeMethods / FlaUiDriver /
// TemplateMatcher（通过 [assembly: InternalsVisibleTo("Client.Probe")]），不重复 P/Invoke。
//
// 安全保证（重要）：
//   本工具只读。它只做：(1) 读环境指标；(2) 读窗口句柄 / 控件树；(3) 截屏存 PNG；
//   (4) 读 OpenCV 模板匹配分数；(5) 验证 SetForegroundWindow→GetForegroundWindow 一致性。
//   绝不调用 SendInput / keybd_event / 剪贴板写入，避免向企微发送任何字符或粘贴，
//   避免误发消息（design.md §10.3：误发 0 宩查）。
//
// 退出码：0 = 全部步骤执行完毕（不代表全部成功，只代表没崩）；非 0 = 致命错误。
// ============================================================================

using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text;
using FlaUI.Core;
using FlaUI.Core.AutomationElements;
using FlaUI.Core.Definitions;
using FlaUI.UIA3;
using WeCom.PersonalRpa.Automation.Contracts;
using WeCom.PersonalRpa.Automation.FlaUi;
using WeCom.PersonalRpa.Automation.Vision;
using WeCom.PersonalRpa.Automation.WeCom;
using WeCom.PersonalRpa.Automation.Win32;
using YamlDotNet.Serialization;
using YamlDotNet.Serialization.NamingConventions;

namespace WeCom.PersonalRpa.Probe;

internal static class Program
{
    // 进程名候选：企微 PC 客户端历史用过的可执行名（不含 .exe）。
    private static readonly string[] WecomProcessCandidates = { "WXWork", "WeWork", "WXWork.exe", "WeWork.exe", "wework" };

    private static int Main(string[] args)
    {
        try
        {
            Console.OutputEncoding = Encoding.UTF8;
        }
        catch
        {
            // 某些精简 Windows 不支持 UTF-8 控制台编码，忽略。
        }

        var opts = ProbeOptions.Parse(args);
        if (opts.Help)
        {
            opts.PrintUsage(Console.Out);
            return 0;
        }

        WriteBanner(opts);

        // probe-report.yaml 默认写到「运行目录 / 当前时间戳」路径，--out 可覆盖。
        string reportPath = opts.ResolveReportPath();
        string screenshotsDir = opts.ResolveScreenshotsDir();
        try
        {
            Directory.CreateDirectory(screenshotsDir);
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"[FAIL] 无法创建截图目录 {screenshotsDir}：{ex.Message}");
        }

        var report = new ProbeReport();

        // —— 7 步探测，每步独立 try/catch ——
        Step1_EnvironmentBaseline(report);
        Step2_MainWindow(report, opts);
        Step3_UiaTree(report, opts, screenshotsDir);
        Step4_LoginState(report, opts, screenshotsDir);
        Step5_QrRegionScreenshot(report, opts, screenshotsDir);
        Step6_TemplateMatch(report, opts, screenshotsDir);
        Step7_SendInputSafety(report, opts);

        // —— 汇总建议 ——
        BuildRecommendations(report, opts);

        // —— 写 YAML ——
        try
        {
            WriteReportYaml(report, reportPath);
            report.Meta.ReportPath = reportPath;
            // 再写一次，把 ReportPath 字段写进文件本身（方便脚本 grep）。
            WriteReportYaml(report, reportPath);
            Console.WriteLine($"[OK] 报告已写入：{reportPath}");
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"[FAIL] 写 probe-report.yaml 失败：{ex.Message}");
            return 2;
        }

        Console.WriteLine();
        Console.WriteLine("准入验证探测结束。请把本报告对应字段回填到 assets/wecom_nodes.yaml，");
        Console.WriteLine("详见 docs/准入验证手册.md「翻译字段回填」章节。");
        return 0;
    }

    // =========================================================================
    // 第 1 步：环境基线（分辨率 / DPI / 锁屏 / 磁盘 / 企微进程）
    // =========================================================================
    private static void Step1_EnvironmentBaseline(ProbeReport report)
    {
        Console.WriteLine();
        Console.WriteLine("=== [1/7] 环境基线 ===");
        var env = report.Env;
        var desktop = new DesktopState();

        try
        {
            var (w, h) = desktop.GetScreenSize();
            env.Resolution = new[] { w, h };
            Console.WriteLine($"[OK] 屏幕分辨率：{w}x{h}");
        }
        catch (Exception ex)
        {
            env.ResolutionError = ex.Message;
            Console.WriteLine($"[FAIL] 屏幕分辨率不可探测：{ex.Message}");
        }

        try
        {
            int dpi = desktop.GetDpi();
            env.Dpi = dpi;
            env.DpiScale = Math.Round(dpi / 96.0, 3);
            Console.WriteLine($"[OK] DPI：{dpi}（缩放 {env.DpiScale}）");
        }
        catch (Exception ex)
        {
            env.DpiError = ex.Message;
            Console.WriteLine($"[FAIL] DPI 不可探测：{ex.Message}");
        }

        try
        {
            env.Locked = desktop.IsLocked();
            Console.WriteLine(env.Locked == true ? "[!] 桌面已锁定（屏保 / 快速用户切换）" : "[OK] 桌面未锁定");
        }
        catch (Exception ex)
        {
            env.LockedError = ex.Message;
            Console.WriteLine($"[FAIL] 锁屏状态不可探测：{ex.Message}");
        }

        try
        {
            env.DiskFreeGb = GetSystemDriveFreeGb();
            Console.WriteLine($"[OK] 系统盘剩余空间：{env.DiskFreeGb:F2} GB");
        }
        catch (Exception ex)
        {
            env.DiskFreeError = ex.Message;
            Console.WriteLine($"[FAIL] 磁盘空间不可探测：{ex.Message}");
        }

        // 企微进程：枚举所有候选名，列出实际命中的。
        try
        {
            var found = new List<string>();
            var running = Process.GetProcesses();
            foreach (var p in running)
            {
                string name;
                try { name = p.ProcessName; }
                catch { continue; }
                if (Array.Exists(WecomProcessCandidates,
                        c => string.Equals(c, name, StringComparison.OrdinalIgnoreCase)
                          || string.Equals(c, name + ".exe", StringComparison.OrdinalIgnoreCase)))
                {
                    found.Add(name);
                }
            }
            env.WecomProcesses = found.Distinct(StringComparer.OrdinalIgnoreCase).OrderBy(x => x).ToList();
            // 去掉 .exe 后缀的「干净」候选，供回填 wecom_nodes.yaml.global.wecom_process_name。
            env.WecomProcessNameHint = env.WecomProcesses.Count > 0
                ? env.WecomProcesses[0]
                : "(未找到，企微可能未启动)";
            if (env.WecomProcesses.Count > 0)
            {
                Console.WriteLine($"[OK] 企微进程在跑：{string.Join(", ", env.WecomProcesses)}");
            }
            else
            {
                Console.WriteLine("[!] 未检测到企微进程（候选：WXWork / WeWork）。请确认企微 PC 客户端已启动。");
            }
        }
        catch (Exception ex)
        {
            env.WecomProcessesError = ex.Message;
            Console.WriteLine($"[FAIL] 企微进程探测失败：{ex.Message}");
        }
    }

    // =========================================================================
    // 第 2 步：主窗口定位（Win32 FindWindow + EnumWindows 兜底）
    // =========================================================================
    private static void Step2_MainWindow(ProbeReport report, ProbeOptions opts)
    {
        Console.WriteLine();
        Console.WriteLine("=== [2/7] 主窗口定位 ===");
        var win = report.MainWindow;

        // 候选 class_name：--window-class > 默认 WeWorkWindow。
        // 遍历多个常见候选，把命中的记下来，供回填 main_window.class_name。
        var classCandidates = opts.WindowClassCandidates;
        IntPtr foundHandle = IntPtr.Zero;
        string foundClass = "";
        string foundTitle = "";
        foreach (var cls in classCandidates)
        {
            try
            {
                var main = new WeComMainWindow(cls);
                if (main.TryFind())
                {
                    foundHandle = main.Handle;
                    foundClass = cls;
                    foundTitle = TryGetWindowText(foundHandle);
                    win.Found = true;
                    win.Handle = foundHandle.ToString("X");
                    win.ClassName = cls;
                    win.Title = foundTitle;
                    break;
                }
            }
            catch (Exception ex)
            {
                win.FindError = $"按类名 {cls} 查找异常：{ex.Message}";
            }
        }

        // 兜底：枚举所有可见顶层窗口，打印含「企业微信/WeCom/WeWork」标题或类名的候选，
        // 帮助运维在 --window-class 不命中时挑出正确的类名。
        if (!win.Found)
        {
            try
            {
                var candidates = EnumerateTopWindows()
                    .Where(t => IsLikelyWeCom(t.ClassName, t.Title, opts))
                    .Take(10)
                    .Select(t => new { t.ClassName, t.Title, Handle = t.Handle.ToString("X") })
                    .ToList();
                win.TopWindowCandidates = candidates
                    .Select(c => $"{c.ClassName}|{c.Title}|0x{c.Handle}")
                    .ToList();
                if (candidates.Count > 0)
                {
                    Console.WriteLine($"[!] 未在预设类名候选命中，但枚举到 {candidates.Count} 个疑似企微顶层窗口：");
                    foreach (var c in candidates)
                    {
                        Console.WriteLine($"      class={c.ClassName} title={c.Title} hwnd=0x{c.Handle}");
                    }
                }
                else
                {
                    Console.WriteLine("[FAIL] 没有任何顶层窗口匹配企微。请确认企微 PC 客户端已登录并显示主窗口。");
                }
            }
            catch (Exception ex)
            {
                win.FindError = (win.FindError ?? "") + " 枚举顶层窗口异常：" + ex.Message;
                Console.WriteLine($"[FAIL] 枚举顶层窗口失败：{ex.Message}");
            }
            return;
        }

        // 命中后，补 ClientRect / 可见 / 前台。
        Console.WriteLine($"[OK] 命中企微主窗口：class={foundClass} title={foundTitle} hwnd=0x{win.Handle}");

        try
        {
            win.Visible = NativeMethods.IsWindowVisible(foundHandle);
            var foreground = NativeMethods.GetForegroundWindow();
            win.IsForeground = (foreground == foundHandle);
            Console.WriteLine($"[OK] 可见={win.Visible}，是否前台={win.IsForeground}");
        }
        catch (Exception ex)
        {
            win.VisibleError = ex.Message;
        }

        try
        {
            if (NativeMethods.GetWindowRect(foundHandle, out var rc))
            {
                win.WindowRect = new[] { rc.Left, rc.Top, rc.Right - rc.Left, rc.Bottom - rc.Top };
                Console.WriteLine($"[OK] WindowRect={{{rc.Left},{rc.Top},{rc.Right - rc.Left},{rc.Bottom - rc.Top}}}");
            }
            if (NativeMethods.GetClientRect(foundHandle, out var crc))
            {
                win.ClientRect = new[] { crc.Left, crc.Top, crc.Width, crc.Height };
                Console.WriteLine($"[OK] ClientRect={{{crc.Left},{crc.Top},{crc.Width},{crc.Height}}}");
            }
        }
        catch (Exception ex)
        {
            win.RectError = ex.Message;
            Console.WriteLine($"[FAIL] 窗口矩形不可探测：{ex.Message}");
        }
    }

    // =========================================================================
    // 第 3 步：UIA 控件树（FlaUI/UIA3 dump 顶层 ~80 个控件）
    // =========================================================================
    private static void Step3_UiaTree(ProbeReport report, ProbeOptions opts, string screenshotsDir)
    {
        Console.WriteLine();
        Console.WriteLine("=== [3/7] UIA 控件树（FlaUI/UIA3）===");

        if (!report.MainWindow.Found)
        {
            report.UiaTreeError = "主窗口未定位，跳过 UIA 控件树 dump。";
            Console.WriteLine("[!] 主窗口未定位，跳过 UIA 控件树。请先让企微显示主窗口并登录。");
            return;
        }

        var className = report.MainWindow.ClassName ?? opts.WindowClassCandidates[0];
        var nodes = new List<UiNode>();

        try
        {
            // 直接用 UIA3 session 取桌面 → 按类名找顶层窗口 → 遍历后代。
            // 不复用 FlaUiDriver.AttachMainWindow 的 FindFirstChild，因为企微部分版本窗口在
            // desktop.FindAllChildren(ByControlType(Window)) 才能拿到；这里把两条路径都跑一遍。
            using var automation = new UIA3Automation();
            var desktop = automation.GetDesktop();

            Window? window = null;
            try
            {
                var cond = automation.ConditionFactory.ByClassName(className);
                window = desktop.FindFirstChild(cond)?.AsWindow();
            }
            catch { /* 忽略，走兜底 */ }

            if (window is null)
            {
                foreach (var w in desktop.FindAllChildren(automation.ConditionFactory.ByControlType(ControlType.Window)))
                {
                    var asWin = w.AsWindow();
                    if (asWin != null && string.Equals(asWin.ClassName, className, StringComparison.Ordinal))
                    {
                        window = asWin;
                        break;
                    }
                }
            }

            if (window is null)
            {
                report.UiaTreeError = "UIA3 找不到企微主窗口（类名：" + className + "）。";
                Console.WriteLine($"[FAIL] UIA3 找不到企微主窗口（类名 {className}）。");
                return;
            }

            Console.WriteLine($"[OK] UIA3 已附加主窗口：Name={window.Name} ClassName={window.ClassName}");
            report.UiaWindowName = window.Name;
            report.UiaWindowClassName = window.ClassName;

            // TreeScope.Descendants 全量太大（企微主窗口常有上千个控件），用 FindAllChildren 递归，
            // 每层限宽，整体截断到 maxNodes 个，够人工挑出输入框 / 发送按钮 / 搜索框。
            const int maxNodes = 80;
            int visited = 0;
            Walk(window, depth: 0, maxDepth: 4, maxNodes, nodes, ref visited);

            report.UiaTree = nodes;
            Console.WriteLine($"[OK] 已 dump {nodes.Count} 个控件（最多 {maxNodes}）。挑出 EditControl / ButtonControl 填 wecom_nodes.yaml。");

            // 顺手把 Edit / Button 单独列表，方便运维一眼定位。
            var edits = nodes.Where(n => n.Type?.Contains("Edit") == true).ToList();
            var buttons = nodes.Where(n => n.Type?.Contains("Button") == true).ToList();
            if (edits.Count > 0)
            {
                Console.WriteLine("      [提示] Edit 控件候选（输入框/搜索框 AutomationId 来源）：");
                foreach (var e in edits.Take(10))
                {
                    Console.WriteLine($"        - automation_id={e.AutomationId} name={e.Name} rect={{{e.Rect?[0]},{e.Rect?[1]},{e.Rect?[2]},{e.Rect?[3]}}}");
                }
            }
            if (buttons.Count > 0)
            {
                Console.WriteLine("      [提示] Button 控件候选（发送按钮 Name 来源）：");
                foreach (var b in buttons.Take(10))
                {
                    Console.WriteLine($"        - name={b.Name} automation_id={b.AutomationId} rect={{{b.Rect?[0]},{b.Rect?[1]},{b.Rect?[2]},{b.Rect?[3]}}}");
                }
            }
        }
        catch (Exception ex)
        {
            report.UiaTreeError = "UIA 控件树 dump 异常：" + ex.Message;
            Console.WriteLine($"[FAIL] UIA 控件树不可探测：{ex.Message}");
        }
    }

    // =========================================================================
    // 第 4 步：登录态探测
    // =========================================================================
    private static void Step4_LoginState(ProbeReport report, ProbeOptions opts, string screenshotsDir)
    {
        Console.WriteLine();
        Console.WriteLine("=== [4/7] 登录态探测 ===");

        if (!report.MainWindow.Found)
        {
            report.LoginState = "主窗口未定位，无法判定登录态";
            Console.WriteLine("[!] 主窗口未定位，跳过登录态。");
            return;
        }

        try
        {
            var className = report.MainWindow.ClassName ?? opts.WindowClassCandidates[0];
            using var flaUi = new FlaUiDriver(className);
            var mainWin = new WeComMainWindow(className);
            if (!mainWin.TryFind())
            {
                report.LoginState = "Win32 未找到主窗口";
                Console.WriteLine("[FAIL] Win32 未找到主窗口。");
                return;
            }

            bool attached = flaUi.IsAttached || flaUi.AttachMainWindow();

            // 启发式（与 LoginStateDetector 思路对齐）：
            //   - UIA 主窗口可见 + 能找到搜索框（Edit 且 AutomationId 命中）→ Online
            //   - UIA 主窗口可见但找不到搜索框 + 窗口里有「扫码登录/二维码」相关文本 → NeedLogin
            //   - 其它 → Unknown（保守）
            if (!attached)
            {
                report.LoginState = "FlaUI 无法附加主窗口";
                Console.WriteLine("[FAIL] FlaUI 无法附加主窗口。");
                return;
            }

            // 主窗口整体是否可见。
            bool visible = NativeMethods.IsWindowVisible(mainWin.Handle);
            if (!visible)
            {
                report.LoginState = "WindowNotVisible";
                Console.WriteLine("[!] 企微主窗口不可见（可能最小化到托盘）。");
                return;
            }

            // 用 UIA3 直接遍历主窗口文本，找登录态关键词（不依赖 wecom_nodes.yaml 占位值）。
            string stateText = "Unknown";
            try
            {
                using var automation = new UIA3Automation();
                var desktop = automation.GetDesktop();
                var cond = automation.ConditionFactory.ByClassName(className);
                var window = desktop.FindFirstChild(cond)?.AsWindow();
                if (window != null)
                {
                    bool foundSearchEdit = false;
                    bool foundLoginHint = false;
                    foreach (var leaf in EnumerateLeafTexts(window, max: 200))
                    {
                        if (leaf.Contains("搜索", StringComparison.Ordinal)
                            || leaf.Contains("搜索联系人", StringComparison.Ordinal))
                        {
                            foundSearchEdit = true;
                        }
                        if (leaf.Contains("扫码", StringComparison.Ordinal)
                            || leaf.Contains("二维码", StringComparison.Ordinal)
                            || leaf.Contains("登录", StringComparison.Ordinal))
                        {
                            foundLoginHint = true;
                        }
                    }
                    if (foundSearchEdit) stateText = "Online";
                    else if (foundLoginHint) stateText = "NeedLogin";
                }
            }
            catch (Exception ex)
            {
                report.LoginState = "UIA 文本遍历异常：" + ex.Message;
                Console.WriteLine($"[FAIL] 登录态 UIA 文本遍历失败：{ex.Message}");
                return;
            }

            report.LoginState = stateText;
            switch (stateText)
            {
                case "Online":
                    Console.WriteLine("[OK] 登录态：Online（已登录）。");
                    break;
                case "NeedLogin":
                    Console.WriteLine("[!] 登录态：NeedLogin（显示二维码 / 登录入口）。");
                    break;
                default:
                    Console.WriteLine("[!] 登录态：Unknown（保守归类，请人工确认）。");
                    break;
            }
        }
        catch (Exception ex)
        {
            report.LoginState = "登录态探测异常：" + ex.Message;
            Console.WriteLine($"[FAIL] 登录态不可探测：{ex.Message}");
        }
    }

    // =========================================================================
    // 第 5 步：二维码区域截图
    // =========================================================================
    private static void Step5_QrRegionScreenshot(ProbeReport report, ProbeOptions opts, string screenshotsDir)
    {
        Console.WriteLine();
        Console.WriteLine("=== [5/7] 二维码区域截图 ===");

        // 只有在疑似 NeedLogin / Unknown 时才截；Online 时二维码不存在，截图无意义。
        bool maybeQr = report.LoginState is "NeedLogin" or "Unknown" or null;
        if (!maybeQr)
        {
            Console.WriteLine($"[OK] 登录态={report.LoginState}，无需截二维码区域。");
            report.Qr = new QrReport { Note = "登录态非 NeedLogin，跳过二维码截图。" };
            return;
        }

        if (!report.MainWindow.Found || report.MainWindow.WindowRect is null)
        {
            report.Qr = new QrReport { Note = "主窗口未定位或无矩形，跳过。" };
            Console.WriteLine("[!] 主窗口未定位或无 WindowRect，跳过二维码截图。");
            return;
        }

        // 二维码区域：--qr-region x,y,w,h（相对主窗口左上角）；不提供则截整个主窗口供人工框选。
        try
        {
            var rect = report.MainWindow.WindowRect;
            int winX = rect[0], winY = rect[1];
            int x, y, w, h;
            if (opts.QrRegion is { } qr)
            {
                x = winX + qr.X; y = winY + qr.Y; w = qr.Width; h = qr.Height;
            }
            else
            {
                // 默认截整个窗口，由人工从截图里框出二维码区域，再回填 --qr-region。
                x = winX; y = winY; w = rect[2]; h = rect[3];
            }

            if (w <= 0 || h <= 0)
            {
                report.Qr = new QrReport { Note = "二维码区域尺寸非法。" };
                Console.WriteLine("[FAIL] 二维码区域尺寸非正。");
                return;
            }

            string path = Path.Combine(screenshotsDir, $"qr_{Timestamp()}.png");
            if (CaptureScreenRegion(x, y, w, h, path))
            {
                report.Qr = new QrReport { ScreenshotPath = path, Region = new[] { x, y, w, h } };
                Console.WriteLine($"[OK] 二维码区域截图：{path}");
                if (opts.QrRegion is null)
                {
                    Console.WriteLine("      [提示] 未指定 --qr-region，截了整个主窗口。请打开截图，人工框出二维码区域，");
                    Console.WriteLine("             测出相对主窗口左上角的 x,y,w,h，再回填 wecom_nodes.yaml.qr_region。");
                }
            }
            else
            {
                report.Qr = new QrReport { Note = "CopyFromScreen 失败。" };
                Console.WriteLine("[FAIL] 二维码区域截图失败（CopyFromScreen）。");
            }
        }
        catch (Exception ex)
        {
            report.Qr = new QrReport { Note = "二维码区域截图异常：" + ex.Message };
            Console.WriteLine($"[FAIL] 二维码区域截图异常：{ex.Message}");
        }
    }

    // =========================================================================
    // 第 6 步：发送按钮区域截图 + OpenCV 模板匹配
    // =========================================================================
    private static void Step6_TemplateMatch(ProbeReport report, ProbeOptions opts, string screenshotsDir)
    {
        Console.WriteLine();
        Console.WriteLine("=== [6/7] 发送按钮区域截图 + OpenCV 模板匹配 ===");

        if (string.IsNullOrWhiteSpace(opts.Template))
        {
            Console.WriteLine("[!] 未提供 --template <path>，跳过模板匹配（仅占位）。");
            Console.WriteLine("      准入阶段先从真实企微截图里裁出发送按钮另存为 assets/templates/send_btn.png，");
            Console.WriteLine("      再带 --template 重跑本工具，验证 OpenCvSharp 能否稳定命中。");
            report.TemplateMatch = new TemplateMatchReport { Note = "未提供 --template，跳过。" };
            return;
        }

        if (!File.Exists(opts.Template))
        {
            report.TemplateMatch = new TemplateMatchReport { Note = "--template 指定的文件不存在：" + opts.Template };
            Console.WriteLine($"[FAIL] --template 文件不存在：{opts.Template}");
            return;
        }

        // 源图：主窗口可见区域截图（发送按钮通常在右下角输入区附近）。
        if (!report.MainWindow.Found || report.MainWindow.WindowRect is null)
        {
            report.TemplateMatch = new TemplateMatchReport { Note = "主窗口未定位，跳过模板匹配。" };
            Console.WriteLine("[!] 主窗口未定位，跳过模板匹配。");
            return;
        }

        try
        {
            var rect = report.MainWindow.WindowRect;
            string sourcePath = Path.Combine(screenshotsDir, $"wecom_full_{Timestamp()}.png");
            if (!CaptureScreenRegion(rect[0], rect[1], rect[2], rect[3], sourcePath))
            {
                report.TemplateMatch = new TemplateMatchReport { Note = "主窗口截图失败。" };
                Console.WriteLine("[FAIL] 主窗口截图失败。");
                return;
            }
            Console.WriteLine($"[OK] 主窗口截图：{sourcePath}");

            // OpenCvSharp：读源图 + 模板，跑 MatchTemplate CCoeffNormed。
            // 注意：OpenCvSharp 的 Rect / Window / Size / Point 与 System.Drawing / FlaUI 命名冲突，
            //       这里全限定 OpenCvSharp 命名空间，避免歧义引用。
            using var source = OpenCvSharp.Cv2.ImRead(sourcePath, OpenCvSharp.ImreadModes.Color);
            using var template = OpenCvSharp.Cv2.ImRead(opts.Template, OpenCvSharp.ImreadModes.Color);
            if (source.Empty() || template.Empty())
            {
                report.TemplateMatch = new TemplateMatchReport { Note = "OpenCvSharp 读取源图或模板失败。" };
                Console.WriteLine("[FAIL] OpenCvSharp 读取源图 / 模板失败。");
                return;
            }
            if (template.Width > source.Width || template.Height > source.Height)
            {
                report.TemplateMatch = new TemplateMatchReport { Note = "模板尺寸大于源图，无法匹配。" };
                Console.WriteLine("[FAIL] 模板尺寸大于源图，无法匹配。");
                return;
            }

            using var sourceGray = new OpenCvSharp.Mat();
            using var templateGray = new OpenCvSharp.Mat();
            using var result = new OpenCvSharp.Mat();
            OpenCvSharp.Cv2.CvtColor(source, sourceGray, OpenCvSharp.ColorConversionCodes.BGR2GRAY);
            OpenCvSharp.Cv2.CvtColor(template, templateGray, OpenCvSharp.ColorConversionCodes.BGR2GRAY);
            OpenCvSharp.Cv2.MatchTemplate(sourceGray, templateGray, result, OpenCvSharp.TemplateMatchModes.CCoeffNormed);
            OpenCvSharp.Cv2.MinMaxLoc(result, out _, out double maxVal, out _, out var maxLoc);

            double threshold = opts.TemplateThreshold > 0 ? opts.TemplateThreshold : 0.85;
            bool hit = maxVal >= threshold;
            report.TemplateMatch = new TemplateMatchReport
            {
                Score = Math.Round(maxVal, 4),
                Threshold = threshold,
                Hit = hit,
                Point = new[] { maxLoc.X, maxLoc.Y },
                SourceScreenshot = sourcePath,
                TemplatePath = opts.Template,
            };
            Console.WriteLine($"[OK] 模板匹配置信度：{maxVal:F4}（阈值 {threshold}）→ {(hit ? "命中" : "未命中")} @ ({maxLoc.X},{maxLoc.Y})");
            if (!hit)
            {
                Console.WriteLine("      [提示] 未命中：换企微版本 / 重裁模板 / 调整窗口尺寸，或降低 --template-threshold（不建议低于 0.8）。");
            }
        }
        catch (Exception ex)
        {
            report.TemplateMatch = new TemplateMatchReport { Note = "模板匹配异常：" + ex.Message };
            Console.WriteLine($"[FAIL] 模板匹配不可探测：{ex.Message}");
        }
    }

    // =========================================================================
    // 第 7 步：SendInput 安全性测试（只测焦点夺取，绝不发键鼠）
    // =========================================================================
    private static void Step7_SendInputSafety(ProbeReport report, ProbeOptions opts)
    {
        Console.WriteLine();
        Console.WriteLine("=== [7/7] SendInput 安全性（焦点夺取，不发送任何键鼠）===");

        if (!report.MainWindow.Found)
        {
            report.SendInput = new SendInputReport { Note = "主窗口未定位，跳过焦点测试。" };
            Console.WriteLine("[!] 主窗口未定位，跳过焦点测试。");
            return;
        }

        try
        {
            var className = report.MainWindow.ClassName ?? opts.WindowClassCandidates[0];
            var main = new WeComMainWindow(className);
            if (!main.TryFind())
            {
                report.SendInput = new SendInputReport { Note = "Win32 未找到主窗口。" };
                Console.WriteLine("[FAIL] Win32 未找到主窗口。");
                return;
            }

            // 复用 WeComMainWindow.BringToForeground：它内部走 ShowWindow(SW_RESTORE) +
            // SetWindowPos(TOPMOST→NOTOPMOST) + SetForegroundWindow。不会发任何键鼠。
            // 注意：BringToForeground 仅用于测试焦点是否能夺取；本工具绝不调用 SendInput。
            bool brought = main.BringToForeground();
            // 给系统一点时间稳定前台窗口（与生产 SendInput 节奏控制一致）。
            Thread.Sleep(150);
            IntPtr fg = NativeMethods.GetForegroundWindow();
            bool canFocus = brought && fg == main.Handle;
            report.SendInput = new SendInputReport
            {
                CanFocus = canFocus,
                BroughtToForeground = brought,
                ForegroundHandle = fg.ToString("X"),
                ExpectedHandle = main.Handle.ToString("X"),
            };
            Console.WriteLine($"[OK] SetForegroundWindow→GetForegroundWindow 一致：{canFocus}");
            if (!canFocus)
            {
                Console.WriteLine("      [提示] 焦点夺取失败常见原因：前台被其它进程锁住（Windows 前台权限）、");
                Console.WriteLine("             远控会话处于断开态、企微主窗口被最小化且未恢复。");
            }
            Console.WriteLine("      [安全说明] 本步骤没有调用 SendInput / keybd_event / 剪贴板，未向企微发送任何字符。");
        }
        catch (Exception ex)
        {
            report.SendInput = new SendInputReport { Note = "焦点测试异常：" + ex.Message };
            Console.WriteLine($"[FAIL] 焦点测试不可探测：{ex.Message}");
        }
    }

    // =========================================================================
    // 汇总建议
    // =========================================================================
    private static void BuildRecommendations(ProbeReport report, ProbeOptions opts)
    {
        Console.WriteLine();
        Console.WriteLine("=== 汇总建议 ===");
        var recs = report.Recommendations;

        if (report.MainWindow.Found && !string.IsNullOrEmpty(report.MainWindow.ClassName))
        {
            recs.Add($"main_window.class_name ← {report.MainWindow.ClassName}（来自第 2 步 FindWindow 命中）");
        }
        if (report.Env.WecomProcessNameHint is { } proc && !proc.StartsWith('('))
        {
            recs.Add($"global.wecom_process_name ← {proc}（来自第 1 步进程枚举）");
        }
        if (report.Env.Resolution is { } res)
        {
            recs.Add($"global.resolution ← [{res[0]}, {res[1]}]（设计要求固定 1920x1080）");
        }
        if (report.Env.DpiScale is { } scale && Math.Abs(scale - 1.0) > 0.001)
        {
            recs.Add($"[!] global.dpi_scale={scale} 非 1.0。设计要求 DPI 100%，请先在系统显示设置里改回 100%。");
        }
        if (report.Env.Locked == true)
        {
            recs.Add("[!] 桌面处于锁定 / 屏保态。准入验证必须在已登录可见桌面下重跑。");
        }
        var edits = report.UiaTree?.Where(n => n.Type?.Contains("Edit") == true).ToList();
        if (edits != null && edits.Count > 0)
        {
            // 自动挑出 AutomationId 非空、最像搜索框 / 消息输入框的两个候选。
            var withId = edits.Where(e => !string.IsNullOrWhiteSpace(e.AutomationId)).Take(3).ToList();
            foreach (var e in withId)
            {
                recs.Add($"候选 controls.search_box/message_input.automation_id ← \"{e.AutomationId}\"（Edit name=\"{e.Name}\"，需人工确认）");
            }
        }
        var buttons = report.UiaTree?.Where(n => n.Type?.Contains("Button") == true && !string.IsNullOrWhiteSpace(n.Name)).Take(3).ToList();
        if (buttons != null && buttons.Count > 0)
        {
            foreach (var b in buttons)
            {
                recs.Add($"候选 controls.send_button.name ← \"{b.Name}\"（Button automation_id=\"{b.AutomationId}\"，需人工确认）");
            }
        }
        if (report.Qr?.Region is { } qr)
        {
            // 报告里 Region 是屏幕绝对坐标；翻译回相对主窗口左上角再回填。
            if (report.MainWindow.WindowRect is { } wr)
            {
                int rx = qr[0] - wr[0], ry = qr[1] - wr[1];
                recs.Add($"qr_region.x/y/width/height ← {rx}/{ry}/{qr[2]}/{qr[3]}（屏幕绝对 {qr[0]},{qr[1]} 减主窗口左上 {wr[0]},{wr[1]}）");
            }
        }
        if (report.TemplateMatch?.Hit == true)
        {
            recs.Add($"vision.template_match_threshold ← {report.TemplateMatch.Threshold}（实测置信度 {report.TemplateMatch.Score} 已稳定命中）");
        }
        else if (report.TemplateMatch?.Hit == false)
        {
            recs.Add($"[!] 发送按钮模板未命中（置信度 {report.TemplateMatch.Score} < {report.TemplateMatch.Threshold}）。");
            recs.Add("    请重裁模板 / 固定窗口尺寸后重跑；若多版本企微都打不到 0.85，需考虑放弃纯模板匹配策略。");
        }
        if (report.SendInput?.CanFocus == false)
        {
            recs.Add("[!] 焦点夺取不达标（SetForegroundWindow 后 GetForegroundWindow 不一致）。SendInput 在此环境会失真。");
            recs.Add("    检查：远控是否断开 / 是否有其它前台锁进程 / 是否企微被最小化未恢复。");
        }

        if (recs.Count == 0)
        {
            recs.Add("（无自动建议——请结合手册逐项人工核对。）");
        }

        foreach (var r in recs)
        {
            Console.WriteLine("  - " + r);
        }
    }

    // =========================================================================
    // 工具方法
    // =========================================================================

    private static double GetSystemDriveFreeGb()
    {
        // Environment.SystemDirectory 形如 C:\Windows\System32；取盘根。
        string root = Path.GetPathRoot(Environment.SystemDirectory) ?? "C:\\";
        var drive = new DriveInfo(root);
        return drive.AvailableFreeSpace / 1024.0 / 1024.0 / 1024.0;
    }

    private static string TryGetWindowText(IntPtr hWnd)
    {
        try
        {
            int len = NativeMethods.GetWindowTextLengthW(hWnd);
            if (len <= 0) return "";
            var sb = new StringBuilder(len + 2);
            _ = Win32GetWindowText(hWnd, sb, sb.Capacity);
            return sb.ToString();
        }
        catch
        {
            return "";
        }
    }

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern int Win32GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    private static IEnumerable<(IntPtr Handle, string ClassName, string Title)> EnumerateTopWindows()
    {
        var list = new List<(IntPtr, string, string)>();
        NativeMethods.EnumWindows((wnd, _) =>
        {
            if (!NativeMethods.IsWindowVisible(wnd)) return true;
            var sb = new StringBuilder(256);
            _ = NativeMethods.GetClassNameW(wnd, sb, sb.Capacity);
            string cls = sb.ToString();
            string title = TryGetWindowText(wnd);
            list.Add((wnd, cls, title));
            return true;
        }, IntPtr.Zero);
        return list;
    }

    private static bool IsLikelyWeCom(string className, string title, ProbeOptions opts)
    {
        // 类名命中候选 → 直接认。
        if (opts.WindowClassCandidates.Any(c => string.Equals(c, className, StringComparison.OrdinalIgnoreCase)))
        {
            return true;
        }
        // 标题命中关键字 → 也算疑似（运维需人工最终确认）。
        if (!string.IsNullOrEmpty(title))
        {
            return title.Contains("企业微信", StringComparison.Ordinal)
                || title.Contains("WeCom", StringComparison.OrdinalIgnoreCase)
                || title.Contains("WeWork", StringComparison.OrdinalIgnoreCase);
        }
        return false;
    }

    private static void Walk(AutomationElement parent, int depth, int maxDepth, int maxNodes,
        List<UiNode> sink, ref int visited)
    {
        if (depth > maxDepth || visited >= maxNodes) return;
        AutomationElement[] children;
        try
        {
            children = parent.FindAllChildren();
        }
        catch
        {
            return;
        }
        foreach (var child in children)
        {
            if (visited >= maxNodes) return;
            try
            {
                sink.Add(ToUiNode(child, depth));
                visited++;
            }
            catch
            {
                // 单个控件取值失败忽略，不影响整体。
            }
            Walk(child, depth + 1, maxDepth, maxNodes, sink, ref visited);
        }
    }

    private static UiNode ToUiNode(AutomationElement e, int depth)
    {
        var node = new UiNode
        {
            Depth = depth,
            Type = e.ControlType.ToString(),
            Name = e.Name,
            AutomationId = e.AutomationId,
        };
        try
        {
            var bb = e.BoundingRectangle;
            node.Rect = new[]
            {
                (int)Math.Round((double)bb.X),
                (int)Math.Round((double)bb.Y),
                (int)Math.Round((double)bb.Width),
                (int)Math.Round((double)bb.Height)
            };
        }
        catch
        {
            // 控件无 BoundingRectangle（离屏 / 隐藏）时留空。
        }
        return node;
    }

    private static List<string> EnumerateLeafTexts(FlaUI.Core.AutomationElements.Window window, int max)
    {
        // 只取有 Name / Value 的叶子文本，用于登录态关键词匹配。最多 max 个，避免全树遍历爆栈。
        // 改为返回 List（不再用 ref 计数 + yield），因为 C# 迭代器不允许 ref/in/out 参数。
        var texts = new List<string>();
        var queue = new Queue<AutomationElement>();
        queue.Enqueue(window);
        while (queue.Count > 0 && texts.Count < max)
        {
            AutomationElement cur;
            try { cur = queue.Dequeue(); }
            catch { break; }
            try
            {
                if (!string.IsNullOrEmpty(cur.Name)) texts.Add(cur.Name);
            }
            catch { /* ignore */ }
            try
            {
                foreach (var c in cur.FindAllChildren()) queue.Enqueue(c);
            }
            catch { /* ignore */ }
        }
        return texts;
    }

    private static bool CaptureScreenRegion(int x, int y, int w, int h, string outPath)
    {
        try
        {
            using var bmp = new Bitmap(w, h);
            using var g = Graphics.FromImage(bmp);
            g.CopyFromScreen(x, y, 0, 0, new System.Drawing.Size(w, h));
            Directory.CreateDirectory(Path.GetDirectoryName(outPath)!);
            bmp.Save(outPath, ImageFormat.Png);
            return true;
        }
        catch
        {
            return false;
        }
    }

    private static string Timestamp()
    {
        return DateTime.Now.ToString("yyyyMMdd_HHmmss", CultureInfo.InvariantCulture);
    }

    private static void WriteReportYaml(ProbeReport report, string path)
    {
        report.Meta.GeneratedAt = DateTime.Now.ToString("yyyy-MM-ddTHH:mm:ss", CultureInfo.InvariantCulture);
        report.Meta.Tool = "Client.Probe v1.0.0";
        var serializer = new SerializerBuilder()
            .WithNamingConvention(NullNamingConvention.Instance) // 保持 PascalCase 字段名
            .Build();
        var yaml = serializer.Serialize(report);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, yaml, new UTF8Encoding(false));
    }

    private static void WriteBanner(ProbeOptions opts)
    {
        Console.WriteLine("================================================================");
        Console.WriteLine(" 企业微信个人账号 RPA —— 编码前准入验证探测工具");
        Console.WriteLine("================================================================");
        Console.WriteLine(" 本工具只读：只截图 / 读控件树 / 验证焦点夺取，不发送任何键鼠或剪贴板输入到企微。");
        Console.WriteLine($" 报告路径：{opts.ResolveReportPath()}");
        Console.WriteLine($" 截图目录：{opts.ResolveScreenshotsDir()}");
        if (!string.IsNullOrEmpty(opts.Template))
        {
            Console.WriteLine($" 模板路径：{opts.Template}");
        }
        Console.WriteLine("================================================================");
    }
}

// ============================================================================
// 报告数据模型（YamlDotNet 直接序列化）
// ============================================================================

internal sealed class ProbeReport
{
    public ReportMeta Meta { get; } = new();
    public EnvReport Env { get; } = new();
    public MainWindowReport MainWindow { get; } = new();
    public List<UiNode> UiaTree { get; set; } = new();
    public string? UiaWindowName { get; set; }
    public string? UiaWindowClassName { get; set; }
    public string? UiaTreeError { get; set; }
    public string? LoginState { get; set; }
    public QrReport? Qr { get; set; }
    public TemplateMatchReport? TemplateMatch { get; set; }
    public SendInputReport? SendInput { get; set; }
    public List<string> Recommendations { get; } = new();
}

internal sealed class ReportMeta
{
    public string GeneratedAt { get; set; } = "";
    public string Tool { get; set; } = "";
    public string? ReportPath { get; set; }
}

internal sealed class EnvReport
{
    public int[]? Resolution { get; set; }
    public string? ResolutionError { get; set; }
    public int? Dpi { get; set; }
    public double? DpiScale { get; set; }
    public string? DpiError { get; set; }
    public bool? Locked { get; set; }
    public string? LockedError { get; set; }
    public double? DiskFreeGb { get; set; }
    public string? DiskFreeError { get; set; }
    public List<string> WecomProcesses { get; set; } = new();
    public string? WecomProcessNameHint { get; set; }
    public string? WecomProcessesError { get; set; }
}

internal sealed class MainWindowReport
{
    public bool Found { get; set; }
    public string? Handle { get; set; }
    public string? ClassName { get; set; }
    public string? Title { get; set; }
    public bool? Visible { get; set; }
    public bool? IsForeground { get; set; }
    public int[]? WindowRect { get; set; } // x, y, w, h
    public int[]? ClientRect { get; set; }  // x, y, w, h
    public string? FindError { get; set; }
    public string? VisibleError { get; set; }
    public string? RectError { get; set; }
    public List<string>? TopWindowCandidates { get; set; }
}

internal sealed class UiNode
{
    public int Depth { get; set; }
    public string? Type { get; set; }       // ControlType: EditControl / ButtonControl / ...
    public string? Name { get; set; }
    public string? AutomationId { get; set; }
    public int[]? Rect { get; set; }        // x, y, w, h
}

internal sealed class QrReport
{
    public string? ScreenshotPath { get; set; }
    public int[]? Region { get; set; }      // 屏幕绝对坐标 x, y, w, h
    public string? Note { get; set; }
}

internal sealed class TemplateMatchReport
{
    public double? Score { get; set; }
    public double? Threshold { get; set; }
    public bool? Hit { get; set; }
    public int[]? Point { get; set; }       // 模板左上角在源图中的 x, y
    public string? SourceScreenshot { get; set; }
    public string? TemplatePath { get; set; }
    public string? Note { get; set; }
}

internal sealed class SendInputReport
{
    public bool? CanFocus { get; set; }
    public bool? BroughtToForeground { get; set; }
    public string? ForegroundHandle { get; set; }
    public string? ExpectedHandle { get; set; }
    public string? Note { get; set; }
}

// ============================================================================
// 命令行选项
// ============================================================================

internal sealed class ProbeOptions
{
    public bool Help { get; private set; }

    // 多个候选 class_name：默认 [WeWorkWindow, WeComMainWnd, TXGuiFoundation(腾讯系通用)]。
    // --window-class 可追加 / 覆盖。
    public List<string> WindowClassCandidates { get; private set; } = new()
    {
        "WeWorkWindow",
        "WeComMainWnd",
        "TXGuiFoundation"
    };

    public WeCom.PersonalRpa.Automation.Contracts.Rect? QrRegion { get; private set; }

    public string? Template { get; private set; }
    public double TemplateThreshold { get; private set; } = 0.85;

    public string? OutPath { get; private set; }
    public string? ScreenshotsDir { get; private set; }

    public static ProbeOptions Parse(string[] args)
    {
        var o = new ProbeOptions();
        for (int i = 0; i < args.Length; i++)
        {
            string a = args[i];
            switch (a)
            {
                case "-h":
                case "--help":
                case "/?":
                    o.Help = true;
                    return o;
                case "--window-class":
                    if (i + 1 < args.Length)
                    {
                        // 多次提供则追加候选。
                        o.WindowClassCandidates.Insert(0, args[++i]);
                    }
                    break;
                case "--title-contains":
                    // 已通过枚举兜底覆盖；此参数保留兼容，暂不单独使用。
                    if (i + 1 < args.Length) i++;
                    break;
                case "--qr-region":
                    if (i + 1 < args.Length)
                    {
                        var parts = args[++i].Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
                        if (parts.Length == 4
                            && int.TryParse(parts[0], out int qx) && int.TryParse(parts[1], out int qy)
                            && int.TryParse(parts[2], out int qw) && int.TryParse(parts[3], out int qh))
                        {
                            o.QrRegion = new WeCom.PersonalRpa.Automation.Contracts.Rect(qx, qy, qw, qh);
                        }
                    }
                    break;
                case "--template":
                    if (i + 1 < args.Length) o.Template = args[++i];
                    break;
                case "--template-threshold":
                    if (i + 1 < args.Length && double.TryParse(args[++i], out double th))
                    {
                        o.TemplateThreshold = th;
                    }
                    break;
                case "--out":
                    if (i + 1 < args.Length) o.OutPath = args[++i];
                    break;
                case "--screenshots-dir":
                    if (i + 1 < args.Length) o.ScreenshotsDir = args[++i];
                    break;
                default:
                    // 未知参数忽略（不崩），保持探测鲁棒。
                    break;
            }
        }
        return o;
    }

    public string ResolveReportPath()
    {
        if (!string.IsNullOrWhiteSpace(OutPath)) return Path.GetFullPath(OutPath);
        return Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "probe-report.yaml"));
    }

    public string ResolveScreenshotsDir()
    {
        if (!string.IsNullOrWhiteSpace(ScreenshotsDir)) return Path.GetFullPath(ScreenshotsDir);
        return Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "probe-screenshots"));
    }

    public void PrintUsage(TextWriter w)
    {
        w.WriteLine("用法：Client.Probe [options]");
        w.WriteLine();
        w.WriteLine("选项：");
        w.WriteLine("  --window-class <name>       企微主窗口类名候选（默认 WeWorkWindow/WeComMainWnd/TXGuiFoundation）");
        w.WriteLine("                              可多次提供，最前者优先");
        w.WriteLine("  --qr-region <x,y,w,h>       二维码区域，相对主窗口左上角（不提供则截整个主窗口供人工框选）");
        w.WriteLine("  --template <path>           发送按钮模板 PNG（不提供则跳过模板匹配）");
        w.WriteLine("  --template-threshold <0..1> 模板匹配置信度阈值（默认 0.85）");
        w.WriteLine("  --out <path>                报告输出路径（默认 clients/wecom-personal-rpa/probe-report.yaml）");
        w.WriteLine("  --screenshots-dir <dir>     截图输出目录（默认 clients/wecom-personal-rpa/probe-screenshots/）");
        w.WriteLine("  -h, --help                  显示本帮助");
        w.WriteLine();
        w.WriteLine("示例：");
        w.WriteLine("  dotnet run --project src/Client.Probe -- --window-class WeWorkWindow");
        w.WriteLine("  dotnet run --project src/Client.Probe -- --template assets/templates/send_btn.png");
        w.WriteLine("  powershell -File scripts/run-probe.ps1 --template assets/templates/send_btn.png --qr-region 520,240,200,200");
    }
}
