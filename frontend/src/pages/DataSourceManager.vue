<template>
  <div class="p-6 max-w-[1400px] mx-auto">
    <!-- Tab switcher -->
    <div class="flex gap-1 mb-5 border-b border-default">
      <button
        :class="[
          'px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors cursor-pointer',
          activeTab === 'schemas'
            ? 'border-primary-500 text-primary-600'
            : 'border-transparent text-muted hover:text-default'
        ]"
        @click="activeTab = 'schemas'"
      >
        已注册数据表
      </button>
      <button
        :class="[
          'px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors cursor-pointer',
          activeTab === 'connectors'
            ? 'border-primary-500 text-primary-600'
            : 'border-transparent text-muted hover:text-default'
        ]"
        @click="activeTab = 'connectors'"
      >
        数据连接器
      </button>
    </div>

    <!-- Tab: Schemas -->
    <div v-if="activeTab === 'schemas'">
      <div class="flex justify-between items-center mb-4">
        <div class="flex gap-2 items-center">
          <BaseInput v-model="searchQuery" placeholder="搜索表名..." @keyup.enter="searchSchemas" />
          <BaseButton :disabled="searching" @click="searchSchemas">
            {{ searching ? '搜索中...' : '搜索' }}
          </BaseButton>
          <BaseButton v-if="searched" intent="secondary" @click="clearSearch">显示全部</BaseButton>
        </div>
        <div class="flex gap-2">
          <BaseButton @click="openUploadDialog">上传 Excel/CSV</BaseButton>
          <BaseButton intent="secondary" @click="openRelationsDialog">管理关联关系</BaseButton>
        </div>
      </div>

      <BaseTable :columns="schemaColumns" :data="(filteredSchemas as any[])" row-key="id">
        <template #index="{ index }">{{ index + 1 }}</template>
        <template #title="{ row }"><span class="font-medium">{{ row.title }}</span></template>
        <template #source="{ row }">{{ row.metadata?.source_info || '-' }}</template>
        <template #columns_count="{ row }">{{ row.metadata?.columns?.length || 0 }}</template>
        <template #relations="{ row }">{{ countRelations(row) }}</template>
        <template #created_at="{ row }">{{ formatDate(row.created_at) }}</template>
        <template #actions="{ row }">
          <div class="flex gap-2">
            <BaseButton intent="ghost" size="sm" @click="editSchema(row)">编辑</BaseButton>
            <BaseButton intent="danger" size="sm" @click="handleDeleteSchema(row)">删除</BaseButton>
          </div>
        </template>
        <template v-if="loadingSchemas" #empty>加载中...</template>
        <template v-else #empty>暂无数据表，请上传 Excel/CSV 或从数据库导入</template>
      </BaseTable>
    </div>

    <!-- Tab: Connectors -->
    <div v-if="activeTab === 'connectors'">
      <div class="flex justify-between items-center mb-4">
        <h3 class="text-lg font-medium text-default m-0">数据库连接器</h3>
        <BaseButton @click="openConnectorDialog()">新建连接器</BaseButton>
      </div>

      <BaseTable :columns="connectorColumns" :data="(connectors as any[])" row-key="id">
        <template #index="{ index }">{{ index + 1 }}</template>
        <template #name="{ row }"><span class="font-medium">{{ row.name }}</span></template>
        <template #db_type="{ row }"><BaseBadge intent="info">{{ row.db_type }}</BaseBadge></template>
        <template #host="{ row }">{{ row.host }}:{{ row.port }}</template>
        <template #status="{ row }">
          <BaseBadge :intent="row.is_active ? 'success' : 'danger'">
            {{ row.is_active ? '正常' : '异常' }}
          </BaseBadge>
        </template>
        <template #tables_count="{ row }">{{ row.imported_tables?.length || 0 }}</template>
        <template #actions="{ row }">
          <div class="flex gap-2">
            <BaseButton intent="ghost" size="sm" :disabled="testingConnectorId === row.id" @click="testConnector(row)">
              {{ testingConnectorId === row.id ? '测试中...' : '测试' }}
            </BaseButton>
            <BaseButton intent="ghost" size="sm" @click="openConnectorDialog(row)">编辑</BaseButton>
            <BaseButton intent="ghost" size="sm" @click="openImportDialog(row)">导入</BaseButton>
            <BaseButton intent="danger" size="sm" @click="handleDeleteConnector(row)">删除</BaseButton>
          </div>
        </template>
        <template v-if="loadingConnectors" #empty>加载中...</template>
        <template v-else #empty>暂无连接器</template>
      </BaseTable>
    </div>

    <!-- Modal: Schema Review -->
    <BaseModal v-model="showSchemaReview" :title="'Schema 审核 — ' + (currentSchema?.table_name || '')" size="xl" mode="edit">
      <div v-if="reviewSchemas.length > 0 && currentSchema" class="space-y-4">
        <div class="flex justify-between items-center">
          <span class="text-muted text-sm">{{ currentReviewIndex + 1 }} / {{ reviewSchemas.length }}</span>
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">表名 <span class="text-danger-500">*</span></label>
          <BaseInput v-model="currentSchema.table_name" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">描述</label>
          <BaseInput v-model="currentSchema.description" />
        </div>
        <BaseTable :columns="columnReviewColumns" :data="currentSchema.columns || []" row-key="name">
          <template #name="{ row }"><span class="text-sm">{{ row.name }}</span></template>
          <template #semantic_name="{ row }">
            <BaseInput v-model="row.semantic_name" size="sm" />
          </template>
          <template #data_type="{ row }">
            <select
              :value="row.data_type"
              class="px-2 py-1 border border-default rounded text-sm bg-surface"
              @change="row.data_type = ($event.target as HTMLSelectElement).value"
            >
              <option v-for="dt in dataTypes" :key="dt.value" :value="dt.value">{{ dt.label }}</option>
            </select>
          </template>
          <template #description="{ row }">
            <BaseInput v-model="row.description" size="sm" />
          </template>
          <template #enum_values="{ row }">
            <span class="text-sm text-muted">{{ row.enum_values?.join(', ') || '-' }}</span>
          </template>
        </BaseTable>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="skipSchema">跳过</BaseButton>
        <BaseButton @click="confirmSchema">确认入库</BaseButton>
      </template>
    </BaseModal>

    <!-- Modal: Connector Create/Edit -->
    <BaseModal v-model="showConnectorModal" :title="editingConnector ? '编辑连接器' : '新建连接器'" size="md" :mode="editingConnector ? 'edit' : 'create'">
      <div class="space-y-4">
        <div>
          <label class="text-sm text-muted mb-1 block">名称 <span class="text-danger-500">*</span></label>
          <BaseInput v-model="connectorForm.name" />
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="text-sm text-muted mb-1 block">数据库类型 <span class="text-danger-500">*</span></label>
            <select
              v-model="connectorForm.db_type"
              :disabled="!!editingConnector"
              class="w-full px-3 py-2 border border-default rounded bg-surface text-sm"
            >
              <option value="mysql">MySQL</option>
              <option value="postgresql">PostgreSQL</option>
            </select>
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">数据库名 <span class="text-danger-500">*</span></label>
            <BaseInput v-model="connectorForm.database_name" />
          </div>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="text-sm text-muted mb-1 block">主机 <span class="text-danger-500">*</span></label>
            <BaseInput v-model="connectorForm.host" />
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">端口 <span class="text-danger-500">*</span></label>
            <BaseInput v-model="connectorForm.port" type="number" />
          </div>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="text-sm text-muted mb-1 block">用户名 <span class="text-danger-500">*</span></label>
            <BaseInput v-model="connectorForm.username" />
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">密码 <span class="text-danger-500">*</span></label>
            <BaseInput v-model="connectorForm.password" type="password" :placeholder="editingConnector ? '留空不修改' : ''" />
          </div>
        </div>
        <div class="flex gap-2 items-center">
          <BaseButton intent="secondary" :disabled="testingForm" @click="testConnectionForm">
            {{ testingForm ? '测试中...' : '测试连接' }}
          </BaseButton>
          <span
            v-if="testResult"
            :class="testResult.success ? 'text-success-600' : 'text-danger-600'"
            class="text-sm"
          >
            {{ testResult.success ? '连接成功: ' + testResult.version_info : '连接失败: ' + testResult.error }}
          </span>
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showConnectorModal = false">取消</BaseButton>
        <BaseButton @click="saveConnector">保存</BaseButton>
      </template>
    </BaseModal>

    <!-- Modal: Import tables from connector -->
    <BaseModal v-model="showImportModal" title="导入数据库表" size="lg">
      <div v-if="remoteTables.length > 0">
        <div
          v-for="table in remoteTables"
          :key="table.table_name"
          class="flex items-center gap-3 py-2 border-b border-default"
        >
          <input type="checkbox" v-model="selectedTables" :value="table.table_name" />
          <span class="text-default">{{ table.table_name }}</span>
          <span class="text-muted text-sm">({{ table.row_count }} 行, {{ table.columns?.length || 0 }} 列)</span>
        </div>
      </div>
      <div v-else class="text-muted text-center py-8">加载中...</div>
      <template #footer>
        <BaseButton intent="secondary" @click="showImportModal = false">取消</BaseButton>
        <BaseButton :disabled="selectedTables.length === 0 || importing" @click="doImport">
          {{ importing ? '导入中...' : '导入选中表' }}
        </BaseButton>
      </template>
    </BaseModal>

    <!-- Modal: Relations management -->
    <BaseModal v-model="showRelationsModal" title="关联关系管理" size="xl" mode="view">
      <div class="flex justify-between items-center mb-4">
        <BaseButton intent="secondary" :disabled="inferring" @click="inferRelations">
          {{ inferring ? '推断中...' : '自动推断关联' }}
        </BaseButton>
      </div>
      <BaseTable :columns="relationColumns" :data="(relations as any[])" row-key="id">
        <template #index="{ index }">{{ index + 1 }}</template>
        <template #from="{ row }">{{ row.from_table }}.{{ row.from_column }}</template>
        <template #to="{ row }">{{ row.to_table }}.{{ row.to_column }}</template>
        <template #type="{ row }">{{ row.relation_type }}</template>
        <template #description="{ row }">{{ row.description || '-' }}</template>
        <template #actions="{ row }">
          <BaseButton intent="danger" size="sm" @click="handleDeleteRelation(row)">删除</BaseButton>
        </template>
        <template #empty>暂无关联关系</template>
      </BaseTable>
      <div class="mt-4 pt-4 border-t border-default">
        <h4 class="text-sm font-medium text-muted mb-3">手动添加关联</h4>
        <div class="grid grid-cols-2 gap-3">
          <BaseInput v-model="newRelation.from_table" placeholder="源表名" />
          <BaseInput v-model="newRelation.from_column" placeholder="源列名" />
          <BaseInput v-model="newRelation.to_table" placeholder="目标表名" />
          <BaseInput v-model="newRelation.to_column" placeholder="目标列名" />
        </div>
        <div class="mt-2">
          <BaseInput v-model="newRelation.description" placeholder="关联描述（可选）" />
        </div>
        <BaseButton class="mt-2" @click="handleAddRelation">添加</BaseButton>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showRelationsModal = false">关闭</BaseButton>
      </template>
    </BaseModal>

    <!-- Hidden file input -->
    <input ref="fileInput" type="file" accept=".xlsx,.xls,.csv" style="display:none" @change="handleFileSelect" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import {
  listConnectors,
  createConnector,
  updateConnector,
  deleteConnector,
  testConnection,
  testSavedConnector,
  listRemoteTables,
  importTables,
  uploadExcel,
  listSchemas,
  saveSchema,
  updateSchema,
  deleteSchema,
  inferRelations as inferRelationsApi,
  listRelations,
  addRelation as addRelationApi,
  deleteRelation as deleteRelationApi,
  type Connector,
  type SchemaDocument,
  type SchemaInfo,
  type RelationItem,
} from '@/api/dataSource'

