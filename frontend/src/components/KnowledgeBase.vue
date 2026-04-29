<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <!-- Main Content -->
    <main class="flex-1 flex overflow-hidden">
      <!-- Session Sidebar - 仅在非PortalLayout子路由时渲染 -->
      <MenuSidebar
        v-if="!isNestedRoute"
        :is-collapsed="isSidebarCollapsed"
        :current-subagent-id="currentSubagentId"
        :available-subagents="availableSubagents"
        @collapse="isSidebarCollapsed = true"
      />

      <!-- Right Content Area -->
      <div class="flex-1 flex flex-col min-w-0">
        <!-- Header Bar -->
        <AppHeader
          title="企业知识库"
          :is-logged-in="effectiveIsLoggedIn"
          :user="effectiveUser"
          @toggle-sidebar="isSidebarCollapsed = !isSidebarCollapsed"
          @logout="handleLogout"
        >
          <template #menu-items="{ closeMenu }">
            <button
              @click="goToChat(); closeMenu()"
              class="w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
              </svg>
              返回对话
            </button>
            <button
              @click="openCustomerInfo"
              class="w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
              </svg>
              我的客户
            </button>
            <button
              @click="showCredentialManager = true; closeMenu()"
              class="w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
              </svg>
              凭据管理
            </button>
            <button
              @click="openScheduledTasks"
              class="w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              我的定时任务
            </button>
          </template>
        </AppHeader>

        <!-- Main Content Area -->
        <div class="flex-1 overflow-hidden p-6">
          <div class="max-w-6xl mx-auto h-full flex flex-col">
            <!-- Toolbar -->
            <div class="flex items-center justify-between mb-4">
              <!-- Search -->
              <div class="relative flex-1 max-w-md">
                <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
                <input
                  v-model="searchQuery"
                  @input="handleSearchInput"
                  type="text"
                  placeholder="搜索文档..."
                  class="w-full pl-10 pr-4 py-2.5 bg-white border border-gray-200 rounded-lg text-gray-800 placeholder-gray-400 focus:outline-none focus:border-primary-500 focus:ring-2 focus:ring-primary-100 transition-all"
                />
              </div>

              <!-- Upload Button -->
              <button
                @click="showUploadModal = true"
                class="ml-4 flex items-center gap-2 px-4 py-2.5 bg-primary-600 hover:bg-primary-500 text-white rounded-lg transition-colors"
              >
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                </svg>
                上传文档
              </button>
            </div>

            <!-- Document List -->
            <div class="flex-1 overflow-hidden bg-white rounded-xl border border-gray-200">
              <!-- Loading -->
              <div v-if="isLoading" class="flex items-center justify-center h-full">
                <svg class="w-8 h-8 animate-spin text-primary-600" fill="none" viewBox="0 0 24 24">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <span class="ml-3 text-gray-500">加载中...</span>
              </div>

              <!-- Searching -->
              <div v-else-if="isSearching" class="flex flex-col items-center justify-center h-full">
                <svg class="w-8 h-8 animate-spin text-primary-600" fill="none" viewBox="0 0 24 24">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <span class="ml-3 text-gray-500">搜索中...</span>
              </div>

              <!-- Search Error -->
              <div v-else-if="searchError && isSearchMode" class="flex flex-col items-center justify-center h-full">
                <svg class="w-16 h-16 text-danger-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
                <p class="text-danger-500 mb-2">搜索失败</p>
                <p class="text-sm text-gray-400">{{ searchError }}</p>
              </div>

              <!-- Search Results Empty -->
              <div v-else-if="isSearchMode && groupedSearchResults.length === 0 && !isLoading" class="flex flex-col items-center justify-center h-full">
                <svg class="w-16 h-16 text-gray-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <p class="text-gray-500 mb-2">未找到匹配的文档</p>
                <p class="text-sm text-gray-400">尝试其他关键词</p>
              </div>

              <!-- Document List Empty -->
              <div v-else-if="filteredDocuments.length === 0 && !isSearchMode && !isLoading" class="flex flex-col items-center justify-center h-full">
                <svg class="w-16 h-16 text-gray-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                <p class="text-gray-500 mb-2">暂无已上传的文档</p>
                <p class="text-sm text-gray-400">点击上方按钮上传文档</p>
              </div>

              <!-- Search Results -->
              <div v-else-if="isSearchMode && groupedSearchResults.length > 0" class="h-full overflow-auto p-4">
                <div class="space-y-4">
                  <div
                    v-for="group in groupedSearchResults"
                    :key="group.chunks[0].doc_id"
                    class="bg-gray-50 rounded-lg p-4"
                  >
                    <div class="flex items-center gap-3 mb-3">
                      <div :class="getFileIconClass('.' + group.file_type)" class="w-10 h-10 rounded-lg flex items-center justify-center">
                        <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                        </svg>
                      </div>
                      <div class="flex-1 min-w-0">
                        <h3
                          @click="openDocument(group.chunks[0].doc_id)"
                          class="text-sm font-medium text-primary-600 hover:text-primary-700 hover:underline cursor-pointer truncate"
                          :title="'点击打开原文: ' + group.title"
                        >{{ group.title }}</h3>
                        <p class="text-xs text-gray-500">{{ group.chunks.length }} 个相关片段</p>
                      </div>
                    </div>
                    <div class="space-y-2">
                      <div
                        v-for="(chunk, _idx) in group.chunks.slice(0, 3)"
                        :key="chunk.chunk_id"
                        class="bg-white rounded p-3 text-sm text-gray-600 border border-gray-100"
                      >
                        <p class="line-clamp-3">{{ chunk.text }}</p>
                        <p class="text-xs text-primary-500 mt-1">相关度: {{ (chunk.score * 100).toFixed(1) }}%</p>
                      </div>
                      <p v-if="group.chunks.length > 3" class="text-xs text-gray-400 text-center">
                        还有 {{ group.chunks.length - 3 }} 个相关片段...
                      </p>
                    </div>
                  </div>
                </div>
              </div>

              <!-- Table -->
              <div v-else class="h-full flex flex-col">
                <div class="flex-1 overflow-auto">
                <table class="w-full">
                  <thead class="sticky top-0 bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-64">文档名称</th>
                      <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-96">摘要</th>
                      <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">类型</th>
                      <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">大小</th>
                      <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">分块数</th>
                      <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">上传时间</th>
                      <th class="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">操作</th>
                    </tr>
                  </thead>
                  <tbody class="divide-y divide-gray-100">
                    <tr
                      v-for="doc in filteredDocuments"
                      :key="doc.id"
                      class="hover:bg-gray-50 transition-colors"
                    >
                      <td class="px-6 py-4">
                        <div class="flex items-center gap-3">
                          <!-- File Icon -->
                          <div :class="getFileIconClass(doc.file_type)" class="w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0">
                            <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                            </svg>
                          </div>
                          <div class="flex-1 min-w-0">
                            <span
                              @click="openDocument(doc.id)"
                              class="text-sm font-medium text-primary-600 hover:text-primary-700 hover:underline cursor-pointer block"
                              :title="'点击打开原文: ' + doc.title"
                            >{{ doc.title }}</span>
                          </div>
                        </div>
                      </td>
                      <td class="px-6 py-4">
                        <p
                          v-if="doc.summary"
                          class="text-sm text-gray-600 line-clamp-2"
                          :title="doc.summary"
                        >{{ doc.summary }}</p>
                        <p v-else class="text-sm text-gray-400">-</p>
                      </td>
                      <td class="px-6 py-4">
                        <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600 uppercase">
                          {{ doc.file_type.replace('.', '') }}
                        </span>
                      </td>
                      <td class="px-6 py-4 text-sm text-gray-600">{{ formatFileSize(doc.file_size) }}</td>
                      <td class="px-6 py-4 text-sm text-gray-600">{{ doc.total_chunks }}</td>
                      <td class="px-6 py-4 text-sm text-gray-600">{{ formatTime(doc.created_at) }}</td>
                      <td class="px-6 py-4 text-right">
                        <button
                          @click="handleDelete(doc)"
                          class="p-2 text-gray-400 hover:text-danger-500 hover:bg-danger-50 rounded-lg transition-colors"
                          title="删除"
                        >
                          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
                </div>

                <!-- Pagination -->
                <div v-if="totalDocuments > pageSize" class="flex-shrink-0 flex items-center justify-between px-6 py-3 border-t border-gray-200 bg-gray-50">
                  <span class="text-sm text-gray-500">
                    共 {{ totalDocuments }} 篇文档，第 {{ currentPage }}/{{ totalPages }} 页
                  </span>
                  <div class="flex items-center gap-1">
                    <button
                      @click="goToPage(1)"
                      :disabled="currentPage === 1"
                      class="px-2 py-1.5 text-sm text-gray-600 hover:bg-gray-200 rounded disabled:text-gray-300 disabled:cursor-not-allowed transition-colors"
                    >
                      首页
                    </button>
                    <button
                      @click="goToPage(currentPage - 1)"
                      :disabled="currentPage === 1"
                      class="px-2 py-1.5 text-sm text-gray-600 hover:bg-gray-200 rounded disabled:text-gray-300 disabled:cursor-not-allowed transition-colors"
                    >
                      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
                      </svg>
                    </button>
                    <template v-for="page in totalPages" :key="page">
                      <button
                        v-if="shouldShowPage(page)"
                        @click="goToPage(page)"
                        :class="[
                          'min-w-[32px] px-2 py-1.5 text-sm rounded transition-colors',
                          page === currentPage
                            ? 'bg-primary-600 text-white'
                            : 'text-gray-600 hover:bg-gray-200'
                        ]"
                      >
                        {{ page }}
                      </button>
                    </template>
                    <button
                      @click="goToPage(currentPage + 1)"
                      :disabled="currentPage === totalPages"
                      class="px-2 py-1.5 text-sm text-gray-600 hover:bg-gray-200 rounded disabled:text-gray-300 disabled:cursor-not-allowed transition-colors"
                    >
                      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                      </svg>
                    </button>
                    <button
                      @click="goToPage(totalPages)"
                      :disabled="currentPage === totalPages"
                      class="px-2 py-1.5 text-sm text-gray-600 hover:bg-gray-200 rounded disabled:text-gray-300 disabled:cursor-not-allowed transition-colors"
                    >
                      末页
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>

    <!-- Upload Modal -->
    <div
      v-if="showUploadModal"
      class="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
      @click.self="showUploadModal = false"
    >
      <div class="bg-white rounded-2xl w-full max-w-lg mx-4 shadow-2xl">
        <div class="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <h2 class="text-sm font-semibold text-gray-800">上传文档</h2>
          <button
            @click="showUploadModal = false"
            class="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div class="p-6">
          <!-- Drop Zone -->
          <div
            @dragover.prevent="isDragging = true"
            @dragleave.prevent="isDragging = false"
            @drop.prevent="handleDrop"
            :class="[
              'border-2 border-dashed rounded-xl p-8 text-center transition-all',
              isDragging ? 'border-primary-500 bg-primary-50' : 'border-gray-300 hover:border-gray-400'
            ]"
          >
            <input
              ref="fileInputRef"
              type="file"
              accept=".docx,.xlsx,.pptx,.pdf"
              @change="handleFileSelect"
              class="hidden"
            />

            <svg v-if="!selectedFile" class="w-12 h-12 mx-auto text-gray-400 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
            </svg>

            <div v-if="!selectedFile" class="space-y-2">
              <p class="text-gray-600 font-medium">拖拽文件到此处，或<span @click="fileInputRef?.click()" class="text-primary-600 hover:text-primary-500 cursor-pointer">点击选择</span></p>
              <p class="text-sm text-gray-400">支持 docx, xlsx, pptx, pdf 格式</p>
            </div>

            <div v-else class="space-y-2">
              <div class="flex items-center justify-center gap-3">
                <div :class="getFileIconClass(selectedFile.type)" class="w-10 h-10 rounded-lg flex items-center justify-center">
                  <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                </div>
                <div class="text-left">
                  <p class="text-gray-800 font-medium">{{ selectedFile.name }}</p>
                  <p class="text-sm text-gray-400">{{ formatFileSize(selectedFile.size) }}</p>
                </div>
              </div>
              <button
                @click.stop="selectedFile = null"
                class="text-sm text-gray-500 hover:text-gray-700"
              >
                移除
              </button>
            </div>
          </div>

          <!-- Error Message -->
          <p v-if="uploadError" class="mt-3 text-sm text-danger-500">{{ uploadError }}</p>
        </div>

        <div class="flex justify-end gap-3 px-6 py-4 border-t border-gray-200 bg-gray-50 rounded-b-2xl">
          <button
            @click="showUploadModal = false"
            class="px-4 py-2 text-gray-600 hover:text-gray-800 hover:bg-gray-200 rounded-lg transition-colors"
          >
            取消
          </button>
          <button
            @click="handleUpload"
            :disabled="!selectedFile || isUploading"
            class="px-4 py-2 bg-primary-600 hover:bg-primary-500 disabled:bg-primary-400 text-white rounded-lg transition-colors flex items-center gap-2"
          >
            <svg v-if="isUploading" class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            {{ isUploading ? '上传中...' : '上传' }}
          </button>
        </div>
      </div>
    </div>

    <!-- Delete Confirmation Modal -->
    <div
      v-if="documentToDelete"
      class="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
      @click.self="documentToDelete = null"
    >
      <div class="bg-white rounded-2xl w-full max-w-md mx-4 shadow-2xl p-6">
        <div class="flex items-center gap-4 mb-4">
          <div class="w-12 h-12 rounded-full bg-danger-100 flex items-center justify-center">
            <svg class="w-6 h-6 text-danger-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          </div>
          <div>
            <h3 class="text-sm font-semibold text-gray-800">删除文档</h3>
            <p class="text-sm text-gray-500">确定要删除"{{ documentToDelete.title }}"吗？此操作不可恢复。</p>
          </div>
        </div>
        <div class="flex justify-end gap-3">
          <button
            @click="documentToDelete = null"
            class="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
          >
            取消
          </button>
          <button
            @click="confirmDelete"
            :disabled="isDeleting"
            class="px-4 py-2 bg-danger-600 hover:bg-danger-500 disabled:bg-danger-400 text-white rounded-lg transition-colors"
          >
            {{ isDeleting ? '删除中...' : '删除' }}
          </button>
        </div>
      </div>
    </div>

    <!-- Credential Manager -->
    <CredentialManager
      v-if="showCredentialManager"
      @close="showCredentialManager = false"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from './AppHeader.vue'
