import type pptxgen from "pptxgenjs";
import { SlideNode } from "../spec";
import { renderChart } from "./chart";
import { renderImage } from "./image";
import { renderShape } from "./shape";
import { renderTable } from "./table";
import { renderText } from "./text";

export function renderNode(slide: pptxgen.Slide, node: SlideNode): void {
  switch (node.type) {
    case "text": return renderText(slide, node);
    case "shape": return renderShape(slide, node);
    case "image":
    case "raster": return renderImage(slide, node);
    case "table": return renderTable(slide, node);
    case "chart": return renderChart(slide, node);
  }
}
