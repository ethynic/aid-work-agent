<template>
  <!-- 图片素材库（P4-A）：上传（并发上限 3/进度/取消/失败重试）+ 网格预览 + 引用保护删除 + 选择插入 -->
  <BaseModal
    :model-value="modelValue"
    :title="selectMode ? '选择图片素材' : '图片素材库'"
    size="lg"
    @update:model-value="v => emit('update:modelValue', v)"
  >
    <div class="space-y-4">
      <!-- 未启用门禁 -->
      <div v-if="loaded && !imagesEnabled" class="rounded-lg border border-warning-300 bg-warning-50 p-3 text-sm text-warning-800">
        图片内容未启用（images_enabled=false）：素材上传与图片块暂不可用，请联系管理员开启后使用。
      </div>

      <!-- 上传区 -->
      <section class="rounded-lg border border-default p-3 space-y-3">
        <div class="flex items-center justify-between gap-2 flex-wrap">
          <div class="text-sm font-semibold text-default">上传图片</div>
          <div class="flex items-center gap-2">
            <span class="text-xs text-muted">并发上限 {{ MAX_CONCURRENT_UPLOADS }}，PNG/JPEG/GIF/WebP/BMP</span>
            <input
              ref="fileInput"
              type="file"
              class="hidden"
              accept="image/png,image/jpeg,image/gif,image/webp,image/bmp"
              multiple
              :disabled="!imagesEnabled"
              @change="onFilesChosen"
            />
            <BaseButton size="sm" :disabled="!imagesEnabled" @click="fileInput?.click()">选择文件</BaseButton>
          </div>
        </div>

        <!-- 上传队列 -->
        <div v-if="queue.length" class="space-y-2" data-testid="upload-queue">
          <div
            v-for="entry in queue"
            :key="entry.localId"
            class="rounded border border-default bg-canvas px-3 py-2 flex items-center gap-3"
          >
            <span class="text-xs text-default truncate w-40" :title="entry.name">{{ entry.name }}</span>
            <div class="flex-1 h-2 rounded bg-canvas border border-default overflow-hidden">
              <div
                class="h-full bg-primary-500 transition-all"
                :class="entry.status === 'error' ? 'bg-danger-500' : ''"
                :style="{ width: `${entry.status === 'done' ? 100 : entry.status === 'queued' ? 0 : entry.progress}%` }"
              ></div>
            </div>
            <span class="text-xs w-14 text-right" :class="statusClass(entry.status)">
              {{ statusLabel(entry.status) }}
            </span>
            <BaseButton
              v-if="entry.status === 'uploading' || entry.status === 'queued'"
              size="sm"
              intent="ghost"
              @click="cancelUpload(entry)"
            >取消</BaseButton>
            <BaseButton
              v-if="entry.status === 'error'"
              size="sm"
              intent="ghost"
              @click="retryUpload(entry)"
            >重试</BaseButton>
            <BaseButton
              v-if="entry.status === 'error' || entry.status === 'done'"
              size="sm"
              intent="ghost"
              @click="dismissUpload(entry)"
            >移除</BaseButton>
          </div>
        </div>
        <p v-if="uploadError" class="text-xs text-danger-600" data-testid="upload-error">{{ uploadError }}</p>
      </section>

      <!-- 素材网格 -->
      <section>
        <div class="flex items-center justify-between mb-2">
          <div class="text-sm font-semibold text-default">已有素材（{{ total }}）</div>
          <BaseButton size="sm" intent="ghost" :disabled="loading" @click="loadAssets">刷新</BaseButton>
        </div>
        <div v-if="loading && !items.length" class="text-sm text-muted py-3">加载中...</div>
        <div v-else-if="loadError" class="text-sm text-danger-600 py-3">
          {{ loadError }}
          <BaseButton size="sm" intent="secondary" @click="loadAssets">重试</BaseButton>
        </div>
        <div v-else-if="!items.length" class="empty-state">
          <div class="empty-state-icon">▦</div>
          <p class="text-sm text-muted">暂无素材，请先上传图片</p>
        </div>
        <div v-else class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          <div
            v-for="asset in items"
            :key="asset.id"
            class="rounded-lg border border-default bg-surface overflow-hidden"
            data-testid="asset-tile"
          >
            <button
              type="button"
              class="block w-full aspect-video bg-canvas relative"
              :disabled="!selectMode"
              :title="selectMode ? '选用该图片（已引用素材可复用，引用保护只约束删除）' : ''"
              @click="chooseAsset(asset)"
            >
              <img
                v-if="objectUrls[asset.id]"
                :src="objectUrls[asset.id]"
                class="w-full h-full object-contain"
                :alt="`素材 ${asset.id.slice(0, 8)}`"
              />
              <span v-else class="absolute inset-0 flex items-center justify-center text-xs text-muted">加载中...</span>
            </button>
            <div class="p-2 space-y-1">
              <div class="flex items-center gap-1 flex-wrap">
                <BaseBadge intent="info">{{ asset.mime.replace('image/', '').toUpperCase() }}</BaseBadge>
                <span class="text-xs text-muted">{{ asset.width }}×{{ asset.height }}</span>
                <span class="text-xs text-muted">{{ formatSize(asset.size) }}</span>
              </div>
              <div class="flex items-center justify-between">
                <span v-if="asset.reference_count" class="text-xs text-warning-600" data-testid="asset-referenced">
                  被 {{ asset.reference_count }} 个版本引用
                </span>
                <span v-else class="text-xs text-muted">未引用</span>
                <BaseButton
                  size="sm"
                  intent="danger-ghost"
                  :disabled="deletingId === asset.id"
                  :title="asset.reference_count ? '被引用的素材不可删除（先在任务中移除引用）' : '删除素材（不可恢复）'"
                  @click="pendingDelete = asset"
                >{{ deletingId === asset.id ? '删除中...' : '删除' }}</BaseButton>
              </div>
            </div>
          </div>
        </div>

        <!-- 分页 -->
        <div v-if="total > pageSize" class="flex items-center justify-between mt-3">
          <BaseButton size="sm" intent="secondary" :disabled="page <= 1 || loading" @click="page--; loadAssets()">上一页</BaseButton>
          <span class="text-xs text-muted">第 {{ page }} 页 / 共 {{ pageCount }} 页</span>
          <BaseButton size="sm" intent="secondary" :disabled="page >= pageCount || loading" @click="page++; loadAssets()">下一页</BaseButton>
        </div>
      </section>
    </div>

    <!-- 删除确认 -->
    <BaseModal :model-value="!!pendingDelete" title="确认删除素材" size="sm" @update:model-value="v => !v && (pendingDelete = null)">
      <div class="space-y-2">
        <p class="text-sm text-default">删除后不可恢复；被内容引用的素材会被服务端拒绝。</p>
        <p v-if="deleteError" class="text-xs text-danger-600" data-testid="delete-error">{{ deleteError }}</p>
      </div>
      <template #footer>
        <BaseButton intent="secondary" :disabled="deleting" @click="pendingDelete = null">取消</BaseButton>
        <BaseButton intent="danger" :disabled="deleting" @click="confirmDelete">
          {{ deleting ? '删除中...' : '确认删除' }}
        </BaseButton>
      </template>
    </BaseModal>
  </BaseModal>
