<template>
  <div class="page-container">
    <div class="page-toolbar">
      <div class="page-toolbar-left">
        <h2 class="m-0 text-lg">邮件记录</h2>
      </div>
      <div class="page-toolbar-right">
        <BaseInput
          v-model="keyword"
          size="sm"
          placeholder="搜索公司/联系人/主题"
          class="w-80"
          @keyup.enter="onSearch"
        />
        <BaseButton size="sm" :disabled="loading" @click="onSearch">
          {{ loading ? '加载中...' : '搜索' }}
        </BaseButton>
        <BaseButton size="sm" intent="secondary" :disabled="loading" @click="onRefresh">
          刷新
        </BaseButton>
      </div>
    </div>

    <!-- 错误提示 -->
    <div v-if="error" class="mb-4 p-4 bg-danger-50 border border-danger-200 rounded-lg text-danger-600 text-sm">
      <p class="m-0">{{ error }}</p>
      <p v-if="debug" class="mt-2 text-xs text-danger-700">{{ debug }}</p>
    </div>

    <!-- 邮件列表 -->
    <template v-if="!loading && !error && filteredEmails.length > 0">
      <div class="table-scroll-wrapper">
        <BaseTable :columns="columns" :data="pagedEmails" row-key="email_id">
          <template #index="{ index }">{{ seqNumber(index) }}</template>
          <template #company_name="{ row }">
            <span class="font-medium text-default">{{ row.company_name || '-' }}</span>
          </template>
          <template #contact_name="{ row }">{{ row.contact_name || '-' }}</template>
          <template #email_subject="{ row }">
            <span class="block max-w-[260px] truncate" :title="row.email_subject">{{ row.email_subject || '-' }}</span>
          </template>
          <template #email_language="{ row }">
            <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-primary-100 text-primary-700">{{ row.email_language || '-' }}</span>
          </template>
          <template #send_time="{ row }">{{ formatDateTime(row.send_time) }}</template>
          <template #send_status="{ row }">
            <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="emailStatusClass(row.send_status)">
              {{ statusLabel(row.send_status) }}
            </span>
          </template>
          <template #actions="{ row }">
            <BaseButton intent="ghost" size="sm" @click="openEmailDetail(row.email_id)">查看内容</BaseButton>
          </template>
          <template #empty>暂无邮件记录</template>
        </BaseTable>
      </div>

      <BasePagination
        :total="filteredEmails.length"
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
      />
    </template>

    <!-- 加载状态 -->
    <div v-else-if="loading" class="text-center py-12 text-muted">
      <svg class="w-8 h-8 animate-spin mx-auto mb-3" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
      加载邮件记录中...
    </div>

    <!-- 空状态 -->
    <div v-else-if="!error && filteredEmails.length === 0" class="text-center py-12 text-muted">
      <p class="text-lg mb-2">暂无邮件记录</p>
      <p class="text-sm">外贸智能体发送邮件后，记录会显示在此处</p>
    </div>

    <!-- 邮件详情弹窗 -->
    <BaseModal v-model="showDetailModal" :title="'邮件内容 - ' + (currentEmail?.company_name || '-')" size="lg" mode="view">
      <div v-if="currentEmail" class="space-y-3">
        <div class="grid grid-cols-2 gap-4 text-sm">
          <div>
            <div class="text-muted mb-1">公司名称</div>
            <div class="text-default">{{ currentEmail.company_name || '-' }}</div>
          </div>
          <div>
            <div class="text-muted mb-1">联系人</div>
            <div class="text-default">{{ currentEmail.contact_name || '-' }}</div>
          </div>
          <div>
            <div class="text-muted mb-1">发送时间</div>
            <div class="text-default">{{ formatDateTime(currentEmail.send_time) }}</div>
          </div>
          <div>
            <div class="text-muted mb-1">发送状态</div>
            <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium" :class="emailStatusClass(currentEmail.send_status)">
              {{ statusLabel(currentEmail.send_status) }}
            </span>
          </div>
          <div class="col-span-2">
            <div class="text-muted mb-1">邮件主题</div>
            <div class="text-default font-medium">{{ currentEmail.email_subject || '-' }}</div>
          </div>
          <div v-if="currentEmail.error_message" class="col-span-2">
            <div class="text-muted mb-1">失败原因</div>
            <div class="text-danger-600 text-sm">{{ currentEmail.error_message }}</div>
          </div>
          <div class="col-span-2">
            <div class="text-muted mb-1">邮件正文</div>
            <pre class="text-sm text-default whitespace-pre-wrap break-words m-0 font-inherit max-h-[400px] overflow-y-auto p-3 bg-canvas rounded-lg border border-default">{{ currentEmail.email_body || '-' }}</pre>
          </div>
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showDetailModal = false">关闭</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { listEmails, type CustomerEmail } from '@/api/customer'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import { usePageContext } from '@/composables/usePageContext'
import { formatDateTime } from '@/utils/date'
import { useDemoAuth } from '@/composables/useDemoAuth'
import { useTenantAuth } from '@/composables/useTenantAuth'

