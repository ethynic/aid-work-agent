<template>
  <div class="credential-manager-overlay">
    <div class="credential-manager">
      <div class="header">
        <h2>远程连接凭据管理</h2>
        <button @click="$emit('close')" class="btn-close">&times;</button>
      </div>

      <div class="header-actions">
        <button @click="showCreateDialog = true" class="btn-primary">
          + 添加凭据
        </button>
      </div>

      <!-- 凭据列表 -->
      <div v-if="loading" class="loading">
        加载中...
      </div>
      <div v-else-if="credentials.length === 0" class="empty">
        <p>暂无凭据，点击上方按钮添加</p>
      </div>
      <div v-else class="credential-list">
        <div
          v-for="cred in credentials"
          :key="cred.credential_id"
          class="credential-card"
        >
          <div class="credential-header">
            <div class="credential-title">
              <span class="type-badge" :class="cred.connection_type">
                {{ cred.connection_type.toUpperCase() }}
              </span>
              <h3>{{ cred.name || `${cred.connection_type.toUpperCase()}://${cred.server_host}${cred.remote_path}` }}</h3>
            </div>
            <div class="credential-actions">
              <button @click="editCredential(cred)" class="btn-icon" title="编辑">
                ✏️
              </button>
              <button @click="handleDeleteCredential(cred.credential_id)" class="btn-icon btn-danger" title="删除">
                🗑️
              </button>
            </div>
          </div>
          <div class="credential-body">
            <div class="credential-field">
              <label>服务器地址:</label>
              <span>{{ cred.server_host }}:{{ cred.server_port }}</span>
            </div>
            <div class="credential-field">
              <label>用户名:</label>
              <span>{{ cred.username }}</span>
            </div>
            <div class="credential-field">
              <label>远程路径:</label>
              <span>{{ cred.remote_path }}</span>
            </div>
            <div v-if="cred.domain" class="credential-field">
              <label>域:</label>
              <span>{{ cred.domain }}</span>
            </div>
            <div v-if="cred.description" class="credential-field">
              <label>描述:</label>
              <span>{{ cred.description }}</span>
            </div>
            <div class="credential-footer">
              <small>创建于: {{ formatDate(cred.created_at) }}</small>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 创建/编辑对话框 -->
    <div v-if="showCreateDialog || showEditDialog" class="dialog-overlay" @click.self="closeDialog">
      <div class="dialog">
        <div class="dialog-header">
          <h3>{{ isEditing ? '编辑凭据' : '添加凭据' }}</h3>
          <button @click="closeDialog" class="btn-close">&times;</button>
        </div>
        <div class="dialog-body">
          <form @submit.prevent="submitForm">
            <div class="form-group">
              <label for="connection_type">连接类型 *</label>
              <select
                id="connection_type"
                v-model="formData.connection_type"
                required
              >
                <option value="smb">SMB</option>
                <option value="ftp">FTP</option>
              </select>
            </div>

            <div class="form-group">
              <label for="name">凭据名称</label>
              <input
                id="name"
                v-model="formData.name"
                type="text"
                placeholder="可选，用于标识该凭据"
              />
            </div>

            <div class="form-group">
              <label for="server_host">服务器地址 *</label>
              <input
                id="server_host"
                v-model="formData.server_host"
                type="text"
                placeholder="例如: 192.168.1.100 或 ftp.example.com"
                required
              />
            </div>

            <div class="form-group">
              <label for="server_port">服务器端口 *</label>
              <input
                id="server_port"
                v-model.number="formData.server_port"
                type="number"
                :placeholder="formData.connection_type === 'smb' ? '445' : '21'"
                required
              />
            </div>

            <div v-if="formData.connection_type === 'smb'" class="form-group">
              <label for="domain">域</label>
              <input
                id="domain"
                v-model="formData.domain"
                type="text"
                placeholder="可选，例如: WORKGROUP"
              />
            </div>

            <div class="form-group">
              <label for="username">用户名 *</label>
              <input
                id="username"
                v-model="formData.username"
                type="text"
                placeholder="例如: admin"
                required
              />
            </div>

            <div class="form-group">
              <label for="password">密码 *</label>
              <input
                id="password"
                v-model="formData.password"
                type="password"
                placeholder="输入密码"
                required
              />
            </div>

            <div class="form-group">
              <label for="remote_path">远程路径 *</label>
              <input
                id="remote_path"
                v-model="formData.remote_path"
                type="text"
                :placeholder="formData.connection_type === 'smb' ? '/share/folder' : '/var/www/uploads'"
                required
              />
            </div>

            <div class="form-group">
              <label for="description">描述</label>
              <textarea
                id="description"
                v-model="formData.description"
                rows="2"
                placeholder="可选，添加备注信息"
              ></textarea>
            </div>

            <div class="form-actions">
              <button type="button" @click="closeDialog" class="btn-secondary">
                取消
              </button>
              <button type="submit" class="btn-primary">
                {{ isEditing ? '更新' : '创建' }}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import {
  listCredentials,
  createCredential,
  updateCredential,
  deleteCredential as apiDeleteCredential,
  type RemoteCredential,
  type CreateCredentialRequest,
  type UpdateCredentialRequest
} from '@/api/credentials'

const credentials = ref<RemoteCredential[]>([])
const loading = ref(true)
const showCreateDialog = ref(false)
const showEditDialog = ref(false)
const editingCredential = ref<RemoteCredential | null>(null)

const formData = ref<CreateCredentialRequest>({
  connection_type: 'smb',
  server_host: '',
  server_port: 445,
  username: '',
  password: '',
  remote_path: '',
  name: '',
  domain: '',
  description: ''
})

