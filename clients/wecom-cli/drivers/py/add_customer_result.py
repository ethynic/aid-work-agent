# drivers/py/add_customer_result.py — wecom_add_customer 弹窗截图 OCR 定位
# 输入：argv[1] 弹窗截图路径（PrintWindow 全窗口图），argv[2] 模式：
#   result — SearchExternalsWnd「添加客户」对话框（参考尺寸 400x292）：
#       定位检索结果行：微信名、「添加」/「已发送申请」按钮及其中心坐标（图像坐标系）。
#       输出 state: none / not_found / addable / sent
#   reason — InputReasonWnd「发送添加邀请」对话框（参考尺寸 393x244）：
#       定位「发送」按钮中心坐标（图像坐标系）。
#   page — 主窗口通讯录页截图：校验是否落在「新的客户」页签（found: true/false）。
# 输出：stdout 最后一行 ADDCUSTOMER_JSON: {...}（单行 JSON，\u 转义中文，规避控制台编码）。
# 环境缺失（python/rapidocr/PIL 不可用、图片读不出）→ {"error": ..., "message": ...} 单行 + 退出码 0，
# 由 PS 侧归并 DRIVER_JSON ok=false（OCR_UNAVAILABLE → CONFIG_MISSING，其余 → INTERNAL_ERROR）。
import json
import sys

RESULT_NO_MARKERS = ('未找到', '找不到', '无结果', '没有相关')
ADD_BUTTON_TEXT = '添加'
SENT_BUTTON_TEXT = '已发送申请'
SEND_BUTTON_TEXT = '发送'


def emit(payload):
    print('ADDCUSTOMER_JSON: ' + json.dumps(payload, ensure_ascii=True))


def load_boxes(img_path):
    """返回 (boxes, error_payload)。boxes: [{x0,y0,x1,y1,text}]。"""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as e:
        return None, {'error': 'OCR_UNAVAILABLE', 'message': f'rapidocr_onnxruntime 不可用：{e}（pip install rapidocr_onnxruntime）'}
    try:
        engine = RapidOCR()
        result, _ = engine(img_path)
    except Exception as e:
        return None, {'error': 'OCR_FAILED', 'message': f'OCR 执行失败：{e}'}
    boxes = []
    for box, text, _score in (result or []):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        t = text.strip()
        if not t:
            continue
        boxes.append({'x0': min(xs), 'y0': min(ys), 'x1': max(xs), 'y1': max(ys), 'text': t})
    return boxes, None


def center(b):
    return {'x': int((b['x0'] + b['x1']) / 2), 'y': int((b['y0'] + b['y1']) / 2)}


def analyze_result(boxes, img_path):
    # 结果行在输入框之下：标题「添加客户」与输入行都在窗口上部（真机 400x292 下结果行 y≈110-150）
    try:
        from PIL import Image
        _w, h = Image.open(img_path).size
    except Exception:
        h = 0
    row_y_min = h * 0.30 if h else 0

    if any(any(m in b['text'] for m in RESULT_NO_MARKERS) for b in boxes):
        return {'found': True, 'state': 'not_found', 'wechat_name': None, 'button': None}

    sent = next((b for b in boxes if b['text'] == SENT_BUTTON_TEXT), None)
    if sent is not None:
        return {'found': True, 'state': 'sent', 'wechat_name': None, 'button': center(sent)}

    add = next((b for b in boxes if b['text'] == ADD_BUTTON_TEXT and b['y0'] >= row_y_min), None)
    if add is None:
        return {'found': False, 'state': 'none', 'wechat_name': None, 'button': None}

    # 微信名：与「添加」按钮同一视觉行（y 重叠）、位于按钮左侧、非纯数字（排除手机号回显）
    row_mid = (add['y0'] + add['y1']) / 2
    row_h = max(add['y1'] - add['y0'], 10)
    name_candidates = [
        b for b in boxes
        if b is not add
        and abs((b['y0'] + b['y1']) / 2 - row_mid) < row_h
        and b['x1'] <= add['x0'] + 2
        and not b['text'].isdigit()
        and b['text'] not in (ADD_BUTTON_TEXT, SENT_BUTTON_TEXT)
    ]
    name = max(name_candidates, key=lambda b: len(b['text']))['text'] if name_candidates else None
    return {'found': True, 'state': 'addable', 'wechat_name': name, 'button': center(add)}


def analyze_reason(boxes):
    # 「发送」按钮精确匹配（对话框标题「发送添加邀请」含「发送」但不是精确等值，天然排除）
    send = next((b for b in boxes if b['text'] == SEND_BUTTON_TEXT), None)
    if send is None:
        return {'found': False, 'send_button': None}
    return {'found': True, 'send_button': center(send)}


NEW_CUSTOMER_TAB_TEXT = '新的客户'


def analyze_page(boxes):
    # 「新的客户」页签校验：通讯录内容页落在「新的客户」时主窗口截图应能 OCR 到该文本。
    # 读不到即 fail-closed（调用方中止流程），避免在其他页签误点右上角按钮。
    return {'found': any(NEW_CUSTOMER_TAB_TEXT in b['text'] for b in boxes)}


def main():
    if len(sys.argv) < 3 or sys.argv[2] not in ('result', 'reason', 'page'):
        emit({'error': 'BAD_ARGS', 'message': '用法：add_customer_result.py <截图路径> <result|reason|page>'})
        return
    img_path, mode = sys.argv[1], sys.argv[2]
    boxes, err = load_boxes(img_path)
    if err is not None:
        emit(err)
        return
    if mode == 'result':
        emit(analyze_result(boxes, img_path))
    elif mode == 'reason':
        emit(analyze_reason(boxes))
    else:
        emit(analyze_page(boxes))


if __name__ == '__main__':
    main()
