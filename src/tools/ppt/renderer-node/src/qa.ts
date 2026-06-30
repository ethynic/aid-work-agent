import { SlideDeckSpec } from "./spec";

export interface LayoutReport {
  version: "1.0";
  slide_count: number;
  node_count: number;
  slides: Array<{ id: string; node_count: number; node_types: Record<string, number> }>;
}

export function buildLayoutReport(spec: SlideDeckSpec): LayoutReport {
  const slides = spec.slides.map((slide) => {
    const nodeTypes: Record<string, number> = {};
    for (const node of slide.nodes) nodeTypes[node.type] = (nodeTypes[node.type] ?? 0) + 1;
    return { id: slide.id, node_count: slide.nodes.length, node_types: nodeTypes };
  });
  return {
    version: "1.0",
    slide_count: slides.length,
    node_count: slides.reduce((total, slide) => total + slide.node_count, 0),
    slides,
  };
}
