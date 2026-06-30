import type pptxgen from "pptxgenjs";
import { ImageNode } from "../spec";

export function renderImage(slide: pptxgen.Slide, node: ImageNode): void {
  slide.addImage({
    path: node.path, x: node.x, y: node.y, w: node.w, h: node.h,
    transparency: node.transparency,
  });
}
