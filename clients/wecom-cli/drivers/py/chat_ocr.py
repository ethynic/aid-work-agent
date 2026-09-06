# drivers/py/chat_ocr.py — wecom-cli M2 OCR 驱动（search/send 共用单一 RapidOCR 入口）
# 输入：argv[1] 截图路径（PrintWindow 全窗口图），argv[2] 模式：
#   search  — SearchResultWindow2 搜索 overlay（参考 400x542）：
#       解析分区（联系人/群聊/聊天记录）结果列表 →
#       {items: [{name, subtitle, section, x, y}]}（x/y = 结果行中心，图像坐标系）
#   title   — 主窗口：读聊天区顶部会话标题（外部联系人形如「名字 @微信」）→ {title}
#   input   — 主窗口：底部输入区空/非空判定（残留草稿检查）→ {input_empty, texts}
#   bubble  — 主窗口：读消息区末尾气泡文本 → {last_message, last_messages, last_messages_norm}
#       （末尾行检出不可靠，终态校验消费 last_messages_norm：任一行归一化后含前缀即过）
#   preview — 主窗口：读会话列表列（x∈[0.16w,0.38w]，排除导航栏）各行 {name, preview}
#       → {rows, texts, texts_norm}（归一化见 normalize_text，比对两侧同规则）
#   searchbox — 主窗口：读顶部搜索框内容 tokens → {texts}
#       （企微搜索框保留上次查询词：搜索前判残留/清空后复核/输入后回读验证用；
#       占位符「搜索」与 × 清空按钮误识由 PS 侧过滤，此处只平铺 tokens）
#   unread  — 主窗口：聚合未读会话（M3）→ {unread: [{name, preview, unread_count, x, y}]}
#       未读角标 = 会话行头像右上角的红色圆形白字数字（像素 blob 检测定位 +
#       裁切放大单独 OCR 读数，读不出兜底 1），y 与会话名称行对齐即归属该行；
#       无角标的会话不出现。x/y = 名称行中心（图像坐标系，供 watch 直接点会话行用）
#   history — 主窗口：消息区行序列（M3）→ {messages: [{side, text, y}]}
#       按 y 排序的视觉行；side ∈ self/peer/timeline（时间分割线如「7月16日 09:01」
#       「08:23」）；side 归属是启发式（peer 看左缘、self 看右缘，长行以左缘优先）
# 预处理：统一 2x 放大（企微 UI 字号小，RapidOCR 对原尺寸小字漏检率高），返回坐标已除回放大倍率。
# 输出：stdout 最后一行 CHATOCR_JSON: {...}（单行 JSON，\u 转义中文，规避控制台编码）。
# 环境缺失 → {"error": ..., "message": ...} 单行 + 退出码 0，由 PS 侧归并
# DRIVER_JSON ok=false（OCR_UNAVAILABLE → CONFIG_MISSING，其余 → INTERNAL_ERROR）。
import json
import re
import sys