import MenuSidebar from './MenuSidebar.vue'
import CredentialManager from './CredentialManager.vue'
import { listSubagents, type SubagentListItem } from '@/api/subagent'
import { listDocuments, deleteDocument, uploadDocument, searchDocuments, getDocumentDownloadUrl, type DocumentResponse, type SearchResultItem } from '@/api/knowledge'
import { useDemoAuth } from '@/composables/useDemoAuth'
import { useTenantAuth } from '@/composables/useTenantAuth'

const router = useRouter()
const route = useRoute()
const { user: demoUser, isLoggedIn: demoIsLoggedIn, logout: demoLogout } = useDemoAuth()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()

const isTenantMode = computed(() => route.path.startsWith('/t/'))

// 统一的登录状态检查
const effectiveIsLoggedIn = computed(() => {
  return isTenantMode.value ? tenantIsLoggedIn.value : demoIsLoggedIn.value
})

// 统一的用户信息
const effectiveUser = computed(() => {
  if (isTenantMode.value) {
    return tenantAdmin.value ? {
      user_id: tenantAdmin.value.user_id,
      username: tenantAdmin.value.username,
      phone: tenantAdmin.value.phone
    } : null
  }
  return demoUser.value
})
const toast = useToast()

const documents = ref<DocumentResponse[]>([])
const isLoading = ref(false)
const searchQuery = ref('')
const searchResults = ref<SearchResultItem[]>([])
const isSearching = ref(false)
const searchError = ref('')
const showUploadModal = ref(false)
const selectedFile = ref<File | null>(null)
const isDragging = ref(false)
const isUploading = ref(false)
const uploadError = ref('')
const documentToDelete = ref<DocumentResponse | null>(null)
const isDeleting = ref(false)

