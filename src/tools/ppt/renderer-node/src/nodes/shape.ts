import type pptxgen from "pptxgenjs";
import { ShapeNode } from "../spec";
import { normalizeColor } from "../units";

const shapeTypes: Record<ShapeNode["shape"], string> = {
  rect: "rect",
  roundRect: "roundRect",
  line: "line",
  ellipse: "ellipse",
};

export function renderShape(slide: pptxgen.Slide, node: ShapeNode): void {
  slide.addShape(shapeTypes[node.shape] as unknown as pptxgen.ShapeType, {
    x: node.x, y: node.y, w: node.w, h: node.h,
    fill: { color: normalizeColor(node.fill) },
    line: { color: normalizeColor(node.line_color), width: node.line_width },
  });
}
