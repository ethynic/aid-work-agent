using Dapper;
using Microsoft.Data.Sqlite;
using WeCom.PersonalRpa.Automation.Vision;
using Xunit;

namespace WeCom.PersonalRpa.Tests.Vision;

// ====================================================================================
// 契约来源：plans/plan-wecom-personal-rpa-vision.md §A2（249-258 行，验收标准）。
//
// 本测试覆盖视觉缓存层的 6 个核心行为：
//   1) 写入后立即读取 → 命中
//   2) 过期（CachedAt = now - 25h，TTL = 24h）→ 未命中
//   3) 任一 fingerprint 字段变化 → 未命中（测 WindowClass / WindowRect / DpiScale / WeComVersion）
//   4) InvalidateWindowAsync 后整窗口失效（同窗口下两个不同元素的缓存都被清掉）
//   5) ClearAsync 后全空
//   6) 目录不存在时构造函数自动创建
//
// 每个测试用独立 sqlite 临时文件，避免相互污染（SqliteSendQueueTests 风格）。
//
// 集成期更新：placeholder → A1 WindowFingerprint record + A3 BoundingBox record；
// 命中返回值从 A2 CachedBbox 改为 A3 CachedEntry（IVisionCache 契约）。
// ====================================================================================

public sealed class VisionCacheTests : IDisposable
{
    private readonly string _dbPath;
    private readonly string _tempDir;
    private readonly VisionCache _cache;

    public VisionCacheTests()
    {
        _tempDir = Path.Combine(Path.GetTempPath(), $"rpa_vision_{Guid.NewGuid():N}");
        Directory.CreateDirectory(_tempDir);
        _dbPath = Path.Combine(_tempDir, "vision_cache.db");
        _cache = new VisionCache(_dbPath, TimeSpan.FromHours(24));
    }

    public void Dispose()
    {
        try { _cache.Dispose(); } catch { /* 容忍 */ }
        try
        {
            for (int i = 0; i < 3; i++)
            {
                try { Directory.Delete(_tempDir, recursive: true); break; }
                catch { Thread.Sleep(50); }
            }
        }
        catch { /* 测试清理容忍 */ }
    }

    /// <summary>
    /// 构造测试用窗口指纹。默认匹配企微主窗口常见形态（WeWorkWindow，1920x1080）。
    /// </summary>
    private static WindowFingerprint MakeFp(
        string windowClass = "WeWorkWindow",
        int x = 0, int y = 0,
        int width = 1100, int height = 720,
        double dpiScale = 1.0,
        string wecomVersion = "4.1.38") => new(
        windowClass, x, y, width, height, dpiScale, wecomVersion);

    [Fact(DisplayName = "写入后立即读取 → 命中")]
    public async Task Set_Then_TryGet_Hit()
    {
        var fp = MakeFp();
        var bbox = new BoundingBox(122, 15, 243, 38);

        await _cache.SetAsync(fp, "input", "搜索", bbox);
        var hit = await _cache.TryGetAsync(fp, "input", "搜索");

        Assert.NotNull(hit);
        Assert.Equal(bbox.X1, hit!.Bbox.X1);
        Assert.Equal(bbox.Y1, hit.Bbox.Y1);
        Assert.Equal(bbox.X2, hit.Bbox.X2);
        Assert.Equal(bbox.Y2, hit.Bbox.Y2);
        // CachedAt 应该是最近（写入时刻附近）
        var skew = DateTimeOffset.UtcNow - hit.CachedAt;
        Assert.True(skew.TotalSeconds < 5, $"CachedAt 偏离过大：{skew}");
    }

    [Fact(DisplayName = "过期（cached_at 被 SQL 强制回写 25h 前）→ 未命中")]
    public async Task Expired_Entry_Not_Hit()
    {
        var fp = MakeFp();
        var bbox = new BoundingBox(10, 20, 30, 40);

        await _cache.SetAsync(fp, "button", "发送", bbox);

        // 直接改 SQL：把 cached_at / expires_at 拉回 25h 前，模拟过期
        var backdated = DateTimeOffset.UtcNow - TimeSpan.FromHours(25);
        var backdatedIso = backdated.ToString("O");
        await using var conn = new SqliteConnection($"Data Source={_dbPath}");
        await conn.OpenAsync();
        await conn.ExecuteAsync(
            "UPDATE vision_bbox_cache SET cached_at = @c, expires_at = @e WHERE window_class = @wc;",
            new { c = backdatedIso, e = backdatedIso, wc = fp.WindowClass });

        var hit = await _cache.TryGetAsync(fp, "button", "发送");
        Assert.Null(hit);
    }

    [Fact(DisplayName = "WindowClass 变化 → 未命中")]
    public async Task Different_WindowClass_Miss()
    {
        var fp1 = MakeFp(windowClass: "WeWorkWindow");
        var fp2 = MakeFp(windowClass: "OtherWindowClass");

        await _cache.SetAsync(fp1, "button", "发送", new BoundingBox(1, 2, 3, 4));
        var hit = await _cache.TryGetAsync(fp2, "button", "发送");
        Assert.Null(hit);
    }

