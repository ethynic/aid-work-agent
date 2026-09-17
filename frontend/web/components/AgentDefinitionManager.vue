<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <!-- Toast -->
    <div class="fixed top-[20%] left-1/2 -translate-x-1/2 z-50 space-y-2">
      <TransitionGroup name="toast">
        <div v-for="toast in toasts" :key="toast.id"
          :class="['px-4 py-2 rounded-lg shadow-lg text-sm flex items-center gap-2',
            toast.type === 'success' ? 'bg-success-500 text-white' : '',
            toast.type === 'error' ? 'bg-danger-500 text-white' : '',
            toast.type === 'info' ? 'bg-info-500 text-white' : ''
          ]">
          <span>{{ toast.message }}</span>
        </div>
      </TransitionGroup>
    </div>

    <main class="flex-1 flex overflow-hidden">
      <div class="flex-1 flex flex-col min-w-0">
        <AppHeader
          title="自定义数字员工"
          :is-online="true"
          :is-logged-in="effectiveIsLoggedIn"
          :user="displayUser"
        >
        </AppHeader>

        <div class="flex-1 flex overflow-hidden">
          <!-- Left: List -->
          <div class="w-72 flex-shrink-0 border-r border-gray-200 bg-white overflow-y-auto">
            <div class="p-3 space-y-2">
              <button @click="openCreateDialog"
                class="w-full px-3 py-2 text-sm bg-primary-600 text-white rounded-lg hover:bg-primary-700 flex items-center justify-center gap-1">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
                </svg>
                新建智能体
              </button>
              <input v-model="searchQuery" type="text" placeholder="搜索..."
                class="w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-primary-400" />
            </div>
            <div v-if="filteredList.length === 0" class="p-4 text-sm text-gray-400 text-center">
              暂无智能体定义
            </div>
            <div v-for="item in filteredList" :key="item.agent_id"
              @click="selectAgent(item)"
              :class="['p-3 mx-2 mb-1 rounded-lg cursor-pointer transition-colors border',
                selectedAgentId === item.agent_id
                  ? 'bg-primary-50 border-primary-200'
                  : 'border-transparent hover:bg-gray-50']">
              <div class="flex items-center justify-between">
                <span class="text-sm font-medium text-gray-800 truncate">{{ item.name }}</span>
                <span :class="['px-1.5 py-0.5 text-xs rounded',
                  item.status === 'active' ? 'bg-success-100 text-success-700' : 'bg-gray-100 text-gray-500']">
                  {{ item.status === 'active' ? '启用' : '禁用' }}
                </span>
              </div>
              <div class="text-xs text-gray-400 mt-0.5 truncate">{{ item.agent_id }}</div>
              <div v-if="item.production_version" class="text-xs text-gray-400 mt-0.5">
                Prompt V{{ item.production_version }}
              </div>
            </div>
          </div>

          <!-- Right: Detail/Edit -->
          <div class="flex-1 flex flex-col min-w-0 overflow-hidden">
            <div v-if="!selectedAgent && !pendingCreate" class="flex-1 flex items-center justify-center text-gray-400">
              <div class="text-center">
                <svg class="w-12 h-12 mx-auto mb-3 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                </svg>
                <p class="text-sm">选择左侧智能体查看详情，或新建一个</p>
              </div>
            </div>

            <div v-else class="flex-1 flex overflow-hidden">
              <!-- Definition Area (left half) -->
              <div class="w-[45%] flex flex-col border-r border-gray-200 overflow-y-auto p-4">
                <div class="flex items-center justify-between mb-4">
                  <h3 class="text-sm font-semibold text-gray-700">定义配置</h3>
                  <div class="flex gap-2">
                    <button @click="saveDefinition"
                      class="px-3 py-1.5 text-xs bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50"
                      :disabled="saving">
                      {{ saving ? '保存中...' : (pendingCreate ? '保存为自定义数字员工' : '保存定义') }}
                    </button>
                    <button v-if="!pendingCreate" @click="confirmDelete"
                      class="px-3 py-1.5 text-xs text-danger-600 border border-danger-200 rounded-lg hover:bg-danger-50">
                      删除
                    </button>
                  </div>
                </div>

                <!-- 从内置数字员工复制创建的提示条 -->
                <div v-if="pendingCreate" class="mb-3 flex items-center justify-between bg-primary-50 border border-primary-200 rounded-lg px-3 py-2">
                  <span class="text-xs text-primary-700">正在从内置数字员工「{{ pendingCreate.source_name }}」创建自定义副本，确认参数后保存</span>
                  <button @click="cancelPendingCreate"
                    class="text-xs text-gray-500 hover:text-gray-700 whitespace-nowrap ml-2">取消</button>
                </div>

                <div class="space-y-3">
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">Agent ID <span class="text-danger-500">*</span></label>
                    <input v-model="form.agent_id" type="text" :disabled="!pendingCreate"
                      :class="pendingCreate
                        ? 'w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-primary-400'
                        : 'w-full px-3 py-1.5 text-sm bg-gray-100 border border-gray-200 rounded-lg'" />
                  </div>
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">名称 <span class="text-danger-500">*</span></label>
                    <input v-model="form.name" type="text"
                      class="w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-primary-400" />
                  </div>
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">描述</label>
                    <textarea v-model="form.description" rows="2"
                      class="w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-primary-400"></textarea>
                  </div>

                  <!-- Tools Picker -->
                  <div>
                    <div class="flex items-center justify-between mb-1">
                      <label class="text-xs text-gray-500">工具配置</label>
                      <FullscreenPicker title="工具配置" :selected-count="additionalTools.length">
                        <template #content>
                          <div class="space-y-1.5">
                            <label class="flex items-center gap-2 text-sm text-gray-600">
                              <input type="checkbox" v-model="toolsInherit"
                                class="w-4 h-4 rounded border-primary-200 text-primary-600 focus:ring-primary-500" />
                              继承默认工具
                            </label>
                            <div v-if="!toolsInherit" class="space-y-1.5">
                              <label v-for="tool in availableTools" :key="tool.id"
                                class="flex items-center gap-3 px-3 py-2 text-sm bg-white rounded-lg border border-gray-200 hover:border-gray-300 cursor-pointer">
                                <input type="checkbox"
                                  class="w-4 h-4 rounded border-primary-200 text-primary-600 focus:ring-primary-500"
                                  :checked="additionalTools.includes(tool.id)" @change="toggleTool(tool.id)" />
                                <span class="font-medium text-gray-700">{{ tool.name }}</span>
                                <span class="text-gray-400 truncate flex-1" :title="tool.description">{{ tool.description }}</span>
                              </label>
                              <div v-if="availableTools.length === 0" class="text-sm text-gray-400 py-2">加载中...</div>
                            </div>
                          </div>
                        </template>
                      </FullscreenPicker>
                    </div>
                    <div class="bg-gray-50 rounded-lg p-2">
                      <label class="flex items-center gap-2 text-xs text-gray-600 mb-2">
                        <input type="checkbox" v-model="toolsInherit" />
                        继承默认工具
                      </label>
                      <div v-if="!toolsInherit" class="max-h-40 overflow-y-auto space-y-1">
                        <label v-for="tool in availableTools" :key="tool.id"
                          class="flex items-center gap-2 px-2 py-1 text-xs bg-white rounded border border-gray-100 hover:border-gray-200 cursor-pointer">
                          <input type="checkbox" :checked="additionalTools.includes(tool.id)"
                            @change="toggleTool(tool.id)" />
                          <span class="font-medium text-gray-700">{{ tool.name }}</span>
                          <span class="text-gray-400 truncate flex-1" :title="tool.description">{{ tool.description }}</span>
                        </label>
                        <div v-if="availableTools.length === 0" class="text-xs text-gray-400 py-1">加载中...</div>
                      </div>
                    </div>
                  </div>

                  <!-- Skills Picker -->
                  <div>
                    <div class="flex items-center justify-between mb-1">
                      <label class="text-xs text-gray-500">技能配置</label>
                      <FullscreenPicker title="技能配置" :selected-count="form.skills?.allowed?.length || 0">
                        <template #content>
                          <div class="space-y-1.5">
                            <label v-for="skill in availableSkills" :key="skill.id"
                              class="flex items-center gap-3 px-3 py-2 text-sm bg-white rounded-lg border border-gray-200 hover:border-gray-300 cursor-pointer">
                              <input type="checkbox"
                                class="w-4 h-4 rounded border-primary-200 text-primary-600 focus:ring-primary-500"
                                :checked="form.skills?.allowed?.includes(skill.id)" @change="toggleSkill(skill.id)" />
                              <span class="font-medium text-gray-700">{{ skill.name }}</span>
                              <span class="text-gray-400 truncate flex-1" :title="skill.description">{{ skill.description }}</span>
                            </label>
                            <div v-if="availableSkills.length === 0" class="text-sm text-gray-400 py-2">加载中...</div>
                          </div>
                        </template>
                      </FullscreenPicker>
                    </div>
                    <div class="bg-gray-50 rounded-lg p-2 max-h-40 overflow-y-auto space-y-1">
                      <label v-for="skill in availableSkills" :key="skill.id"
                        class="flex items-center gap-2 px-2 py-1 text-xs bg-white rounded border border-gray-100 hover:border-gray-200 cursor-pointer">
                        <input type="checkbox" :checked="form.skills?.allowed?.includes(skill.id)"
                          @change="toggleSkill(skill.id)" />
                        <span class="font-medium text-gray-700">{{ skill.name }}</span>
                        <span class="text-gray-400 truncate flex-1" :title="skill.description">{{ skill.description }}</span>
                      </label>
                      <div v-if="availableSkills.length === 0" class="text-xs text-gray-400 py-1">加载中...</div>
                    </div>
                  </div>

                  <!-- Reply Style Selector -->
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">回复风格</label>
                    <select v-model="form.reply_style"
                      class="w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-primary-400">
                      <option :value="null">默认</option>
                      <option v-for="style in replyStyles" :key="style.id" :value="style.id">
                        {{ style.name }}<template v-if="style.description"> — {{ style.description }}</template>
                      </option>
                    </select>
                  </div>

                  <!-- LLM 配置覆盖 -->
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">LLM 提供者</label>
                    <select v-model="form.llm_provider"
                      class="w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-primary-400">
                      <option :value="null">留空（用全局默认）</option>
                      <option value="deepseek">deepseek</option>
                      <option value="qwen">qwen</option>
                      <option value="zhipu">zhipu</option>
                    </select>
                    <p class="text-[11px] text-gray-400 mt-1">指定后该智能体使用此 provider；留空则沿用 .env 中的 LLM_PROVIDER。</p>
                  </div>
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">各 Provider Model 覆盖</label>
                    <div class="bg-gray-50 rounded-lg p-2 space-y-1.5">
                      <div class="grid grid-cols-[80px_1fr] items-center gap-2">
                        <span class="text-xs text-gray-600">deepseek</span>
                        <input v-model="form.llm_model_codes.deepseek" type="text" placeholder="留空用全局默认"
                          :class="modelCodeErrors.deepseek ? 'border-danger-500 focus:border-danger-500' : ''"
                          @input="sanitizeModelCode('deepseek', $event)"
                          class="w-full px-2 py-1 text-xs bg-white border border-gray-200 rounded focus:outline-none focus:border-primary-400" />
                      </div>
                      <div class="grid grid-cols-[80px_1fr] items-center gap-2">
                        <span class="text-xs text-gray-600">qwen</span>
                        <input v-model="form.llm_model_codes.qwen" type="text" placeholder="留空用全局默认"
                          :class="modelCodeErrors.qwen ? 'border-danger-500 focus:border-danger-500' : ''"
                          @input="sanitizeModelCode('qwen', $event)"
                          class="w-full px-2 py-1 text-xs bg-white border border-gray-200 rounded focus:outline-none focus:border-primary-400" />
                      </div>
                      <div class="grid grid-cols-[80px_1fr] items-center gap-2">
                        <span class="text-xs text-gray-600">zhipu</span>
                        <input v-model="form.llm_model_codes.zhipu" type="text" placeholder="留空用全局默认"
                          :class="modelCodeErrors.zhipu ? 'border-danger-500 focus:border-danger-500' : ''"
                          @input="sanitizeModelCode('zhipu', $event)"
                          class="w-full px-2 py-1 text-xs bg-white border border-gray-200 rounded focus:outline-none focus:border-primary-400" />
                      </div>
                      <p v-if="modelCodeErrorText" class="text-[11px] text-danger-500">{{ modelCodeErrorText }}</p>
                    </div>
                    <p class="text-[11px] text-gray-400 mt-1">仅允许 ASCII 可见字符（字母、数字、常用符号），禁止中文全角、Unicode 特殊符号等。</p>
                  </div>

                  <!-- Recap 轮后任务编辑器 -->
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">Recap 轮后任务</label>
                    <div class="bg-gray-50 rounded-lg p-2 space-y-1.5">
                      <div v-for="(task, i) in recapTasks" :key="i"
                        class="flex items-center gap-1.5 bg-white rounded-lg p-1.5 border border-gray-100">
                        <select v-model="task.name"
                          class="px-1.5 py-1 text-xs border border-gray-200 rounded focus:outline-none focus:border-primary-400 flex-1 min-w-0">
                          <option v-for="opt in recapTaskOptions" :key="opt.name" :value="opt.name" :title="opt.description">
                            {{ opt.name }}
                          </option>
                        </select>
                        <select v-model="task.when"
                          class="px-1.5 py-1 text-xs border border-gray-200 rounded focus:outline-none focus:border-primary-400">
                          <option v-for="w in recapWhenOptions" :key="w" :value="w">{{ w }}</option>
                        </select>
                        <label class="flex items-center gap-1 text-xs text-gray-600 whitespace-nowrap cursor-pointer">
                          <input type="checkbox" v-model="task.enabled" />启用
                        </label>
                        <button @click="recapTasks.splice(i, 1)"
                          class="text-danger-400 hover:text-danger-600 text-sm px-1">&times;</button>
                      </div>
                      <button @click="addRecapTask"
                        class="w-full px-2 py-1 text-xs text-primary-600 bg-primary-50 hover:bg-primary-100 rounded-lg">
                        + 添加任务
                      </button>
                    </div>
                    <p class="text-[11px] text-gray-400 mt-1">每轮问答回复送达后异步执行，失败不影响对话主流程。</p>
                  </div>

                  <!-- Business Pages Editor -->
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">业务页面</label>
                    <div class="bg-gray-50 rounded-lg p-2 space-y-1.5">
                      <div v-for="(page, i) in businessPages" :key="i"
                        class="flex items-center gap-1 bg-white rounded-lg p-1.5 border border-gray-100">
                        <MenuIcon :icon="page.icon" />
                        <span class="text-xs text-default flex-1 truncate">{{ page.title }}</span>
                        <span class="text-xs text-gray-400 truncate">{{ page.route }}</span>
                        <button @click="businessPages.splice(i, 1)"
                          class="text-danger-400 hover:text-danger-600 text-sm px-1">&times;</button>
                      </div>
                      <button @click="showPageSelector = true"
                        class="w-full px-2 py-1 text-xs text-primary-600 bg-primary-50 hover:bg-primary-100 rounded-lg">
                        从库中选择
                      </button>
                    </div>
                  </div>

                  <!-- Chat Toolbar Buttons -->
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">聊天工具栏额外按钮</label>
                    <div class="bg-gray-50 rounded-lg p-2 space-y-1.5">
                      <label v-for="btn in toolbarButtonOptions" :key="btn.id"
                        class="flex items-center gap-2 text-xs text-gray-600 cursor-pointer">
                        <input type="checkbox"
                          class="w-4 h-4 rounded border-primary-200 text-primary-600 focus:ring-primary-500"
                          :checked="chatToolbar.includes(btn.id)" @change="toggleToolbarButton(btn.id)" />
                        <span class="text-gray-700">{{ btn.label }}</span>
                        <span class="text-gray-400 font-mono">{{ btn.id }}</span>
                      </label>
                      <div v-if="toolbarButtonOptions.length === 0" class="text-xs text-gray-400 py-1">暂无可选按钮</div>
                    </div>
                  </div>

                  <!-- Upload Accept -->
                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">上传文件类型限定</label>
                    <input v-model="uploadAccept" type="text" placeholder="如 image/* 或 .pdf,.docx"
                      class="w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-primary-400" />
                    <p class="text-[11px] text-gray-400 mt-1">对齐 HTML input accept 语法，留空用全局默认（.pdf,.doc,.docx,.xls,.xlsx,.txt,.png,.jpg,.jpeg,.gif,.ppt,.pptx）。</p>
                  </div>

                  <div>
                    <label class="text-xs text-gray-500 mb-1 block">状态</label>
                    <select v-model="form.status"
                      class="w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-primary-400">
                      <option value="active">启用</option>
                      <option value="disabled">禁用</option>
                    </select>
                  </div>
                </div>
              </div>

              <!-- Prompt Area (right half) — Template + Sections -->
              <div class="w-[55%] flex flex-col overflow-hidden">
                <div class="p-4 pb-2 flex-shrink-0">
                  <div class="flex items-center justify-between mb-2">
                    <h3 class="text-sm font-semibold text-gray-700">System Prompt</h3>
                    <div class="flex gap-2">
                      <button @click="showVersionHistoryDialog = true"
                        class="px-3 py-1.5 text-xs border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50">
                        版本历史
                      </button>
                      <button @click="savePromptDraft"
                        class="px-3 py-1.5 text-xs border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50"
                        :disabled="promptSaving">
                        {{ promptSaving ? '保存中...' : '保存草稿' }}
                      </button>
                      <button @click="commitPromptVersion"
                        class="px-3 py-1.5 text-xs bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50"
                        :disabled="promptSaving">
                        提交新版本
                      </button>
                    </div>
                  </div>
                  <div v-if="selectedAgent?.production_version" class="text-xs text-gray-400">
                    当前 production: V{{ selectedAgent.production_version }}
                    <span class="text-info-600 ml-1">（模板含 {{ sectionKeys.length }} 个变量）</span>
                  </div>
                </div>

                <div class="flex-1 overflow-y-auto px-4 pb-4">
                  <!-- Template + Sections Editor (full width) -->
                  <div class="flex flex-col min-w-0">
                  <!-- Template textarea -->
                  <div class="text-xs text-gray-400 mb-1">模板（用 <span v-pre>{{变量名}}</span> 作为分段占位符）</div>
                  <MyTextarea
                    v-model="promptContent"
                    :rows="6"
                    monospace
                    show-char-count
                    :min-height="'200px'"
                    placeholder="输入 System Prompt 模板，用 {{变量名}} 作为分段占位符..."
                    @input="onPromptInput"
                  />

                    <!-- Section Variables Editor (dynamic, from template parsing) -->
                    <div v-if="sectionKeys.length > 0" class="mt-3 space-y-2">
                      <div class="flex items-center justify-between">
                        <div class="text-xs font-medium text-gray-500">分段变量值</div>
                        <button @click="saveAllSections"
                          class="px-2 py-1 text-[10px] bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50"
                          :disabled="promptSaving">
                          {{ promptSaving ? '保存中...' : '全部保存' }}
                        </button>
                      </div>
                      <div v-for="key in sectionKeys" :key="key" class="border border-gray-100 rounded-lg p-2">
                        <MyTextarea
                          v-model="sections[key]"
                          :ref="el => bindSectionRef(key, el)"
                          :rows="3"
                          monospace
                          show-char-count
                          enable-preview
                          :busy="optimizingKey === key"
                          :busy-text="`AI 优化 ${key} 中...`"
                          :min-height="'72px'"
                          :label="key"
                          :placeholder="`输入 ${key} 的内容...`"
                          @optimize="optimizeSection(key)"
                        >
                          <template #extra>
                            <button @click="optimizeSection(key)"
                              class="px-2 py-0.5 text-[10px] text-info-600 bg-info-50 hover:bg-info-100 rounded disabled:opacity-50"
                              :disabled="optimizingKey === key || !sections[key]?.trim()">
                              {{ optimizingKey === key ? '优化中...' : 'AI 优化' }}
                            </button>
                          </template>
                        </MyTextarea>
                      </div>
                    </div>
                    <div v-else class="mt-3 text-xs text-gray-400 py-2">
                      <span v-pre>模板中未检测到 {{变量名}} 占位符。添加如 {{role_description}} 的占位符后，下方会出现对应的编辑区。</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>

    <!-- Create Dialog -->
    <div v-if="createDialogVisible" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div class="bg-white rounded-xl shadow-xl w-full max-w-lg p-6">
        <h3 class="text-base font-semibold mb-4">新建智能体</h3>
        <div class="space-y-3">
          <div>
            <label class="text-xs text-gray-500 mb-1 block">Agent ID <span class="text-danger-500">*</span></label>
            <input v-model="createForm.agent_id" type="text" placeholder="如 after-sales-v2"
              class="w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-primary-400" />
          </div>
          <div>
            <label class="text-xs text-gray-500 mb-1 block">名称 <span class="text-danger-500">*</span></label>
            <input v-model="createForm.name" type="text" placeholder="如 售后服务助手"
              class="w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-primary-400" />
          </div>
          <div>
            <label class="text-xs text-gray-500 mb-1 block">描述</label>
            <input v-model="createForm.description" type="text" placeholder="一句话描述智能体用途"
              class="w-full px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-primary-400" />
          </div>
          <div>
            <label class="text-xs text-gray-500 mb-1 block">System Prompt 模板 <span class="text-danger-500">*</span></label>
            <MyTextarea
              v-model="createForm.system_prompt"
              :rows="6"
              monospace
              :min-height="'120px'"
              placeholder="输入初始 System Prompt 模板，用 {{变量名}} 作为分段占位符..."
            />
          </div>
        </div>
        <div class="flex justify-end gap-2 mt-4">
          <button @click="createDialogVisible = false"
            class="px-4 py-2 text-sm border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50">取消</button>
          <button @click="doCreate" :disabled="creating"
            class="px-4 py-2 text-sm bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50">
            {{ creating ? '创建中...' : '创建' }}
          </button>
        </div>
      </div>
    </div>

    <!-- Version History Dialog -->
    <div v-if="showVersionHistoryDialog" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div class="bg-white rounded-xl shadow-xl w-full max-w-lg max-h-[80vh] flex flex-col p-6">
        <div class="flex items-center justify-between mb-4 flex-shrink-0">
          <h3 class="text-base font-semibold">版本历史</h3>
          <button @click="showVersionHistoryDialog = false" class="p-1 text-gray-400 hover:text-gray-600">&times;</button>
        </div>
        <div v-if="versionsLoading" class="text-sm text-gray-400 py-4 text-center">加载中...</div>
        <div v-else-if="versions.length === 0" class="text-sm text-gray-400 py-4 text-center">暂无版本</div>
        <div v-else class="flex-1 overflow-y-auto space-y-1.5">
          <div v-for="v in versions" :key="v.version"
            @click="loadVersionContent(v); showVersionHistoryDialog = false"
            :class="['p-3 rounded-lg cursor-pointer border transition-colors',
              isProduction(v.version) ? 'border-success-200 bg-success-50' : 'border-gray-100 hover:border-gray-200',
              selectedVersionNum === v.version ? 'ring-1 ring-info-300' : '']">
            <div class="flex items-center gap-2">
              <span class="text-sm font-medium" :class="isProduction(v.version) ? 'text-success-700' : 'text-gray-700'">
                V{{ v.version }}
              </span>
              <span v-if="isProduction(v.version)"
                class="px-1.5 py-0.5 text-xs bg-success-100 text-success-700 rounded">production</span>
            </div>
            <div class="text-xs text-gray-400 mt-1">
              {{ formatTime(v.created_at) }}
              <span v-if="v.created_by" class="ml-2">by {{ v.created_by }}</span>
            </div>
            <div v-if="v.commit_message" class="text-xs text-gray-500 mt-1">
              {{ v.commit_message }}
            </div>
          </div>
        </div>
        <div v-if="versions.length >= 2" class="mt-3 pt-3 border-t border-gray-100 flex gap-2">
          <button @click="showVersionHistoryDialog = false; showDiffDialog = true"
            class="flex-1 px-3 py-2 text-sm text-info-600 bg-info-50 hover:bg-info-100 rounded-lg">
            版本对比
          </button>
          <button v-if="selectedVersionNum && !isProduction(selectedVersionNum)"
            @click="rollbackVersion(selectedVersionNum); showVersionHistoryDialog = false"
            class="flex-1 px-3 py-2 text-sm text-warning-600 bg-warning-50 hover:bg-warning-100 rounded-lg">
            回滚到 V{{ selectedVersionNum }}
          </button>
        </div>
      </div>
    </div>

    <!-- Diff Dialog -->
    <div v-if="showDiffDialog" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div class="bg-white rounded-xl shadow-xl w-full max-w-4xl max-h-[80vh] flex flex-col p-6">
        <div class="flex items-center justify-between mb-4 flex-shrink-0">
          <h3 class="text-base font-semibold">版本对比</h3>
          <button @click="showDiffDialog = false" class="p-1 text-gray-400 hover:text-gray-600">&times;</button>
        </div>
        <div class="flex gap-3 mb-4 flex-shrink-0">
          <div class="flex items-center gap-2">
            <label class="text-sm text-gray-500">从</label>
            <select v-model="diffFrom" class="px-2 py-1 text-sm border border-gray-300 rounded-lg">
              <option v-for="v in versions" :key="v.version" :value="v.version">V{{ v.version }}</option>
            </select>
          </div>
          <div class="flex items-center gap-2">
            <label class="text-sm text-gray-500">到</label>
            <select v-model="diffTo" class="px-2 py-1 text-sm border border-gray-300 rounded-lg">
              <option v-for="v in versions" :key="v.version" :value="v.version">V{{ v.version }}</option>
            </select>
          </div>
          <button @click="loadDiff" :disabled="diffLoading"
            class="px-3 py-1 text-sm text-white bg-primary-600 hover:bg-primary-700 rounded-lg disabled:opacity-50">
            {{ diffLoading ? '对比中...' : '对比' }}
          </button>
        </div>
        <div v-if="diffData" class="flex-1 flex gap-4 overflow-hidden min-h-0">
          <div class="flex-1 flex flex-col min-w-0">
            <div class="text-sm font-medium text-gray-500 mb-1">V{{ diffData.from.version }}</div>
            <pre class="flex-1 bg-gray-50 rounded-lg p-3 text-xs text-gray-700 overflow-auto whitespace-pre-wrap font-mono">{{ diffData.from.content }}</pre>
          </div>
          <div class="flex-1 flex flex-col min-w-0">
            <div class="text-sm font-medium text-gray-500 mb-1">V{{ diffData.to.version }}</div>
            <pre class="flex-1 bg-primary-50 rounded-lg p-3 text-xs text-gray-700 overflow-auto whitespace-pre-wrap font-mono">{{ diffData.to.content }}</pre>
          </div>
        </div>
      </div>
    </div>

    <!-- Page Meta Selector -->
    <PageMetaSelector
      v-model="showPageSelector"
      :selected-page-ids="selectedPageIds"
      :agent-description="form.description || ''"
      @select="onPagesSelected"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppHeader from './AppHeader.vue'
