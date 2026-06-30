import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { buildLayoutReport } from "../src/qa";
import { assertSlideDeckSpec, SlideDeckSpec } from "../src/spec";
import { renderDeck } from "../src/render";

const spec: SlideDeckSpec = {
  version: "1.0", title: "Node renderer test", width: 13.333, height: 7.5,
  author: "test", warnings: [],
  slides: [{
    id: "slide-1", background: "FFFFFF", nodes: [
      { type: "text", x: 1, y: 1, w: 4, h: 1, text: "Hello", font_size: 24,
        font_face: "Arial", color: "222222", bold: true, align: "left", valign: "top",
        margin: 0, bullet: false },
      { type: "shape", x: 1, y: 2.2, w: 2, h: 1, shape: "rect", fill: "EAF1F8",
        line_color: "2F75B5", line_width: 1 },
      { type: "table", x: 4, y: 2.2, w: 4, h: 2, rows: [["A", "B"], ["1", "2"]],
        font_size: 14, color: "222222", header_fill: "1F4E78", header_color: "FFFFFF",
        border_color: "D9E2F3" },
      { type: "chart", x: 8.3, y: 2.2, w: 4, h: 3, chart_type: "column",
        labels: ["A", "B"], series: [{ name: "S", values: [1, 2] }],
        show_legend: true, show_title: false },
    ],
  }],
};

test("validates and reports node counts", () => {
  assert.doesNotThrow(() => assertSlideDeckSpec(spec));
  const report = buildLayoutReport(spec);
  assert.equal(report.slide_count, 1);
  assert.equal(report.node_count, 4);
  assert.deepEqual(report.slides[0].node_types, { text: 1, shape: 1, table: 1, chart: 1 });
});

test("rejects nodes outside the slide", () => {
  const invalid = structuredClone(spec);
  invalid.slides[0].nodes[0].x = 13;
  assert.throws(() => assertSlideDeckSpec(invalid), /outside slide/);
});

test("rejects malformed node fields", () => {
  const invalid = structuredClone(spec);
  const textNode = invalid.slides[0].nodes[0];
  assert.equal(textNode.type, "text");
  if (textNode.type === "text") textNode.color = "not-a-color";
  assert.throws(() => assertSlideDeckSpec(invalid), /invalid text node/);
});

test("rejects unexpected schema fields", () => {
  const invalid = structuredClone(spec) as SlideDeckSpec & { secret?: string };
  invalid.secret = "not part of the protocol";
  assert.throws(() => assertSlideDeckSpec(invalid), /unexpected field/);
});

test("renders a non-empty pptx and QA json", async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "aid-ppt-node-"));
  const output = path.join(dir, "deck.pptx");
  const qa = path.join(dir, "qa", "layout.json");
  await renderDeck(spec, output, qa);
  assert.ok((await fs.stat(output)).size > 0);
  assert.equal(JSON.parse(await fs.readFile(qa, "utf8")).slide_count, 1);
  await fs.rm(dir, { recursive: true, force: true });
});
