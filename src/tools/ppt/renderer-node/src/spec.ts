export type Align = "left" | "center" | "right";
export type VAlign = "top" | "mid" | "bottom";

export interface BaseNode {
  type: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface TextNode extends BaseNode {
  type: "text";
  text: string;
  font_size: number;
  font_face: string;
  color: string;
  bold: boolean;
  align: Align;
  valign: VAlign;
  margin: number;
  bullet: boolean;
}

export interface ShapeNode extends BaseNode {
  type: "shape";
  shape: "rect" | "roundRect" | "line" | "ellipse";
  fill: string;
  line_color: string;
  line_width: number;
}

export interface ImageNode extends BaseNode {
  type: "image" | "raster";
  path: string;
  transparency: number;
}

export interface TableNode extends BaseNode {
  type: "table";
  rows: string[][];
  font_size: number;
  color: string;
  header_fill: string;
  header_color: string;
  border_color: string;
}

export interface ChartSeries {
  name: string;
  values: number[];
}

export interface ChartNode extends BaseNode {
  type: "chart";
  chart_type: "bar" | "column" | "line" | "pie" | "doughnut";
  labels: string[];
  series: ChartSeries[];
  show_legend: boolean;
  show_title: boolean;
  title?: string | null;
}

export type SlideNode = TextNode | ShapeNode | ImageNode | TableNode | ChartNode;

export interface SlideSpec {
  id: string;
  layout?: string | null;
  background: string;
  nodes: SlideNode[];
  notes?: string | null;
}

export interface SlideDeckSpec {
  version: "1.0";
  title: string;
  width: number;
  height: number;
  author: string;
  subject?: string | null;
  warnings: string[];
  slides: SlideSpec[];
}

export function assertSlideDeckSpec(value: unknown): asserts value is SlideDeckSpec {
  if (!value || typeof value !== "object") throw new Error("invalid spec");
  const spec = value as Partial<SlideDeckSpec>;
  exactKeys(spec, ["version", "title", "width", "height", "author", "subject", "warnings", "slides"]);
  if (spec.version !== "1.0" || typeof spec.title !== "string" || !spec.title.trim()) {
    throw new Error("invalid deck metadata");
  }
  if (!positive(spec.width) || spec.width > 100 || !positive(spec.height) || spec.height > 100 ||
      typeof spec.author !== "string" || !Array.isArray(spec.warnings) ||
      !spec.warnings.every((warning) => typeof warning === "string") ||
      !Array.isArray(spec.slides) || !spec.slides.length) {
    throw new Error("invalid deck dimensions or slides");
  }
  for (const slide of spec.slides) {
    exactKeys(slide, ["id", "layout", "background", "nodes", "notes"]);
    if (!slide || typeof slide.id !== "string" || !slide.id.trim() ||
        typeof slide.background !== "string" || !color(slide.background) ||
        !Array.isArray(slide.nodes)) throw new Error("invalid slide");
    for (const node of slide.nodes) {
      if (!node || !["text", "shape", "image", "raster", "table", "chart"].includes(node.type)) {
        throw new Error("invalid node type");
      }
      if (![node.x, node.y].every(nonNegative) || !positive(node.w) || !positive(node.h)) {
        throw new Error("invalid node bounds");
      }
      if (node.x + node.w > spec.width! + 1e-6 || node.y + node.h > spec.height! + 1e-6) {
        throw new Error("node outside slide");
      }
      assertNode(node);
    }
  }
}

function assertNode(node: SlideNode): void {
  const bounds = ["type", "x", "y", "w", "h"];
  switch (node.type) {
    case "text":
      exactKeys(node, [...bounds, "text", "font_size", "font_face", "color", "bold",
        "align", "valign", "margin", "bullet"]);
      if (typeof node.text !== "string" || !node.text.trim() || !positive(node.font_size) ||
          node.font_size > 200 || typeof node.font_face !== "string" || !node.font_face ||
          !color(node.color) || typeof node.bold !== "boolean" ||
          !["left", "center", "right"].includes(node.align) ||
          !["top", "mid", "bottom"].includes(node.valign) ||
          !nonNegative(node.margin) || typeof node.bullet !== "boolean") throw new Error("invalid text node");
      return;
    case "shape":
      exactKeys(node, [...bounds, "shape", "fill", "line_color", "line_width"]);
      if (!["rect", "roundRect", "line", "ellipse"].includes(node.shape) ||
          !color(node.fill) || !color(node.line_color) || !nonNegative(node.line_width)) {
        throw new Error("invalid shape node");
      }
      return;
    case "image":
    case "raster":
      exactKeys(node, [...bounds, "path", "transparency"]);
      if (typeof node.path !== "string" || !node.path.trim() ||
          !nonNegative(node.transparency) || node.transparency > 100) throw new Error("invalid image node");
      return;
    case "table": {
      exactKeys(node, [...bounds, "rows", "font_size", "color", "header_fill",
        "header_color", "border_color"]);
      if (!Array.isArray(node.rows) || !node.rows.length || !Array.isArray(node.rows[0]) ||
          !node.rows[0].length || !node.rows.every((row) =>
            Array.isArray(row) && row.length === node.rows[0].length &&
            row.every((cell) => typeof cell === "string")) ||
          !positive(node.font_size) || node.font_size > 100 ||
          ![node.color, node.header_fill, node.header_color, node.border_color].every(color)) {
        throw new Error("invalid table node");
      }
      return;
    }
    case "chart":
      exactKeys(node, [...bounds, "chart_type", "labels", "series", "show_legend",
        "show_title", "title"]);
      if (!["bar", "column", "line", "pie", "doughnut"].includes(node.chart_type) ||
          !Array.isArray(node.labels) || !node.labels.length ||
          !node.labels.every((label) => typeof label === "string") ||
          !Array.isArray(node.series) || !node.series.length ||
          !node.series.every((series) => typeof series?.name === "string" &&
            Array.isArray(series.values) && series.values.length === node.labels.length &&
            series.values.every((item) => typeof item === "number" && Number.isFinite(item))) ||
          typeof node.show_legend !== "boolean" || typeof node.show_title !== "boolean") {
        throw new Error("invalid chart node");
      }
  }
}

const positive = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value > 0;
const nonNegative = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value >= 0;
const color = (value: unknown): value is string =>
  typeof value === "string" && /^#?[0-9A-F]{6}$/i.test(value);
const exactKeys = (value: object, allowed: string[]): void => {
  if (Object.keys(value).some((key) => !allowed.includes(key))) throw new Error("unexpected field");
};
