// ====================================================================================
// 任务 A3 单测：QwenVisionLocator。
// 契约：plan-wecom-personal-rpa-vision.md §A3 验收（456-460 行）。
// 用最小 stub 注入 IScreenCapturer / IVisionCache / IVisionApi（mock 三件套）。
// ====================================================================================

using System.Drawing;
using System.Drawing.Imaging;
using System.Text.Json;
using WeCom.PersonalRpa.Automation.Vision;
using Xunit;

namespace WeCom.PersonalRpa.Tests.Vision;

/// <summary>QwenVisionLocator 单测。</summary>
public sealed class QwenVisionLocatorTests
{
    private static readonly WindowFingerprint Fp = new("WeWorkWindow", 0, 0, 800, 600, 1.0, "1.0");

    private static VisionConfig MakeConfig() => new()
    {
        PrimaryModel = "qwen3-vl-plus",
        FallbackModel = "qwen3-vl-max",
        MaxRetries = 1,
        ApiKeys = new[] { "key-1" }
    };

    /// <summary>生成 800x600 的固定 Bitmap（避免每次跑测试依赖外部截图文件）。</summary>
    private static Bitmap MakeTestBitmap()
    {
        var bmp = new Bitmap(800, 600, PixelFormat.Format24bppRgb);
        using var g = Graphics.FromImage(bmp);
        g.Clear(Color.LightGray);
        return bmp;
    }

    // ========================================================================
    // Stubs
    // ========================================================================

    private sealed class StubCapturer : IScreenCapturer
    {
        private readonly Bitmap _bmp;
        public StubCapturer(Bitmap bmp) { _bmp = bmp; }
        public Task<CaptureResult> CaptureWeComMainWindowAsync(CancellationToken cancellationToken = default)
        {
            // 注意：CaptureResult 实现了 IDisposable，会 Dispose 我们注入的 Bitmap。
            // 测试每次创建新 StubCapturer 时新建 bitmap，避免跨用例共享被释放。
            var fp = new WindowFingerprint("WeWorkWindow", 0, 0, 800, 600, 1.0, "1.0");
            return Task.FromResult(new CaptureResult
            {
                Bitmap = _bmp,
                Fingerprint = fp,
                WindowRect = (0, 0, 800, 600)
            });
        }
    }

    private sealed class InMemoryCache : IVisionCache
    {
        public Dictionary<string, CachedEntry> Store { get; } = new();
        public int InvalidateCalls { get; private set; }

        private static string Key(WindowFingerprint fp, string type, string kw) =>
            $"{fp.WindowClass}|{fp.X},{fp.Y},{fp.Width},{fp.Height}|{fp.DpiScale}|{fp.WeComVersion}|{type}|{kw}";

        public Task<CachedEntry?> TryGetAsync(WindowFingerprint fingerprint, string elementType, string labelKeyword, CancellationToken cancellationToken = default)
        {
            string k = Key(fingerprint, elementType, labelKeyword);
            return Task.FromResult(Store.TryGetValue(k, out var e) ? e : null);
        }

        public Task SetAsync(WindowFingerprint fingerprint, string elementType, string labelKeyword, BoundingBox bbox, CancellationToken cancellationToken = default)
        {
            Store[Key(fingerprint, elementType, labelKeyword)] = new CachedEntry
            {
                Bbox = bbox,
                CachedAt = DateTimeOffset.UtcNow
            };
            return Task.CompletedTask;
        }

        public Task InvalidateWindowAsync(WindowFingerprint fingerprint, CancellationToken cancellationToken = default)
        {
            InvalidateCalls++;
            string prefix = $"{fingerprint.WindowClass}|{fingerprint.X},{fingerprint.Y},{fingerprint.Width},{fingerprint.Height}|";
            foreach (var k in Store.Keys.Where(k => k.StartsWith(prefix)).ToList())
                Store.Remove(k);
            return Task.CompletedTask;
        }
    }

    private sealed class StubVisionApi : IVisionApi
    {
        private readonly string _content;
        public int CallCount { get; private set; }
        public StubVisionApi(string content) { _content = content; }
        public Task<VisionApiResponse> CallAsync(string model, string imageJpegBase64, string prompt,
            double temperature = 0.1, int maxTokens = 4096, CancellationToken cancellationToken = default)
        {
            CallCount++;
            return Task.FromResult(new VisionApiResponse
            {
                Content = _content,
                TotalTokens = 100,
                ElapsedSeconds = 0.5,
                Model = model
            });
        }
    }

