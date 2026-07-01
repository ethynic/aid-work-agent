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

/** 订阅状态 */
export enum SubscriptionStatus {
  ACTIVE = 'active',
  EXPIRED = 'expired',
  CANCELLED = 'cancelled',
}

/** 订阅状态工具函数 */
export const SubscriptionStatusMap = {
  [SubscriptionStatus.ACTIVE]: { label: '活跃', color: 'green' },
  [SubscriptionStatus.EXPIRED]: { label: '已过期', color: 'orange' },
  [SubscriptionStatus.CANCELLED]: { label: '已取消', color: 'gray' },
} as const;

/** 支付状态 */
export enum PaymentStatus {
  PENDING = 'pending',
  PAID = 'paid',
  REFUNDED = 'refunded',
}

/** 支付状态工具函数 */
export const PaymentStatusMap = {
  [PaymentStatus.PENDING]: { label: '待支付', color: 'yellow' },
  [PaymentStatus.PAID]: { label: '已支付', color: 'green' },
  [PaymentStatus.REFUNDED]: { label: '已退款', color: 'blue' },
} as const;

/** 智能体实例状态 */
export enum AgentInstanceStatus {
  IDLE = 'idle',
  BUSY = 'busy',
}

/** 智能体实例状态工具函数 */
export const AgentInstanceStatusMap = {
  [AgentInstanceStatus.IDLE]: { label: '空闲', color: 'green' },
  [AgentInstanceStatus.BUSY]: { label: '忙碌', color: 'orange' },
} as const;

/** 用户状态 */
export enum UserStatus {
  ACTIVE = 'active',       // 正常
  SUSPENDED = 'suspended',   // 停用
  DEACTIVATED = 'deactivated', // 已注销
}

/** 用户状态工具函数 */
export const UserStatusMap = {
  [UserStatus.ACTIVE]: { label: '正常', color: 'green' },
  [UserStatus.SUSPENDED]: { label: '停用', color: 'red' },
  [UserStatus.DEACTIVATED]: { label: '已注销', color: 'gray' },
} as const;

/** 用户角色 */
export enum UserRole {
  PLATFORM_ADMIN = 'platform_admin',
  TENANT_ADMIN = 'tenant_admin',
  TENANT_USER = 'tenant_user',
}

/** 用户角色工具函数 */
export const UserRoleMap = {
  [UserRole.PLATFORM_ADMIN]: { label: '平台管理员' },
  [UserRole.TENANT_ADMIN]: { label: '租户管理员' },
  [UserRole.TENANT_USER]: { label: '用户' },
} as const;

/** 套餐类型 */
export enum PlanType {
  BASIC = 'basic',
  STANDARD = 'standard',
  PREMIUM = 'premium',
}

/** 套餐类型工具函数 */
export const PlanTypeMap = {
  [PlanType.BASIC]: { label: '基础版' },
  [PlanType.STANDARD]: { label: '标准版' },
  [PlanType.PREMIUM]: { label: '旗舰版' },
} as const;

/** 排队状态 */
export enum QueueStatus {
  WAITING = 'waiting',
  READY = 'ready',
  EXPIRED = 'expired',
  CANCELLED = 'cancelled',
  ABANDONED = 'abandoned',
}

/** 上下文压缩摘要状态（Phase 7 §7.3） */
export enum ContextSummaryStatus {
  ACTIVE = 'active',
  SUPERSEDED = 'superseded',
  ROLLED_BACK = 'rolled_back',
}

export const ContextSummaryStatusMap = {
  [ContextSummaryStatus.ACTIVE]: { label: '生效中', color: 'green' },
  [ContextSummaryStatus.SUPERSEDED]: { label: '已替代', color: 'gray' },
  [ContextSummaryStatus.ROLLED_BACK]: { label: '已回滚', color: 'orange' },
} as const;

/** 用户来源 */
export enum UserSource {
  WECOM_KF = 'wecom_kf',
}

export const UserSourceMap = {
  [UserSource.WECOM_KF]: { label: '企业微信客服', color: 'blue' },
} as const;

/**
 * 获取用户来源显示信息（NULL 显示为"内部用户"）
 */
export function getUserSourceInfo(source: string | null | undefined): { label: string; color: string } {
  if (!source) return { label: '内部用户', color: 'gray' };
  return (UserSourceMap as Record<string, { label: string; color: string }>)[source] ?? { label: source, color: 'gray' };
}

/** 排队状态工具函数 */
export const QueueStatusMap = {
  [QueueStatus.WAITING]: { label: '排队中', color: 'blue' },
  [QueueStatus.READY]: { label: '已到号', color: 'green' },
  [QueueStatus.EXPIRED]: { label: '过期', color: 'orange' },
  [QueueStatus.CANCELLED]: { label: '已取消', color: 'gray' },
  [QueueStatus.ABANDONED]: { label: '已取消', color: 'gray' }, // 归并为 cancelled 显示
} as const;

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
