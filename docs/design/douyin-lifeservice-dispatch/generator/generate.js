// 抖音来客订单同步与门店微信播报系统 — 私有化部署解决方案（面向客户）
// R4 封面（GO-1 石墨橙）+ 三段式页码 + TOC + 横线商务表格
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, PageNumber, NumberFormat, SectionType, TableLayoutType,
  AlignmentType, HeadingLevel, WidthType, BorderStyle, ShadingType,
  LevelFormat, TableOfContents,
} = require("docx");
const fs = require("fs");

// ── 调色板（GO-1 Graphite Orange：方案/投标/PRD）──
const P = {
  bg: "1A2330", accent: "D4875A",
  cover: { titleColor: "FFFFFF", subtitleColor: "B0B8C0", metaColor: "5A6470", footerColor: "909090" },
  table: { headerBg: "D4875A", headerText: "FFFFFF", accentLine: "D4875A", innerLine: "DDD0C8", surface: "F8F0EB" },
  primary: "1F2933", body: "000000", secondary: "687078",
};

const NB = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
const noBorders = { top: NB, bottom: NB, left: NB, right: NB };
const allNoBorders = { top: NB, bottom: NB, left: NB, right: NB, insideHorizontal: NB, insideVertical: NB };
const emptyPara = () => new Paragraph({ children: [] });

// ── 封面标题布局函数（design-system 标准实现）──
function splitTitleLines(title, charsPerLine) {
  if (title.length <= charsPerLine) return [title];
  const breakAfter = new Set([..."\uff0c\u3002\u3001\uff1b\uff1a\uff01\uff1f", ..."\u7684\u4e0e\u548c\u53ca\u4e4b\u5728\u4e8e\u4e3a", ..."-_\u2014\u2013\u00b7/", ..." \t"]);
  const lines = [];
  let remaining = title;
  while (remaining.length > charsPerLine) {
    let breakAt = -1;
    for (let i = charsPerLine; i >= Math.floor(charsPerLine * 0.6); i--) {
      if (i < remaining.length && breakAfter.has(remaining[i - 1])) { breakAt = i; break; }
    }
    if (breakAt === -1) {
      const limit = Math.min(remaining.length, Math.ceil(charsPerLine * 1.3));
      for (let i = charsPerLine + 1; i < limit; i++) {
        if (breakAfter.has(remaining[i - 1])) { breakAt = i; break; }
      }
    }
    if (breakAt === -1) {
      breakAt = charsPerLine;
      const prevChar = remaining[breakAt - 1], nextChar = remaining[breakAt];
      if (prevChar && nextChar && !breakAfter.has(prevChar) && !breakAfter.has(nextChar) &&
          /[\u4e00-\u9fff]/.test(prevChar) && /[\u4e00-\u9fff]/.test(nextChar)) breakAt -= 1;
    }
    lines.push(remaining.slice(0, breakAt).trim());
    remaining = remaining.slice(breakAt).trim();
  }
  if (remaining) lines.push(remaining);
  if (lines.length > 1 && lines[lines.length - 1].length <= 2) {
    const last = lines.pop();
    lines[lines.length - 1] += last;
  }
  return lines;
}
function calcTitleLayout(title, maxWidthTwips, preferredPt = 40, minPt = 24) {
  const charsPerLine = (pt) => Math.floor(maxWidthTwips / (pt * 20));
  let titlePt = preferredPt, lines;
  while (titlePt >= minPt) {
    const cpl = charsPerLine(titlePt);
    if (cpl < 2) { titlePt -= 2; continue; }
    lines = splitTitleLines(title, cpl);
    if (lines.length <= 3) break;
    titlePt -= 2;
  }
  if (!lines || lines.length > 3) { lines = splitTitleLines(title, charsPerLine(minPt)); titlePt = minPt; }
  return { titlePt, titleLines: lines };
}

