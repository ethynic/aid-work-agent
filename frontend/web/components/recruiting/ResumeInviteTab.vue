<template>
  <!-- 简历详情「邀约信息」tab：邀约卡片列表 + 新增/编辑弹框（第④期） -->
  <div class="space-y-3">
    <!-- 工具行：新增入口 -->
    <div class="flex justify-end">
      <BaseButton size="sm" @click="openCreateModal">新增邀约</BaseButton>
    </div>

    <div v-if="loading" class="rounded-lg border border-default bg-white p-4 text-muted text-sm">加载中...</div>

    <template v-else>
      <!-- 邀约卡片列表（created_at DESC，最新在上） -->
      <div
        v-for="inv in invitations"
        :key="inv.id"
        class="rounded-lg border border-default bg-white p-4"
      >
        <div class="flex items-start justify-between gap-2 mb-2">
          <div class="flex items-center gap-2 flex-wrap">
            <BaseBadge :intent="statusIntent(inv.status)">
              {{ INVITATION_STATUSES[inv.status] || inv.status }}
            </BaseBadge>
            <span class="text-xs text-muted">发起于 {{ formatDate(inv.created_at) }}</span>
          </div>
          <div class="flex items-center gap-2 flex-shrink-0">
            <BaseButton intent="secondary" size="sm" class="whitespace-nowrap text-xs" @click="openEditModal(inv)">编辑</BaseButton>
          </div>
        </div>
        <div class="space-y-1">
          <div class="flex text-sm">
            <span class="text-muted w-20 flex-shrink-0">面试时间</span>
            <span class="text-default">{{ formatDateTime(inv.interview_at) }}</span>
          </div>
          <div class="flex text-sm">
            <span class="text-muted w-20 flex-shrink-0">面试官</span>
            <span class="text-default">{{ inv.interviewer || '-' }}</span>
          </div>
          <div class="flex text-sm">
            <span class="text-muted w-20 flex-shrink-0">方式</span>
            <span class="text-default">{{ inv.method || '-' }}</span>
          </div>
          <div v-if="inv.notes" class="flex text-sm">
            <span class="text-muted w-20 flex-shrink-0">备注</span>
            <span class="text-default break-all whitespace-pre-wrap">{{ inv.notes }}</span>
          </div>
        </div>
        <!-- 状态即时流转：下拉直接 PATCH 保存 -->
        <div class="mt-2 pt-2 border-t border-default flex items-center gap-2">
          <span class="text-xs text-muted">流转状态</span>
          <BaseSelect
            :model-value="inv.status"
            size="sm"
            class="w-32"
            :disabled="statusSavingId === inv.id"
            @update:model-value="status => handleStatusChange(inv, String(status))"
          >
            <option v-for="(label, value) in INVITATION_STATUSES" :key="value" :value="value">{{ label }}</option>
          </BaseSelect>
          <span v-if="statusSavingId === inv.id" class="text-xs text-muted">保存中...</span>
        </div>
      </div>
      <div v-if="!invitations.length" class="rounded-lg border border-default bg-white p-4 text-muted text-sm">
        尚未发起邀约，点击右上角「新增邀约」添加
      </div>
    </template>

    <!-- ============== 新增/编辑邀约弹框 ============== -->
    <BaseModal v-model="showModal" :title="form.id ? '编辑邀约' : '新增邀约'">
      <div class="space-y-4">
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="form-label">面试时间</label>
            <input
              v-model="form.interview_at"
              type="datetime-local"
              class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
            />
          </div>
          <div>
            <label class="form-label">状态</label>
            <BaseSelect v-model="form.status">
              <option v-for="(label, value) in INVITATION_STATUSES" :key="value" :value="value">{{ label }}</option>
            </BaseSelect>
          </div>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="form-label">面试官</label>
            <BaseInput v-model="form.interviewer" placeholder="如：王经理（选填）" />
          </div>
          <div>
            <label class="form-label">方式</label>
            <BaseInput v-model="form.method" placeholder="现场/电话/视频面试（选填）" />
          </div>
        </div>
        <div>
          <label class="form-label">备注</label>
          <textarea
            v-model="form.notes"
            rows="3"
            class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
            placeholder="候选人确认情况、注意事项等（选填）"
          />
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" :disabled="submitting" @click="showModal = false">取消</BaseButton>
        <BaseButton :disabled="submitting" @click="handleSubmit">
          {{ submitting ? '提交中...' : '保存' }}
        </BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
/**
 * 简历详情「邀约信息」tab 子组件（第④期）。
 *
 * - 父组件 ResumeDetail 传 resumeId，tab 首次激活（v-if 挂载）时拉取列表
 * - 状态徽标：pending 绿（待推进）/ confirmed info / done success / noshow danger / cancelled neutral
 * - 卡片上状态下拉即时 PATCH 保存（失败 toast，回滚显示）；编辑复用新增弹框回填
 */
