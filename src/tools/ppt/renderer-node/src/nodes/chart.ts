import pptxgen from "pptxgenjs";
import { ChartNode } from "../spec";

const chartTypes: Record<ChartNode["chart_type"], pptxgen.CHART_NAME> = {
  bar: "bar",
  column: "bar",
  line: "line",
  pie: "pie",
  doughnut: "doughnut",
};

export function renderChart(slide: pptxgen.Slide, node: ChartNode): void {
  slide.addChart(chartTypes[node.chart_type], node.series.map((series) => ({
    name: series.name,
    labels: node.labels,
    values: series.values,
  })), {
    x: node.x, y: node.y, w: node.w, h: node.h,
    catAxisLabelFontFace: "Microsoft YaHei",
    valAxisLabelFontFace: "Microsoft YaHei",
    showLegend: node.show_legend,
    showTitle: node.show_title,
    title: node.title ?? undefined,
    showValue: node.chart_type === "pie" || node.chart_type === "doughnut",
    showPercent: node.chart_type === "pie" || node.chart_type === "doughnut",
    barDir: node.chart_type === "bar" ? "bar" : "col",
  });
}
