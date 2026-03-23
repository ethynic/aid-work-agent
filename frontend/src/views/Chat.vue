<template>
  <div class="chat-container">
    <div class="chat-layout">
      <div class="chat-sidebar">
        <div class="sidebar-header">
          <el-button type="primary" @click="startNewChat" style="width: 100%;">
            <el-icon>
              <Plus />
            </el-icon>
            新建对话
          </el-button>
        </div>

        <!-- 对话历史区域 -->
        <div class="chat-history-section">
          <div class="section-header">
            <h3>对话历史</h3>
          </div>
          <div class="chat-history">
            <div class="history-item" v-for="chat in chatHistory" :key="chat.id"
              :class="{ active: currentChat?.id === chat.id }" @click="selectChat(chat)">
              <el-icon>
                <ChatLineRound />
              </el-icon>
              <span class="title">{{ chat.title }}</span>
              <el-dropdown trigger="click" @command="(cmd) => handleChatAction(cmd, chat)">
                <el-icon class="more-icon">
                  <MoreFilled />
                </el-icon>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="rename">重命名</el-dropdown-item>
                    <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </div>
        </div>
      </div>

      <div class="chat-main">
        <div class="chat-header">
          <div class="model-selector">
            <span>智能体：</span>
            <el-select v-model="currentAgentId" placeholder="选择智能体" style="width: 200px;" @change="handleAgentChange">
              <el-option
                v-for="agent in availableAgents"
                :key="agent.agent_id"
                :label="agent.display_name || agent.name"
                :value="agent.agent_id"
              />
            </el-select>
          </div>
          <div class="model-selector">
            <span>使用模型：</span>
            <el-select v-model="selectedModel" placeholder="选择模型" style="width: 180px;">
              <template v-for="item in availableModels" :key="item.id">
                <el-option v-if="item && item.enabled" :label="item.name" :value="item.id" />
              </template>
            </el-select>
          </div>
          <div class="chat-actions">
            <el-button :icon="Refresh" circle @click="clearChat" />
            <el-button :icon="Setting" circle @click="showSettings = true" />
          </div>
        </div>

        <div class="chat-messages" ref="messagesContainer">
          <div class="welcome-message" v-if="messages.length === 0">
            <el-icon :size="64">
              <ChatLineSquare />
            </el-icon>
            <h2>{{ currentAgent ? currentAgent.display_name || currentAgent.name : '欢迎使用企业智能体' }}</h2>

            <template v-if="currentAgent">
              <p class="agent-intro">{{ currentAgent.description || '这是一个企业智能助手，可以帮助您完成多种任务。' }}</p>

              <div v-if="currentAgent.skills && currentAgent.skills.length > 0" class="agent-capabilities">
                <h3>核心技能</h3>
                <ul>
                  <li v-for="skill in currentAgent.skills" :key="skill.skill_id">
                    <strong>{{ skill.name }}</strong>
                    <span v-if="skill.description">{{ skill.description }}</span>
                  </li>
                </ul>
              </div>

              <div v-if="currentAgent.mcps && currentAgent.mcps.length > 0" class="agent-mcps">
                <h3>可用工具</h3>
                <div class="mcps-list">
                  <el-tag v-for="mcp in currentAgent.mcps" :key="mcp.mcp_id" size="small" effect="plain" type="info">
                    {{ mcp.name }}
                  </el-tag>
                </div>
              </div>
            </template>

            <template v-else>
              <p class="agent-intro">我是您的企业智能助手，可以帮助您完成多种任务。我具备以下能力：</p>
              <ul class="agent-capabilities">
                <li><strong>智能对话</strong>：与各类大模型进行自然语言交互，解答问题、提供建议</li>
                <li><strong>多模态生成</strong>：调用MCP工具生成高质量图像和视频内容</li>
                <li><strong>数据库分析</strong>：连接企业数据库，执行查询、分析数据、生成报表</li>
                <li><strong>复杂任务处理</strong>：分解和执行复杂的业务流程和数据处理任务</li>
                <li><strong>文件处理</strong>：上传和分析各种格式的文件，提取关键信息</li>
                <li><strong>实时协作</strong>：支持流式响应，实时展示任务执行过程</li>
              </ul>
            </template>

            <div class="input-prompt">
              <el-icon class="prompt-icon">
                <Message />
              </el-icon>
              <p>{{ currentAgent ? `请在下方对话框中输入您需要${currentAgent.display_name ||
                currentAgent.name}执行的任务需求，我将为您提供专业的支持和解决方案。` :
                '请在下方对话框中输入您需要执行的任务需求，我将为您提供专业的支持和解决方案。' }}</p>
            </div>
          </div>

          <div class="message-list">
            <div v-for="msg in messages" :key="msg.id" class="message" :class="msg.role"
              v-memo="[msg.id, msg.content, msg.reasoning_content, msg.role]">
              <div class="message-avatar">
                <el-avatar v-if="msg.role === 'user'" :size="36">我</el-avatar>
                <el-avatar v-else :size="36" style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);">
                  <el-icon>
                    <ChatLineSquare />
                  </el-icon>
                </el-avatar>
              </div>

              <div class="message-content">
                <div class="message-role">{{ msg.role === 'user' ? '我' : '智能体' }}</div>

                <!-- 思考过程：始终显示，有正式回答时显示在正式回答前面 -->
                <div class="message-reasoning"
                  v-if="msg.reasoning_content && msg.reasoning_content !== undefined && msg.reasoning_content !== null">
                  <div class="reasoning-content" v-html="formatMessage(msg.reasoning_content)"></div>
                </div>

                <!-- 正式回答 -->
                <div class="message-text" v-if="msg.content">
                  <div v-html="formatMessage(msg.content)"></div>

                  <!-- 查看完整报告链接 -->
                  <div v-if="msg.doc_url" class="message-doc-link">
                    <a :href="getFullDocUrl(msg.doc_url)" target="_blank" class="doc-link">
                      <el-icon><Document /></el-icon>
                      查看完整报告
                    </a>
                  </div>

                  <!-- 选项按钮（放到正式回答区域里面） -->
                  <div class="message-options" v-if="msg.is_question && msg.options && msg.options.length > 0">
                    <el-button 
                      v-for="(option, index) in msg.options" 
                      :key="index" 
                      type="primary" 
                      size="small" 
                      class="option-button"
                      @click="handleOptionSelect(msg, option, index)"
                    >
                      {{ option }}
                    </el-button>
                  </div>
                </div>

                <div class="message-tools" v-if="msg.tool_calls?.length">
                  <el-divider content-position="left">调用工具</el-divider>
                  <div v-for="tool in msg.tool_calls" :key="tool.id" class="tool-call">
                    <el-tag type="warning" size="small">{{ tool.name }}</el-tag>
                    <pre>{{ JSON.stringify(tool.input, null, 2) }}</pre>
                  </div>
                </div>

                <div class="message-attachments" v-if="msg.attachments?.length">
                  <div v-for="att in msg.attachments" :key="att.name" class="attachment">
                    <el-icon>
                      <Document />
                    </el-icon>
                    <a :href="att.url" target="_blank">{{ att.name }}</a>
                  </div>
                </div>

                <div class="message-time">{{ formatTime(msg.createdAt, msg.updatedAt) }}</div>
              </div>
            </div>

            <div class="message loading" v-if="isLoading">
              <div class="message-avatar">
                <el-avatar :size="36" style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);">
                  <el-icon>
                    <Loading />
                  </el-icon>
                </el-avatar>
              </div>
              <div class="message-content">
                <div class="typing-indicator">
                  <span></span><span></span><span></span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="chat-input">
          <div class="input-container">
            <!-- 录音状态指示器 -->
            <div class="recording-indicator" v-if="isRecording">
              <div class="recording-dot"></div>
              <span class="recording-text">正在录音中...点击麦克风按钮停止</span>
            </div>
            
            <!-- 上传文件显示区域 -->
            <div class="uploaded-files" v-if="uploadedFiles.length > 0">
              <div class="file-item" v-for="file in uploadedFiles" :key="file.id">
                <el-button :icon="isImageFile(file) ? ZoomIn : Document" circle size="small"
                  @click.stop="handlePreview(file)" class="file-preview-btn" />
                <span class="file-name" @click="handlePreview(file)">{{ file.originalFilename }}</span>
                <el-button :icon="Delete" circle size="small" type="danger" @click.stop="removeFile(file.id)" />
              </div>
            </div>

            <el-input v-model="userInput" type="textarea" :rows="3" placeholder="输入您的问题或指令..." resize="none"
              @keydown.enter.exact.prevent="sendMessage" />
            <div class="input-actions">
              <div class="left-actions">
                <el-button :icon="Upload" circle size="small" @click="handleFileClick('file')" :loading="isUploading"
                  title="上传文件 (txt, pdf, word, ppt, excel)" />
                <input ref="fileInputRef" type="file" multiple style="display: none" @change="handleFileChange"
                  accept=".txt,.pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx" />
                <el-button :icon="Picture" circle size="small" @click="handleFileClick('image')" :loading="isUploading"
                  title="上传图片" />
                <input ref="imageInputRef" type="file" multiple style="display: none" @change="handleFileChange"
                  accept="image/jpeg,image/png,image/gif,image/webp,image/bmp" />
                <el-button :icon="Connection" circle size="small" @click="showDatabasePanel = true" />
                <el-button 
                  :icon="isRecording ? VideoPause : Microphone" 
                  circle 
                  size="small" 
                  @click="toggleVoiceRecording" 
                  :type="isRecording ? 'danger' : 'default'"
                  :title="isRecording ? '停止录音' : '语音输入'" 
                />
              </div>
              <el-button type="primary" :icon="Position" @click="sendMessage" :loading="isLoading">
                发送
              </el-button>
            </div>
          </div>
        </div>
      </div>

      <!-- 智能体详情面板 -->
      <div class="chat-agent-panel" v-if="currentAgent">
        <div class="agent-panel-header">
          <h3>智能体信息</h3>
          <el-button v-if="isAdmin" type="primary" size="small" @click="openEditAgentDialog">
            <el-icon>
              <Edit />
            </el-icon>
            编辑
          </el-button>
        </div>

        <div class="agent-info">
          <div class="agent-avatar-container">
            <el-avatar :size="120" :src="currentAgent.avatar">
              {{ currentAgent.display_name?.charAt(0) }}
            </el-avatar>
          </div>

          <div class="agent-details">
            <div class="agent-name">{{ currentAgent.name }}</div>
            <div class="agent-display-name">{{ currentAgent.display_name }}</div>
            <div class="agent-type">{{ currentAgent.position }}</div>

            <div class="agent-section">
              <h4>简介</h4>
              <p>{{ currentAgent.description }}</p>
            </div>

            <div class="agent-section">
              <h4>技能 (Skills)</h4>
              <div class="agent-skills">
                <el-tag v-for="skill in currentAgent.skills" :key="skill.skill_id" size="small" effect="plain">
                  {{ skill.name }}
                </el-tag>
              </div>
            </div>

            <div class="agent-section">
              <h4>MCP</h4>
              <div class="agent-mcps">
                <el-tag v-for="mcp in currentAgent.mcps" :key="mcp.mcp_id" size="small" effect="plain" type="info">
                  {{ mcp.name }}
                </el-tag>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <el-drawer v-model="showDatabasePanel" title="数据库查询" size="50%">
      <DatabaseQueryPanel @insert-query="insertQuery" />
    </el-drawer>

    <el-dialog v-model="showSettings" title="对话设置" width="500px">
      <el-form label-width="100px">
        <el-form-item label="温度参数">
          <el-slider v-model="chatSettings.temperature" :min="0" :max="2" :step="0.1" />
        </el-form-item>
        <el-form-item label="最大Token">
          <el-input-number v-model="chatSettings.maxTokens" :min="100" :max="4096" :step="100" />
        </el-form-item>
        <el-form-item label="启用流式">
          <el-switch v-model="chatSettings.streaming" />
        </el-form-item>
      </el-form>
    </el-dialog>

    <!-- 智能体选择对话框 -->
    <el-dialog v-model="showAgentSelector" title="选择智能体" width="600px" :close-on-click-modal="false">
      <div v-if="loadingAgents" class="loading-container">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span>加载智能体列表...</span>
      </div>
      <div v-else-if="availableAgents.length === 0" class="empty-agents">
        <el-empty description="暂无可用智能体，请联系管理员创建智能体" />
      </div>
      <div v-else class="agent-selector-content">
        <el-radio-group v-model="tempSelectedAgent" class="agent-list">
          <el-radio v-for="agent in availableAgents" :key="agent.agent_id" :label="agent" class="agent-item">
            <div class="agent-info">
              <div class="agent-name">{{ agent.display_name || agent.name }}</div>
              <div class="agent-description">{{ agent.description || '暂无描述' }}</div>
              <div v-if="agent.skills && agent.skills.length > 0" class="agent-tags">
                <el-tag v-for="skill in agent.skills.slice(0, 3)" :key="skill.skill_id" size="small" type="success">
                  {{ skill.name }}
                </el-tag>
              </div>
            </div>
          </el-radio>
        </el-radio-group>
      </div>
      <template #footer>
        <el-button @click="showAgentSelector = false">取消</el-button>
        <el-button type="primary" @click="confirmAgentSelection" :disabled="!tempSelectedAgent">确定</el-button>
      </template>
    </el-dialog>

    <!-- 图片预览组件 -->
    <el-image-viewer v-if="previewVisible" :url-list="[previewImage]" @close="handleClosePreview" />

    <!-- 编辑智能体对话框 -->
    <el-dialog v-model="editAgentDialogVisible" title="编辑智能体" width="700px">
      <el-form :model="agentForm" label-position="top">
        <el-form-item label="智能体名称">
          <el-input v-model="agentForm.name" placeholder="请输入智能体名称" />
        </el-form-item>

        <el-form-item label="简介">
          <el-input v-model="agentForm.description" type="textarea" placeholder="请输入智能体简介" :rows="3" />
        </el-form-item>

        <el-form-item label="技能">
          <el-checkbox-group v-model="agentForm.skills">
            <el-checkbox v-for="skill in availableSkills" :key="skill.skill_id" :label="skill.skill_id">
              {{ skill.name }} ({{ skill.description }})
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>

        <el-form-item label="MCP">
          <el-checkbox-group v-model="agentForm.mcps">
            <el-checkbox v-for="mcp in availableMCPs" :key="mcp.mcp_id" :label="mcp.mcp_id">
              {{ mcp.name }} ({{ mcp.description }})
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="editAgentDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveAgentEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { marked } from 'marked'
import hljs from 'highlight.js'
import { Plus, ChatLineRound, MoreFilled, Refresh, Setting, Picture, Connection, Position, Document, Loading, Upload, Delete, ZoomIn, Edit, Message, Warning, ChatLineSquare, Microphone, VideoPause } from '@element-plus/icons-vue'
import { ElMessage, ElImageViewer, ElMessageBox } from 'element-plus'
import { llmAPI, mcpAPI, uploadAPI, employeeAPI, API_BASE } from '@/services/api'
import { useGlobalStore } from '@/stores'
import dayjs from 'dayjs'
import DatabaseQueryPanel from '@/components/DatabaseQueryPanel.vue'

