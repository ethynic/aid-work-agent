#!/usr/bin/env python3
"""sample_color.py — 从产品/材质图中取"精确色"，供效果图提示词锁定颜色（治色差）。

我方接入说明（2026-11-06 移植自 ext_package/qwen-interior-render，逻辑零改动）：
- 图片输入须为本地绝对路径或 URL；file_id（file_xxx）不能直接喂脚本，
  先用 image_read 工具判读拿到其返回的 file_path 再传入。
- 经 skill_execute 执行；依赖 Pillow（容器已内置）。

用法：
  python3 scripts/sample_color.py --image product.jpg --crop 40,120,300,200 --label floor
  python3 scripts/sample_color.py --image product.jpg --label wall --out swatches.json

说明：
- --crop  x,y,w,h（像素，左上原点）；省略则取图像中心 40% 区域。请尽量框住"纯净材质样块"，
  避开家具/人/招牌/灯箱/结构面/接缝高光。
- 支持本地路径或 http(s) URL（URL 需运行环境可联网）。
- 结果打印到 stdout；给了 --out 则把 {label: "#RRGGBB"} 合并写入该 JSON 文件。
- 把得到的 HEX 写进 qwen-image 提示词，如 floor=#DEDAD4, wall=#CAC6BB，
  比"暖白/米色"等模糊词能显著降低色差。
"""
import argparse
import json
import os
import sys

try:
    from PIL import Image
except ImportError:
    sys.exit("需要 Pillow：pip install pillow")


def load_image(src):
    if src.startswith("http://") or src.startswith("https://"):
        from io import BytesIO
        import urllib.request
        with urllib.request.urlopen(src, timeout=30) as r:
            data = r.read()
        return Image.open(BytesIO(data))
    return Image.open(src)


def region(img, crop):
    w, h = img.size
    if crop:
        x, y, cw, ch = crop
        x = max(0, min(x, w - 1)); y = max(0, min(y, h - 1))
        cw = max(1, min(cw, w - x)); ch = max(1, min(ch, h - y))
        return img.crop((x, y, x + cw, y + ch))
    # 中心 40%
    bw, bh = int(w * 0.4), int(h * 0.4)
    x0, y0 = (w - bw) // 2, (h - bh) // 2
    return img.crop((x0, y0, x0 + bw, y0 + bh))


def avg_hex(im):
    im = im.convert("RGB")
    # 用 BOX 重采样到 1x1 取整块平均色（避免逐像素遍历与 getdata 弃用告警）
    r, g, b = im.resize((1, 1), Image.BOX).getpixel((0, 0))
    return "#%02X%02X%02X" % (r, g, b), (r, g, b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--crop", help="x,y,w,h（像素）；省略取中心 40%%")
    ap.add_argument("--label", default="sample")
    ap.add_argument("--out", help="把结果合并写入的 JSON 文件路径")
    a = ap.parse_args()

    crop = None
    if a.crop:
        try:
            crop = [int(v) for v in a.crop.split(",")]
            assert len(crop) == 4
        except Exception:
            sys.exit("--crop 需为 4 个整数：x,y,w,h")

    im = load_image(a.image)
    hexv, rgb = avg_hex(region(im, crop))
    print("%s\t%s\trgb%s" % (a.label, hexv, rgb))

    if a.out:
        data = {}
        if os.path.exists(a.out):
            try:
                with open(a.out, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data[a.label] = hexv
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