// ===== Tab state =====
const activeTab = ref<'schemas' | 'connectors'>('schemas')

// ===== Schemas tab =====
const loadingSchemas = ref(false)
const schemas = ref<SchemaDocument[]>([])
const searchQuery = ref('')
const searched = ref(false)
const searching = ref(false)

const schemaColumns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'title', label: '表名' },
  { key: 'source', label: '来源' },
  { key: 'columns_count', label: '字段数', width: '80px' },
  { key: 'relations', label: '关联数', width: '80px' },
  { key: 'created_at', label: '创建时间', width: '160px' },
  { key: 'actions', label: '操作', width: '120px' },
]

const filteredSchemas = computed(() => {
  if (!searched.value) return schemas.value
  const q = searchQuery.value.toLowerCase()
  return schemas.value.filter(s => s.title?.toLowerCase().includes(q))
})

async function loadSchemas() {
  loadingSchemas.value = true
  try {
    schemas.value = await listSchemas()
  } catch (e) {
    console.error('加载数据表失败', e)
    schemas.value = []
  } finally {
    loadingSchemas.value = false
  }
}

function searchSchemas() {
  if (!searchQuery.value.trim()) return
  searched.value = true
}

function clearSearch() {
  searchQuery.value = ''
  searched.value = false
}

function countRelations(row: any): number {
  const cols = row.metadata?.columns
  if (!Array.isArray(cols)) return 0
  return cols.filter((c: any) => c.foreign_key).length
}

