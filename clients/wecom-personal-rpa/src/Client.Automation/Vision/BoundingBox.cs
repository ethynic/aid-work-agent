// ====================================================================================
// 契约来源：plans/plan-wecom-personal-rpa-vision.md §A3（290-302 行）。
// 并行协同约束（plan §467-484）：A3 是 BoundingBox 的唯一归属，A2 的 CachedBbox 在
// A3 落地后由集成者把 BoundingBoxPlaceholder 引用替换为本类型。
// ====================================================================================

namespace WeCom.PersonalRpa.Automation.Vision;

/// <summary>
/// 像素坐标轴对齐矩形（left/top/right/bottom，左上原点，y 向下）。
/// 用于 Qwen3-VL / Windows.Media.Ocr / 视觉缓存层三处共享的 bbox 表达。
/// </summary>
public sealed record BoundingBox(int X1, int Y1, int X2, int Y2)
{
    /// <summary>矩形宽度（像素）。</summary>
    public int Width => X2 - X1;

    /// <summary>矩形高度（像素）。</summary>
    public int Height => Y2 - Y1;

    /// <summary>中心点坐标 (Cx, Cy)。</summary>
    public (int Cx, int Cy) Center => ((X1 + X2) / 2, (Y1 + Y2) / 2);

    /// <summary>
    /// 相对图像尺寸的越界检查。模型偶尔会把屏幕绝对坐标当相对坐标返回（或反之），
    /// 调用方拿到 bbox 后必须先调本方法校验，越界即视为模型幻觉，失效该元素缓存。
    /// </summary>
    /// <param name="imageWidth">参考图像宽度（像素）。</param>
    /// <param name="imageHeight">参考图像高度（像素）。</param>
    /// <param name="tolerance">允许的越界像素（默认 0，企微 grounding 场景传 5）。</param>
    /// <returns>全部落在 [−tolerance, W/H + tolerance] 范围内返回 true。</returns>
    public bool IsWithin(int imageWidth, int imageHeight, int tolerance = 0)
    {
        if (tolerance < 0) tolerance = 0;
        return X1 >= -tolerance
               && Y1 >= -tolerance
               && X2 <= imageWidth + tolerance
               && Y2 <= imageHeight + tolerance
               && X1 <= X2
               && Y1 <= Y2;
    }

    /// <summary>
    /// 计算与另一个 bbox 的交并比（Intersection over Union）。
    /// 缓存命中时做"窗口未变"二次校验、以及多个模型结果去重用。
    /// 完全重叠 = 1.0；完全不重叠 = 0.0。
    /// </summary>
    public double IoU(BoundingBox other)
    {
        if (other is null) return 0.0;

        int interX1 = Math.Max(X1, other.X1);
        int interY1 = Math.Max(Y1, other.Y1);
        int interX2 = Math.Min(X2, other.X2);
        int interY2 = Math.Min(Y2, other.Y2);

        int interW = interX2 - interX1;
        int interH = interY2 - interY1;
        if (interW <= 0 || interH <= 0) return 0.0;

        double interArea = interW * (double)interH;
        double unionArea = (Width * (double)Height) + (other.Width * (double)other.Height) - interArea;
        if (unionArea <= 0) return 0.0;
        return interArea / unionArea;
    }

    /// <summary>返回易读的字符串表达，便于日志输出。</summary>
    public override string ToString() => $"bbox[{X1},{Y1},{X2},{Y2}] ({Width}x{Height})";
}