    // ========================================================================
    // 业务逻辑测试
    // ========================================================================

    [Fact]
    public async Task LocateAsync_CacheHit_DoesNotCallApi()
    {
        // 预置缓存命中
        var cache = new InMemoryCache();
        cache.Store[$"{Fp.WindowClass}|0,0,800,600|1|1.0|button|发送"] = new CachedEntry
        {
            Bbox = new BoundingBox(100, 100, 200, 140),
            CachedAt = DateTimeOffset.UtcNow
        };
        var api = new StubVisionApi("should-not-be-called");
        using var locator = new QwenVisionLocator(api, new StubCapturer(MakeTestBitmap()), cache, MakeConfig());

        VisionProbeResult? result = await locator.LocateAsync("button", "发送");

        Assert.NotNull(result);
        Assert.Equal("cache", result!.Source);
        Assert.Equal(0, api.CallCount);
        Assert.Equal(new BoundingBox(100, 100, 200, 140), result.Bbox);
    }

    [Fact]
    public async Task LocateAsync_CacheMiss_CallsApi_AndWritesCache_SecondCallHits()
    {
        var cache = new InMemoryCache();
        // 方案 B 新格式：role 字段定位
        string apiContent = JsonSerializer.Serialize(new[]
        {
            new { role = "send_button", label = "Send", bbox = new[] { 300, 500, 360, 540 } }
        });
        var api = new StubVisionApi(apiContent);
        using var locator = new QwenVisionLocator(api, new StubCapturer(MakeTestBitmap()), cache, MakeConfig());

        VisionProbeResult? r1 = await locator.LocateAsync("button", "发送");
        Assert.NotNull(r1);
        Assert.Equal("api", r1!.Source);
        Assert.Equal(new BoundingBox(300, 500, 360, 540), r1.Bbox);
        Assert.Equal(1, api.CallCount);

        // 第二次：应命中缓存，不再调 API
        VisionProbeResult? r2 = await locator.LocateAsync("button", "发送");
        Assert.NotNull(r2);
        Assert.Equal("cache", r2!.Source);
        Assert.Equal(1, api.CallCount);  // API 调用次数没增加
    }

    [Fact]
    public async Task LocateAsync_BboxOutOfBounds_Throws_AndInvalidatesWindow()
    {
        var cache = new InMemoryCache();
        // 故意让 bbox 越界（800x600 图片，bbox 跑到 2000,2000）
        string apiContent = JsonSerializer.Serialize(new[]
        {
            new { role = "send_button", label = "Send", bbox = new[] { 1900, 1900, 2000, 2000 } }
        });
        var api = new StubVisionApi(apiContent);
        using var locator = new QwenVisionLocator(api, new StubCapturer(MakeTestBitmap()), cache, MakeConfig());

        await Assert.ThrowsAsync<BboxOutOfBoundsException>(() => locator.LocateAsync("button", "发送"));
        Assert.True(cache.InvalidateCalls >= 1);
    }

    [Fact]
    public async Task LocateAsync_EmptyElementArray_ReturnsNull()
    {
        var cache = new InMemoryCache();
        var api = new StubVisionApi("[]");   // 空数组
        using var locator = new QwenVisionLocator(api, new StubCapturer(MakeTestBitmap()), cache, MakeConfig());

        VisionProbeResult? result = await locator.LocateAsync("button", "发送");
        Assert.Null(result);
    }

    [Fact]
    public async Task LocateAsync_NoMatchingRole_ReturnsNull()
    {
        var cache = new InMemoryCache();
        // 只有 search_box，没有 send_button
        string apiContent = JsonSerializer.Serialize(new[]
        {
            new { role = "search_box", label = "Search", bbox = new[] { 10, 10, 100, 50 } }
        });
        var api = new StubVisionApi(apiContent);
        using var locator = new QwenVisionLocator(api, new StubCapturer(MakeTestBitmap()), cache, MakeConfig());

        VisionProbeResult? result = await locator.LocateAsync("button", "发送");
        Assert.Null(result);
    }

