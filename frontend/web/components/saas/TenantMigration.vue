<template>
  <div class="space-y-4">
    <div class="text-sm text-muted">
      将其他数据库中的租户数据迁移到当前租户。支持知识库文档、知识库分类和旅游报价数据。
    </div>

    <!-- 源数据库 -->
    <div>
      <label class="text-sm text-muted mb-1 block">源数据库</label>
      <select v-model="form.source_db"
        class="w-full px-3 py-2 bg-surface-hover border border-hover rounded-lg text-default focus:outline-none focus:border-primary-400">
        <option value="aid_work_agent2">aid_work_agent2 (测试环境)</option>
        <option value="aid_work_agent">aid_work_agent (生产环境)</option>
      </select>
    </div>

    <!-- 源租户 ID -->
    <div>
      <label class="text-sm text-muted mb-1 block">源租户 ID <span class="text-danger-500">*</span></label>
      <input v-model="form.source_tenant" type="text" placeholder="请输入源租户ID"
        class="w-full px-3 py-2 bg-surface-hover border border-hover rounded-lg text-default focus:outline-none focus:border-primary-400 font-mono text-sm" />
    </div>

    <!-- 迁移内容（多选） -->
    <div>
      <label class="text-sm text-muted mb-1 block">迁移内容</label>
      <div class="space-y-1.5">
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" v-model="form.tables" value="kb"
            class="w-3.5 h-3.5 rounded border-primary-200 text-primary-600 focus:ring-primary-500" />
          <span class="text-sm text-default">知识库文档（documents + chunks + chunks_vec）</span>
        </label>
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" v-model="form.tables" value="knowledge_categories"
            class="w-3.5 h-3.5 rounded border-primary-200 text-primary-600 focus:ring-primary-500" />
          <span class="text-sm text-default">知识库分类（knowledge_categories）</span>
        </label>
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" v-model="form.tables" value="travel_quote"
            class="w-3.5 h-3.5 rounded border-primary-200 text-primary-600 focus:ring-primary-500" />
          <span class="text-sm text-default">旅游报价数据（5张业务表）</span>
        </label>
      </div>
    </div>

    <!-- 迁移模式 -->
    <div>
      <label class="text-sm text-muted mb-1 block">迁移模式</label>
      <div class="flex gap-4">
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="radio" v-model="form.mode" value="replace"
            class="w-3.5 h-3.5 text-primary-600 focus:ring-primary-500" />
          <span class="text-sm text-default">替换</span>
          <span class="text-xs text-muted">（先删后插，目标 = 源）</span>
        </label>
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="radio" v-model="form.mode" value="merge"
            class="w-3.5 h-3.5 text-primary-600 focus:ring-primary-500" />
          <span class="text-sm text-default">合并</span>
          <span class="text-xs text-muted">（按UUID去重，跳过已存在）</span>
        </label>
      </div>
    </div>

    <!-- 操作按钮 -->
    <div class="flex gap-2">
      <BaseButton intent="secondary" :disabled="loading" @click="handlePreview">
        {{ loading ? '处理中...' : '预览' }}
      </BaseButton>
      <BaseButton :disabled="loading || !previewResult" @click="handleExecute">
        {{ loading ? '处理中...' : '执行迁移' }}
      </BaseButton>
    </div>

    <!-- 错误信息 -->
    <div v-if="error" class="p-3 bg-danger-50 border border-danger-200 rounded text-danger-600 text-sm">{{ error }}</div>

    <!-- 预览/结果展示 -->
    <div v-if="previewResult" class="border border-default rounded-lg overflow-hidden">
      <div class="px-4 py-2 bg-gray-50 border-b border-default">
        <span class="text-sm font-medium text-default">
          {{ previewResult._executed ? '迁移结果' : '预览统计' }}
        </span>
        <span v-if="previewResult._executed" class="ml-2 text-xs px-1.5 py-0.5 rounded"
          :class="previewResult._success ? 'bg-success-100 text-success-700' : 'bg-danger-100 text-danger-700'">
          {{ previewResult._success ? '成功' : '失败' }}
        </span>
      </div>
      <div class="p-4 space-y-3">
        <div v-for="(val, key) in displaySummary" :key="key">
          <div class="text-sm font-medium text-default mb-1">{{ tableLabel(key) }}</div>
          <div class="grid grid-cols-3 gap-2 text-xs">
            <template v-if="key === 'kb'">
              <template v-if="val.documents">
                <div class="text-muted">文档: 源 {{ val.documents.source_count }} → 目标 {{ val.documents.target_before ?? '-' }}</div>
                <div class="text-muted">插入 {{ val.documents.inserted }} 条</div>
                <div class="text-muted">文件复制 {{ val.documents.files_copied ?? 0 }}</div>
              </template>
              <template v-if="val.chunks">
                <div class="text-muted">文本块: 源 {{ val.chunks.source_count }} → 目标 {{ val.chunks.target_before ?? '-' }}</div>
                <div class="text-muted">插入 {{ val.chunks.inserted }} 条</div>
                <div></div>
              </template>
              <template v-if="val.chunks_vec">
                <div class="text-muted">向量: 插入 {{ val.chunks_vec.inserted }} 条</div>
                <div></div>
                <div></div>
              </template>
            </template>
            <template v-else-if="key === 'travel_quote'">
              <template v-for="(tq, tqKey) in val" :key="tqKey">
                <div class="text-muted">{{ tq.table || tqKey }}: 源 {{ tq.source_count }}</div>
                <div class="text-muted">插入 {{ tq.inserted }}</div>
                <div class="text-muted">{{ tq.skipped ? '跳过 ' + tq.skipped : (tq.deleted ? '删除 ' + tq.deleted : '') }}</div>
              </template>
            </template>
            <template v-else>
              <div class="text-muted">源 {{ val.source_count }} 条</div>
              <div class="text-muted">插入 {{ val.inserted }} 条</div>
              <div class="text-muted">{{ val.skipped ? '跳过 ' + val.skipped : (val.deleted ? '删除 ' + val.deleted : '') }}</div>
            </template>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed } from 'vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { previewMigration, executeMigration } from '@/api/saasTenantMigration'

