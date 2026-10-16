<template>
  <BaseModal v-model="visible" :title="`定制 - ${displayName}`" size="lgx">
    <div class="space-y-5">
      <!-- 基础信息 -->
      <section>
        <h4 class="text-sm font-semibold text-default mb-2">基础信息</h4>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label class="text-sm text-muted mb-1 block">显示名（留空继承父定义）</label>
            <BaseInput v-model="form.display_name" placeholder="留空则使用默认名称" :disabled="saving" />
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">简介描述（留空继承父定义）</label>
            <BaseInput v-model="form.description" placeholder="留空则使用默认描述" :disabled="saving" />
          </div>
        </div>
      </section>

      <!-- 扩展提示词 -->
      <section>
        <h4 class="text-sm font-semibold text-default mb-2">扩展提示词</h4>
        <p class="text-xs text-muted mb-2 leading-relaxed">
          追加到该数字员工系统提示词末尾的本租户定制需求，留空使用默认配置。
        </p>
        <textarea
          v-model="form.extra_prompt"
          rows="5"
          placeholder="例如：&#10;- 回复中必须使用「贵司」而非「你」&#10;- 所有报价保留两位小数"
          class="w-full px-3 py-2 rounded-lg border border-default bg-surface text-sm text-default font-mono focus:border-primary-400 focus:ring-1 focus:ring-primary-400 outline-none resize-y"
          :disabled="saving"
        />
      </section>

      <!-- 知识库栏目 -->
      <section>
        <h4 class="text-sm font-semibold text-default mb-2">知识库栏目</h4>
        <p class="text-xs text-muted mb-2 leading-relaxed">
          该数字员工可检索的知识库栏目与共享来源，统一在知识库页面维护。
        </p>
        <BaseButton intent="secondary" size="sm" @click="openKnowledgePage">
          跳转知识库
        </BaseButton>
      </section>

      <!-- 空态简介与快捷按钮 -->
      <section>
        <h4 class="text-sm font-semibold text-default mb-2">新会话简介与快捷按钮</h4>
        <p class="text-xs text-muted mb-2 leading-relaxed">
          覆盖该数字员工新会话页展示的能力摘要与快捷操作按钮；留空由 AI 自动生成。
        </p>
        <div class="mb-3">
          <label class="text-sm text-muted mb-1 block">能力摘要（留空 AI 生成）</label>
          <textarea
            v-model="form.greeting_summary"
            rows="2"
            maxlength="500"
            class="w-full px-3 py-2 rounded-lg border border-default bg-surface text-sm text-default focus:border-primary-400 focus:ring-1 focus:ring-primary-400 outline-none resize-y"
            :disabled="saving"
          />
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div v-for="(p, i) in greetingPrompts" :key="i" class="space-y-2">
            <label class="text-sm text-muted mb-0 block">快捷按钮 {{ i + 1 }}（留空不显示）</label>
            <BaseInput v-model="p.label" placeholder="按钮文案，≤6 字" maxlength="20" :disabled="saving" />
            <BaseInput v-model="p.message" placeholder="点击后发送的完整指令" maxlength="200" :disabled="saving" />
          </div>
        </div>
      </section>

      <!-- 错误提示 -->
      <div v-if="errorMsg" class="bg-danger-50 border border-danger-200 rounded-lg p-3 text-sm text-danger-700">
        {{ errorMsg }}
      </div>
    </div>

    <template #footer>
      <div class="w-full flex items-center gap-2">
        <!-- 副本操作：复制来源可为基础员工或本租户实例（克隆其覆盖项） -->
        <BaseButton intent="secondary" size="sm" :disabled="copying" @click="handleCopy">
          {{ copying ? '复制中...' : '复制为副本' }}
        </BaseButton>
        <BaseInput v-model="copyName" placeholder="副本显示名" size="sm" class="w-40" :disabled="copying" />
        <BaseButton
          v-if="isCopy"
          intent="danger-ghost"
          size="sm"
          :disabled="deleting"
          @click="handleDelete"
        >
          {{ deleting ? '删除中...' : '删除副本' }}
        </BaseButton>
        <div class="flex-1" />
        <BaseButton intent="secondary" @click="handleClose">取消</BaseButton>
        <BaseButton :disabled="saving" @click="handleSave">
          {{ saving ? '保存中...' : '保存' }}
        </BaseButton>
      </div>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import {
  getTenantAgentInstanceOrDefault,
  updateTenantAgentInstance,
  createTenantAgentInstance,
  deleteTenantAgentInstance,
  type TenantAgentInstance,
  type GreetingPrompt,
} from '@/api/tenantAgentInstances'