const route = useRoute()
const { user: demoUser } = useDemoAuth()
const { admin: tenantAdmin } = useTenantAuth()

const loading = ref(false)
const error = ref('')
const debug = ref('')
const emails = ref<CustomerEmail[]>([])
const keyword = ref('')
const showDetailModal = ref(false)
const currentEmail = ref<CustomerEmail | null>(null)

const userId = computed(() => {
  const queryUserId = route.query.user_id as string
  if (queryUserId) return queryUserId
  if (route.path.startsWith('/t/')) {
    return tenantAdmin.value?.user_id || ''
  }
  return demoUser.value?.user_id || ''
})

const columns = [
  { key: 'index', label: '序号', width: '60px' },
  { key: 'company_name', label: '公司名称' },
  { key: 'contact_name', label: '联系人' },
  { key: 'email_subject', label: '邮件主题' },
  { key: 'email_language', label: '语言', width: '90px' },
  { key: 'send_time', label: '发送时间', width: '150px' },
  { key: 'send_status', label: '状态', width: '90px' },
  { key: 'actions', label: '操作', width: '110px' },
]

const { currentPage, pageSize, seqNumber, refresh } =
  usePageContext(async () => { await loadData() })

function onSearch() {
  currentPage.value = 1
}

function onRefresh() {
  refresh()
}

const filteredEmails = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return emails.value
  return emails.value.filter(e =>
    (e.company_name || '').toLowerCase().includes(kw) ||
    (e.contact_name || '').toLowerCase().includes(kw) ||
    (e.email_subject || '').toLowerCase().includes(kw)
  )
})

const pagedEmails = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredEmails.value.slice(start, start + pageSize.value)
})

function emailStatusClass(status: string) {
  const map: Record<string, string> = {
    success: 'bg-success-100 text-success-700',
    failed: 'bg-danger-100 text-danger-700',
    pending: 'bg-warning-100 text-warning-700',
  }
  return map[status] || 'bg-gray-100 text-muted'
}

function statusLabel(status: string): string {
  if (status === 'success') return '成功'
  if (status === 'failed') return '失败'
  if (status === 'pending') return '进行中'
  return status || '-'
}

async function loadData() {
  if (!userId.value) {
    error.value = '缺少用户ID参数，请从外贸智能体页面跳转'
    return
  }

  loading.value = true
  error.value = ''
  debug.value = ''

  try {
    const res = await listEmails(undefined, userId.value, 500)
    if (res.success && res.data) {
      emails.value = res.data.emails || []
    } else {
      error.value = res.error || '获取邮件记录失败'
      debug.value = res.debug || ''
    }
  } catch (e: any) {
    error.value = '加载邮件记录失败'
    debug.value = e.message || ''
  } finally {
    loading.value = false
  }
}

function openEmailDetail(emailId: string) {
  const email = filteredEmails.value.find(e => e.email_id === emailId) || null
  currentEmail.value = email
  showDetailModal.value = true
}

onMounted(() => { loadData() })
</script>
