<template>
  <div class="knowledge-base-container">
    <el-card class="knowledge-card">
      <template #header>
        <div class="card-header">
          <h2>企业知识库</h2>
          <div class="header-actions">
            <el-input
              v-model="searchKeyword"
              placeholder="搜索知识库..."
              style="width: 200px; margin-right: 10px"
              clearable
              @keyup.enter="handleSearch"
              @clear="handleSearch"
            >
              <template #prefix>
                <el-icon><Search /></el-icon>
              </template>
            </el-input>
            <el-button type="primary" @click="showAddDialog">
              <el-icon><Plus /></el-icon>
              新增知识
            </el-button>
          </div>
        </div>
      </template>

      <!-- 知识库列表 -->
      <div class="knowledge-list" v-loading="loading">
        <el-empty v-if="!loading && knowledgeList.length === 0" description="暂无知识库内容" />

        <div
          v-for="item in knowledgeList"
          :key="item.id"
          class="knowledge-item"
        >
          <div class="knowledge-content" @click="viewDetail(item)">
            <h3 class="knowledge-title">{{ item.title }}</h3>
            <p class="knowledge-excerpt">{{ item.content || '暂无内容摘要' }}</p>
            <div class="knowledge-meta">
              <el-tag v-if="item.attachments && item.attachments.length > 0" size="small">
                {{ item.attachments.length }} 个附件
              </el-tag>
              <span class="knowledge-date">创建于 {{ formatDate(item.created_at) }}</span>
            </div>
          </div>
          <div class="knowledge-actions">
            <el-button type="primary" link size="small" @click="viewDetail(item)">
              查看
            </el-button>
            <el-button type="danger" link size="small" @click="deleteKnowledge(item)">
              删除
            </el-button>
          </div>
        </div>
      </div>

      <!-- 分页 -->
      <div class="pagination-wrapper" v-if="total > 0">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="handleSizeChange"
          @current-change="handlePageChange"
        />
      </div>
    </el-card>

    <!-- 新增/查看详情对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="600px"
      :close-on-click-modal="false"
    >
      <!-- 查看详情模式 -->
      <div v-if="dialogMode === 'view'" class="detail-content">
        <h3>{{ currentKnowledge.title }}</h3>
        <p class="detail-text">{{ currentKnowledge.content }}</p>
        <div v-if="currentKnowledge.attachments && currentKnowledge.attachments.length > 0" class="detail-attachments">
          <h4>附件列表：</h4>
          <ul>
            <li v-for="(file, index) in currentKnowledge.attachments" :key="index">
              <el-icon><Document /></el-icon>
              {{ file.filename }}
            </li>
          </ul>
        </div>
      </div>

      <!-- 新增模式 -->
      <div v-else class="add-content">
        <el-form ref="formRef" :model="formData" :rules="formRules" label-position="top">
          <!-- 附件上传 -->
          <el-form-item label="上传附件（可选）">
            <el-upload
              ref="uploadRef"
              :auto-upload="false"
              :limit="10"
              :on-change="handleFileChange"
              :on-remove="handleFileRemove"
              multiple
              accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md"
              class="upload-section"
            >
              <el-button type="primary" plain>
                <el-icon><Upload /></el-icon>
                选择文件
              </el-button>
              <template #tip>
                <div class="upload-tip">支持 PDF、Word、Excel、PPT、TXT、Markdown 格式</div>
              </template>
            </el-upload>
          </el-form-item>

          <!-- 描述信息 -->
          <el-form-item label="描述" prop="description">
            <el-input
              v-model="formData.description"
              type="textarea"
              :rows="4"
              placeholder="请输入知识内容的描述，AI会自动提取标题和关键信息"
            />
          </el-form-item>

          <el-alert
            v-if="isProcessing"
            type="info"
            :closable="false"
            show-icon
            title="正在处理中..."
          >
            AI正在分析您上传的文件和描述信息，请稍候...
          </el-alert>
        </el-form>
      </div>

      <template #footer>
        <div v-if="dialogMode === 'view'">
          <el-button @click="dialogVisible = false">关闭</el-button>
        </div>
        <div v-else>
          <el-button @click="dialogVisible = false" :disabled="isProcessing">取消</el-button>
          <el-button type="primary" @click="submitKnowledge" :loading="isProcessing">
            {{ isProcessing ? '处理中...' : '创建知识' }}
          </el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search, Upload, Document } from '@element-plus/icons-vue'
import { knowledgeAPI } from '@/services/api'

const loading = ref(false)
const knowledgeList = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)
const searchKeyword = ref('')

// 对话框状态
const dialogVisible = ref(false)
const dialogTitle = ref('新增知识')
const dialogMode = ref('add') // 'add' | 'view'
const currentKnowledge = ref({})
const isProcessing = ref(false)

// 表单
const formRef = ref(null)
const uploadRef = ref(null)
const formData = reactive({
  description: ''
})
const uploadedFiles = ref([])

// 表单验证
const formRules = {
  description: [
    { required: true, message: '请输入描述信息', trigger: 'blur' },
    { min: 5, message: '描述信息至少5个字符', trigger: 'blur' }
  ]
}