import PageMetaSelector from './PageMetaSelector.vue'
import MyTextarea from './ui/MyTextarea.vue'
import MenuIcon from './ui/MenuIcon.vue'
import FullscreenPicker from './ui/FullscreenPicker.vue'
import {
  listDefinitions, getDefinition, createDefinition,
  updateDefinition, deleteDefinition, updateSystemPrompt,
  listVersions, getVersion, diffVersions,
  saveDraft, listLabels, setLabel,
  listToolsMeta, listSkillsMeta, listReplyStylesMeta, listRecapTasksMeta,
  getSections, saveSection, optimizeSection as apiOptimizeSection,
  type AgentDefinition, type PromptVersion, type DiffResult,
  type ToolMeta, type SkillMeta, type ReplyStyleMeta, type RecapTaskMeta,
} from '@/api/agentDefinitions'
import { listToolbarButtonMeta } from '@/components/chat/toolbar-buttons/registry'
import { useTenantAuth } from '@/composables/useTenantAuth'

// ============== Auth (portal mode) ==============
const { admin: portalAdmin, isLoggedIn: tenantIsLoggedIn } = useTenantAuth()
const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)
// AppHeader 展示用户（登录态缺失时回退到"管理员"占位）
const displayUser = computed(() =>
  portalAdmin.value ? { username: portalAdmin.value.username } : { username: '管理员' }
)

