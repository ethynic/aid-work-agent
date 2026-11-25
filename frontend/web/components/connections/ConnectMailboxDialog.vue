<template>
  <BaseModal
    :model-value="modelValue"
    class="px-4"
    :size="brand.kind === 'custom' ? 'lg' : 'md'"
    accessible
    :show-fullscreen="false"
    @update:model-value="onCloseRequest"
  >
    <template #title>
      <span class="inline-flex items-center gap-2">
        <MailboxBrandIcon :brand="brand.key" :size="32" />
        <span>连接{{ titleGap }}{{ brand.name }}</span>
      </span>
    </template>

    <div class="space-y-4">
      <!-- 失败提示：全部服务器尝试失败后出现，放在最上面保证可见 -->
      <div v-if="failure" role="alert" class="flex items-start gap-2 rounded-lg border border-danger-200 bg-danger-50 p-3 text-sm leading-6 text-danger-700">
        <svg class="mt-0.5 h-4 w-4 flex-shrink-0" fill="none" stroke="currentColor" stroke-width="1.7" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v4M12 16.5h.01M10.3 3.9L2.6 17a2 2 0 001.7 3h15.4a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z" />
        </svg>
        <div class="min-w-0">
          <p class="font-semibold">连接失败：{{ failure.title }}</p>
          <p class="mt-0.5">{{ failure.text }}</p>
          <button
            v-if="failure.helpLinkText"
            type="button"
            class="mt-1 rounded text-danger-600 hover:underline focus:outline-none focus-visible:ring-1 focus-visible:ring-danger-400"
            @click="expandHelp"
          >{{ failure.helpLinkText }}</button>
        </div>
      </div>

      <!-- 替换提示 -->
      <div v-if="showReplace" class="rounded-lg bg-warning-50 p-3 text-sm leading-5 text-warning-800">
        连接成功后将替换当前已连接的 {{ current!.email_address }}。
      </div>

      <!-- 账号 / 地址 -->
      <div>
        <label class="mb-1 block text-sm text-default" :for="brand.kind === 'suffix' ? 'mailbox-account' : 'mailbox-address'">
          {{ brand.accountLabel }}
        </label>
        <div v-if="brand.kind === 'suffix'" class="relative">
          <BaseInput
            id="mailbox-account"
            v-model="form.account"
            class="w-full pr-20"
            autocomplete="off"
            spellcheck="false"
            :placeholder="brand.accountPlaceholder"
            :state="errors.account ? 'error' : 'default'"
            :disabled="connecting"
            :aria-describedby="errors.account ? 'mailbox-account-error' : undefined"
            @update:model-value="clearError('account')"
            @keydown.enter="submit"
          />
          <span v-if="!form.account.includes('@')" class="pointer-events-none absolute inset-y-0 right-3 flex items-center text-sm text-muted">@{{ brand.domain }}</span>
        </div>
        <template v-else>
          <BaseInput
            id="mailbox-address"
            v-model="form.account"
            class="w-full"
            autocomplete="off"
            spellcheck="false"
            :placeholder="brand.accountPlaceholder"
            :state="errors.address ? 'error' : 'default'"
            :disabled="connecting"
            :aria-describedby="errors.address ? 'mailbox-address-error' : undefined"
            @update:model-value="clearError('address')"
            @keydown.enter="submit"
          />
          <p v-if="errors.address" id="mailbox-address-error" class="mt-1 text-xs text-danger-600">{{ errors.address }}</p>
        </template>
        <p v-if="brand.kind === 'suffix' && errors.account" id="mailbox-account-error" class="mt-1 text-xs text-danger-600">{{ errors.account }}</p>
        <p v-else-if="brand.kind === 'suffix' && resolvedAddress" class="mt-1 text-xs text-muted">将连接 {{ resolvedAddress }}</p>
      </div>

      <!-- 密码 / 授权码 -->
      <div>
        <label class="mb-1 block text-sm text-default" for="mailbox-secret">{{ brand.secretLabel }}</label>
        <BaseInput
          id="mailbox-secret"
          v-model="form.secret"
          type="password"
          autocomplete="new-password"
          class="w-full"
          :placeholder="brand.secretPlaceholder"
          :state="errors.secret ? 'error' : 'default'"
          :disabled="connecting"
          :aria-describedby="errors.secret ? 'mailbox-secret-error' : undefined"
          @update:model-value="clearError('secret')"
          @keydown.enter="submit"
        />
        <p v-if="errors.secret" id="mailbox-secret-error" class="mt-1 text-xs text-danger-600">{{ errors.secret }}</p>
        <p v-else-if="brand.secretHint" class="mt-1 text-xs text-muted">{{ brand.secretHint }}</p>
      </div>

      <!-- 自定义邮箱：发件 / 收件服务器 -->
      <template v-if="brand.kind === 'custom'">
        <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label class="mb-1 block text-sm text-default" for="mailbox-smtp-server">发件服务器（SMTP）</label>
            <BaseInput
              id="mailbox-smtp-server"
              v-model="form.smtpServer"
              class="w-full"
              autocomplete="off"
              spellcheck="false"
              placeholder="smtp.example.com"
              :state="errors.smtp ? 'error' : 'default'"
              :disabled="connecting"
              :aria-describedby="errors.smtp ? 'mailbox-smtp-error' : undefined"
              @update:model-value="clearError('smtp')"
            />
            <p v-if="errors.smtp" id="mailbox-smtp-error" class="mt-1 text-xs text-danger-600">{{ errors.smtp }}</p>
          </div>
          <div>
            <label class="mb-1 block text-sm text-default" for="mailbox-imap-server">收件服务器（IMAP）</label>
            <BaseInput
              id="mailbox-imap-server"
              v-model="form.imapServer"
              class="w-full"
              autocomplete="off"
              spellcheck="false"
              placeholder="imap.example.com"
              :state="errors.imap ? 'error' : 'default'"
              :disabled="connecting"
              :aria-describedby="errors.imap ? 'mailbox-imap-error' : undefined"
              @update:model-value="clearError('imap')"
            />
            <p v-if="errors.imap" id="mailbox-imap-error" class="mt-1 text-xs text-danger-600">{{ errors.imap }}</p>
          </div>
        </div>

        <!-- 高级设置 -->
        <div class="rounded-lg border border-default">
          <button
            type="button"
            class="flex w-full items-center gap-2 rounded-lg px-3 py-2.5 text-left text-sm text-default hover:bg-surface-hover focus:outline-none focus-visible:ring-1 focus-visible:ring-primary-400"
            :aria-expanded="advOpen"
            @click="advOpen = !advOpen"
          >
            <svg class="h-4 w-4 flex-shrink-0 text-muted transition-transform" :class="advOpen ? 'rotate-90' : ''" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
            </svg>
            <span>高级设置</span>
            <span class="ml-auto min-w-0 text-right text-xs text-muted">端口、加密方式、SMTP 用户名，通常无需修改</span>
          </button>
          <div v-show="advOpen" class="border-t border-default p-3">
            <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <label class="mb-1 block text-sm text-default" for="mailbox-smtp-port">SMTP 端口</label>
                <BaseInput
                  id="mailbox-smtp-port"
                  v-model="form.smtpPort"
                  class="w-full"
                  inputmode="numeric"
                  :state="errors.smtpPort ? 'error' : 'default'"
                  :disabled="connecting"
                  :aria-describedby="errors.smtpPort ? 'mailbox-smtp-port-error' : undefined"
                  @update:model-value="clearError('smtpPort')"
                />
                <p v-if="errors.smtpPort" id="mailbox-smtp-port-error" class="mt-1 text-xs text-danger-600">{{ errors.smtpPort }}</p>
              </div>
              <div>
                <label class="mb-1 block text-sm text-default" for="mailbox-smtp-encryption">SMTP 加密</label>
                <BaseSelect id="mailbox-smtp-encryption" v-model="form.smtpEncryption" :disabled="connecting">
                  <option v-for="option in ENCRYPTION_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
                </BaseSelect>
              </div>
              <div>
                <label class="mb-1 block text-sm text-default" for="mailbox-imap-port">IMAP 端口</label>
                <BaseInput
                  id="mailbox-imap-port"
                  v-model="form.imapPort"
                  class="w-full"
                  inputmode="numeric"
                  :state="errors.imapPort ? 'error' : 'default'"
                  :disabled="connecting"
                  :aria-describedby="errors.imapPort ? 'mailbox-imap-port-error' : undefined"
                  @update:model-value="clearError('imapPort')"
                />
                <p v-if="errors.imapPort" id="mailbox-imap-port-error" class="mt-1 text-xs text-danger-600">{{ errors.imapPort }}</p>
              </div>
              <div>
                <label class="mb-1 block text-sm text-default" for="mailbox-imap-encryption">IMAP 加密</label>
                <BaseSelect id="mailbox-imap-encryption" v-model="form.imapEncryption" :disabled="connecting">
                  <option v-for="option in ENCRYPTION_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
                </BaseSelect>
              </div>
              <div class="sm:col-span-2">
                <label class="mb-1 block text-sm text-default" for="mailbox-smtp-user">SMTP 用户名</label>
                <BaseInput
                  id="mailbox-smtp-user"
                  v-model="form.smtpUser"
                  class="w-full"
                  autocomplete="off"
                  spellcheck="false"
                  placeholder="默认与邮箱地址相同"
                  :disabled="connecting"
                />
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- 步骤说明 -->
      <HelpExpand ref="helpExpand" v-if="brand.help" v-model:open="helpOpen" :label="brand.helpLabel">
        <ol class="list-decimal space-y-0.5 pl-4">
          <li v-for="step in brand.help.steps" :key="step">{{ step }}</li>
        </ol>
        <p v-if="brand.help.extra" class="mt-2 border-t border-dashed border-default pt-2">{{ brand.help.extra }}</p>
        <a :href="brand.help.href" target="_blank" rel="noopener noreferrer" class="mt-2 inline-block text-primary-600 hover:underline">{{ brand.help.linkText }}</a>
      </HelpExpand>

      <!-- 测试邮件说明 -->
      <div class="rounded-lg border border-default bg-canvas p-3 text-xs leading-5 text-muted">
        {{ brand.noticePrefix || '' }}点击「连接」后会向该邮箱发送一封测试邮件，发送成功即连接成功。
      </div>
    </div>

    <template #footer>
      <div class="flex justify-end gap-2">
        <BaseButton intent="secondary" :disabled="connecting" @click="close">取消</BaseButton>
        <BaseButton :disabled="connecting" @click="submit">
          <svg v-if="connecting" class="mr-1 h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          {{ mainButtonText }}
        </BaseButton>
      </div>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import HelpExpand from '@/components/ui/HelpExpand.vue'
