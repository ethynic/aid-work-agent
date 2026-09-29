import pptxgen from "pptxgenjs";
import { ChartNode } from "../spec";
import { normalizeColor } from "../units";

const chartTypes: Record<ChartNode["chart_type"], pptxgen.CHART_NAME> = {
  bar: "bar",
  column: "bar",
  line: "line",
  pie: "pie",
  doughnut: "doughnut",
};

export function renderChart(slide: pptxgen.Slide, node: ChartNode): void {
  const palette = node.chart_colors.map(normalizeColor);
  const single = node.series.length === 1;
  const axisLabel = { fontFace: "Microsoft YaHei", color: "7A8494", fontSize: 12 };
  slide.addChart(chartTypes[node.chart_type], node.series.map((series) => ({
    name: series.name,
    labels: node.labels,
    values: series.values,
  })), {
    x: node.x, y: node.y, w: node.w, h: node.h,
    chartColors: palette.length ? palette : undefined,
    catAxisLabelFontFace: axisLabel.fontFace, catAxisLabelColor: axisLabel.color,
    valAxisLabelFontFace: axisLabel.fontFace, valAxisLabelColor: axisLabel.color,
    valAxisLabelFontSize: axisLabel.fontSize, catAxisLabelFontSize: axisLabel.fontSize,
    valGridLine: node.chart_type === "pie" || node.chart_type === "doughnut"
      ? { style: "none" }
      : { color: "E6EAF0", size: 0.5 },
    catGridLine: { style: "none" },
    showLegend: node.show_legend && !single,
    showTitle: node.show_title,
    title: node.title ?? undefined,
    showValue: node.chart_type === "pie" || node.chart_type === "doughnut" ||
      (single && (node.chart_type === "bar" || node.chart_type === "column")),
    dataLabelPosition: node.chart_type === "pie" || node.chart_type === "doughnut"
      ? "bestFit"
      : single ? "outEnd" : undefined,
    dataLabelColor: "3D4655",
    dataLabelFontFace: "Microsoft YaHei",
    dataLabelFontSize: 11,
    barDir: node.chart_type === "bar" ? "bar" : "col",
    barGapWidthPct: 60,
  });
}
