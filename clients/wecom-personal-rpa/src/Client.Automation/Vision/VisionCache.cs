using System.Globalization;
using Dapper;
using Microsoft.Data.Sqlite;
using Serilog;

namespace WeCom.PersonalRpa.Automation.Vision;

// ====================================================================================
// 契约来源：plans/plan-wecom-personal-rpa-vision.md §A2（167-260 行）。
//
// VisionCache 是视觉定位方案的「性能护栏」：
//   - 同一企微窗口下，控件像素位置绝对稳定；
//   - 命中缓存可避免 28s 的 Qwen3-VL API 调用；
//   - 缓存键 = (window_class, window_rect, dpi_scale, wecom_version, element_type, label_keyword)。
//
// 设计约束：
//   1) 复用 Client.Core/Queue/SqliteSendQueue 的 Dapper + Microsoft.Data.Sqlite 模式（通过 Client.Core 传递引用）；
//   2) TTL 检查只在 TryGetAsync 做（不做后台清理），过期行由 TryGetAsync 视为 miss；
//   3) SQL 异常必须 catch + log warning + 视为 miss（不向上抛，避免缓存故障拖垮主流程）；
//   4) 日志统一走 Serilog（与 Client.Core/Client.Automation 现有约定一致）；
//   5) 实现 IVisionCache 接口（A3 定义），供 QwenVisionLocator / OcrVisionLocator 消费。
//
// 集成状态：阶段 2A 集成期已完成 placeholder → A1/A3 真实类型迁移：
//   - WindowFingerprintPlaceholder → A1 WindowFingerprint record
//   - BoundingBoxPlaceholder → A3 BoundingBox record
//   - CachedBbox → A3 CachedEntry（IVisionCache 契约）
// ====================================================================================

/// <summary>
/// 基于 SQLite 的视觉 bbox 缓存层。实现 <see cref="IVisionCache"/> 供视觉定位器消费。
/// </summary>
/// <remarks>
/// 缓存键 = (window_class, window_rect, dpi_scale, wecom_version, element_type, label_keyword)，
/// 任意一个字段变化都视为新 key。TTL 在 TryGetAsync 中检查，不做后台清理。
/// 所有 SQL 异常会被吞掉并视为 miss，避免缓存故障拖垮主定位流程。
/// </remarks>
public sealed class VisionCache : IVisionCache, IDisposable
{
    private readonly string _connectionString;
    private readonly TimeSpan _defaultTtl;
    private readonly ILogger _logger;
    private bool _disposed;

    /// <summary>构造缓存实例。</summary>
    /// <param name="dbPath">SQLite 文件绝对路径，目录不存在时自动创建。</param>
    /// <param name="defaultTtl">默认 TTL，建议传入 TimeSpan.FromHours(24)。</param>
    public VisionCache(string dbPath, TimeSpan defaultTtl) : this(dbPath, defaultTtl, Log.Logger)
    {
    }

    /// <summary>构造缓存实例（可注入 Serilog logger，便于单测隔离日志）。</summary>
    /// <param name="dbPath">SQLite 文件绝对路径，目录不存在时自动创建。</param>
    /// <param name="defaultTtl">默认 TTL，建议传入 TimeSpan.FromHours(24)。</param>
    /// <param name="logger">Serilog logger 实例。</param>
    public VisionCache(string dbPath, TimeSpan defaultTtl, ILogger logger)
    {
        if (string.IsNullOrWhiteSpace(dbPath))
            throw new ArgumentNullException(nameof(dbPath));
        if (defaultTtl <= TimeSpan.Zero)
            throw new ArgumentOutOfRangeException(nameof(defaultTtl), "defaultTtl 必须为正 TimeSpan");

        _defaultTtl = defaultTtl;
        _logger = logger ?? Log.Logger;

        var dir = Path.GetDirectoryName(dbPath);
        if (!string.IsNullOrEmpty(dir))
        {
            Directory.CreateDirectory(dir);
        }

        _connectionString = new SqliteConnectionStringBuilder
        {
            DataSource = dbPath,
            Mode = SqliteOpenMode.ReadWriteCreate,
            Cache = SqliteCacheMode.Shared,
        }.ToString();

        try
        {
            EnsureTableCreated();
        }
        catch (Exception ex)
        {
            // 建表失败属于致命错误（磁盘满 / 路径无效），向上抛由上层决定是否降级。
            _logger.Error(ex, "VisionCache 建表失败 dbPath={DbPath}", dbPath);
            throw;
        }
    }