// ── R4 封面（Top Color Block）──
function buildCoverR4(config) {
  const C = config.palette;
  const padL = 1200, padR = 800;
  const availableWidth = 11906 - padL - padR;
  const { titlePt, titleLines } = calcTitleLayout(config.title, availableWidth, 40, 26);
  const titleSize = titlePt * 2;

  const titleBlockHeight = titleLines.length * (titlePt * 23 + 200);
  const englishLabelH = config.englishLabel ? (9 * 23 + 500) : 0;
  const subtitleH = config.subtitle ? (12 * 23 + 200) : 0;
  const upperContentH = englishLabelH + titleBlockHeight + subtitleH;
  const UPPER_MIN = 7500;
  const UPPER_H = Math.max(UPPER_MIN, upperContentH + 1500 + 800);
  const DIVIDER_H = 60;
  const spacerIntrinsic = 280;
  const topSpacing = Math.max(UPPER_H - upperContentH - spacerIntrinsic - 800, 400);

  const upperBlock = new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    layout: TableLayoutType.FIXED,
    borders: allNoBorders,
    rows: [new TableRow({
      height: { value: UPPER_H, rule: "exact" },
      children: [new TableCell({
        shading: { type: ShadingType.CLEAR, fill: C.bg }, borders: noBorders,
        verticalAlign: "top",
        margins: { left: padL, right: padR },
        children: [
          new Paragraph({ spacing: { before: topSpacing } }),
          config.englishLabel ? new Paragraph({
            spacing: { after: 500 },
            children: [new TextRun({ text: config.englishLabel.split("").join(" "), size: 18, color: C.accent, font: { ascii: "Calibri" }, characterSpacing: 60 })],
          }) : null,
          ...titleLines.map((line, i) => new Paragraph({
            spacing: { after: i < titleLines.length - 1 ? 100 : 200, line: Math.ceil(titlePt * 23), lineRule: "atLeast" },
            children: [new TextRun({ text: line, size: titleSize, bold: true, color: C.cover.titleColor, font: { eastAsia: "SimHei", ascii: "Arial" } })],
          })),
          config.subtitle ? new Paragraph({
            spacing: { after: 100 },
            children: [new TextRun({ text: config.subtitle, size: 24, color: C.cover.subtitleColor, font: { eastAsia: "Microsoft YaHei", ascii: "Arial" } })],
          }) : null,
        ].filter(Boolean),
      })],
    })],
  });

  const divider = new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    borders: allNoBorders,
    rows: [new TableRow({
      height: { value: DIVIDER_H, rule: "exact" },
      children: [new TableCell({ borders: noBorders, shading: { type: ShadingType.CLEAR, fill: C.accent }, children: [emptyPara()] })],
    })],
  });

  const lowerContent = [
    new Paragraph({ spacing: { before: 800 } }),
    ...(config.metaLines || []).map(line => new Paragraph({
      indent: { left: padL }, spacing: { after: 100 },
      children: [new TextRun({ text: line, size: 28, color: C.cover.metaColor, font: { eastAsia: "Microsoft YaHei", ascii: "Arial" } })],
    })),
    new Paragraph({ spacing: { before: 2000 } }),
    new Paragraph({
      indent: { left: padL },
      children: [
        new TextRun({ text: config.footerLeft || "", size: 22, color: "909090", font: { eastAsia: "Microsoft YaHei", ascii: "Arial" } }),
        new TextRun({ text: "          " }),
        new TextRun({ text: config.footerRight || "", size: 22, color: "909090", font: { ascii: "Arial" } }),
      ],
    }),
  ];

  return [new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    layout: TableLayoutType.FIXED,
    borders: allNoBorders,
    rows: [new TableRow({
      height: { value: 16838, rule: "exact" },
      children: [new TableCell({
        shading: { type: ShadingType.CLEAR, fill: "FFFFFF" }, borders: noBorders,
        verticalAlign: "top",
        children: [upperBlock, divider, ...lowerContent],
      })],
    })],
  })];
}

// ── 正文组件 ──
const F_BODY = { ascii: "Times New Roman", eastAsia: "SimSun" };
const F_HEAD = { ascii: "Times New Roman", eastAsia: "SimHei" };

