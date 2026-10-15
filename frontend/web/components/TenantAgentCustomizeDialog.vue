<template>
  <BaseModal v-model="visible" :title="`定制 - ${displayName}`" size="lg">
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
          勾选该数字员工可检索的本租户知识库栏目；不勾选任何栏目时默认可检索全部。
          共享来源由「知识库接入」统一控制，此处只读展示。
        </p>
        <div v-if="kbLoading" class="text-xs text-muted py-2">加载中...</div>
        <template v-else>
          <div v-if="kbCategories.length > 0" class="flex flex-wrap gap-x-5 gap-y-2">
            <label
              v-for="cat in kbCategories"
              :key="cat.source_type"
              class="flex items-center gap-2 text-sm text-default cursor-pointer"
            >
              <input
                v-model="kbChecked"
                type="checkbox"
                :value="cat.source_type"
                class="w-3.5 h-3.5 rounded border-primary-200 text-primary-600 focus:ring-primary-500"
                :disabled="saving"
              />
              {{ cat.display_name || cat.source_type }}
            </label>
          </div>
          <div v-else class="text-xs text-muted py-1">本租户暂无知识库栏目</div>
          <div v-if="kbShared.length > 0" class="mt-2 flex flex-wrap items-center gap-2">
            <span class="text-xs text-muted">共享栏目：</span>
            <BaseBadge v-for="s in kbShared" :key="s.source_type" intent="info">
              {{ s.display_name || s.source_type }}
            </BaseBadge>
          </div>
        </template>
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
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import {
  getTenantAgentInstanceOrDefault,
  updateTenantAgentInstance,
  createTenantAgentInstance,
  deleteTenantAgentInstance,
  type TenantAgentInstance,
  type GreetingPrompt,
} from '@/api/tenantAgentInstances'
import {
  getSubagentKnowledgeSources,
  setSubagentKnowledgeSources,
  listTenantKnowledgeCategories,
  type KnowledgeSourceItem,
} from '@/api/saasPermissions'

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

// 知识库栏目
const kbLoading = ref(false)
const kbCategories = ref<{ source_type: string; display_name: string | null }[]>([])
const kbChecked = ref<string[]>([])
const kbInitialChecked = ref<string[]>([])
const kbShared = ref<KnowledgeSourceItem[]>([])

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

async function loadKnowledge() {
  kbLoading.value = true
  try {
    const [catsRes, srcRes] = await Promise.all([
      listTenantKnowledgeCategories(props.tenantId).catch(() => ({ items: [] })),
      getSubagentKnowledgeSources(props.tenantId, props.agentId).catch(() => ({ success: false, data: [] })),
    ])
    kbCategories.value = (catsRes.items || []).map((c: any) => ({
      source_type: c.source_type,
      display_name: c.display_name,
    }))
    const sources: KnowledgeSourceItem[] = srcRes.data || []
    kbShared.value = sources.filter(s => s.owner_tenant_id)
    const ownChecked = sources
      .filter(s => !s.owner_tenant_id && s.source_type)
      .map(s => s.source_type)
    kbInitialChecked.value = ownChecked
    kbChecked.value = ownChecked
  } finally {
    kbLoading.value = false
  }
}

async function handleSave() {
  if (saving.value) return
  saving.value = true
  errorMsg.value = ''
  try {
    // 知识库栏目单独保存（全量覆盖 own 项，共享项原样保留）
    const ownItems: KnowledgeSourceItem[] = kbChecked.value.map(st => ({
      source_type: st,
      display_name: kbCategories.value.find(c => c.source_type === st)?.display_name || st,
      owner_tenant_id: null,
    }))
    const kbChanged =
      JSON.stringify([...kbChecked.value].sort()) !== JSON.stringify([...kbInitialChecked.value].sort())
    if (kbChanged) {
      await setSubagentKnowledgeSources(props.tenantId, props.agentId, [...ownItems, ...kbShared.value])
    }

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
    loadKnowledge()
  }
})
</script>