// 格式化日期
const formatDate = (dateStr) => {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  return date.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

// 加载知识库列表
const loadKnowledge = async () => {
  loading.value = true
  try {
    const response = await knowledgeAPI.list({
      page: currentPage.value,
      page_size: pageSize.value,
      keyword: searchKeyword.value || undefined
    })
    knowledgeList.value = response.items || []
    total.value = response.total || 0
  } catch (error) {
    ElMessage.error('加载知识库失败')
  } finally {
    loading.value = false
  }
}

// 搜索
const handleSearch = () => {
  currentPage.value = 1
  loadKnowledge()
}

// 分页
const handlePageChange = (page) => {
  currentPage.value = page
  loadKnowledge()
}

const handleSizeChange = (size) => {
  pageSize.value = size
  currentPage.value = 1
  loadKnowledge()
}

// 显示新增对话框
const showAddDialog = () => {
  dialogMode.value = 'add'
  dialogTitle.value = '新增知识'
  formData.description = ''
  uploadedFiles.value = []
  dialogVisible.value = true
}

// 文件变化
const handleFileChange = (file, fileList) => {
  uploadedFiles.value = fileList
}

// 移除文件
const handleFileRemove = (file, fileList) => {
  uploadedFiles.value = fileList
}

// 提交知识
const submitKnowledge = async () => {
  if (!formData.description.trim()) {
    ElMessage.warning('请输入描述信息')
    return
  }

  isProcessing.value = true

  try {
    // TODO: 调用文件上传API，先获取文件URL
    const attachments = uploadedFiles.value.map(file => ({
      filename: file.name,
      path: file.raw?.path || '',
      size: file.size
    }))

    const requestData = {
      title: extractTitle(formData.description),
      content: formData.description,
      attachments: attachments
    }

    // 调用API创建知识库条目
    // 注意：这里简化处理，实际应该调用LLM提取标题
    const response = await knowledgeAPI.create(requestData)

    if (response.id) {
      ElMessage.success('知识创建成功')
      dialogVisible.value = false
      await loadKnowledge()
    }
  } catch (error) {
    ElMessage.error('创建知识失败: ' + (error.message || '未知错误'))
  } finally {
    isProcessing.value = false
  }
}

// 从描述中提取标题（简单处理）
const extractTitle = (description) => {
  // 取描述的前30个字符作为标题
  const title = description.substring(0, 30)
  return title.length < description.length ? title + '...' : title
}

// 查看详情
const viewDetail = (item) => {
  dialogMode.value = 'view'
  dialogTitle.value = '知识详情'
  currentKnowledge.value = item
  dialogVisible.value = true
}

// 删除知识
const deleteKnowledge = async (item) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除知识 "${item.title}" 吗？`,
      '删除确认',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    await knowledgeAPI.delete(item.id)
    ElMessage.success('删除成功')
    await loadKnowledge()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败')
    }
  }
}

// 初始化
onMounted(() => {
  loadKnowledge()
})
</script>

<style lang="scss" scoped>
.knowledge-base-container {
  .knowledge-card {
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;

      h2 {
        margin: 0;
        font-size: 18px;
      }

      .header-actions {
        display: flex;
        align-items: center;
      }
    }
  }

  .knowledge-list {
    min-height: 200px;
  }

  .knowledge-item {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    padding: 15px;
    border-bottom: 1px solid #f0f0f0;
    transition: background-color 0.2s;

    &:hover {
      background-color: #f5f7fa;
    }

    &:last-child {
      border-bottom: none;
    }

    .knowledge-content {
      flex: 1;
      cursor: pointer;

      .knowledge-title {
        margin: 0 0 8px;
        font-size: 16px;
        color: #303133;
      }

      .knowledge-excerpt {
        margin: 0 0 8px;
        font-size: 14px;
        color: #909399;
        overflow: hidden;
        text-overflow: ellipsis;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
      }

      .knowledge-meta {
        display: flex;
        align-items: center;
        gap: 10px;
        font-size: 12px;
        color: #c0c4cc;

        .knowledge-date {
          color: #c0c4cc;
        }
      }
    }

    .knowledge-actions {
      display: flex;
      gap: 10px;
      margin-left: 20px;
    }
  }

  .pagination-wrapper {
    display: flex;
    justify-content: center;
    margin-top: 20px;
  }

  // 对话框内容
  .detail-content {
    h3 {
      margin-top: 0;
      color: #303133;
    }

    .detail-text {
      color: #606266;
      line-height: 1.6;
      white-space: pre-wrap;
    }

    .detail-attachments {
      margin-top: 20px;
      padding-top: 15px;
      border-top: 1px solid #ebeef5;

      h4 {
        margin: 0 0 10px;
        color: #606266;
      }

      ul {
        list-style: none;
        padding: 0;
        margin: 0;

        li {
          display: flex;
          align-items: center;
          gap: 5px;
          padding: 5px 0;
          color: #409eff;
        }
      }
    }
  }

  .add-content {
    .upload-section {
      .upload-tip {
        font-size: 12px;
        color: #909399;
        margin-top: 5px;
      }
    }
  }
}
</style>
