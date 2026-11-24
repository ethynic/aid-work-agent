<template>
  <div class="h-screen flex flex-col bg-canvas">
    <AppHeader
      title="连接邮箱"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    />

    <div class="flex-1 overflow-y-auto px-4 py-6 sm:px-6">
      <div class="max-w-4xl mx-auto space-y-4">
        <!-- 加载中：骨架屏 -->
        <BaseCard v-if="view === 'loading'">
          <div role="status">
            <p class="sr-only">正在加载邮箱信息</p>
            <div class="animate-pulse space-y-6">
            <div class="space-y-3">
              <div class="h-5 w-28 rounded bg-gray-200"></div>
              <div class="h-3 w-72 rounded bg-gray-100"></div>
            </div>
            <div v-for="(count, index) in [4, 3]" :key="index" class="space-y-3">
              <div class="h-3 w-20 rounded bg-gray-100"></div>
              <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                <div v-for="i in count" :key="i" class="flex items-center gap-3 rounded-lg border border-default p-4">
                  <div class="h-10 w-10 flex-shrink-0 rounded-full bg-gray-200"></div>
                  <div class="min-w-0 flex-1 space-y-2">
                    <div class="h-3 w-24 rounded bg-gray-200"></div>
                    <div class="h-3 w-16 rounded bg-gray-100"></div>
                  </div>
                </div>
              </div>
            </div>
            </div>
          </div>
        </BaseCard>

        <!-- 加载失败 -->
        <BaseCard v-else-if="view === 'error'">
          <div class="flex flex-col items-center gap-2 py-6 text-center">
            <svg class="h-8 w-8 text-danger-500" fill="none" stroke="currentColor" stroke-width="1.7" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v4M12 16.5h.01M10.3 3.9L2.6 17a2 2 0 001.7 3h15.4a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z" />
            </svg>
            <p class="text-sm font-medium text-default">邮箱信息加载失败</p>
            <p class="text-xs text-muted">请检查网络后重试。</p>
            <BaseButton intent="secondary" class="mt-2" @click="load">重试</BaseButton>
          </div>
        </BaseCard>

        <!-- 未连接 -->
        <BaseCard v-else-if="!settings">
          <h2 class="text-lg font-semibold text-default">我的邮箱</h2>
          <p class="mt-1 text-sm text-muted">连接后，数字员工可以代你收发邮件。选择邮箱类型，只需填写账号和密码或授权码。</p>
          <div class="mt-6 space-y-6">
            <section v-for="group in groups" :key="group.key">
              <h3 class="text-sm font-semibold text-default">{{ group.title }}</h3>
              <p v-if="group.desc" class="mt-0.5 text-xs text-muted">{{ group.desc }}</p>
              <div class="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4" role="group" :aria-label="group.title">
                <button
                  v-for="brand in group.brands"
                  :key="brand.key"
                  type="button"
                  class="flex items-center gap-3 rounded-lg border border-default bg-surface p-4 text-left transition-colors hover:border-primary-300 hover:bg-primary-50 focus:outline-none focus-visible:ring-1 focus-visible:ring-primary-400"
                  :aria-label="`连接${brand.name}`"
                  @click="openConnect(brand.key)"
                >
                  <MailboxBrandIcon :brand="brand.key" />
                  <span class="min-w-0 flex flex-col">
                    <span class="truncate text-sm font-semibold text-default">{{ brand.name }}</span>
                    <span class="mt-0.5 truncate text-xs text-muted">{{ brand.tileHint }}</span>
                  </span>
                </button>
              </div>
            </section>
          </div>
          <p class="mt-6 text-xs text-muted">每个账号连接 1 个邮箱，连接新的会替换当前的。密码和授权码加密保存，保存后不再显示。</p>
        </BaseCard>

        <!-- 已连接 -->
        <template v-else>
          <BaseCard>
            <h2 class="text-lg font-semibold text-default">我的邮箱</h2>
            <p class="mt-1 text-sm text-muted">数字员工发邮件、读邮件时使用这个邮箱。</p>
            <div class="mt-4 flex flex-wrap items-center gap-4">
              <MailboxBrandIcon :brand="connectedBrand" :size="48" />
              <div class="min-w-0 basis-[calc(100%-60px)] sm:basis-auto sm:flex-1">
                <p class="break-all text-sm font-semibold text-default">{{ settings.email_address }}</p>
                <p class="mt-0.5 text-xs text-muted">{{ connectedMeta }}</p>
              </div>
              <BaseBadge intent="success" class="ml-auto sm:ml-0">已连接</BaseBadge>
              <div class="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto sm:items-center">
                <BaseButton intent="secondary" @click="openReconnect">重新连接</BaseButton>
                <BaseButton intent="danger-ghost" :disabled="disconnecting" @click="disconnect">断开连接</BaseButton>
              </div>
            </div>
          </BaseCard>
          <BaseCard>
            <h2 class="text-lg font-semibold text-default">更换邮箱</h2>
            <p class="mt-1 text-sm text-muted">连接新邮箱后会替换当前邮箱。</p>
            <div class="mt-6 space-y-6">
              <section v-for="group in groups" :key="group.key">
                <h3 class="text-sm font-semibold text-default">{{ group.title }}</h3>
                <p v-if="group.desc" class="mt-0.5 text-xs text-muted">{{ group.desc }}</p>
                <div class="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4" role="group" :aria-label="group.title">
                  <button
                    v-for="brand in group.brands"
                    :key="brand.key"
                    type="button"
                    class="flex items-center gap-3 rounded-lg border border-default bg-surface p-4 text-left transition-colors hover:border-primary-300 hover:bg-primary-50 focus:outline-none focus-visible:ring-1 focus-visible:ring-primary-400"
                    :aria-label="`连接${brand.name}`"
                    @click="openConnect(brand.key)"
                  >
                    <MailboxBrandIcon :brand="brand.key" />
                    <span class="min-w-0 flex flex-col">
                      <span class="truncate text-sm font-semibold text-default">{{ brand.name }}</span>
                      <span class="mt-0.5 truncate text-xs text-muted">{{ brand.tileHint }}</span>
                    </span>
                  </button>
                </div>
              </section>
            </div>
          </BaseCard>
        </template>
      </div>
    </div>

    <ConnectMailboxDialog
      v-model="dialogOpen"
      :brand="dialogBrand"
      :current="settings"
      @connected="onConnected"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, inject, onMounted, ref } from 'vue'
