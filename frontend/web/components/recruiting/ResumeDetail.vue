<template>
  <!-- 简历详情页：简历详情 / 匹配评估 / 沟通记录 / 邀约信息 四个 tab（第④期扩为四 tab） -->
  <div class="page-container p-5">
    <!-- ============== 顶部工具栏 ============== -->
    <div class="page-toolbar">
      <div class="page-toolbar-left">
        <BaseButton size="sm" intent="secondary" @click="goBack">← 返回列表</BaseButton>
        <span v-if="detail" class="text-sm font-medium text-default">
          {{ detail.candidate_name || '未命名候选人' }}
          <template v-if="detail.job_name">
            <span class="text-muted font-normal">· {{ detail.job_name }}</span>
            <a
              v-if="detail.job_id"
              class="ml-1 text-xs text-primary-600 hover:underline cursor-pointer"
              @click="goJobDetail"
            >查看职位</a>
            <span v-else class="text-xs text-muted">未关联</span>
          </template>
        </span>
      </div>
    </div>

    <div v-if="loading" class="page-content p-4 text-muted text-sm">加载中...</div>

    <template v-else-if="detail">
      <!-- ============== Tab 切换（按钮式 tab bar，与 ConnectionCenter 同模式） ============== -->
      <div class="flex-shrink-0 border-b border-default">
        <div class="flex gap-6">
          <button
            v-for="tab in tabs"
            :key="tab.key"
            :class="[
              'py-2.5 text-sm font-medium border-b-2 transition-colors',
              activeTab === tab.key
                ? 'text-primary-600 border-primary-600'
                : 'text-muted border-transparent hover:text-default hover:border-hover'
            ]"
            @click="activeTab = tab.key"
          >
            {{ tab.label }}
          </button>
        </div>
      </div>

      <!-- ============== Tab 内容 ============== -->
      <div class="flex-1 min-h-0 pt-4">
        <!-- ── 简历详情：左图右文布局 ── -->
        <div v-if="activeTab === 'resume-detail'" class="flex gap-4 h-full min-h-0">
          <!-- 左侧：简历图片画廊 -->
          <div class="w-1/2 flex flex-col gap-3 min-h-0">
            <div class="flex-1 min-h-0 flex items-center justify-center rounded-lg border border-default bg-gray-50 overflow-hidden">
              <img
                v-if="currentImage"
                :src="`/api/files/${currentImage.file_id}`"
                class="max-w-full max-h-full object-contain cursor-zoom-in"
                @click="openOriginal(currentImage)"
              />
              <span v-else class="text-muted text-sm">无简历图片</span>
            </div>
            <!-- 缩略图列表 + 新窗口看原图 -->
            <div v-if="detailImages.length" class="flex gap-2 overflow-x-auto pb-1">
              <img
                v-for="(img, i) in detailImages"
                :key="img.file_id"
                :src="`/api/files/${img.file_id}`"
                class="h-16 w-14 flex-shrink-0 rounded-md border-2 object-cover cursor-pointer"
                :class="currentImageIndex === i ? 'border-primary-500' : 'border-default'"
                :title="img.name || `第 ${i + 1} 张`"
                @click="currentImageIndex = i"
              />
              <BaseButton
                v-if="currentImage"
                intent="secondary"
                size="sm"
                class="whitespace-nowrap"
                @click="openOriginal(currentImage)"
              >
                新窗口看原图
              </BaseButton>
            </div>
          </div>

          <!-- 右侧：基本信息 / 状态 / 备注 / OCR 文本 -->
          <div class="w-1/2 flex flex-col gap-3 min-h-0 overflow-y-auto pr-1">
            <!-- 基本信息卡 -->
            <div class="rounded-lg border border-default bg-white p-4">
              <div class="text-sm font-semibold text-default mb-2">基本信息</div>
              <div v-if="infoEntries.length" class="space-y-1">
                <div v-for="[key, value] in infoEntries" :key="key" class="flex text-sm">
                  <span class="text-muted w-24 flex-shrink-0">{{ key }}</span>
                  <span class="text-default break-all">{{ value }}</span>
                </div>
              </div>
              <div v-else class="text-muted text-sm">暂无基本信息</div>
              <div class="mt-2 pt-2 border-t border-default text-xs text-muted">
                来源：{{ sourceLabel(detail.source) }} · 获取日期：{{ formatDate(detail.fetched_at) }}
              </div>
            </div>

            <!-- 状态切换（即时保存） -->
            <div class="rounded-lg border border-default bg-white p-4 flex items-center gap-3">
              <span class="text-sm font-semibold text-default">状态</span>
              <BaseSelect v-model="detail.status" size="sm" class="w-36" @change="saveStatus">
                <option v-for="(label, value) in STATUS_LABELS" :key="value" :value="value">{{ label }}</option>
              </BaseSelect>
              <span v-if="savingStatus" class="text-xs text-muted">保存中...</span>
            </div>

            <!-- 备注（可编辑保存） -->
            <div class="rounded-lg border border-default bg-white p-4">
              <div class="text-sm font-semibold text-default mb-2">备注</div>
              <textarea
                v-model="remarkDraft"
                rows="3"
                class="w-full rounded-lg border border-default bg-white px-3 py-2 text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
                placeholder="补充候选人备注..."
              />
              <div class="mt-2 flex justify-end">
                <BaseButton size="sm" :disabled="savingRemark" @click="saveRemark">
                  {{ savingRemark ? '保存中...' : '保存备注' }}
                </BaseButton>
              </div>
            </div>

            <!-- OCR 文本（等宽滚动 + 复制） -->
            <div class="rounded-lg border border-default bg-white p-4 flex flex-col flex-1 min-h-40">
              <div class="flex items-center justify-between mb-2">
                <div class="text-sm font-semibold text-default">OCR 文本</div>
                <BaseButton
                  v-if="detail.ocr_text"
                  intent="secondary"
                  size="sm"
                  class="whitespace-nowrap"
                  @click="copyOcr"
                >
                  复制
                </BaseButton>
              </div>
              <pre
                v-if="detail.ocr_text"
                class="flex-1 overflow-auto rounded-md bg-gray-50 p-3 font-mono text-xs leading-relaxed text-default whitespace-pre-wrap"
              >{{ detail.ocr_text }}</pre>
              <div v-else class="text-muted text-sm">暂无 OCR 文本</div>
            </div>
          </div>
        </div>

        <!-- ── 匹配评估：匹配卡 + 关键信息 + 重新评分 ── -->
        <div v-else-if="activeTab === 'match-evaluation'" class="max-w-3xl space-y-3">
          <div class="rounded-lg border border-default bg-white p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold text-default">匹配评估</div>
              <BaseButton
                intent="secondary"
                size="sm"
                class="whitespace-nowrap"
                :disabled="reEvaluating"
                @click="handleReEvaluate"
              >
                {{ reEvaluating ? '评分中...' : '重新评分' }}
              </BaseButton>
            </div>
            <div class="flex items-center gap-2">
              <BaseBadge :intent="matchIntent(detail.match_status)">{{ matchText(detail) }}</BaseBadge>
              <span v-if="!detail.match_status" class="text-xs text-muted">未评分或未关联职位，可点「重新评分」</span>
            </div>
            <p v-if="detail.match_summary" class="mt-2 text-sm text-default leading-relaxed">{{ detail.match_summary }}</p>
            <div v-if="keyInfoEntries.length" class="mt-2 space-y-1 border-t border-default pt-2">
              <div v-for="[label, value] in keyInfoEntries" :key="label" class="flex text-sm">
                <span class="text-muted w-24 flex-shrink-0">{{ label }}</span>
                <span class="text-default break-all">{{ value }}</span>
              </div>
            </div>
            <div v-else-if="!detail.match_status" class="mt-2 text-xs text-muted">
              评分后展示结构化关键信息（经验/学历/核心技能/亮点等）
            </div>
          </div>
        </div>

        <!-- ── 沟通记录：时间线 + 补录（第④期，v-if 懒挂载：tab 首次激活才拉数据） ── -->
        <div v-else-if="activeTab === 'comm-logs'" class="max-w-3xl">
          <ResumeCommTab :resume-id="detail.id" />
        </div>

        <!-- ── 邀约信息：邀约卡片 + 状态流转（第④期，v-if 懒挂载） ── -->
        <div v-else-if="activeTab === 'invitations'" class="max-w-3xl">
          <ResumeInviteTab :resume-id="detail.id" />
        </div>
      </div>
    </template>

    <div v-else class="page-content p-4 text-muted text-sm">简历不存在或已被删除</div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import ResumeCommTab from './ResumeCommTab.vue'