</template>

<script setup lang="ts">
/**
 * AssetLibrary：图片素材库（P4-A，R57）。
 * - 上传：XHR 进度 + 取消（abort 后无孤儿资源——服务端只在校验全通过后落盘）；
 *   并发上限 3（超出排队），失败单条重试；
 * - 网格预览：鉴权 blob URL（fetchAssetObjectUrl 会话级缓存）；
 * - 删除：引用保护 409 ASSET_IN_USE 文案化展示；未引用可删（硬删，二次确认）；
 * - 选择模式：点击未引用/已引用素材均可复用插入编辑器（引用保护只约束删除）。
 */
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useToast } from 'vue-toastification'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import {
  WeixinApiError,
  deleteAsset,
  fetchAssetObjectUrl,
  listAssets,
  uploadAsset,
  type AssetItem,
} from '@/api/weixinMarketing'

const MAX_CONCURRENT_UPLOADS = 3
const ACCEPTED_MIME = ['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/bmp']

const props = defineProps<{
  modelValue: boolean
  /** true = 选择模式（点击素材回传 selected）；false = 仅管理 */
  selectMode?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  'selected': [asset: AssetItem]
}>()

const toast = useToast()

// 隐藏文件输入引用（模板 ref）
const fileInput = ref<HTMLInputElement | null>(null)

// ============== 列表状态 ==============

const loading = ref(false)
const loadError = ref('')
const items = ref<AssetItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 12
const imagesEnabled = ref(true)
const loaded = ref(false)
const objectUrls = reactive<Record<string, string>>({})

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

async function loadAssets() {
  loading.value = true
  loadError.value = ''
  try {
    const result = await listAssets({ page: page.value, page_size: pageSize })
    // 防御畸形响应（items/total 缺省时按空列表渲染，不抛渲染错误）
    const list = Array.isArray(result?.items) ? result.items : []
    items.value = list
    total.value = Number(result?.total) || 0
    imagesEnabled.value = result?.images_enabled !== false
    loaded.value = true
    void loadThumbnails(list)
  } catch (e) {
    loadError.value = e instanceof WeixinApiError ? e.message : '加载素材列表失败'
  } finally {
    loading.value = false
  }
}

