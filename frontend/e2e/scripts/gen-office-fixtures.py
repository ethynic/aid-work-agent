#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「前端 Office 预览」手动验收与组件测试样例文件。

在容器 aid-agent-api 内执行（仓库整体挂载于 /app）：
    docker exec aid-agent-api python /app/frontend/e2e/scripts/gen-office-fixtures.py

输出 5 个文件到 /app/test_uploads/office_preview/（宿主机对应仓库根 test_uploads/office_preview/）：
    multisheet.xlsx  3 个 sheet，中文表头与数据，含合并单元格 —— 验收基本预览与 sheet 切换
    bigrows.xlsx     单 sheet 1250 行真实感数据 —— 验收 1000 行截断提示
    oversize.xlsx    压缩后 >10MB(10485760 字节) —— 验收 Excel 大小门槛
    sample.pptx      4 页（标题页 / 文字+形状 / 彩色主题 / 多元素混排） —— 验收 PPT 渲染与翻页
    sample.docx      多级标题 + 段落 + 表格 —— 验收 DOCX 预览回归

脚本可重入：重跑覆盖同名文件；oversize 生成后用 os.path.getsize 校验，不足则扩大规模重写。
随机数据使用固定种子，保证重跑产物规模一致。
"""
import os
import random
import datetime

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from docx import Document
from docx.shared import Pt as DocPt

OUT_DIR = "/app/test_uploads/office_preview"
OVERSIZE_MIN_BYTES = 10 * 1024 * 1024  # 10485760，须严格大于
OVERSIZE_ROW_ATTEMPTS = [60000, 100000, 160000, 260000]  # 逐级扩大直至超过门槛

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(name="微软雅黑", bold=True, color="FFFFFF", size=11)
TITLE_FONT = Font(name="微软雅黑", bold=True, size=14, color="1F4E79")


def _style_header(ws, col_count):
    for col in range(1, col_count + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")


def _set_widths(ws, widths):
    for idx, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = w


def gen_multisheet(path):
    """3 个 sheet、中文表头与数据、含合并单元格，单 sheet ≤200 行。"""
    wb = Workbook()

    ws = wb.active
    ws.title = "项目概览"
    ws["A1"] = "2026 年 Q3 项目预算总览"
    ws.merge_cells("A1:E1")  # 合并单元格：验收预览渲染
    ws["A1"].font = TITLE_FONT
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.append(["项目名称", "负责人", "预算(万元)", "已用(万元)", "状态"])
    _style_header(ws, 5)
    rng = random.Random(20260901)
    projects = ["智能问答平台", "知识库检索", "移动端适配", "权限中台", "数据看板", "消息通道改造"]
    owners = ["陈明", "李婷", "王浩", "赵雪", "刘洋", "周倩"]
    states = ["进行中", "已结项", "风险中", "待启动"]
    for i in range(2, 22):  # 20 行数据
        ws.append([
            f"{rng.choice(projects)}-{i:02d}",
            rng.choice(owners),
            rng.randint(20, 300),
            rng.randint(5, 280),
            rng.choice(states),
        ])
    _set_widths(ws, [18, 10, 12, 12, 10])

    ws2 = wb.create_sheet("成员名单")
    ws2["A1"] = "研发中心成员名单"
    ws2.merge_cells("A1:D1")
    ws2["A1"].font = TITLE_FONT
    ws2["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws2.append(["姓名", "部门", "职位", "入职日期"])
    _style_header(ws2, 4)
    depts = ["后端组", "前端组", "算法组", "测试组", "运维组"]
    roles = ["工程师", "高级工程师", "技术专家", "组长"]
    base = datetime.date(2019, 1, 10)
    for i in range(2, 42):  # 40 行数据
        ws2.append([
            f"{rng.choice(owners[:4])}{rng.choice(['伟', '芳', '军', '静', '磊'])}",
            rng.choice(depts),
            rng.choice(roles),
            (base + datetime.timedelta(days=rng.randint(0, 2500))).isoformat(),
        ])
    _set_widths(ws2, [12, 12, 14, 14])

    ws3 = wb.create_sheet("月度统计")
    ws3.append(["月份", "收入(万元)", "支出(万元)", "净额(万元)"])
    _style_header(ws3, 4)
    for m in range(1, 13):
        income = rng.randint(180, 460)
        expense = rng.randint(120, 380)
        ws3.append([f"2026-{m:02d}", income, expense, income - expense])
    note_row = 15
    ws3.cell(row=note_row, column=1, value="备注：以上为示例数据，仅供预览验收使用")
    ws3.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=4)
    ws3.cell(row=note_row, column=1).font = Font(italic=True, color="808080")
    _set_widths(ws3, [12, 12, 12, 12])

    wb.save(path)


def gen_bigrows(path):
    """单 sheet 1250 行真实感订单数据，验收 1000 行截断提示。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "订单明细"
    ws.append(["订单编号", "下单日期", "客户姓名", "商品名称", "数量", "单价(元)", "金额(元)", "支付方式", "订单状态"])
    _style_header(ws, 9)
    rng = random.Random(20260902)
    surnames = ["王", "李", "张", "刘", "陈", "杨", "赵", "黄", "周", "吴"]
    given = ["伟", "芳", "秀英", "敏", "静", "磊", "军", "洋", "勇", "艳", "杰", "娟", "涛", "明", "超", "霞"]
    goods = [
        ("无线机械键盘", 299.0), ("27寸显示器", 1299.0), ("升降办公桌", 1899.0),
        ("人体工学椅", 1099.0), ("降噪耳机", 799.0), ("激光投影仪", 4599.0),
        ("便携打印机", 1290.0), ("会议摄像头", 699.0), ("扩展坞", 329.0),
        ("固态硬盘 1TB", 559.0), ("多功能传真机", 2199.0), ("白板笔套装", 39.9),
    ]
    pays = ["微信支付", "支付宝", "企业转账", "银行卡"]
    states = ["已完成", "已发货", "待发货", "已取消", "退款中"]
    start = datetime.date(2026, 7, 1)
    rows = 1250
    for i in range(rows):
        name = rng.choice(goods)
        qty = rng.randint(1, 20)
        price = name[1]
        ws.append([
            f"DD2026{90000 + i:05d}",
            (start + datetime.timedelta(days=rng.randint(0, 91))).isoformat(),
            rng.choice(surnames) + rng.choice(given),
            name[0],
            qty,
            price,
            round(qty * price, 2),
            rng.choice(pays),
            rng.choice(states),
        ])
    _set_widths(ws, [14, 12, 10, 16, 8, 10, 12, 10, 10])
    wb.save(path)