// 辅助函数：将对象转换为可读字符串
const formatResult = (result) => {
  if (result === null || result === undefined) {
    return ''
  }
  // 如果是字符串，尝试解析为 JSON
  if (typeof result === 'string') {
    try {
      const parsed = JSON.parse(result)
      // 如果解析后的对象有 message 字段，提取出来
      if (parsed && typeof parsed === 'object' && parsed.message) {
        return formatResult(parsed.message)
      }
      return formatResult(parsed)
    } catch (e) {
      return result
    }
  }
  if (typeof result === 'object') {
    try {
      return JSON.stringify(result, null, 2)
    } catch (e) {
      return String(result)
    }
  }
  return String(result)
}

// 辅助函数：格式化任务结果显示
const formatTaskResult = (result) => {
  if (!result) return ''
  if (typeof result !== 'object') return String(result)
  
  // 1. 优先显示 natural_summary
  if (result.natural_summary) {
    return result.natural_summary
  }
  // 2. 如果 success 为 true，显示"成功"
  if (result.success === true) {
    return '成功'
  }
  // 3. 否则显示完整 JSON
  return formatResult(result)
}

const route = useRoute()
const router = useRouter()
const globalStore = useGlobalStore()

// 会话ID：用于memory记忆功能，页面刷新后保持同一会话
let sessionId = sessionStorage.getItem('chat_session_id')
if (!sessionId) {
  sessionId = 'web_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9)
  sessionStorage.setItem('chat_session_id', sessionId)
}

