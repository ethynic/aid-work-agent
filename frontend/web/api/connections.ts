/**
 * 连接中心 API Client
 *
 * Phase 1：仅占位 stubs，Tab 1/2 复用现有 saasPermissions.ts 中的环境变量与配置文件 API。
 * Phase 2 将填充内置连接器相关接口（listBuiltinConnectors / saveConfig / testConnection 等）。
 *
 * 关联设计：docs/system/connection-center-design.md
 * 关联计划：docs/plans/plan-connection-center.md
 */

// ==================== Phase 2 待填充 stubs ====================
// 以下函数为占位，Phase 2 实现内置连接器时填充具体接口。

export interface BuiltinConnector {
  connector_id: string
  display_name: string
  category: string
  config_schema: Record<string, any>
  sensitive_keys: string[]
}

export interface ConnectorConfig {
  connector_id: string
  connector_type: 'builtin' | 'custom_mcp' | 'custom_openapi'
  name: string
  config_json: Record<string, any> | null
  // 掩码后的敏感字段（如 "sk-***...***ab12"），永不返回明文
  masked_secret?: string
  status: 'untested' | 'active' | 'error' | 'disabled'
  last_tested_at: string | null
  last_test_error: string | null
  scope_subagents: string[]
}

export async function listBuiltinConnectors(): Promise<{ success: boolean; data?: BuiltinConnector[] }> {
  // Phase 2 实现
  return { success: true, data: [] }
}

export async function listConfigs(): Promise<{ success: boolean; data?: ConnectorConfig[] }> {
  // Phase 2 实现
  return { success: true, data: [] }
}

export async function getConfig(connectorId: string): Promise<{ success: boolean; data?: ConnectorConfig }> {
  // Phase 2 实现
  void connectorId
  return { success: false }
}

export async function saveConfig(
  connectorId: string,
  payload: Record<string, any>,
): Promise<{ success: boolean; message?: string }> {
  // Phase 2 实现
  void connectorId
  void payload
  return { success: false, message: '尚未实现' }
}

export async function deleteConfig(connectorId: string): Promise<{ success: boolean; message?: string }> {
  // Phase 2 实现
  void connectorId
  return { success: false, message: '尚未实现' }
}

export async function testConnection(connectorId: string): Promise<{
  success: boolean
  message?: string
  latency_ms?: number
}> {
  // Phase 2 实现
  void connectorId
  return { success: false, message: '尚未实现' }
}