def _write_oversize(path, rows):
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("大数据量测试")
    ws.append([f"列{i}" for i in range(1, 17)])
    rng = random.Random(20261010)
    for _ in range(rows):
        rec = []
        for col in range(16):
            if col % 2 == 0:
                rec.append(rng.random())  # 全精度随机小数，难以压缩
            else:
                rec.append("%032x" % rng.getrandbits(128))  # 随机 16 进制文本
        ws.append(rec)
    wb.save(path)


def gen_oversize(path):
    """压缩后 >10MB 的 xlsx：随机数值/文本撑体积，写后 getsize 校验，不足则扩大规模重写。"""
    chosen = None
    for rows in OVERSIZE_ROW_ATTEMPTS:
        _write_oversize(path, rows)
        size = os.path.getsize(path)
        print(f"  oversize.xlsx 尝试 {rows} 行 -> {size} 字节 ({size / 1024 / 1024:.2f} MB)")
        if size > OVERSIZE_MIN_BYTES:
            chosen = (rows, size)
            break
    if chosen is None:
        raise RuntimeError(f"所有规模尝试均未超过 {OVERSIZE_MIN_BYTES} 字节门槛")
    return chosen[1]


def gen_pptx(path):
    """4 页 PPT：标题页、文字+形状页、彩色主题页、多元素混排页。"""
    prs = Presentation()
    blank = prs.slide_layouts[6]
    navy = RGBColor(0x1F, 0x4E, 0x79)
    white = RGBColor(0xFF, 0xFF, 0xFF)

    # 第 1 页：标题页
    s1 = prs.slides.add_slide(blank)
    band = s1.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(2.2), Inches(10), Inches(1.6))
    band.fill.solid()
    band.fill.fore_color.rgb = navy
    band.line.fill.background()
    tb = s1.shapes.add_textbox(Inches(0.5), Inches(2.35), Inches(9), Inches(0.9))
    tf = tb.text_frame
    p = tf.paragraphs[0]
    p.text = "企业智能代理系统"
    p.alignment = PP_ALIGN.CENTER
    p.font.size = Pt(40)
    p.font.bold = True
    p.font.color.rgb = white
    sub = s1.shapes.add_textbox(Inches(0.5), Inches(3.9), Inches(9), Inches(0.6))
    sp = sub.text_frame.paragraphs[0]
    sp.text = "Office 预览功能验收样例 · 2026-10"
    sp.alignment = PP_ALIGN.CENTER
    sp.font.size = Pt(18)
    sp.font.color.rgb = RGBColor(0x59, 0x59, 0x59)

    # 第 2 页：文字 + 形状
    s2 = prs.slides.add_slide(blank)
    title = s2.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(9), Inches(0.8))
    tp = title.text_frame.paragraphs[0]
    tp.text = "系统核心能力"
    tp.font.size = Pt(30)
    tp.font.bold = True
    tp.font.color.rgb = navy
    body = s2.shapes.add_textbox(Inches(0.6), Inches(1.3), Inches(4.6), Inches(4.5))
    btf = body.text_frame
    btf.word_wrap = True
    lines = [
        "多渠道接入：企业微信、钉钉、飞书",
        "文档理解：Office 三格式在线预览",
        "知识检索：企业知识库语义搜索",
        "任务编排：日常工作流程自动化",
    ]
    for i, line in enumerate(lines):
        para = btf.paragraphs[0] if i == 0 else btf.add_paragraph()
        para.text = line
        para.font.size = Pt(18)
        para.space_after = Pt(14)
    r1 = s2.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(5.6), Inches(1.5), Inches(3.8), Inches(1.6))
    r1.fill.solid()
    r1.fill.fore_color.rgb = RGBColor(0x2E, 0x75, 0xB6)
    r1.line.fill.background()
    r1.text_frame.paragraphs[0].text = "对话入口"
    r1.text_frame.paragraphs[0].font.size = Pt(20)
    r1.text_frame.paragraphs[0].font.color.rgb = white
    oval = s2.shapes.add_shape(MSO_SHAPE.OVAL, Inches(5.6), Inches(3.4), Inches(1.7), Inches(1.7))
    oval.fill.solid()
    oval.fill.fore_color.rgb = RGBColor(0xED, 0x7D, 0x31)
    oval.line.fill.background()
    rect2 = s2.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(7.6), Inches(3.4), Inches(1.8), Inches(1.7))
    rect2.fill.solid()
    rect2.fill.fore_color.rgb = RGBColor(0x70, 0xAD, 0x47)
    rect2.line.fill.background()

    # 第 3 页：彩色主题页
    s3 = prs.slides.add_slide(blank)
    bg = s3.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(10), Inches(7.5))
    bg.fill.solid()
    bg.fill.fore_color.rgb = RGBColor(0x2B, 0x1A, 0x4D)
    bg.line.fill.background()
    t3 = s3.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(9), Inches(0.9))
    p3 = t3.text_frame.paragraphs[0]
    p3.text = "季度主题：深空紫"
    p3.alignment = PP_ALIGN.CENTER
    p3.font.size = Pt(32)
    p3.font.bold = True
    p3.font.color.rgb = RGBColor(0xFF, 0xD9, 0x66)
    palette = [RGBColor(0xFF, 0x6B, 0x6B), RGBColor(0xFF, 0xD9, 0x3B), RGBColor(0x6B, 0xC7, 0x77),
               RGBColor(0x4D, 0x96, 0xFF), RGBColor(0xB9, 0x80, 0xFF)]
    for i, color in enumerate(palette):
        chip = s3.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                   Inches(0.8 + i * 1.8), Inches(2.2), Inches(1.5), Inches(1.5))
        chip.fill.solid()
        chip.fill.fore_color.rgb = color
        chip.line.color.rgb = white
        chip.line.width = Pt(1.5)
    note = s3.shapes.add_textbox(Inches(0.5), Inches(4.6), Inches(9), Inches(2.2))
    np_ = note.text_frame.paragraphs[0]
    np_.text = "本页用于验证彩色主题与高对比配色的渲染效果：渐变色块、描边与字体颜色在预览组件中应保持一致。"
    np_.alignment = PP_ALIGN.CENTER
    np_.font.size = Pt(16)
    np_.font.color.rgb = RGBColor(0xE8, 0xE8, 0xE8)
    note.text_frame.word_wrap = True

    # 第 4 页：多元素混排（标题 + 文本 + 表格 + 形状）
    s4 = prs.slides.add_slide(blank)
    t4 = s4.shapes.add_textbox(Inches(0.4), Inches(0.3), Inches(9.2), Inches(0.7))
    p4 = t4.text_frame.paragraphs[0]
    p4.text = "预览验收检查清单"
    p4.font.size = Pt(28)
    p4.font.bold = True
    p4.font.color.rgb = navy
    tbl = s4.shapes.add_table(4, 3, Inches(0.6), Inches(1.3), Inches(8.8), Inches(2.4)).table
    headers = ["检查项", "操作", "预期结果"]
    data = [
        ["Sheet 切换", "点击 multisheet.xlsx 页签", "表格内容随页签切换"],
        ["行数截断", "打开 bigrows.xlsx", "显示 1000 行截断提示"],
        ["大小门槛", "打开 oversize.xlsx", "提示文件超过 10MB 限制"],
    ]
    for c, h in enumerate(headers):
        cell = tbl.cell(0, c)
        cell.text = h
        cell.text_frame.paragraphs[0].font.size = Pt(14)
        cell.text_frame.paragraphs[0].font.bold = True
    for r, row in enumerate(data, start=1):
        for c, val in enumerate(row):
            cell = tbl.cell(r, c)
            cell.text = val
            cell.text_frame.paragraphs[0].font.size = Pt(13)
    arrow = s4.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(0.8), Inches(4.3), Inches(3.2), Inches(0.9))
    arrow.fill.solid()
    arrow.fill.fore_color.rgb = RGBColor(0xED, 0x7D, 0x31)
    arrow.line.fill.background()
    star = s4.shapes.add_shape(MSO_SHAPE.STAR_5_POINT, Inches(4.6), Inches(4.0), Inches(1.4), Inches(1.4))
    star.fill.solid()
    star.fill.fore_color.rgb = RGBColor(0xFF, 0xC0, 0x00)
    star.line.fill.background()
    foot = s4.shapes.add_textbox(Inches(0.6), Inches(5.8), Inches(8.8), Inches(1.2))
    fp = foot.text_frame.paragraphs[0]
    fp.text = "混排元素：表格、箭头、星形、文本框同页呈现，验证组合渲染与翻页导航。"
    fp.font.size = Pt(15)
    fp.font.color.rgb = RGBColor(0x40, 0x40, 0x40)
    foot.text_frame.word_wrap = True

    prs.save(path)


