#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
竞品研究报告 HTML 合并工具

将多个独立的 HTML 子页面合并为一份带导航的完整报告。
这是竞品研究子智能体的专用工具，不属于通用 file_write 工具。

用法（通过 skill_execute 调用）：
  skill_execute(
    skill="competitor-research",
    command="python scripts/html_report_merger.py",
    content='{"file_paths": ["p1.html", "p2.html"], "output_path": "report.html", "title": "竞品报告"}'
  )
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional


def extract_html_body(html_content: str) -> str:
    """从 HTML 内容中提取 <body> 标签之间的内容"""
    body_start = html_content.lower().find("<body")
    if body_start == -1:
        return html_content
    tag_close = html_content.find(">", body_start)
    if tag_close == -1:
        return html_content
    body_end = html_content.lower().rfind("</body>")
    if body_end == -1:
        return html_content[tag_close + 1:]
    return html_content[tag_close + 1:body_end]


def extract_html_head_styles(html_content: str) -> List[str]:
    """从 HTML 的 <head> 中提取所有 <style> 标签的内容"""
    styles = []
    head_start = html_content.lower().find("<head")
    if head_start == -1:
        return styles
    head_tag_close = html_content.find(">", head_start)
    if head_tag_close == -1:
        return styles
    head_end = html_content.lower().find("</head>", head_tag_close)
    if head_end == -1:
        return styles
    head_content = html_content[head_tag_close + 1:head_end]
    pattern = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)
    for match in pattern.finditer(head_content):
        styles.append(match.group(1).strip())
    return styles


def extract_page_title(html_content: str, fallback: str) -> str:
    """从 HTML 中提取第一个 <h1> 标签的内容作为页面标题"""
    match = re.search(r"<h1[^>]*>(.*?)</h1>", html_content, re.DOTALL | re.IGNORECASE)
    if match:
        title = re.sub(r"<[^>]+>", "", match.group(1)).strip()
        if title:
            return title
    return fallback


