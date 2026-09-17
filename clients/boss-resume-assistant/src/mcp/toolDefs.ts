/**
 * 18 个 MCP tool 的契约定义（实施规格 m02 §6 / 标准 §5 / 设计 §10.8 / §10.9；boss_open_chat
 * 2026-08-27；boss_overlay_inspect/dismiss 弹层自愈原语 2026-08-31）。
 *
 * zodShape 是 registerTool 的输入；manifest digest 用同一来源推导的 JSON Schema，
 * 保证「Host 看到的 schema」与「manifest digest 的 schema」同源（SDK 1.30.0 内部同样
 * 用 zod v4 toJSONSchema 生成 list_tools 的 inputSchema）。
 *
 * 写动作硬上限在 schema 层收紧（设计 §14）：greet 单次最大 3、accept 最大 1、reject 固定 1；
 * resume_batch 因单份约 30 秒滚动截图也收紧到 3；CLI/operation 层的上限（10）不变。
 */
import { z } from 'zod'

export interface BossToolDef {
  name: string
  /** 中文标题 */
  title: string
  description: string
  /** registerTool 的 zod raw shape */
  zodShape: Record<string, z.ZodTypeAny>
  annotations: {
    title: string
    readOnlyHint: boolean
    destructiveHint: boolean
    idempotentHint: boolean
    openWorldHint: boolean
  }
}