function formatDate(dateStr: string): string {
  if (!dateStr) return '-'
  try {
    const d = new Date(dateStr)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  } catch {
    return dateStr
  }
}

// ===== Schema review modal =====
const showSchemaReview = ref(false)
const reviewSchemas = ref<SchemaInfo[]>([])
const currentReviewIndex = ref(0)
const editingSchemaDoc = ref<SchemaDocument | null>(null)
const importConnectorId = ref<string | null>(null)

const currentSchema = computed(() => reviewSchemas.value[currentReviewIndex.value] || null)

const columnReviewColumns = [
  { key: 'name', label: '原始列名', width: '120px' },
  { key: 'semantic_name', label: '语义名称' },
  { key: 'data_type', label: '数据类型', width: '120px' },
  { key: 'description', label: '描述' },
  { key: 'enum_values', label: '枚举值', width: '150px' },
]

const dataTypes = [
  { label: '文本', value: 'text' },
  { label: '整数', value: 'integer' },
  { label: '小数', value: 'decimal' },
  { label: '日期', value: 'date' },
  { label: '布尔', value: 'boolean' },
]

function openSchemaReview(schemasData: SchemaInfo[], connectorId?: string) {
  reviewSchemas.value = schemasData.map(s => ({
    ...s,
    columns: s.columns.map(c => ({ ...c })),
  }))
  currentReviewIndex.value = 0
  editingSchemaDoc.value = null
  importConnectorId.value = connectorId || null
  showSchemaReview.value = true
}

