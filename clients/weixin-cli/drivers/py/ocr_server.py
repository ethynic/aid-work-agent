# -*- coding: utf-8 -*-
"""常驻 OCR 服务（端侧会话任务 C2，设计 §6：消除每次启动 Python/加载模型）。

协议（stdin/stdout，每行一个 JSON 请求/响应，UTF-8）：
  请求: {"id": <int>, "image": "<png 绝对路径>", "crop": [x0,y0,x1,y1] 可选}
  响应: {"id": <int>, "ok": true, "boxes": [[text, score, x0, y0, x1, y1], ...]}
        {"id": <int>, "ok": false, "error": "<reason>"}   # 图片缺失/解码失败等
  EOF（stdin 关闭）即退出；崩溃由 TS 侧检测并重启（重启后调用方必须重新对齐水位）。

RapidOCR 只构造一次（常驻）；crop 在 OCR 前裁剪（只识别目标区域，降低耗时）。
"""
import json
import sys

from rapidocr_onnxruntime import RapidOCR

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = RapidOCR()
    return _engine


def ocr_image(path, crop=None):
    from PIL import Image

    img = Image.open(path)
    if crop is not None:
        x0, y0, x1, y1 = crop
        img = img.crop((int(x0), int(y0), int(x1), int(y1)))
    result, _ = get_engine()(img)
    boxes = []
    for box, text, score in (result or []):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        boxes.append([text, round(float(score), 4), min(xs), min(ys), max(xs), max(ys)])
    return boxes


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            rid = req.get("id")
            boxes = ocr_image(req.get("image", ""), req.get("crop"))
            print(json.dumps({"id": rid, "ok": True, "boxes": boxes}, ensure_ascii=False), flush=True)
        except Exception as exc:  # noqa: BLE001 单请求失败不杀进程
            try:
                rid = json.loads(line).get("id")
            except Exception:
                rid = None
            print(json.dumps({"id": rid, "ok": False, "error": str(exc)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