const messagesContainer = ref(null)
const userInput = ref('')
const isLoading = ref(false)
const showSettings = ref(false)
const showDatabasePanel = ref(false)
const showAgentSelector = ref(false)
const selectedModel = ref('qwen')
const currentChat = ref(null)


// 智能体相关
const currentAgent = ref(null)
const currentAgentId = ref(null)
const availableAgents = ref([])
const loadingAgents = ref(false)
const tempSelectedAgent = ref(null)
const isAdmin = ref(true) // 模拟管理员权限，实际应从登录状态获取
const editAgentDialogVisible = ref(false)
const agentForm = ref({
  name: '',
  description: '',
  skills: [],
  mcps: []
})
const availableSkills = ref([])
const availableMCPs = ref([])



// 上传文件相关
const uploadedFiles = ref([])
const isUploading = ref(false)
const fileInputRef = ref(null)
const imageInputRef = ref(null)

// 预览相关
const previewVisible = ref(false)
const previewImage = ref('')

// 语音识别相关
const isRecording = ref(false)
const recognition = ref(null)
const interimTranscript = ref('')
const mediaRecorder = ref(null)
const audioChunks = ref([])

const messages = ref([])
const chatHistory = ref([
  { id: 1, title: '数据库结构分析讨论' },
  { id: 2, title: '图像生成测试' },
  { id: 3, title: '业务数据查询' }
])

const chatSettings = ref({
  temperature: 0.7,
  maxTokens: 2000,
  streaming: true
})

const availableModels = ref([
  { id: 'kimi', name: 'Kimi (Moonshot)', enabled: true }
])

// 缓存marked实例，避免重复初始化
const markedInstance = marked.setOptions({
  highlight: (code, lang) => {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(code, { language: lang }).value
    }
    return hljs.highlightAuto(code).value
  },
  breaks: true,
  gfm: true
})

// 缓存格式化结果
const messageCache = new Map()

// 获取后端基础URL（用于静态文件如memories）
const getBackendBaseUrl = () => API_BASE

// 转换doc_url为完整URL
const getFullDocUrl = (docUrl) => {
  if (!docUrl) return ''
  if (docUrl.startsWith('http')) return docUrl
  return `${getBackendBaseUrl()}${docUrl}`
}

const formatMessage = (content) => {
  if (content === undefined || content === null) return ''
  if (content === '') return '' // 确保空字符串也返回空字符串

  // 检查缓存
  if (messageCache.has(content)) {
    return messageCache.get(content)
  }

  const result = markedInstance.parse(content)
  // 缓存结果
  messageCache.set(content, result)
  return result
}

const formatTime = (time, updatedAt) => {
  // 如果有最新更新时间，则显示最新更新时间
  if (updatedAt) {
    return dayjs(updatedAt).format('HH:mm:ss')
  }
  return dayjs(time).format('HH:mm:ss')
}

// 更新消息内容并同步更新时间
const updateMessageContent = (message, newContent) => {
  message.content = newContent
  message.updatedAt = new Date().toISOString()
  messages.value = [...messages.value]
}

const scrollToBottom = async () => {
  await nextTick()
  if (messagesContainer.value) {
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
  }
}

// =============================================================================
// 通用流数据处理函数 - 合并重复代码
// =============================================================================
const handleStreamDataType = async (message, data) => {
  // 思考过程类型处理
  if (data.type === 'tasks') {
    message.tasks = data.tasks
    message.reasoning_content = formatResult(data.message)
  } else if (data.type === 'task_start') {
    message.reasoning_content = (message.reasoning_content || '') + `\n\n开始执行任务 ${data.task_index + 1}: ${data.task_name}`
    if (data.description) {
      message.reasoning_content += `\n任务描述: ${data.description}`
    }
  } else if (data.type === 'task_end') {
    message.reasoning_content = (message.reasoning_content || '') + `\n✓ 任务 ${data.task_index + 1} 完成: ${data.task_name}`
    if (data.result) {
      message.reasoning_content += `\n结果: ${formatTaskResult(data.result)}`
    }
    if (data.reasoning_content) {
      message.reasoning_content += `\n\n${formatResult(data.reasoning_content)}`
    }
    // 保存 doc_url 以便在正式回复中显示报告链接
    if (data.doc_url) {
      message.doc_url = data.doc_url
    }
    message.results.push(data)
  } else if (data.type === 'processing') {
    message.reasoning_content = (message.reasoning_content || '') + `\n\n${formatResult(data.message)}`
  } else if (data.type === 'content') {
    // 流式输出内容 - 追加而非替换
    if (data.content) {
      message.content = (message.content || '') + formatResult(data.content)
    }
    // 保存 doc_url 以便渲染链接
    if (data.doc_url) {
      message.doc_url = data.doc_url
    }
  } else if (data.type === 'summary') {
    // summary 消息只显示内容，不添加"任务执行完成！"（由 [DONE] 时统一添加）
    message.content = formatResult(data.message)

    // 保存 doc_url 以便渲染链接
    if (data.doc_url) {
      message.doc_url = data.doc_url
    }
  } else if (data.type === 'skill_result') {
    // skill_result 消息 - 技能执行结果，显示在正式回答中
    message.content = formatResult(data.message)

    // 保存 doc_url 以便渲染链接
    if (data.doc_url) {
      message.doc_url = data.doc_url
    }

    // 如果有选项，设置选项
    if (data.options && data.options.length > 0) {
      message.is_question = true
      message.options = data.options
    }
  } else if (data.type === 'error') {
    message.content = `❌ 错误: ${formatResult(data.message)}`
  } else if (data.type === 'options') {
    message.content = `需要更多信息: ${formatResult(data.message)}`
  } else if (data.type === 'question') {
    message.content = formatResult(data.message)
    message.is_question = true
    message.question = data.question || data.message
    if (data.reasoning_content) {
      message.reasoning_content = (message.reasoning_content || '') + `\n\n${formatResult(data.reasoning_content)}`
    }
  } else if (data.type === 'needs_user_input') {
    if (data.input_type === 'selection') {
      message.content = formatResult(data.message)
      message.is_question = true
      message.question = formatResult(data.message)
      message.options = data.options
      message.task_name = data.task_name
      message.task_index = data.task_index
    } else {
      message.content = `需要更多信息: ${formatResult(data.message)}`
      message.is_question = true
      message.question = formatResult(data.message)
    }
  } else if (data.message) {
    // 其他未明确类型的消息 - 默认追加到 reasoning_content
    message.reasoning_content = (message.reasoning_content || '') + `\n${formatResult(data.message)}`
  } else if (data.content) {
    message.reasoning_content = (message.reasoning_content || '') + `\n${formatResult(data.content)}`
  }

  // 统一更新消息并滚动
  message.updatedAt = new Date().toISOString()
  messages.value = [...messages.value]
  await scrollToBottom()
}