const isEditing = computed(() => showEditDialog.value && editingCredential.value !== null)

onMounted(async () => {
  await loadCredentials()
})

async function loadCredentials() {
  loading.value = true
  try {
    const result = await listCredentials()
    if (result.success) {
      credentials.value = result.data
    }
  } catch (error) {
    console.error('加载凭据失败:', error)
    alert('加载凭据失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

function editCredential(cred: RemoteCredential) {
  editingCredential.value = cred
  formData.value = {
    connection_type: cred.connection_type,
    server_host: cred.server_host,
    server_port: cred.server_port,
    username: cred.username,
    password: '', // 不显示已有密码
    remote_path: cred.remote_path,
    name: cred.name,
    domain: cred.domain,
    description: cred.description
  }
  showEditDialog.value = true
}

async function submitForm() {
  try {
    if (isEditing.value && editingCredential.value) {
      const updateData: UpdateCredentialRequest = { ...formData.value }
      if (!formData.value.password) {
        delete updateData.password
      }
      const result = await updateCredential(editingCredential.value.credential_id, updateData)
      if (result.success) {
        alert('更新成功')
        closeDialog()
        await loadCredentials()
      } else {
        alert(result.error || '更新失败')
      }
    } else {
      const result = await createCredential(formData.value)
      if (result.success) {
        alert('创建成功')
        closeDialog()
        await loadCredentials()
      } else {
        alert(result.error || '创建失败')
      }
    }
  } catch (error) {
    console.error('操作失败:', error)
    alert('操作失败，请稍后重试')
  }
}

async function handleDeleteCredential(credentialId: string) {
  if (!confirm('确定要删除此凭据吗？')) {
    return
  }
  try {
    const result = await apiDeleteCredential(credentialId)
    if (result.success) {
      alert('删除成功')
      await loadCredentials()
    } else {
      alert(result.error || '删除失败')
    }
  } catch (error) {
    console.error('删除失败:', error)
    alert('删除失败，请稍后重试')
  }
}

function closeDialog() {
  showCreateDialog.value = false
  showEditDialog.value = false
  editingCredential.value = null
  resetForm()
}

function resetForm() {
  formData.value = {
    connection_type: 'smb',
    server_host: '',
    server_port: 445,
    username: '',
    password: '',
    remote_path: '',
    name: '',
    domain: '',
    description: ''
  }
}

function formatDate(dateString: string) {
  return new Date(dateString).toLocaleString('zh-CN')
}
</script>

<style scoped>
.credential-manager-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: #f9fafb;
  z-index: 2000;
  overflow-y: auto;
}

.credential-manager {
  max-width: 800px;
  margin: 0 auto;
  padding: 20px;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.header h2 {
  margin: 0;
  color: #1f2937;
}

.header-actions {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
}

.btn-close {
  background: none;
  border: none;
  font-size: 32px;
  cursor: pointer;
  color: #6b7280;
  line-height: 1;
  padding: 0 8px;
}

.btn-close:hover {
  color: #374151;
}

.loading,
.empty {
  text-align: center;
  padding: 40px;
  color: #6b7280;
}

.credential-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.credential-card {
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 16px;
  transition: box-shadow 0.2s;
}

.credential-card:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.credential-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}

.credential-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.type-badge {
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
}

.type-badge.smb {
  background: #dbeafe;
  color: #1e40af;
}

.type-badge.ftp {
  background: #d1fae5;
  color: #065f46;
}

.credential-title h3 {
  margin: 0;
  font-size: 16px;
  color: #1f2937;
}

.credential-actions {
  display: flex;
  gap: 8px;
}

.btn-icon {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 18px;
  padding: 4px;
  border-radius: 4px;
  transition: background 0.2s;
}

.btn-icon:hover {
  background: #f3f4f6;
}

.btn-danger:hover {
  background: #fee2e2;
}

.credential-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.credential-field {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 14px;
}

.credential-field label {
  color: #6b7280;
  min-width: 100px;
  font-weight: 500;
}

.credential-field span {
  color: #1f2937;
  word-break: break-all;
}

.credential-footer {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid #e5e7eb;
}

.credential-footer small {
  color: #9ca3af;
}

.dialog-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.dialog {
  background: white;
  border-radius: 8px;
  max-width: 500px;
  width: 90%;
  max-height: 90vh;
  overflow-y: auto;
}

.dialog-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px;
  border-bottom: 1px solid #e5e7eb;
}

.dialog-header h3 {
  margin: 0;
  color: #1f2937;
}

.btn-close {
  background: none;
  border: none;
  font-size: 24px;
  cursor: pointer;
  color: #6b7280;
}

.dialog-body {
  padding: 16px;
}

.form-group {
  margin-bottom: 16px;
}

.form-group label {
  display: block;
  margin-bottom: 6px;
  font-weight: 500;
  color: #374151;
  font-size: 14px;
}

.form-group input,
.form-group select,
.form-group textarea {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 14px;
  font-family: inherit;
}

.form-group input:focus,
.form-group select:focus,
.form-group textarea:focus {
  outline: none;
  border-color: #3b82f6;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.1);
}

.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 24px;
}

.btn-primary {
  background: #3b82f6;
  color: white;
  border: none;
  padding: 8px 16px;
  border-radius: 6px;
  cursor: pointer;
  font-weight: 500;
  transition: background 0.2s;
}

.btn-primary:hover {
  background: #2563eb;
}

.btn-secondary {
  background: #f3f4f6;
  color: #374151;
  border: none;
  padding: 8px 16px;
  border-radius: 6px;
  cursor: pointer;
  font-weight: 500;
  transition: background 0.2s;
}

.btn-secondary:hover {
  background: #e5e7eb;
}
</style>