const fileInputRef = ref<HTMLInputElement | null>(null)

// Pagination state
const currentPage = ref(1)
const pageSize = ref(10)
const totalDocuments = ref(0)

// Layout state
const isSidebarCollapsed = ref(false)

// 判断是否为嵌套路由（作为PortalLayout的子路由）
// 如果路由路径以 /t/ 开头，说明被PortalLayout包裹，不需要自己渲染MenuSidebar
const isNestedRoute = computed(() => route.path.startsWith('/t/'))
const showCredentialManager = ref(false)

// 可用的数字员工列表
const availableSubagents = ref<SubagentListItem[]>([])

// 当前选中的子智能体ID（从路由获取）
const currentSubagentId = computed(() => {
  const segments = route.path.split('/').filter(p => p)
  // 租户模式：segments = ['t', 'tenantId', ...]
  if (segments[0] === 't' && segments.length >= 3) {
    if (segments[2] === 'chat' && segments.length >= 4) {
      // 格式: /t/:tenantId/chat/:subagentId
      return segments[3]
    }
    // 格式: /t/:tenantId/:subagentId/* （业务数据页面）
    return segments[2]
  }
  // 非租户模式：匹配 /chat/:subagentId 或 /:subagentId/*
  if (segments.length >= 1) {
    if (segments[0] === 'chat' && segments.length >= 2) {
      // 格式: /chat/:subagentId
      return segments[1]
    }
    // 格式: /:subagentId/* （业务数据页面）
    return segments[0]
  }
  return undefined
})

