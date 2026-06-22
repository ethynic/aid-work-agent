using FlaUI.Core.AutomationElements;
using Serilog;
using WeCom.PersonalRpa.Automation.Contracts;

namespace WeCom.PersonalRpa.Automation.WeCom;

/// <summary>
/// 会话定位器：通过搜索框输入关键词，识别候选会话；多候选 / 无候选返回 NeedsReview
/// 暂停该会话，等人工绑定（对应 protocol.md §A.8 conversation_needs_review）。
/// </summary>
internal sealed class ConversationNavigator
{
    private readonly INodesConfig _nodes;
    private readonly FlaUi.FlaUiDriver _flaUi;

    public ConversationNavigator(INodesConfig nodes, FlaUi.FlaUiDriver flaUi)
    {
        _nodes = nodes ?? throw new ArgumentNullException(nameof(nodes));
        _flaUi = flaUi ?? throw new ArgumentNullException(nameof(flaUi));
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
            // 1. 找到搜索框并清空 + 输入关键词。
            var searchElement = _flaUi.FindElementByAutomationId(_nodes.SearchBoxAutomationId);
            if (searchElement is null)
            {
                return FailedWithReview("搜索框未找到（AutomationId 不匹配，待准入验证回填）");
            }

            var searchBox = searchElement.AsTextBox();
            if (searchBox is null)
            {
                return FailedWithReview("搜索框不可作为 TextBox");
            }

            _flaUi.Focus(searchBox);
            searchBox.Text = keyword;

            // 2. 给搜索结果列表渲染时间。
            System.Threading.Thread.Sleep(400);

            // 3. 枚举候选会话（搜索结果列表项）。
            // 企微搜索结果通常是 ListView / 自定义 List；这里枚举 MainWindow 下所有 ListItem。
            var candidates = CollectCandidates();
            if (candidates.Count == 0)
            {
                return FailedWithReview($"关键词 '{keyword}' 未匹配到任何会话");
            }

            if (candidates.Count > 1)
            {
                // 歧义：上报 NeedsReview，等待人工在服务端绑定。
                return new ConversationNavigateResult
                {
                    Success = false,
                    NeedsReview = true,
                    CandidateDisplayName = string.Join(" | ", candidates.Take(3)),
                    ErrorCode = "conversation_needs_review",
                };
            }

            // 4. 唯一候选：点击进入会话。
            _flaUi.Click(candidates[0]);
            System.Threading.Thread.Sleep(300);

            return new ConversationNavigateResult
            {
                Success = true,
                NeedsReview = false,
                CandidateDisplayName = _flaUi.GetText(candidates[0]),
            };
        }
        catch (AutomationLayerException ex)
        {
            Log.Warning(ex, "后端日志：会话定位 FlaUI 层异常，Layer={Layer}", ex.Layer);
            return new ConversationNavigateResult
            {
                Success = false,
                NeedsReview = false,
                ErrorCode = "automation_layer_error",
            };
        }
    }

    private List<AutomationElement> CollectCandidates()
    {
        var result = new List<AutomationElement>();
        try
        {
            var mainWindow = _flaUi.MainWindow;
            if (mainWindow is null)
            {
                return result;
            }

            // 简化策略：枚举主窗口下的 ListItem（搜索结果容器），最多取前 5 项。
            var items = mainWindow.FindAllDescendants(
                mainWindow.Automation.ConditionFactory.ByControlType(FlaUI.Core.Definitions.ControlType.ListItem));

            foreach (var it in items.Take(5))
            {
                if (it.IsAvailable)
                {
                    result.Add(it);
                }
            }
        }
        catch
        {
            // 收集失败按 0 候选处理，由上层 NeedsReview。
        }
        return result;
    }

    private static ConversationNavigateResult FailedWithReview(string detail)
        => new()
        {
            Success = false,
            NeedsReview = true,
            ErrorCode = "conversation_needs_review",
        };
}
