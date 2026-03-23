<template>
  <div class="image-generation">
    <el-row :gutter="20">
      <el-col :span="14">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>图像生成</span>
              <el-select v-model="selectedTool" placeholder="选择工具" style="width: 160px;">
                <el-option label="Stable Diffusion" value="stable-diffusion" />
                <el-option label="Midjourney" value="midjourney" />
              </el-select>
            </div>
          </template>
          
          <el-form label-position="top">
            <el-form-item label="提示词">
              <el-input
                v-model="prompt"
                type="textarea"
                :rows="4"
                placeholder="描述您想要的图像..."
              />
            </el-form-item>
            
            <el-row :gutter="20">
              <el-col :span="8">
                <el-form-item label="宽度">
                  <el-input-number v-model="width" :min="256" :max="2048" :step="64" />
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="高度">
                  <el-input-number v-model="height" :min="256" :max="2048" :step="64" />
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="生成步数">
                  <el-input-number v-model="steps" :min="1" :max="100" />
                </el-form-item>
              </el-col>
            </el-row>
            
            <el-form-item label="负面提示词（可选）">
              <el-input
                v-model="negativePrompt"
                type="textarea"
                :rows="2"
                placeholder="不想在图像中出现的元素..."
              />
            </el-form-item>
            
            <el-form-item>
              <el-button type="primary" @click="generateImage" :loading="generating" style="width: 100%;">
                <el-icon><Picture /></el-icon>
                生成图像
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>
      
      <el-col :span="10">
        <el-card>
          <template #header>
            <span>生成结果</span>
          </template>
          
          <div class="result-container">
            <div v-if="generating" class="loading-state">
              <el-icon :size="48" class="loading-icon"><Loading /></el-icon>
              <p>正在生成图像，请稍候...</p>
            </div>
            
            <div v-else-if="result" class="result-image">
              <el-image :src="result.image_url" fit="contain" />
              <div class="image-info">
                <el-tag type="info" size="small">{{ result.prompt?.substring(0, 50) }}...</el-tag>
              </div>
              <div class="result-actions">
                <el-button type="primary" @click="downloadImage">
                  <el-icon><Download /></el-icon>
                  下载
                </el-button>
                <el-button @click="regenerate">
                  <el-icon><Refresh /></el-icon>
                  重新生成
                </el-button>
              </div>
            </div>
            
            <el-empty v-else description="暂无生成结果" />
          </div>
        </el-card>
        
        <el-card style="margin-top: 20px;">
          <template #header>
            <span>历史记录</span>
          </template>
          
          <div class="history-list">
            <div v-for="item in history" :key="item.id" class="history-item" @click="viewHistory(item)">
              <el-image :src="item.thumbnail" fit="cover" />
              <div class="history-info">
                <span>{{ item.prompt.substring(0, 30) }}...</span>
                <small>{{ formatTime(item.createdAt) }}</small>
              </div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { Picture, Download, Refresh, Loading } from '@element-plus/icons-vue'
import { mcpAPI } from '@/services/api'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'

const selectedTool = ref('stable-diffusion')
const prompt = ref('')
const negativePrompt = ref('')
const width = ref(512)
const height = ref(512)
const steps = ref(20)
const generating = ref(false)
const result = ref(null)
const history = ref([
  { id: 1, thumbnail: '', prompt: '山水画风格的风景', createdAt: new Date() },
  { id: 2, thumbnail: '', prompt: '现代城市夜景', createdAt: new Date(Date.now() - 86400000) }
])

const formatTime = (time) => {
  return dayjs(time).format('MM-DD HH:mm')
}

const generateImage = async () => {
  if (!prompt.value.trim()) {
    ElMessage.warning('请输入提示词')
    return
  }
  
  generating.value = true
  try {
    const response = await mcpAPI.generate_image(
      prompt.value,
      selectedTool.value,
      {
        width: width.value,
        height: height.value,
        steps: steps.value,
        negative_prompt: negativePrompt.value
      }
    )
    result.value = response.result
    
    history.value.unshift({
      id: Date.now(),
      thumbnail: response.result.image_url,
      prompt: prompt.value,
      createdAt: new Date()
    })
    
    ElMessage.success('图像生成成功')
  } catch (error) {
    ElMessage.error(`生成失败: ${error.message}`)
  } finally {
    generating.value = false
  }
}

const downloadImage = () => {
  if (result.value?.image_url) {
    window.open(result.value.image_url, '_blank')
  }
}

const regenerate = () => {
  if (prompt.value) {
    generateImage()
  }
}

const viewHistory = (item) => {
  prompt.value = item.prompt
}
</script>

<style lang="scss" scoped>
.image-generation {
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  
  .result-container {
    min-height: 400px;
    
    .loading-state {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      height: 300px;
      
      .loading-icon {
        animation: rotate 2s linear infinite;
      }
      
      p {
        margin-top: 15px;
        color: #909399;
      }
    }
    
    .result-image {
      .el-image {
        width: 100%;
        max-height: 300px;
        border-radius: 8px;
      }
      
      .image-info {
        margin: 10px 0;
      }
      
      .result-actions {
        display: flex;
        gap: 10px;
      }
    }
  }
  
  .history-list {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
    
    .history-item {
      cursor: pointer;
      border-radius: 6px;
      overflow: hidden;
      border: 1px solid #e4e7ed;
      transition: all 0.2s;
      
      &:hover {
        border-color: #409eff;
      }
      
      .el-image {
        width: 100%;
        height: 80px;
      }
      
      .history-info {
        padding: 8px;
        
        span {
          display: block;
          font-size: 12px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        
        small {
          color: #909399;
        }
      }
    }
  }
}

@keyframes rotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
