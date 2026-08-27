# p4-history-capture — 聊天区 OCR + 按位置区分发言人
# 输入：微信主窗口截图（PrintWindow 全窗口图）；输出：带 [时间]/[我]/[对方] 标签的行
# 分类规则（比例坐标，随窗口尺寸缩放）：
#   - 聊天区 = 窗口宽度的 29% 以右；左侧会话列表整体丢弃
#   - 文本框中心 x > 聊天区约 63% 处 → 我（右对齐绿气泡），否则 → 对方
#   - 窗口顶部 8%（标题栏）与底部 12%（输入区）丢弃；正中的时间分割线单独标注
# 已知边界：纯表情/图片消息 OCR 不到；长消息换行会产生多行（未合并）；
#   链接卡片会识别出标题文字。见 probe.json notes。
import re
import sys
from rapidocr_onnxruntime import RapidOCR

TIME_RE = re.compile(r'(\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日)\s*\d{1,2}:\d{2}')

def classify(img_path, peer_name='对方'):
    from PIL import Image
    w, h = Image.open(img_path).size
    chat_left = w * 0.2905      # 参考系 2100x1771 下 x=610
    center_x = w * 0.633        # 参考系下 x=1330（聊天区中线）
    time_x0, time_x1 = w * 0.545, w * 0.69
    top_y = h * 0.079           # 标题栏
    bottom_y = h * 0.881        # 输入区
    engine = RapidOCR()
    result, _ = engine(img_path)
    rows = []
    for box, text, score in (result or []):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        x0, x1, y = min(xs), max(xs), min(ys)
        if x1 < chat_left or y < top_y or y > bottom_y:
            continue
        if text.strip() in ('发送',):
            continue
        cx = (x0 + x1) / 2
        if TIME_RE.search(text) and time_x0 < cx < time_x1:
            rows.append((y, 'time', text.strip()))
        elif cx > center_x:
            rows.append((y, 'me', text.strip()))
        else:
            rows.append((y, 'peer', text.strip()))
    rows.sort()
    tags = {'time': '[时间]', 'me': '[我]', 'peer': f'[{peer_name}]'}
    return [f'{tags[k]} {t}' for _, k, t in rows]

if __name__ == '__main__':
    peer = sys.argv[2] if len(sys.argv) > 2 else '对方'
    for line in classify(sys.argv[1], peer):
        print(line)