// 加载数字员工列表
async function loadAvailableSubagents() {
  try {
    const res = await listSubagents()
    if (res.success && res.data) {
      availableSubagents.value = [
        { agent_id: 'main', name: 'CEO智能体', description: '', capabilities: [], type: 'builtin' },
        ...res.data
      ]
    }
  } catch (e) {
    console.error('加载数字员工列表失败:', e)
  }
}

// 分页总页数
const totalPages = computed(() => Math.ceil(totalDocuments.value / pageSize.value) || 1)

// Filtered documents based on search
const filteredDocuments = computed(() => {
  if (!searchQuery.value.trim()) {
    return documents.value
  }
  // 搜索模式下返回空（显示搜索结果）
  return []
})

// 判断是否处于搜索模式
const isSearchMode = computed(() => searchQuery.value.trim().length > 0)

// 防抖定时器
let searchDebounceTimer: ReturnType<typeof setTimeout> | null = null

// 执行搜索
async function performSearch(query: string) {
  if (!query.trim()) {
    searchResults.value = []
    searchError.value = ''
    return
  }

  isSearching.value = true
  searchError.value = ''

  try {
    const result = await searchDocuments(query)
    if (result.success) {
      searchResults.value = result.results
    } else {
      searchError.value = result.error || '搜索失败'
      searchResults.value = []
    }
  } catch (error: any) {
    console.error('前端日志：搜索文档失败', error)
    searchError.value = error.message || '搜索失败'
    searchResults.value = []
  } finally {
    isSearching.value = false
  }
}