SCALE = 2
# 搜索 overlay 分区标题（2026-09-04 实测：新版客户端官方应用账号归「应用提醒」分区；
# 旧三分区仍保留——分区标题是结果行的准入门，缺了会把整段结果丢弃）
SECTION_NAMES = ('联系人', '群聊', '聊天记录', '应用提醒')
# 输入区占位符（空输入框时 OCR 可能读到的灰字；命中视为空）
PLACEHOLDER_MARKERS = ('发送消息', '输入消息', '聊点什么')
# 输入区非内容 UI 文本（发送按钮标签；全/半角括号与 S/5 误识均归一后比对）
BUTTON_LABEL_MARKERS = ('发送(s)', '发送(5)')
# 搜索 overlay 底部按钮（不是结果项）的精确标记
SEARCH_EXCLUDE_MARKERS = ('全局搜索',)
# 底部按钮模糊特征：OCR 常把「索」误识为形近字（实测「搜素」），精确标记会被绕过，
# 底部 15% 区域内命中任一特征即按底部按钮排除
BOTTOM_BUTTON_FEATURES = ('全局', 'Ctrl', '查找')
# 结果行右侧日期 box（06/26 等），不是条目名称/副标题内容
DATE_BOX_RE = re.compile(r'^\d{1,2}/\d{1,2}$')
# 未读角标：纯数字小文本框（1-3 位，红色圆形白字，在会话列表列右半部）
BADGE_RE = re.compile(r'^\d{1,3}$')
# 会话行右侧时间列（09:08 / 08/24 / 昨天 / 星期二 / 刚刚 / 7月16日），
# 归属角标行名称/preview 前须剔除（仅对列右半部 box 生效，避免误吃名称）
TIME_TEXT_RES = tuple(re.compile(p) for p in (
    r'^\d{1,2}:\d{2}$',
    r'^\d{1,2}/\d{1,2}$',
    # 时间列宽松误识（实测「07/09」读成「60/L0」）：纯数字/形近字母 + 分隔符的短串
    r'^[0-9OolL]{1,2}[/\\:][0-9OolL]{1,2}$',
    r'^\d{1,2}月\d{1,2}日.*$',
    r'^星期[一二三四五六日天].*$',
    r'^昨[天日].*$',
    r'^刚刚$',
    r'^[上下早]午.*$',
))
# 消息区时间分割线（history 模式）。必须锚定整行（允许尾部跟时间）：
# 宽松前缀匹配会把「昨天发的文件收到了吗」「7月16日的报表请查收」这类
# 真实消息误判为分割线而从 watch 增量里漏掉（漏消息方向，不可接受）；
# 反向（分割线行带 OCR 尾噪而不匹配）只降级为普通聊天行，属噪声不丢消息。
TIMELINE_RES = tuple(re.compile(p) for p in (
    r'^\d{4}年\d{1,2}月\d{1,2}日(\s*\d{1,2}:\d{2})?$',
    r'^\d{1,2}月\d{1,2}日(\s*\d{1,2}:\d{2})?$',
    r'^\d{1,2}:\d{2}$',
    r'^昨天(\s*\d{1,2}:\d{2})?$',
    r'^星期[一二三四五六日天](\s*\d{1,2}:\d{2})?$',
    r'^\d{4}/\d{1,2}/\d{1,2}(\s*\d{1,2}:\d{2})?$',
))


def chat_area_x_min(w):
    # 聊天区左边界：左侧会话列表栏宽为客户端固定像素（2026-09-04 实测 2196/2916 两种
    # 窗宽下列内日期/预览 x1≈594，聊天区自 x≈620 起）。列表栏不随窗口宽度按比例伸缩，
    # 比例阈值（旧 0.28w，在 2916 宽的外部联系人布局下=831）会把真实 peer 气泡
    # （x0≈667）整段排除（真机实测漏消息）。取 max(0.10w, 620) 像素锚定。
    return max(w * 0.10, 620)


def has_right_sidebar(boxes, w, h):
    # 外部联系人（@微信）会话右侧出现「智能总结」侧栏（客户需求/客户意向/成交卡点…），
    # 全部文本 x0≥0.76w、x1≤0.83w，跨多行分布；self 气泡右缘贴聊天区右边（x1≈0.92w）。
    # 以「≥3 个 x0∈(0.74w,0.95w) 且 x1<0.90w 的 box、纵向跨度>0.3h」判侧栏存在。
    col = [b for b in boxes
           if w * 0.74 < b['x0'] < w * 0.95 and b['x1'] < w * 0.90
           and h * 0.07 < b['y0'] < h * 0.85]
    if len(col) < 3:
        return False
    ys = sorted(b['y0'] for b in col)
    return (ys[-1] - ys[0]) > h * 0.3


def emit(payload):
    print('CHATOCR_JSON: ' + json.dumps(payload, ensure_ascii=True))


_OCR_ENGINE = None


def get_ocr_engine():
    """RapidOCR 实例惰性单例（初始化秒级，同一进程内多模式/多裁切复用）。"""
    global _OCR_ENGINE
    if _OCR_ENGINE is not None:
        return _OCR_ENGINE, None
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as e:
        return None, {'error': 'OCR_UNAVAILABLE', 'message': f'rapidocr_onnxruntime 不可用：{e}（pip install rapidocr_onnxruntime）'}
    try:
        _OCR_ENGINE = RapidOCR()
    except Exception as e:
        return None, {'error': 'OCR_FAILED', 'message': f'OCR 引擎初始化失败：{e}'}
    return _OCR_ENGINE, None