async function loadThumbnails(list: AssetItem[]) {
  for (const asset of list) {
    if (objectUrls[asset.id]) continue
    try {
      objectUrls[asset.id] = await fetchAssetObjectUrl(asset.id)
    } catch {
      // 单个缩略图失败不阻断网格（占位「加载中」保留）
    }
  }
}

watch(
  () => props.modelValue,
  open => {
    if (open) {
      page.value = 1
      void loadAssets()
    }
  },
)

// ============== 上传队列（并发上限 3 / 取消 / 重试） ==============

interface UploadEntry {
  localId: number
  name: string
  file: File
  progress: number
  status: 'queued' | 'uploading' | 'done' | 'error'
  error: string
  handle: { abort: () => void } | null
}

const queue = ref<UploadEntry[]>([])
const uploadError = ref('')
let localIdSeq = 0

const activeUploads = computed(
  () => queue.value.filter(e => e.status === 'uploading').length,
)

function onFilesChosen(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  if (!files.length) return
  const rejected: string[] = []
  for (const file of files) {
    if (file.type && !ACCEPTED_MIME.includes(file.type)) {
      rejected.push(file.name)
      continue
    }
    queue.value.push({
      localId: ++localIdSeq, name: file.name, file,
      progress: 0, status: 'queued', error: '', handle: null,
    })
  }
  if (rejected.length) {
    uploadError.value = `不支持的格式已忽略：${rejected.join('、')}（仅 PNG/JPEG/GIF/WebP/BMP）`
  } else {
    uploadError.value = ''
  }
  pump()
}

function pump() {
  while (activeUploads.value < MAX_CONCURRENT_UPLOADS) {
    const next = queue.value.find(e => e.status === 'queued')
    if (!next) break
    startUpload(next)
  }
}

function startUpload(entry: UploadEntry) {
  entry.status = 'uploading'
  entry.progress = 0
  entry.error = ''
  const { promise, abort } = uploadAsset(entry.file, {
    onProgress: percent => {
      entry.progress = percent
    },
  })
  entry.handle = { abort }
  promise
    .then(() => {
      entry.status = 'done'
      entry.progress = 100
      toast.success(`已上传：${entry.name}`)
      void loadAssets()
    })
    .catch(err => {
      if (err instanceof DOMException && err.name === 'AbortError') {
        // 用户取消：移除队列项即可（服务端无部分状态需要回收）
        queue.value = queue.value.filter(e => e.localId !== entry.localId)
        return
      }
      entry.status = 'error'
      entry.error = err instanceof WeixinApiError ? err.message : '上传失败'
    })
    .finally(() => pump())
}

function cancelUpload(entry: UploadEntry) {
  if (entry.status === 'uploading') {
    entry.handle?.abort()
  } else {
    queue.value = queue.value.filter(e => e.localId !== entry.localId)
  }
}

function retryUpload(entry: UploadEntry) {
  if (entry.status !== 'error') return
  entry.status = 'queued'
  pump()
}

function dismissUpload(entry: UploadEntry) {
  queue.value = queue.value.filter(e => e.localId !== entry.localId)
}

function statusLabel(status: UploadEntry['status']): string {
  if (status === 'queued') return '排队中'
  if (status === 'uploading') return '上传中'
  if (status === 'done') return '完成'
  return '失败'
}

function statusClass(status: UploadEntry['status']): string {
  if (status === 'error') return 'text-danger-600'
  if (status === 'done') return 'text-success-600'
  return 'text-muted'
}

// ============== 删除（引用保护 409 文案化） ==============

const pendingDelete = ref<AssetItem | null>(null)
const deleting = ref(false)
const deletingId = ref('')
const deleteError = ref('')

async function confirmDelete() {
  const asset = pendingDelete.value
  if (!asset || deleting.value) return
  deleting.value = true
  deletingId.value = asset.id
  deleteError.value = ''
  try {
    await deleteAsset(asset.id)
    toast.success('素材已删除')
    pendingDelete.value = null
    void loadAssets()
  } catch (e) {
    // 409 ASSET_IN_USE：服务端文案直出（含引用计数与解引用引导）
    deleteError.value = e instanceof WeixinApiError ? e.message : '删除失败'
  } finally {
    deleting.value = false
    deletingId.value = ''
  }
}

// ============== 选择插入 ==============

function chooseAsset(asset: AssetItem) {
  if (!props.selectMode) return
  emit('selected', asset)
  emit('update:modelValue', false)
}

function formatSize(size: number): string {
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  return `${Math.max(1, Math.round(size / 1024))} KB`
}

onBeforeUnmount(() => {
  // 取消在途上传（防组件销毁后继续占用并发额度/产生不必要请求）
  for (const entry of queue.value) {
    if (entry.status === 'uploading') entry.handle?.abort()
  }
})
</script>
