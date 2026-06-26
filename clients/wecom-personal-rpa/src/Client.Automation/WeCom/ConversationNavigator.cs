using Serilog;
using WeCom.PersonalRpa.Automation.Contracts;
using WeCom.PersonalRpa.Automation.Vision;
using WeCom.PersonalRpa.Automation.Win32;

namespace WeCom.PersonalRpa.Automation.WeCom;

/// <summary>
/// 会话定位器（阶段 2B 视觉路径重构）。
/// 流程：通过 IVisionLocator 定位搜索框 → InputExecutor 输入关键词 → 视觉定位搜索结果候选项 →
/// 多候选检测：唯一命中点击进入会话；零候选 / 多候选返回 NeedsReview=true。
/// </summary>
public sealed class ConversationNavigator
{
    private readonly IVisionLocator _visionLocator;
    private readonly InputExecutor _inputExecutor;
    private readonly WeComMainWindow _mainWindow;

    public ConversationNavigator(
        IVisionLocator visionLocator,
        InputExecutor inputExecutor,
        WeComMainWindow mainWindow)
    {
        _visionLocator = visionLocator ?? throw new ArgumentNullException(nameof(visionLocator));
        _inputExecutor = inputExecutor ?? throw new ArgumentNullException(nameof(inputExecutor));
        _mainWindow = mainWindow ?? throw new ArgumentNullException(nameof(mainWindow));
    }

    /// <summary>
    /// 用搜索关键词定位会话。
    /// 返回：<see cref="ConversationNavigateResult"/>；多候选 / 无候选时 NeedsReview=true。
    /// </summary>
    public ConversationNavigateResult Navigate(string keyword)
    {
        if (string.IsNullOrWhiteSpace(keyword))
        {
            return new ConversationNavigateResult
            {
                Success = false,
                NeedsReview = false,
                ErrorCode = "bad_request",
            };
        }

        try
        {
            var origin = ResolveWindowOrigin();
            if (origin is null)
            {
                Log.Warning("后端日志：ConversationNavigator 无法解析主窗口原点");
                return new ConversationNavigateResult
                {
                    Success = false,
                    NeedsReview = false,
                    ErrorCode = "automation_layer_error",
                };
            }

            // 1. 定位搜索框并点击
            var searchProbe = _visionLocator.LocateAsync("input", "搜索").GetAwaiter().GetResult();
            if (searchProbe is null)
            {
                Log.Warning("后端日志：ConversationNavigator 定位搜索框失败（elementType=input, kw=搜索）");
                return new ConversationNavigateResult
                {
                    Success = false,
                    NeedsReview = false,
                    ErrorCode = "automation_layer_error",
                };
            }

            _inputExecutor.ClickElement(searchProbe.Bbox, origin.Value);
            Thread.Sleep(400);

            // 2. 清空搜索框已有内容（Ctrl+A 全选 + Delete）后再输入关键词。
            // 必要性：搜索框可能保留上次的搜索词；不清空直接 Ctrl+V 会拼接成 "旧词新词"。
            Win32Input.SelectAllAndDelete();
            Thread.Sleep(150);
            _inputExecutor.TypeText(keyword, pressEnterAfter: false);
            Thread.Sleep(1500);  // 给企微搜索结果渲染留时间（实测 500ms 不够，结果还没出来）

            // 3. 定位搜索结果列表项
            var listProbe = _visionLocator.LocateAsync("list_item", keyword).GetAwaiter().GetResult();
            if (listProbe is null)
            {
                // 零候选：上报 NeedsReview 让上层人工绑定
                Log.Information("后端日志：ConversationNavigator 关键词 '{Kw}' 未匹配到任何会话", keyword);
                return new ConversationNavigateResult
                {
                    Success = false,
                    NeedsReview = true,
                    ErrorCode = "conversation_needs_review",
                };
            }

            // 多候选检测：当模型只返回单条命中时，我们无法 100% 判定唯一；首版保守按"命中即点击"
            // 进入会话，由 MessageWatcher / 后续业务验证是否进入正确会话。
            // 真正的多候选需要模型支持 multiple bbox 返回，落地后这里改为按返回数量分流。
            // 单击搜索结果列表项：企微 5.0.8 单击即进入会话视图。
            // 注意：不要双击！第二次点击会落在已经切换好的会话视图上，可能误点中历史消息里的图片把它放大。
            _inputExecutor.ClickElement(listProbe.Bbox, origin.Value);
            Thread.Sleep(2500);  // 进入会话视图后给企微充足时间渲染聊天界面 + 消息输入框

            return new ConversationNavigateResult
            {
                Success = true,
                NeedsReview = false,
                CandidateDisplayName = keyword,
            };
        }
        catch (Exception ex)
        {
            Log.Warning(ex, "后端日志：ConversationNavigator.Navigate 异常");
            return new ConversationNavigateResult
            {
                Success = false,
                NeedsReview = false,
                ErrorCode = "automation_layer_error",
            };
        }
    }

    /// <summary>解析企微主窗口左上角屏幕坐标；窗口未就绪返回 null。</summary>
    private (int Left, int Top)? ResolveWindowOrigin()
    {
        try
        {
            if (_mainWindow.Handle == IntPtr.Zero)
            {
                _mainWindow.TryFind();
            }
            if (_mainWindow.Handle == IntPtr.Zero)
            {
                return null;
            }
            var (left, top, _, _) = _mainWindow.GetRect();
            return (left, top);
        }
        catch (Exception ex)
        {
            Log.Warning(ex, "后端日志：ConversationNavigator 解析主窗口原点失败");
            return null;
        }
    }
}
