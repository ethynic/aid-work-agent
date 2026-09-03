# scripts/cv-ocr-rapid.py — RapidOCR 批量适配器（boss 简历逐段 OCR 主引擎，P2 2026-09-02）
#
# 用法：python cv-ocr-rapid.py <img1> <img2> ...（一次进程批量处理全部段：模型 session 只加载一次）
#       python cv-ocr-rapid.py --bench（自检：合成中文图单次推理计时，stdout 输出 bench=<秒>，
#       供 doctor 报告实测耗时做装机验收；模型加载不计入）
# 每图：PIL 打开 → RapidOCR 推理 → 行重建（y 中心聚类成行，行内按 x 排序、box 文本直接拼接
# 不加空格）→ 写 <img>.rapid.txt（UTF-8 无 BOM，\n 行尾）。stdout 每图一行 file=<i> chars=<n>。
#
# 行重建参照 wecom-cli 生产用法（clients/wecom-cli/drivers/py/chat_ocr.py 的
# load_boxes/cluster_lines）；**不做 2x 放大**（wecom-cli 是企微 UI 小字场景；简历 canvas 字号大，
# 真机基准 2026-09-02：1x 与 2x 正文识别一致（差异仅标点/装饰符级：丨|、：:、全半角括号）且
# 置信度同 0.99，放大白费 ~20% 时间）。
#
# 部署要求：pip install rapidocr_onnxruntime Pillow（模型随 wheel 内置、离线可用、无需下载；
# 版本与仓库 venv 对齐：rapidocr-onnxruntime==1.4.4 + Pillow）。
# 任何异常（依赖缺失/图片读不了/推理失败/写文件失败）：stderr 打印原因，退出码非 0
# —— Node 侧（ocrBatch）据此整批回退 WinRT，绝不因缺 RapidOCR 而失败。
import os
import sys

try:
    # Windows 控制台默认 GBK：中文路径/文本打 stdout/stderr 会 UnicodeEncodeError（wecom-cli 同款坑）
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

SCALE = 1

# onnxruntime CPU 线程数显式限核（真机基准 2026-09-02，28 逻辑核机：默认全核开是 4.2s/段，
# intra_op=8 最快 2.1s/段——核多时全开过订阅反而慢；min(8, 核数) 兜住 4 核客户机不超订阅）
INTRA_OP_THREADS = min(8, os.cpu_count() or 4)