const props = defineProps<{
  tenantId: string
}>()

const form = reactive({
  source_db: 'aid_work_agent2',
  source_tenant: '',
  tables: ['kb', 'knowledge_categories', 'travel_quote'] as string[],
  mode: 'replace',
  source_storage: '/app/source_storage',
})

const loading = ref(false)
const error = ref('')
const previewResult = ref<Record<string, any> | null>(null)

const displaySummary = computed(() => {
  if (!previewResult.value) return {}
  const { _executed, _success, ...rest } = previewResult.value
  return rest
})

function tableLabel(key: string | number): string {
  const k = String(key)
  const map: Record<string, string> = {
    kb: '知识库文档',
    knowledge_categories: '知识库分类',
    travel_quote: '旅游报价数据',
  }
  return map[k] || k
}

async function handlePreview() {
  if (!form.source_tenant.trim()) {
    error.value = '请填写源租户 ID'
    return
  }
  if (form.tables.length === 0) {
    error.value = '请选择至少一项迁移内容'
    return
  }
  loading.value = true
  error.value = ''
  previewResult.value = null
  try {
    const res = await previewMigration(props.tenantId, { ...form })
    previewResult.value = { ...res.summary, _executed: false, _success: res.success }
    if (res.errors && res.errors.length > 0) {
      error.value = '预览部分失败: ' + res.errors.join('; ')
    }
  } catch (e: any) {
    error.value = e.message || '预览失败'
  } finally {
    loading.value = false
  }
}

async function handleExecute() {
  if (!form.source_tenant.trim()) {
    error.value = '请填写源租户 ID'
    return
  }
  if (!confirm(`确定要执行迁移吗？模式: ${form.mode === 'replace' ? '替换（先删后插）' : '合并（按UUID去重）'}。此操作不可撤销。`)) return

  loading.value = true
  error.value = ''
  previewResult.value = null
  try {
    const res = await executeMigration(props.tenantId, { ...form })
    previewResult.value = { ...res.summary, _executed: true, _success: res.success }
    if (res.errors && res.errors.length > 0) {
      error.value = '迁移部分失败: ' + res.errors.join('; ')
    } else if (res.success) {
      error.value = ''
    }
  } catch (e: any) {
    error.value = e.message || '执行迁移失败'
  } finally {
    loading.value = false
  }
}
</script>
