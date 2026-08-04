<template>
  <div class="h-full overflow-y-auto">
    <div class="max-w-6xl mx-auto p-6 space-y-6">
      <!-- ① 向导区 -->
      <BaseCard title="生成新视频">
        <div class="space-y-4">
          <!-- 场景选择 -->
          <div>
            <label class="block text-sm font-medium text-default mb-1.5">选择场景</label>
            <BaseSelect v-model="form.sceneId">
              <option value="" disabled>请选择场景</option>
              <option v-for="s in scenes" :key="s.scene_id" :value="s.scene_id">{{ s.name }} — {{ s.description }}</option>
            </BaseSelect>
          </div>

          <!-- 产品图上传（必填，锁定产品外观防变形） -->
          <div>
            <label class="block text-sm font-medium text-default mb-1.5">
              产品图 <span class="text-red-500">*</span>
              <span class="text-xs font-normal text-muted ml-1">用于锁定产品外观，防变形</span>
            </label>
            <div v-if="!form.productImageFid" class="flex items-center justify-center w-full">
              <label class="flex flex-col items-center justify-center w-full h-32 border-2 border-dashed border-default rounded-lg cursor-pointer hover:border-primary-400 hover:bg-primary-50/50 transition-colors">
                <div class="flex flex-col items-center justify-center pt-5 pb-6">
                  <svg class="w-8 h-8 mb-2 text-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" /></svg>
                  <p class="text-sm text-muted"><span class="font-semibold text-primary-600">点击上传</span> 产品图</p>
                </div>
                <input type="file" accept="image/*" class="hidden" @change="(e) => onUpload(e, 'product')" />
              </label>
            </div>
            <div v-else class="flex items-center gap-3 p-3 bg-gray-50 rounded-lg">
              <img :src="previewUrl('product')" class="w-16 h-16 object-cover rounded" />
              <div class="flex-1 min-w-0">
                <p class="text-sm text-default truncate">{{ form.productImageName }}</p>
                <p class="text-xs text-muted">已上传</p>
              </div>
              <BaseButton intent="ghost" size="sm" @click="clearImage('product')">更换</BaseButton>
            </div>
            <p v-if="uploading === 'product'" class="text-xs text-primary-600 mt-1">上传中...</p>
          </div>

          <!-- 模特图上传（可选，作视频起始帧） -->
          <div>
            <label class="block text-sm font-medium text-default mb-1.5">
              模特图 <span class="text-xs font-normal text-muted ml-1">可选，作视频起始画面（建议竖屏 9:16）</span>
            </label>
            <div v-if="!form.modelImageFid" class="flex items-center justify-center w-full">
              <label class="flex flex-col items-center justify-center w-full h-24 border-2 border-dashed border-default rounded-lg cursor-pointer hover:border-primary-400 hover:bg-primary-50/50 transition-colors">
                <div class="flex flex-col items-center justify-center py-3">
                  <svg class="w-6 h-6 mb-1 text-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" /></svg>
                  <p class="text-xs text-muted"><span class="font-semibold text-primary-600">点击上传</span> 模特图（可跳过）</p>
                </div>
                <input type="file" accept="image/*" class="hidden" @change="(e) => onUpload(e, 'model')" />
              </label>
            </div>
            <div v-else class="flex items-center gap-3 p-3 bg-gray-50 rounded-lg">
              <img :src="previewUrl('model')" class="w-16 h-16 object-cover rounded" />
              <div class="flex-1 min-w-0">
                <p class="text-sm text-default truncate">{{ form.modelImageName }}</p>
                <p class="text-xs text-muted">起始画面</p>
              </div>
              <BaseButton intent="ghost" size="sm" @click="clearImage('model')">移除</BaseButton>
            </div>
            <p v-if="uploading === 'model'" class="text-xs text-primary-600 mt-1">上传中...</p>
          </div>

          <!-- 文案（视频提示词主体） -->
          <div>
            <label class="block text-sm font-medium text-default mb-1.5">
              产品文案 <span class="text-red-500">*</span>
            </label>
            <p class="text-xs text-muted mb-1.5">这段文案会成为视频生成提示词的主体，描述要展示的产品和卖点（越具体出片越准）</p>
            <MyTextarea v-model="form.copywriting" :rows="4" placeholder="如：12mm 水貂毛自然款假睫毛，轻盈贴合，放大双眼。展示睫毛的弧度和佩戴效果，突出自然妆感。" />
          </div>

          <!-- 抽卡条数 -->
          <div class="flex items-center gap-4">
            <label class="text-sm font-medium text-default">生成条数</label>
            <div class="flex gap-2">
              <button v-for="n in [2,3,4]" :key="n" @click="form.cardCount = n"
                :class="['px-4 py-1.5 rounded-md text-sm border transition-colors',
                  form.cardCount===n ? 'bg-primary-600 text-white border-primary-600' : 'bg-white text-default border-default hover:border-primary-400']">
                {{ n }} 条
              </button>
            </div>
          </div>

          <!-- 提示词预览（可微调） -->
          <div>
            <label class="block text-sm font-medium text-default mb-1.5">提示词预览（留空用场景默认，可微调）</label>
            <MyTextarea v-model="form.expandedPrompt" :rows="3" placeholder="留空则按场景模板自动生成" />
          </div>

          <!-- 错误提示 -->
          <p v-if="createError" class="text-sm text-red-600">{{ createError }}</p>

          <div class="flex justify-end">
            <BaseButton intent="primary" :disabled="!canCreate || creating" @click="onCreate">
              {{ creating ? '抽卡中...' : '开始抽卡' }}
            </BaseButton>
          </div>
        </div>
      </BaseCard>

      <!-- ② 抽卡结果区 -->
      <BaseCard v-if="currentSession">
        <template #header>
          <div class="flex items-center justify-between w-full">
            <h3 class="text-base font-semibold text-default">抽卡结果 · {{ currentSession.scene_name || currentSession.scene_id }}</h3>
            <span :class="['text-xs px-2 py-0.5 rounded-full', sessionStatusIntent]">{{ sessionStatusLabel }}</span>
          </div>
        </template>
        <div v-if="currentSession.cards && currentSession.cards.length" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <div v-for="card in currentSession.cards" :key="card.card_id"
            class="border border-default rounded-lg overflow-hidden bg-white">
            <!-- 视频预览 / 状态占位 -->
            <div class="aspect-[9/16] bg-gray-100 flex items-center justify-center relative">
              <video v-if="card.provider_status === 'SUCCEEDED' && card.output_fid"
                :src="fileUrl(card.output_fid)" controls class="w-full h-full object-cover" />
              <div v-else-if="['PENDING','RUNNING'].includes(card.provider_status)" class="text-center text-muted">
                <svg class="animate-spin w-8 h-8 mx-auto mb-2 text-primary-500" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" /><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>
                <p class="text-xs">生成中（约3分钟）</p>
              </div>
              <div v-else-if="card.provider_status === 'FAILED'" class="text-center px-4">
                <p class="text-xs text-red-500 mb-2">生成失败</p>
                <p class="text-xs text-muted">{{ card.error_msg || '请重新生成' }}</p>
              </div>
              <span v-if="card.kept" class="absolute top-2 left-2 text-xs px-2 py-0.5 rounded-full bg-green-500 text-white">已留用</span>
            </div>
            <!-- 卡片操作 -->
            <div class="p-3 space-y-2">
              <p class="text-xs text-muted">Seed: {{ card.seed }} · {{ card.provider_status }}</p>
              <div class="flex items-center gap-2">
                <button @click="toggleKept(card)"
                  :class="['flex-1 text-xs py-1.5 rounded border transition-colors',
                    card.kept ? 'bg-green-50 text-green-700 border-green-300' : 'bg-white text-default border-default hover:border-green-400']">
                  {{ card.kept ? '✓ 留用' : '留用' }}
                </button>
                <button v-if="card.provider_status === 'SUCCEEDED'" @click="onDownload(card)"
                  class="flex-1 text-xs py-1.5 rounded border bg-white text-default border-default hover:border-primary-400">下载</button>
              </div>
              <button @click="onRegenerate(card)"
                class="w-full text-xs py-1.5 rounded border bg-white text-primary-600 border-primary-300 hover:bg-primary-50">重新生成</button>
            </div>
          </div>
        </div>
        <p v-else class="text-sm text-muted">暂无卡片</p>
      </BaseCard>

      <!-- ③ 历史会话区 -->
      <BaseCard title="历史会话">
        <div v-if="history.length" class="divide-y divide-default">
          <button v-for="s in history" :key="s.session_id" @click="loadSession(s.session_id)"
            class="w-full flex items-center justify-between py-3 text-left hover:bg-gray-50 px-2 rounded transition-colors">
            <div class="min-w-0">
              <p class="text-sm text-default truncate">{{ s.copywriting }}</p>
              <p class="text-xs text-muted">{{ s.scene_name || s.scene_id }} · {{ s.card_count }} 条</p>
            </div>
            <span :class="['text-xs px-2 py-0.5 rounded-full flex-shrink-0 ml-3', statusIntent(s.status)]">{{ statusLabel(s.status) }}</span>
          </button>
        </div>
        <p v-else class="text-sm text-muted">暂无历史</p>
      </BaseCard>

      <!-- Toast -->
      <div v-if="message" class="fixed bottom-6 right-6 px-4 py-2 rounded-lg shadow-lg text-sm text-white"
        :class="messageType === 'success' ? 'bg-green-600' : 'bg-red-600'">{{ message }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import MyTextarea from '@/components/ui/MyTextarea.vue'
import { videoGenAPI, fileUrl, type SceneItem, type GenSession, type GenCard } from '@/api/videoGen'

const scenes = ref<SceneItem[]>([])
const history = ref<GenSession[]>([])
const currentSession = ref<GenSession | null>(null)

const form = ref({
  sceneId: '',
  productImageFid: '',
  productImageName: '',
  modelImageFid: '',
  modelImageName: '',
  copywriting: '',
  cardCount: 3,
  expandedPrompt: '',
})
const uploading = ref<string | null>(null)   // null | 'product' | 'model'
const creating = ref(false)
const createError = ref('')
const message = ref('')
const messageType = ref<'success' | 'error'>('success')
let pollTimer: ReturnType<typeof setInterval> | null = null

const previewUrl = computed(() => (type: 'product' | 'model') => {
  const fid = type === 'product' ? form.value.productImageFid : form.value.modelImageFid
  return fid ? fileUrl(fid) : ''
})
const canCreate = computed(() => form.value.sceneId && form.value.productImageFid && form.value.copywriting.trim())

const sessionStatusLabel = computed(() => {
  const st = currentSession.value?.status
  if (st === 'generating') return '生成中'
  if (st === 'done') return '已完成'
  if (st === 'failed') return '失败'
  return st || ''
})
const sessionStatusIntent = computed(() => {
  const st = currentSession.value?.status
  if (st === 'done') return 'bg-green-100 text-green-700'
  if (st === 'failed') return 'bg-red-100 text-red-700'
  return 'bg-amber-100 text-amber-700'
})

function statusLabel(st: string): string {
  return { generating: '生成中', done: '已完成', failed: '失败' }[st] || st
}
function statusIntent(st: string): string {
  if (st === 'done') return 'bg-green-100 text-green-700'
  if (st === 'failed') return 'bg-red-100 text-red-700'
  return 'bg-amber-100 text-amber-700'
}

function toast(msg: string, type: 'success' | 'error' = 'success') {
  message.value = msg
  messageType.value = type
  setTimeout(() => { message.value = '' }, 3000)
}

async function loadScenes() {
  const res = await videoGenAPI.listScenes()
  if (res.success && res.data) scenes.value = res.data.items
}

async function loadHistory() {
  const res = await videoGenAPI.listSessions()
  if (res.success && res.data) history.value = res.data.items
}

async function onUpload(e: Event, type: 'product' | 'model') {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploading.value = type
  createError.value = ''
  try {
    const res = await videoGenAPI.uploadImage(file)
    if (res.success && res.file_id) {
      if (type === 'product') {
        form.value.productImageFid = res.file_id
        form.value.productImageName = res.name || file.name
      } else {
        form.value.modelImageFid = res.file_id
        form.value.modelImageName = res.name || file.name
      }
    } else {
      toast('上传失败', 'error')
    }
  } catch {
    toast('上传失败', 'error')
  } finally {
    uploading.value = null
    input.value = ''
  }
}

function clearImage(type: 'product' | 'model') {
  if (type === 'product') {
    form.value.productImageFid = ''
    form.value.productImageName = ''
  } else {
    form.value.modelImageFid = ''
    form.value.modelImageName = ''
  }
}

async function onCreate() {
  creating.value = true
  createError.value = ''
  try {
    const res = await videoGenAPI.createSession({
      scene_id: form.value.sceneId,
      product_image_fid: form.value.productImageFid,
      copywriting: form.value.copywriting.trim(),
      model_image_fid: form.value.modelImageFid || undefined,
      card_count: form.value.cardCount,
      expanded_prompt: form.value.expandedPrompt.trim() || undefined,
    })
    if (res.success && res.data) {
      currentSession.value = res.data
      startPolling()
      toast('已开始生成，约 3 分钟出片')
      await loadHistory()
    } else {
      createError.value = res.error || '创建失败'
    }
  } catch (e) {
    createError.value = '创建失败'
  } finally {
    creating.value = false
  }
}

async function loadSession(sessionId: string) {
  const res = await videoGenAPI.getSession(sessionId)
  if (res.success && res.data) {
    currentSession.value = res.data
    if (res.data.status === 'generating') startPolling()
    else stopPolling()
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    if (!currentSession.value) return
    const res = await videoGenAPI.getSession(currentSession.value.session_id)
    if (res.success && res.data) {
      currentSession.value = res.data
      if (res.data.status !== 'generating') {
        stopPolling()
        await loadHistory()
      }
    }
  }, 5000)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

async function toggleKept(card: GenCard) {
  const res = await videoGenAPI.setKept(card.card_id, !card.kept)
  if (res.success) card.kept = !card.kept
}

async function onDownload(card: GenCard) {
  const res = await videoGenAPI.getDownloadUrl(card.card_id)
  if (res.success && res.data) {
    window.open(res.data.download_url, '_blank')
  } else {
    toast(res.error || '获取下载地址失败', 'error')
  }
}

async function onRegenerate(card: GenCard) {
  try {
    const res = await videoGenAPI.regenerate(card.card_id, {})
    if (res.success) {
      toast('已提交重新生成')
      if (currentSession.value) await loadSession(currentSession.value.session_id)
      startPolling()
    } else {
      toast(res.error || '重新生成失败', 'error')
    }
  } catch {
    toast('重新生成失败', 'error')
  }
}

onMounted(async () => {
  await Promise.all([loadScenes(), loadHistory()])
})
onUnmounted(stopPolling)
</script>