function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    keepNext: true,
    spacing: { before: 360, after: 160, line: 312 },
    children: [new TextRun({ text, bold: true, size: 32, color: P.primary, font: F_HEAD })],
  });
}
function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    keepNext: true,
    spacing: { before: 240, after: 120, line: 312 },
    children: [new TextRun({ text, bold: true, size: 28, color: P.primary, font: F_HEAD })],
  });
}
function p(text, opts = {}) {
  return new Paragraph({
    alignment: AlignmentType.JUSTIFIED,
    indent: { firstLine: 480 },
    spacing: { line: 312, after: 80 },
    children: [new TextRun({ text, size: 24, color: P.body, font: F_BODY, bold: !!opts.bold })],
  });
}
// 段内混排（部分加粗）
function pRuns(runs) {
  return new Paragraph({
    alignment: AlignmentType.JUSTIFIED,
    indent: { firstLine: 480 },
    spacing: { line: 312, after: 80 },
    children: runs.map(r => new TextRun({ text: r.t, bold: !!r.b, size: 24, color: P.body, font: F_BODY })),
  });
}
function numItem(ref, text, bold = false) {
  return new Paragraph({
    numbering: { reference: ref, level: 0 },
    alignment: AlignmentType.JUSTIFIED,
    spacing: { line: 312, after: 60 },
    children: [new TextRun({ text, size: 24, color: P.body, font: F_BODY, bold })],
  });
}
function bullet(text) {
  return new Paragraph({
    bullet: { level: 0 },
    alignment: AlignmentType.JUSTIFIED,
    spacing: { line: 312, after: 60 },
    children: [new TextRun({ text, size: 24, color: P.body, font: F_BODY })],
  });
}
function tableTitle(text) {
  return new Paragraph({
    keepNext: true,
    spacing: { before: 160, after: 80, line: 312 },
    children: [new TextRun({ text, bold: true, size: 21, color: P.secondary, font: F_HEAD })],
  });
}
// 横线商务表
function mkTable(headers, rows, widths, opts = {}) {
  const mkCell = (text, isHeader, w, alignRight, fill) => new TableCell({
    width: { size: w, type: WidthType.PERCENTAGE },
    shading: isHeader
      ? { type: ShadingType.CLEAR, fill: P.table.headerBg }
      : (fill ? { type: ShadingType.CLEAR, fill } : undefined),
    margins: { top: 60, bottom: 60, left: 120, right: 120 },
    children: [new Paragraph({
      alignment: alignRight ? AlignmentType.RIGHT : AlignmentType.LEFT,
      spacing: { line: 276 },
      children: [new TextRun({
        text, bold: isHeader, size: 21,
        color: isHeader ? P.table.headerText : P.body,
        font: { ascii: "Times New Roman", eastAsia: isHeader ? "SimHei" : "SimSun" },
      })],
    })],
  });
  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    borders: {
      top: { style: BorderStyle.SINGLE, size: 4, color: P.table.accentLine },
      bottom: { style: BorderStyle.SINGLE, size: 4, color: P.table.accentLine },
      left: NB, right: NB,
      insideHorizontal: { style: BorderStyle.SINGLE, size: 2, color: P.table.innerLine },
      insideVertical: NB,
    },
    rows: [
      new TableRow({
        tableHeader: true, cantSplit: true,
        children: headers.map((t, i) => mkCell(t, true, widths[i], opts.rightCols && opts.rightCols.includes(i))),
      }),
      ...rows.map((r, ri) => new TableRow({
        cantSplit: true,
        children: r.map((t, i) => mkCell(
          t, false, widths[i],
          opts.rightCols && opts.rightCols.includes(i),
          opts.zebra && ri % 2 === 1 ? P.table.surface : undefined,
        )),
      })),
    ],
  });
}
// 提示框（浅底 + 左侧强调线）
function callout(title, text) {
  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    borders: { top: NB, bottom: NB, right: NB, insideHorizontal: NB, insideVertical: NB,
      left: { style: BorderStyle.SINGLE, size: 12, color: P.accent } },
    rows: [new TableRow({
      cantSplit: true,
      children: [new TableCell({
        shading: { type: ShadingType.CLEAR, fill: P.table.surface },
        margins: { top: 100, bottom: 100, left: 160, right: 160 },
        children: [
          new Paragraph({
            spacing: { line: 300, after: 40 },
            children: [new TextRun({ text: title, bold: true, size: 22, color: P.primary, font: F_HEAD })],
          }),
          new Paragraph({
            alignment: AlignmentType.JUSTIFIED,
            spacing: { line: 300 },
            children: [new TextRun({ text, size: 21, color: P.body, font: F_BODY })],
          }),
        ],
      })],
    })],
  });
}
const spacerAfterTable = () => new Paragraph({ spacing: { after: 60 }, children: [] });

// ── 页脚 ──
const pageFooter = () => new Footer({
  children: [new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ children: [PageNumber.CURRENT], size: 18, color: P.secondary, font: { ascii: "Times New Roman" } })],
  })],
});
const bodyHeader = new Header({
  children: [new Paragraph({
    alignment: AlignmentType.CENTER,
    border: { bottom: { style: BorderStyle.SINGLE, size: 2, color: P.table.innerLine, space: 4 } },
    children: [new TextRun({ text: "抖音来客订单同步与门店微信播报系统 · 私有化部署解决方案", size: 16, color: P.secondary, font: { eastAsia: "SimSun", ascii: "Times New Roman" } })],
  })],
});