// =============================================================================
// 重要：agent_chat_stream 接口文档见 doc/agent_chat_stream_api.md
// 此函数处理 SSE 流式响应，前端解析逻辑需与后端消息格式保持一致
// 消息类型分为：
// - 思考过程：processing, tasks, task_start, task_end → 显示在 reasoning_content
// - 正式回答：summary, error, question, content 等 → 显示在 content
// 如需修改解析逻辑，请同步更新接口文档
// =============================================================================
const sendMessage = async () => {
  if ((!userInput.value.trim() && uploadedFiles.value.length === 0) || isLoading.value) return

  // 检查是否选择了智能体，如果没有则显示选择对话框
  if (!currentAgent.value) {
    tempSelectedAgent.value = null
    showAgentSelector.value = true
    return
  }

  // 构建消息内容，包含文本和附件
  let messageContent = userInput.value.trim()

  // 如果有附件，添加附件信息
  if (uploadedFiles.value.length > 0) {
    // 将附件信息添加到消息内容中
    if (messageContent) {
      messageContent += '\n\n**附件：**\n' + uploadedFiles.value.map(file => `- ${file.originalFilename} (${(file.size / 1024).toFixed(1)}KB)`).join('\n')
    } else {
      messageContent = '**附件：**\n' + uploadedFiles.value.map(file => `- ${file.originalFilename} (${(file.size / 1024).toFixed(1)}KB)`).join('\n')
    }
  }

  const userMessage = {
    id: Date.now(),
    role: 'user',
    content: messageContent,
    createdAt: new Date().toISOString(),
    attachments: uploadedFiles.value.length > 0 ? [...uploadedFiles.value] : undefined
  }

  messages.value.push(userMessage)
  userInput.value = ''
  isLoading.value = true

  // 清空上传的文件
  const filesToSend = [...uploadedFiles.value]
  clearFiles()

  await scrollToBottom()

  try {
      // 构建发送给API的消息，确保包含所有历史上下文
      // 包括用户输入和之前的AI回复，确保完整的对话历史被传递给API
      const apiMessages = messages.value.map(m => ({
        role: m.role,
        content: m.content,
        attachments: m.attachments,
        reasoning_content: m.reasoning_content // 也传递思考过程，确保完整上下文
      }))
      
      // 收集所有历史消息中的附件信息
      const allAttachments = []
      messages.value.forEach(m => {
        if (m.attachments && m.attachments.length > 0) {
          allAttachments.push(...m.attachments)
        }
      })
      
      // 确保附件信息格式正确
      const formattedAttachments = allAttachments.map(file => ({
        filename: file.filename,
        originalFilename: file.originalFilename,
        url: file.url,
        size: file.size
      }))

    // 统一使用智能体流式模式
      try {
        // 构建流式请求，包含agent_id（如果有）
        // user_id: 真正的用户ID（从 globalStore.user 获取），用于记忆的用户隔离
        // session_id: 会话ID，用于区分同一用户的不同对话
        const requestData = {
          query: messageContent,
          messages: apiMessages,
          model: selectedModel.value,
          attachments: formattedAttachments.length > 0 ? formattedAttachments : undefined,
          user_id: globalStore.user?.id || globalStore.user?.user_id || 'anonymous',
          session_id: sessionId,
          platform: 'web'
        }

        // 如果有当前智能体或URL参数中有agentId，添加到请求中
        const agentId = currentAgent.value?.agent_id || route.query.agentId
        if (agentId) {
          requestData.agent_id = agentId
        }

        let streamResponse
        if (agentId) {
          // 有智能体ID，使用智能体流式接口
          streamResponse = await llmAPI.agent_chat_stream(requestData)
        } else {
          // 没有智能体ID，使用普通流式聊天接口
          streamResponse = await llmAPI.chat_stream({
            messages: apiMessages,
            model: selectedModel.value
          })
        }

        // 检查响应状态
        if (!streamResponse || !streamResponse.ok) {
          throw new Error('无效的响应')
        }

        // 处理流式响应
        let reader
        if (streamResponse.body && typeof streamResponse.body.getReader === 'function') {
          reader = streamResponse.body.getReader()
        } else {
          // 尝试作为普通响应处理
          try {

          // 实时显示任务执行过程
          // 创建一个新的消息对象用于单独显示最新的大模型回复
          let taskExecutionMessageId = Date.now() + 1
          let taskExecutionMessage = {
            id: taskExecutionMessageId,
            role: 'assistant',
            content: '',
            reasoning_content: undefined, // 初始化为undefined，避免显示空的思考过程区域
            createdAt: new Date().toISOString(),
            updatedAt: null, // 追踪最新更新时间
            type: 'agent_execution',
            tasks: [],
            results: [],
          }
          // 将消息对象添加到messages数组中，后续只更新内容，不清空
          messages.value.push(taskExecutionMessage)
          await scrollToBottom()

            if (typeof streamResponse.data === 'string') {
              // 处理字符串响应
              const lines = streamResponse.data.split('\n')
              for (const line of lines) {
                if (!line.trim()) continue

                // 处理 SSE 格式：跳过空行，处理 data: 前缀
                if (line.startsWith('data: ')) {
                  let dataContent = line.substring(6).trim()

                  // 移除 \r \n 字符
                  dataContent = dataContent.replace(/[\r\n]/g, '')

                  // 检查是否是结束标记
                  if (dataContent === '[DONE]') {
                    // 收到结束标记时，只有在不需要用户回答问题时才添加"任务执行完成！"
                    if (!taskExecutionMessage.is_question) {
                      taskExecutionMessage.content += '\n\n任务执行完成！'
                    }
                    messages.value = [...messages.value]
                    await scrollToBottom()
                    break
                  }

                  let data
                  try {
                    // SSE消息格式：后端直接返回JSON对象，无需 data.content 解包
                    data = JSON.parse(dataContent)
                  } catch (parseError) {
                    // 尝试修复损坏的JSON：提取有效的JSON部分
                    try {
                      const startIdx = dataContent.indexOf('{')
                      const endIdx = dataContent.lastIndexOf('}')
                      if (startIdx !== -1 && endIdx !== -1 && endIdx > startIdx) {
                        const fixedJson = dataContent.substring(startIdx, endIdx + 1)
                        data = JSON.parse(fixedJson)
                      } else {
                        throw parseError
                      }
                    } catch (fixError) {
                      console.error('无法修复损坏的JSON，跳过此数据 ', dataContent)
                      continue
                    }
                  }

                  try {
                    // 更新任务执行消息
                    if (data.type === 'tasks') {
                      // tasks 类型有特殊过滤逻辑，保留单独处理
                      taskExecutionMessage.tasks = data.tasks
                      let message = data.message
                      if (message && message.includes('正在分析您的意图')) {
                        message = message.replace(/正在分析您的意图，以下是我的思考过程[\s\S]*?\n/, '')
                      }
                      taskExecutionMessage.reasoning_content = message
                      taskExecutionMessage.updatedAt = new Date().toISOString()
                      messages.value = [...messages.value]
                      await scrollToBottom()
                    } else {
                      // 其他类型使用通用处理函数
                      await handleStreamDataType(taskExecutionMessage, data)
                    }
                  } catch (error) {
                    console.error('Error parsing stream data:', error)
                    console.error('Error data content (first 500 chars):', dataContent.substring(0, 500))
                    console.error('Error data content length:', dataContent.length)
                  }
                } else {
                  // 忽略非数据行
                }
              }
              return
            } else if (typeof streamResponse.data === 'object' && streamResponse.data !== null) {
              // 处理对象响应
              if (streamResponse.data.type === 'error') {
                // 处理错误响应
                taskExecutionMessage.content = `❌ 错误: ${streamResponse.data.message}`
              } else if (streamResponse.data.message) {
                // 处理正常响应：显示消息内容
                taskExecutionMessage.content = streamResponse.data.message
              } else {
                // 没有 message 字段时的备用处理
                taskExecutionMessage.content = '智能体处理完成'
              }
              messages.value = [...messages.value]
              await scrollToBottom()
              return
            } else {
              throw new Error('无法获取可读流: streamResponse.data 不是一个有效的流对象')
            }
          } catch (error) {
            console.error('处理普通响应失败:', error)
            throw new Error('无法获取可读流: streamResponse.data 不是一个有效的流对象')
          }
        }

        const decoder = new TextDecoder('utf-8')

        // 实时显示任务执行过程
        // 创建一个新的消息对象用于单独显示最新的大模型回复
        let taskExecutionMessageId = Date.now() + 1
        let taskExecutionMessage = {
          id: taskExecutionMessageId,
          role: 'assistant',
          content: '',
          reasoning_content: undefined, // 初始化为undefined，避免显示空的思考过程区域
          createdAt: new Date().toISOString(),
          type: 'agent_execution',
          tasks: [],
          results: [],
        }
          // 将消息对象添加到messages数组中，后续只更新内容，不清空
          messages.value.push(taskExecutionMessage)
          await scrollToBottom()

        // 心跳超时检测配置
        let lastReceiveTime = Date.now()
        const HEARTBEAT_TIMEOUT = 35000  // 35秒超时（允许2个15秒心跳+5秒缓冲）
        let timeoutCheckInterval = null

        // 启动超时检测定时器
        timeoutCheckInterval = setInterval(() => {
          if (Date.now() - lastReceiveTime > HEARTBEAT_TIMEOUT) {
            console.error('流式响应超时：超过35秒未收到任何消息')
            clearInterval(timeoutCheckInterval)
            // 强制取消读取
            reader.cancel('超时').catch(() => {})
          }
        }, 5000)  // 每5秒检查一次

        // 读取流式响应
        while (true) {
          let done, value
          try {
            const result = await reader.read()
            done = result.done
            value = result.value
          } catch (readError) {
            // 读取被取消（超时）
            clearInterval(timeoutCheckInterval)
            throw new Error('流式响应超时，连接可能已经断开，请稍后重试')
          }

          if (done) {
            clearInterval(timeoutCheckInterval)
            break
          }

          // 收到数据，更新最后接收时间
          lastReceiveTime = Date.now()

          const chunk = decoder.decode(value, { stream: true })
          const lines = chunk.split('\n')

          for (const line of lines) {
            if (!line.trim()) continue

            // 处理 SSE 格式：跳过空行，处理 data: 前缀
            if (line.startsWith('data: ')) {
              let dataContent = line.substring(6).trim()

              // 移除 \r \n 字符
              dataContent = dataContent.replace(/[\r\n]/g, '')

              // 跳过心跳信号 [DOING]
              if (dataContent === '[DOING]') {
                continue
              }

              // 检查是否是结束标记
              if (dataContent === '[DONE]') {
                // 收到结束标记时，只有在不需要用户回答问题时才添加"任务执行完成！"
                if (!taskExecutionMessage.is_question) {
                  taskExecutionMessage.content += '\n\n任务执行完成！'
                }
                taskExecutionMessage.updatedAt = new Date().toISOString()
                messages.value = [...messages.value]
                await scrollToBottom()
                break
              }

              let data
              try {
                // SSE消息格式：后端直接返回JSON对象，无需 data.content 解包
                data = JSON.parse(dataContent)
              } catch (parseError) {
                // 尝试修复损坏的JSON：提取有效的JSON部分
                try {
                  const startIdx = dataContent.indexOf('{')
                  const endIdx = dataContent.lastIndexOf('}')
                  if (startIdx !== -1 && endIdx !== -1 && endIdx > startIdx) {
                    const fixedJson = dataContent.substring(startIdx, endIdx + 1)
                    data = JSON.parse(fixedJson)
                  } else {
                    throw parseError
                  }
                } catch (fixError) {
                  console.error('无法修复损坏的JSON，跳过此数据', dataContent)
                  continue
                }
              }

              try {
                // 更新任务执行消息
                if (data.type === 'tasks') {
                  // tasks 类型有特殊过滤逻辑，保留单独处理
                  taskExecutionMessage.tasks = data.tasks
                  let message = data.message
                  if (message && message.includes('正在分析您的意图')) {
                    message = message.replace(/正在分析您的意图，以下是我的思考过程[\s\S]*?\n/, '')
                  }
                  taskExecutionMessage.reasoning_content = message
                  taskExecutionMessage.updatedAt = new Date().toISOString()
                  messages.value = [...messages.value]
                  await scrollToBottom()
                } else {
                  // 其他类型使用通用处理函数
                  await handleStreamDataType(taskExecutionMessage, data)
                }
              } catch (error) {
                console.error('Error parsing stream data:', error)
                console.error('Error data content (first 500 chars):', dataContent.substring(0, 500))
                console.error('Error data content length:', dataContent.length)
              }
            } else {
              // 非 SSE 格式的行，忽略
            }
          }
        }
      } catch (error) {
        console.error('Agent stream error:', error)
        // 提取错误消息
        let errorMessage = error.message
        if (error.response && error.response.data) {
          if (typeof error.response.data === 'string') {
            try {
              const errorData = JSON.parse(error.response.data)
              if (errorData.message) {
                errorMessage = errorData.message
              }
            } catch (e) {
              errorMessage = error.response.data
            }
          } else if (error.response.data.message) {
            errorMessage = error.response.data.message
          }
        }
        messages.value.push({
          id: Date.now() + 1,
          role: 'assistant',
          content: `智能体处理失败：${errorMessage}`,
          createdAt: new Date().toISOString()
        })
      }
  } catch (error) {
    // 这个错误处理现在主要处理非流式请求的错误
    // 流式请求的错误已经在上面的try-catch中处理
    console.error('Chat error:', error)
    messages.value.push({
      id: Date.now() + 1,
      role: 'assistant',
      content: `抱歉，发生错误：${error.message}`,
      createdAt: new Date().toISOString()
    })
  } finally {
    isLoading.value = false
    await scrollToBottom()
  }
}

