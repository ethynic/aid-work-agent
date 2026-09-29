import type pptxgen from "pptxgenjs";
import { TableNode } from "../spec";
import { normalizeColor } from "../units";

export function renderTable(slide: pptxgen.Slide, node: TableNode): void {
  const zebra = node.zebra_color ? normalizeColor(node.zebra_color) : null;
  const rows = node.rows.map((row, rowIndex) => row.map((text) => {
    // 数据行隔行上浅色（斑马纹）：数据行第 2、4、6…（rowIndex 为偶数且非表头）
    const zebraRow = zebra && rowIndex > 0 && rowIndex % 2 === 0;
    const options = rowIndex === 0 ? {
      bold: true,
      color: normalizeColor(node.header_color),
      fill: { color: normalizeColor(node.header_fill) },
    } : zebraRow ? { fill: { color: zebra } } : undefined;
    return { text, options };
  }));
  // 行高封顶 0.62in：表格按内容行数收缩高度，避免占满预留框导致行高虚高空洞
  const rowH = Math.min(node.h / node.rows.length, 0.62);
  slide.addTable(rows, {
    x: node.x, y: node.y, w: node.w, h: rowH * node.rows.length,
    fontSize: node.font_size, color: normalizeColor(node.color),
    border: { type: "solid", color: normalizeColor(node.border_color), pt: 1 },
    fill: { color: "FFFFFF" }, margin: 0.06,
    rowH,
    valign: "middle",
    bold: false,
  });
}