async function confirmSchema() {
  if (!currentSchema.value) return
  const schema = currentSchema.value
  try {
    if (editingSchemaDoc.value) {
      await updateSchema(editingSchemaDoc.value.id, schema)
    } else {
      await saveSchema({ ...schema, connector_id: importConnectorId.value || undefined })
    }
  } catch (e: any) {
    alert(e.message || '保存失败')
    return
  }

  if (currentReviewIndex.value < reviewSchemas.value.length - 1) {
    currentReviewIndex.value++
  } else {
    showSchemaReview.value = false
    await loadSchemas()
  }
}

function skipSchema() {
  if (currentReviewIndex.value < reviewSchemas.value.length - 1) {
    currentReviewIndex.value++
  } else {
    showSchemaReview.value = false
  }
}

async function editSchema(row: any) {
  const schema: SchemaInfo = {
    table_name: row.title || '',
    description: row.summary || '',
    source_info: row.metadata?.source_info || '',
    columns: row.metadata?.columns || [],
  }
  editingSchemaDoc.value = row
  openSchemaReview([schema])
}

async function handleDeleteSchema(row: any) {
  if (!confirm(`确定删除数据表「${row.title}」？`)) return
  try {
    await deleteSchema(row.id)
    await loadSchemas()
  } catch (e: any) {
    alert(e.message || '删除失败')
  }
}