const startNewChat = () => {
  messages.value = []
  currentChat.value = null
  // 清空缓存
  messageCache.clear()
}

const selectChat = (chat) => {
  currentChat.value = chat
}

const handleChatAction = (command, chat) => {
  if (command === 'rename') {
    // 重命名逻辑
  } else if (command === 'delete') {
    const index = chatHistory.value.findIndex(c => c.id === chat.id)
    if (index > -1) {
      chatHistory.value.splice(index, 1)
    }
  }
}

const clearChat = () => {
  messages.value = []
  // 清空缓存
  messageCache.clear()
}

const insertQuery = (query) => {
  userInput.value = query
  showDatabasePanel.value = false
}

// 处理用户选择选项（也使用 agent_chat_stream 流式接口）
// 消息解析逻辑见 doc/agent_chat_stream_api.md
const handleOptionSelect = async (message, option, index) => {
  // 模拟用户的填写，创建一条用户消息显示用户的选择
  const userMessage = {
    id: Date.now(),
    role: 'user',
    content: `我选择了：${option}`,
    createdAt: new Date().toISOString()
  }
  messages.value.push(userMessage)
  await scrollToBottom()

  // 构建请求对象，符合要求的格式
  const request = {
    query: option,
    messages: messages.value.map(m => ({
      role: m.role,
      content: m.content
    })),
    model: selectedModel.value,
    agent_id: currentAgent.value?.agent_id || route.query.agentId,
    user_id: globalStore.user?.id || globalStore.user?.user_id || 'anonymous',
    session_id: sessionId,
    platform: 'web'
  }

  // 清空输入框
  userInput.value = ''
  isLoading.value = true

  try {
    // 调用智能体流式接口
    const streamResponse = await llmAPI.agent_chat_stream(request)

    if (streamResponse.body && typeof streamResponse.body.getReader === 'function') {
      const reader = streamResponse.body.getReader()
      const decoder = new TextDecoder('utf-8')

      // 创建一个新的消息对象用于显示AI的回复，确保回复显示在用户选择消息之后
      let assistantMessageId = Date.now() + 1
      let assistantMessage = {
        id: assistantMessageId,
        role: 'assistant',
        content: '',
        reasoning_content: undefined, // 初始化为undefined，避免显示空的思考过程区域
        createdAt: new Date().toISOString(),
        updatedAt: null, // 追踪最新更新时间
        type: 'agent_execution',
        tasks: [],
        results: [],
      }
      messages.value.push(assistantMessage)
      await scrollToBottom()

      // 心跳超时检测配置
      let lastReceiveTime2 = Date.now()
      const HEARTBEAT_TIMEOUT2 = 35000  // 35秒超时
      let timeoutCheckInterval2 = null

      // 启动超时检测定时器
      timeoutCheckInterval2 = setInterval(() => {
        if (Date.now() - lastReceiveTime2 > HEARTBEAT_TIMEOUT2) {
          console.error('流式响应超时：超过35秒未收到任何消息')
          clearInterval(timeoutCheckInterval2)
          reader.cancel('超时').catch(() => {})
        }
      }, 5000)

      // 读取流式响应
      while (true) {
        let done, value
        try {
          const result = await reader.read()
          done = result.done
          value = result.value
        } catch (readError) {
          clearInterval(timeoutCheckInterval2)
          throw new Error('流式响应超时，连接可能已经断开，请稍后重试')
        }

        if (done) {
          clearInterval(timeoutCheckInterval2)
          break
        }

        lastReceiveTime2 = Date.now()

        const chunk = decoder.decode(value, { stream: true })
        const lines = chunk.split('\n')

        for (const line of lines) {
          if (!line.trim()) continue

          if (line.startsWith('data: ')) {
            let dataContent = line.substring(6).trim()

            // 移除 \r \n 字符
            dataContent = dataContent.replace(/[\r\n]/g, '')

            // 跳过心跳信号 [DOING]
            if (dataContent === '[DOING]') {
              continue
            }

            if (dataContent === '[DONE]') {
              // 收到结束标记时，只有在不需要用户回答问题时才添加"任务执行完成！"
              if (!assistantMessage.is_question) {
                assistantMessage.content += '\n\n任务执行完成！'
              }
              messages.value = [...messages.value]
              await scrollToBottom()
              break
            }

            let data
            try {
              data = JSON.parse(dataContent)
              // SSE消息格式：后端直接返回JSON对象，无需 data.content 解包
            } catch (parseError) {
              // 尝试修复损坏的JSON：提取有效的JSON部分
              try {
                const startIdx = dataContent.indexOf('{')
                const endIdx = dataContent.lastIndexOf('}')
                if (startIdx !== -1 && endIdx !== -1 && endIdx > startIdx) {
                  const fixedJson = dataContent.substring(startIdx, endIdx + 1)
                  data = JSON.parse(fixedJson)
                } else {
                  throw parseError
                }
              } catch (fixError) {
                console.error('无法修复损坏的JSON，跳过此数据', dataContent)
                continue
              }
            }

            try {
              // 使用通用处理函数处理所有类型
              await handleStreamDataType(assistantMessage, data)
            } catch (error) {
              console.error('Error parsing stream data:', error)
              console.error('Error data content (first 500 chars):', dataContent.substring(0, 500))
              console.error('Error data content length:', dataContent.length)
            }
          }
        }
      }
    } else {
      // 处理普通响应
      const response = await streamResponse.json()

      let assistantMessage = {
        id: Date.now() + 1,
        role: 'assistant',
        content: response.summary || '任务执行完成',
        reasoning_content: response.reasoning_content,
        createdAt: new Date().toISOString()
      }

      messages.value.push(assistantMessage)
      await scrollToBottom()
    }
  } catch (error) {
    console.error('Error submitting user input:', error)
    let errorMessage = {
      id: Date.now() + 1,
      role: 'assistant',
      content: `抱歉，发生错误：${error.message}`,
      createdAt: new Date().toISOString()
    }
    messages.value.push(errorMessage)
    await scrollToBottom()
  } finally {
    isLoading.value = false
  }
}

