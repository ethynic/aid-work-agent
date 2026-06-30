<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <AppHeader
      title="社媒运营工作台"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    />

    <main class="flex-1 overflow-y-auto">
      <section class="border-b border-default bg-surface">
        <div class="px-6 py-5">
          <div class="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <p class="text-sm text-muted">微信公众号 / 微信视频号</p>
              <h1 class="mt-1 text-2xl font-semibold text-default">计划、审核、发布和数据统一看板</h1>
            </div>
            <div class="flex flex-wrap gap-2">
              <BaseButton intent="secondary" @click="loadAll">刷新</BaseButton>
              <BaseButton @click="seedDemoFlow">创建示例草稿</BaseButton>
            </div>
          </div>
        </div>
      </section>

      <section class="grid gap-4 px-6 py-5 md:grid-cols-3">
        <div class="rounded-lg border border-default bg-surface p-4">
          <p class="text-sm text-muted">账号</p>
          <p class="mt-2 text-2xl font-semibold text-default">{{ accounts.length }}</p>
        </div>
        <div class="rounded-lg border border-default bg-surface p-4">
          <p class="text-sm text-muted">内容计划</p>
          <p class="mt-2 text-2xl font-semibold text-default">{{ plans.length }}</p>
        </div>
        <div class="rounded-lg border border-default bg-surface p-4">
          <p class="text-sm text-muted">发布任务</p>
          <p class="mt-2 text-2xl font-semibold text-default">{{ publishJobs.length }}</p>
        </div>
      </section>

      <section class="grid gap-5 px-6 pb-6 xl:grid-cols-[360px_1fr]">
        <div class="space-y-5">
          <BaseCard title="绑定平台账号">
            <div class="space-y-3">
              <label class="block text-sm text-default">
                平台
                <BaseSelect v-model="accountForm.platform" class="mt-1">
                  <option value="wechat_official">微信公众号</option>
                  <option value="wechat_channels">微信视频号</option>
                </BaseSelect>
              </label>
              <label class="block text-sm text-default">
                账号名称
                <BaseInput v-model="accountForm.display_name" class="mt-1" placeholder="例如：品牌服务号" />
              </label>
              <label class="block text-sm text-default">
                外部账号 ID
                <BaseInput v-model="accountForm.external_account_id" class="mt-1" placeholder="AppID 或账号标识" />
              </label>
              <BaseButton full-width :disabled="savingAccount" @click="createAccount">
                {{ savingAccount ? '保存中...' : '保存账号' }}
              </BaseButton>
            </div>
          </BaseCard>

          <BaseCard title="创建周计划">
            <div class="space-y-3">
              <label class="block text-sm text-default">
                计划名称
                <BaseInput v-model="planForm.name" class="mt-1" placeholder="本周品牌内容运营" />
              </label>
              <label class="block text-sm text-default">
                目标
                <BaseInput v-model="planForm.goal" class="mt-1" placeholder="获客、转化、活动预热" />
              </label>
              <BaseButton full-width intent="secondary" :disabled="savingPlan" @click="createPlan">
                {{ savingPlan ? '创建中...' : '创建计划' }}
              </BaseButton>
            </div>
          </BaseCard>
        </div>

        <div class="space-y-5">
          <BaseCard title="账号能力">
            <div v-if="accounts.length === 0" class="py-8 text-center text-sm text-muted">暂无账号</div>
            <div v-else class="overflow-x-auto">
              <table class="w-full min-w-[720px] text-left text-sm">
                <thead class="border-b border-default text-muted">
                  <tr>
                    <th class="py-2 font-medium">账号</th>
                    <th class="py-2 font-medium">平台</th>
                    <th class="py-2 font-medium">能力</th>
                    <th class="py-2 font-medium">状态</th>
                    <th class="py-2 text-right font-medium">操作</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-default">
                  <tr v-for="account in accounts" :key="account.account_id">
                    <td class="py-3 text-default">{{ account.display_name }}</td>
                    <td class="py-3 text-muted">{{ platformLabel(account.platform) }}</td>
                    <td class="py-3">
                      <div class="flex flex-wrap gap-1">
                        <BaseBadge
                          v-for="cap in supportedCapabilities(account)"
                          :key="cap"
                          :intent="capIntent(cap)"
                          size="sm"
                        >
                          {{ capabilityLabel(cap) }}
                        </BaseBadge>
                      </div>
                    </td>
                    <td class="py-3"><BaseBadge intent="success" size="sm">{{ account.status }}</BaseBadge></td>
                    <td class="py-3 text-right">
                      <BaseButton intent="ghost" size="sm" @click="refreshAccount(account.account_id)">验证</BaseButton>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </BaseCard>

          <BaseCard title="发布队列">
            <div v-if="publishJobs.length === 0" class="py-8 text-center text-sm text-muted">暂无发布任务</div>
            <div v-else class="grid gap-3">
              <article
                v-for="job in publishJobs"
                :key="job.job_id"
                class="rounded-lg border border-default bg-surface-hover p-4"
              >
                <div class="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                  <div>
                    <div class="flex items-center gap-2">
                      <p class="font-medium text-default">{{ job.account_name || job.account_id }}</p>
                      <BaseBadge :intent="statusIntent(job.status)" size="sm">{{ statusLabel(job.status) }}</BaseBadge>
                    </div>
                    <p class="mt-1 text-sm text-muted">
                      {{ platformLabel(job.platform || '') }} / {{ modeLabel(job.publish_mode) }}
                    </p>
                  </div>
                  <p class="text-sm text-muted">{{ job.created_at ? new Date(job.created_at).toLocaleString() : '' }}</p>
                </div>
              </article>
            </div>
          </BaseCard>

          <BaseCard title="运营数据">
            <div class="grid gap-3 md:grid-cols-2">
              <div class="rounded-lg bg-surface-hover p-4">
                <p class="text-sm text-muted">平台账号分布</p>
                <p class="mt-2 text-default">{{ overviewText(overview.accounts_by_platform) }}</p>
              </div>
              <div class="rounded-lg bg-surface-hover p-4">
                <p class="text-sm text-muted">发布状态分布</p>
                <p class="mt-2 text-default">{{ overviewText(overview.publish_jobs_by_status) }}</p>
              </div>
            </div>
          </BaseCard>
        </div>
      </section>
    </main>

    <div
      v-if="message"
      class="fixed bottom-5 right-5 max-w-sm rounded-lg border px-4 py-3 text-sm shadow-lg"
      :class="messageType === 'success' ? 'border-success-200 bg-success-50 text-success-700' : 'border-danger-200 bg-danger-50 text-danger-700'"
    >
      {{ message }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppHeader from '@/components/AppHeader.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import { useDemoAuth } from '@/composables/useDemoAuth'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { socialMediaAPI, type ContentPlan, type PublishJob, type SocialAccount } from '@/api/socialMedia'

const route = useRoute()
const router = useRouter()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()
const { user: demoUser, isLoggedIn: demoIsLoggedIn, logout: demoLogout } = useDemoAuth()
const toggleSidebarFn = inject<() => void>('toggleSidebar')

const tenantId = computed(() => route.params.tenant_id as string | undefined)
const isTenantPath = computed(() => Boolean(tenantId.value))
const effectiveIsLoggedIn = computed(() => isTenantPath.value ? tenantIsLoggedIn.value : demoIsLoggedIn.value)
const effectiveUser = computed(() => tenantAdmin.value ? {
  user_id: tenantAdmin.value.user_id,
  username: tenantAdmin.value.username,
  phone: tenantAdmin.value.phone,
} : demoUser.value)

const accounts = ref<SocialAccount[]>([])
const plans = ref<ContentPlan[]>([])
const publishJobs = ref<PublishJob[]>([])
const overview = ref({
  publish_jobs_by_status: {} as Record<string, number>,
  accounts_by_platform: {} as Record<string, number>,
})
const savingAccount = ref(false)
const savingPlan = ref(false)
const message = ref('')
const messageType = ref<'success' | 'error'>('success')

const accountForm = ref({
  platform: 'wechat_official',
  display_name: '',
  external_account_id: '',
})
const planForm = ref({
  name: '',
  goal: '',
})

function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}