import MailboxBrandIcon from '@/components/connections/MailboxBrandIcon.vue'
import {
  buildMailboxConfigs,
  detectMailboxBrand,
  getMailboxBrand,
  pickMailboxFailure,
  resolveMailboxAddress,
} from '@/components/connections/mailboxBrands'
import type { MailboxBrand, MailboxBrandKey, MailboxConnectForm, MailboxFailure } from '@/components/connections/mailboxBrands'
import { saveEmailSettings } from '@/api/connectMailbox'
import type { EmailSettingsConfig, MailboxSaveErrorCode } from '@/api/connectMailbox'

const props = defineProps<{
  modelValue: boolean
  brand: MailboxBrandKey
  /** 当前已连接的配置：用于带出地址和显示替换提示 */
  current: EmailSettingsConfig | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  connected: [address: string]
}>()

const ENCRYPTION_OPTIONS = [
  { value: 'ssl', label: 'SSL（465/993）' },
  { value: 'tls', label: 'STARTTLS（587/143）' },
  { value: 'none', label: '无加密（不推荐）' },
] as const

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const brand = computed(() => getMailboxBrand(props.brand) as MailboxBrand)

const form = reactive<MailboxConnectForm>({
  account: '',
  secret: '',
  smtpServer: '',
  imapServer: '',
  smtpPort: '465',
  smtpEncryption: 'ssl',
  imapPort: '993',
  imapEncryption: 'ssl',
  smtpUser: '',
})