// ============== State ==============
const agents = ref<AgentDefinition[]>([])
const selectedAgentId = ref<string | null>(null)
const selectedAgent = ref<AgentDefinition | null>(null)
const searchQuery = ref('')
const toasts = ref<{ id: number; message: string; type: string }[]>([])
let toastId = 0

// Form state
const form = ref<Record<string, any>>({})
const toolsInherit = ref(true)
const additionalTools = ref<string[]>([])
const saving = ref(false)

// Metadata for pickers
const availableTools = ref<ToolMeta[]>([])
const availableSkills = ref<SkillMeta[]>([])
const replyStyles = ref<ReplyStyleMeta[]>([])

// Business pages
const businessPages = ref<{ id: string; title: string; icon: string; route: string }[]>([])
const showPageSelector = ref(false)
const selectedPageIds = computed(() => businessPages.value.map(p => p.id))

// Recap 轮后任务（通用任务编辑器，任务名/触发时机选项来自 /meta/recap-tasks）
const recapTasks = ref<{ name: string; when: string; enabled: boolean }[]>([])
const recapTaskOptions = ref<RecapTaskMeta[]>([])
const recapWhenOptions = ref<string[]>([])

// 聊天工具栏额外按钮 + 上传类型限定（选项来自前端 toolbar-buttons 注册表）
const toolbarButtonOptions = listToolbarButtonMeta()
const chatToolbar = ref<string[]>([])
const uploadAccept = ref('')