async function handleLogout() {
  if (isTenantPath.value) {
    await tenantLogout()
  } else {
    await demoLogout()
  }
  router.push(tenantId.value ? `/t/${tenantId.value}/login` : '/')
}

function showMessage(text: string, type: 'success' | 'error' = 'success') {
  message.value = text
  messageType.value = type
  window.setTimeout(() => {
    if (message.value === text) message.value = ''
  }, 3500)
}

async function loadAll() {
  const [accountRes, planRes, jobRes, overviewRes] = await Promise.all([
    socialMediaAPI.listAccounts(),
    socialMediaAPI.listPlans(),
    socialMediaAPI.listPublishJobs(),
    socialMediaAPI.analyticsOverview(),
  ])
  accounts.value = accountRes.data?.items || []
  plans.value = planRes.data?.items || []
  publishJobs.value = jobRes.data?.items || []
  if (overviewRes.data) overview.value = overviewRes.data
}

async function createAccount() {
  if (!accountForm.value.display_name) {
    showMessage('请填写账号名称', 'error')
    return
  }
  savingAccount.value = true
  try {
    const res = await socialMediaAPI.createAccount({
      ...accountForm.value,
      auth_type: accountForm.value.platform === 'wechat_official' ? 'credentials' : 'assisted',
      credentials: accountForm.value.platform === 'wechat_official'
        ? { app_id: accountForm.value.external_account_id, app_secret: '' }
        : {},
    })
    if (!res.success) throw new Error(res.error || '保存失败')
    showMessage('账号已保存')
    accountForm.value.display_name = ''
    accountForm.value.external_account_id = ''
    await loadAll()
  } catch (error: any) {
    showMessage(error.message || '保存账号失败', 'error')
  } finally {
    savingAccount.value = false
  }
}