// ═══════════════ 正文内容 ═══════════════
const body = [];

// 1 项目背景与目标
body.push(h1("1. 项目背景与目标"));
body.push(h2("1.1 建设背景"));
body.push(p("商家在抖音平台通过直播、短视频挂载团购券的方式售卖线下服务，顾客线上购买后到对应门店核销使用。目前订单的同步、门店群的客户播报依赖第三方 SaaS 平台完成，存在经营数据（订单、客户联系方式、核销记录）沉淀在外部平台的顾虑，也不便于与商家内部系统深度整合。"));
body.push(p("为此，商家拟建设一套部署在自有内部网络中的私有化系统，在数据完全自主可控的前提下，实现抖音订单到门店微信群的自动化同步与播报。"));
body.push(h2("1.2 建设目标"));
body.push(numItem("goals", "订单同步：自动获取抖音来客的团购订单、买家信息、门店与商品数据，无需人工导出；"));
body.push(numItem("goals", "门店播报：订单产生后自动将顾客、商品、订单号、门店等信息推送到订单对应门店的微信群，并支持预约提醒；"));
body.push(numItem("goals", "核销闭环：支持系统内核销操作与核销状态双向同步，自动统计各门店核销率；"));
body.push(numItem("goals", "经营看板：提供门店、商品、时段维度的订单量、金额、核销率实时看板；"));
body.push(numItem("goals", "数据自主：全部数据存储在商家自有服务器，手机号等敏感信息加密存储。"));
body.push(h2("1.3 建设范围说明"));
body.push(p("一期聚焦抖音来客（本地生活服务类团购）场景，实现订单同步、门店播报、核销统计与经营看板的完整闭环；抖店（实物商品、快递发货类目）作为二期扩展预留，届时可增加配货、发货提醒等内部协同场景。本方案第 6 章报价仅覆盖一期及可选扩展包。"));

// 2 总体方案设计
body.push(h1("2. 总体方案设计"));
body.push(h2("2.1 业务闭环"));
body.push(p("系统围绕以下五个环节形成业务闭环："));
body.push(numItem("flow", "线上成交：商家在抖音直播、短视频或店铺页面售卖服务类团购券，顾客下单购买；"));
body.push(numItem("flow", "订单同步：系统通过抖音来客开放接口增量拉取新订单与核销、退款状态变化；"));
body.push(numItem("flow", "门店播报：按商品与门店的映射规则，将订单与顾客信息自动推送到对应门店微信群；"));
body.push(numItem("flow", "到店核销：顾客到店后由门店在系统内核销，或商家在抖音来客后台核销后系统自动同步；"));
body.push(numItem("flow", "经营分析：核销率、订单量、订单金额等指标实时汇入经营看板，辅助多门店管理。"));
body.push(h2("2.2 系统架构"));
body.push(p("系统部署于商家内部网络，采用纯出网架构：抖音订单采用授权后增量轮询方式获取，无需在防火墙开放公网入站端口，最大限度收窄暴露面。整体架构如下："));
body.push(tableTitle("表 2-1 系统架构组成"));
body.push(mkTable(
  ["层级", "组件", "说明"],
  [
    ["接入层", "Web 管理后台", "浏览器访问，供总部管理员与门店店长配置与查看，按角色与门店隔离数据"],
    ["应用服务层", "API 服务 + 同步 Worker + 推送引擎", "订单增量拉取与状态机管理；事件驱动的模板渲染、群路由、发送队列与重试"],
    ["数据层", "PostgreSQL + Redis", "门店、商品映射、订单、客户、推送记录等主数据；手机号等敏感字段加密存储"],
    ["部署层", "Docker Compose 一键部署", "含备份策略、监控告警、升级方案与运维手册；可选 Windows 播报节点（见 3.3 通道 C）"],
    ["外部依赖", "抖音来客接口、企业微信接口", "仅出网 HTTPS 访问，域名白名单清单随部署手册交付"],
  ],
  [16, 34, 50],
));
body.push(spacerAfterTable());
body.push(h2("2.3 多门店与路由模型"));
body.push(bullet("门店档案与抖音来客门店（POI）一一绑定，支持总部多门店统一管理；"));
body.push(bullet("抖音商品（SKU）到门店的映射规则可配置，未匹配订单按默认门店兜底；"));
body.push(bullet("门店与播报群绑定，支持一店一群或多群；"));
body.push(bullet("员工账号归属门店，数据按门店隔离，总部账号可看全局。"));