// 文件上传处理
const handleFileClick = (type) => {
  if (type === 'file' && fileInputRef.value) {
    fileInputRef.value.click()
  } else if (type === 'image' && imageInputRef.value) {
    imageInputRef.value.click()
  }
}

const handleFileChange = async (event) => {
  const files = event.target.files
  if (files.length === 0) return

  isUploading.value = true
  try {
    for (const file of files) {
      const result = await uploadAPI.upload_file(file)
      if (result) {
        uploadedFiles.value.push({
          id: Date.now() + Math.random(),
          filename: result.filename,
          originalFilename: result.original_filename,
          url: result.url,
          size: result.size
        })
      }
    }
  } catch (error) {
    console.error('文件上传失败:', error)
    ElMessage.error('文件上传失败，请重试')
  } finally {
    isUploading.value = false
    // 清空文件输入
    if (fileInputRef.value) {
      fileInputRef.value.value = ''
    }
  }
}

const removeFile = (fileId) => {
  const index = uploadedFiles.value.findIndex(file => file.id === fileId)
  if (index > -1) {
    uploadedFiles.value.splice(index, 1)
  }
}

const clearFiles = () => {
  uploadedFiles.value = []
}

// 预览处理
const handlePreview = (file) => {
  if (!file || !file.url) return

  const fileExtension = file.filename.split('.').pop().toLowerCase()
  const imageExtensions = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp']

  if (imageExtensions.includes(fileExtension)) {
    // 图片预览
    previewImage.value = `${API_BASE}${file.url}`
    previewVisible.value = true
  } else {
    // 文件下载或新窗口打开
    const fileUrl = `${apiBaseUrl}${file.url}`
    window.open(fileUrl, '_blank')
  }
}

const handleClosePreview = () => {
  previewVisible.value = false
  previewImage.value = ''
}

// 判断是否为图片文件
const isImageFile = (file) => {
  if (!file || !file.filename) return false
  const fileExtension = file.filename.split('.').pop().toLowerCase()
  const imageExtensions = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp']
  return imageExtensions.includes(fileExtension)
}

// 语音识别功能 - 使用 MediaRecorder 录制并上传到后端
const initSpeechRecognition = () => {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
  
  if (!SpeechRecognition) {
    ElMessage.warning('您的浏览器不支持语音识别功能，请使用 Chrome 或 Edge 浏览器')
    return false
  }
  
  recognition.value = new SpeechRecognition()
  recognition.value.continuous = true
  recognition.value.interimResults = true
  recognition.value.lang = 'zh-CN'
  
  recognition.value.onstart = () => {
    isRecording.value = true
    ElMessage.success('开始录音，请说话...')
  }
  
  recognition.value.onresult = (event) => {
    let finalTranscript = ''
    interimTranscript.value = ''
    
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const transcript = event.results[i][0].transcript
      if (event.results[i].isFinal) {
        finalTranscript += transcript
      } else {
        interimTranscript.value += transcript
      }
    }
    
    if (finalTranscript) {
      userInput.value += finalTranscript
    }
    
    if (interimTranscript.value) {
      userInput.value = userInput.value.replace(/\[正在识别中...\].*$/, '') + `[正在识别中...] ${interimTranscript.value}`
    } else {
      userInput.value = userInput.value.replace(/\[正在识别中...\].*$/, '')
    }
  }
  
  recognition.value.onerror = (event) => {
    console.error('语音识别错误:', event.error)
    isRecording.value = false
    
    if (event.error === 'not-allowed') {
      ElMessage.error('请允许麦克风权限后再试')
    } else if (event.error === 'no-speech') {
      ElMessage.warning('未检测到语音，请重试')
    } else if (event.error === 'network') {
      ElMessage.error('网络连接失败，语音识别服务可能不可用。请检查网络连接或使用 VPN 后重试')
    } else if (event.error === 'audio-capture') {
      ElMessage.error('无法捕获音频，请检查麦克风是否正常工作')
    } else if (event.error === 'aborted') {
      ElMessage.info('录音已取消')
    } else if (event.error === 'language-not-supported') {
      ElMessage.error('当前语言不支持，请使用 Chrome 或 Edge 浏览器')
    } else if (event.error === 'service-not-allowed') {
      ElMessage.error('语音识别服务不可用，请检查浏览器设置')
    } else {
      ElMessage.error(`语音识别错误: ${event.error}`)
    }
  }
  
  recognition.value.onend = () => {
    isRecording.value = false
    userInput.value = userInput.value.replace(/\[正在识别中...\].*$/, '')
  }
  
  return true
}

const startMediaRecording = async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    
    mediaRecorder.value = new MediaRecorder(stream, {
      mimeType: 'audio/webm;codecs=opus'
    })
    
    audioChunks.value = []
    
    mediaRecorder.value.ondataavailable = (event) => {
      if (event.data.size > 0) {
        audioChunks.value.push(event.data)
      }
    }
    
    mediaRecorder.value.onstop = async () => {
      const audioBlob = new Blob(audioChunks.value, { type: 'audio/webm' })
      await uploadAudioForTranscription(audioBlob)
      
      stream.getTracks().forEach(track => track.stop())
    }
    
    mediaRecorder.value.start()
    isRecording.value = true
    ElMessage.success('开始录音，请说话...')
    
  } catch (error) {
    console.error('启动录音失败:', error)
    isRecording.value = false
    
    if (error.name === 'NotAllowedError') {
      ElMessage.error('请允许麦克风权限后再试')
    } else if (error.name === 'NotFoundError') {
      ElMessage.error('未找到麦克风设备')
    } else {
      ElMessage.error(`启动录音失败: ${error.message}`)
    }
  }
}

const stopMediaRecording = () => {
  if (mediaRecorder.value && mediaRecorder.value.state !== 'inactive') {
    mediaRecorder.value.stop()
    isRecording.value = false
    ElMessage.info('录音已停止，正在识别...')
  }
}

