import assert from 'node:assert/strict'
import test from 'node:test'
import { FilterSetter, FilterSetError, viewportOf } from '../src/main/boss/FilterSetter.js'
import type { DomSnapshot, ClickPoint } from '../src/main/boss/domSnapshot.js'

/**
 * 构造单 document 的测试 snapshot。node 0 为根（bounds = 视口 1917x1905，device px），
 * 其余节点为文本项。坐标取自 2026-08-05 真机 demo 实测值。
 * parents[i] 为第 i 个文本项的父节点 index（默认 0 = 根直下）。
 */
function buildSnap(
  items: Array<{ text: string; bounds: [number, number, number, number] }>,
  parents?: number[],
): DomSnapshot {
  const idx = [0, ...items.map((_, i) => i + 1)]
  return {
    strings: ['', ...items.map((i) => i.text)],
    documents: [
      {
        nodes: {
          nodeValue: { index: idx, value: idx },
          contentDocumentIndex: { index: [], value: [] },
          parentIndex: [0, ...items.map((_, i) => parents?.[i] ?? 0)],
        },
        layout: { nodeIndex: idx, bounds: [[0, 0, 1917, 1905], ...items.map((i) => i.bounds)] },
      },
    ],
  }
}

/** 面板打开态：行标签 + 选项 + 确定 + 候选人卡片里的重名诱饵「本科」 */
const panelItems = [
  { text: '筛选', bounds: [1514, 33.5, 42, 23] as [number, number, number, number] },
  { text: '经验要求', bounds: [600, 629, 75, 22] as [number, number, number, number] },
  { text: '5-10年', bounds: [874.5, 629, 58.5, 22] as [number, number, number, number] },
  { text: '学历要求', bounds: [600, 758.5, 75, 22] as [number, number, number, number] },
  { text: '本科', bounds: [1349, 758.5, 39, 22] as [number, number, number, number] },
  { text: '硕士', bounds: [1442, 758.5, 39, 22] as [number, number, number, number] },
  { text: '博士', bounds: [1535, 758.5, 39, 22] as [number, number, number, number] },
  { text: '薪资待遇[单选]', bounds: [600, 898, 105, 22] as [number, number, number, number] },
  { text: '10-20K', bounds: [1192, 898, 63, 22] as [number, number, number, number] },
  { text: '清除', bounds: [1633, 988.5, 42, 23] as [number, number, number, number] },
  { text: '确定', bounds: [1738.5, 988.5, 42, 23] as [number, number, number, number] },
  // 诱饵：候选人卡片里的「本科」，同行带外、x 在标签左侧区域
  { text: '本科', bounds: [600, 1556, 39, 22] as [number, number, number, number] },
]

const closedSnap = buildSnap([{ text: '筛选', bounds: [1514, 33.5, 42, 23] }])
const panelSnap = buildSnap(panelItems)
const doneSnap = buildSnap([{ text: '筛选·5', bounds: [1514, 33.5, 60, 23] }])
const doneSnap1 = buildSnap([{ text: '筛选·1', bounds: [1514, 33.5, 60, 23] }])

/** 依调用序返回 snapshot 队列，用完后重复最后一个 */
function snapshotQueue(snaps: DomSnapshot[]): () => Promise<DomSnapshot> {
  let i = 0
  return async () => snaps[Math.min(i++, snaps.length - 1)]!
}

function recorder() {
  const clicks: ClickPoint[] = []
  return {
    clicks,
    click: async (p: ClickPoint) => {
      clicks.push(p)
    },
    sleep: async () => {},
  }
}

const FULL_SPEC = { experience: '5-10年', educations: ['本科', '硕士', '博士'], salary: '10-20K' }