export const TOOL_DEFS: BossToolDef[] = [
  {
    name: 'boss_filter',
    title: '设置筛选条件',
    description:
      '在 BOSS 直聘「推荐牛人」页设置筛选面板：经验要求/学历要求/薪资待遇（替换语义，先清除残留再选）。' +
      '写动作：会改动页面上的筛选状态。至少提供一个条件。' +
      '数值档位（经验/薪资）自动保底映射：传了页面不存在的档位时按「保下限」规则映射到最接近的真实档位' +
      '（如要 15-30K 而页面只有 10-20K/20-50K 会选 20-50K），结果 substitutions 注明实际档位，务必向用户转述；' +
      '建议先用 boss_filter_options 查实际档位选更准；学历等非数值选项必须精确，传错报错并列出该行全部可选档位。',
    zodShape: {
      experience: z.string().min(1).optional().describe('经验要求行选项，如 "5-10年"'),
      educations: z.array(z.string().min(1)).optional().describe('学历要求行选项（多选），如 ["本科","硕士"]'),
      salary: z.string().min(1).optional().describe('薪资待遇行选项（单选），如 "10-20K"'),
    },
    annotations: { title: '设置筛选条件', readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  {
    name: 'boss_clear_filter',
    title: '清空筛选条件',
    description: '清空 BOSS 直聘「推荐牛人」页的全部筛选条件（开面板 → 清除 → 确定，徽章计数归零校验）。写动作。',
    zodShape: {},
    annotations: { title: '清空筛选条件', readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  {
    name: 'boss_goto',
    title: '跳转页面',
    description:
      '点击 BOSS 直聘左侧导航菜单跳转页面：recommend=推荐牛人，chat=沟通（看打招呼回复/附件简历请求）。' +
      '已在目标页时自动跳过（幂等）。无外部写副作用。',
    zodShape: {
      target: z.enum(['recommend', 'chat']).describe('目标页面：recommend=推荐牛人，chat=沟通'),
    },
    annotations: { title: '跳转页面', readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  {
    name: 'boss_greet',
    title: '打招呼',
    description:
      '在 BOSS 直聘「推荐牛人」页逐个点击「打招呼」向候选人发起沟通（外部写动作，单次最大 3 人）。' +
      '当前屏点完自动向下滚动，到底或达到 limit 结束；触发付费墙（职位无开聊权益）会立即停止并返回 PAYWALL。' +
      '定向模式：传 names 姓名清单时先配对卡片姓名再点击，只向姓名精确匹配（trim 相等）的候选人打招呼，' +
      '配对失败的卡片一律跳过（宁可不打，不能打错）；结果返回 greeted_names（实际打过的人）与' +
      ' missing_names（滚到底也没找到的人），汇报时必须以此为准、绝不声称给未打的人打过招呼。' +
      '前置要求：当前在推荐牛人列表页，否则返回 WRONG_PAGE。',
    zodShape: {
      limit: z
        .number()
        .int()
        .min(1)
        .max(3)
        .default(1)
        .describe('打招呼人数上限：默认 1，单次最大 3；定向模式（传 names）自动取 max(limit, names 数量)，默认 1 不会截断名单'),
      names: z
        .array(z.string().min(1))
        .min(1)
        .max(3)
        .optional()
        .describe(
          '定向打招呼：候选人姓名清单（1-3 个，精确匹配卡片上的姓名）。' +
            '推荐/筛选后向指定候选人打招呼必须传（列表顺序与名单顺序不保证一致，不传会打错人）；' +
            '姓名配对失败的卡片一律跳过，打给谁以返回的 greeted_names 为准',
        ),
    },
    annotations: { title: '打招呼', readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
  },
  {
    name: 'boss_accept_resume',
    title: '同意接收附件简历',
    description:
      '在 BOSS 直聘「沟通」页逐个打开「对方想发送附件简历」的会话并点「同意」接收简历（外部写动作，单次固定 1 人）。' +
      '同意后默认点开附件预览再关闭（preview 可关）。不在沟通页时自动先跳转。',
    zodShape: {
      limit: z.number().int().min(1).max(1).default(1).describe('同意人数：固定 1（产品硬上限）'),
      preview: z.boolean().default(true).describe('同意后点开附件简历预览再关闭（默认开启）'),
    },
    annotations: { title: '同意接收附件简历', readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
  },
  {
    name: 'boss_reject_current',
    title: '标记当前候选人不合适',
    description:
      '把 BOSS 直聘「沟通」页当前会话的候选人标记为「不合适」（外部写动作，每次固定 1 人）。' +
      '弹确认层自动点确定；若弹出原因选择层会停止并提示人工处理（绝不自动乱选原因）。',
    zodShape: {},
    annotations: { title: '标记当前候选人不合适', readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
  },
  {
    name: 'boss_interview_demo',
    title: '约面试表单填充演示',
    description:
      '演示填充 BOSS 直聘「沟通」页当前会话的约面试表单：逐字填备注 + 面试时间选明天，然后点「取消」关闭。' +
      '⚠️ 只填不发送（sent 恒为 false），无外部写副作用。',
    zodShape: {
      remark: z.string().min(1).max(140).optional().describe('备注内容（缺省用默认文案，表单上限 140 字）'),
    },
    annotations: { title: '约面试表单填充演示', readOnlyHint: true, destructiveHint: false, idempotentHint: false, openWorldHint: false },
  },
  {
    name: 'boss_send_to',
    title: '打开联系人会话并发送消息',
    description:
      '在 BOSS 直聘「沟通」页打开指定联系人的会话后逐字输入消息并发送（外部写动作）。' +
      '打开会话走统一切换链路（与 boss_open_chat 同源）：已在目标会话零点击（via=already）/' +
      '搜索找人（via=search）/ 会话列表兜底（via=list），带头部身份校验防止串错会话。' +
      '默认真发送；dry_run=true 时只输入不点发送（测试链路）。' +
      '前置要求：当前在沟通页（不在时自动跳转）；联系人存在且可达，否则报错。',
    zodShape: {
      to: z.string().min(1).describe('联系人姓名（精确，与会话列表/头部姓名 trim 全等）'),
      message: z.string().min(1).describe('要发送的消息内容'),
      dry_run: z.boolean().default(false).describe('只输入不点发送（测试链路，默认 false 真发送）'),
    },
    annotations: { title: '打开联系人会话并发送消息', readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
  },
  {
    name: 'boss_send_current',
    title: '向当前会话发送消息',
    description:
      '在 BOSS 直聘「沟通」页向当前已选中的会话逐字输入消息并发送（外部写动作）。' +
      '默认真发送；dry_run=true 时只输入不点发送（测试链路）。' +
      '前置要求：当前在沟通页且已选中一个会话（右侧面板有发送按钮），否则返回 WRONG_PAGE（请先选会话）。',
    zodShape: {
      message: z.string().min(1).describe('要发送的消息内容'),
      dry_run: z.boolean().default(false).describe('只输入不点发送（测试链路，默认 false 真发送）'),
    },
    annotations: { title: '向当前会话发送消息', readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
  },
  {
    name: 'boss_read_chat',
    title: '读取会话消息与未读清单',
    description:
      '读取 BOSS 直聘「沟通」页当前会话的消息流（谁发了什么：me/them/system + 正文 + 时间 + 我方消息已读状态）' +
      '与左侧列表全部未读会话清单（姓名/未读条数/时间/最后一条预览，含视口外全部）及左导航「沟通」总未读徽章。' +
      '纯只读：单次快照，不点击、不输入、不切换会话。可选 contact 校验当前打开的会话是否为该联系人（trim 全等），' +
      '不匹配时报错（会话存在但未打开请先切换；不存在则返回可用联系人名单）——本工具绝不自动切换会话。' +
      '前置要求：当前已在沟通页（不在时返回 WRONG_PAGE，请先 boss_goto chat，不自动跳转）且已打开一个会话。',
    zodShape: {
      contact: z.string().min(1).max(30).optional().describe(
        '可选：联系人姓名。校验当前打开的会话是否为该联系人；不匹配时报错并列出可用联系人，绝不自动切换会话',
      ),
    },
    annotations: { title: '读取会话消息与未读清单', readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  {
    name: 'boss_open_chat',
    title: '打开指定联系人的会话',
    description:
      '在 BOSS 直聘「沟通」页切换到指定联系人的会话（不发任何消息）：已在目标会话时零点击返回（via=already）；' +
      '否则优先搜索找人进入对话，搜索失败回退点击左侧会话列表项（视口外自动滚动）。' +
      '返回 via=already/search/list 告知实际路径；打开后用 boss_read_chat 读取消息。' +
      '联系人不存在/同名多命中/切换未生效会报错（附可用联系人名单），绝不盲点。' +
      '前置要求：当前已在沟通页（不在时返回 WRONG_PAGE，请先 boss_goto chat，不自动跳转）。' +
      '无外部写副作用（只切换显示的会话），但搜索/列表点击与姓名输入借用真实鼠标约 3-10 秒，期间勿动鼠标。',
    zodShape: {
      contact: z.string().min(1).max(30).describe('联系人姓名（精确，与头部/会话列表姓名 trim 全等）'),
    },
    annotations: { title: '打开指定联系人的会话', readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  {
    name: 'boss_overlay_inspect',
    title: '导出弹层识别候选清单',
    description:
      '采集当前页面主文档全部文本节点（text + 坐标 + class），供上层判断是否存在遮挡弹层' +
      '（广告/功能引导弹窗）并定位关闭控件。纯只读单次快照，不点击、不输入、不滚屏，无页面前置。' +
      '弹层盖顶导致其它工具失败（UI_CHANGED/BUSY）时，先调本工具导出候选再决定关闭动作。',
    zodShape: {},
    annotations: { title: '导出弹层识别候选清单', readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  {
    name: 'boss_overlay_dismiss',
    title: '关闭页面弹层',
    description:
      '点击关闭当前页面最上层的弹窗/引导弹层：按传入的关闭控件文本定位并真实鼠标点击，' +
      '点击后校验弹层已消失。⚠️ 仅接受关闭语义白名单文案（关闭/知道了/我知道了/以后再说/下次再说/' +
      '暂不/取消/跳过/不再提醒/残忍拒绝/稍后再说/× 等），非白名单文本直接拒绝——' +
      '绝不点击「领取/立即打开/开通」类按钮，不产生任何业务副作用。',
    zodShape: {
      text: z.string().min(1).max(20).describe('关闭控件的精确文本（必须在关闭语义白名单内，与页面文本 trim 全等）'),
    },
    annotations: { title: '关闭页面弹层', readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  {
    name: 'boss_list_jobs',
    title: '列出当前招聘者所有职位',
    description:
      '在 BOSS 直聘「推荐牛人」页打开职位下拉，解析并返回当前招聘者的全部职位列表（职位名/城市/薪资/点击坐标/是否待开放）。' +
      '只读：不改变任何职位状态（但会借用真实鼠标点开下拉，操作期间勿动鼠标）。' +
      '每个职位标注 pending（待开放/未发布，项右侧有「待」徽章）——切到待开放职位会导致页面异常，select-job 会拒绝这类职位。' +
      '用于在 select-job 前确认精确职位名（用户口述的职位名可能不精确）。前置要求：当前在推荐牛人页，否则返回 WRONG_PAGE。',
    zodShape: {},
    annotations: { title: '列出当前招聘者所有职位', readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  {
    name: 'boss_select_job',
    title: '切换当前招聘职位',
    description:
      '在 BOSS 直聘「推荐牛人」页切换当前招聘职位到指定职位名（精确匹配，外部写动作）。' +
      'job_name 必须是 list-jobs 返回的精确职位名（CLI 内部不做模糊匹配，匹配 0 或多个都报错）。' +
      '若目标职位待开放（pending=true），点击前直接拒绝（切到未发布职位会致页面异常），请改选已开放职位。' +
      '前置要求：当前在推荐牛人页，否则返回 WRONG_PAGE。切换后会校验职位框文本已变更，未生效则报错。',
    zodShape: {
      job_name: z.string().min(1).describe('目标职位名（精确，用 list-jobs 查看，如 "PHP开发工程师"）'),
    },
    annotations: { title: '切换当前招聘职位', readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  {
    name: 'boss_filter_options',
    title: '查询筛选可选档位',
    description:
      '只读探查 BOSS 直聘「推荐牛人」页筛选面板的全部可选档位（经验要求/学历要求/薪资待遇各行选项），' +
      '读完后自动收起面板还原页面。用于把用户口语化的筛选要求（如 15k-20k、5年以上、本科及以上）' +
      '映射成页面实际存在的精确档位，再调 boss_filter。前置要求：当前在推荐牛人页，否则返回 WRONG_PAGE。' +
      '开/收面板借用真实鼠标约 2 秒，期间勿动鼠标。',
    zodShape: {},
    annotations: { title: '查询筛选可选档位', readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  {
    name: 'boss_resume_detail',
    title: '读取候选人简历详情入库',
    description:
      '读取 BOSS 直聘当前打开的候选人在线简历详情（推荐牛人页或沟通页均可，前提已点开候选人详情，否则 WRONG_PAGE）。' +
      '简历是 canvas 像素渲染（DOM 抓不到文字），通过「滚动分段截图 → 重叠拼接」采集拼接长图，' +
      '文本由云端多模态模型识别（2026-09-17 去 OCR 化，客户端零本地 OCR 依赖）。' +
      '结果按简历库契约返回：candidate_name（必传，缺省直接报错）、' +
      'job_name（推荐页当前招聘职位）、拼接长图 base64（云端识别后入库，图片绝不进对话上下文）。' +
      '姓名来源=非截图识别（显式入参）：云端识别后会做姓名交叉校验，未在识别文本头部命中会报错（疑似打开的不是该候选人的简历）。' +
      '只读：无外部写副作用；但滚动借用真实鼠标约 1-2 秒，操作期间勿动鼠标、勿遮挡 Chrome 窗口。' +
      '可选 save_image_to 保存拼接长图（PNG）。',
    zodShape: {
      candidate_name: z.string().min(1).max(30).optional().describe(
        '必传：智能体会话上下文已知的候选人姓名；未传直接报错。姓名来源=非截图识别（显式入参+云端交叉校验）',
      ),
      save_image_to: z.string().min(1).optional().describe('可选：拼接长图保存路径（PNG）；缺省不保留图片'),
    },
    annotations: { title: '读取候选人简历详情入库', readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  {
    name: 'boss_resume_batch',
    title: '批量读取牛人简历入库',
    description:
      '在 BOSS 直聘「推荐牛人」页逐个点开当前视口的牛人卡片 → 读取在线简历（滚动分段截图拼接，文本由云端多模态模型识别）→ 自动关闭 → 下一份。' +
      '结果 resumes 数组按简历库契约返回（姓名=卡片 DOM 配对，唯一来源；' +
      '配对失败的记 failures 跳过，绝不错名入库；job_name 取当前招聘职位；' +
      '含拼接长图 base64，云端识别文本后逐份存入简历库，只返回紧凑摘要）。' +
      '单份失败（打开超时/读取失败/姓名无法确定/云端姓名交叉校验不过）记入 failures 后继续下一份。' +
      '前置要求：当前在推荐牛人列表页，否则返回 WRONG_PAGE。' +
      '只读：无外部写副作用；但每份简历滚动借用真实鼠标约 30 秒，操作期间勿动鼠标、勿遮挡 Chrome 窗口。',
    zodShape: {
      limit: z.number().int().min(1).max(3).default(1).describe('读取份数上限：默认 1，单次最大 3（每份约 30 秒滚动截图）'),
      save_dir: z.string().min(1).optional().describe('可选：拼接长图保存目录（每份存 <姓名>.png）；缺省不保留图片'),
    },
    annotations: { title: '批量读取牛人简历入库', readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
]

export const TOOL_NAMES = TOOL_DEFS.map((t) => t.name)
