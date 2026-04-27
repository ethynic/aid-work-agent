import { getAuthHeader } from './auth'

/** 远程连接凭据管理 API */

export interface RemoteCredential {
  credential_id: string
  connection_type: 'smb' | 'ftp'
  server_host: string
  server_port: number
  username: string
  remote_path: string
  domain?: string
  name?: string
  description?: string
  status: number
  created_at: string
  updated_at: string
}

export interface CreateCredentialRequest {
  connection_type: 'smb' | 'ftp'
  server_host: string
  server_port: number
  username: string
  password: string
  remote_path: string
  name?: string
  domain?: string
  description?: string
}

export interface UpdateCredentialRequest {
  connection_type?: 'smb' | 'ftp'
  server_host?: string
  server_port?: number
  username?: string
  password?: string
  remote_path?: string
  name?: string
  domain?: string
  description?: string
  status?: number
}

const apiBase = import.meta.env.VITE_API_BASE_URL || '/api'

/**
 * 获取所有凭据
 */
export async function listCredentials(connectionType?: string): Promise<{
  success: boolean
  data: RemoteCredential[]
  count: number
}> {
  const params = new URLSearchParams()
  if (connectionType) {
    params.append('connection_type', connectionType)
  }
  const query = params.toString()
  const response = await fetch(`${apiBase}/credentials${query ? '?' + query : ''}`, {
    headers: { ...getAuthHeader() },
  })
  return await response.json()
}

/**
 * 获取单个凭据详情（包含密码）
 */
export async function getCredential(credentialId: string): Promise<{
  success: boolean
  data: RemoteCredential & { password: string }
}> {
  const response = await fetch(`${apiBase}/credentials/${credentialId}`, {
    headers: { ...getAuthHeader() },
  })
  return await response.json()
}

/**
 * 创建凭据
 */
export async function createCredential(request: CreateCredentialRequest): Promise<{
  success: boolean
  data: { credential_id: string }
  message?: string
  error?: string
  debug?: string
}> {
  const response = await fetch(`${apiBase}/credentials`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader(),
    },
    body: JSON.stringify(request),
  })
  return await response.json()
}

/**
 * 更新凭据
 */
export async function updateCredential(
  credentialId: string,
  request: UpdateCredentialRequest
): Promise<{
  success: boolean
  message?: string
  error?: string
  debug?: string
}> {
  const response = await fetch(`${apiBase}/credentials/${credentialId}`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader(),
    },
    body: JSON.stringify(request),
  })
  return await response.json()
}

/**
 * 删除凭据
 */
export async function deleteCredential(credentialId: string): Promise<{
  success: boolean
  message?: string
  error?: string
  debug?: string
}> {
  const response = await fetch(`${apiBase}/credentials/${credentialId}`, {
    method: 'DELETE',
    headers: { ...getAuthHeader() },
  })
  return await response.json()
}
