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

/** 租户类型（real=真实租户，test=测试/演示租户） */
export enum TenantType {
  REAL = 'real',
  TEST = 'test',
}

export const TenantTypeMap = {
  [TenantType.REAL]: { label: '真实租户', color: 'green' },
  [TenantType.TEST]: { label: '测试租户', color: 'gray' },
} as const;

/** 用户来源 */
export enum UserSource {
  WECOM_KF = 'wecom_kf',
}

export const UserSourceMap = {
  [UserSource.WECOM_KF]: { label: '企微客服', color: 'blue' },
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

/** chat_records.source_type 枚举（与后端 ChatRecordSourceType 同步） */
export enum ChatRecordSourceType {
  CHAT = 'chat',
  WECOM = 'wecom',
  WECOM_KF = 'wecom_kf',
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

// ============================================================================
// 用户行为审计日志枚举（与后端 src/saas/models/enums.py 同步）
//
// Phase 1 仅定义（登录/登出/改密/渠道绑定等事件已在后端入库），
// 行为日志查询页面为 Phase 4，届时在页面中消费这些枚举。
// 详见 docs/system/user-behavior-audit-log-design.md §4
// ============================================================================

/** 行为类型（user_behavior_logs.action） */
export enum BehaviorAction {
  LOGIN = 'login',
  LOGIN_FAILED = 'login_failed',
  LOGOUT = 'logout',
  PASSWORD_CHANGE = 'password_change',
  VERIFY_CODE_SENT = 'verify_code_sent',
  PROFILE_UPDATE = 'profile_update',
  CHANNEL_BIND = 'channel_bind',
  CHANNEL_UNBIND = 'channel_unbind',
  CREATE = 'create',
  UPDATE = 'update',
  DELETE = 'delete',
  BATCH_DELETE = 'batch_delete',
  EXPORT = 'export',
}

export const BehaviorActionMap: Record<string, { label: string; color: string }> = {
  [BehaviorAction.LOGIN]: { label: '登录成功', color: 'green' },
  [BehaviorAction.LOGIN_FAILED]: { label: '登录失败', color: 'red' },
  [BehaviorAction.LOGOUT]: { label: '登出', color: 'gray' },
  [BehaviorAction.PASSWORD_CHANGE]: { label: '修改密码', color: 'orange' },
  [BehaviorAction.VERIFY_CODE_SENT]: { label: '发送验证码', color: 'blue' },
  [BehaviorAction.PROFILE_UPDATE]: { label: '修改资料', color: 'blue' },
  [BehaviorAction.CHANNEL_BIND]: { label: '渠道账号绑定', color: 'green' },
  [BehaviorAction.CHANNEL_UNBIND]: { label: '渠道账号解绑', color: 'red' },
  [BehaviorAction.CREATE]: { label: '创建', color: 'green' },
  [BehaviorAction.UPDATE]: { label: '更新', color: 'orange' },
  [BehaviorAction.DELETE]: { label: '删除', color: 'red' },
  [BehaviorAction.BATCH_DELETE]: { label: '批量删除', color: 'red' },
  [BehaviorAction.EXPORT]: { label: '导出', color: 'blue' },
};

/** 请求入口（user_behavior_logs.entry） */
export enum BehaviorEntry {
  WEB = 'web',           // web 前端发起
  API = 'api',           // 脚本/第三方直接调 API
  CHANNEL = 'channel',   // 渠道回调（无用户侧 IP/UA）
}

export const BehaviorEntryMap: Record<string, { label: string; color: string }> = {
  [BehaviorEntry.WEB]: { label: 'Web 前端', color: 'blue' },
  [BehaviorEntry.API]: { label: 'API 直调', color: 'gray' },
  [BehaviorEntry.CHANNEL]: { label: '渠道回调', color: 'orange' },
};

/** 资源类型（user_behavior_logs.resource_type） */
export enum BehaviorResourceType {
  TENANT = 'tenant',
  TENANT_USER = 'tenant_user',
  SUBAGENT = 'subagent',
  PROMPT = 'prompt',
  SESSION = 'session',
  KNOWLEDGE_DOC = 'knowledge_doc',
  CONFIG = 'config',
  BILLING = 'billing',
  ACCOUNT = 'account',
  ACTIVATION_CODE = 'activation_code',
  CLIENT_BINDING = 'client_binding',
  CHANNEL_ACCOUNT = 'channel_account',
  EXTERNAL_CUSTOMER = 'external_customer',
  REPLY_STYLE = 'reply_style',
  SKILL = 'skill',
  ERROR_LOG = 'error_log',
  KNOWLEDGE_SHARE = 'knowledge_share',
  KNOWLEDGE_CATEGORY = 'knowledge_category',
}

export const BehaviorResourceTypeMap: Record<string, { label: string }> = {
  [BehaviorResourceType.TENANT]: { label: '租户' },
  [BehaviorResourceType.TENANT_USER]: { label: '租户用户' },
  [BehaviorResourceType.SUBAGENT]: { label: '数字员工' },
  [BehaviorResourceType.PROMPT]: { label: '提示词' },
  [BehaviorResourceType.SESSION]: { label: '会话' },
  [BehaviorResourceType.KNOWLEDGE_DOC]: { label: '知识库文档' },
  [BehaviorResourceType.CONFIG]: { label: '配置' },
  [BehaviorResourceType.BILLING]: { label: '计费' },
  [BehaviorResourceType.ACCOUNT]: { label: '账号' },
  [BehaviorResourceType.ACTIVATION_CODE]: { label: '激活码' },
  [BehaviorResourceType.CLIENT_BINDING]: { label: '客户端绑定' },
  [BehaviorResourceType.CHANNEL_ACCOUNT]: { label: '客服账号' },
  [BehaviorResourceType.EXTERNAL_CUSTOMER]: { label: '外部客户' },
  [BehaviorResourceType.REPLY_STYLE]: { label: '回复风格' },
  [BehaviorResourceType.SKILL]: { label: '租户技能' },
  [BehaviorResourceType.ERROR_LOG]: { label: '错误日志' },
  [BehaviorResourceType.KNOWLEDGE_SHARE]: { label: '知识库授权' },
  [BehaviorResourceType.KNOWLEDGE_CATEGORY]: { label: '知识库分类' },
};

/** 粗分设备类型（user_behavior_logs.device_type） */
export enum BehaviorDeviceType {
  PC = 'pc',
  MOBILE = 'mobile',
  TABLET = 'tablet',
  UNKNOWN = 'unknown',
}

export const BehaviorDeviceTypeMap: Record<string, { label: string }> = {
  [BehaviorDeviceType.PC]: { label: 'PC' },
  [BehaviorDeviceType.MOBILE]: { label: '移动端' },
  [BehaviorDeviceType.TABLET]: { label: '平板' },
  [BehaviorDeviceType.UNKNOWN]: { label: '未知' },
};

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