import AppHeader from '@/components/AppHeader.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import MailboxBrandIcon from '@/components/connections/MailboxBrandIcon.vue'
import ConnectMailboxDialog from '@/components/connections/ConnectMailboxDialog.vue'
import { detectMailboxBrand, getMailboxBrand, visibleMailboxGroups } from '@/components/connections/mailboxBrands'
import type { MailboxBrandKey } from '@/components/connections/mailboxBrands'
import { deleteEmailSettings, getEmailSettings } from '@/api/connectMailbox'
import type { EmailSettingsConfig } from '@/api/connectMailbox'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { useToast } from 'vue-toastification'

type EmailSettingsData = EmailSettingsConfig & { updated_at?: string | null }

const toast = useToast()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, init, isInitialized, logout: tenantLogout } = useTenantAuth()
const toggleSidebarFn = inject<() => void>('toggleSidebar')

const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)
const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone
  } : null
})

function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}

function handleLogout() {
  if (tenantLogout) tenantLogout()
}

const groups = visibleMailboxGroups()

const view = ref<'loading' | 'error' | 'ready'>('loading')
const settings = ref<EmailSettingsData | null>(null)

const dialogOpen = ref(false)
const dialogBrand = ref<MailboxBrandKey>('qq')
const disconnecting = ref(false)

const connectedBrand = computed(() => (settings.value ? detectMailboxBrand(settings.value) : 'custom'))
const connectedMeta = computed(() => {
  const name = getMailboxBrand(connectedBrand.value)?.name || ''
  const date = settings.value?.updated_at ? settings.value.updated_at.slice(0, 10) : ''
  return date ? `${name} · 最近连接 ${date}` : name
})

async function load() {
  view.value = 'loading'
  try {
    const result = await getEmailSettings()
    if (result.success) {
      settings.value = result.bound && result.data ? result.data : null
      view.value = 'ready'
    } else {
      settings.value = null
      view.value = 'error'
    }
  } catch {
    settings.value = null
    view.value = 'error'
  }
}

function openConnect(brand: MailboxBrandKey) {
  dialogBrand.value = brand
  dialogOpen.value = true
}

function openReconnect() {
  if (!settings.value) return
  openConnect(detectMailboxBrand(settings.value))
}

function onConnected(address: string) {
  dialogOpen.value = false
  toast.success(`邮箱连接成功，测试邮件已发送至 ${address}`)
  load()
}

async function disconnect() {
  const current = settings.value
  if (!current || disconnecting.value) return
  if (!window.confirm(`断开邮箱连接？\n\n断开后，数字员工将不能再用 ${current.email_address} 收发邮件。`)) return
  disconnecting.value = true
  try {
    const result = await deleteEmailSettings()
    if (result.success) {
      toast.success('已断开邮箱连接')
      await load()
    } else {
      toast.error(result.error || '断开邮箱连接失败')
    }
  } catch {
    toast.error('断开邮箱连接失败')
  } finally {
    disconnecting.value = false
  }
}

onMounted(async () => {
  if (!isInitialized.value) await init()
  await load()
})
</script>