const errors = reactive<Record<string, string>>({})
const failure = ref<MailboxFailure | null>(null)
const helpOpen = ref(false)
const advOpen = ref(false)
const connecting = ref(false)
const helpExpand = ref<InstanceType<typeof HelpExpand> | null>(null)

/** 失败提示里点「查看步骤」：展开后把步骤说明滚进可视区，小屏上才看得见有反应 */
async function expandHelp() {
  helpOpen.value = true
  await nextTick()
  ;(helpExpand.value?.$el as HTMLElement | undefined)?.scrollIntoView({ block: 'nearest' })
}

const titleGap = computed(() => (/^[A-Za-z0-9]/.test(brand.value.name) ? ' ' : ''))
const resolvedAddress = computed(() => resolveMailboxAddress(brand.value, form.account))
const showReplace = computed(() => !!props.current && resolvedAddress.value.toLowerCase() !== props.current.email_address.toLowerCase())
const mainButtonText = computed(() => (connecting.value ? '连接中…' : failure.value ? '重试' : '连接'))

function resetForm() {
  Object.assign(form, {
    account: '', secret: '', smtpServer: '', imapServer: '',
    smtpPort: '465', smtpEncryption: 'ssl', imapPort: '993', imapEncryption: 'ssl', smtpUser: '',
  })
  for (const key of Object.keys(errors)) delete errors[key]
  failure.value = null
  helpOpen.value = false
  advOpen.value = false

  const current = props.current
  if (current && detectMailboxBrand(current) === props.brand) {
    form.account = current.email_address
    if (brand.value.kind === 'custom') {
      form.smtpServer = current.smtp_server
      form.imapServer = current.imap_server
      form.smtpPort = String(current.smtp_port)
      form.imapPort = String(current.imap_port)
      form.smtpEncryption = current.smtp_encryption
      form.imapEncryption = current.imap_encryption
      form.smtpUser = current.smtp_user
    }
  }
}