    private void EnsureTableCreated()
    {
        const string sql = """
            CREATE TABLE IF NOT EXISTS vision_bbox_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                window_class TEXT NOT NULL,
                window_rect TEXT NOT NULL,
                dpi_scale REAL NOT NULL,
                wecom_version TEXT NOT NULL,
                element_type TEXT NOT NULL,
                label_keyword TEXT NOT NULL,
                bbox_x1 INTEGER NOT NULL,
                bbox_y1 INTEGER NOT NULL,
                bbox_x2 INTEGER NOT NULL,
                bbox_y2 INTEGER NOT NULL,
                cached_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                UNIQUE(window_class, window_rect, dpi_scale, wecom_version, element_type, label_keyword)
            );
            CREATE INDEX IF NOT EXISTS idx_vbc_lookup ON vision_bbox_cache(window_class, window_rect, element_type, label_keyword);
            CREATE INDEX IF NOT EXISTS idx_vbc_expires ON vision_bbox_cache(expires_at);
            """;
        using var conn = OpenConnection();
        conn.Execute(sql);
    }

    private SqliteConnection OpenConnection()
    {
        var conn = new SqliteConnection(_connectionString);
        conn.Open();
        return conn;
    }

    /// <summary>
    /// 将 WindowRect 序列化为 "$x,$y,$w,$h" 格式（不是 JSON），与计划文档 §A2 表结构一致。
    /// </summary>
    private static string FormatWindowRect(int x, int y, int w, int h)
        => FormattableString.Invariant($"{x},{y},{w},{h}");

    private static string FormatDto(DateTimeOffset dto)
        => dto.ToUniversalTime().ToString("O", CultureInfo.InvariantCulture);

    private static DateTimeOffset ParseDto(string s)
        => DateTimeOffset.Parse(s, CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal);

    /// <summary>查缓存。命中且未过期返回 bbox；否则返回 null。任何 SQL 异常视为 miss。</summary>
    /// <param name="fingerprint">窗口指纹（A1 真实 record）。</param>
    /// <param name="elementType">控件类型（button/input/list_item/icon/text）。</param>
    /// <param name="labelKeyword">控件标签关键字（如 "发送" / "搜索" / "文件传输助手"）。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>命中返回 <see cref="CachedEntry"/>；未命中或异常返回 null。</returns>
    public async Task<CachedEntry?> TryGetAsync(
        WindowFingerprint fingerprint,
        string elementType,
        string labelKeyword,
        CancellationToken cancellationToken = default)
    {
        if (fingerprint is null) throw new ArgumentNullException(nameof(fingerprint));
        if (string.IsNullOrEmpty(elementType)) throw new ArgumentNullException(nameof(elementType));
        if (string.IsNullOrEmpty(labelKeyword)) throw new ArgumentNullException(nameof(labelKeyword));

        const string sql = """
            SELECT bbox_x1 AS X1, bbox_y1 AS Y1, bbox_x2 AS X2, bbox_y2 AS Y2,
                   cached_at AS CachedAt
            FROM vision_bbox_cache
            WHERE window_class = @WindowClass
              AND window_rect = @WindowRect
              AND dpi_scale = @DpiScale
              AND wecom_version = @WeComVersion
              AND element_type = @ElementType
              AND label_keyword = @LabelKeyword
              AND expires_at > @NowUtc
            LIMIT 1;
            """;

        var nowUtc = DateTimeOffset.UtcNow;
        var parameters = new
        {
            WindowClass = fingerprint.WindowClass,
            WindowRect = FormatWindowRect(fingerprint.X, fingerprint.Y, fingerprint.Width, fingerprint.Height),
            DpiScale = fingerprint.DpiScale,
            WeComVersion = fingerprint.WeComVersion,
            ElementType = elementType,
            LabelKeyword = labelKeyword,
            NowUtc = FormatDto(nowUtc),
        };

        try
        {
            using var conn = OpenConnection();
            var row = await conn.QueryFirstOrDefaultAsync<CacheRow>(
                new CommandDefinition(sql, parameters, cancellationToken: cancellationToken)).ConfigureAwait(false);
            if (row is null) return null;

            return new CachedEntry
            {
                Bbox = new BoundingBox(row.X1, row.Y1, row.X2, row.Y2),
                CachedAt = ParseDto(row.CachedAt),
            };
        }
        catch (Exception ex)
        {
            _logger.Warning(ex,
                "VisionCache.TryGetAsync 失败，按 miss 处理 elementType={ElementType} labelKeyword={LabelKeyword}",
                elementType, labelKeyword);
            return null;
        }
    }

