/* 看板自检：用轻量 DOM stub 在 Node 里跑一遍渲染逻辑，捕获语法/运行时错误。
   用法：node scripts/selftest.js [dashboard/index.html 路径] */
const fs = require("fs");
const path = require("path");

const file = process.argv[2] || path.join(__dirname, "..", "dashboard", "index.html");
const html = fs.readFileSync(file, "utf8");

const dataMatch = html.match(/<script id="ppt-data" type="application\/json">([\s\S]*?)<\/script>/);
const jsMatch = html.match(/<script>\s*([\s\S]*?)<\/script>\s*<\/body>/);
if (!dataMatch || !jsMatch) {
  console.error("[selftest] 未找到内嵌数据或主脚本，看板可能未正确构建");
  process.exit(1);
}

const store = {};
function stub() {
  return {
    innerHTML: "", textContent: "", value: "", files: [], style: {}, dataset: {},
    classList: { add() {}, remove() {}, contains() { return false; } },
    querySelectorAll() { return []; }, querySelector() { return stub(); },
    appendChild() {}, click() {}, remove() {}, setAttribute() {},
    addEventListener() {}, focus() {}, closest() { return null; },
  };
}
const nodes = {};
function node(sel) { return (nodes[sel] = nodes[sel] || stub()); }

global.document = {
  getElementById(id) { return id === "ppt-data" ? { textContent: dataMatch[1] } : node("#" + id); },
  querySelector(sel) { return node(sel); },
  querySelectorAll() { return []; },
  addEventListener() {},
  documentElement: stub(),
  body: { appendChild() {} },
  createElement() { return stub(); },
  title: "",
};
global.localStorage = {
  getItem(k) { return store[k] || null; },
  setItem(k, v) { store[k] = v; },
};
global.Blob = function () {};
global.URL = { createObjectURL() { return "blob:stub"; }, revokeObjectURL() {} };
global.FileReader = function () {};
global.location = { protocol: "file:" };
global.fetch = function () { return Promise.reject(new Error("offline stub")); };

let ok = true;
const probe = `
; globalThis.__probe = {};
openDetail(DATA.projects[0].id); __probe.drawer = document.querySelector("#drawer").innerHTML;
openEntry(DATA.projects[0].id); __probe.entry = document.querySelector("#modal").innerHTML;
openHelp(); __probe.help = document.querySelector("#modal").innerHTML;
arr(DATA.projects).forEach(p => openDetail(p.id));
visibleProjects(); renderGrid(); exportData.toString(); importData.toString();
`;
try {
  new Function(jsMatch[1] + probe)();
  console.log("[selftest] 主脚本执行通过（含详情抽屉 / 录入弹窗 / 帮助 / 全部项目详情）");
} catch (e) {
  ok = false;
  console.error("[selftest] 脚本异常：" + e.stack);
}
const pr = globalThis.__probe || {};
for (const [name, rx] of [
  ["详情抽屉渲染", /AI 研判结论/],
  ["录入弹窗渲染", /录入项目汇报/],
  ["帮助弹窗渲染", /使用说明/],
]) {
  const src = name === "详情抽屉渲染" ? pr.drawer : name === "录入弹窗渲染" ? pr.entry : pr.help;
  const hit = rx.test(src || "");
  console.log((hit ? "[selftest] ✓ " : "[selftest] ✗ ") + name);
  if (!hit) ok = false;
}

const checks = [
  ["KPI 卡片", "#kpis", /class="kpi"/],
  ["AI 总体总结", "#summary", /AI 总体研判/],
  ["状态分布环图", "#donut", /donut-center/],
  ["进度对比条", "#progressBars", /pb-row/],
  ["筛选工具条", "#toolbar", /data-filter="overdue"/],
  ["项目卡片", "#grid", /class="card"/],
];
for (const [name, sel, rx] of checks) {
  const hit = rx.test(node(sel).innerHTML);
  console.log((hit ? "[selftest] ✓ " : "[selftest] ✗ ") + name);
  if (!hit) ok = false;
}
const cards = (node("#grid").innerHTML.match(/class="card"/g) || []).length;
console.log("[selftest] 渲染项目卡片数：" + cards);
const data = JSON.parse(dataMatch[1]);
if (cards !== data.projects.length) { console.error("[selftest] 卡片数与项目数不一致"); ok = false; }

process.exit(ok ? 0 : 1);
