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
  RUNNING = 'running',
  STOPPED = 'stopped',
  ERROR = 'error',
}

/** 智能体实例状态工具函数 */
export const AgentInstanceStatusMap = {
  [AgentInstanceStatus.RUNNING]: { label: '运行中', color: 'green' },
  [AgentInstanceStatus.STOPPED]: { label: '已停止', color: 'gray' },
  [AgentInstanceStatus.ERROR]: { label: '异常', color: 'red' },
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