test('完整流程：开面板 → 清除残留 → 5 个选项 → 确定 → 徽章 筛选·5 校验通过', async () => {
  const r = recorder()
  const setter = new FilterSetter({
    // 9 次 snapshot：初始 + 开面板后（清除定位复用） + 5 选项 + 确定前 + 徽章校验
    snapshot: snapshotQueue([closedSnap, ...Array(7).fill(panelSnap), doneSnap]),
    click: r.click,
    sleep: r.sleep,
  })
  const result = await setter.apply(FULL_SPEC)
  assert.equal(result.filterCount, 5)
  // 点击序列：筛选按钮 + 清除 + 5 个选项 + 确定 = 8 次
  assert.equal(r.clicks.length, 8)
  // 筛选按钮中心
  assert.deepEqual(r.clicks[0], { x: 1535, y: 45 })
  // 必须先点清除（残留已选状态下直接点同名选项会变反选，2026-08-06 实测翻车）
  assert.deepEqual(r.clicks[1], { x: 1654, y: 1000 })
  // 5-10年（经验要求行内）
  assert.deepEqual(r.clicks[2], { x: 903.75, y: 640 })
  // 本科命中的是学历要求行内的选项，不是候选人卡片诱饵（x≈619）
  assert.deepEqual(r.clicks[3], { x: 1368.5, y: 769.5 })
  assert.deepEqual(r.clicks[6], { x: 1223.5, y: 909 })
  assert.deepEqual(r.clicks[7], { x: 1759.5, y: 1000 })
})

test('已带计数的筛选按钮（筛选·3）也能定位开面板', async () => {
  const r = recorder()
  const withBadge = buildSnap([{ text: '筛选·3', bounds: [1514, 33.5, 60, 23] }])
  const badge1 = buildSnap([{ text: '筛选·1', bounds: [1514, 33.5, 60, 23] }])
  const setter = new FilterSetter({
    snapshot: snapshotQueue([withBadge, panelSnap, panelSnap, panelSnap, badge1]),
    click: r.click,
    sleep: r.sleep,
  })
  const result = await setter.apply({ experience: '5-10年' })
  assert.equal(result.filterCount, 1)
  assert.deepEqual(r.clicks[0], { x: 1544, y: 45 })
})

test('面板未打开（行标签缺失）→ fail-loud，不盲点', async () => {
  const r = recorder()
  const setter = new FilterSetter({ snapshot: snapshotQueue([closedSnap, closedSnap]), click: r.click, sleep: r.sleep })
  await assert.rejects(setter.apply(FULL_SPEC), FilterSetError)
  // 只点了筛选按钮一次，后续选项绝不再点
  assert.equal(r.clicks.length, 1)
})

test('行内选项缺失（数值档位 20年以上，页面最高 5-10年）→ 保底映射最接近档位并记录 substitution', async () => {
  const r = recorder()
  const setter = new FilterSetter({ snapshot: snapshotQueue([closedSnap, ...Array(3).fill(panelSnap), doneSnap1]), click: r.click, sleep: r.sleep })
  const result = await setter.apply({ experience: '20年以上' })
  assert.deepEqual(result.substitutions, [{ row: '经验要求', requested: '20年以上', matched: '5-10年' }])
  assert.equal(r.clicks.length, 4) // 筛选 + 清除 + 5-10年 + 确定
})

test('行内选项多命中（同名诱饵落在行带内）→ fail-loud', async () => {
  const r = recorder()
  const dup = buildSnap([
    ...panelItems,
    // 学历要求行带内再加一个「本科」（如 tooltip 复制品）
    { text: '本科', bounds: [1400, 758.5, 39, 22] },
  ])
  const setter = new FilterSetter({ snapshot: snapshotQueue([closedSnap, dup]), click: r.click, sleep: r.sleep })
  await assert.rejects(setter.apply({ educations: ['本科'] }), /恰好 1 个可见匹配，实际 2/)
})

test('行带内的卡片诱饵（x 在标签左缘右、右缘左）必须被排除（真机 2026-08-05：诱饵 cx=631）', async () => {
  // 弹层背后的候选人卡片「本科」x≈611.5，中心 631 < 标签右缘 675——真机曾因此报 2 个匹配
  const withCardDecoy = buildSnap([
    ...panelItems,
    { text: '本科', bounds: [611.5, 780, 39, 22] },
  ])
  const r = recorder()
  const setter = new FilterSetter({
    snapshot: snapshotQueue([closedSnap, withCardDecoy, withCardDecoy, withCardDecoy, doneSnap1]),
    click: r.click,
    sleep: r.sleep,
  })
  const result = await setter.apply({ educations: ['本科'] })
  assert.equal(result.filterCount, 1)
  // 清除之后命中的仍是行内选项 (1368.5,769.5)，不是卡片诱饵
  assert.deepEqual(r.clicks[1], { x: 1654, y: 1000 })
  assert.deepEqual(r.clicks[2], { x: 1368.5, y: 769.5 })
})