const props = defineProps<{
  modelValue: boolean
  tenantId: string
  /** 目标 agent_id：基础员工 id 或副本实例 id */
  agentId: string
  /** 卡片展示名（无覆盖时即父定义名） */
  agentName: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  /** 保存/复制/删除成功后通知父页面刷新列表 */
  changed: []
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const displayName = computed(() => form.value.display_name || props.agentName)
const isCopy = ref(false)
const saving = ref(false)
const copying = ref(false)
const deleting = ref(false)
const errorMsg = ref('')

const form = ref({
  display_name: '',
  description: '',
  extra_prompt: '',
  greeting_summary: '',
})
const greetingPrompts = ref<GreetingPrompt[]>([
  { label: '', message: '' },
  { label: '', message: '' },
])
const copyName = ref('')

const router = useRouter()

function openKnowledgePage() {
  const { href } = router.resolve(`/t/${props.tenantId}/knowledge`)
  window.open(href, '_blank')
}

async function loadInstance() {
  errorMsg.value = ''
  const inst: TenantAgentInstance | null = await getTenantAgentInstanceOrDefault(
    props.tenantId, props.agentId,
  )
  isCopy.value = inst ? inst.is_copy : isLikelyCopy()
  form.value = {
    display_name: inst?.display_name || '',
    description: inst?.description || '',
    extra_prompt: inst?.extra_prompt || '',
    greeting_summary: inst?.greeting_summary || '',
  }
  const gp = inst?.greeting_prompts
  greetingPrompts.value = [0, 1].map(i => ({
    label: gp?.[i]?.label || '',
    message: gp?.[i]?.message || '',
  }))
}

// 实例行未落库时（基础员工未定制过）按 id 猜测副本标记仅作展示兜底；
// 后端权威判定以保存后返回为准
function isLikelyCopy(): boolean {
  return props.agentId !== props.agentName && /-[a-z0-9-]{2,}$/.test(props.agentId)
}

async function handleSave() {
  if (saving.value) return
  saving.value = true
  errorMsg.value = ''
  try {
    const prompts: GreetingPrompt[] = greetingPrompts.value
      .map(p => ({ label: p.label.trim(), message: p.message.trim() }))
      .filter(p => p.label && p.message)

    await updateTenantAgentInstance(props.agentId, {
      // 空串=清除覆盖恢复继承；null=不改（后端契约）
      display_name: form.value.display_name.trim(),
      description: form.value.description.trim(),
      extra_prompt: form.value.extra_prompt,
      greeting_summary: form.value.greeting_summary.trim(),
      greeting_prompts: prompts,
    })
    emit('changed')
    visible.value = false
  } catch (e: any) {
    errorMsg.value = e.message || '保存失败'
  } finally {
    saving.value = false
  }
}

async function handleCopy() {
  const name = copyName.value.trim()
  if (!name) {
    errorMsg.value = '请填写副本显示名'
    return
  }
  copying.value = true
  errorMsg.value = ''
  try {
    await createTenantAgentInstance(props.agentId, name)
    emit('changed')
    visible.value = false
  } catch (e: any) {
    errorMsg.value = e.message || '复制失败'
  } finally {
    copying.value = false
  }
}

async function handleDelete() {
  if (!confirm('确定删除该副本？删除后新会话无法继续使用。')) return
  deleting.value = true
  errorMsg.value = ''
  try {
    await deleteTenantAgentInstance(props.agentId)
    emit('changed')
    visible.value = false
  } catch (e: any) {
    errorMsg.value = e.message || '删除失败'
  } finally {
    deleting.value = false
  }
}

function handleClose() {
  visible.value = false
}

watch(visible, (v) => {
  if (v) {
    copyName.value = ''
    loadInstance()
  }
})
</script>