    [Fact]
    public async Task LocateAsync_LegacyPromptNoRoleField_ReturnsNull_DoesNotCrash()
    {
        // 容错测试：老 prompt 没有 role 字段 → MatchElement 直接返回 null，不崩
        var cache = new InMemoryCache();
        string apiContent = JsonSerializer.Serialize(new[]
        {
            new { label = "发送按钮", type = "button", bbox = new[] { 100, 200, 150, 220 } }
        });
        var api = new StubVisionApi(apiContent);
        using var locator = new QwenVisionLocator(api, new StubCapturer(MakeTestBitmap()), cache, MakeConfig());

        VisionProbeResult? result = await locator.LocateAsync("button", "发送");
        Assert.Null(result);
    }

    [Fact]
    public async Task LocateAsync_RobustParses_NonStandardBboxField()
    {
        // 模型偶尔用 box 而非 bbox；解析器应接受
        var cache = new InMemoryCache();
        string apiContent = JsonSerializer.Serialize(new[]
        {
            new { role = "send_button", label = "Send", box = new[] { 100, 200, 150, 220 } }
        });
        var api = new StubVisionApi(apiContent);
        using var locator = new QwenVisionLocator(api, new StubCapturer(MakeTestBitmap()), cache, MakeConfig());

        VisionProbeResult? result = await locator.LocateAsync("button", "发送");
        Assert.NotNull(result);
        Assert.Equal(new BoundingBox(100, 200, 150, 220), result!.Bbox);
    }

    [Fact]
    public async Task LocateAsync_RobustParses_MarkdownFencedContent()
    {
        var cache = new InMemoryCache();
        // 内容被 ```json 包裹，且是单个对象包数组
        string apiContent = "```json\n" + JsonSerializer.Serialize(new
        {
            elements = new[]
            {
                new { role = "send_button", label = "Send", bbox = new[] { 100, 200, 150, 220 } }
            }
        }) + "\n```";
        var api = new StubVisionApi(apiContent);
        using var locator = new QwenVisionLocator(api, new StubCapturer(MakeTestBitmap()), cache, MakeConfig());

        VisionProbeResult? result = await locator.LocateAsync("button", "发送");
        Assert.NotNull(result);
        Assert.Equal(new BoundingBox(100, 200, 150, 220), result!.Bbox);
    }

    [Fact]
    public async Task LocateAsync_TransientFailure_FallsBackToFallbackModel()
    {
        var cache = new InMemoryCache();
        var api = new SwitchingVisionApi(
            ("qwen3-vl-plus", new VisionApiTransientException("primary 5xx")),
            ("qwen3-vl-max", new VisionApiResponse
            {
                Content = JsonSerializer.Serialize(new[]
                {
                    new { role = "send_button", label = "Send", bbox = new[] { 100, 200, 150, 220 } }
                }),
                Model = "qwen3-vl-max"
            }));
        using var locator = new QwenVisionLocator(api, new StubCapturer(MakeTestBitmap()), cache, MakeConfig());

        VisionProbeResult? result = await locator.LocateAsync("button", "发送");
        Assert.NotNull(result);
        Assert.Equal("qwen3-vl-max", result!.ModelUsed);
        // 主模型重试 MaxRetries=1 → 调 1+1=2 次都失败；fallback 调 1 次成功
        Assert.Equal(2, api.PrimaryCalls);
        Assert.Equal(1, api.FallbackCalls);
    }

    // ========================================================================
    // 方案 B 新增：role 字段 + conversation_name 匹配逻辑测试
    // ========================================================================

    [Fact]
    public void ResolveExpectedRole_SearchInput_MapsToSearchBox()
    {
        Assert.Equal("search_box", QwenVisionLocator.ResolveExpectedRole("input", "搜索"));
        Assert.Equal("search_box", QwenVisionLocator.ResolveExpectedRole("input", "Search"));
        Assert.Equal("search_box", QwenVisionLocator.ResolveExpectedRole("input", "search box at top"));
    }

    [Fact]
    public void ResolveExpectedRole_MessageInput_MapsToMessageInput()
    {
        Assert.Equal("message_input", QwenVisionLocator.ResolveExpectedRole("input", "消息输入框"));
        Assert.Equal("message_input", QwenVisionLocator.ResolveExpectedRole("input", "message input"));
        // input 类型没匹配到搜索/消息时，默认回退 message_input（企微业务实际只有这两种 input）
        Assert.Equal("message_input", QwenVisionLocator.ResolveExpectedRole("input", "anything else"));
    }

    [Fact]
    public void ResolveExpectedRole_SendButton_MapsToSendButton()
    {
        Assert.Equal("send_button", QwenVisionLocator.ResolveExpectedRole("button", "发送"));
        Assert.Equal("send_button", QwenVisionLocator.ResolveExpectedRole("button", "send"));
    }

