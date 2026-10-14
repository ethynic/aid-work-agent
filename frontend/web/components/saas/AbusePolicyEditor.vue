<template>
  <div class="space-y-4">
    <div v-if="loading" class="text-center py-6 text-muted text-sm">加载中...</div>
    <template v-else>
      <!-- 规则勾选 -->
      <div>
        <label class="text-sm text-muted mb-1 block">启用规则</label>
        <div v-if="rules.length === 0" class="text-sm text-muted">暂无可选规则</div>
        <div v-else class="space-y-2">
          <div v-for="rule in rules" :key="rule.rule_id" class="border border-default rounded-lg p-3">
            <label class="flex items-start cursor-pointer">
              <input
                type="checkbox"
                :checked="isChecked(rule.rule_id)"
                @change="() => toggleRule(rule)"
                class="mt-0.5 w-3.5 h-3.5 rounded border-primary-200 text-primary-600 focus:ring-primary-500"
              />
              <div class="ml-3 flex-1">
                <div class="flex items-center gap-2">
                  <span class="text-sm font-medium text-default">{{ rule.name }}</span>
                  <BaseBadge :intent="rule.type === 'hard' ? 'danger' : 'warning'">
                    {{ rule.type === 'hard' ? '硬规则' : '软规则' }}
                  </BaseBadge>
                </div>
                <div v-if="rule.description" class="text-xs text-muted mt-0.5">{{ rule.description }}</div>

                <!-- 参数编辑（勾选后展开） -->
                <div v-if="isChecked(rule.rule_id) && visibleParams(rule).length > 0" class="mt-3 grid gap-3" :style="{ gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))' }">
                  <div v-for="field in visibleParams(rule)" :key="field.key">
                    <label class="text-xs text-muted mb-1 block">
                      {{ field.label }}<span v-if="field.required" class="text-danger-500">*</span>
                    </label>
                    <BaseSelect
                      v-if="field.type === 'enum'"
                      :model-value="String(getParam(rule.rule_id, field.key))"
                      size="sm"
                      @update:model-value="(v: string) => setParam(rule.rule_id, field.key, v)"
                    >
                      <option v-for="opt in field.options || []" :key="opt" :value="opt">{{ enumOptionLabel(field.key, opt) }}</option>
                    </BaseSelect>
                    <BaseInput
                      v-else
                      type="number"
                      size="sm"
                      :model-value="String(getParam(rule.rule_id, field.key) ?? '')"
                      :placeholder="field.default != null ? String(field.default) : ''"
                      @update:model-value="(v: string) => setParam(rule.rule_id, field.key, v === '' ? '' : Number(v))"
                    />
                    <p v-if="field.description" class="text-xs text-muted mt-0.5">{{ field.description }}</p>
                  </div>
                </div>
              </div>
            </label>
          </div>
        </div>
      </div>

      <div v-if="error" class="p-2 bg-danger-50 border border-danger-200 rounded text-danger-600 text-sm">{{ error }}</div>

      <div class="flex items-center gap-2">
        <BaseButton size="sm" :disabled="saving" @click="handleSave">{{ saving ? '保存中...' : '保存策略' }}</BaseButton>
        <span v-if="isPlatformDefault" class="text-xs text-muted">当前未自定义，生效的是平台默认策略</span>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import {
  getAbusePresets,
  getAbusePolicies,
  putAbusePolicies,
  type AbuseRule,
  type AbusePolicyEntry,
} from '@/api/abuse'

const props = defineProps<{
  scopeType: 'tenant' | 'user' | 'channel'
  scopeId?: string
}>()

const emit = defineEmits<{ (e: 'saved'): void }>()

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const rules = ref<AbuseRule[]>([])
const form = ref<{ mode: 'observe' | 'enforce'; policies: AbusePolicyEntry[] }>({
  mode: 'observe',
  policies: [],
})
const isPlatformDefault = ref(false)

// Phase 2 预留参数（如硬限线）不在编辑器展示
function visibleParams(rule: AbuseRule): AbuseRule['params_schema'] {
  return rule.params_schema.filter((f) => !f.phase || f.phase < 2)
}

// 枚举参数选项中文显示；未映射的枚举键/值回退原始值
const ENUM_OPTION_LABELS: Record<string, Record<string, string>> = {
  period: { hour: '每小时', day: '每天' },
}

function enumOptionLabel(fieldKey: string, opt: string): string {
  return ENUM_OPTION_LABELS[fieldKey]?.[opt] ?? opt
}

function isChecked(ruleId: string): boolean {
  return form.value.policies.some((p) => p.rule_id === ruleId)
}

function toggleRule(rule: AbuseRule) {
  const idx = form.value.policies.findIndex((p) => p.rule_id === rule.rule_id)
  if (idx >= 0) {
    form.value.policies.splice(idx, 1)
  } else {
    form.value.policies.push({ rule_id: rule.rule_id, params: { ...rule.default_params } })
  }
}

function getParam(ruleId: string, key: string): string | number {
  const entry = form.value.policies.find((p) => p.rule_id === ruleId)
  return (entry?.params?.[key] ?? '') as string | number
}

function setParam(ruleId: string, key: string, value: string | number) {
  const entry = form.value.policies.find((p) => p.rule_id === ruleId)
  if (entry) {
    entry.params = { ...entry.params, [key]: value }
  }
}

async function loadData() {
  loading.value = true
  error.value = ''
  try {
    const [presetsRes, policiesRes] = await Promise.all([
      getAbusePresets(),
      getAbusePolicies(props.scopeType, props.scopeId),
    ])
    rules.value = presetsRes.rules || []
    form.value = {
      // Phase 1 仅观察模式：忽略已存的 enforce，避免编辑器隐藏模式项后静默保存旧值
      mode: 'observe',
      policies: (policiesRes.policies || []).map((p: AbusePolicyEntry) => ({
        rule_id: p.rule_id,
        params: { ...(p.params || {}) },
      })),
    }
    isPlatformDefault.value = !!policiesRes.is_platform_default
  } catch (e: any) {
    error.value = e.message || '加载防滥用策略失败'
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  saving.value = true
  error.value = ''
  try {
    await putAbusePolicies(props.scopeType, props.scopeId, {
      mode: form.value.mode,
      policies: form.value.policies,
    })
    isPlatformDefault.value = false
    emit('saved')
  } catch (e: any) {
    error.value = e.message || '保存失败'
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  loadData()
})
</script>
