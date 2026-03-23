<template>
  <div class="video-generation">
    <el-row :gutter="20">
      <el-col :span="14">
        <el-card>
          <template #header>
            <span>视频生成</span>
          </template>
          
          <el-form label-position="top">
            <el-form-item label="视频描述">
              <el-input
                v-model="prompt"
                type="textarea"
                :rows="4"
                placeholder="描述您想要的视频内容..."
              />
            </el-form-item>
            
            <el-row :gutter="20">
              <el-col :span="12">
                <el-form-item label="视频时长（秒）">
                  <el-select v-model="duration" style="width: 100%;">
                    <el-option label="5秒" :value="5" />
                    <el-option label="10秒" :value="10" />
                    <el-option label="15秒" :value="15" />
                    <el-option label="30秒" :value="30" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="分辨率">
                  <el-select v-model="resolution" style="width: 100%;">
                    <el-option label="720p" value="720p" />
                    <el-option label="1080p" value="1080p" />
                    <el-option label="4K" value="4k" />
                  </el-select>
                </el-form-item>
              </el-col>
            </el-row>
            
            <el-form-item>
              <el-button type="primary" @click="generateVideo" :loading="generating" style="width: 100%;">
                <el-icon><VideoCamera /></el-icon>
                生成视频
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
              <p>正在生成视频，请稍候...</p>
              <el-progress :percentage="progress" />
            </div>
            
            <div v-else-if="result" class="result-video">
              <video :src="result.video_url" controls />
              <div class="video-info">
                <p>{{ result.prompt }}</p>
                <small>{{ result.duration }}s | {{ result.resolution }}</small>
              </div>
              <div class="result-actions">
                <el-button type="primary" @click="downloadVideo">
                  <el-icon><Download /></el-icon>
                  下载
                </el-button>
              </div>
            </div>
            
            <el-empty v-else description="暂无生成结果" />
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { VideoCamera, Download, Loading } from '@element-plus/icons-vue'
import { mcpAPI } from '@/services/api'
import { ElMessage } from 'element-plus'

const prompt = ref('')
const duration = ref(10)
const resolution = ref('1080p')
const generating = ref(false)
const progress = ref(0)
const result = ref(null)

const generateVideo = async () => {
  if (!prompt.value.trim()) {
    ElMessage.warning('请输入视频描述')
    return
  }
  
  generating.value = true
  progress.value = 0
  
  // 模拟进度
  const interval = setInterval(() => {
    if (progress.value < 90) {
      progress.value += 10
    }
  }, 1000)
  
  try {
    const response = await mcpAPI.generate_video(
      prompt.value,
      'sora',
      {
        duration: duration.value,
        resolution: resolution.value
      }
    )
    result.value = response.result
    progress.value = 100
    ElMessage.success('视频生成成功')
  } catch (error) {
    ElMessage.error(`生成失败: ${error.message}`)
  } finally {
    clearInterval(interval)
    generating.value = false
  }
}

const downloadVideo = () => {
  if (result.value?.video_url) {
    window.open(result.value.video_url, '_blank')
  }
}
</script>

<style lang="scss" scoped>
.video-generation {
  .result-container {
    min-height: 300px;
    
    .loading-state {
      text-align: center;
      padding: 20px;
      
      .loading-icon {
        animation: rotate 2s linear infinite;
      }
      
      p {
        margin: 15px 0;
        color: #909399;
      }
    }
    
    .result-video {
      video {
        width: 100%;
        border-radius: 8px;
      }
      
      .video-info {
        margin: 10px 0;
        
        p {
          margin: 0 0 5px;
        }
        
        small {
          color: #909399;
        }
      }
      
      .result-actions {
        margin-top: 15px;
      }
    }
  }
}

@keyframes rotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