test('行带内、标签右侧的卡片诱饵被 LCA 容器结构级排除（真机 2026-08-07：筛选本科报 2 命中）', async () => {
  // 真机：弹层背后右列候选人卡片「本科」cy=622.5 落进学历要求行带 ±34.75 且 cx=1444 > 标签右缘，
  // 纯几何规则与真选项形成 2 命中。卡片在面板容器（行标签 LCA）之外，必须结构级排除。
  const N = panelItems.length
  const panelContainer = N + 1
  const cardContainer = N + 2
  const snap = buildSnap(
    [
      ...panelItems,
      { text: '', bounds: [560, 140, 1290, 900] as [number, number, number, number] }, // 面板容器节点
      { text: '', bounds: [300, 200, 1550, 600] as [number, number, number, number] }, // 卡片容器节点
      // 卡片诱饵：与真选项 [1349,758.5,39,22]（cy=769.5）同学历要求行带、x 在标签右侧
      { text: '本科', bounds: [1423, 752, 42, 23] as [number, number, number, number] },
    ],
    [
      ...panelItems.map(() => panelContainer), // 面板内容挂在面板容器下
      0, // 面板容器挂根
      0, // 卡片容器挂根
      cardContainer, // 诱饵挂在卡片容器下
    ],
  )
  const r = recorder()
  const setter = new FilterSetter({
    snapshot: snapshotQueue([closedSnap, snap, snap, snap, doneSnap1]),
    click: r.click,
    sleep: r.sleep,
  })
  const result = await setter.apply({ educations: ['本科'] })
  assert.equal(result.filterCount, 1)
  // 命中的仍是行内选项 (1368.5,769.5)，不是卡片诱饵 (1444,763.5)
  assert.deepEqual(r.clicks[2], { x: 1368.5, y: 769.5 })
})

test('选项折行：第二行选项落到行带外时，放宽下界到下一行标签仍能命中（真机 2026-08-07：5-10年 0 命中）', async () => {
  // 真机：经验要求行 8 个选项折成两行，5-10年 cy=770 在第二行；标签 cy=712，行带 ±34.75
  // （学历要求标签仅距 69.5px）。紧行带 0 命中 → 放宽到「下一行标签（求职意向 838.5）上沿」唯一命中。
  const wrapped = buildSnap([
    { text: '筛选', bounds: [1514, 33.5, 42, 23] as [number, number, number, number] },
    { text: '学历要求', bounds: [598.5, 631, 84, 23] as [number, number, number, number] },
    { text: '经验要求', bounds: [598.5, 700.5, 84, 23] as [number, number, number, number] },
    { text: '不限', bounds: [790, 702, 28, 22] as [number, number, number, number] },
    { text: '3-5年', bounds: [1654, 702, 58, 22] as [number, number, number, number] },
    // 第二行（折行）：cy=770，超出 ±34.75 行带
    { text: '5-10年', bounds: [875.5, 759, 58.5, 22] as [number, number, number, number] },
    { text: '10年以上', bounds: [987, 759, 80, 22] as [number, number, number, number] },
    { text: '求职意向', bounds: [598.5, 827, 84, 23] as [number, number, number, number] },
    { text: '清除', bounds: [1633, 988.5, 42, 23] as [number, number, number, number] },
    { text: '确定', bounds: [1738.5, 988.5, 42, 23] as [number, number, number, number] },
  ])
  const r = recorder()
  const setter = new FilterSetter({
    snapshot: snapshotQueue([closedSnap, wrapped, wrapped, wrapped, doneSnap1]),
    click: r.click,
    sleep: r.sleep,
  })
  const result = await setter.apply({ experience: '5-10年' })
  assert.equal(result.filterCount, 1)
  assert.deepEqual(r.clicks[2], { x: 904.75, y: 770 })
})

