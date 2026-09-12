# -*- coding: utf-8 -*-
"""OCR 引擎冷/热基线测量（端侧会话任务 C0 §2.1；C5 同条件对照用）。

可复现要求（C0 评审 P2-4）：
- 合成截图参数固定（2100×1400、18 个会话条目块），无随机成分，同参数重放；
- 冷启动分阶段：import_ms（Python import）→ init_ms（构造 RapidOCR）→ first_ocr_ms
  （首次全图 OCR），total 为三者之和。冷启动≠「纯引擎初始化」，结论引用时按阶段拆分；
- 热路径：同进程新建 engine 后连续 OCR N 次、无预热（首样本含预热开销，如实保留）；
- 分位数算法：p50=statistics.median（偶数样本取中间两值均值），p95=nearest-rank
  （sorted[ceil(0.95*n)-1]），max=最大值。逐样本数据全部输出。

用法（仓库根 venv，不碰真实微信）：
    venv/Scripts/python.exe clients/weixin-cli/scripts/ocr_engine_bench.py \
        --cold 3 --hot 30 [--keep-image] 
输出：单行 BENCH_JSON: {json}（ stdout ），另打印人类可读摘要。
"""
import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

W, H = 2100, 1400
ROWS = 18  # 会话列表条目数


def build_image(path: str) -> None:
    from PIL import Image, ImageDraw

    img = Image.new('RGB', (W, H), (245, 245, 245))
    d = ImageDraw.Draw(img)
    for row in range(ROWS):
        y = 120 + row * 70
        d.rectangle([30, y, 90, y + 34], fill=(220, 40, 40))  # 未读角标区
        d.rectangle([130, y, 560, y + 26], fill=(30, 30, 30))  # 名字行
        d.rectangle([130, y + 34, 590, y + 54], fill=(120, 120, 120))  # preview 行
        # 右侧聊天气泡（模拟消息区，给 OCR 提供检测框）
        d.rectangle([700 + (row % 5) * 200, 200 + row * 90, 860 + (row % 5) * 200, 620 + row * 90], fill=(60, 60, 60))
    img.save(path)


COLD_STAGE_CODE = """
import time
t0 = time.perf_counter()
from rapidocr_onnxruntime import RapidOCR
from PIL import Image
t1 = time.perf_counter()
e = RapidOCR()
im = Image.open(r'{IMG}')
t2 = time.perf_counter()
e(im)
t3 = time.perf_counter()
print('BENCH_COLD:' + '{{"import_ms": {i}, "init_ms": {n}, "first_ocr_ms": {f}, "total_ms": {t}}}'.format(
    i=round((t1 - t0) * 1000, 1), n=round((t2 - t1) * 1000, 1), f=round((t3 - t2) * 1000, 1), t=round((t3 - t0) * 1000, 1)))
"""


def run_cold(img_path: str, n: int) -> list[dict]:
    out = []
    for _ in range(n):
        code = COLD_STAGE_CODE.replace('{IMG}', img_path)
        r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
        line = [ln for ln in r.stdout.splitlines() if ln.startswith('BENCH_COLD:')]
        if not line or r.returncode != 0:
            raise SystemExit(f'cold 子进程失败: rc={r.returncode} stderr={r.stderr[:400]}')
        out.append(json.loads(line[-1][len('BENCH_COLD:'):]))
    return out


def run_hot(img_path: str, n: int) -> dict:
    from rapidocr_onnxruntime import RapidOCR
    from PIL import Image

    engine = RapidOCR()
    im = Image.open(img_path)
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        engine(im)
        samples.append(round((time.perf_counter() - t0) * 1000, 1))
    ordered = sorted(samples)
    p95 = ordered[min(len(ordered) - 1, max(0, __import__('math').ceil(0.95 * n) - 1))]
    return {
        'n': n,
        'samples_ms': samples,
        'p50': round(statistics.median(samples), 1),
        'p95': round(p95, 1),
        'max': ordered[-1],
        'algorithm': 'p50=statistics.median; p95/max=nearest-rank',
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--cold', type=int, default=3)
    ap.add_argument('--hot', type=int, default=30)
    ap.add_argument('--keep-image', action='store_true', help='保留合成图（默认用完即删）')
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix='ocr-bench-'))
    img_path = str(tmp / 'input.png')
    build_image(img_path)

    result = {
        'image': {'w': W, 'h': H, 'rows': ROWS, 'deterministic': True},
        'cold': run_cold(img_path, args.cold),
        'hot': run_hot(img_path, args.hot),
    }
    print('BENCH_JSON: ' + json.dumps(result, ensure_ascii=False))
    cold_totals = [c['total_ms'] for c in result['cold']]
    print(f"cold total_ms={cold_totals} | hot p50={result['hot']['p50']} p95={result['hot']['p95']} max={result['hot']['max']} (n={args.hot})")
    if not args.keep_image:
        Path(img_path).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