def merge_html_files(file_paths: List[str], output_title: str = "合并报告") -> str:
    """将多个 HTML 文件合并为带导航的单一 HTML 报告"""
    all_styles: List[str] = []
    page_sections: List[Dict[str, str]] = []

    for idx, fp in enumerate(file_paths):
        file_path_obj = Path(fp)
        if not file_path_obj.exists():
            print(f"[WARN] 跳过不存在的文件: {fp}", file=sys.stderr)
            continue

        try:
            content = file_path_obj.read_text(encoding="utf-8")
        except Exception as e:
            print(f"[WARN] 跳过无法读取的文件 {fp}: {e}", file=sys.stderr)
            continue

        if not content.strip():
            print(f"[WARN] 跳过空文件: {fp}", file=sys.stderr)
            continue

        for style in extract_html_head_styles(content):
            if style not in all_styles:
                all_styles.append(style)

        body_content = extract_html_body(content).strip()
        if not body_content:
            body_content = f"<p>（空页面: {file_path_obj.name}）</p>"

        page_title = extract_page_title(content, file_path_obj.stem)

        page_sections.append({
            "title": page_title,
            "body": body_content,
        })

    if not page_sections:
        raise ValueError("没有有效的文件可合并")

    merged_styles = "\n".join(all_styles)

    toc_items = []
    for idx, page in enumerate(page_sections):
        toc_items.append(
            f'      <div class="toc-item" data-page="{idx}"'
            f' onclick="goToPage({idx})">'
            f"{idx + 1}. {page['title']}</div>"
        )
    toc_html = "\n".join(toc_items)

    section_divs = []
    for idx, page in enumerate(page_sections):
        active_class = " active" if idx == 0 else ""
        section_divs.append(
            f'    <div class="page-section{active_class}" id="page-{idx}">'
            f"\n{page['body']}\n"
            f"    </div>"
        )
    sections_html = "\n\n".join(section_divs)

    total_pages = len(page_sections)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{output_title}</title>
  <style>
    /* === Base Reset & Layout === */
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Helvetica Neue", sans-serif; background: #f5f5f5; }}

    /* === Top Navigation Bar === */
    .report-nav {{
      position: fixed; top: 0; left: 0; right: 0; height: 48px;
      background: #1a56db; color: #fff; display: flex; align-items: center;
      padding: 0 20px; z-index: 1000; box-shadow: 0 2px 8px rgba(0,0,0,0.15);
    }}
    .report-nav .nav-btn {{
      background: rgba(255,255,255,0.15); border: none; color: #fff;
      padding: 6px 14px; border-radius: 4px; cursor: pointer; font-size: 14px;
      transition: background 0.2s;
    }}
    .report-nav .nav-btn:hover {{ background: rgba(255,255,255,0.3); }}
    .report-nav .nav-btn:disabled {{ opacity: 0.4; cursor: not-allowed; }}
    .report-nav .nav-title {{
      flex: 1; text-align: center; font-size: 16px; font-weight: 600;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      padding: 0 16px;
    }}
    .report-nav .page-info {{
      font-size: 13px; opacity: 0.8; margin-left: 12px; white-space: nowrap;
    }}

    /* === Table of Contents Sidebar === */
    .toc-panel {{
      position: fixed; top: 48px; left: 0; bottom: 0; width: 220px;
      background: #fff; border-right: 1px solid #e5e7eb; overflow-y: auto;
      padding: 16px 0; z-index: 999;
    }}
    .toc-panel .toc-header {{
      padding: 0 16px 12px; font-size: 13px; color: #6b7280;
      font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;
      border-bottom: 1px solid #e5e7eb; margin-bottom: 8px;
    }}
    .toc-item {{
      padding: 10px 20px; font-size: 14px; color: #374151;
      cursor: pointer; transition: all 0.15s; border-left: 3px solid transparent;
      line-height: 1.4;
    }}
    .toc-item:hover {{ background: #f0f4ff; color: #1a56db; }}
    .toc-item.active {{
      background: #eff6ff; color: #1a56db; font-weight: 600;
      border-left-color: #1a56db;
    }}

    /* === Main Content Area === */
    .main-content {{
      margin-top: 48px; margin-left: 220px; min-height: calc(100vh - 48px);
      background: #fff;
    }}
    .page-section {{
      display: none; padding: 40px 48px; max-width: 960px; margin: 0 auto;
      line-height: 1.8; color: #1f2937;
    }}
    .page-section.active {{ display: block; }}

    /* === Merged styles from sub-pages === */
{merged_styles}
  </style>
</head>
<body>
  <div class="report-nav">
    <button class="nav-btn" id="prevBtn" onclick="prevPage()">&#9664; 上一页</button>
    <span class="nav-title">{output_title}</span>
    <button class="nav-btn" id="nextBtn" onclick="nextPage()">下一页 &#9654;</button>
    <span class="page-info" id="pageInfo">1 / {total_pages}</span>
  </div>

  <div class="toc-panel">
    <div class="toc-header">目录</div>
{toc_html}
  </div>

  <div class="main-content">
{sections_html}
  </div>

  <script>
    var totalPages = {total_pages};
    var currentPage = 0;

    function goToPage(idx) {{
      if (idx < 0 || idx >= totalPages) return;
      var sections = document.querySelectorAll('.page-section');
      sections.forEach(function(s) {{ s.classList.remove('active'); }});
      var target = document.getElementById('page-' + idx);
      if (target) target.classList.add('active');
      var items = document.querySelectorAll('.toc-item');
      items.forEach(function(item, i) {{
        if (i === idx) item.classList.add('active');
        else item.classList.remove('active');
      }});
      currentPage = idx;
      document.getElementById('pageInfo').textContent = (idx + 1) + ' / ' + totalPages;
      document.getElementById('prevBtn').disabled = (idx === 0);
      document.getElementById('nextBtn').disabled = (idx === totalPages - 1);
      window.scrollTo(0, 0);
    }}

    function prevPage() {{ goToPage(currentPage - 1); }}
    function nextPage() {{ goToPage(currentPage + 1); }}

    document.addEventListener('keydown', function(e) {{
      if (e.key === 'ArrowLeft') prevPage();
      else if (e.key === 'ArrowRight') nextPage();
    }});

    goToPage(0);
  </script>
</body>
</html>"""


def main():
    """CLI 入口：接收 JSON 参数，合并 HTML 文件"""
    # 确保 stdout 使用 UTF-8 编码（Windows 子进程默认可能用 GBK）
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.stderr.encoding != "utf-8":
        sys.stderr.reconfigure(encoding="utf-8")

    if len(sys.argv) < 2:
        print(json.dumps({"success": False, "error": "缺少参数，请通过 content 传入 JSON"}))
        sys.exit(1)

    try:
        params = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        print(json.dumps({"success": False, "error": f"JSON 解析失败: {e}"}))
        sys.exit(1)

    file_paths = params.get("file_paths", [])
    output_path = params.get("output_path")
    title = params.get("title", "竞品分析报告")

    if not file_paths or len(file_paths) < 2:
        print(json.dumps({"success": False, "error": "至少需要 2 个文件路径"}))
        sys.exit(1)

    if not output_path:
        print(json.dumps({"success": False, "error": "必须指定 output_path"}))
        sys.exit(1)

    try:
        merged_html = merge_html_files(file_paths, output_title=title)

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(merged_html, encoding="utf-8")

        result = {
            "success": True,
            "file_path": str(out.resolve()),
            "file_name": out.name,
            "file_size": out.stat().st_size,
            "merged_count": len(file_paths),
            "message": f"已合并 {len(file_paths)} 个文件为 {out.name}",
        }
        print(json.dumps(result, ensure_ascii=False))

    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