// ===== Upload =====
const fileInput = ref<HTMLInputElement | null>(null)

function openUploadDialog() {
  fileInput.value?.click()
}

async function handleFileSelect(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  input.value = ''

  try {
    const result = await uploadExcel(file)
    const schemas = result.schemas
    if (schemas && schemas.length > 0) {
      openSchemaReview(schemas)
    } else {
      alert('未能识别出有效的数据表结构')
    }
  } catch (e: any) {
    alert(e.message || '上传失败')
  }
}

// ===== Connectors tab =====
const loadingConnectors = ref(false)
const connectors = ref<Connector[]>([])
const testingConnectorId = ref<string | null>(null)

const connectorColumns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'name', label: '名称' },
  { key: 'db_type', label: '类型', width: '100px' },
  { key: 'host', label: '地址' },
  { key: 'status', label: '状态', width: '80px' },
  { key: 'tables_count', label: '已导入表', width: '100px' },
  { key: 'actions', label: '操作', width: '240px' },
]

async function loadConnectors() {
  loadingConnectors.value = true
  try {
    connectors.value = await listConnectors()
  } catch (e) {
    console.error('加载连接器失败', e)
    connectors.value = []
  } finally {
    loadingConnectors.value = false
  }
}

async function testConnector(row: any) {
  testingConnectorId.value = row.id
  try {
    const result = await testSavedConnector(row.id)
    if (result.success) {
      alert('连接成功: ' + (result.version_info || ''))
    } else {
      alert('连接失败: ' + (result.error || '未知错误'))
    }
    await loadConnectors()
  } catch (e: any) {
    alert(e.message || '测试连接失败')
  } finally {
    testingConnectorId.value = null
  }
}

async function handleDeleteConnector(row: any) {
  if (!confirm(`确定删除连接器「${row.name}」？`)) return
  try {
    await deleteConnector(row.id)
    await loadConnectors()
  } catch (e: any) {
    alert(e.message || '删除失败')
  }
}

// ===== Connector form modal =====
const showConnectorModal = ref(false)
const editingConnector = ref<Connector | null>(null)
const testingForm = ref(false)
const testResult = ref<{ success: boolean; version_info?: string; error?: string } | null>(null)

const connectorForm = ref({
  name: '',
  db_type: 'mysql',
  host: '',
  port: '3306',
  database_name: '',
  username: '',
  password: '',
})

function openConnectorDialog(connector?: any) {
  editingConnector.value = connector || null
  testResult.value = null
  if (connector) {
    connectorForm.value = {
      name: connector.name,
      db_type: connector.db_type,
      host: connector.host,
      port: String(connector.port),
      database_name: connector.database_name,
      username: connector.username,
      password: '',
    }
  } else {
    connectorForm.value = {
      name: '',
      db_type: 'mysql',
      host: '',
      port: '3306',
      database_name: '',
      username: '',
      password: '',
    }
  }
  showConnectorModal.value = true
}

async function testConnectionForm() {
  testingForm.value = true
  testResult.value = null
  try {
    testResult.value = await testConnection({
      db_type: connectorForm.value.db_type,
      host: connectorForm.value.host,
      port: Number(connectorForm.value.port),
      database_name: connectorForm.value.database_name,
      username: connectorForm.value.username,
      password: connectorForm.value.password,
    })
  } catch (e: any) {
    testResult.value = { success: false, error: e.message }
  } finally {
    testingForm.value = false
  }
}

async function saveConnector() {
  const form = connectorForm.value
  if (!form.name || !form.host || !form.database_name || !form.username) {
    alert('请填写必填项')
    return
  }

  try {
    if (editingConnector.value) {
      const updateData: Record<string, any> = {
        name: form.name,
        host: form.host,
        port: Number(form.port),
        database_name: form.database_name,
        username: form.username,
      }
      if (form.password) {
        updateData.password = form.password
      }
      await updateConnector(editingConnector.value.id, updateData)
    } else {
      if (!form.password) {
        alert('请填写密码')
        return
      }
      await createConnector({
        name: form.name,
        db_type: form.db_type,
        host: form.host,
        port: Number(form.port),
        database_name: form.database_name,
        username: form.username,
        password: form.password,
      })
    }
    showConnectorModal.value = false
    await loadConnectors()
  } catch (e: any) {
    alert(e.message || '保存失败')
  }
}

