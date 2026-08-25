/**
 * 数字员工「新会话空态」配置：按 subagent_type 映射一段能力摘要文字 + 2 个快捷操作按钮。
 *
 * 展示位置：MessageList 空态（无消息时）。摘要优先取本配置，未配置的智能体
 * （如主智能体 main 或新增智能体）由 ChatContainer 用其 description 兜底。
 * 快捷按钮点击等价于用户手动发送 message，与 ChatInput 顶部常驻 chips（quickPrompts.ts）
 * 走同一条发送链路，但语义独立：前者是空态引导，后者是常驻操作。
 */
import type { QuickPrompt } from './quickPrompts'

export interface SubagentGreeting {
  /** 能力摘要文字（展示在空态中） */
  summary: string
  /** 快捷操作按钮（点击即发送） */
  prompts: QuickPrompt[]
}

const BY_SUBAGENT: Record<string, SubagentGreeting> = {
  'recruiting-operator': {
    summary: '在您本机已登录的 BOSS 直聘上执行招聘操作，支持筛选简历、打招呼、接收简历、标记不合适等。',
    prompts: [
      { label: '帮我筛选简历', message: '帮我筛选简历' },
      { label: '帮我整理招聘任务', message: '帮我整理今天的招聘任务' },
    ],
  },
  'video-agent': {
    summary: '会话式视频创作，支持脚本精修与敏捷生成双模式，并可沉淀到企业素材库、视频库与提示词库。',
    prompts: [
      { label: '帮我写视频脚本', message: '帮我写一条产品宣传视频脚本' },
      { label: '生成一段短视频', message: '我想生成一段短视频' },
    ],
  },
  'trade-specialist': {
    summary: '帮助您快速获取海外潜在客户资源，支持线索匹配、邮件营销与产品推荐。',
    prompts: [
      { label: '匹配海外客户', message: '帮我匹配海外潜在客户' },
      { label: '写一封开发信', message: '帮我写一封外贸开发信' },
    ],
  },
  'travel-consultant': {
    summary: '提供行程建议、费用咨询与报价服务，覆盖国内与出境旅游咨询。',
    prompts: [
      { label: '规划三日游行程', message: '帮我规划一次三日游行程' },
      { label: '查询路线报价', message: '帮我查询某条路线的报价' },
    ],
  },
  'after-sales': {
    summary: '处理订单查询、退换货、商品使用问题等售后服务，支持工单管理。',
    prompts: [
      { label: '查询订单物流', message: '帮我查一下订单物流' },
      { label: '处理退换货', message: '客户想退货怎么处理' },
    ],
  },
  'competitor-research': {
    summary: '基于深度搜索研究竞品，生成可预览的 HTML 竞品分析报告。',
    prompts: [
      { label: '调研一个竞品', message: '帮我调研一个竞品' },
      { label: '生成竞品分析报告', message: '生成一份竞品分析报告' },
    ],
  },
  'complaint-handling': {
    summary: '专业受理客户投诉，智能分类、快速响应安抚、推荐解决方案并升级预警。',
    prompts: [
      { label: '处理一条投诉', message: '帮我处理一条客户投诉' },
      { label: '分析高风险投诉', message: '分析最近的高风险投诉' },
    ],
  },
  'contract-archive-review': {
    summary: '自动完成合同 PDF 解析、基础校验（合同类型、审批单、骑缝章、双方盖章）、EAS 数据比对与审批全流程。',
    prompts: [
      { label: '审核一份归档合同', message: '帮我审核一份归档合同' },
      { label: '校验合同盖章', message: '校验合同的骑缝章与双方盖章' },
    ],
  },
  'customer-followup': {
    summary: '智能线索分配与跟进管理，支持线索导入、智能分配、自动提醒、跟进分析与转化漏斗。',
    prompts: [
      { label: '导入并分配线索', message: '帮我导入一批线索并分配' },
      { label: '分析跟进转化', message: '分析一下最近的跟进转化情况' },
    ],
  },
  'order-processing': {
    summary: '全流程订单管理，支持订单创建、审批、发货、退货退款等完整生命周期，可对接外部 ERP/OMS 系统。',
    prompts: [
      { label: '创建一个新订单', message: '帮我创建一个新订单' },
      { label: '查询订单物流', message: '查询某个订单的物流状态' },
    ],
  },
  'pre-sales': {
    summary: '面向 C 端客户的售前咨询：产品讲解、需求挖掘、价格/优惠咨询、促成留资。',
    prompts: [
      { label: '解答产品咨询', message: '帮我解答客户的产品咨询' },
      { label: '挖掘需求并留资', message: '帮我挖掘客户需求并促成留资' },
    ],
  },
  'social-media-operations': {
    summary: '面向微信公众号/视频号的社媒运营，支持内容日历、发布计划、内容母版管理、审核交接与运营复盘。',
    prompts: [
      { label: '排下周内容计划', message: '帮我排一条下周的内容计划' },
      { label: '复盘本周运营', message: '帮我复盘本周的运营数据' },
    ],
  },
}

/** 通用兜底（主智能体 main / 未配置的智能体） */
export const DEFAULT_GREETING: SubagentGreeting = {
  summary: '输入您的问题或任务，AI助手将为您处理。可以上传文件进行智能分析。',
  prompts: [
    { label: '帮我写一份周报', message: '帮我写一份周报' },
    { label: '分析上传的文件', message: '分析上传的文件' },
  ],
}

/** 当前子智能体的空态配置；无配置返回 null（由调用方用 description / DEFAULT 兜底） */
export function greetingForSubagent(subagentType: string | null | undefined): SubagentGreeting | null {
  if (!subagentType) return null
  return BY_SUBAGENT[subagentType] ?? null
}