    [Fact(DisplayName = "WindowRect 变化（X/Y/Width/Height 任一）→ 未命中")]
    public async Task Different_WindowRect_Miss()
    {
        var fp1 = MakeFp(x: 0, y: 0, width: 1100, height: 720);
        var fp2 = MakeFp(x: 10, y: 0, width: 1100, height: 720); // X 变
        var fp3 = MakeFp(x: 0, y: 10, width: 1100, height: 720); // Y 变
        var fp4 = MakeFp(x: 0, y: 0, width: 1200, height: 720); // Width 变
        var fp5 = MakeFp(x: 0, y: 0, width: 1100, height: 800); // Height 变

        await _cache.SetAsync(fp1, "button", "发送", new BoundingBox(1, 2, 3, 4));
        Assert.Null(await _cache.TryGetAsync(fp2, "button", "发送"));
        Assert.Null(await _cache.TryGetAsync(fp3, "button", "发送"));
        Assert.Null(await _cache.TryGetAsync(fp4, "button", "发送"));
        Assert.Null(await _cache.TryGetAsync(fp5, "button", "发送"));
    }

    [Fact(DisplayName = "DpiScale 变化 → 未命中")]
    public async Task Different_DpiScale_Miss()
    {
        var fp1 = MakeFp(dpiScale: 1.0);
        var fp2 = MakeFp(dpiScale: 1.25);

        await _cache.SetAsync(fp1, "button", "发送", new BoundingBox(1, 2, 3, 4));
        Assert.Null(await _cache.TryGetAsync(fp2, "button", "发送"));
    }

    [Fact(DisplayName = "WeComVersion 变化 → 未命中")]
    public async Task Different_WeComVersion_Miss()
    {
        var fp1 = MakeFp(wecomVersion: "4.1.38");
        var fp2 = MakeFp(wecomVersion: "4.1.39");

        await _cache.SetAsync(fp1, "button", "发送", new BoundingBox(1, 2, 3, 4));
        Assert.Null(await _cache.TryGetAsync(fp2, "button", "发送"));
    }

    [Fact(DisplayName = "InvalidateWindowAsync 后整窗口下所有元素缓存失效")]
    public async Task InvalidateWindow_Removes_All_Elements_Under_Window()
    {
        var fp = MakeFp();

        // 同窗口下写两条不同元素的缓存
        await _cache.SetAsync(fp, "input", "搜索", new BoundingBox(122, 15, 243, 38));
        await _cache.SetAsync(fp, "button", "发送", new BoundingBox(951, 965, 984, 980));

        // 命中校验
        Assert.NotNull(await _cache.TryGetAsync(fp, "input", "搜索"));
        Assert.NotNull(await _cache.TryGetAsync(fp, "button", "发送"));

        // 失效整窗口
        await _cache.InvalidateWindowAsync(fp);

        // 两条都被清掉
        Assert.Null(await _cache.TryGetAsync(fp, "input", "搜索"));
        Assert.Null(await _cache.TryGetAsync(fp, "button", "发送"));

        // 其它窗口（不同指纹）不受影响
        var otherFp = MakeFp(windowClass: "OtherWindow");
        await _cache.SetAsync(otherFp, "button", "发送", new BoundingBox(1, 2, 3, 4));
        await _cache.InvalidateWindowAsync(fp);
        Assert.NotNull(await _cache.TryGetAsync(otherFp, "button", "发送"));
    }

    [Fact(DisplayName = "ClearAsync 后全部清空")]
    public async Task Clear_Removes_Everything()
    {
        var fp1 = MakeFp();
        var fp2 = MakeFp(windowClass: "OtherWindow");

        await _cache.SetAsync(fp1, "input", "搜索", new BoundingBox(1, 2, 3, 4));
        await _cache.SetAsync(fp2, "button", "发送", new BoundingBox(5, 6, 7, 8));

        Assert.NotNull(await _cache.TryGetAsync(fp1, "input", "搜索"));
        Assert.NotNull(await _cache.TryGetAsync(fp2, "button", "发送"));

        await _cache.ClearAsync();

        Assert.Null(await _cache.TryGetAsync(fp1, "input", "搜索"));
        Assert.Null(await _cache.TryGetAsync(fp2, "button", "发送"));
    }

    [Fact(DisplayName = "目录不存在时构造函数自动创建并写入成功")]
    public async Task Constructor_Creates_Parent_Directory()
    {
        var nestedDir = Path.Combine(_tempDir, "nested", "deep", "path");
        var nestedDb = Path.Combine(nestedDir, "cache.db");

        // 嵌套目录尚未存在
        Assert.False(Directory.Exists(nestedDir));

        using var nested = new VisionCache(nestedDb, TimeSpan.FromHours(24));
        Assert.True(Directory.Exists(nestedDir));

        var fp = MakeFp();
        await nested.SetAsync(fp, "input", "搜索", new BoundingBox(1, 2, 3, 4));
        var hit = await nested.TryGetAsync(fp, "input", "搜索");
        Assert.NotNull(hit);
    }

    [Fact(DisplayName = "SetAsync UPSERT：同 key 第二次写覆盖第一次的 bbox")]
    public async Task Set_Upsert_Overwrites_Same_Key()
    {
        var fp = MakeFp();
        await _cache.SetAsync(fp, "button", "发送", new BoundingBox(1, 2, 3, 4));

        // 同 key 第二次写（覆盖）
        var newBbox = new BoundingBox(100, 200, 300, 400);
        await _cache.SetAsync(fp, "button", "发送", newBbox);

        var hit = await _cache.TryGetAsync(fp, "button", "发送");
        Assert.NotNull(hit);
        Assert.Equal(newBbox.X1, hit!.Bbox.X1);
        Assert.Equal(newBbox.X2, hit.Bbox.X2);
    }
}
