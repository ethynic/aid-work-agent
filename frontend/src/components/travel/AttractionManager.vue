<template>
  <div class="manager-container">
    <div class="header-bar">
      <h2>景点知识库</h2>
      <div class="actions">
        <input ref="fileInput" type="file" accept=".xlsx,.xls" style="display:none"
               @change="(e: any) => e.target.files[0] && handleImport(e.target.files[0])" />
        <button class="btn-secondary" @click="handleDownloadTemplate">下载模板</button>
        <button class="btn-primary" :disabled="importing" @click="triggerFileInput(fileInput)">
          {{ importing ? '导入中...' : '导入 Excel' }}
        </button>
      </div>
    </div>

    <!-- 搜索栏 -->
    <div class="kb-search-bar">
      <input v-model="searchQuery" class="kb-search-input" placeholder="输入关键词搜索景点，如：黄果树 5A景区"
             @keyup.enter="doSearch" />
      <button class="btn-primary" @click="doSearch" :disabled="searching">
        {{ searching ? '搜索中...' : '搜索' }}
      </button>
      <button v-if="searched" class="btn-secondary" @click="clearSearch">显示全部</button>
    </div>

    <!-- 统计信息 -->
    <div class="kb-stats">
      <span v-if="!searched">共 {{ allAttractions.length }} 个景点</span>
      <span v-else>搜索结果：{{ searchResults.length }} 个</span>
    </div>

    <!-- 加载状态 -->
    <div v-if="loading" class="kb-empty">加载中...</div>

    <!-- 空状态 -->
    <div v-else-if="currentList.length === 0 && searched" class="kb-empty">未找到匹配的景点</div>

    <!-- 景点列表 -->
    <div v-for="attraction in currentList" :key="attraction.doc_id" class="kb-card">
      <div class="kb-card-header">
        <div>
          <div class="kb-card-title">{{ attraction.title }}</div>
          <div class="kb-card-meta">
            <span v-if="attraction.metadata?.region" class="meta-tag">{{ attraction.metadata.region }}</span>
            <span v-if="attraction.metadata?.category_cn" class="meta-tag">{{ attraction.metadata.category_cn }}</span>
            <span v-if="attraction.score != null" class="meta-score">相似度: {{ (attraction.score * 100).toFixed(1) }}%</span>
          </div>
        </div>
        <button class="btn-sm" @click="showDetail(attraction.doc_id)">查看详情</button>
      </div>
      <div v-if="attraction.source_file" class="kb-card-source">
        来源文件：{{ attraction.source_file }}
      </div>
      <div v-if="attraction.info" class="kb-card-snippet">{{ attraction.info }}</div>
    </div>

    <!-- 导入知识库结果弹窗 -->
    <div v-if="showImportResult" class="modal-overlay" @click.self="showImportResult = false">
      <div class="modal-content">
        <h3>导入景点知识库结果</h3>
        <p>共识别 <strong>{{ importResult?.total_attractions || 0 }}</strong> 个景点，成功导入 <strong>{{ importResult?.imported || 0 }}</strong> 个，跳过 <strong>{{ importResult?.skipped || 0 }}</strong> 个</p>
        <div v-for="r in importResult?.details" :key="r.sheet" style="margin-bottom:8px;font-size:13px">
          {{ r.sheet }}：共 {{ r.total }} 个，导入 {{ r.imported }} 个，跳过 {{ r.skipped }} 个
        </div>
        <div v-if="importResult?.errors?.length" style="margin-top:12px;">
          <div style="font-size:13px;color:#dc2626;margin-bottom:4px;">错误信息：</div>
          <div v-for="err in importResult.errors.slice(0, 10)" :key="err" style="color:#dc2626;font-size:12px;">{{ err }}</div>
        </div>
        <div class="modal-actions">
          <button class="btn-primary" @click="showImportResult = false">确定</button>
        </div>
      </div>
    </div>

    <!-- 景点详情弹窗 -->
    <div v-if="detailVisible" class="modal-overlay" @click.self="detailVisible = false">
      <div class="modal-content" style="width: 750px;">
        <h3>{{ detailData.title }}</h3>
        <div v-if="detailData.info" style="margin-bottom: 16px;">
          <h4 style="margin: 0 0 8px; font-size: 14px; color: #555;">景点信息</h4>
          <pre class="kb-pre">{{ detailData.info }}</pre>
        </div>
        <div v-if="detailData.ticket_table" style="margin-bottom: 16px;">
          <h4 style="margin: 0 0 8px; font-size: 14px; color: #555;">门票价格</h4>
          <pre class="kb-pre">{{ detailData.ticket_table }}</pre>
        </div>
        <div v-if="detailData.project_table" style="margin-bottom: 16px;">
          <h4 style="margin: 0 0 8px; font-size: 14px; color: #555;">项目/服务价格</h4>
          <pre class="kb-pre">{{ detailData.project_table }}</pre>
        </div>
        <div v-if="!detailData.info && !detailData.ticket_table && !detailData.project_table" class="center" style="padding: 20px; color: #999;">
          暂无详细信息
        </div>
        <div class="modal-actions">
          <button class="btn-primary" @click="detailVisible = false">关闭</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { searchAttractionsKB, listAttractionsKB, getAttractionKB } from '@/api/travelQuote'