// Model 覆盖输入校验：只允许 ASCII 可见字符（\x21-\x7E），过滤全角/Unicode 特殊符号
// 背景：曾发生把 U+2011（非断行连字符）当成普通连字符填入模型名导致调用失败
const ASCII_VISIBLE_RE = /[^\x21-\x7E]/g
const ASCII_VISIBLE_TEST_RE = /[^\x21-\x7E]/
const modelCodeErrors = ref<Record<string, string>>({})

const modelCodeErrorText = computed(() => {
  const parts = Object.entries(modelCodeErrors.value)
    .filter(([, msg]) => msg)
    .map(([k, msg]) => `${k}: ${msg}`)
  return parts.length > 0 ? parts.join('；') : ''
})

function sanitizeModelCode(provider: string, e: Event) {
  const input = e.target as HTMLInputElement
  const raw = input.value
  const cleaned = raw.replace(ASCII_VISIBLE_RE, '')
  if (cleaned !== raw) {
    const removed = [...new Set(raw.replace(/[\x21-\x7E]/g, ''))]
    modelCodeErrors.value[provider] = `已移除非法字符 ${removed.join(' ')}`
  } else {
    delete modelCodeErrors.value[provider]
  }
  // 触发 modelCodeErrors 响应式更新（delete 不触发）
  modelCodeErrors.value = { ...modelCodeErrors.value }
  if (cleaned !== raw) {
    input.value = cleaned
    form.value.llm_model_codes[provider] = cleaned
  }
}

