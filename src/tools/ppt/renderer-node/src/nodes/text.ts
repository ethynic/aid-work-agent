import type pptxgen from "pptxgenjs";
import { TextNode } from "../spec";
import { normalizeColor } from "../units";

export function renderText(slide: pptxgen.Slide, node: TextNode): void {
  slide.addText(node.text, {
    x: node.x, y: node.y, w: node.w, h: node.h,
    fontFace: node.font_face, fontSize: node.font_size,
    color: normalizeColor(node.color), bold: node.bold,
    align: node.align, valign: node.valign === "mid" ? "middle" : node.valign, margin: node.margin,
    bullet: node.bullet ? { type: "bullet" } : undefined,
    breakLine: false, fit: "shrink",
  });
}