import { useAttractionKBImport } from '@/composables/useImport'

const fileInput = ref<HTMLInputElement | null>(null)
const { importing, showImportResult, importResult, handleImport, handleDownloadTemplate, triggerFileInput } = useAttractionKBImport(loadAll)

// 数据状态
const loading = ref(false)
const allAttractions = ref<any[]>([])
const searchResults = ref<any[]>([])
const searched = ref(false)
const searching = ref(false)
const searchQuery = ref('')

// 详情弹窗
const detailVisible = ref(false)
const detailData = ref<any>({})

const currentList = computed(() => searched.value ? searchResults.value : allAttractions.value)

async function loadAll() {
  loading.value = true
  try {
    const result = await listAttractionsKB({ limit: 500 })
    allAttractions.value = result.items || []
  } catch (e) {
    console.error('加载景点列表失败', e)
  } finally {
    loading.value = false
  }
}

async function doSearch() {
  if (!searchQuery.value.trim()) return
  searching.value = true
  try {
    searchResults.value = await searchAttractionsKB({ q: searchQuery.value })
    searched.value = true
  } catch (e) {
    console.error('搜索景点失败', e)
    searchResults.value = []
    searched.value = true
  } finally {
    searching.value = false
  }
}

function clearSearch() {
  searchQuery.value = ''
  searched.value = false
  searchResults.value = []
}

async function showDetail(docId: number) {
  try {
    detailData.value = await getAttractionKB(docId)
    detailVisible.value = true
  } catch (e) {
    console.error('获取景点详情失败', e)
    alert('获取景点详情失败')
  }
}

onMounted(() => { loadAll() })
</script>

<style scoped>
.manager-container { padding: 20px; max-width: 1400px; margin: 0 auto; }
.header-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.header-bar h2 { margin: 0; font-size: 18px; }
.actions { display: flex; gap: 10px; align-items: center; }
.btn-primary { background: #4f46e5; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; }
.btn-primary:hover { background: #4338ca; }
.btn-primary:disabled { background: #a5a5d4; cursor: not-allowed; }
.btn-secondary { background: #f3f4f6; color: #333; border: 1px solid #ddd; padding: 8px 16px; border-radius: 4px; cursor: pointer; }
.btn-sm { padding: 4px 10px; border: 1px solid #ddd; border-radius: 3px; background: white; cursor: pointer; font-size: 12px; }
.btn-sm:hover { background: #f5f5f5; }

/* 搜索栏 */
.kb-search-bar { display: flex; gap: 10px; margin-bottom: 12px; }
.kb-search-input { flex: 1; max-width: 500px; padding: 8px 12px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; box-sizing: border-box; }
.kb-search-input:focus { outline: none; border-color: #4f46e5; box-shadow: 0 0 0 2px rgba(79,70,229,0.1); }
.kb-stats { font-size: 13px; color: #888; margin-bottom: 16px; }
.kb-empty { text-align: center; color: #999; padding: 40px; font-size: 14px; }

/* 景点卡片 */
.kb-card { border: 1px solid #e5e7eb; border-radius: 6px; margin-bottom: 12px; padding: 16px; transition: box-shadow 0.2s; }
.kb-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
.kb-card-header { display: flex; justify-content: space-between; align-items: flex-start; }
.kb-card-title { font-size: 15px; font-weight: 600; margin: 0 0 4px; }
.kb-card-meta { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.meta-tag { background: #f3f4f6; color: #555; font-size: 12px; padding: 2px 8px; border-radius: 3px; }
.meta-score { color: #999; font-size: 13px; }
.kb-card-source { margin-top: 8px; font-size: 12px; color: #9ca3af; }
.kb-card-snippet { margin-top: 10px; font-size: 13px; color: #666; line-height: 1.5; white-space: pre-line; max-height: 80px; overflow: hidden; }

/* 弹窗 */
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; z-index: 100; }
.modal-content { background: white; border-radius: 8px; padding: 24px; width: 600px; max-width: 90vw; max-height: 85vh; overflow-y: auto; }
.modal-content h3 { margin: 0 0 20px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
.kb-pre { background: #f5f7fa; padding: 12px; border-radius: 4px; white-space: pre-wrap; word-break: break-word; font-size: 13px; line-height: 1.6; margin: 0; font-family: inherit; }
.center { text-align: center; color: #999; }
</style>