async function createPlan() {
  if (!planForm.value.name) {
    showMessage('请填写计划名称', 'error')
    return
  }
  savingPlan.value = true
  try {
    const res = await socialMediaAPI.createPlan(planForm.value)
    if (!res.success) throw new Error(res.error || '创建失败')
    showMessage('计划已创建')
    planForm.value.name = ''
    planForm.value.goal = ''
    await loadAll()
  } catch (error: any) {
    showMessage(error.message || '创建计划失败', 'error')
  } finally {
    savingPlan.value = false
  }
}

async function refreshAccount(accountId: string) {
  const res = await socialMediaAPI.refreshCapabilities(accountId)
  if (!res.success) {
    showMessage(res.error || '验证失败', 'error')
    return
  }
  showMessage('账号能力已刷新')
  await loadAll()
}

async function seedDemoFlow() {
  if (accounts.value.length === 0) {
    showMessage('请先绑定至少一个平台账号', 'error')
    return
  }
  const account = accounts.value[0]
  try {
    const master = await socialMediaAPI.createMaster({
      title: '本周品牌内容母版',
      brief: '围绕产品能力和客户案例生成社媒内容。',
      facts: [{ source: 'manual', text: '示例事实，需运营人员替换为真实来源。' }],
      source_refs: [{ type: 'manual', label: '运营输入' }],
    })
    if (!master.success || !master.data?.master_id) throw new Error(master.error || '母版创建失败')
    const variant = await socialMediaAPI.createVariant(master.data.master_id, {
      account_id: account.account_id,
      content_type: account.platform === 'wechat_channels' ? 'video' : 'article',
      content: {
        title: '本周品牌内容草稿',
        digest: '聚焦客户价值与业务成果。',
        body: '这是一份待审核的示例内容，请运营人员补充真实素材和事实来源。',
        description: '这是一份待审核的视频号示例描述。',
      },
    })
    if (!variant.success || !variant.data?.variant_id) throw new Error(variant.error || '平台版本创建失败')
    await socialMediaAPI.submitReview(variant.data.variant_id)
    await socialMediaAPI.approveVariant(variant.data.variant_id, '示例流程审核通过')
    const publishMode = supportedCapabilities(account).includes('api_publish') ? 'immediate' : 'assisted'
    await socialMediaAPI.createPublishJob({ variant_id: variant.data.variant_id, publish_mode: publishMode })
    showMessage('示例草稿已进入发布队列')
    await loadAll()
  } catch (error: any) {
    showMessage(error.message || '示例流程失败', 'error')
  }
}

function supportedCapabilities(account: SocialAccount): string[] {
  const caps = account.capabilities_json?.supported || []
  return Array.isArray(caps) ? caps : []
}

function platformLabel(platform: string) {
  const map: Record<string, string> = {
    wechat_official: '微信公众号',
    wechat_channels: '微信视频号',
  }
  return map[platform] || platform || '未知平台'
}

function capabilityLabel(capability: string) {
  const map: Record<string, string> = {
    account_credentials: '凭证绑定',
    remote_draft: '远端草稿',
    api_publish: 'API 发布',
    assisted_publish: '辅助发布',
    scheduled_publish: '定时',
    publish_status: '状态回查',
    api_analytics: '数据同步',
    data_import: '数据导入',
  }
  return map[capability] || capability
}

function capIntent(capability: string) {
  if (capability === 'api_publish') return 'success'
  if (capability === 'assisted_publish') return 'warning'
  if (capability.includes('analytics') || capability.includes('import')) return 'info'
  return 'neutral'
}

function statusLabel(status: string) {
  const map: Record<string, string> = {
    scheduled: '已排期',
    queued: '待发布',
    submitted: '平台已受理',
    published: 'API 已发布',
    ready_for_manual_publish: '待人工发布',
    manually_confirmed: '人工确认',
    status_unknown: '状态未知',
    failed: '失败',
    cancelled: '已取消',
  }
  return map[status] || status
}

function statusIntent(status: string) {
  if (['published', 'manually_confirmed'].includes(status)) return 'success'
  if (['ready_for_manual_publish', 'submitted', 'status_unknown'].includes(status)) return 'warning'
  if (status === 'failed') return 'danger'
  return 'info'
}

function modeLabel(mode: string) {
  const map: Record<string, string> = {
    immediate: '立即发布',
    scheduled: '定时发布',
    assisted: '辅助发布',
  }
  return map[mode] || mode
}

function overviewText(data: Record<string, number>) {
  const entries = Object.entries(data || {})
  if (entries.length === 0) return '暂无数据'
  return entries.map(([key, count]) => `${statusLabel(platformLabel(key))}: ${count}`).join('，')
}

onMounted(loadAll)
</script>