// 监听搜索输入（防抖）
function handleSearchInput() {
  if (searchDebounceTimer) {
    clearTimeout(searchDebounceTimer)
  }
  searchDebounceTimer = setTimeout(() => {
    if (isSearchMode.value) {
      performSearch(searchQuery.value)
    } else {
      searchResults.value = []
    }
  }, 300)
}

// 按文档分组搜索结果，按相关度从高到低排列
const groupedSearchResults = computed(() => {
  const groups: Record<number, { title: string; file_type: string; chunks: SearchResultItem[]; maxScore: number }> = {}
  for (const result of searchResults.value) {
    if (!groups[result.doc_id]) {
      groups[result.doc_id] = {
        title: result.title,
        file_type: result.file_type,
        chunks: [],
        maxScore: 0
      }
    }
    groups[result.doc_id].chunks.push(result)
    // 跟踪该文档的最高分
    if (result.score > groups[result.doc_id].maxScore) {
      groups[result.doc_id].maxScore = result.score
    }
  }
  // 按文档最高分从高到低排序，同文档内 chunks 也按分数降序排列
  return Object.values(groups)
    .map(group => ({
      ...group,
      chunks: group.chunks.sort((a, b) => b.score - a.score)
    }))
    .sort((a, b) => b.maxScore - a.maxScore)
})

// Load documents
async function loadDocuments() {
  isLoading.value = true
  try {
    const result = await listDocuments(pageSize.value, (currentPage.value - 1) * pageSize.value)
    documents.value = result.items
    totalDocuments.value = result.total
  } catch (error: any) {
    console.error('前端日志：加载文档列表失败', error)
  } finally {
    isLoading.value = false
  }
}

// 翻页
function goToPage(page: number) {
  if (page < 1 || page > totalPages.value) return
  currentPage.value = page
  loadDocuments()
}

// 判断页码是否显示（总页数 <= 7 全部显示，否则只显示首尾和当前页附近的）
function shouldShowPage(page: number): boolean {
  if (totalPages.value <= 7) return true
  if (page === 1 || page === totalPages.value) return true
  if (Math.abs(page - currentPage.value) <= 1) return true
  return false
}

// Handle file selection
function handleFileSelect(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files && input.files[0]) {
    selectedFile.value = input.files[0]
    uploadError.value = ''
  }
}