// ===== Import modal =====
const showImportModal = ref(false)
const remoteTables = ref<{ table_name: string; row_count: number; columns: { name: string; type: string }[] }[]>([])
const selectedTables = ref<string[]>([])
const importing = ref(false)
const importingConnectorId = ref<string | null>(null)

async function openImportDialog(connector: any) {
  importingConnectorId.value = connector.id
  selectedTables.value = []
  remoteTables.value = []
  showImportModal.value = true

  try {
    remoteTables.value = await listRemoteTables(connector.id)
  } catch (e: any) {
    alert(e.message || '获取远程表列表失败')
    showImportModal.value = false
  }
}

async function doImport() {
  if (selectedTables.value.length === 0 || !importingConnectorId.value) return
  importing.value = true
  try {
    const result = await importTables(importingConnectorId.value, selectedTables.value)
    showImportModal.value = false
    if (result.schemas && result.schemas.length > 0) {
      openSchemaReview(result.schemas, importingConnectorId.value)
    } else {
      alert('导入完成')
      await loadSchemas()
    }
  } catch (e: any) {
    alert(e.message || '导入失败')
  } finally {
    importing.value = false
  }
}

// ===== Relations modal =====
const showRelationsModal = ref(false)
const relations = ref<(RelationItem & { id?: number })[]>([])
const inferring = ref(false)
const newRelation = ref({
  from_table: '',
  from_column: '',
  to_table: '',
  to_column: '',
  description: '',
})

const relationColumns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'from', label: '源' },
  { key: 'to', label: '目标' },
  { key: 'type', label: '类型', width: '100px' },
  { key: 'description', label: '描述' },
  { key: 'actions', label: '操作', width: '80px' },
]

async function openRelationsDialog() {
  showRelationsModal.value = true
  try {
    relations.value = await listRelations()
  } catch (e) {
    console.error('加载关联关系失败', e)
    relations.value = []
  }
}

async function inferRelationsFn() {
  inferring.value = true
  try {
    const schemaDocIds = schemas.value.map(s => s.id)
    if (schemaDocIds.length === 0) {
      alert('暂无已注册的数据表，无法推断关联')
      return
    }
    const inferred = await inferRelationsApi(schemaDocIds)
    relations.value = inferred
  } catch (e: any) {
    alert(e.message || '推断关联关系失败')
  } finally {
    inferring.value = false
  }
}

// Alias for template
const inferRelations = inferRelationsFn

async function handleAddRelation() {
  const r = newRelation.value
  if (!r.from_table || !r.from_column || !r.to_table || !r.to_column) {
    alert('请填写完整的关联信息')
    return
  }
  try {
    await addRelationApi({
      from_table: r.from_table,
      from_column: r.from_column,
      to_table: r.to_table,
      to_column: r.to_column,
      relation_type: 'manual',
      description: r.description || undefined,
    })
    newRelation.value = { from_table: '', from_column: '', to_table: '', to_column: '', description: '' }
    relations.value = await listRelations()
  } catch (e: any) {
    alert(e.message || '添加关联关系失败')
  }
}

async function handleDeleteRelation(row: any) {
  if (!confirm(`确定删除关联关系 ${row.from_table}.${row.from_column} → ${row.to_table}.${row.to_column}？`)) return
  try {
    await deleteRelationApi({
      from_table: row.from_table,
      from_column: row.from_column,
      to_table: row.to_table,
      to_column: row.to_column,
    })
    relations.value = await listRelations()
  } catch (e: any) {
    alert(e.message || '删除关联关系失败')
  }
}

// ===== Init =====
onMounted(() => {
  loadSchemas()
  loadConnectors()
})
</script>