test('徽章计数与条件数不符 → fail-loud', async () => {
  const r = recorder()
  const wrongBadge = buildSnap([{ text: '筛选·4', bounds: [1514, 33.5, 60, 23] }])
  const setter = new FilterSetter({
    snapshot: snapshotQueue([closedSnap, panelSnap, panelSnap, panelSnap, panelSnap, panelSnap, panelSnap, panelSnap, wrongBadge]),
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(setter.apply(FULL_SPEC), /筛选·4/)
})

test('未提供任何条件 → 直接失败，不做任何页面操作', async () => {
  const r = recorder()
  const setter = new FilterSetter({ snapshot: snapshotQueue([closedSnap]), click: r.click, sleep: r.sleep })
  await assert.rejects(setter.apply({}), /未提供任何筛选条件/)
  assert.equal(r.clicks.length, 0)
})

test('分行布局：标签独占一行、选项在下一行也能命中（真机 2026-08-05 实测布局）', async () => {
  // 真机实测：经验要求标签 (598.5,631)，5-10年选项 (874.5,689)，垂直差 57.5px。
  // 行带必须由行距（最近其他行标签距离的一半 ≈ 64.5）推导，固定行高倍数会漏。
  const splitRow = buildSnap([
    { text: '筛选', bounds: [1514, 33.5, 42, 23] },
    { text: '经验要求', bounds: [598.5, 631, 84, 23] },
    { text: '5-10年', bounds: [874.5, 689, 58.53125, 22] },
    { text: '学历要求', bounds: [600, 760.5, 75, 22] },
    // 下一行选项区的同名诱饵（y 差 186.5，超出半个行距，必须被排除）
    { text: '5-10年', bounds: [874.5, 818.5, 58.53125, 22] },
    { text: '清除', bounds: [1633, 988.5, 42, 23] },
    { text: '确定', bounds: [1738.5, 988.5, 42, 23] },
  ])
  const r = recorder()
  const setter = new FilterSetter({
    snapshot: snapshotQueue([closedSnap, splitRow, splitRow, splitRow, doneSnap1]),
    click: r.click,
    sleep: r.sleep,
  })
  const result = await setter.apply({ experience: '5-10年' })
  assert.equal(result.filterCount, 1)
  assert.deepEqual(r.clicks[2], { x: 903.765625, y: 700 })
})

test('面板已打开（经验要求行标签可见）→ 跳过开面板，不再点筛选按钮', async () => {
  // 上次运行可能中途失败留下打开的面板；此时再点「筛选」会把面板关掉
  const r = recorder()
  const setter = new FilterSetter({
    snapshot: snapshotQueue([panelSnap, panelSnap, panelSnap, doneSnap1]),
    click: r.click,
    sleep: r.sleep,
  })
  const result = await setter.apply({ experience: '5-10年' })
  assert.equal(result.filterCount, 1)
  // 点击序列：清除 + 5-10年 + 确定 = 3 次，不含筛选按钮 (1535,45)
  assert.equal(r.clicks.length, 3)
  assert.deepEqual(r.clicks[0], { x: 1654, y: 1000 })
  assert.deepEqual(r.clicks[1], { x: 903.75, y: 640 })
})

test('清除流程：开面板 → 清除 → 确定 → 徽章无计数校验通过', async () => {
  const r = recorder()
  const setter = new FilterSetter({
    // 4 次 snapshot：初始（关） + 开面板校验（清除定位复用） + 确定 + 徽章校验
    snapshot: snapshotQueue([closedSnap, panelSnap, panelSnap, closedSnap]),
    click: r.click,
    sleep: r.sleep,
  })
  await setter.clear()
  // 点击序列：筛选按钮 → 清除 → 确定
  assert.equal(r.clicks.length, 3)
  assert.deepEqual(r.clicks[0], { x: 1535, y: 45 })
  assert.deepEqual(r.clicks[1], { x: 1654, y: 1000 })
  assert.deepEqual(r.clicks[2], { x: 1759.5, y: 1000 })
})

test('清除后面板已打开 → 跳过开面板，只点清除+确定', async () => {
  const r = recorder()
  const setter = new FilterSetter({
    snapshot: snapshotQueue([panelSnap, panelSnap, closedSnap]),
    click: r.click,
    sleep: r.sleep,
  })
  await setter.clear()
  assert.equal(r.clicks.length, 2)
  assert.deepEqual(r.clicks[0], { x: 1654, y: 1000 })
})

test('清除后徽章仍带计数（确定未生效）→ fail-loud', async () => {
  const r = recorder()
  const dirty = buildSnap([{ text: '筛选·2', bounds: [1514, 33.5, 60, 23] }])
  const setter = new FilterSetter({
    snapshot: snapshotQueue([closedSnap, panelSnap, panelSnap, dirty]),
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(setter.clear(), /清除筛选校验失败/)
})

test('探针 describePanel：一次快照输出三行全部选项坐标，卡片诱饵被排除', async () => {
  // 与点击定位同一套行带/x 规则：「本科」卡片诱饵 (600,1556) 不得出现在学历要求行
  const r = recorder()
  const setter = new FilterSetter({ snapshot: snapshotQueue([panelSnap]), click: r.click, sleep: r.sleep })
  const rows = setter.describePanel(panelSnap)
  assert.equal(rows.length, 3)
  const byLabel = new Map(rows.map((row) => [row.label, row.options.map((o) => o.text)]))
  assert.deepEqual(byLabel.get('经验要求'), ['5-10年'])
  assert.deepEqual(byLabel.get('学历要求'), ['本科', '硕士', '博士'])
  assert.deepEqual(byLabel.get('薪资待遇[单选]'), ['10-20K'])
  // 选项按 x 升序；坐标与点击定位同口径
  const edu = rows[1]!
  assert.deepEqual(edu.options[0]!.point, { x: 1368.5, y: 769.5 })
  // 探针只读：零点击
  assert.equal(r.clicks.length, 0)
})

test('探针 describePanel：面板未打开（无行标签）返回空数组，不报错', async () => {
  const r = recorder()
  const setter = new FilterSetter({ snapshot: snapshotQueue([closedSnap]), click: r.click, sleep: r.sleep })
  assert.deepEqual(setter.describePanel(closedSnap), [])
})

test('探针 describePanel：面板容器外的同行带垃圾文本被结构级排除（LCA 容器）', async () => {
  // 真机实测：候选人卡片的技能标签/公司名与「薪资待遇」选项同 y 行带、x 也在标签右侧，
  // 纯几何规则无法排除——必须靠「行标签 LCA = 面板容器，卡片在容器外」排除
  const N = panelItems.length
  const panelContainer = N + 1
  const cardContainer = N + 2
  const snap = buildSnap(
    [
      ...panelItems,
      { text: '', bounds: [560, 140, 1290, 900] }, // 面板容器节点
      { text: '', bounds: [300, 700, 1550, 600] }, // 卡片容器节点
      // 与 10-20K 同行带、x > 标签右缘的卡片垃圾（真机：技能标签行 y≈891）
      { text: 'REDIS', bounds: [1490, 880, 38, 22] },
      { text: '本科', bounds: [1488, 868, 39, 22] },
    ],
    [
      ...panelItems.map(() => panelContainer), // 面板内容挂在面板容器下
      0, // 面板容器挂根
      0, // 卡片容器挂根
      cardContainer, // 垃圾挂在卡片容器下
      cardContainer,
    ],
  )
  const r = recorder()
  const setter = new FilterSetter({ snapshot: snapshotQueue([snap]), click: r.click, sleep: r.sleep })
  const rows = setter.describePanel(snap)
  const byLabel = new Map(rows.map((row) => [row.label, row.options.map((o) => o.text)]))
  assert.deepEqual(byLabel.get('薪资待遇[单选]'), ['10-20K'])
  assert.deepEqual(byLabel.get('学历要求'), ['本科', '硕士', '博士'])
})

test('viewportOf 取根文档 bounds 作为视口尺寸', () => {
  assert.deepEqual(viewportOf(closedSnap), { width: 1917, height: 1905 })
})

// ---------- 档位匹配策略（2026-08-17 用户定调：判断交 AI，CLI 只精确执行） ----------

test('normalizeOptionText：去空白+大写（15k-20k ≡ 15-20K）', async () => {
  const { normalizeOptionText } = await import('../src/main/boss/FilterSetter.js')
  assert.equal(normalizeOptionText('15k - 20k'), normalizeOptionText('15-20K'))
})

test('pickClosestOption 保底映射：15-30K → 20-50K（保下限）；5年以上 → 5-10年；本科 → null', async () => {
  const { pickClosestOption } = await import('../src/main/boss/FilterSetter.js')
  assert.equal(pickClosestOption('15-30K', ['10-20K', '20-50K']), '20-50K')
  assert.equal(pickClosestOption('15-30k', ['10-20K', '20-50K']), '20-50K')
  assert.equal(pickClosestOption('5年以上', ['1-3年', '3-5年', '5-10年', '10年以上']), '5-10年')
  assert.equal(pickClosestOption('15-20K', ['10-20K', '15-25K', '20-50K']), '15-25K')
  assert.equal(pickClosestOption('15-20K', ['10-20K']), '10-20K') // 全部低于下限 → 重叠最大
  assert.equal(pickClosestOption('本科', ['大专', '本科']), null) // 非数值不自动换
})

test('apply 保底映射：请求 15-20K（页面只有 10-20K）→ 实点 10-20K 并记录 substitution', async () => {
  const r = recorder()
  const setter = new FilterSetter({
    snapshot: snapshotQueue([closedSnap, ...Array(3).fill(panelSnap), doneSnap1]),
    click: r.click,
    sleep: r.sleep,
  })
  const result = await setter.apply({ salary: '15-20K' })
  assert.equal(result.filterCount, 1)
  assert.deepEqual(result.substitutions, [{ row: '薪资待遇', requested: '15-20K', matched: '10-20K' }])
  assert.equal(r.clicks.length, 4) // 筛选 + 清除 + 10-20K + 确定
  assert.deepEqual(r.clicks[2], { x: 1223.5, y: 909 })
})

test('apply 归一化兜底：传 15-25k（页面 15-25K）→ 点真实档位，不报错', async () => {
  const panel15 = buildSnap([
    ...panelItems,
    { text: '15-25K', bounds: [1280, 898, 63, 22] as [number, number, number, number] },
  ])
  const r = recorder()
  const setter = new FilterSetter({
    snapshot: snapshotQueue([closedSnap, ...Array(3).fill(panel15), doneSnap1]),
    click: r.click,
    sleep: r.sleep,
  })
  const result = await setter.apply({ salary: '15-25k' })
  assert.equal(result.filterCount, 1)
  assert.equal(r.clicks.length, 4) // 筛选 + 清除 + 15-25K + 确定
})

test('apply 非数值选项不存在（学历 大专以上）→ 报错并列出该行全部可选档位（AI 自纠用，绝不让用户看页面）', async () => {
  const r = recorder()
  const setter = new FilterSetter({
    snapshot: snapshotQueue([closedSnap, ...Array(4).fill(panelSnap)]),
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(setter.apply({ educations: ['大专以上'] }), (e: unknown) => {
    const msg = (e as Error).message
    return msg.includes('没有匹配的选项「大专以上」') && msg.includes('本科、硕士、博士') && msg.includes('boss_filter_options')
  })
})

test('probeOptions：开面板 → 读各行选项 → 点「筛选」收起还原页面', async () => {
  const r = recorder()
  const closedAgain = buildSnap([{ text: '筛选', bounds: [1514, 33.5, 42, 23] }])
  const setter = new FilterSetter({
    snapshot: snapshotQueue([closedSnap, panelSnap, panelSnap, closedAgain]),
    click: r.click,
    sleep: r.sleep,
  })
  const rows = await setter.probeOptions()
  const byLabel = new Map(rows.map((row) => [row.label, row.options.map((o) => o.text)]))
  assert.deepEqual(byLabel.get('经验要求'), ['5-10年'])
  assert.deepEqual(byLabel.get('学历要求'), ['本科', '硕士', '博士'])
  assert.deepEqual(byLabel.get('薪资待遇[单选]'), ['10-20K'])
  // 点击序列：开面板（筛选按钮）+ 收起（再点筛选）
  assert.equal(r.clicks.length, 2)
  assert.deepEqual(r.clicks[0], { x: 1535, y: 45 })
  assert.deepEqual(r.clicks[1], { x: 1535, y: 45 })
})

test('probeOptions：面板行标签在但行内无选项（无可读行）→ fail-loud', async () => {
  const noOptions = buildSnap([
    { text: '筛选', bounds: [1514, 33.5, 42, 23] },
    { text: '经验要求', bounds: [600, 629, 75, 22] },
  ])
  const r = recorder()
  const setter = new FilterSetter({
    snapshot: snapshotQueue([closedSnap, noOptions, noOptions]),
    click: r.click,
    sleep: r.sleep,
  })
  await assert.rejects(setter.probeOptions(), /未解析到任何可选档位/)
})