import { ref, onMounted } from 'vue'
import { useToast } from 'vue-toastification'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import {
  listInvitations,
  createInvitation,
  updateInvitation,
  INVITATION_STATUSES,
  type Invitation,
} from '@/api/recruitingOperator'
import { formatDate } from './recruitingDisplay'

const props = defineProps<{
  resumeId: number
}>()

const toast = useToast()

// ============== 状态徽标配色 ==============

function statusIntent(status?: string): 'success' | 'info' | 'danger' | 'neutral' {
  const map: Record<string, 'success' | 'info' | 'danger' | 'neutral'> = {
    pending: 'success',
    confirmed: 'info',
    done: 'success',
    noshow: 'danger',
    cancelled: 'neutral',
  }
  return map[status || ''] || 'neutral'
}

// 时间展示：面试时间带时分（区别于 formatDate 只到日期）
function formatDateTime(t?: string | null): string {
  if (!t) return '-'
  return t.slice(0, 16).replace('T', ' ')
}

// ============== 列表加载 ==============

const invitations = ref<Invitation[]>([])
const loading = ref(true)

async function loadInvitations() {
  loading.value = true
  try {
    const res = await listInvitations(props.resumeId)
    if (res.success && res.data) {
      invitations.value = res.data.items
    } else {
      toast.error(res.error || '加载邀约记录失败')
    }
  } catch (e: any) {
    toast.error(e.message || '加载邀约记录异常')
  } finally {
    loading.value = false
  }
}

// ============== 新增/编辑弹框 ==============

const showModal = ref(false)
const submitting = ref(false)

interface InviteFormState {
  id: number
  /** datetime-local 原始值（YYYY-MM-DDTHH:mm，空串=未约时间） */
  interview_at: string
  interviewer: string
  method: string
  status: string
  notes: string
}

function emptyForm(): InviteFormState {
  return { id: 0, interview_at: '', interviewer: '', method: '', status: 'pending', notes: '' }
}

const form = ref<InviteFormState>(emptyForm())

function openCreateModal() {
  form.value = emptyForm()
  showModal.value = true
}

function openEditModal(inv: Invitation) {
  form.value = {
    id: inv.id,
    // datetime-local 需要秒；后端返回 ISO 带时区，截到分钟即可回填
    interview_at: inv.interview_at ? inv.interview_at.slice(0, 16) : '',
    interviewer: inv.interviewer || '',
    method: inv.method || '',
    status: String(inv.status),
    notes: inv.notes || '',
  }
  showModal.value = true
}

/**
 * datetime-local 值（YYYY-MM-DDTHH:mm）补秒转 ISO 后传后端。
 * 注意：清空时间必须返回空串（而非 undefined）——JSON.stringify 会丢弃 undefined 键，
 * 后端将「键缺失」解释为不修改，清空操作会静默失效；空串在后端语义为置 NULL。
 */
function buildInterviewAt(): string {
  const v = form.value.interview_at.trim()
  if (!v) return ''
  return v.length === 16 ? `${v}:00` : v
}

async function handleSubmit() {
  submitting.value = true
  try {
    const interview_at = buildInterviewAt()
    if (form.value.id) {
      // 编辑：恒传全部字段（与弹框所见一致；面试时间清空 = 置空）
      const res = await updateInvitation(form.value.id, {
        interview_at,
        interviewer: form.value.interviewer.trim(),
        method: form.value.method.trim(),
        status: form.value.status,
        notes: form.value.notes.trim(),
      })
      if (res.success) {
        toast.success('邀约已更新')
        showModal.value = false
        await loadInvitations()
      } else {
        toast.error(res.error || '保存失败')
      }
    } else {
      // 新增：默认 pending；后端校验非法状态/时间格式转 400
      const res = await createInvitation(props.resumeId, {
        interview_at,
        interviewer: form.value.interviewer.trim() || undefined,
        method: form.value.method.trim() || undefined,
        status: form.value.status,
        notes: form.value.notes.trim() || undefined,
      })
      if (res.success) {
        toast.success('邀约已创建')
        showModal.value = false
        await loadInvitations()
      } else {
        toast.error(res.error || '保存失败')
      }
    }
  } catch (e: any) {
    toast.error(e.message || '保存异常')
  } finally {
    submitting.value = false
  }
}

// ============== 状态即时流转 ==============

const statusSavingId = ref<number | null>(null)

async function handleStatusChange(inv: Invitation, nextStatus: string) {
  if (nextStatus === inv.status) return
  statusSavingId.value = inv.id
  try {
    const res = await updateInvitation(inv.id, { status: nextStatus })
    if (res.success && res.data) {
      // 用后端返回的记录原地替换，保持列表顺序
      const idx = invitations.value.findIndex(i => i.id === inv.id)
      if (idx !== -1) invitations.value[idx] = res.data
      toast.success('状态已更新')
    } else {
      toast.error(res.error || '状态更新失败')
      await loadInvitations() // 失败回滚显示
    }
  } catch (e: any) {
    toast.error(e.message || '状态更新异常')
    await loadInvitations()
  } finally {
    statusSavingId.value = null
  }
}

onMounted(() => {
  loadInvitations()
})
</script>