def median_line_h(boxes):
    hs = sorted(b['y1'] - b['y0'] for b in boxes)
    return hs[len(hs) // 2] if hs else 16


def cluster_lines(boxes, y_gap_ratio=0.7):
    """按 y 聚成视觉行；行内按 x 排序。返回 [[box,...],...]（按 y 升序）。同 chat_ocr.py。"""
    if not boxes:
        return []
    line_h = median_line_h(boxes)
    boxes = sorted(boxes, key=lambda b: (b['y0'], b['x0']))
    lines = []
    cur = []
    cur_y = None
    for b in boxes:
        mid = (b['y0'] + b['y1']) / 2
        if cur_y is None or abs(mid - cur_y) <= line_h * y_gap_ratio:
            cur.append(b)
            cur_y = mid if cur_y is None else (cur_y + mid) / 2
        else:
            lines.append(sorted(cur, key=lambda b: b['x0']))
            cur = [b]
            cur_y = mid
    lines.append(sorted(cur, key=lambda b: b['x0']))
    return lines


def ocr_one(engine, img_path):
    """单图 OCR + 行重建。返回重建后的文本（行间 \n，行内 box 文本直接拼接不加空格）。

    PIL Image 用 with 显式关闭（不依赖 GC/refcount），16 段批量下文件句柄即刻释放；
    resize 返回已脱离源图的新 Image，src 关闭后 img 仍可用。"""
    from PIL import Image
    with Image.open(img_path) as src:
        if SCALE > 1:
            # SCALE>1：resize 产物已脱离源图（像素已读入内存），with 退出后仍可用
            img = src.resize((src.width * SCALE, src.height * SCALE), Image.LANCZOS)
        else:
            # SCALE=1：直接用源图省一次拷贝，但 PIL 懒加载像素——必须先 load() 进内存，
            # 否则 with 退出关闭文件句柄后 engine 取像素报 I/O 错误（真机 0.4s 即败，2026-09-02）
            src.load()
            img = src
    try:
        result, _ = engine(img)
    finally:
        if img is not src:
            img.close()
    boxes = []
    for box, text, _score in (result or []):
        t = text.strip()
        if not t:
            continue
        xs = [p[0] / SCALE for p in box]
        ys = [p[1] / SCALE for p in box]
        boxes.append({'x0': min(xs), 'y0': min(ys), 'x1': max(xs), 'y1': max(ys), 'text': t})
    lines = cluster_lines(boxes)
    return '\n'.join(''.join(b['text'] for b in line) for line in lines)


def make_bench_image():
    """自检图：白底黑字两行（中文+数字+英文，覆盖 det+rec 真实路径）。

    字体按 Win10/11 必装顺序探测（微软雅黑→黑体→宋体）；全缺失时 PIL 默认位图字体兜底
    （无中文字形也能跑，det 有笔画可找，bench 只测耗时）。"""
    from PIL import Image, ImageDraw, ImageFont
    font = None
    for name in ('msyh.ttc', 'simhei.ttf', 'simsun.ttc'):
        try:
            font = ImageFont.truetype(f'C:/Windows/Fonts/{name}', 28)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    img = Image.new('RGB', (560, 120), 'white')
    d = ImageDraw.Draw(img)
    d.text((16, 16), '简历读取引擎自检：熟练使用PHP与MySQL开发', fill='black', font=font)
    d.text((16, 64), 'Work experience 2020-2026 PHP开发工程师', fill='black', font=font)
    return img


def detect_dml():
    """是否可用 DirectML GPU 加速：onnxruntime 为 directml 构建时自动启用（默认）。

    - 装的是 `onnxruntime`（CPU 版，rapidocr pip 默认依赖）→ 无 DML → CPU 推理，行为不变；
    - 装了 `onnxruntime-directml`（可选，任意 DX12 GPU/核显即可，无需 NVIDIA）→ 自动走 DML，
      真机实测 2026-09-02（Intel 核显）：预热 0.30s/段 vs CPU 0.87s（~3 倍），识别结果一致；
    - env AID_BOSS_OCR_DML=0 强制关闭（驱动异常排障用）。
    """
    if (os.environ.get('AID_BOSS_OCR_DML') or '').strip() == '0':
        return False
    try:
        import onnxruntime
        return 'DmlExecutionProvider' in onnxruntime.get_available_providers()
    except Exception:
        return False


def make_engine(RapidOCR, use_dml):
    # RapidOCR 类由 main() 局部导入后传入（模块顶部不 import，缺依赖时先走 usage/exit 3 分支）
    kw = dict(intra_op_num_threads=INTRA_OP_THREADS)
    if use_dml:
        # use_dml 必须按模块前缀传（det_/cls_/rec_）：无前缀的只改 Global 节，
        # 而 update_global_to_module 只传播 intra/inter_op 线程参数（真机实证全局版不生效）
        kw.update(det_use_dml=True, cls_use_dml=True, rec_use_dml=True)
    return RapidOCR(**kw)


def main():
    bench = len(sys.argv) == 2 and sys.argv[1] == '--bench'
    if not bench and len(sys.argv) < 2:
        print('usage: cv-ocr-rapid.py <img1> <img2> ... | cv-ocr-rapid.py --bench', file=sys.stderr)
        return 2
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as e:
        print(f'rapidocr_onnxruntime 不可用：{e}（pip install rapidocr_onnxruntime Pillow）', file=sys.stderr)
        return 3
    # 引擎选择：DML 可用即默认启用；init 失败自动落 CPU（驱动异常时不因 GPU 挂掉而整体失败）
    use_dml = detect_dml()
    try:
        engine = make_engine(RapidOCR, use_dml)
    except Exception as e:
        if not use_dml:
            print(f'RapidOCR 引擎初始化失败：{e}', file=sys.stderr)
            return 4
        print(f'DirectML 初始化失败，回退 CPU：{e}', file=sys.stderr)
        use_dml = False
        try:
            engine = make_engine(RapidOCR, False)
        except Exception as e2:
            print(f'RapidOCR 引擎初始化失败：{e2}', file=sys.stderr)
            return 4
    print(f'engine={"dml" if use_dml else "cpu"}')
    if bench:
        import time
        try:
            img = make_bench_image()
        except Exception as e:
            print(f'自检图生成失败：{e}', file=sys.stderr)
            return 7
        try:
            t0 = time.time()
            engine(img)
            print(f'bench={time.time() - t0:.2f}s')
        except Exception as e:
            print(f'自检推理失败：{e}', file=sys.stderr)
            return 8
        print('done=1')
        return 0
    # DML 推理期异常（驱动/显存问题可能 init 成功但推理挂）→ 整批用 CPU 重跑一次再判失败，
    # CPU 重跑覆盖前面已写的结果文件，绝不留半批 DML 半批 CPU 的混合输出
    for attempt_cpu_fallback in (False, True):
        if attempt_cpu_fallback:
            if not use_dml:
                return 5  # 纯 CPU 也失败，无回退可试（具体错误已在下方打印）
            print('DirectML 推理失败，整批回退 CPU 重跑', file=sys.stderr)
            try:
                engine = make_engine(RapidOCR, False)
            except Exception as e:
                print(f'CPU 引擎初始化失败：{e}', file=sys.stderr)
                return 4
            use_dml = False
            print('engine=cpu')
        failed = None
        for i, img_path in enumerate(sys.argv[1:]):
            try:
                text = ocr_one(engine, img_path)
            except Exception as e:
                failed = f'OCR 失败 {img_path}：{e}'
                break
            out_path = img_path + '.rapid.txt'
            try:
                with open(out_path, 'w', encoding='utf-8', newline='') as f:
                    f.write(text)
            except Exception as e:
                failed = f'写文件失败 {out_path}：{e}'
                break
            print(f'file={i} chars={len(text)}')
        if failed is None:
            print(f'done={len(sys.argv) - 1}')
            return 0
        print(failed, file=sys.stderr)
    return 5


if __name__ == '__main__':
    sys.exit(main())