// 3 对接方案设计
body.push(h1("3. 对接方案设计"));
body.push(h2("3.1 抖音来客（一期核心）"));
body.push(p("接入路径采用商家自研应用模式：商家在抖音来客后台进入店铺管理、服务应用授权，以商家自研服务身份入驻抖音开放平台并创建应用，随后授权本系统访问。该模式下数据仅在商家自有店铺范围内流转，与私有化部署模式天然匹配。"));
body.push(bullet("数据范围：门店（POI）、团购商品与 SKU、订单列表与详情（增量）、核销状态、退款状态、买家昵称与联系方式；"));
body.push(bullet("核销闭环：支持本系统调用核销接口直接核销；商家在来客后台人工核销的订单亦自动同步，保证两端一致；"));
body.push(bullet("同步机制：增量轮询 + 失败重试 + 每日对账补拉，接口权限审批周期通常为 1 至 4 周，与开发并行推进。"));
body.push(callout("重要说明：买家联系方式",
  "抖音平台对买家联系方式有隐私保护策略，接口实际返回明文手机号还是脱敏号，需在项目启动后第一周以商家真实店铺授权验证（M0 验证项）。若为脱敏号，播报模板自动降级为尾号加到店补录模式：顾客到店报手机号后由门店在系统内补录归档。该项验证结论不影响系统整体交付。"));
body.push(spacerAfterTable());
body.push(h2("3.2 抖店（二期预留）"));
body.push(p("抖店开放平台对商家自研应用支持成熟，订单、物流、发货接口齐全。二期可扩展配货发货提醒包：订单事件自动播报到仓库或门店内部群（新增订单、待配货、已发货、异常件），并可选回写发货状态。该包不在一期范围内。"));
body.push(h2("3.3 微信播报通道矩阵"));
body.push(p("微信群机器人能力存在平台硬性限制：企业微信机器人仅支持企微内部群，不支持含顾客的外部群；个人微信没有官方机器人接口。因此系统按群性质设计三条通道，并在系统内统一为通道适配器，可按群配置、随时切换："));
body.push(tableTitle("表 3-1 播报通道矩阵"));
body.push(mkTable(
  ["通道", "适用群", "实现方式", "自动化程度", "合规与风险"],
  [
    ["A. 企微群机器人", "门店员工群、仓库/配货群、管理层群（企微内部群）", "企业微信官方机器人接口", "全自动实时，20 条/分钟", "官方合规，零风险"],
    ["B. 企微客户群", "含顾客的企微外部群", "企业微信客户联系：入群欢迎语（全自动）+ 客户群群发接口", "欢迎语全自动；群发每群每天约 1 条、需员工在企微端确认", "官方合规，但无法做到每单实时推送"],
    ["C. 微信客户端自动化", "存量个人微信群（顾客群）", "自研微信桌面端自动化引擎，在真机上以门店机器人号发送", "全自动实时，可支持群内 @机器人 查订单等互动（差异化能力）", "非官方通道，存在账号风控风险；采用真机、真人号、低频播报策略降低风险，机器人号与主号隔离"],
  ],
  [16, 22, 24, 19, 19],
));
body.push(spacerAfterTable());
body.push(p("通道选择建议：内部协同群一律走通道 A，合规且全自动；顾客所在的群默认采用通道 B（合规档），如需对齐行业同类产品每单实时播报的体验，可加购通道 C（体验档），由商家在充分知情的基础上选择。无论通道 C 是否选购，通道 A 与 B 均不受影响，系统核心交付不受牵连。"));
body.push(p("可选增强：通过企业微信微信客服以点对点方式向顾客本人发送订单与预约提醒，合规、不进群，适合对打扰敏感的场景。"));
body.push(h2("3.4 可选外围对接"));
body.push(p("短信提醒兜底（需报备签名与模板）、云打印核销小票、门店地图定位展示。以上均为可选项，按需在实施中确定。"));