// Handle drag and drop
function handleDrop(event: DragEvent) {
  isDragging.value = false
  if (event.dataTransfer?.files && event.dataTransfer.files[0]) {
    const file = event.dataTransfer.files[0]
    const allowedTypes = ['.docx', '.xlsx', '.pptx', '.pdf']
    const ext = '.' + file.name.split('.').pop()?.toLowerCase()
    if (allowedTypes.includes(ext)) {
      selectedFile.value = file
      uploadError.value = ''
    } else {
      uploadError.value = `不支持的文件格式。支持：${allowedTypes.join(', ')}`
    }
  }
}

// Handle upload
async function handleUpload() {
  if (!selectedFile.value) return

  isUploading.value = true
  uploadError.value = ''

  try {
    await uploadDocument(selectedFile.value)
    showUploadModal.value = false
    // 清除搜索状态，回到文档列表视图
    searchQuery.value = ''
    searchResults.value = []
    searchError.value = ''
    if (searchDebounceTimer) {
      clearTimeout(searchDebounceTimer)
      searchDebounceTimer = null
    }
    const fileName = selectedFile.value.name
    selectedFile.value = null
    // 翻回第一页，刷新列表，最新文档在最上面
    currentPage.value = 1
    await loadDocuments()
    toast.success(`${fileName} 上传成功`)
  } catch (error: any) {
    const errorMsg = error.response?.data?.error || error.message || '上传失败'
    uploadError.value = errorMsg
    toast.error(errorMsg)
  } finally {
    isUploading.value = false
  }
}

// Handle delete
function handleDelete(doc: DocumentResponse) {
  documentToDelete.value = doc
}

async function confirmDelete() {
  if (!documentToDelete.value) return

  isDeleting.value = true
  try {
    await deleteDocument(documentToDelete.value.id)
    documentToDelete.value = null
    // 如果当前页已无数据且不是第一页，则回到上一页
    if (documents.value.length === 1 && currentPage.value > 1) {
      currentPage.value--
    }
    await loadDocuments()
  } catch (error: any) {
    console.error('前端日志：删除文档失败', error)
    toast.error(error.response?.data?.error || '删除失败')
  } finally {
    isDeleting.value = false
  }
}

// Format file size
function formatFileSize(bytes: number | null): string {
  if (bytes === null || bytes === 0) return '-'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

// Format time
function formatTime(isoString: string): string {
  if (!isoString) return '-'
  // 直接解析 ISO 字符串，JavaScript 会正确处理本地时间（不带 Z 的格式）
  const date = new Date(isoString)
  const year = date.getFullYear()
  const month = date.getMonth() + 1
  const day = date.getDate()
  const hours = date.getHours().toString().padStart(2, '0')
  const minutes = date.getMinutes().toString().padStart(2, '0')
  return `${year}-${month}-${day} ${hours}:${minutes}`
}

// Get file icon class based on type
function getFileIconClass(fileType: string): string {
  const ext = fileType.toLowerCase()
  if (ext === '.pdf') return 'bg-danger-500'
  if (ext === '.docx' || ext === '.doc') return 'bg-primary-500'
  if (ext === '.xlsx' || ext === '.xls') return 'bg-success-500'
  if (ext === '.pptx' || ext === '.ppt') return 'bg-warning-500'
  return 'bg-gray-500'
}

// 打开原始文档（新窗口）
function openDocument(docId: number) {
  const url = getDocumentDownloadUrl(docId)
  window.open(url, '_blank')
}

// Navigation functions
function goToChat() {
  const targetPath = isTenantMode.value
    ? route.path.replace(/\/knowledge.*/, '')
    : '/'
  router.push(targetPath)
}

function openCustomerInfo() {
  const userId = effectiveUser.value?.user_id
  if (userId) {
    window.open(`/customer-info?user_id=${userId}`, '_blank')
  } else {
    toast.warning('请先登录')
  }
}

function openScheduledTasks() {
  window.open('/scheduled-tasks', '_blank')
}

async function handleLogout() {
  if (isTenantMode.value) {
    await tenantLogout()
  } else {
    await demoLogout()
  }
  router.push(isTenantMode.value ? route.path.replace(/\/knowledge.*/, '') : '/')
}

onMounted(() => {
  loadAvailableSubagents()
  loadDocuments()
})
</script>