function validateModelCodes(): boolean {
  const rawCodes = form.value.llm_model_codes || {}
  for (const [k, v] of Object.entries(rawCodes)) {
    if (typeof v === 'string' && v && ASCII_VISIBLE_TEST_RE.test(v)) {
      showToast(`${k} 的 Model 含非 ASCII 字符，请检查`, 'error')
      return false
    }
  }
  return true
}

// 从内置数字员工复制创建模式（入口：内置数字员工页「自定义」按钮）
const pendingCreate = ref<{ source_agent_id: string; source_name: string } | null>(null)

function applyBuiltinPrefill(payload: Record<string, any>) {
  pendingCreate.value = {
    source_agent_id: payload.agent_id,
    source_name: payload.name || payload.agent_id,
  }
  populateForm({
    ...payload,
    agent_id: payload.suggested_agent_id || `${payload.agent_id}_custom`,
    status: 'active',
  } as AgentDefinition)
  promptContent.value = payload.system_prompt || ''
  // reply_style 容错：内置风格的 style_id 可能不在 DB 风格列表中，兜底补一个选项避免下拉框显示空白
  const styleId = payload.reply_style
  if (styleId && !replyStyles.value.some(s => s.id === styleId)) {
    replyStyles.value.push({ id: styleId, name: styleId, description: '内置风格' })
  }
}

