import type pptxgen from "pptxgenjs";
import { TableNode } from "../spec";
import { normalizeColor } from "../units";

export function renderTable(slide: pptxgen.Slide, node: TableNode): void {
  const rows = node.rows.map((row, rowIndex) => row.map((text) => ({
    text,
    options: rowIndex === 0 ? {
      bold: true,
      color: normalizeColor(node.header_color),
      fill: { color: normalizeColor(node.header_fill) },
    } : undefined,
  })));
  slide.addTable(rows, {
    x: node.x, y: node.y, w: node.w, h: node.h,
    fontSize: node.font_size, color: normalizeColor(node.color),
    border: { type: "solid", color: normalizeColor(node.border_color), pt: 1 },
    fill: { color: "FFFFFF" }, margin: 0.06,
    rowH: node.h / node.rows.length,
    bold: false,
  });
}