// 4 功能范围
body.push(h1("4. 功能范围"));
body.push(h2("4.1 一期功能清单"));
body.push(tableTitle("表 4-1 一期功能清单"));
body.push(mkTable(
  ["模块", "功能点"],
  [
    ["账号与权限", "登录认证、角色-权限-门店数据域（RBAC）、员工管理、操作日志"],
    ["主数据中心", "门店管理、抖音商品/SKU 到门店映射、门店到播报群绑定、映射规则配置界面"],
    ["抖音来客同步", "授权管理、增量订单拉取、状态机（待核销/已核销/已退款）、对账与补拉"],
    ["订单中心", "列表/详情/多维筛选/导出、系统内核销、退款同步、异常告警"],
    ["客户中心", "顾客档案（手机号去重合并）、到店信息补录"],
    ["播报引擎", "事件订阅（新订单/核销/预约提醒）、消息模板与变量、定时与免打扰时段、发送队列与重试、发送记录查询"],
    ["播报通道", "通道 A/B 标准交付；通道 C 为可选项"],
    ["数据看板", "今日接单门店/订单数/订单金额/核销金额/核销率、趋势图、门店与商品维度对比、数据导出"],
    ["部署交付", "内网 Docker 部署、备份策略、监控告警、运维手册、使用培训"],
  ],
  [22, 78],
));
body.push(spacerAfterTable());
body.push(h2("4.2 二期可选包"));
body.push(tableTitle("表 4-2 二期可选包"));
body.push(mkTable(
  ["可选包", "内容", "适用场景"],
  [
    ["抖店发货提醒包", "抖店订单同步、配货/发货状态内部群播报、发货回写", "实物商品场景"],
    ["群内互动（差异化）", "@机器人 查订单/查核销码、群指令菜单", "已选购通道 C"],
    ["抢单/客服工作台", "抢单大厅、分配处理、跟进记录", "多门店运营"],
    ["顾客次卡/套餐", "套餐资产、剩余次数、有效期预警", "储值、多次服务"],
    ["移动端 H5 看板", "经营层手机随时查看数据", "管理层移动办公"],
  ],
  [22, 52, 26],
));
body.push(spacerAfterTable());

// 5 开发计划
body.push(h1("5. 开发计划"));
body.push(h2("5.1 里程碑计划"));
body.push(p("以合同生效日为 T0，总日历工期约 14 周（含 2 至 3 周试运行陪跑）。团队配置：后端 2 人、前端 1 人、测试 0.5 人、项目经理 0.5 人。"));
body.push(tableTitle("表 5-1 里程碑计划"));
body.push(mkTable(
  ["阶段", "内容", "工期", "交付物 / 出口标准"],
  [
    ["M0 启动与验证", "双方立项；商家完成抖音开放平台入驻与授权申请；以真实店铺验证订单、联系方式、核销接口的实际范围；细化需求与播报文案模板", "第 1–2 周", "PoC 验证报告、需求规格书（双方确认）"],
    ["M1 基础平台", "部署框架、账号权限、门店与映射配置中心", "第 3–5 周", "后台可登录，门店/映射可配置"],
    ["M2 订单链路", "抖音来客同步、订单中心、客户中心（权限审批与开发并行）", "第 5–9 周", "真实订单进系统，核销状态一致"],
    ["M3 播报链路", "播报引擎、通道 A/B（如选购含通道 C）", "第 8–11 周", "真实订单自动播报到指定群"],
    ["M4 看板与打磨", "数据看板、报表导出、整体联调", "第 10–12 周", "看板指标与来客后台对账一致"],
    ["M5 部署与试运行", "商家内网正式部署、培训、2–3 周试运行陪跑、验收", "第 12–14 周", "验收报告、全套文档移交"],
  ],
  [15, 37, 14, 34],
));
body.push(spacerAfterTable());
body.push(h2("5.2 验收标准"));
body.push(bullet("真实订单从成交到群播报延迟不超过 3 分钟（通道 A / C）；"));
body.push(bullet("订单同步与抖音来客后台 T+1 对账差异率不超过 0.5%；"));
body.push(bullet("看板核心指标与来客后台误差不超过 1%；"));
body.push(bullet("试运行期系统可用性不低于 99.5%；手机号等敏感字段落库加密抽查通过。"));
body.push(h2("5.3 商家侧配合事项"));
body.push(bullet("提供抖音来客商家账号，并完成自研应用创建与授权；"));
body.push(bullet("提供企业微信管理员权限（开通自建应用）；"));
body.push(bullet("提供内网服务器（建议 8 核 16G 起，支持 Docker）及出网域名白名单配置；"));
body.push(bullet("指定门店与人员配合联调测试（UAT）；"));
body.push(bullet("如选购通道 C：提供播报用微信号及一台可长期开机的 Windows 电脑。"));

