# PDF 图文配对方法（把产品图准确挂到条目上）

## 1. 先做整本图片普查

```bash
python scripts/probe_pdf.py <pdf> --all
```

输出每页的图片数量与尺寸，据此把页面分成三类：

| 类型 | 特征 | 配对方式 |
|---|---|---|
| 规则表格页 | 每页 3–4 个产品并排，图片等大、同一 y 区间 | 图片按 x0 从左到右排序 = 标题从左到右顺序 |
| 不规则产品页 | 图片大小不一、跨多列多排 | 标题矩形 + 图片 bbox 做"上方 + 横向重叠 + 垂直最近"配对 |
| 效果图页 | 1–2 张大图 + 做法文字 | 整页主视觉作为该空间所有条目的兜底效果图 |

## 2. 不规则页的逐步配对

```bash
python scripts/probe_pdf.py <pdf> 78 79          # 看图片清单 + 文字行
python scripts/probe_pdf.py <pdf> 42 --search "Treadmill"   # 定位标题矩形
```

配对规则（本技能脚本 `auto_match_text` 的实现）：

1. `page.search_for(英文标题)` 得到标题矩形 `R`；
2. 候选图片需满足 `min(img.x1, R.x1) - max(img.x0, R.x0) > 0`（横向有重叠）；
3. 取 `|img.y1 - R.y0|` 最小的那张；
4. 用 `["x", 页号, xref]` 记录结论，避免依赖易变的顺序索引。

## 3. 两个高频破局技巧

- **同一图片对象跨页复用**：`get_image_info` 的 xref 在两页相同，说明是同一张图。
  若 A 页图文关系读得准、B 页含同样 xref，可用 A 页结论反推 B 页。
  （实例：某规范书第 18 页与第 68 页复用同一批灯具/开关图。）
- **PDF 自带图片替代文字**：部分 PDF 会把 alt text（"A light bulb with a green base"、
  "A black outlet with a red light"）渲染在图旁，其坐标与图片重合，可直接反推图中内容。
  这类文字在 `get_text("words")` 里能看到。

**判不出来就不要配**：当两种解释都说得通（例如标签在图上还是图下）时，宁可留空或用空间效果图兜底，
也不要把错的产品图贴进行里。

## 4. 三级取图策略（脚本已实现）

1. `item.img`：编制时已经确认的精确图；
2. `precise_images[code]`：人工核对后集中登记的精确映射；
3. `auto_match_text`：按标题自动匹配；
4. `page_renderings[页号]`：该条目 Ref. 命中某页时，用该页空间效果图；
5. 文件级 `default_img`：整本效果图兜底。

同一区域多行共用同一张效果图是可接受的——用户想要的是"有图可对照"，
但应如实说明哪些是产品图、哪些是效果图兜底。

## 5. 体积控制

- 用 `page.get_pixmap(clip=rect, dpi=130)` 裁剪，再 `tobytes("jpg", jpg_quality=80)`。
- 实测：219 条全部配图，PNG(150dpi) 合计约 40MB，JPEG(130dpi/80) 约 4MB。
- 图片列宽设为约 110px，行高按 `图高 × 0.78 + 8` 兜底。

> 完整实战（规范书类文档的 xref 对照表与踩坑记录）见 `worked_example_spec_book.md`。