    [Fact]
    public void ResolveExpectedRole_ListItemIcon_MapsToNavIcon()
    {
        Assert.Equal("nav_icon", QwenVisionLocator.ResolveExpectedRole("icon", "navigation"));
        Assert.Equal("nav_icon", QwenVisionLocator.ResolveExpectedRole("icon", "导航"));
    }

    [Fact]
    public void ResolveExpectedRole_ListItem_MapsToConversationItem()
    {
        Assert.Equal("conversation_item", QwenVisionLocator.ResolveExpectedRole("list_item", "张三"));
        Assert.Equal("conversation_item", QwenVisionLocator.ResolveExpectedRole("list_item", "*"));
    }

    [Fact]
    public async Task LocateAsync_ConversationItemWildcard_ReturnsFirstMatch()
    {
        // list_item + "*" → 返回第一个 conversation_item（按 Confidence 排序）
        var cache = new InMemoryCache();
        string apiContent = JsonSerializer.Serialize(new[]
        {
            new { role = "conversation_item", label = "张三", bbox = new[] { 10, 100, 200, 140 }, conversation_name = "张三" },
            new { role = "conversation_item", label = "李四", bbox = new[] { 10, 150, 200, 190 }, conversation_name = "李四" }
        });
        var api = new StubVisionApi(apiContent);
        using var locator = new QwenVisionLocator(api, new StubCapturer(MakeTestBitmap()), cache, MakeConfig());

        VisionProbeResult? result = await locator.LocateAsync("list_item", "*");
        Assert.NotNull(result);
        // 默认 Confidence=0.5 相同 → 取第一个（张三）
        Assert.Equal(new BoundingBox(10, 100, 200, 140), result!.Bbox);
    }

    [Fact]
    public async Task LocateAsync_ConversationItemByName_MatchesConversationNameField()
    {
        var cache = new InMemoryCache();
        string apiContent = JsonSerializer.Serialize(new[]
        {
            new { role = "conversation_item", label = "Zhang San", bbox = new[] { 10, 100, 200, 140 }, conversation_name = "张三" },
            new { role = "conversation_item", label = "Li Si", bbox = new[] { 10, 150, 200, 190 }, conversation_name = "李四" }
        });
        var api = new StubVisionApi(apiContent);
        using var locator = new QwenVisionLocator(api, new StubCapturer(MakeTestBitmap()), cache, MakeConfig());

        // 即使 label 是英文，也能通过 conversation_name 命中
        VisionProbeResult? result = await locator.LocateAsync("list_item", "李四");
        Assert.NotNull(result);
        Assert.Equal(new BoundingBox(10, 150, 200, 190), result!.Bbox);
    }

    [Fact]
    public async Task LocateAsync_ConversationItemByName_FallsBackToLabelIfConversationNameMissing()
    {
        // 老格式没 conversation_name → label.Contains(keyword) 兜底
        var cache = new InMemoryCache();
        string apiContent = JsonSerializer.Serialize(new[]
        {
            new { role = "conversation_item", label = "文件传输助手", bbox = new[] { 10, 100, 200, 140 } }
        });
        var api = new StubVisionApi(apiContent);
        using var locator = new QwenVisionLocator(api, new StubCapturer(MakeTestBitmap()), cache, MakeConfig());

        VisionProbeResult? result = await locator.LocateAsync("list_item", "文件传输");
        Assert.NotNull(result);
        Assert.Equal(new BoundingBox(10, 100, 200, 140), result!.Bbox);
    }

    private sealed class SwitchingVisionApi : IVisionApi
    {
        private readonly (string model, object result) _primary;
        private readonly (string model, object result) _fallback;
        public int PrimaryCalls { get; private set; }
        public int FallbackCalls { get; private set; }

        public SwitchingVisionApi((string, object) primary, (string, object) fallback)
        {
            _primary = primary; _fallback = fallback;
        }

        public Task<VisionApiResponse> CallAsync(string model, string imageJpegBase64, string prompt,
            double temperature = 0.1, int maxTokens = 4096, CancellationToken cancellationToken = default)
        {
            if (model == _primary.model)
            {
                PrimaryCalls++;
                if (_primary.result is Exception ex) throw ex;
                return Task.FromResult((VisionApiResponse)_primary.result);
            }
            FallbackCalls++;
            return Task.FromResult((VisionApiResponse)_fallback.result);
        }
    }
}