function cancelPendingCreate() {
  pendingCreate.value = null
  form.value = {}
  selectedAgentId.value = null
  selectedAgent.value = null
}

// Sections state — dynamic from template parsing
const sectionKeys = ref<string[]>([])
const sections = ref<Record<string, string>>({})
// 记录分段变量从后端加载时的初始值，用于检测未保存修改
const sectionsInitial = ref<Record<string, string>>({})
const optimizingKey = ref<string | null>(null)

// 是否存在未保存的分段内容
const hasUnsavedSections = computed(() => {
  for (const key of sectionKeys.value) {
    const cur = sections.value[key] ?? ''
    const init = sectionsInitial.value[key] ?? ''
    if (cur !== init) return true
  }
  return false
})

// Prompt version state
const promptContent = ref('')
const promptSaving = ref(false)
const versions = ref<PromptVersion[]>([])
const versionsLoading = ref(false)
const selectedVersionNum = ref<number | null>(null)
const labels = ref<{ label: string; version: number | null }[]>([])

// Create dialog
const createDialogVisible = ref(false)
const creating = ref(false)
const createForm = ref({ agent_id: '', name: '', description: '', system_prompt: '' })

// Diff dialog
const showDiffDialog = ref(false)
const showVersionHistoryDialog = ref(false)
const diffFrom = ref(1)
const diffTo = ref(2)
const diffData = ref<DiffResult | null>(null)
const diffLoading = ref(false)

// ============== Computed ==============
const filteredList = computed(() => {
  if (!searchQuery.value) return agents.value
  const q = searchQuery.value.toLowerCase()
  return agents.value.filter(a =>
    a.name.toLowerCase().includes(q) ||
    a.agent_id.toLowerCase().includes(q)
  )
})

// ============== Toast ==============
function showToast(message: string, type = 'success') {
  const id = ++toastId
  toasts.value.push({ id, message, type })
  setTimeout(() => { toasts.value = toasts.value.filter(t => t.id !== id) }, 2500)
}

// ============== Metadata Loading ==============
async function loadMetadata() {
  try {
    const [tRes, sRes, rRes, recapRes] = await Promise.all([
      listToolsMeta(),
      listSkillsMeta(),
      listReplyStylesMeta(),
      listRecapTasksMeta(),
    ])
    if (tRes.success) availableTools.value = tRes.data
    if (sRes.success) availableSkills.value = sRes.data
    if (rRes.success) replyStyles.value = rRes.data
    if (recapRes.success) {
      recapTaskOptions.value = recapRes.data.tasks
      recapWhenOptions.value = recapRes.data.when_options
    }
  } catch (e) { console.error('加载元数据失败', e) }
}

// ============== Parse section keys from template ==============
// Phase 4.0 起：DB 分段变量使用 {{var}} 双花括号，与系统模板的 {var} 分离
function parseSectionKeys(template: string): string[] {
  const regex = /\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g
  const keys: string[] = []
  let match: RegExpExecArray | null
  while ((match = regex.exec(template)) !== null) {
    if (!keys.includes(match[1])) {
      keys.push(match[1])
    }
  }
  return keys
}

// Watch prompt content changes to update section keys
watch(promptContent, (val) => {
  sectionKeys.value = parseSectionKeys(val)
  // Ensure sections has entries for all keys
  for (const key of sectionKeys.value) {
    if (!(key in sections.value)) {
      sections.value[key] = ''
    }
  }
})

// ============== Data Loading ==============
async function loadList() {
  try {
    const res = await listDefinitions({ page: 1, page_size: 100 })
    if (res.success) agents.value = res.data.items
  } catch (e) { console.error('加载列表失败', e) }
}

async function selectAgent(item: AgentDefinition) {
  selectedAgentId.value = item.agent_id
  try {
    const res = await getDefinition(item.agent_id)
    if (res.success) {
      selectedAgent.value = res.data
      populateForm(res.data)
      await loadVersionsAndLabels()
      await loadLatestPromptContent()
      await loadSections()
    }
  } catch (e) { console.error('加载详情失败', e) }
}

function populateForm(data: AgentDefinition) {
  form.value = {
    agent_id: data.agent_id,
    name: data.name,
    description: data.description || '',
    skills: { ...data.skills },
    reply_style: data.reply_style || null,
    status: data.status || 'active',
    llm_provider: data.llm_provider || null,
    llm_model_codes: {
      deepseek: data.llm_model_codes?.deepseek || '',
      qwen: data.llm_model_codes?.qwen || '',
      zhipu: data.llm_model_codes?.zhipu || '',
    },
  }
  toolsInherit.value = data.tools?.inherit !== false
  additionalTools.value = data.tools?.additional || []
  businessPages.value = (data.business_pages || []).map((p: any) => ({ ...p }))
  recapTasks.value = (data.recap?.tasks || []).map(t => ({
    name: t.name || '',
    when: t.when || 'every_round',
    enabled: t.enabled !== false,
  }))
  chatToolbar.value = [...(data.chat_toolbar || [])]
  uploadAccept.value = data.upload_accept || ''
}

function addRecapTask() {
  const defaultName = recapTaskOptions.value[0]?.name || ''
  const defaultWhen = recapWhenOptions.value[0] || 'every_round'
  recapTasks.value.push({ name: defaultName, when: defaultWhen, enabled: true })
}

function toggleToolbarButton(id: string) {
  const arr = chatToolbar.value
  const i = arr.indexOf(id)
  if (i >= 0) arr.splice(i, 1)
  else arr.push(id)
}

async function loadSections() {
  if (!selectedAgentId.value) return
  try {
    const res = await getSections(selectedAgentId.value)
    if (res.success) {
      // 重置初始值快照
      const initial: Record<string, string> = {}
      // Populate sections from API data
      for (const s of res.data) {
        const content = s.content || ''
        sections.value[s.section_key] = content
        initial[s.section_key] = content
      }
      sectionsInitial.value = initial
    }
  } catch (e) { console.error('加载分段失败', e) }
}