// 6 报价
body.push(h1("6. 报价"));
body.push(h2("6.1 一期报价（固定总价包干）"));
body.push(tableTitle("表 6-1 一期工作量明细"));
body.push(mkTable(
  ["模块", "人日"],
  [
    ["需求调研与方案细化", "12"],
    ["项目管理（贯穿全程）", "20"],
    ["账号权限与基础平台", "12"],
    ["抖音来客对接与订单同步", "28"],
    ["门店/商品/群路由配置中心", "15"],
    ["订单中心（含核销）", "25"],
    ["播报引擎", "22"],
    ["企微通道集成（通道 A+B）", "12"],
    ["数据看板与报表", "15"],
    ["数据安全（加密/脱敏/审计）", "6"],
    ["私有化部署、文档与培训", "8"],
    ["测试与验收支持（含试运行陪跑）", "28"],
    ["合计", "203"],
  ],
  [70, 30],
  { rightCols: [1], zebra: true },
));
body.push(spacerAfterTable());
body.push(pRuns([
  { t: "一期打包价：", b: true },
  { t: "人民币 298,000 元（含 3 个月免费质保）", b: true },
  { t: "。打包折扣后约合 1,470 元/人日；合同范围外的新增开发按 2,000 元/人日另行评估，经双方书面确认后排期。" },
]));
body.push(h2("6.2 二期可选包报价"));
body.push(tableTitle("表 6-2 二期可选包报价（一期客户签约享 9 折）"));
body.push(mkTable(
  ["可选包", "报价（人民币）"],
  [
    ["抖店发货提醒包", "￥35,000"],
    ["群内互动（@机器人查订单，需通道 C）", "￥25,000"],
    ["抢单/客服工作台", "￥40,000"],
    ["顾客次卡/套餐资产", "￥30,000"],
    ["移动端 H5 看板", "￥30,000"],
    ["通道 C：微信客户端自动化播报", "￥28,000（含 1 门店试点；每增加 10 门店 ￥8,000）"],
    ["年度运维（第 2 年起，可选）", "一期合同额的 15%/年（小版本升级、故障响应、巡检）"],
  ],
  [52, 48],
));
body.push(spacerAfterTable());
body.push(h2("6.3 付款节点"));
body.push(tableTitle("表 6-3 付款节点建议"));
body.push(mkTable(
  ["节点", "比例", "支付条件"],
  [
    ["合同签订", "30%", "合同生效后 5 个工作日内"],
    ["订单链路联调通过", "30%", "M2 出口标准达成，真实订单进系统"],
    ["内网部署完成", "30%", "系统在商家内网上线并进入试运行"],
    ["最终验收", "10%", "试运行结束，双方签署验收报告"],
  ],
  [34, 14, 52],
));
body.push(spacerAfterTable());
body.push(h2("6.4 商家自行承担的第三方费用"));
body.push(p("服务器硬件或虚拟机、企业微信（免费版即可满足需求）、抖音平台相关费用（接口暂无费用，以平台届时政策为准）、短信与云打印服务（如选购）、通道 C 所需的微信号与设备。以上费用不含在本方案报价内。"));

// 7 边界、风险与责任划分
body.push(h1("7. 边界、风险与责任划分"));
body.push(tableTitle("表 7-1 风险与责任划分"));
body.push(mkTable(
  ["事项", "说明", "责任与对策"],
  [
    ["抖音接口权限范围", "以平台审批结果为准，审批周期 1–4 周", "乙方主导提单与跟进；个别接口受限时提供来客后台导出导入的降级链路，不影响整体交付"],
    ["买家手机号脱敏", "平台隐私策略可能不返回明文手机号", "M0 阶段真店验证；如脱敏则模板自动降级为尾号显示与到店补录"],
    ["通道 C 账号风控", "非官方通道存在微信账号被限制的可能", "商家知情选择；机器人号独立、低频播报、真机自动化；通道受限不构成验收失败（通道 A/B 不受影响）"],
    ["内网环境保障", "服务器、网络、备份由商家 IT 保障", "乙方提供部署包、手册与远程指导；现场实施费用另议"],
    ["需求变更", "一期范围外的新增需求", "按 2,000 元/人日评估变更单，双方书面确认后排期"],
  ],
  [18, 32, 50],
));
body.push(spacerAfterTable());

