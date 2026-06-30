import fs from "node:fs/promises";
import path from "node:path";
import pptxgen from "pptxgenjs";
import { renderNode } from "./nodes";
import { buildLayoutReport } from "./qa";
import { assertSlideDeckSpec, SlideDeckSpec } from "./spec";
import { normalizeColor } from "./units";

export async function renderDeck(spec: SlideDeckSpec, outputPath: string, qaPath: string): Promise<void> {
  assertSlideDeckSpec(spec);
  const pptx = new pptxgen();
  pptx.defineLayout({ name: "CUSTOM", width: spec.width, height: spec.height });
  pptx.layout = "CUSTOM";
  pptx.author = spec.author;
  pptx.subject = spec.subject ?? "";
  pptx.title = spec.title;
  pptx.company = "AID Work Agent";

  for (const item of spec.slides) {
    const slide = pptx.addSlide();
    slide.background = { color: normalizeColor(item.background) };
    for (const node of item.nodes) renderNode(slide, node);
    if (item.notes) slide.addNotes(item.notes);
  }
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await fs.mkdir(path.dirname(qaPath), { recursive: true });
  await pptx.writeFile({ fileName: outputPath });
  await fs.writeFile(qaPath, JSON.stringify(buildLayoutReport(spec), null, 2), "utf8");
}

function parseArgs(args: string[]): { spec: string; out: string; qa: string } {
  const value = (name: string): string => {
    const index = args.indexOf(name);
    if (index < 0 || !args[index + 1]) throw new Error(`missing ${name}`);
    return args[index + 1];
  };
  return { spec: value("--spec"), out: value("--out"), qa: value("--qa-json") };
}

async function main(): Promise<void> {
  const args = parseArgs(process.argv.slice(2));
  const parsed: unknown = JSON.parse(await fs.readFile(args.spec, "utf8"));
  assertSlideDeckSpec(parsed);
  for (const slide of parsed.slides) {
    for (const node of slide.nodes) {
      if ((node.type === "image" || node.type === "raster") && !path.isAbsolute(node.path)) {
        node.path = path.resolve(path.dirname(args.spec), node.path);
      }
    }
  }
  await renderDeck(parsed, args.out, args.qa);
  process.stdout.write(JSON.stringify({ success: true }) + "\n");
}

if (require.main === module) {
  main().catch(() => {
    process.stderr.write("PPT renderer failed\n");
    process.exitCode = 1;
  });
}