def gen_docx(path):
    """多级标题 + 段落 + 表格的中文 DOCX。"""
    doc = Document()
    h = doc.add_heading("企业内容系统 Office 预览接口说明", level=0)
    h.alignment = 1
    meta = doc.add_paragraph("文档编号：AID-PREVIEW-001　版本：v1.2　更新日期：2026-10-10")
    meta.runs[0].font.size = DocPt(9)
    meta.runs[0].italic = True

    doc.add_heading("一、功能概述", level=1)
    doc.add_paragraph(
        "Office 预览组件面向企业内容系统中的附件在线查看场景，支持 Excel（.xlsx）、"
        "PowerPoint（.pptx）与 Word（.docx）三种格式。组件在浏览器端完成解析与渲染，"
        "原始文件不落盘到用户本地，保障企业数据的安全边界。"
    )
    doc.add_paragraph(
        "Excel 预览支持多工作表切换、合并单元格展示；超过 1000 行的表格仅加载前 1000 行并显示截断提示；"
        "压缩后超过 10MB 的文件命中大小门槛，直接提示不支持预览。"
    )

    doc.add_heading("二、验收要点", level=1)
    doc.add_heading("2.1 基本预览", level=2)
    doc.add_paragraph("打开 multisheet.xlsx，确认三个工作表页签可切换，表头样式与合并单元格正确呈现。")
    doc.add_heading("2.2 截断与门槛", level=2)
    doc.add_paragraph("bigrows.xlsx 共 1250 行数据，预览应显示前 1000 行并出现截断提示；oversize.xlsx 超过 10MB，应被大小门槛拦截。")
    doc.add_heading("2.3 幻灯片与文档", level=3)
    doc.add_paragraph("sample.pptx 共 4 页，验证翻页导航与彩色主题渲染；本文件验证多级标题、段落与表格的排版回归。")

    doc.add_heading("三、测试用例清单", level=1)
    table = doc.add_table(rows=4, cols=3)
    table.style = "Table Grid"
    for c, val in enumerate(["用例编号", "场景", "预期结果"]):
        table.rows[0].cells[c].text = val
    cases = [
        ["TC-001", "切换工作表", "内容与合并单元格同步更新"],
        ["TC-002", "加载 1250 行表格", "第 1000 行处显示截断提示"],
        ["TC-003", "上传超限文件", "提示超过大小门槛，不渲染"],
    ]
    for r, row in enumerate(cases, start=1):
        for c, val in enumerate(row):
            table.rows[r].cells[c].text = val

    doc.add_heading("四、备注", level=1)
    doc.add_paragraph("本文档由生成脚本自动创建，内容为验收示例，不对应真实接口契约。如需扩展格式支持，请先在设计文档中登记。")
    doc.save(path)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    jobs = [
        ("multisheet.xlsx", gen_multisheet),
        ("bigrows.xlsx", gen_bigrows),
        ("oversize.xlsx", gen_oversize),
        ("sample.pptx", gen_pptx),
        ("sample.docx", gen_docx),
    ]
    for name, fn in jobs:
        path = os.path.join(OUT_DIR, name)
        fn(path)
        print(f"[ok] {name}: {os.path.getsize(path)} 字节")
    print(f"输出目录: {OUT_DIR}")


if __name__ == "__main__":
    main()
