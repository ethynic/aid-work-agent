# drivers/py/unread_list.py — 主窗口截图左栏会话列表 OCR，聚合未读会话
# 输入：微信主窗口截图（PrintWindow 全窗口图）；输出：UNREAD_JSON: {...} 单行 JSON。
# 规则（比例坐标，随窗口尺寸缩放；与 p4-history-capture/ocr_chat.py 同一参考系）：
#   - 会话列表区 = 窗口宽度的 29% 以左，其余整体丢弃
#   - 窗口顶部 8%（标题/搜索区）丢弃；顶部搜索区若无「搜索」框则视为单栏模式
#   - 未读角标 = 行首的红色小数字（OCR 成单独的纯数字文本框，x 很小）
#   - 按 y 聚合：名字是第一行文本，preview 是第二行；没有未读角标的会话不出现
import json
import re
import sys

from rapidocr_onnxruntime import RapidOCR

BADGE_RE = re.compile(r'^\d{1,3}$')


def analyze(img_path):
    from PIL import Image

    w, h = Image.open(img_path).size
    list_right = w * 0.29  # 会话列表区右边界（参考系 2100 宽下 x=610）
    top_y = h * 0.08  # 顶部搜索/标题区
    badge_x_max = w * 0.10  # 未读角标在列内靠左的小 x 区域

    engine = RapidOCR()
    result, _ = engine(img_path)
    left_boxes = []
    for box, text, score in (result or []):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        if x1 >= list_right:
            continue
        t = text.strip()
        if not t:
            continue
        left_boxes.append({'x0': x0, 'y0': y0, 'y1': y1, 'text': t})

    if not left_boxes:
        return {'error': 'SINGLE_COLUMN'}
    # 单栏模式判据：左栏顶部搜索区应存在「搜索」框；缺失即单栏（无左侧会话列表）
    search_present = any(b['y0'] < top_y and '搜索' in b['text'] for b in left_boxes)
    if not search_present:
        return {'error': 'SINGLE_COLUMN'}

    boxes = [b for b in left_boxes if b['y0'] >= top_y]
    if not boxes:
        return {'unread': []}
    boxes.sort(key=lambda b: b['y0'])
    heights = sorted(b['y1'] - b['y0'] for b in boxes)
    line_h = heights[len(heights) // 2] or 20

    # 按 y 聚合成会话条目：相邻文本框纵向间距 > 1.8 倍行高视为新条目
    entries = []
    cur = None
    for b in boxes:
        if cur is None or b['y0'] - cur['last_y'] > line_h * 1.8:
            cur = {'last_y': b['y0'], 'items': []}
            entries.append(cur)
        cur['last_y'] = max(cur['last_y'], b['y0'])
        cur['items'].append(b)

    unread = []
    for e in entries:
        items = sorted(e['items'], key=lambda b: (b['y0'], b['x0']))
        badge = None
        lines = []
        for b in items:
            if badge is None and b['x0'] < badge_x_max and BADGE_RE.match(b['text']):
                badge = int(b['text'])
                continue
            # 同一视觉行的多个文本框按 x 顺序拼接
            if lines and abs(b['y0'] - lines[-1][0]) < line_h * 0.8:
                lines[-1][1] += b['text']
            else:
                lines.append([b['y0'], b['text']])
        if badge is None:
            continue  # 没有未读角标的会话不出现
        name = lines[0][1] if lines else ''
        preview = lines[1][1] if len(lines) > 1 else ''
        unread.append({'name': name, 'preview': preview, 'unread_count': badge})
    return {'unread': unread}


if __name__ == '__main__':
    print('UNREAD_JSON: ' + json.dumps(analyze(sys.argv[1]), ensure_ascii=False))
