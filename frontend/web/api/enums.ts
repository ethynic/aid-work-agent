/**
 * SaaS 领域枚举值定义
 *
 * 与后端 src/saas/models/enums.py 保持同步。
 * 修改枚举值时需要同时修改两处。
 */

/** 租户状态 */
export enum TenantStatus {
  ACTIVE = 'active',       // 正常
  SUSPENDED = 'suspended',   // 停用
  DEACTIVATED = 'deactivated', // 已删除
}

/** 租户状态工具函数 */
export const TenantStatusMap = {
  [TenantStatus.ACTIVE]: { label: '正常', color: 'green' },
  [TenantStatus.SUSPENDED]: { label: '停用', color: 'red' },
  [TenantStatus.DEACTIVATED]: { label: '已删除', color: 'gray' },
} as const;

/** 用户来源 */
export enum UserSource {
  WECOM_KF = 'wecom_kf',
  WECOM_PERSONAL_RPA = 'wecom_personal_rpa',
}

export const UserSourceMap = {
  [UserSource.WECOM_KF]: { label: '企业微信客服', color: 'blue' },
  [UserSource.WECOM_PERSONAL_RPA]: { label: '微信RPA', color: 'green' },
} as const;

/**
 * 获取用户来源显示信息（NULL 显示为"内部用户"）
 */
export function getUserSourceInfo(source: string | null | undefined): { label: string; color: string } {
  if (!source) return { label: '内部用户', color: 'gray' };
  return (UserSourceMap as Record<string, { label: string; color: string }>)[source] ?? { label: source, color: 'gray' };
}

/**
 * 根据状态值获取显示标签
 * @param status 状态值（数字或字符串）
 * @param statusMap 状态映射表
 */
export function getStatusLabel<T extends string | number>(
  status: T,
  statusMap: Record<string | number, { label: string }>
): string {
  return statusMap[status]?.label ?? '未知';
}

// ============================================================================
// 企业微信个人账号 RPA 状态枚举
//
// 注意：后端这些状态值目前是 src/channels/wecom_personal_rpa/db.py 中的字符串字面量，
// 尚未 formalize 进 src/saas/models/enums.py。此处为前端展示映射，后端 db.py 为唯一真源。
// ============================================================================

/** RPA 客户端状态（wecom_rpa_clients.status） */
export enum WecomRpaClientStatus {
  ACTIVE = 'active',       // 正常
  DISABLED = 'disabled',   // 已停用
}

export const WecomRpaClientStatusMap = {
  [WecomRpaClientStatus.ACTIVE]: { label: '正常', color: 'green' },
  [WecomRpaClientStatus.DISABLED]: { label: '已停用', color: 'gray' },
} as const;

/** RPA 账号状态（wecom_rpa_accounts.status） */
export enum WecomRpaAccountStatus {
  ONLINE = 'online',         // 在线
  OFFLINE = 'offline',       // 离线
  NEED_LOGIN = 'need_login', // 待登录
  PAUSED = 'paused',         // 已暂停
}

export const WecomRpaAccountStatusMap = {
  [WecomRpaAccountStatus.ONLINE]: { label: '在线', color: 'green' },
  [WecomRpaAccountStatus.OFFLINE]: { label: '离线', color: 'gray' },
  [WecomRpaAccountStatus.NEED_LOGIN]: { label: '待登录', color: 'orange' },
  [WecomRpaAccountStatus.PAUSED]: { label: '已暂停', color: 'yellow' },
} as const;

/** RPA 会话绑定状态（wecom_rpa_bindings.status） */
export enum WecomRpaBindingStatus {
  PENDING = 'pending',           // 待确认
  ACTIVE = 'active',             // 正常
  PAUSED = 'paused',             // 已暂停
  INVALID = 'invalid',           // 已失效
  NEEDS_REVIEW = 'needs_review', // 待复核
}

export const WecomRpaBindingStatusMap = {
  [WecomRpaBindingStatus.PENDING]: { label: '待确认', color: 'orange' },
  [WecomRpaBindingStatus.ACTIVE]: { label: '正常', color: 'green' },
  [WecomRpaBindingStatus.PAUSED]: { label: '已暂停', color: 'yellow' },
  [WecomRpaBindingStatus.INVALID]: { label: '已失效', color: 'gray' },
  [WecomRpaBindingStatus.NEEDS_REVIEW]: { label: '待复核', color: 'red' },
} as const;

/** chat_records.source_type 枚举（与后端 ChatRecordSourceType 同步） */
export enum ChatRecordSourceType {
  CHAT = 'chat',
  WECOM = 'wecom',
  WECOM_KF = 'wecom_kf',
  WECOM_PERSONAL_RPA = 'wecom_personal_rpa',
  DINGTALK = 'dingtalk',
  FEISHU = 'feishu',
  REPORT_PERSONAL = 'report_personal',
  REPORT_TEAM = 'report_team',
  REPORT_PERSONAL_WEEKLY = 'report_personal_weekly',
  REPORT_TEAM_WEEKLY = 'report_team_weekly',
  REPORT_PERSONAL_MONTHLY = 'report_personal_monthly',
  REPORT_TEAM_MONTHLY = 'report_team_monthly',
}

export const ChatRecordSourceTypeMap: Record<string, { label: string; color: string }> = {
  [ChatRecordSourceType.CHAT]: { label: 'Web 对话', color: 'blue' },
  [ChatRecordSourceType.WECOM]: { label: '企业微信', color: 'green' },
  [ChatRecordSourceType.WECOM_KF]: { label: '企微客服', color: 'green' },
  [ChatRecordSourceType.WECOM_PERSONAL_RPA]: { label: '企微个人号', color: 'green' },
  [ChatRecordSourceType.DINGTALK]: { label: '钉钉', color: 'blue' },
  [ChatRecordSourceType.FEISHU]: { label: '飞书', color: 'blue' },
  [ChatRecordSourceType.REPORT_PERSONAL]: { label: '个人日报', color: 'orange' },
  [ChatRecordSourceType.REPORT_TEAM]: { label: '团队日报', color: 'orange' },
  [ChatRecordSourceType.REPORT_PERSONAL_WEEKLY]: { label: '个人周报', color: 'orange' },
  [ChatRecordSourceType.REPORT_TEAM_WEEKLY]: { label: '团队周报', color: 'orange' },
  [ChatRecordSourceType.REPORT_PERSONAL_MONTHLY]: { label: '个人月报', color: 'orange' },
  [ChatRecordSourceType.REPORT_TEAM_MONTHLY]: { label: '团队月报', color: 'orange' },
};

/** 判断 source_type 是否为报告类 */
export function isReportSourceType(source: string | null | undefined): boolean {
  if (!source) return false;
  return source.startsWith('report_');
}

/** 获取 source_type 显示信息 */
export function getChatRecordSourceTypeInfo(source: string | null | undefined): { label: string; color: string } {
  if (!source) return { label: '未知', color: 'gray' };
  return ChatRecordSourceTypeMap[source] ?? { label: source, color: 'gray' };
}

/**
 * BaseBadge intent 与 statusMap color 的映射。
 * BaseBadge intent: primary | success | warning | danger | info | neutral
 */
export function colorToBadgeIntent(color: string): 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'neutral' {
  switch (color) {
    case 'green': return 'success'
    case 'red': return 'danger'
    case 'orange':
    case 'yellow': return 'warning'
    case 'blue': return 'info'
    case 'gray': return 'neutral'
    default: return 'neutral'
  }
}