import ResumeInviteTab from './ResumeInviteTab.vue'
import {
  getResume,
  updateResume,
  reEvaluateResume,
  type ResumeDetail as ResumeDetailData,
} from '@/api/recruitingOperator'
import {
  STATUS_LABELS,
  sourceLabel,
  matchIntent,
  matchText,
  formatDate,
  resumeListPath,
  jobDetailPath,
} from './recruitingDisplay'

const toast = useToast()
const route = useRoute()
const router = useRouter()

const resumeId = computed(() => Number(route.params.resumeId))

// ============== Tab 切换 ==============

type TabKey = 'resume-detail' | 'match-evaluation' | 'comm-logs' | 'invitations'

const tabs: Array<{ key: TabKey; label: string }> = [
  { key: 'resume-detail', label: '简历详情' },
  { key: 'match-evaluation', label: '匹配评估' },
  { key: 'comm-logs', label: '沟通记录' },
  { key: 'invitations', label: '邀约信息' },
]

const activeTab = ref<TabKey>('resume-detail')

// ============== 详情加载 ==============

const detail = ref<ResumeDetailData | null>(null)
const loading = ref(true)
const currentImageIndex = ref(0)
const remarkDraft = ref('')

async function loadDetail() {
  loading.value = true
  try {
    const res = await getResume(resumeId.value)
    if (res.success && res.data) {
      detail.value = res.data
      remarkDraft.value = res.data.remark || ''
    } else {
      detail.value = null
      toast.error(res.error || '加载简历详情失败')
    }
  } catch (e: any) {
    // 网络异常兜底：不再让 Promise 拒绝悬空，明确报错
    detail.value = null
    toast.error(e.message || '加载简历详情异常')
  } finally {
    loading.value = false
  }
}