// 8 交付物清单
body.push(h1("8. 交付物清单"));
body.push(p("项目验收通过后，乙方向甲方移交以下交付物：系统部署包与部署手册、运维手册、用户操作手册、数据库设计文档、接口对接文档、测试报告、验收报告、培训材料（管理员与门店店长各一场）。系统源码的交付方式（源码移交或加密托管）按合同约定执行。"));
body.push(new Paragraph({
  spacing: { before: 300, line: 300 },
  children: [new TextRun({
    text: "附注：本方案所载工期与报价为编制时点的建议值，最终以双方签署的合同及其附件为准。本方案涉及商家经营信息，请妥善保管，未经许可请勿向第三方传播。",
    italics: true, size: 18, color: "888888", font: F_BODY,
  })],
}));

// ═══════════════ 文档组装 ═══════════════
const doc = new Document({
  creator: "Solution Team",
  title: "抖音来客订单同步与门店微信播报系统 私有化部署解决方案",
  styles: {
    default: {
      document: {
        run: { font: { ascii: "Times New Roman", eastAsia: "SimSun" }, size: 24, color: "000000" },
        paragraph: { spacing: { line: 312 } },
      },
      heading1: {
        run: { font: F_HEAD, size: 32, bold: true, color: P.primary },
        paragraph: { spacing: { before: 360, after: 160, line: 312 }, outlineLevel: 0 },
      },
      heading2: {
        run: { font: F_HEAD, size: 28, bold: true, color: P.primary },
        paragraph: { spacing: { before: 240, after: 120, line: 312 }, outlineLevel: 1 },
      },
    },
  },
  numbering: {
    config: ["goals", "flow"].map(ref => ({
      reference: ref,
      levels: [{
        level: 0, format: LevelFormat.DECIMAL, text: "%1.",
        alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 720, hanging: 360 } } },
      }],
    })),
  },
  sections: [
    { // 封面
      properties: {
        page: { size: { width: 11906, height: 16838 }, margin: { top: 0, bottom: 0, left: 0, right: 0 } },
      },
      children: buildCoverR4({
        title: "抖音来客订单同步与门店微信播报系统",
        subtitle: "私有化部署解决方案（含开发计划与报价）",
        englishLabel: "SOLUTION PROPOSAL",
        metaLines: [
          "文档版本：V1.0",
          "编制日期：2026 年 9 月 6 日",
          "文档性质：商务解决方案（面向客户评审）",
          "编制单位：【乙方公司名称】",
        ],
        footerLeft: "保密文件 · 仅供客户评审使用",
        footerRight: "2026",
        palette: P,
      }),
    },
    { // 目录（罗马页码）
      properties: {
        type: SectionType.NEXT_PAGE,
        page: {
          size: { width: 11906, height: 16838 },
          margin: { top: 1440, bottom: 1440, left: 1701, right: 1417 },
          pageNumbers: { start: 1, formatType: NumberFormat.UPPER_ROMAN },
        },
      },
      footers: { default: pageFooter() },
      children: [
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 480, after: 360 },
          children: [new TextRun({ text: "目  录", bold: true, size: 32, font: F_HEAD, color: P.primary })],
        }),
        new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-2" }),
        new Paragraph({
          spacing: { before: 200 },
          children: [new TextRun({
            text: "注：本目录由域代码生成。文档编辑后如页码变化，请在目录上点击右键并选择\u201c更新域\u201d以刷新页码。",
            italics: true, size: 18, color: "888888", font: F_BODY,
          })],
        }),
      ],
    },
    { // 正文（阿拉伯页码从 1 起）
      properties: {
        type: SectionType.NEXT_PAGE,
        page: {
          size: { width: 11906, height: 16838 },
          margin: { top: 1440, bottom: 1440, left: 1701, right: 1417 },
          pageNumbers: { start: 1, formatType: NumberFormat.DECIMAL },
        },
      },
      headers: { default: bodyHeader },
      footers: { default: pageFooter() },
      children: body,
    },
  ],
});

const OUT = "C:/repos/aid-work-agent/docs/design/douyin-lifeservice-dispatch/抖音来客订单同步与门店微信播报系统-私有化部署解决方案-V1.0.docx";
Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync(OUT, buf);
  console.log("OK ->", OUT, buf.length, "bytes");
});
