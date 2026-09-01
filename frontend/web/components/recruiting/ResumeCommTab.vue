<template>
  <!-- 简历详情「沟通记录」tab：时间线列表 + 补录弹框（第④期） -->
  <div class="space-y-3">
    <!-- 工具行：补录入口 -->
    <div class="flex justify-end">
      <BaseButton size="sm" @click="openCreateModal">补录沟通</BaseButton>
    </div>

    <div v-if="loading" class="rounded-lg border border-default bg-white p-4 text-muted text-sm">加载中...</div>

    <template v-else>
      <!-- 时间线列表（created_at DESC，最新在上） -->
      <div
        v-for="log in logs"
        :key="log.id"
        class="rounded-lg border border-default bg-white p-4"
      >
        <div class="flex items-start justify-between gap-2 mb-1.5">
          <div class="flex items-center gap-2 flex-wrap">
            <BaseBadge :intent="log.direction === 'out' ? 'info' : 'success'">
              {{ COMM_DIRECTIONS[log.direction] || log.direction }}
            </BaseBadge>
            <BaseBadge intent="neutral">{{ COMM_CHANNELS[log.channel] || log.channel }}</BaseBadge>
            <span class="text-xs text-muted">{{ formatDate(log.created_at) }}</span>
            <span v-if="log.user_id" class="text-xs text-muted">操作人：{{ log.user_id }}</span>
          </div>
          <BaseButton
            intent="danger-ghost"
            size="sm"
            class="whitespace-nowrap text-xs flex-shrink-0"
            @click="handleDelete(log)"
          >删除</BaseButton>
        </div>
        <p class="text-sm text-default leading-relaxed whitespace-pre-wrap break-all">{{ log.content }}</p>
      </div>
      <div v-if="!logs.length" class="rounded-lg border border-default bg-white p-4 text-muted text-sm">
        暂无沟通记录，点击右上角「补录沟通」添加
      </div>
    </template>

    <!-- ============== 补录沟通弹框 ============== -->
    <BaseModal v-model="showModal" title="补录沟通">
      <div class="space-y-4">
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="form-label">沟通方向 <span class="form-required">*</span></label>
            <BaseSelect v-model="form.direction">
              <option v-for="(label, value) in COMM_DIRECTIONS" :key="value" :value="value">{{ label }}</option>
            </BaseSelect>
          </div>
          <div>
            <label class="form-label">沟通渠道</label>
            <BaseSelect v-model="form.channel">
              <option v-for="(label, value) in COMM_CHANNELS" :key="value" :value="value">{{ label }}</option>
            </BaseSelect>
          </div>
        </div>
        <div>
          <label class="form-label">沟通内容 <span class="form-required">*</span></label>
          <textarea
            v-model="form.content"
            rows="6"
            class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
            placeholder="沟通全文（聊了什么、候选人反馈等）"
          />
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" :disabled="submitting" @click="showModal = false">取消</BaseButton>
        <BaseButton :disabled="!canSubmit || submitting" @click="handleSubmit">
          {{ submitting ? '提交中...' : '保存' }}
        </BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
/**
 * 简历详情「沟通记录」tab 子组件（第④期）。
 *
 * - 父组件 ResumeDetail 传 resumeId，tab 首次激活（v-if 挂载）时拉取列表
 * - 方向徽标：out 发出=info 蓝 / in 收到=success 绿；渠道徽标统一 neutral
 */
import { ref, computed, onMounted } from 'vue'
import { useToast } from 'vue-toastification'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import {
  listCommLogs,
  createCommLog,
  deleteCommLog,
  COMM_DIRECTIONS,
  COMM_CHANNELS,
  type CommLog,
} from '@/api/recruitingOperator'
import { formatDate } from './recruitingDisplay'

const props = defineProps<{
  resumeId: number
}>()

const toast = useToast()

// ============== 列表加载 ==============

const logs = ref<CommLog[]>([])
const loading = ref(true)

async function loadLogs() {
  loading.value = true
  try {
    const res = await listCommLogs(props.resumeId)
    if (res.success && res.data) {
      logs.value = res.data.items
    } else {
      toast.error(res.error || '加载沟通记录失败')
    }
  } catch (e: any) {
    toast.error(e.message || '加载沟通记录异常')
  } finally {
    loading.value = false
  }
}

// ============== 补录弹框 ==============

const showModal = ref(false)
const submitting = ref(false)
const form = ref<{ direction: string; channel: string; content: string }>({
  direction: 'out',
  channel: 'boss',
  content: '',
})

const canSubmit = computed(() => form.value.content.trim().length > 0)

function openCreateModal() {
  form.value = { direction: 'out', channel: 'boss', content: '' }
  showModal.value = true
}

async function handleSubmit() {
  if (!canSubmit.value) return
  submitting.value = true
  try {
    const res = await createCommLog(props.resumeId, {
      direction: form.value.direction,
      channel: form.value.channel,
      content: form.value.content.trim(),
    })
    if (res.success) {
      toast.success('沟通记录已保存')
      showModal.value = false
      await loadLogs()
    } else {
      toast.error(res.error || '保存失败')
    }
  } catch (e: any) {
    toast.error(e.message || '保存异常')
  } finally {
    submitting.value = false
  }
}

// ============== 删除 ==============

async function handleDelete(log: CommLog) {
  if (!confirm('确定删除这条沟通记录？')) return
  try {
    const res = await deleteCommLog(log.id)
    if (res.success) {
      toast.success('沟通记录已删除')
      await loadLogs()
    } else {
      toast.error(res.error || '删除失败')
    }
  } catch (e: any) {
    toast.error(e.message || '删除异常')
  }
}

onMounted(() => {
  loadLogs()
})
</script>