async function loadVersionsAndLabels() {
  if (!selectedAgentId.value) return
  versionsLoading.value = true
  try {
    const [vRes, lRes] = await Promise.all([
      listVersions(selectedAgentId.value, 1, 50),
      listLabels(selectedAgentId.value),
    ])
    if (vRes.success) versions.value = vRes.data.items
    if (lRes.success) labels.value = lRes.data
    if (versions.value.length >= 2) {
      diffFrom.value = versions.value[1].version
      diffTo.value = versions.value[0].version
    }
  } catch (e) { console.error('加载版本失败', e) }
  finally { versionsLoading.value = false }
}

async function loadLatestPromptContent() {
  if (!selectedAgent.value?.production_version) {
    if (versions.value.length > 0) {
      await loadVersionContent(versions.value[0])
    } else {
      promptContent.value = ''
    }
    return
  }
  try {
    const res = await getVersion(selectedAgentId.value!, selectedAgent.value.production_version)
    if (res.success) promptContent.value = res.data.content
  } catch (e) { console.error('加载 prompt 内容失败', e) }
}

async function loadVersionContent(v: PromptVersion) {
  selectedVersionNum.value = v.version
  promptContent.value = v.content
}

function isProduction(version: number): boolean {
  const label = labels.value.find(l => l.label === 'production')
  return label?.version === version
}