const detailImages = computed(() => detail.value?.images || [])
const currentImage = computed(() => detailImages.value[currentImageIndex.value] || null)
const infoEntries = computed(() => Object.entries(detail.value?.candidate_info || {}))

function goBack() {
  // 优先浏览器后退（回到列表时保住上一页的 URL query 筛选条件）；
  // 无历史（新标签直接打开详情）时回列表默认页
  if ((window.history.state as { back?: string } | null)?.back) {
    router.back()
  } else {
    router.push(resumeListPath(route))
  }
}

// 跳关联职位详情（job_id 为空时工具栏已隐藏入口）
function goJobDetail() {
  if (!detail.value?.job_id) return
  router.push(jobDetailPath(route, detail.value.job_id))
}

function openOriginal(img: { file_id: string }) {
  window.open(`/api/files/${img.file_id}/download`, '_blank')
}

// ============== 状态 / 备注（即时保存） ==============

const savingStatus = ref(false)
const savingRemark = ref(false)

async function saveStatus() {
  if (!detail.value) return
  savingStatus.value = true
  try {
    const res = await updateResume(detail.value.id, { status: detail.value.status })
    if (res.success && res.data) {
      detail.value = res.data
      toast.success('状态已更新')
    } else {
      toast.error(res.error || '状态更新失败')
    }
  } finally {
    savingStatus.value = false
  }
}

async function saveRemark() {
  if (!detail.value) return
  savingRemark.value = true
  try {
    const res = await updateResume(detail.value.id, { remark: remarkDraft.value })
    if (res.success && res.data) {
      detail.value = res.data
      toast.success('备注已保存')
    } else {
      toast.error(res.error || '备注保存失败')
    }
  } finally {
    savingRemark.value = false
  }
}

async function copyOcr() {
  if (!detail.value?.ocr_text) return
  try {
    await navigator.clipboard.writeText(detail.value.ocr_text)
    toast.success('OCR 文本已复制')
  } catch {
    toast.error('复制失败，请手动选择复制')
  }
}

// ============== 匹配评估 tab（简历-职位匹配 Phase 5） ==============

// key_info 固定 8 字段中文标签（设计 §2 schema；null/空数组跳过该行，宁缺勿编）
const KEY_INFO_LABELS: [string, string][] = [
  ['years_of_experience', '经验年限'],
  ['education', '学历'],
  ['current_company', '当前公司'],
  ['core_skills', '核心技能'],
  ['highlights', '亮点'],
  ['ai_tool_usage', 'AI 工具使用'],
  ['salary_expectation', '期望薪资'],
  ['concerns', '关注点'],
]

// 匹配评估卡的关键信息行（中文标签 + 数组顿号连接；空值跳过）
const keyInfoEntries = computed<[string, string][]>(() => {
  const info = detail.value?.key_info
  if (!info) return []
  const entries: [string, string][] = []
  for (const [key, label] of KEY_INFO_LABELS) {
    const value = (info as Record<string, any>)[key]
    if (value === null || value === undefined || value === '') continue
    if (Array.isArray(value) && !value.length) continue
    entries.push([label, Array.isArray(value) ? value.join('、') : String(value)])
  }
  return entries
})

const reEvaluating = ref(false)

// 重新评分：评分失败后端不报错（data.note 说明原因），前端据 note 提示并刷新详情（原值保留）
async function handleReEvaluate() {
  if (!detail.value || reEvaluating.value) return
  reEvaluating.value = true
  try {
    const res = await reEvaluateResume(detail.value.id)
    if (res.success) {
      if (res.data?.note) {
        toast.info(`评分未完成：${res.data.note}`)
      } else {
        toast.success('重新评分完成')
      }
      // 只刷新详情数据，不重置正在查看的图片索引与备注草稿
      const detailRes = await getResume(detail.value.id)
      if (detailRes.success && detailRes.data) {
        detail.value = detailRes.data
      }
    } else {
      toast.error(res.error || '重新评分失败')
    }
  } catch (e: any) {
    toast.error(e.message || '重新评分异常')
  } finally {
    reEvaluating.value = false
  }
}

onMounted(() => {
  loadDetail()
})
</script>