const uploadAudioForTranscription = async (audioBlob) => {
  try {
    const formData = new FormData()
    formData.append('audio', audioBlob, 'recording.webm')

    const response = await fetch(`${API_BASE_URL}/llm/transcribe`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${globalStore.token}`
      },
      body: formData
    })
    
    if (!response.ok) {
      throw new Error(`上传失败: ${response.status}`)
    }
    
    const result = await response.json()
    
    if (result.success && result.text) {
      userInput.value += result.text
      ElMessage.success('语音识别完成')
    } else {
      ElMessage.warning('未能识别出文字，请重试')
    }
    
  } catch (error) {
    console.error('语音识别上传失败:', error)
    ElMessage.error(`语音识别失败: ${error.message}`)
  }
}

const toggleVoiceRecording = async () => {
  if (isRecording.value) {
    stopMediaRecording()
  } else {
    await startMediaRecording()
  }
}

// 加载智能体信息
const loadAgentInfo = async () => {
  const agentId = route.query.agentId
  if (!agentId) return

  try {
    const response = await employeeAPI.get_employee(agentId)
    currentAgent.value = response
  } catch (error) {
    console.error('加载智能体信息失败:', error)
    ElMessage.error('加载智能体信息失败')

    // 重新获取智能体列表
    try {
      const employeesResponse = await employeeAPI.get_employees()
      const employees = employeesResponse.agents

      // 检查是否有差旅智能体
      const travelAgent = employees.find(agent => agent.name.includes('差旅'))
      if (travelAgent) {
        // 重定向到差旅智能体的聊天页面
        router.push({
          path: '/chat',
          query: {
            agentId: travelAgent.agent_id,
            agentName: travelAgent.name,
            agentDisplayName: travelAgent.display_name
          }
        })
      }
    } catch (error) {
      console.error('重新获取智能体列表失败:', error)
    }
  }
}

// 加载可用的技能和MCP
const loadAvailableSkillsAndMCPs = async () => {
  try {
    // 加载技能库
    const skillsResponse = await employeeAPI.get_skill_library()
    availableSkills.value = skillsResponse.skills

    // 加载MCP库
    const mcpsResponse = await employeeAPI.get_mcp_library()
    availableMCPs.value = mcpsResponse.mcps
  } catch (error) {
    console.error('加载技能和MCP失败:', error)
    ElMessage.error('加载技能和MCP失败')
  }
}

// 显示编辑智能体对话框
const openEditAgentDialog = () => {
  if (!currentAgent.value) return

  // 填充表单数据
  agentForm.value.name = currentAgent.value.name
  agentForm.value.description = currentAgent.value.description
  agentForm.value.skills = currentAgent.value.skills.map(skill => skill.skill_id)
  agentForm.value.mcps = currentAgent.value.mcps.map(mcp => mcp.mcp_id)

  editAgentDialogVisible.value = true
}

// 保存智能体编辑
const saveAgentEdit = async () => {
  if (!currentAgent.value) return

  try {
    await employeeAPI.update_employee(currentAgent.value.agent_id, {
      name: agentForm.value.name,
      description: agentForm.value.description,
      skills: agentForm.value.skills,
      mcps: agentForm.value.mcps
    })

    ElMessage.success('更新智能体成功')
    await loadAgentInfo()
    editAgentDialogVisible.value = false
  } catch (error) {
    console.error('更新智能体失败:', error)
    ElMessage.error('更新智能体失败')
  }
}



// 监听agentId变化
watch(
  () => route.query.agentId,
  async (newAgentId) => {
    if (newAgentId) {
      await loadAgentInfo()
    }
  }
)

// 初始化
onMounted(async () => {
  await loadAgentsList()
  loadSelectedAgentFromStorage()
  await loadAgentInfo()
  await loadAvailableSkillsAndMCPs()
})

// 加载智能体列表
const loadAgentsList = async () => {
  loadingAgents.value = true
  try {
    const response = await employeeAPI.get_employees()
    // cachedRequest 已经返回了 response.data，所以直接访问 response.agents
    if (response && response.agents) {
      availableAgents.value = response.agents
    }
  } catch (error) {
    console.error('加载智能体列表失败:', error)
    ElMessage.error('加载智能体列表失败')
  } finally {
    loadingAgents.value = false
  }
}

// 从 localStorage 加载选中的智能体
const loadSelectedAgentFromStorage = () => {
  try {
    const savedAgentId = localStorage.getItem('selectedAgentId')
    if (savedAgentId) {
      const agentId = savedAgentId
      // 验证智能体是否在可用列表中
      const exists = availableAgents.value.find(a => a.agent_id === agentId)
      if (exists) {
        currentAgentId.value = agentId
        currentAgent.value = exists
      }
    }
  } catch (error) {
    console.error('从 localStorage 加载智能体失败:', error)
  }
}

// 保存选中的智能体到 localStorage
const saveSelectedAgentToStorage = (agent) => {
  try {
    localStorage.setItem('selectedAgentId', agent.agent_id)
  } catch (error) {
    console.error('保存智能体到 localStorage 失败:', error)
  }
}

// 处理智能体选择变更
const handleAgentChange = (agentId) => {
  const agent = availableAgents.value.find(a => a.agent_id === agentId)
  if (agent) {
    currentAgentId.value = agentId
    currentAgent.value = agent
    saveSelectedAgentToStorage(agent)
    ElMessage.success(`已切换到智能体：${agent.display_name || agent.name}`)
  }
}

// 确认智能体选择
const confirmAgentSelection = () => {
  if (tempSelectedAgent.value) {
    currentAgent.value = tempSelectedAgent.value
    currentAgentId.value = tempSelectedAgent.value.agent_id
    saveSelectedAgentToStorage(tempSelectedAgent.value)
    showAgentSelector.value = false
    ElMessage.success(`已选择智能体：${tempSelectedAgent.value.display_name || tempSelectedAgent.value.name}`)
  }
}
</script>

<style lang="scss" scoped>
.chat-container {
  height: 100%;
  overflow: hidden;
  box-sizing: border-box;
  margin: -20px;
  padding: 0;
}

.chat-layout {
  height: 100%;
  display: flex;
}

.chat-agent-panel {
  width: 320px;
  background: #fff;
  border-left: 1px solid #e4e7ed;
  display: flex;
  flex-direction: column;
  height: 100%;
  flex-shrink: 0;

  .agent-panel-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 16px 20px;
    border-bottom: 1px solid #e4e7ed;

    h3 {
      margin: 0;
      font-size: 16px;
      color: #303133;
    }
  }

  .agent-info {
    padding: 20px;
    flex: 1;
    overflow-y: auto;

    .agent-avatar-container {
      display: flex;
      justify-content: center;
      margin-bottom: 20px;

      .el-avatar {
        border: 3px solid #f0f2f5;
        box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1);
      }
    }

    .agent-name {
      font-size: 18px;
      font-weight: 600;
      color: #303133;
      text-align: center;
      margin-bottom: 4px;
    }

    .agent-display-name {
      font-size: 14px;
      color: #606266;
      text-align: center;
      margin-bottom: 12px;
    }

    .agent-type {
      font-size: 12px;
      color: #909399;
      background-color: #f5f7fa;
      display: inline-block;
      padding: 2px 12px;
      border-radius: 10px;
      margin: 0 auto 20px;
      display: table;
    }

    .agent-section {
      margin-bottom: 20px;

      h4 {
        margin: 0 0 8px;
        font-size: 14px;
        font-weight: 600;
        color: #303133;
      }

      p {
        margin: 0;
        font-size: 13px;
        color: #606266;
        line-height: 1.5;
      }

      .agent-skills,
      .agent-mcps {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 8px;
      }
    }
  }
}

.chat-sidebar {
  width: 280px;
  background: #f8f9fa;
  border-right: 1px solid #e4e7ed;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;

  .sidebar-header {
    padding: 15px;
    border-bottom: 1px solid #e4e7ed;
  }

  .chat-history-section {
    padding: 15px;
    flex: 1;
    overflow-y: auto;

    .section-header {
      margin-bottom: 12px;

      h3 {
        margin: 0;
        font-size: 14px;
        font-weight: 600;
        color: #303133;
      }
    }

    .chat-history {
      .history-item {
        display: flex;
        align-items: center;
        padding: 10px 12px;
        border-radius: 6px;
        cursor: pointer;
        transition: all 0.2s;
        margin-bottom: 4px;

        &:hover {
          background: #e4e7ed;
        }

        &.active {
          background: #ecf5ff;
          color: #409eff;
        }

        .title {
          flex: 1;
          margin-left: 10px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .more-icon {
          opacity: 0;
          transition: opacity 0.2s;
        }

        &:hover .more-icon {
          opacity: 1;
        }
      }
    }
  }
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  height: 100%;
}

.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  padding: 0 20px;
  height: 60px;
  flex-shrink: 0;

  .chat-mode {
    margin: 0 20px;
  }
}

.chat-messages {
  overflow-y: auto;
  padding: 20px;
  background: #f5f7fa;
  flex: 1;
  min-height: 0;

  .welcome-message {
    text-align: center;
    padding: 60px 20px;
    color: #606266;

    h2 {
      margin: 20px 0 20px;
      color: #303133;
      font-size: 24px;
    }

    .agent-intro {
      margin: 0 0 30px;
      font-size: 16px;
      line-height: 1.6;
      color: #409eff;
    }

    .agent-capabilities {
      text-align: left;
      max-width: 600px;
      margin: 0 auto 30px;
      padding: 20px;
      background: rgba(255, 255, 255, 0.8);
      border-radius: 12px;
      box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);

      h3 {
        margin: 0 0 15px;
        font-size: 16px;
        color: #303133;
      }

      li {
        margin: 12px 0;
        padding-left: 20px;
        position: relative;
        line-height: 1.5;

        &::before {
          content: "✓";
          position: absolute;
          left: 0;
          color: #67c23a;
          font-weight: bold;
        }

        span {
          margin-left: 8px;
          color: #606266;
        }
      }
    }

    .agent-mcps {
      text-align: left;
      max-width: 600px;
      margin: 0 auto 40px;
      padding: 20px;
      background: rgba(255, 255, 255, 0.8);
      border-radius: 12px;
      box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);

      h3 {
        margin: 0 0 15px;
        font-size: 16px;
        color: #303133;
      }

      .mcps-list {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }
    }

    .input-prompt {
      max-width: 600px;
      margin: 0 auto;
      padding: 20px;
      background: linear-gradient(135deg, #ecf5ff 0%, #f0f9ff 100%);
      border-radius: 12px;
      border-left: 4px solid #409eff;
      display: flex;
      align-items: flex-start;
      gap: 15px;

      .prompt-icon {
        color: #409eff;
        font-size: 24px;
        flex-shrink: 0;
        margin-top: 2px;
      }

      p {
        margin: 0;
        text-align: left;
        font-size: 15px;
        line-height: 1.6;
        color: #303133;
      }
    }
  }

  .message-list {
    max-width: 900px;
    margin: 0 auto;
  }

  .message {
    display: flex;
    margin-bottom: 20px;

    &.user {
      flex-direction: row-reverse;
    }

    .message-avatar {
      flex-shrink: 0;
    }

    .message-content {
      max-width: 70%;
      margin: 0 12px;

      .message-role {
        font-size: 12px;
        color: #909399;
        margin-bottom: 4px;
      }

      .message-text {
        padding: 12px 16px;
        border-radius: 12px;
        background: #fff;
        line-height: 1.6;

        :deep(pre) {
          background: #1e1e1e;
          padding: 12px;
          border-radius: 8px;
          overflow-x: auto;
        }

        :deep(code) {
          font-family: 'Fira Code', monospace;
        }
      }

      .message-reasoning {
        margin-bottom: 12px;

        .reasoning-header {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 12px;
          color: #909399;
          margin-bottom: 8px;

          .reasoning-icon {
            color: #e6a23c;
          }
        }

        .reasoning-content {
          padding: 12px 16px;
          border-radius: 12px;
          background: #fdf6ec;
          border-left: 4px solid #e6a23c;
          line-height: 1.6;
          color: #606266;

          :deep(pre) {
            background: #f0f0f0;
            padding: 10px;
            border-radius: 6px;
            overflow-x: auto;
          }

          :deep(code) {
            font-family: 'Fira Code', monospace;
            background: #f0f0f0;
            padding: 2px 6px;
            border-radius: 4px;
          }
        }
      }

      .message-doc-link {
        margin-top: 8px;
        padding-top: 8px;
        border-top: 1px solid #ebeef5;

        .doc-link {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          color: #409eff;
          text-decoration: none;
          font-size: 14px;

          &:hover {
            text-decoration: underline;
          }
        }
      }

      .message-time {
        font-size: 12px;
        color: #c0c4cc;
        margin-top: 4px;
      }
    }

    &.user .message-content .message-text {
      background: #409eff;
      color: #fff;
    }

    &.assistant .message-content .message-text {
      background: #fff;
      color: #303133;
    }

    .typing-indicator {
      display: flex;
      gap: 4px;
      padding: 12px 16px;
      background: #fff;
      border-radius: 12px;

      span {
        width: 8px;
        height: 8px;
        background: #909399;
        border-radius: 50%;
        animation: typing 1.4s infinite ease-in-out;

        &:nth-child(2) {
          animation-delay: 0.2s;
        }

        &:nth-child(3) {
          animation-delay: 0.4s;
        }
      }
    }
  }
}

.chat-input {
  background: #fff;
  border-top: 1px solid #e4e7ed;
  padding: 15px 20px;
  flex-shrink: 0;
  box-sizing: border-box;
  min-height: 120px;

  .input-container {
    max-width: 900px;
    margin: 0 auto;

    .uploaded-files {
      margin-bottom: 10px;
      padding: 10px;
      background: #f5f7fa;
      border-radius: 6px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;

      .file-item {
        display: flex;
        align-items: center;
        padding: 6px 10px;
        background: #fff;
        border-radius: 4px;
        border: 1px solid #e4e7ed;
        flex: 0 0 calc(25% - 6px);
        min-width: 200px;
        max-width: 240px;
        cursor: pointer;
        transition: all 0.2s;

        &:hover {
          border-color: #409eff;
          box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
        }

        .file-preview-btn {
          margin-right: 6px;
          flex-shrink: 0;
        }

        .file-name {
          flex: 1;
          margin: 0 8px;
          font-size: 13px;
          color: #303133;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          cursor: pointer;

          &:hover {
            color: #409eff;
            text-decoration: underline;
          }
        }
      }
    }

    .input-actions {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-top: 10px;

      .left-actions {
        display: flex;
        gap: 8px;
      }
    }
    
    .recording-indicator {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      background: #fef0f0;
      border-radius: 6px;
      margin-bottom: 10px;

      .recording-dot {
        width: 12px;
        height: 12px;
        background: #f56c6c;
        border-radius: 50%;
        animation: pulse 1.5s infinite;
      }

      .recording-text {
        color: #f56c6c;
        font-size: 14px;
      }
    }
  }
}

// 智能体选择对话框样式
.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 0;
  gap: 12px;

  .el-icon {
    font-size: 32px;
    color: #409eff;
  }

  span {
    color: #606266;
    font-size: 14px;
  }
}

.empty-agents {
  padding: 20px;
}

.agent-selector-content {
  max-height: 400px;
  overflow-y: auto;
}

.agent-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 100%;
}

.agent-item {
  width: 100%;
  margin: 0;
  padding: 16px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  transition: all 0.3s;

  &:hover {
    border-color: #409eff;
    background-color: #f5f7fa;
  }

  :deep(.el-radio__label) {
    width: 100%;
    padding-left: 12px;
  }

  .agent-info {
    width: 100%;
  }

  .agent-name {
    font-size: 16px;
    font-weight: 600;
    color: #303133;
    margin-bottom: 6px;
  }

  .agent-description {
    font-size: 13px;
    color: #606266;
    line-height: 1.5;
    margin-bottom: 8px;
  }

  .agent-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
}

@keyframes pulse {
  0% {
    transform: scale(1);
    opacity: 1;
  }
  50% {
    transform: scale(1.2);
    opacity: 0.7;
  }
  100% {
    transform: scale(1);
    opacity: 1;
  }
}

@keyframes typing {

  0%,
  60%,
  100% {
    transform: translateY(0);
  }

  30% {
    transform: translateY(-10px);
  }
}
</style>