function formatTime(ts: string): string {
  if (!ts) return ''
  const d = new Date(ts)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// ============== Auto-resize Textarea ==============
// MyTextarea 内部已自动撑高；这里只保留对外部调用 resize() 的入口，
// 旧 watch 链路改为遍历保存的 ref map 触发各 MyTextarea 的 resize。
const sectionRefs = ref<Record<string, InstanceType<typeof MyTextarea> | null>>({})

function bindSectionRef(key: string, el: any) {
  if (el) {
    sectionRefs.value[key] = el as InstanceType<typeof MyTextarea>
  } else {
    delete sectionRefs.value[key]
  }
}

function autoResizeAll() {
  nextTick(() => {
    Object.values(sectionRefs.value).forEach((comp) => {
      comp?.resize?.()
    })
  })
}

watch(sections, () => autoResizeAll(), { deep: true })
watch(sectionKeys, () => autoResizeAll())

// ============== Tool/Skill Toggles ==============
function toggleTool(toolId: string) {
  const idx = additionalTools.value.indexOf(toolId)
  if (idx >= 0) {
    additionalTools.value.splice(idx, 1)
  } else {
    additionalTools.value.push(toolId)
  }
}

function toggleSkill(skillId: string) {
  if (!form.value.skills) form.value.skills = { allowed: [] }
  if (!form.value.skills.allowed) form.value.skills.allowed = []
  const arr: string[] = form.value.skills.allowed
  const idx = arr.indexOf(skillId)
  if (idx >= 0) {
    arr.splice(idx, 1)
  } else {
    arr.push(skillId)
    applySkillRequirements(skillId)
  }
}

// 勾选技能时，按技能声明的依赖（requires_tools / requires_recap）自动补全工具与 recap 任务。
// 只做增量补全，取消勾选不回删，避免误删管理员手动配置。
function applySkillRequirements(skillId: string) {
  const skill = availableSkills.value.find(s => s.id === skillId)
  if (!skill) return
  for (const toolId of skill.requires_tools || []) {
    if (!additionalTools.value.includes(toolId)) {
      additionalTools.value.push(toolId)
    }
  }
  for (const req of skill.requires_recap || []) {
    if (!recapTasks.value.some(t => t.name === req.name)) {
      recapTasks.value.push({ name: req.name, when: req.when || 'every_round', enabled: true })
    }
  }
}

// ============== Business Pages ==============
function onPagesSelected(pages: Array<{ id: string; title: string; icon: string; route: string }>) {
  businessPages.value = pages
}

// ============== Definition CRUD ==============
function openCreateDialog() {
  createForm.value = { agent_id: '', name: '', description: '', system_prompt: '' }
  createDialogVisible.value = true
}

async function doCreate() {
  const f = createForm.value
  if (!f.agent_id || !f.name || !f.system_prompt) {
    showToast('请填写必填字段', 'error')
    return
  }
  creating.value = true
  try {
    const res = await createDefinition(f)
    if (res.success) {
      showToast('创建成功')
      createDialogVisible.value = false
      await loadList()
      const created = agents.value.find(a => a.agent_id === f.agent_id)
      if (created) await selectAgent(created)
    } else {
      showToast('创建失败', 'error')
    }
  } catch (e: any) {
    showToast(e.message || '创建失败', 'error')
  } finally { creating.value = false }
}

async function saveDefinition() {
  if (!validateModelCodes()) return
  saving.value = true
  try {
    // 组装 llm_model_codes：过滤空值，若全空则传 null
    const rawCodes = form.value.llm_model_codes || {}
    const model_codes: Record<string, string> = {}
    for (const [k, v] of Object.entries(rawCodes)) {
      if (typeof v === 'string' && v.trim()) model_codes[k] = v.trim()
    }
    const data: Record<string, any> = {
      name: form.value.name,
      description: form.value.description || null,
      tools: { inherit: toolsInherit.value, additional: additionalTools.value },
      skills: form.value.skills,
      reply_style: form.value.reply_style || null,
      business_pages: businessPages.value.length > 0 ? businessPages.value : null,
      // 全部删除时传 {tasks: []} 而非 null：后端 update 过滤 None 值，传 null 无法清空配置
      recap: recapTasks.value.length > 0 ? { tasks: recapTasks.value } : { tasks: [] },
      chat_toolbar: [...chatToolbar.value],
      upload_accept: uploadAccept.value.trim(),
      llm_provider: form.value.llm_provider || null,
      llm_model_codes: Object.keys(model_codes).length > 0 ? model_codes : null,
      status: form.value.status,
    }

    // 从内置数字员工复制创建：走 createDefinition 全量落库（status 为 DB 默认 active，create 接口无此字段）
    if (pendingCreate.value) {
      const agentId = (form.value.agent_id || '').trim()
      if (!agentId || !form.value.name || !promptContent.value.trim()) {
        showToast('请填写 Agent ID、名称和 System Prompt', 'error')
        return
      }
      const { status: _ignoredStatus, ...createData } = data
      const res = await createDefinition({
        agent_id: agentId,
        name: form.value.name,
        description: form.value.description || undefined,
        system_prompt: promptContent.value,
        ...createData,
      })
      if (res.success) {
        showToast('自定义数字员工已创建')
        const autoDone = (res as any).auto_completed as string[] | undefined
        if (autoDone && autoDone.length > 0) {
          showToast(`已按技能依赖自动补全: ${autoDone.join('、')}`, 'info')
        }
        pendingCreate.value = null
        await loadList()
        const created = agents.value.find(a => a.agent_id === agentId)
        if (created) await selectAgent(created)
      } else {
        showToast(res.error || '创建失败', 'error')
      }
      return
    }

    if (!selectedAgentId.value) return
    const res = await updateDefinition(selectedAgentId.value, data)
    if (res.success) {
      showToast('定义已保存')
      const autoDone = (res as any).auto_completed as string[] | undefined
      if (autoDone && autoDone.length > 0) {
        showToast(`已按技能依赖自动补全: ${autoDone.join('、')}`, 'info')
      }
      await loadList()
    } else {
      showToast('保存失败', 'error')
    }
  } catch (e: any) {
    showToast(e.message || (pendingCreate.value ? '创建失败' : '保存失败'), 'error')
  } finally { saving.value = false }
}

function confirmDelete() {
  if (!selectedAgentId.value) return
  if (!confirm(`确定删除智能体 "${form.value.name}"？此操作不可恢复。`)) return
  doDelete()
}

async function doDelete() {
  if (!selectedAgentId.value) return
  try {
    const res = await deleteDefinition(selectedAgentId.value)
    if (res.success) {
      showToast('已删除')
      selectedAgent.value = null
      selectedAgentId.value = null
      await loadList()
    } else {
      showToast('删除失败', 'error')
    }
  } catch (e: any) {
    showToast(e.message || '删除失败', 'error')
  }
}

// ============== Prompt Management ==============
function onPromptInput() {
  // Template content changed — section keys auto-update via watch
}

async function savePromptDraft() {
  if (!selectedAgentId.value) return
  promptSaving.value = true
  try {
    const res = await saveDraft(selectedAgentId.value, {
      content: promptContent.value,
      base_version: selectedAgent.value?.production_version || undefined,
    })
    if (res.success) {
      showToast('草稿已保存')
    }
  } catch (e: any) {
    showToast(e.message || '保存草稿失败', 'error')
  } finally { promptSaving.value = false }
}

async function commitPromptVersion() {
  if (!selectedAgentId.value) return
  // 发布新版本前先保存分段变量，避免未保存的内容被覆盖
  if (hasUnsavedSections.value) {
    const ok = await flushUnsavedSections()
    if (!ok) return
  }
  const msg = prompt('提交新版本，变更说明（可选）：')
  if (msg === null) return
  promptSaving.value = true
  try {
    const res = await updateSystemPrompt(selectedAgentId.value, {
      content: promptContent.value,
      commit_message: msg || undefined,
    })
    if (res.success) {
      showToast('新版本已提交并标记为 production')
      await loadList()
      await selectAgent(agents.value.find(a => a.agent_id === selectedAgentId.value)!)
    } else {
      showToast('提交失败', 'error')
    }
  } catch (e: any) {
    showToast(e.message || '提交失败', 'error')
  } finally { promptSaving.value = false }
}

// ============== Sections Management ==============
async function saveAllSections() {
  if (!selectedAgentId.value) return
  promptSaving.value = true
  try {
    for (const key of sectionKeys.value) {
      const content = sections.value[key] ?? ''
      await saveSection(selectedAgentId.value, key, content)
      // 保存后更新初始快照，避免下次误判为未保存
      sectionsInitial.value[key] = content
    }
    showToast('所有分段已保存')
  } catch (e: any) {
    showToast(e.message || '保存失败', 'error')
  } finally { promptSaving.value = false }
}

async function flushUnsavedSections(): Promise<boolean> {
  if (!selectedAgentId.value) return true
  const dirtyKeys: string[] = []
  for (const key of sectionKeys.value) {
    const cur = sections.value[key] ?? ''
    const init = sectionsInitial.value[key] ?? ''
    if (cur !== init) dirtyKeys.push(key)
  }
  if (dirtyKeys.length === 0) return true
  promptSaving.value = true
  try {
    for (const key of dirtyKeys) {
      const content = sections.value[key] ?? ''
      await saveSection(selectedAgentId.value, key, content)
      sectionsInitial.value[key] = content
    }
    showToast(`已自动保存 ${dirtyKeys.length} 个分段`)
    return true
  } catch (e: any) {
    showToast(e.message || '分段保存失败，已取消发布', 'error')
    return false
  } finally {
    promptSaving.value = false
  }
}

async function optimizeSection(key: string) {
  if (!selectedAgentId.value) return
  const content = sections.value[key]?.trim()
  if (!content) {
    showToast('分段内容为空，无法优化', 'error')
    return
  }
  optimizingKey.value = key
  try {
    const res = await apiOptimizeSection(selectedAgentId.value, key, {
      content,
      agent_name: selectedAgent.value?.name,
      agent_description: selectedAgent.value?.description || undefined,
    })
    if (res.success) {
      sections.value[key] = res.data.content
      showToast('AI 优化完成，请检查后保存', 'info')
    }
  } catch (e: any) {
    showToast(e.message || 'AI 优化失败', 'error')
  } finally { optimizingKey.value = null }
}

// ============== Version History ==============
async function rollbackVersion(version: number) {
  if (!selectedAgentId.value) return
  if (!confirm(`确定要回滚到 V${version} 吗？`)) return
  try {
    const res = await setLabel(selectedAgentId.value, 'production', version)
    if (res.success) {
      showToast(`已回滚到 V${version}`)
      await loadList()
      await selectAgent(agents.value.find(a => a.agent_id === selectedAgentId.value)!)
    }
  } catch (e: any) {
    showToast(e.message || '回滚失败', 'error')
  }
}

async function loadDiff() {
  if (!selectedAgentId.value) return
  diffLoading.value = true
  diffData.value = null
  try {
    const res = await diffVersions(selectedAgentId.value, diffFrom.value, diffTo.value)
    if (res.success) diffData.value = res.data
  } catch (e) { console.error('对比失败', e) }
  finally { diffLoading.value = false }
}

// ============== Init ==============
const route = useRoute()
const router = useRouter()

onMounted(async () => {
  loadList()
  await loadMetadata()
  // 内置数字员工页「自定义」跳转过来：消费 sessionStorage 预填数据
  const fromBuiltin = route.query.from_builtin as string | undefined
  if (fromBuiltin) {
    const key = `builtin_prefill_${fromBuiltin}`
    const raw = sessionStorage.getItem(key)
    sessionStorage.removeItem(key)
    router.replace({ path: '/portal/agent-definitions' })
    if (raw) {
      try {
        applyBuiltinPrefill(JSON.parse(raw))
      } catch (e) {
        console.error('解析内置数字员工预填数据失败', e)
        showToast('预填数据解析失败，请手动创建', 'error')
      }
    }
  }
})
</script>