def load_boxes(img_path):
    """OCR + 2x 放大；返回 (boxes, error_payload)。boxes 坐标已除回 SCALE（原图坐标系）。"""
    engine, err = get_ocr_engine()
    if err is not None:
        return None, err
    try:
        from PIL import Image
    except Exception as e:
        return None, {'error': 'OCR_UNAVAILABLE', 'message': f'PIL 不可用：{e}（pip install Pillow）'}
    try:
        img = Image.open(img_path)
        w, h = img.size
        img = img.resize((w * SCALE, h * SCALE), Image.LANCZOS)
        result, _ = engine(img)
    except Exception as e:
        return None, {'error': 'OCR_FAILED', 'message': f'OCR 执行失败：{e}'}
    boxes = []
    for box, text, _score in (result or []):
        xs = [p[0] / SCALE for p in box]
        ys = [p[1] / SCALE for p in box]
        t = text.strip()
        if not t:
            continue
        boxes.append({'x0': min(xs), 'y0': min(ys), 'x1': max(xs), 'y1': max(ys), 'text': t})
    return boxes, None


def median_line_h(boxes):
    hs = sorted(b['y1'] - b['y0'] for b in boxes)
    return hs[len(hs) // 2] if hs else 16


def cluster_lines(boxes, y_gap_ratio=0.7):
    """按 y 聚成视觉行；行内按 x 排序。返回 [[box,...],...]（按 y 升序）。"""
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


def line_text(line):
    return ''.join(b['text'] for b in line)


def line_center_y(line):
    return sum((b['y0'] + b['y1']) / 2 for b in line) / len(line)


def line_center_x(line):
    return sum((b['x0'] + b['x1']) / 2 for b in line) / len(line)


def normalize_text(s):
    """归一化（终态校验前缀比对两侧统一规则，PS 侧 ConvertTo-WeComNormalized 同规则）：
    去全部空白、全角 ASCII 标点/字母/数字转半角（U+FF01–FF5E 平移 0xFEE0，
    覆盖：，！？；（）等）、常见全角标点（。、～）转半角、大小写折叠。"""
    out = []
    for ch in s:
        o = ord(ch)
        if ch.isspace():
            continue
        if 0xFF01 <= o <= 0xFF5E:
            out.append(chr(o - 0xFEE0))
            continue
        out.append({'。': '.', '、': ',', '～': '~'}.get(ch, ch))
    return ''.join(out).casefold()


# ---------- search：SearchResultWindow2 结果列表 ----------

def is_bottom_button(text, y_center, h):
    """底部「进入全局搜索 (Ctrl+Alt+F)」按钮判定：精确标记 + 底部 15% 区域模糊特征。"""
    if any(m in text for m in SEARCH_EXCLUDE_MARKERS):
        return True
    if h and y_center > h * 0.85 and any(f in text for f in BOTTOM_BUTTON_FEATURES):
        return True
    return False


def analyze_search(boxes, img_path):
    try:
        from PIL import Image
        w, h = Image.open(img_path).size
    except Exception:
        w, h = 0, 0
    # 不按 y 过滤顶部：分区标题可能落在顶部 10% 内（实测 400x542 下「联系人」y≈21），
    # 按 y 排除会整段丢分区；搜索框回显由「分区标题门」兜底（首个分区标题之前的行一律丢弃）。
    # 日期 box（右侧 06/26 等）不是条目内容，预先剔除。
    boxes = [b for b in boxes if not DATE_BOX_RE.match(b['text'])]
    lines = cluster_lines(boxes)
    # 头像栏（x1 < 0.14w）误识字符（实测头像被读成 '8' 混入名称行）：
    # 该行存在文本列 box（x0 ≥ 0.14w）时丢弃头像栏 box；整行仅头像栏（如分区标题行 x0≈12）保留。
    if w:
        text_col_x = w * 0.14
        cleaned = []
        for line in lines:
            if any(b['x0'] >= text_col_x for b in line):
                line = [b for b in line if b['x1'] >= text_col_x]
            if line:
                cleaned.append(line)
        lines = cleaned

    items = []
    section = ''
    for i, line in enumerate(lines):
        text = line_text(line)
        if not text:
            continue
        # 分区标题：整行精确等于分区名（任何 y 都认，含顶部）
        if text in SECTION_NAMES:
            section = text
            continue
        # 底部「进入全局搜索」按钮等非结果行
        if is_bottom_button(text, line_center_y(line), h):
            continue
        if not section:
            continue  # 分区标题之前的内容（搜索框残留等）丢弃
        name = line[0]['text']
        subtitle = ''.join(b['text'] for b in line[1:])
        # 两行式条目（名称行 + 副标题行，左对齐）：下一行 x0 与本行相近且不是分区/按钮 → 并作副标题
        if not subtitle and i + 1 < len(lines):
            nxt = lines[i + 1]
            ntext = line_text(nxt)
            if (ntext and ntext not in SECTION_NAMES
                    and not is_bottom_button(ntext, line_center_y(nxt), h)
                    and abs(nxt[0]['x0'] - line[0]['x0']) < 12):
                subtitle = ntext
                lines[i + 1] = []  # 消费掉副标题行
        items.append({
            'name': name,
            'subtitle': subtitle,
            'section': section,
            'x': int(line_center_x(line)),
            'y': int(line_center_y(line)),
        })
    return {'items': items}


# ---------- title：聊天区顶部会话标题 ----------

def analyze_title(boxes, img_path):
    try:
        from PIL import Image
        w, h = Image.open(img_path).size
    except Exception:
        return {'title': ''}
    # 标题栏行带 [0.15w, y<0.06h]（2026-09-04 实测修订）：标题 y0≈0.026-0.035h、
    # x0 0.20w（外部联系人，右侧「智能总结」侧栏挤压聊天区）/0.296w（普通会话）。
    # 旧带宽 y<0.12h + x>x_min 在外部联系人布局下会把消息区时间线（y≈0.074h）与
    # 侧栏文本混进 band 拼成「昨天22:28服务点」这类伪标题（真机实测）。
    band = [b for b in boxes if b['y0'] < h * 0.06 and b['x0'] > w * 0.15]
    lines = cluster_lines(band)
    for line in lines:
        text = line_text(line).strip()
        if text:
            return {'title': text}
    return {'title': ''}


# ---------- input：底部输入区空/非空判定 ----------

def normalize_button_label(t):
    return t.replace('（', '(').replace('）', ')').replace(' ', '').lower()


def analyze_input(boxes, img_path):
    try:
        from PIL import Image
        w, h = Image.open(img_path).size
    except Exception:
        return {'input_empty': False, 'texts': []}
    x_min = chat_area_x_min(w)
    # 输入区 band [0.855h, 0.97h]：2026-09-04 实测工具栏图标行在 ≈0.83h（碎字「X·四·
    # 回口·」会被当草稿文本误判非空 → send 恒拒发），文本区在其下、发送按钮行之上；
    # 外部联系人布局下右侧「智能总结」侧栏的「立即总结」按钮（≈0.89h）也在该 y 带内，
    # 必须随侧栏一起排除（真机实测被误判为残留草稿 → send 拒发）
    if has_right_sidebar(boxes, w, h):
        boxes = [b for b in boxes if b['x0'] <= w * 0.74]
    band = [b for b in boxes if h * 0.855 <= b['y0'] <= h * 0.97 and b['x0'] > x_min]
    texts = [b['text'] for b in sorted(band, key=lambda b: (b['y0'], b['x0']))]
    # 发送按钮标签（「发送(S)」，实测固定出现在输入区右下角）不是草稿内容
    real = [t for t in texts
            if not any(m in t for m in PLACEHOLDER_MARKERS)
            and normalize_button_label(t) not in BUTTON_LABEL_MARKERS]
    return {'input_empty': len(real) == 0, 'texts': texts}


# ---------- bubble：消息区最后一条气泡 ----------

def analyze_bubble(boxes, img_path):
    try:
        from PIL import Image
        w, h = Image.open(img_path).size
    except Exception:
        return {'last_message': '', 'last_messages': []}
    x_min = chat_area_x_min(w)
    # 消息区上界 0.80h（输入工具栏 ≈0.83h）；外部联系人布局排除右侧「智能总结」侧栏
    if has_right_sidebar(boxes, w, h):
        boxes = [b for b in boxes if b['x0'] <= w * 0.74]
    area = [b for b in boxes if h * 0.15 <= b['y0'] <= h * 0.80 and b['x0'] > x_min]
    lines = cluster_lines(area)
    texts = [line_text(line).strip() for line in lines]
    texts = [t for t in texts if t]
    # last_messages（末尾最多 5 行）：气泡之下可能有输入区提示/工具栏/时间戳装饰行的
    # OCR 碎字（真机实测末尾行检出「X·三8」），last_message 末尾行检出不可靠——
    # 调用方必须消费 last_messages_norm 数组按「任一行归一化后含前缀」判定
    tail = texts[-5:]
    return {
        'last_message': texts[-1] if texts else '',
        'last_messages': tail,
        'last_messages_norm': [normalize_text(t) for t in tail],
    }


# ---------- preview：左栏会话列表 {name, preview} ----------

def analyze_preview(boxes, img_path):
    try:
        from PIL import Image
        w, h = Image.open(img_path).size
    except Exception:
        return {'rows': [], 'texts': [], 'texts_norm': []}
    # 会话列表列：中心 x∈[0.08w, 0.30w]（2026-09-04 实测，同 unread；x<0.08w 是
    # 左侧导航栏（邮件/文档/待办/会议…，真机实测曾整列误入），y 从搜索框之下（0.07h）开始
    col = [b for b in boxes
           if w * 0.08 <= (b['x0'] + b['x1']) / 2 <= w * 0.30 and b['y0'] >= h * 0.07]
    lines = cluster_lines(col)
    # texts：会话列表列全部行的平铺文本（OCR 行配对不稳定，调用方做「任一行含
    # 目标名/前缀」判定时用归一化后的 texts_norm 比 rows 稳）
    texts = [t for t in (line_text(line).strip() for line in lines) if t]
    rows = []
    i = 0
    while i < len(lines):
        line = lines[i]
        name = line_text(line).strip()
        preview = ''
        # 名称行下一行（同列缩进）为 preview
        if i + 1 < len(lines):
            nxt = lines[i + 1]
            if abs(nxt[0]['x0'] - line[0]['x0']) < 16:
                preview = line_text(nxt).strip()
                i += 1
        if name:
            rows.append({'name': name, 'preview': preview})
        i += 1
    return {'rows': rows, 'texts': texts, 'texts_norm': [normalize_text(t) for t in texts]}


# ---------- searchbox：主窗口顶部搜索框内容（残留检查/输入回读） ----------

def analyze_searchbox(boxes, img_path):
    try:
        from PIL import Image
        w, h = Image.open(img_path).size
    except Exception:
        return {'texts': []}
    # 搜索框区域（像素锚定）：2026-09-04 实测框体固定在左栏内 x∈[154,505]、y∈[44,100]
    # （不随窗宽伸缩；外部联系人布局主窗口撑宽到 2916 后，右侧聊天区标题「陆伟@微信」
    # x0≈0.222w 会探进比例带，像素带 [140,510] 把它排除在外）。框内文本 x0≈221，
    # 导航栏角标误识 x0≈53。
    band = [b for b in boxes if h * 0.02 <= b['y0'] < h * 0.065 and 140 <= b['x0'] <= 510]
    texts = [b['text'] for b in sorted(band, key=lambda b: (b['y0'], b['x0']))]
    return {'texts': texts}


# ---------- unread：左栏会话列表未读聚合（M3） ----------

def is_time_box(b, w):
    """会话行右侧时间列 box（中心 x 在列右半部且文本像时间）。"""
    # 2026-09-04 实测时间列中心 ≈0.257w、会话名中心 ≈0.128-0.153w，阈值取 0.17w
    if (b['x0'] + b['x1']) / 2 < w * 0.17:
        return False
    return any(r.match(b['text']) for r in TIME_TEXT_RES)


# 红色角标圆掩码阈值（企微未读角标 ≈ #FA5151 系；导航栏红点同色系，
# 靠 x 范围限定在会话列表头像带排除）
def is_badge_red(p):
    r, g, b = p
    return r > 170 and g < 110 and b < 110 and r - g > 90 and r - b > 90


def detect_badge_blobs(img, w, h):
    """未读角标（红色圆形，在会话行头像的右上角，x≈0.09-0.11w）像素级检测。
    RapidOCR 对角标内白字小数字漏检率高（真机实测整角标无 OCR box），
    必须先按红色像素找到圆的位置，再裁切放大单独 OCR 读数。
    返回 [{x0,x1,y0,y1,cy}]（y 中心与会话名称行对齐）。"""
    px = img.convert('RGB').load()
    # 2026-09-04 实测：角标在头像（x≈[0.070w,0.105w]）右上角，x≈[0.09w,0.106w]；
    # 旧标定 [0.15w,0.26w] 完全错过角标。取 [0.05w,0.14w]：含头像带、排除导航栏
    # 红点（x<0.04w）
    x_lo, x_hi = int(w * 0.05), int(w * 0.14)
    y_lo = int(h * 0.06)
    red_rows = []
    for y in range(y_lo, h):
        cnt = 0
        for x in range(x_lo, x_hi):
            if is_badge_red(px[x, y]):
                cnt += 1
        if cnt >= 2:
            red_rows.append(y)
    # 连续红色行段（间隙 ≤4px）聚成 blob
    blobs = []
    cur = []
    for y in red_rows:
        if cur and y - cur[-1] > 4:
            blobs.append(cur)
            cur = []
        cur.append(y)
    if cur:
        blobs.append(cur)
    out = []
    for rows in blobs:
        y0, y1 = rows[0], rows[-1]
        if y1 - y0 < 8:  # 噪点/小红点（无数字的纯红点提示高约 8px 以下也滤掉）
            continue
        xs = []
        for y in rows:
            for x in range(x_lo, x_hi):
                if is_badge_red(px[x, y]):
                    xs.append(x)
        if not xs:
            continue
        out.append({'x0': min(xs), 'x1': max(xs), 'y0': y0, 'y1': y1, 'cy': (y0 + y1) / 2})
    return out


def binarize_badge(crop):
    """红底白字 → 黑字白底（灰度阈值反色，提高白字小数字 OCR 命中率）。"""
    gray = crop.convert('L')
    return gray.point(lambda v: 0 if v > 150 else 255)


def read_badge_count(img, blob, engine):
    """裁切角标圆（外扩 + 5x 放大）单独 OCR 读数字；原图与二值化各试一次。
    读不出兜底 1：有红色角标必有未读，数字只影响 diff 幅度不影响候选触发。"""
    from PIL import Image
    pad = 4
    crop = img.crop((max(0, blob['x0'] - pad), max(0, blob['y0'] - pad),
                     blob['x1'] + pad + 1, blob['y1'] + pad + 1))
    crop = crop.resize((crop.width * 5, crop.height * 5), Image.LANCZOS)
    for variant in (crop, binarize_badge(crop)):
        try:
            result, _ = engine(variant)
        except Exception:
            continue
        for _box, text, _score in (result or []):
            t = text.strip()
            if BADGE_RE.match(t):
                return int(t)
    return 1


def analyze_unread(boxes, img_path):
    try:
        from PIL import Image
        img = Image.open(img_path)
        w, h = img.size
    except Exception:
        return {'unread': []}
    engine, eng_err = get_ocr_engine()
    if eng_err is not None:
        return {'unread': []}
    blobs = detect_badge_blobs(img, w, h)
    if not blobs:
        return {'unread': []}
    # 会话列表列文本行：中心 x∈[0.08w,0.30w]（2026-09-04 实测会话名中心 ≈0.128-0.153w、
    # preview ≈0.128-0.193w；旧标定 [0.16w,0.38w] 会把整列漏光）；
    # x<0.08w 是左侧导航栏（含「消息 8」「我的企业 6」等导航角标，必须排除）；
    # 剔除会话行右侧时间列（09:08 / 08/24 / 昨天 / 刚刚 等，中心 ≈0.257w，见 is_time_box）
    text_boxes = [b for b in boxes
                  if w * 0.08 <= (b['x0'] + b['x1']) / 2 <= w * 0.30 and b['y0'] >= h * 0.07
                  and not is_time_box(b, w)]
    lines = cluster_lines(text_boxes)
    line_h = median_line_h(text_boxes)

    unread = []
    used = set()
    for blob in sorted(blobs, key=lambda b: b['cy']):
        # 归属行：y 中心最近的文本行（角标在头像右上角，与名称行同高），
        # 距离超 1.5 行高视为游离角标（如分组计数）丢弃
        best_i, best_d = None, line_h * 1.5
        for i, line in enumerate(lines):
            if i in used:
                continue
            d = abs(line_center_y(line) - blob['cy'])
            if d <= best_d:
                best_i, best_d = i, d
        if best_i is None:
            continue
        used.add(best_i)
        name_line = lines[best_i]
        name = line_text(name_line).strip()
        if not name:
            continue
        preview = ''
        if best_i + 1 < len(lines):
            nxt = lines[best_i + 1]
            # 下一行是同会话 preview 的条件：纵向间距在 2.2 行高内且左缘缩进相近
            if (line_center_y(nxt) - line_center_y(name_line) <= line_h * 2.2
                    and abs(nxt[0]['x0'] - name_line[0]['x0']) < 16):
                preview = line_text(nxt).strip()
        unread.append({
            'name': name,
            'preview': preview,
            'unread_count': read_badge_count(img, blob, engine),
            'x': int(line_center_x(name_line)),
            'y': int(line_center_y(name_line)),
        })
    return {'unread': unread}


# ---------- history：消息区行序列 + side 归属（M3） ----------

def classify_side(x0, x1, x_min, w):
    """side 启发式：左缘起始于消息区左缘 → peer；右缘贴近窗口右缘 → self；
    其余按行中心与消息区中点比较。长行可能两端都贴边，左缘优先（peer 长段落
    左对齐是常态；self 长行误判为 peer 是已知取舍，调用方不得依赖 side 做安全判定）。"""
    if x0 <= x_min + w * 0.12:
        return 'peer'
    if x1 >= w * 0.92:
        return 'self'
    center = (x0 + x1) / 2
    return 'self' if center >= (x_min + w) / 2 else 'peer'


def analyze_history(boxes, img_path):
    try:
        from PIL import Image
        w, h = Image.open(img_path).size
    except Exception:
        return {'messages': []}
    x_min = chat_area_x_min(w)
    # 消息区：标题带之下（0.12h）、输入工具栏之上（≈0.83h，上界卡 0.80h），排除会话
    # 列表列；外部联系人布局下排除右侧「智能总结」侧栏（真机实测侧栏标签会被当成
    # self 消息整段混入）
    if has_right_sidebar(boxes, w, h):
        boxes = [b for b in boxes if b['x0'] <= w * 0.74]
    area = [b for b in boxes if h * 0.12 <= b['y0'] <= h * 0.80 and b['x0'] > x_min]
    lines = cluster_lines(area)
    messages = []
    for line in lines:
        text = line_text(line).strip()
        if not text:
            continue
        x0 = min(b['x0'] for b in line)
        x1 = max(b['x1'] for b in line)
        if any(r.match(text) for r in TIMELINE_RES):
            side = 'timeline'
        else:
            side = classify_side(x0, x1, x_min, w)
        messages.append({'side': side, 'text': text, 'y': int(line_center_y(line))})
    return {'messages': messages}


def main():
    modes = {'search': analyze_search, 'title': analyze_title, 'input': analyze_input,
             'bubble': analyze_bubble, 'preview': analyze_preview, 'searchbox': analyze_searchbox,
             'unread': analyze_unread, 'history': analyze_history}
    if len(sys.argv) < 3 or sys.argv[2] not in modes:
        emit({'error': 'BAD_ARGS', 'message': '用法：chat_ocr.py <截图路径> <search|title|input|bubble|preview|searchbox|unread|history>'})
        return
    img_path, mode = sys.argv[1], sys.argv[2]
    boxes, err = load_boxes(img_path)
    if err is not None:
        emit(err)
        return
    emit(modes[mode](boxes, img_path))


if __name__ == '__main__':
    main()
