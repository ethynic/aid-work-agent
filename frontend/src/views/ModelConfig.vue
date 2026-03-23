<template>
  <div class="model-config">
    <el-row :gutter="20">
      <el-col :span="8" v-for="provider in providers" :key="provider.id">
        <el-card class="provider-card" :class="{ enabled: enabledModels.includes(provider.id) }">
          <template #header>
            <div class="provider-header">
              <div class="provider-info">
                <h3>{{ provider.name }}</h3>
                <p>{{ provider.description }}</p>
              </div>
              <el-switch v-model="enabledModels" :active-value="provider.id" />
            </div>
          </template>
          
          <div class="provider-content">
            <div class="models-list">
              <el-tag 
                v-for="model in provider.models" 
                :key="model"
                :type="getModelTagType(provider.id, model)"
                size="small"
                class="model-tag"
              >
                {{ model }}
              </el-tag>
            </div>
            
            <div class="features">
              <el-icon><Check /></el-icon>
              <span v-for="feature in provider.features" :key="feature">{{ feature }}</span>
            </div>
            
            <el-button 
              type="primary" 
              plain 
              size="small" 
              @click="configureProvider(provider)"
            >
              配置API
            </el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>
    
    <el-card style="margin-top: 20px;">
      <template #header>
        <div class="card-header">
          <span>已配置模型</span>
        </div>
      </template>
      
      <el-table :data="configuredModels" stripe>
        <el-table-column prop="name" label="模型名称" />
        <el-table-column prop="provider" label="提供商" />
        <el-table-column prop="model_id" label="模型ID" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'danger'" size="small">
              {{ row.enabled ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="editModel(row)">编辑</el-button>
            <el-button type="danger" link size="small" @click="deleteModel(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
    
    <el-dialog v-model="showConfigDialog" :title="`配置 ${currentProvider?.name}`" width="500px">
      <el-form label-width="100px">
        <el-form-item label="API密钥">
          <el-input v-model="configForm.api_key" type="password" show-password />
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input v-model="configForm.base_url" placeholder="留空使用默认地址" />
        </el-form-item>
        <el-form-item label="模型ID">
          <el-select v-model="configForm.model_id" placeholder="选择模型" style="width: 100%;">
            <el-option 
              v-for="model in currentProvider?.models" 
              :key="model"
              :label="model"
              :value="model"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showConfigDialog = false">取消</el-button>
        <el-button type="primary" @click="saveConfig">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { Check } from '@element-plus/icons-vue'
import { llmAPI } from '@/services/api'
import { ElMessage } from 'element-plus'

const enabledModels = ref([])
const showConfigDialog = ref(false)
const currentProvider = ref(null)
const configuredModels = ref([])

const configForm = ref({
  api_key: '',
  base_url: '',
  model_id: ''
})

const providers = ref([
  {
    id: 'deepseek',
    name: 'DeepSeek',
    description: '深度求索大模型',
    models: ['deepseek-chat', 'deepseek-coder'],
    features: ['对话', '代码', '推理']
  },
  {
    id: 'doubao',
    name: '豆包',
    description: '字节跳动大模型',
    models: ['doubao-pro-32k', 'doubao-pro-128k'],
    features: ['对话', '创意', '分析']
  },
  {
    id: 'qwen',
    name: '阿里千问',
    description: '阿里巴巴大语言模型',
    models: ['qwen-turbo', 'qwen-plus', 'qwen-max', 'qwen3.5-plus', 'qwen2.5-72b-instruct', 'qwen2.5-32b-instruct'],
    features: ['对话', '知识', '推理']
  },
  {
    id: 'openai',
    name: 'OpenAI',
    description: 'OpenAI GPT系列',
    models: ['gpt-4', 'gpt-4-turbo', 'gpt-3.5-turbo'],
    features: ['对话', '代码', '分析']
  },
  {
    id: 'anthropic',
    name: 'Anthropic',
    description: 'Anthropic Claude系列',
    models: ['claude-3-opus-20240229', 'claude-3-sonnet-20240229'],
    features: ['对话', '推理', '安全']
  },
  {
    id: 'glm',
    name: '智谱GLM',
    description: '智谱AI大模型',
    models: ['glm-4', 'glm-3-turbo'],
    features: ['对话', '分析', '多模态']
  },
  {
    id: 'kimi',
    name: 'Kimi',
    description: '月之暗面大模型',
    models: ['moonshot-v1-32k', 'moonshot-v1-128k'],
    features: ['对话', '长文本', '知识']
  }
])

const getModelTagType = (provider, model) => {
  if (enabledModels.value.includes(provider)) return 'success'
  return 'info'
}

const configureProvider = (provider) => {
  currentProvider.value = provider
  configForm.value = {
    api_key: '',
    base_url: '',
    model_id: provider.models[0]
  }
  showConfigDialog.value = true
}

const saveConfig = async () => {
  try {
    await llmAPI.config({
      name: currentProvider.value.id,
      provider: currentProvider.value.id,
      api_key: configForm.value.api_key,
      base_url: configForm.value.base_url || undefined,
      model_id: configForm.value.model_id,
      enabled: true
    })
    
    configuredModels.value.push({
      name: currentProvider.value.id,
      provider: currentProvider.value.name,
      model_id: configForm.value.model_id,
      enabled: true
    })
    
    ElMessage.success('配置保存成功')
    showConfigDialog.value = false
  } catch (error) {
    ElMessage.error(`配置失败: ${error.message}`)
  }
}

const editModel = (model) => {
  // 编辑模型
}

const deleteModel = async (model) => {
  try {
    await llmAPI.delete_config(model.name)
    const index = configuredModels.value.findIndex(m => m.name === model.name)
    if (index > -1) {
      configuredModels.value.splice(index, 1)
    }
    ElMessage.success('删除成功')
  } catch (error) {
    ElMessage.error(`删除失败: ${error.message}`)
  }
}
</script>

<style lang="scss" scoped>
.model-config {
  .provider-card {
    transition: all 0.3s;
    
    &.enabled {
      border-color: #67c23a;
      box-shadow: 0 0 0 2px rgba(103, 194, 58, 0.2);
    }
    
    .provider-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      
      .provider-info {
        h3 {
          margin: 0 0 5px;
          font-size: 16px;
        }
        
        p {
          margin: 0;
          font-size: 12px;
          color: #909399;
        }
      }
    }
    
    .provider-content {
      .models-list {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-bottom: 15px;
        
        .model-tag {
          margin: 0;
        }
      }
      
      .features {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-bottom: 15px;
        font-size: 12px;
        color: #909399;
        
        .el-icon {
          color: #67c23a;
          margin-right: 4px;
        }
      }
    }
  }
}
</style>