    /// <summary>写缓存（UPSERT，覆盖同 key）。bbox 越界检查由调用方负责。</summary>
    /// <param name="fingerprint">窗口指纹。</param>
    /// <param name="elementType">控件类型。</param>
    /// <param name="labelKeyword">控件标签关键字。</param>
    /// <param name="bbox">控件 bbox（A3 真实 record）。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    public async Task SetAsync(
        WindowFingerprint fingerprint,
        string elementType,
        string labelKeyword,
        BoundingBox bbox,
        CancellationToken cancellationToken = default)
    {
        if (fingerprint is null) throw new ArgumentNullException(nameof(fingerprint));
        if (string.IsNullOrEmpty(elementType)) throw new ArgumentNullException(nameof(elementType));
        if (string.IsNullOrEmpty(labelKeyword)) throw new ArgumentNullException(nameof(labelKeyword));
        if (bbox is null) throw new ArgumentNullException(nameof(bbox));

        const string sql = """
            INSERT INTO vision_bbox_cache
                (window_class, window_rect, dpi_scale, wecom_version,
                 element_type, label_keyword,
                 bbox_x1, bbox_y1, bbox_x2, bbox_y2,
                 cached_at, expires_at)
            VALUES
                (@WindowClass, @WindowRect, @DpiScale, @WeComVersion,
                 @ElementType, @LabelKeyword,
                 @X1, @Y1, @X2, @Y2,
                 @CachedAt, @ExpiresAt)
            ON CONFLICT(window_class, window_rect, dpi_scale, wecom_version, element_type, label_keyword)
            DO UPDATE SET
                bbox_x1 = excluded.bbox_x1,
                bbox_y1 = excluded.bbox_y1,
                bbox_x2 = excluded.bbox_x2,
                bbox_y2 = excluded.bbox_y2,
                cached_at = excluded.cached_at,
                expires_at = excluded.expires_at;
            """;

        var nowUtc = DateTimeOffset.UtcNow;
        var expiresAt = nowUtc + _defaultTtl;
        var parameters = new
        {
            WindowClass = fingerprint.WindowClass,
            WindowRect = FormatWindowRect(fingerprint.X, fingerprint.Y, fingerprint.Width, fingerprint.Height),
            DpiScale = fingerprint.DpiScale,
            WeComVersion = fingerprint.WeComVersion,
            ElementType = elementType,
            LabelKeyword = labelKeyword,
            X1 = bbox.X1,
            Y1 = bbox.Y1,
            X2 = bbox.X2,
            Y2 = bbox.Y2,
            CachedAt = FormatDto(nowUtc),
            ExpiresAt = FormatDto(expiresAt),
        };

        try
        {
            using var conn = OpenConnection();
            await conn.ExecuteAsync(
                new CommandDefinition(sql, parameters, cancellationToken: cancellationToken)).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            _logger.Warning(ex,
                "VisionCache.SetAsync 失败，缓存写不进去不影响主流程 elementType={ElementType} labelKeyword={LabelKeyword}",
                elementType, labelKeyword);
        }
    }

    /// <summary>失效特定窗口下所有缓存（窗口指纹变化时调用）。不区分 element_type / label_keyword。</summary>
    /// <param name="fingerprint">窗口指纹。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    public async Task InvalidateWindowAsync(
        WindowFingerprint fingerprint,
        CancellationToken cancellationToken = default)
    {
        if (fingerprint is null) throw new ArgumentNullException(nameof(fingerprint));

        const string sql = """
            DELETE FROM vision_bbox_cache
            WHERE window_class = @WindowClass
              AND window_rect = @WindowRect
              AND dpi_scale = @DpiScale
              AND wecom_version = @WeComVersion;
            """;

        var parameters = new
        {
            WindowClass = fingerprint.WindowClass,
            WindowRect = FormatWindowRect(fingerprint.X, fingerprint.Y, fingerprint.Width, fingerprint.Height),
            DpiScale = fingerprint.DpiScale,
            WeComVersion = fingerprint.WeComVersion,
        };

        try
        {
            using var conn = OpenConnection();
            var affected = await conn.ExecuteAsync(
                new CommandDefinition(sql, parameters, cancellationToken: cancellationToken)).ConfigureAwait(false);
            _logger.Information("VisionCache.InvalidateWindowAsync 删除 {Count} 条缓存 windowClass={WindowClass}",
                affected, fingerprint.WindowClass);
        }
        catch (Exception ex)
        {
            _logger.Warning(ex, "VisionCache.InvalidateWindowAsync 失败 windowClass={WindowClass}", fingerprint.WindowClass);
        }
    }

    /// <summary>清空所有缓存（手动「刷新定位」按钮调用）。</summary>
    /// <param name="cancellationToken">取消令牌。</param>
    public async Task ClearAsync(CancellationToken cancellationToken = default)
    {
        const string sql = "DELETE FROM vision_bbox_cache;";

        try
        {
            using var conn = OpenConnection();
            var affected = await conn.ExecuteAsync(
                new CommandDefinition(sql, cancellationToken: cancellationToken)).ConfigureAwait(false);
            _logger.Information("VisionCache.ClearAsync 清空 {Count} 条缓存", affected);
        }
        catch (Exception ex)
        {
            _logger.Warning(ex, "VisionCache.ClearAsync 失败");
        }
    }

    /// <inheritdoc />
    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        // Dapper 每次操作都用独立短生命周期连接，无需释放持久连接。
    }

    // Dapper 映射用内部 DTO（字段名与 SQL 别名对齐）。
    private sealed class CacheRow
    {
        public int X1 { get; set; }
        public int Y1 { get; set; }
        public int X2 { get; set; }
        public int Y2 { get; set; }
        public string CachedAt { get; set; } = "";
    }
}
