<template>
  <main class="login-layout">
    <section class="login-context" aria-labelledby="login-product-title">
      <div class="login-wordmark"><span>A</span> AID WORK AGENT</div>
      <div>
        <p class="page-kicker">SECURE DESKTOP CLIENT</p>
        <h1 id="login-product-title">让任务在正确的<br>执行边界内完成。</h1>
        <p class="login-intro">连接企业工作空间。凭证由操作系统安全存储保护，不会写入浏览器存储。</p>
      </div>
      <dl class="login-facts">
        <div><dt>传输</dt><dd>HTTPS / 企业策略</dd></div>
        <div><dt>凭证</dt><dd>系统安全存储</dd></div>
        <div><dt>构建</dt><dd>Desktop 独立入口</dd></div>
      </dl>
    </section>
    <section class="login-panel" aria-labelledby="login-title">
      <form ref="formElement" class="login-form" @submit.prevent="submit">
        <div><p class="form-step">01 / 身份验证</p><h2 id="login-title">登录企业工作空间</h2><p>使用租户代码和员工账户继续。</p></div>
        <div v-if="offline" class="inline-notice warning" role="status">当前无法连接服务。你可以填写信息，恢复连接后再登录。<button type="button" class="status-action" @click="controller.retryConnectivity">重试连接</button></div>
        <div class="field"><label for="tenant-code">租户代码</label><input id="tenant-code" v-model.trim="form.tenantCode" maxlength="8" autocomplete="organization" spellcheck="false" placeholder="例如 ALIBB" :aria-invalid="Boolean(errors.tenant_code)" :aria-describedby="errors.tenant_code ? 'tenant-code-error' : undefined" @input="clear('tenant_code')"><span v-if="errors.tenant_code" id="tenant-code-error" class="field-error">{{ errors.tenant_code }}</span></div>
        <div class="field"><label for="identifier">手机号或用户名</label><input id="identifier" v-model.trim="form.identifier" autocomplete="username" placeholder="输入员工账户" :aria-invalid="Boolean(errors.identifier)" :aria-describedby="errors.identifier ? 'identifier-error' : undefined" @input="clear('identifier')"><span v-if="errors.identifier" id="identifier-error" class="field-error">{{ errors.identifier }}</span></div>
        <div class="field"><label for="password">密码</label><input id="password" v-model="form.password" type="password" autocomplete="current-password" placeholder="输入密码" :aria-invalid="Boolean(errors.password)" :aria-describedby="errors.password ? 'password-error' : undefined" @input="clear('password')"><span v-if="errors.password" id="password-error" class="field-error">{{ errors.password }}</span></div>
        <div class="field"><label for="captcha">图形验证码</label><div class="captcha-row"><input id="captcha" v-model.trim="form.captchaCode" maxlength="4" autocomplete="off" placeholder="4 位字符" :aria-invalid="Boolean(errors.captcha_code)" :aria-describedby="errors.captcha_code ? 'captcha-error' : undefined" @input="clear('captcha_code')"><button type="button" class="captcha-box" :aria-label="captchaSvg ? '刷新图形验证码' : '重新加载图形验证码'" @click="loadCaptcha"><img v-if="captchaSvg" :src="`data:image/svg+xml;base64,${captchaSvg}`" alt="图形验证码"><span v-else>{{ captchaLoading ? '载入中' : '重新加载' }}</span></button></div><span v-if="errors.captcha_code" id="captcha-error" class="field-error">{{ errors.captcha_code }}</span></div>
        <div v-if="generalError" class="inline-notice error" role="alert">{{ generalError }}</div>
        <button class="primary-button" type="submit" :disabled="submitting || offline">{{ submitting ? '正在验证…' : '安全登录' }}</button>
        <p class="support-copy">忘记密码？请联系企业管理员重置。</p>
      </form>
    </section>
  </main>
</template>

<script setup lang="ts">
import { inject, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { desktopControllerKey } from '@desktop/app/context'
import type { AuthFieldError } from '@shared/auth/contracts'

const props = defineProps<{ offline: boolean }>()
const injectedController = inject(desktopControllerKey)
if (!injectedController) throw new Error('Desktop controller is unavailable')
const controller = injectedController
const form = reactive({ tenantCode: '', identifier: '', password: '', captchaCode: '', captchaId: '' })
const errors = reactive<Record<string, string>>({})
const generalError = ref('')
const captchaSvg = ref('')
const captchaLoading = ref(false)
const submitting = ref(false)
const formElement = ref<HTMLFormElement | null>(null)

function clear(field: string) { delete errors[field]; generalError.value = '' }
function validate(): boolean {
  if (!/^[A-Za-z0-9]{4,8}$/.test(form.tenantCode)) errors.tenant_code = '请输入 4–8 位字母或数字'
  if (!form.identifier) errors.identifier = '请输入手机号或用户名'
  if (!form.password) errors.password = '请输入密码'
  if (!form.captchaCode) errors.captcha_code = '请输入图形验证码'
  return Object.keys(errors).length === 0
}
async function focusFirstError() {
  await nextTick()
  formElement.value?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus()
}
async function loadCaptcha() {
  captchaLoading.value = true
  try {
    const response = await controller.auth.captcha()
    if (!response.success || !response.captcha_id || !response.svg_base64) throw new Error(response.message || '验证码加载失败')
    form.captchaId = response.captcha_id
    captchaSvg.value = response.svg_base64
  } catch { generalError.value = '验证码加载失败，请检查网络后重试。' } finally { captchaLoading.value = false }
}
async function submit() {
  Object.keys(errors).forEach((key) => delete errors[key])
  generalError.value = ''
  if (!validate() || props.offline) { await focusFirstError(); return }
  submitting.value = true
  try {
    const session = await controller.auth.login({ tenant_code: form.tenantCode.toUpperCase(), identifier: form.identifier, password: form.password, captcha_code: form.captchaCode, captcha_id: form.captchaId })
    controller.authenticated(session)
  } catch (error) {
    const fieldErrors = (error as Error & { fieldErrors?: AuthFieldError[] }).fieldErrors ?? []
    for (const fieldError of fieldErrors) errors[fieldError.field] = fieldError.message
    if (fieldErrors.length === 0) generalError.value = error instanceof Error ? error.message : '登录失败，请重试。'
    await focusFirstError()
    form.captchaCode = ''
    await loadCaptcha()
  } finally { submitting.value = false }
}
onMounted(() => { if (!props.offline) void loadCaptcha() })
watch(() => props.offline, (offline, wasOffline) => { if (wasOffline && !offline && !form.captchaId) void loadCaptcha() })
</script>