function clearForm() {
  Object.assign(form, {
    account: '', secret: '', smtpServer: '', imapServer: '',
    smtpPort: '465', smtpEncryption: 'ssl', imapPort: '993', imapEncryption: 'ssl', smtpUser: '',
  })
  for (const key of Object.keys(errors)) delete errors[key]
}

watch(() => props.modelValue, (open) => {
  if (open) {
    resetForm()
    // BaseModal 打开时会把焦点移到弹窗容器上，等它完成后再把焦点交给目标输入框
    window.setTimeout(focusInitialField)
  } else {
    clearForm()
  }
})

function focusInitialField() {
  const sameBrand = !!props.current && detectMailboxBrand(props.current) === props.brand
  const targetId = sameBrand ? 'mailbox-secret' : brand.value.kind === 'suffix' ? 'mailbox-account' : 'mailbox-address'
  document.getElementById(targetId)?.focus()
}

function clearError(key: string) {
  delete errors[key]
}

function onCloseRequest(value: boolean) {
  if (connecting.value) return
  emit('update:modelValue', value)
}

function close() {
  if (connecting.value) return
  emit('update:modelValue', false)
}

const FIELD_IDS: Record<string, string> = {
  account: 'mailbox-account',
  address: 'mailbox-address',
  secret: 'mailbox-secret',
  smtp: 'mailbox-smtp-server',
  imap: 'mailbox-imap-server',
  smtpPort: 'mailbox-smtp-port',
  imapPort: 'mailbox-imap-port',
}

function isValidPort(value: string): boolean {
  return /^\d{1,5}$/.test(value) && Number(value) >= 1 && Number(value) <= 65535
}

function validate(): Record<string, string> {
  const errs: Record<string, string> = {}
  const account = form.account.trim()
  const secret = form.secret.trim()

  if (brand.value.kind === 'suffix') {
    if (!account) errs.account = '请输入邮箱账号'
    else if (account.includes('@')) {
      const domain = account.slice(account.indexOf('@') + 1).toLowerCase()
      if (domain !== brand.value.domain) {
        errs.account = `这里只能连接${titleGap.value}${brand.value.name}，其他邮箱请选择对应的品牌或「自定义邮箱」`
      }
    }
    if (!secret) errs.secret = brand.value.secretRequiredText || '请输入密码或授权码'
    return errs
  }

  if (!account) errs.address = '请输入邮箱地址'
  else if (!EMAIL_RE.test(account)) errs.address = '请输入完整的邮箱地址，如 name@company.com'
  if (!secret) errs.secret = brand.value.secretRequiredText || '请输入密码或授权码'

  if (brand.value.kind === 'custom') {
    if (!form.smtpServer.trim()) errs.smtp = '请输入发件服务器地址'
    if (!form.imapServer.trim()) errs.imap = '请输入收件服务器地址'
    if (!isValidPort(form.smtpPort.trim())) errs.smtpPort = '请输入 1–65535 的端口号'
    if (!isValidPort(form.imapPort.trim())) errs.imapPort = '请输入 1–65535 的端口号'
  }
  return errs
}

async function submit() {
  if (connecting.value) return

  const found = validate()
  for (const key of Object.keys(errors)) delete errors[key]
  Object.assign(errors, found)
  const firstKey = Object.keys(found)[0]
  if (firstKey) {
    document.getElementById(FIELD_IDS[firstKey])?.focus()
    return
  }

  // 即将发起新一轮尝试：先清掉上一轮的失败提示，尝试中不再显示旧的失败原因
  failure.value = null
  connecting.value = true
  const errorCodes: MailboxSaveErrorCode[] = []
  let connectedAddress: string | null = null
  for (const attempt of buildMailboxConfigs(brand.value, form)) {
    try {
      const result = await saveEmailSettings(attempt)
      if (result.success) {
        connectedAddress = attempt.email_address
        break
      }
      errorCodes.push(result.error_code ?? 'other')
    } catch {
      errorCodes.push('network')
    }
  }
  connecting.value = false

  if (connectedAddress) {
    emit('connected', connectedAddress)
    return
  }
  failure.value = pickMailboxFailure(brand.value.key, errorCodes)
}
</script>
