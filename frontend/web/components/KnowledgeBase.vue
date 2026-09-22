<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <main class="flex-1 flex overflow-hidden">
      <MenuSidebar
        v-if="!isNestedRoute"
        :is-collapsed="isSidebarCollapsed"
        :current-subagent-id="currentSubagentId"
        :available-subagents="availableSubagents"
        @collapse="isSidebarCollapsed = true"
      />

      <div class="flex-1 flex flex-col min-w-0">
        <AppHeader
          title="知识库"
          :is-logged-in="effectiveIsLoggedIn"
          :user="effectiveUser"
          @toggle-sidebar="isSidebarCollapsed = !isSidebarCollapsed"
          @logout="handleLogout"
        />

        <div class="flex-1 overflow-hidden p-6">
          <div class="h-full flex gap-4">
            <!-- 左侧分类导航 -->
            <div
              class="flex-shrink-0 flex flex-col bg-white rounded-xl border border-default relative"
              :style="categoryPanelStyle"
            >
              <div class="px-3 py-3 border-b border-default">
                <span class="text-sm font-medium text-default">文档分类</span>
              </div>
              <div class="flex-1 overflow-auto p-2">
                <!-- 全部 -->
                <button
                  @click="selectCategory(null)"
                  :class="[
                    'w-full text-left px-3 py-2 rounded-lg text-sm transition-colors flex items-center justify-between group',
                    !selectedSourceType ? 'bg-primary-50 text-primary-700 font-medium' : 'text-default hover:bg-surface-hover'
                  ]"
                >
                  <span>全部</span>
                  <span class="text-xs text-muted">{{ totalAllDocuments }}</span>
                </button>

                <!-- 分类树 -->
                <div class="mt-0.5 min-w-max">
                  <CategoryTreeItem
                    v-for="cat in categoryTree"
                    :key="cat.id"
                    :category="cat"
                    :depth="0"
                    :selected-source-type="selectedSourceType"
                    :selected-sub-category="selectedSubCategory"
                    @select="handleCategorySelect"
                    @rename="openRenameCategory"
                    @delete="handleDeleteCategory"
                  />
                </div>
              </div>
              <div class="p-2 border-t border-default">
                <button @click="openAddCategory" class="w-full px-3 py-2 text-sm text-primary-600 hover:bg-primary-50 rounded-lg transition-colors text-center">
                  + 添加分类
                </button>
              </div>

              <!-- 拖拽调整宽度的手柄 -->
              <div
                class="hidden md:block absolute right-0 top-0 bottom-0 w-1.5 cursor-col-resize z-10 group hover:bg-primary-200/60 active:bg-primary-300/60 transition-colors"
                @mousedown="startCategoryResize"
              >
                <div class="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-0.5 h-8 bg-gray-300 rounded-full opacity-0 group-hover:opacity-100 group-hover:bg-primary-400 transition-opacity"></div>
              </div>
            </div>

            <!-- 右侧文档列表 -->
            <div class="flex-1 flex flex-col min-w-0">
              <div class="page-toolbar mb-3">
                <div class="page-toolbar-left">
                  <BaseInput v-model="searchQuery" placeholder="在当前选中分类（含子级）中搜索文档" size="sm" class="w-[400px]" @keyup.enter="handleSearchInput" @input="handleSearchInput" />
                  <BaseSelect
                    v-if="showScopeToggle"
                    v-model="categoryScope"
                    size="sm"
                    class="w-[140px]"
                    title="控制列表是否包含子分类下的文档（搜索始终包含子级）"
                  >
                    <option value="include">含子栏目</option>
                    <option value="direct">不含子栏目</option>
                  </BaseSelect>
                  <BaseButton v-if="isSearchMode" size="sm" intent="secondary" @click="clearSearch">显示全部</BaseButton>
                </div>
                <div class="page-toolbar-right">
                  <BaseButton :disabled="selectedArr.length === 0" intent="secondary" @click="openMoveModal">移动 ({{ selectedArr.length }})</BaseButton>
                  <BaseButton :disabled="selectedArr.length === 0" intent="danger" @click="handleBatchDelete">批量删除 ({{ selectedArr.length }})</BaseButton>
                  <BaseButton @click="openUploadModal">上传文档</BaseButton>
                </div>
              </div>

              <div class="flex-1 overflow-hidden bg-white rounded-xl border border-default">
                <!-- Loading -->
                <div v-if="isLoading" class="flex items-center justify-center h-full">
                  <svg class="w-8 h-8 animate-spin text-primary-600" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                  </svg>
                  <span class="ml-3 text-muted">加载中...</span>
                </div>

                <!-- Searching -->
                <div v-else-if="isSearching" class="flex flex-col items-center justify-center h-full">
                  <svg class="w-8 h-8 animate-spin text-primary-600" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                  </svg>
                  <span class="ml-3 text-muted">搜索中...</span>
                </div>

                <!-- Search Error -->
                <div v-else-if="searchError && isSearchMode" class="flex flex-col items-center justify-center h-full">
                  <svg class="w-16 h-16 text-danger-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                  </svg>
                  <p class="text-danger-500 mb-2">搜索失败</p>
                  <p class="text-sm text-muted">{{ searchError }}</p>
                </div>

                <!-- Search Results Empty -->
                <div v-else-if="isSearchMode && groupedSearchResults.length === 0 && !isLoading" class="flex flex-col items-center justify-center h-full">
                  <svg class="w-16 h-16 text-gray-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <p class="text-muted mb-2">未找到匹配的文档</p>
                  <p class="text-sm text-muted">尝试其他关键词</p>
                </div>

                <!-- Document List Empty（不含子栏目且子栏目有文档） -->
                <div v-else-if="isDirectScopeWithSubDocs && documents.length === 0 && !isSearchMode && !isLoading" class="flex flex-col items-center justify-center h-full">
                  <svg class="w-16 h-16 text-gray-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                  <p class="text-muted mb-2">本栏目下没有直接文档</p>
                  <p class="text-sm text-muted">子栏目中共有 {{ selectedCategoryAggCount }} 篇文档，切换为「含子栏目」可查看</p>
                </div>

                <!-- Document List Empty -->
                <div v-else-if="documents.length === 0 && !isSearchMode && !isLoading" class="flex flex-col items-center justify-center h-full">
                  <svg class="w-16 h-16 text-gray-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                  <p class="text-muted mb-2">暂无已上传的文档</p>
                  <p class="text-sm text-muted">点击上方按钮上传文档</p>
                </div>

                <!-- Search Results -->
                <div v-else-if="isSearchMode && groupedSearchResults.length > 0" class="h-full overflow-auto p-4">
                  <div class="space-y-4">
                    <div v-for="group in groupedSearchResults" :key="group.chunks[0].doc_id" class="bg-canvas rounded-lg p-4">
                      <div class="flex items-center gap-3 mb-3">
                        <div :class="getFileIconClass('.' + group.file_type)" class="w-10 h-10 rounded-lg flex items-center justify-center">
                          <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                          </svg>
                        </div>
                        <div class="flex-1 min-w-0">
                          <h3 @click="openDocument(group.chunks[0].doc_id, group.title)" class="text-sm font-medium text-primary-600 hover:text-primary-700 hover:underline cursor-pointer truncate" :title="'点击下载原文: ' + group.title">{{ group.title }}</h3>
                          <p class="text-xs text-muted">{{ group.chunks.length }} 个相关片段</p>
                        </div>
                      </div>
                      <div class="space-y-2">
                        <div v-for="(chunk, _idx) in group.chunks.slice(0, 3)" :key="chunk.chunk_id" class="bg-white rounded p-3 text-sm text-default border border-default">
                          <p class="line-clamp-3">{{ chunk.text }}</p>
                          <p class="text-xs text-primary-500 mt-1">相关度: {{ (chunk.score * 100).toFixed(1) }}%</p>
                        </div>
                        <p v-if="group.chunks.length > 3" class="text-xs text-muted text-center">还有 {{ group.chunks.length - 3 }} 个相关片段...</p>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- Table -->
                <div v-else class="h-full flex flex-col">
                  <div class="flex-1 overflow-auto table-scroll-wrapper">
                    <BaseTable :columns="columns" :data="documents" row-key="id" table-fixed>
                      <!-- 表头全选框 -->
                      <template #checkbox_header>
                        <input
                          type="checkbox"
                          :checked="isAllSelected(documents)"
                          @change="(e: Event) => toggleAll(documents, (e.target as HTMLInputElement).checked)"
                        />
                      </template>
                      <!-- 行选择框 -->
                      <template #checkbox="{ row }">
                        <input
                          type="checkbox"
                          :checked="isSelected(row)"
                          @change="() => toggleRow(row)"
                        />
                      </template>
                      <template #index="{ index }">{{ seqNumber(index) }}</template>
                      <template #title="{ row }">
                        <div class="flex items-center gap-3">
                          <div :class="getFileIconClass(row.file_type)" class="w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0">
                            <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                            </svg>
                          </div>
                          <span @click="openDocument(row.id, row.title)" class="text-sm font-medium text-primary-600 hover:text-primary-700 hover:underline cursor-pointer block flex-1 min-w-0 truncate" :title="'点击下载原文: ' + row.title">{{ row.title }}</span>
                        </div>
                      </template>
                      <template #category_name="{ row }">
                        <span class="text-sm text-default">{{ getCategoryName(row as DocumentResponse) }}</span>
                      </template>
                      <template #summary="{ row }">
                        <p v-if="row.summary" class="text-sm text-default line-clamp-2" :title="row.summary">{{ row.summary }}</p>
                        <p v-else class="text-sm text-muted">-</p>
                      </template>
                      <template #file_type="{ row }">
                        <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-default uppercase">{{ row.file_type.replace('.', '') }}</span>
                      </template>
                      <template #file_size="{ row }">{{ formatFileSize(row.file_size) }}</template>
                      <template #total_chunks="{ row }">
                        <span @click="openChunkDetail(row as DocumentResponse)" class="text-primary-600 hover:text-primary-700 hover:underline cursor-pointer">{{ row.total_chunks }}</span>
                      </template>
                      <template #created_at="{ row }">{{ formatTime(row.created_at) }}</template>
                      <template #actions="{ row }">
                        <div class="flex justify-center gap-2">
                          <BaseButton intent="ghost" size="sm" class="whitespace-nowrap text-xs" @click="openDocDetail(row as DocumentResponse)">详情</BaseButton>
                          <BaseButton intent="danger-ghost" size="sm" class="whitespace-nowrap text-xs" @click="handleDelete(row)">删除</BaseButton>
                        </div>
                      </template>
                    </BaseTable>
                  </div>

                  <BasePagination
                    :total="totalDocuments"
                    :current-page="currentPage"
                    :page-size="pageSize"
                    @change="onPageChange"
                  />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>

    <!-- Upload Modal -->
    <BaseModal v-model="showUploadModal" title="上传文档" size="md" mode="create">
      <!-- 分类提示：文档归入左侧当前选中的分类，弹框内不再重复选择 -->
      <div class="mb-4 p-3 bg-canvas rounded-lg">
        <p class="text-sm text-default">
          文档将上传到分类：
          <span v-if="uploadSourceType" class="font-medium text-primary-600">
            {{ uploadCategoryPath.join(' / ') }}
          </span>
          <span v-else class="text-muted">不分类（全部）</span>
        </p>
      </div>

      <div @dragover.prevent="isDragging = true" @dragleave.prevent="isDragging = false" @drop.prevent="handleDrop"
        :class="['border-2 border-dashed rounded-xl p-8 text-center transition-all', isDragging ? 'border-primary-500 bg-primary-50' : 'border-default hover:border-hover']">
        <input ref="fileInputRef" type="file" multiple
          accept=".docx,.xlsx,.pptx,.pdf,.txt,.md,.json,.yaml,.yml,.log,.csv,.xml,.ini,.properties,.conf,.config"
          @change="handleFileSelect" class="hidden" />

        <svg v-if="selectedFiles.length === 0" class="w-12 h-12 mx-auto text-muted mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
        </svg>

        <div v-if="selectedFiles.length === 0" class="space-y-2">
          <p class="text-default font-medium">拖拽文件到此处，或<span @click="fileInputRef?.click()" class="text-primary-600 hover:text-primary-500 cursor-pointer">点击选择</span></p>
          <p class="text-sm text-muted">支持多文件上传，单文件不超过 {{ MAX_FILE_SIZE_MB }}MB</p>
          <p class="text-xs text-muted">支持格式：docx, xlsx, pptx, pdf, txt, md, json, yaml, yml, log, csv, xml, ini, properties, conf, config 等</p>
        </div>

        <div v-else class="space-y-3">
          <div class="space-y-2 max-h-48 overflow-y-auto">
            <div v-for="(file, index) in selectedFiles" :key="index" class="flex items-center justify-between gap-3 px-3 py-2 bg-canvas rounded-lg">
              <div class="flex items-center gap-3 min-w-0 flex-1">
                <div :class="getFileIconClassByExt(file.name)" class="w-8 h-8 rounded flex items-center justify-center flex-shrink-0">
                  <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                </div>
                <div class="text-left min-w-0 flex-1">
                  <p class="text-default font-medium text-sm truncate" :title="file.name">{{ file.name }}</p>
                  <p class="text-xs text-muted">{{ formatFileSize(file.size) }}</p>
                </div>
              </div>
              <button @click.stop="removeFile(index)" class="p-1 text-muted hover:text-default hover:bg-surface-hover rounded flex-shrink-0">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          </div>
          <div class="flex items-center justify-between">
            <p class="text-sm text-muted">已选择 {{ selectedFiles.length }} 个文件</p>
            <button @click.stop="clearFiles" class="text-sm text-muted hover:text-default">清空全部</button>
          </div>
        </div>
      </div>

      <div v-if="uploadError" class="mt-3 p-3 bg-danger-50 rounded-lg">
        <p class="text-sm text-danger-500">{{ uploadError }}</p>
      </div>
      <div v-if="uploadErrors.length > 0" class="mt-3 p-3 bg-warning-50 rounded-lg">
        <p class="text-sm text-warning-600 font-medium mb-2">以下文件上传失败：</p>
        <ul class="text-sm text-warning-700 space-y-1">
          <li v-for="(err, index) in uploadErrors" :key="index" class="flex items-start gap-2">
            <span class="text-warning-500">•</span>
            <span>{{ err.filename }}: {{ err.error }}</span>
          </li>
        </ul>
      </div>

      <template #footer>
        <BaseButton intent="secondary" @click="handleCancelUpload">取消</BaseButton>
        <BaseButton :disabled="selectedFiles.length === 0 || isUploading" @click="handleUpload">
          {{ uploadButtonText }}
        </BaseButton>
      </template>
    </BaseModal>

    <!-- Delete Document Confirmation Modal -->
    <BaseModal v-model="showDeleteConfirm" title="删除文档" size="md" mode="view">
      <template v-if="documentToDelete">
        <div class="flex items-center gap-4">
          <div class="w-12 h-12 rounded-full bg-danger-100 flex items-center justify-center">
            <svg class="w-6 h-6 text-danger-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          </div>
          <div>
            <p class="text-sm text-default">确定要删除"{{ documentToDelete.title }}"吗？此操作不可恢复。</p>
          </div>
        </div>
      </template>
      <template #footer>
        <BaseButton intent="secondary" @click="showDeleteConfirm = false">取消</BaseButton>
        <BaseButton intent="danger" :disabled="isDeleting" @click="confirmDelete">{{ isDeleting ? '删除中...' : '删除' }}</BaseButton>
      </template>
    </BaseModal>

    <!-- Move Documents Modal -->
    <BaseModal v-model="showMoveModal" title="移动文档到分类" size="md" mode="create">
      <div class="mb-4 p-3 bg-canvas rounded-lg">
        <p class="text-sm text-default">
          将把 <span class="font-medium text-primary-600">{{ selectedArr.length }}</span> 个文档移动到目标分类
        </p>
      </div>
      <div v-if="moveTargetSourceType" class="mb-4 p-3 bg-primary-50 rounded-lg">
        <p class="text-sm text-default">
          目标分类：<span class="font-medium text-primary-700">{{ moveCategoryPath.join(' / ') }}</span>
        </p>
      </div>
      <div class="max-h-[50vh] overflow-auto border border-default rounded-lg p-2">
        <CategoryTreeItem
          v-for="cat in categoryTree"
          :key="cat.id"
          :category="cat"
          :depth="0"
          :selected-source-type="moveTargetSourceType"
          :selected-sub-category="moveTargetSubCategory"
          :show-actions="false"
          :default-expanded="true"
          @select="handleMoveCategorySelect"
        />
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showMoveModal = false">取消</BaseButton>
        <BaseButton :disabled="!moveTargetSourceType || isMoving" @click="confirmMove">
          {{ isMoving ? '移动中...' : `移动到目标分类 (${selectedArr.length})` }}
        </BaseButton>
      </template>
    </BaseModal>

    <!-- Add Category Modal -->
    <BaseModal v-model="showAddCategoryModal" title="添加分类" size="md" mode="create">
      <div class="space-y-4">
        <div>
          <label class="text-sm text-muted mb-1 block">分类名称 <span class="text-danger-500">*</span></label>
          <BaseInput v-model="newCategoryDisplayName" placeholder="如: 合同文档, 政策文件" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">父分类（可选，留空为顶级分类）</label>
          <BaseSelect v-model="newCategoryParentId">
            <option value="">顶级分类</option>
            <option v-for="cat in categories" :key="cat.id" :value="String(cat.id)">
              {{ cat.display_name || cat.source_type }}
            </option>
          </BaseSelect>
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showAddCategoryModal = false">取消</BaseButton>
        <BaseButton :disabled="isCreatingCategory" @click="handleCreateCategory">{{ isCreatingCategory ? '创建中...' : '创建' }}</BaseButton>
      </template>
    </BaseModal>

    <!-- Rename Category Modal -->
    <BaseModal v-model="showRenameCategoryModal" title="编辑分类" size="md" mode="edit">
      <div class="space-y-4">
        <div>
          <label class="text-sm text-muted mb-1 block">英文代号</label>
          <BaseInput :model-value="renameCategorySourceType" disabled />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">分类名称 <span class="text-danger-500">*</span></label>
          <BaseInput v-model="renameCategoryDisplayName" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">所属父栏目</label>
          <div v-if="renameCategoryParentId != null" class="flex items-center gap-2">
            <div class="flex-1">
              <BaseInput :model-value="renameCategoryPath.join(' / ')" disabled />
            </div>
            <BaseButton intent="secondary" @click="openMoveCategory">移动</BaseButton>
          </div>
          <p v-else class="text-sm text-muted">顶级栏目，不支持移动</p>
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showRenameCategoryModal = false">取消</BaseButton>
        <BaseButton :disabled="isRenamingCategory" @click="handleRenameCategory">{{ isRenamingCategory ? '保存中...' : '保存' }}</BaseButton>
      </template>
    </BaseModal>

    <!-- Move Category Modal -->
    <BaseModal v-model="showMoveCategoryModal" title="移动栏目到目标父栏目" size="md" mode="edit">
      <div class="mb-4 p-3 bg-canvas rounded-lg">
        <p class="text-sm text-default">
          将把「<span class="font-medium text-primary-600">{{ renameCategoryDisplayName }}</span>」移动到目标父栏目下
        </p>
      </div>
      <div v-if="moveCategoryTargetId != null" class="mb-4 p-3 bg-primary-50 rounded-lg">
        <p class="text-sm text-default">
          目标父栏目：<span class="font-medium text-primary-700">{{ moveCategoryTargetPath.join(' / ') }}</span>
        </p>
      </div>
      <div v-if="isCrossTopMove" class="mb-4 p-3 bg-warning-50 rounded-lg">
        <p class="text-sm text-warning-700">
          目标顶级栏目与当前不同，该栏目下 {{ renameCategoryDocCount }} 篇文档将同步变更所属顶级栏目，相关授权配置需检查
        </p>
      </div>
      <div class="max-h-[50vh] overflow-auto border border-default rounded-lg p-2">
        <CategoryTreeItem
          v-for="cat in moveCategoryTree"
          :key="cat.id"
          :category="cat"
          :depth="0"
          :selected-source-type="moveCategoryTargetSourceType"
          :selected-sub-category="moveCategoryTargetSubCategory"
          :show-actions="false"
          :default-expanded="true"
          @select="handleMoveCategoryTargetSelect"
        />
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showMoveCategoryModal = false">取消</BaseButton>
        <BaseButton :disabled="moveCategoryTargetId == null || isMovingCategory" @click="confirmMoveCategory">
          {{ isMovingCategory ? '移动中...' : '移动' }}
        </BaseButton>
      </template>
    </BaseModal>

    <!-- Document Detail Modal（元数据查看） -->
    <BaseModal v-model="showDocDetailModal" title="文档详情" size="lg" mode="view">
      <div class="mb-3 text-sm text-muted truncate" :title="docDetailTitle">{{ docDetailTitle }}</div>
      <div v-if="isLoadingDocDetail" class="flex items-center justify-center py-12">
        <svg class="w-6 h-6 animate-spin text-primary-600" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <span class="ml-2 text-muted">加载中...</span>
      </div>
      <div v-else-if="docDetail" class="overflow-y-auto">        <!-- 基本信息 -->
        <dl class="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
          <div class="flex gap-2 min-w-0">
            <dt class="text-muted flex-shrink-0 w-16">来源</dt>
            <dd class="text-default break-all min-w-0">{{ docDetail.origin || 'manual_upload' }}</dd>
          </div>
          <div class="flex gap-2 min-w-0">
            <dt class="text-muted flex-shrink-0 w-16">分类</dt>
            <dd class="text-default break-all min-w-0">{{ docDetail.source_type }}<template v-if="docDetail.sub_category"> / {{ docDetail.sub_category }}</template></dd>
          </div>
          <div class="flex gap-2 min-w-0">
            <dt class="text-muted flex-shrink-0 w-16">类型</dt>
            <dd class="text-default min-w-0 uppercase">{{ docDetail.file_type.replace('.', '') }}</dd>
          </div>
          <div class="flex gap-2 min-w-0">
            <dt class="text-muted flex-shrink-0 w-16">大小</dt>
            <dd class="text-default min-w-0">{{ formatFileSize(docDetail.file_size) }}</dd>
          </div>
          <div class="flex gap-2 min-w-0">
            <dt class="text-muted flex-shrink-0 w-16">分块数</dt>
            <dd class="text-default min-w-0">{{ docDetail.total_chunks }}</dd>
          </div>
          <div class="flex gap-2 items-center min-w-0">
            <dt class="text-muted flex-shrink-0 w-16">状态</dt>
            <dd class="min-w-0"><BaseBadge :intent="docDetail.status === 'active' ? 'success' : 'warning'">{{ docDetail.status }}</BaseBadge></dd>
          </div>
          <div class="flex gap-2 min-w-0 col-span-2">
            <dt class="text-muted flex-shrink-0 w-16">上传时间</dt>
            <dd class="text-default min-w-0">{{ formatTime(docDetail.created_at) }}</dd>
          </div>
        </dl>

        <!-- 元数据 -->
        <div class="border-t border-default mt-4 pt-3">
          <div class="text-sm font-medium text-default mb-2">元数据</div>
          <p v-if="metaEntries.length === 0 && !rawPayloadText" class="text-sm text-muted">暂无元数据</p>
          <div v-else class="space-y-1.5">
            <div v-for="entry in metaEntries" :key="entry.key" class="flex text-sm gap-2">
              <span class="text-muted flex-shrink-0 w-28 truncate text-right" :title="entry.key">{{ entry.label }}</span>
              <pre v-if="entry.multiline" class="flex-1 min-w-0 bg-canvas rounded p-2 text-xs text-default font-mono whitespace-pre-wrap break-all">{{ entry.text }}</pre>
              <span v-else class="text-default break-all flex-1 min-w-0">{{ entry.text }}</span>
            </div>
          </div>

          <!-- raw_payload 原始记录：可能很大（默认 ≤32KB），单独折叠展示 -->
          <div v-if="rawPayloadText" class="mt-3">
            <div class="flex items-center gap-3 mb-1">
              <button class="flex items-center gap-1 text-xs text-primary-600 hover:text-primary-700 font-medium"
                      @click="rawPayloadExpanded = !rawPayloadExpanded">
                <svg :class="['w-3.5 h-3.5 transition-transform', rawPayloadExpanded ? 'rotate-90' : '']" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                </svg>
                原始记录（raw_payload）
              </button>
              <button class="text-xs text-primary-600 hover:text-primary-700" @click="copyToClipboard(rawPayloadText, '原始记录')">复制</button>
            </div>
            <pre v-if="rawPayloadExpanded"
                 class="bg-canvas rounded p-3 text-xs text-muted font-mono whitespace-pre-wrap break-all max-h-72 overflow-y-auto cursor-pointer hover:bg-primary-50 transition-colors"
                 @click="copyToClipboard(rawPayloadText, '原始记录')" title="点击复制">{{ rawPayloadText }}</pre>
          </div>
        </div>
      </div>
      <div v-else class="text-center text-muted py-12">文档详情加载失败</div>
      <template #footer>
        <BaseButton intent="secondary" @click="showDocDetailModal = false">关闭</BaseButton>
      </template>
    </BaseModal>

    <!-- Chunk Detail Modal -->
    <BaseModal v-model="showChunkModal" title="分块详情" size="xl" mode="view">
      <div class="mb-2 text-sm text-muted">{{ chunkDocTitle }}</div>
      <div v-if="isLoadingChunks" class="flex items-center justify-center py-12">
        <svg class="w-6 h-6 animate-spin text-primary-600" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <span class="ml-2 text-muted">加载中...</span>
      </div>
      <div v-else-if="chunkList.length === 0" class="text-center text-muted py-12">暂无分块数据</div>
      <div v-else class="space-y-3 overflow-y-auto">
        <div v-for="chunk in chunkList" :key="chunk.chunk_id" class="border border-default rounded-lg p-4">
          <div class="flex items-center justify-between mb-2">
            <div class="flex items-center gap-3">
              <BaseBadge intent="info">Chunk {{ chunk.index }}</BaseBadge>
              <span class="text-xs text-muted">ID: {{ chunk.chunk_id }}</span>
              <span class="text-xs text-muted">{{ chunk.tokens }} chars</span>
            </div>
            <BaseBadge v-if="chunk.has_vector" intent="success">有向量</BaseBadge>
            <BaseBadge v-else intent="neutral">无向量</BaseBadge>
          </div>
          <div class="bg-canvas rounded p-3 text-sm text-default whitespace-pre-wrap break-all cursor-pointer hover:bg-primary-50 transition-colors"
               @click="copyToClipboard(chunk.text, '分块内容')" title="点击复制内容">
            {{ chunk.text }}
          </div>
          <div v-if="chunk.has_vector && chunk.vector_text" class="mt-2">
            <div class="flex items-center gap-2 mb-1">
              <span class="text-xs text-muted font-medium">向量</span>
              <button class="text-xs text-primary-600 hover:text-primary-700" @click="copyToClipboard(chunk.vector_text!, '向量数据')">复制完整向量</button>
            </div>
            <div class="bg-canvas rounded p-2 text-xs text-muted font-mono break-all cursor-pointer hover:bg-primary-50 transition-colors"
                 @click="copyToClipboard(chunk.vector_text!, '向量数据')" title="点击复制向量">
              {{ truncateVector(chunk.vector_text) }}
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showChunkModal = false">关闭</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from './AppHeader.vue'
import MenuSidebar from './MenuSidebar.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import CategoryTreeItem from './knowledge/CategoryTreeItem.vue'

interface CategoryTreeNode extends CategoryResponse {
  children: CategoryTreeNode[]
  rootSourceType?: string
}
import { usePageContext } from '@/composables/usePageContext'
import { useTableSelection } from '@/composables/useTableSelection'
import { type SubagentListItem } from '@/api/subagent'
import { getMyAllowedAgents } from '@/api/saasPermissions'
import {
  listDocuments, deleteDocument, uploadDocument, searchDocuments, downloadDocument,
  moveDocuments,
  type DocumentResponse, type SearchResultItem,
  listCategories, createCategory, updateCategory, deleteCategory, moveCategory,
  type CategoryResponse,
  getDocumentChunks, type ChunkResponse,
  getDocumentDetail, type DocumentDetail
} from '@/api/knowledge'
import { formatFileSize } from '@/utils/file'
import { useTenantAuth } from '@/composables/useTenantAuth'

const router = useRouter()
const route = useRoute()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()

const isTenantMode = computed(() => route.path.startsWith('/t/'))

const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)

const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone
  } : null
})
const toast = useToast()

const columns = [
  { key: 'checkbox', label: '', width: '40px' },
  { key: 'index', label: '序号', width: '60px' },
  { key: 'title', label: '文档名称', width: '260px' },
  { key: 'category_name', label: '所属栏目', width: '130px', tooltip: (row: Record<string, any>) => getCategoryPath(row as DocumentResponse) },
  { key: 'summary', label: '摘要', width: '380px' },
  { key: 'file_type', label: '类型', width: '110px' },
  { key: 'file_size', label: '大小', width: '90px' },
  { key: 'total_chunks', label: '分块数', width: '70px' },
  { key: 'created_at', label: '上传时间', width: '160px' },
  { key: 'actions', label: '操作', width: '150px', thAlign: 'center' as const },
]

// ========== 分类状态 ==========
const categories = ref<CategoryResponse[]>([])
const categoryTree = ref<CategoryTreeNode[]>([])
const selectedSourceType = ref<string | null>(null)
const selectedSubCategory = ref<string | null>(null)

// ========== 分类树宽度拖拽 ==========
const CATEGORY_PANEL_MIN_WIDTH = 180
const CATEGORY_PANEL_MAX_WIDTH = 400
// 默认宽度与原 w-52（208px）一致
const categoryPanelWidth = ref(208)

const categoryPanelStyle = computed(() => ({
  width: `${categoryPanelWidth.value}px`,
  minWidth: `${CATEGORY_PANEL_MIN_WIDTH}px`,
}))

function startCategoryResize(e: MouseEvent) {
  e.preventDefault()
  const startX = e.clientX
  const startWidth = categoryPanelWidth.value

  function onMouseMove(ev: MouseEvent) {
    // 分类树在左侧，向右拖 = 变宽，向左拖 = 变窄
    const delta = ev.clientX - startX
    const maxWidth = Math.min(CATEGORY_PANEL_MAX_WIDTH, window.innerWidth * 0.4)
    categoryPanelWidth.value = Math.min(Math.max(startWidth + delta, CATEGORY_PANEL_MIN_WIDTH), maxWidth)
  }

  function onMouseUp() {
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }

  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}

// 添加分类（英文代号由后端自动生成，无需用户填写）
const showAddCategoryModal = ref(false)
const newCategoryDisplayName = ref('')
const newCategoryParentId = ref('')
const isCreatingCategory = ref(false)

// 重命名分类
const showRenameCategoryModal = ref(false)
const renameCategoryId = ref<number | null>(null)
const renameCategorySourceType = ref('')
const renameCategoryDisplayName = ref('')
const renameCategoryParentId = ref<number | null>(null)
const isRenamingCategory = ref(false)

// ========== 文档状态 ==========
const documents = ref<DocumentResponse[]>([])
const isLoading = ref(false)
const searchQuery = ref('')

// 栏目范围：include=含子栏目（默认），direct=不含子栏目（仅直接挂载文档）
const categoryScope = ref<'include' | 'direct'>('include')

// 仅当选中的栏目存在子栏目时才显示范围开关，叶子栏目两种口径结果一致，显示反而让用户迷惑
const selectedCategoryHasChildren = computed(() => {
  const target = selectedSubCategory.value || selectedSourceType.value
  if (!target) return false
  const cat = categories.value.find(c => c.source_type === target)
  if (!cat) return false
  return categories.value.some(c => c.parent_id === cat.id)
})
const showScopeToggle = computed(() => !!selectedSourceType.value && selectedCategoryHasChildren.value)
// 开关隐藏（叶子栏目）时恒按含子栏目口径请求，避免残留的 direct 口径隐藏已删除子栏目的孤儿文档
const effectiveIncludeSub = computed(() => !showScopeToggle.value || categoryScope.value === 'include')

// 程序性改值（搜索自动切回含子栏目）时抑制 watch 触发的清搜索/重载，避免与进行中的搜索互相打断
let suppressScopeWatch = false

watch(categoryScope, () => {
  if (suppressScopeWatch) {
    suppressScopeWatch = false
    return
  }
  currentPage.value = 1
  clearSearch()
  clearSelection()
  loadDocuments()
})

// 所属栏目显示名：子分类优先，未挂子分类时为顶级分类
function getCategoryName(row: DocumentResponse): string {
  const target = row.sub_category || row.source_type
  if (!target) return '-'
  const cat = categories.value.find(c => c.source_type === target)
  return cat ? (cat.display_name || cat.source_type) : target
}

// 所属栏目完整路径（用于悬停提示），沿 parent_id 向上追溯
function getCategoryPath(row: DocumentResponse): string {
  const target = row.sub_category || row.source_type
  if (!target) return ''
  const path: string[] = []
  let current = categories.value.find(c => c.source_type === target)
  while (current) {
    path.unshift(current.display_name || current.source_type)
    current = current.parent_id != null
      ? categories.value.find(c => c.id === current!.parent_id)
      : undefined
  }
  return path.join(' / ')
}

// 「不含子栏目」空列表提示：当前选中栏目的聚合文档数（含子级，来自 list_categories）
const selectedCategoryAggCount = computed(() => {
  const target = selectedSubCategory.value || selectedSourceType.value
  if (!target) return 0
  const cat = categories.value.find(c => c.source_type === target)
  return cat?.document_count ?? 0
})
const isDirectScopeWithSubDocs = computed(() =>
  !effectiveIncludeSub.value && !!selectedSourceType.value && selectedCategoryAggCount.value > 0
)

// 分块详情弹窗
const showChunkModal = ref(false)
const chunkDocTitle = ref('')
const chunkList = ref<ChunkResponse[]>([])
const isLoadingChunks = ref(false)

// 文档详情弹窗（元数据查看）
const showDocDetailModal = ref(false)
const docDetailTitle = ref('')
const docDetail = ref<DocumentDetail | null>(null)
const isLoadingDocDetail = ref(false)
const rawPayloadExpanded = ref(false)
let docDetailRequestId = 0
const searchResults = ref<SearchResultItem[]>([])
const isSearching = ref(false)
const searchError = ref('')
const showUploadModal = ref(false)
const uploadSourceType = ref('')
const uploadSubCategory = ref('')
const selectedFiles = ref<File[]>([])
const isDragging = ref(false)
const isUploading = ref(false)
const uploadError = ref('')
const uploadErrors = ref<{ filename: string; error: string }[]>([])
const documentToDelete = ref<DocumentResponse | null>(null)
const showDeleteConfirm = ref(false)
const isDeleting = ref(false)

// ========== 移动文档 ==========
const showMoveModal = ref(false)
const moveTargetSourceType = ref<string | null>(null)
const moveTargetSubCategory = ref<string | null>(null)
const isMoving = ref(false)

const uploadProgress = ref({
  total: 0,
  current: 0,
  currentFileName: ''
})

const MAX_FILE_SIZE = (import.meta.env.VITE_MAX_KNOWLEDGE_FILE_SIZE || 50) * 1024 * 1024
const MAX_FILE_SIZE_MB = Math.round(MAX_FILE_SIZE / 1024 / 1024)

const fileInputRef = ref<HTMLInputElement | null>(null)

const isSidebarCollapsed = ref(typeof window !== 'undefined' && window.innerWidth < 768)
const isNestedRoute = computed(() => route.path.startsWith('/t/'))
const availableSubagents = ref<SubagentListItem[]>([])

const currentSubagentId = computed(() => {
  const segments = route.path.split('/').filter(p => p)
  if (segments[0] === 't' && segments.length >= 3) {
    if (segments[2] === 'chat' && segments.length >= 4) {
      return segments[3]
    }
    return segments[2]
  }
  if (segments.length >= 1) {
    if (segments[0] === 'chat' && segments.length >= 2) {
      return segments[1]
    }
    return segments[0]
  }
  return undefined
})

async function loadAvailableSubagents() {
  try {
    let res
    if (isTenantMode.value) {
      res = await getMyAllowedAgents()
    } else {
      const { listSubagents } = await import('@/api/subagent')
      res = await listSubagents()
    }
    if (res.success && res.data) {
      availableSubagents.value = res.data as SubagentListItem[]
    }
  } catch (e) {
    console.error('加载数字员工列表失败:', e)
  }
}

const totalDocuments = ref(0)
const totalAllDocuments = ref(0)
const { currentPage, pageSize, seqNumber, handlePageChange } =
  usePageContext(async () => {
    await loadDocuments()
  })

// Unified pagination handler: avoids double-request when @update:current-page and @update:page-size fire simultaneously
function onPageChange(page: number, size: number) {
  pageSize.value = size
  currentPage.value = page
  handlePageChange(page)
}

// 批量选择
const { selectedArr, isAllSelected, toggleAll, toggleRow, clearSelection, isSelected } = useTableSelection<any>({
  getRowId: (row) => row.id
})

const uploadButtonText = computed(() => {
  if (isUploading.value) {
    const progress = uploadProgress.value
    if (progress.total > 0) {
      return `上传中 ${progress.current}/${progress.total} - ${progress.currentFileName}`
    }
    return `上传中 (${selectedFiles.value.length} 个文件)...`
  }
  return `上传 (${selectedFiles.value.length})`
})

const isSearchMode = computed(() => searchQuery.value.trim().length > 0)

let searchDebounceTimer: ReturnType<typeof setTimeout> | null = null

async function performSearch(query: string) {
  if (!query.trim()) {
    searchResults.value = []
    searchError.value = ''
    return
  }
  // 搜索始终为含子级口径：开关停在「不含子栏目」时自动切回，避免控件状态与实际过滤范围不一致
  if (categoryScope.value !== 'include') {
    suppressScopeWatch = true
    categoryScope.value = 'include'
  }
  isSearching.value = true
  searchError.value = ''
  try {
    // 跟随当前选中分类：选中子分类时后端会展开为含其所有子级
    const result = await searchDocuments(
      query,
      10,
      selectedSourceType.value || undefined,
      selectedSubCategory.value || undefined
    )
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

function clearSearch() {
  searchQuery.value = ''
  searchResults.value = []
  searchError.value = ''
  if (searchDebounceTimer) {
    clearTimeout(searchDebounceTimer)
    searchDebounceTimer = null
  }
  clearSelection()
}

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
    if (result.score > groups[result.doc_id].maxScore) {
      groups[result.doc_id].maxScore = result.score
    }
  }
  return Object.values(groups)
    .map(group => ({
      ...group,
      chunks: group.chunks.sort((a, b) => b.score - a.score)
    }))
    .sort((a, b) => b.maxScore - a.maxScore)
})

// ========== 分类操作 ==========

function buildCategoryTree(flat: CategoryResponse[]): CategoryTreeNode[] {
  const map = new Map<number, CategoryTreeNode>()
  const roots: CategoryTreeNode[] = []
  for (const c of flat) {
    map.set(c.id, { ...c, children: [] })
  }
  for (const node of map.values()) {
    if (node.parent_id != null && map.has(node.parent_id)) {
      map.get(node.parent_id)!.children.push(node)
    } else {
      roots.push(node)
    }
  }
  // 为每个节点标注所属根分类的 source_type（子分类点击/高亮时使用）
  const assignRoot = (nodes: CategoryTreeNode[], rootSourceType: string) => {
    for (const node of nodes) {
      node.rootSourceType = rootSourceType
      assignRoot(node.children, rootSourceType)
    }
  }
  for (const root of roots) {
    assignRoot([root], root.source_type)
  }
  return roots
}

function findCategoryNode(nodes: CategoryTreeNode[], id: number): CategoryTreeNode | null {
  for (const node of nodes) {
    if (node.id === id) return node
    const found = findCategoryNode(node.children, id)
    if (found) return found
  }
  return null
}

async function loadCategories() {
  try {
    const result = await listCategories()
    categories.value = result.items || []
    categoryTree.value = buildCategoryTree(categories.value)
  } catch (e) {
    console.error('加载分类列表失败:', e)
  }
}

function selectCategory(sourceType: string | null, subCategory: string | null = null) {
  selectedSourceType.value = sourceType
  selectedSubCategory.value = subCategory
  currentPage.value = 1
  clearSearch()
  clearSelection()
  loadDocuments()
}

function handleCategorySelect(cat: CategoryTreeNode) {
  if (cat.parent_id === null) {
    selectCategory(cat.source_type, null)
  } else {
    selectCategory(cat.rootSourceType || cat.source_type, cat.source_type)
  }
}

// 上传弹框展示当前选中分类的完整路径（一级/二级/三级），从选中分类向上追溯父级
const uploadCategoryPath = computed(() => {
  const target = uploadSubCategory.value || uploadSourceType.value
  if (!target) return []
  const path: string[] = []
  let current = categories.value.find(c => c.source_type === target)
  while (current) {
    path.unshift(current.display_name || current.source_type)
    current = current.parent_id != null
      ? categories.value.find(c => c.id === current!.parent_id)
      : undefined
  }
  return path
})

// 当前选中分类的 id（添加分类时用作默认父分类）：子分类优先，其次顶级分类，未选中则顶级
function getSelectedCategoryId(): string {
  const targetSourceType = selectedSubCategory.value || selectedSourceType.value
  if (!targetSourceType) return ''
  const cat = categories.value.find(c => c.source_type === targetSourceType)
  return cat ? String(cat.id) : ''
}

function openUploadModal() {
  uploadSourceType.value = selectedSourceType.value || ''
  uploadSubCategory.value = selectedSubCategory.value || ''
  showUploadModal.value = true
}

function openAddCategory() {
  newCategoryDisplayName.value = ''
  newCategoryParentId.value = getSelectedCategoryId()
  showAddCategoryModal.value = true
}

async function handleCreateCategory() {
  const name = newCategoryDisplayName.value.trim()
  if (!name) {
    toast.error('分类名称不能为空')
    return
  }
  isCreatingCategory.value = true
  try {
    const parentId = newCategoryParentId.value ? Number(newCategoryParentId.value) : null
    await createCategory(name, parentId)
    showAddCategoryModal.value = false
    await loadCategories()
    toast.success('分类创建成功')
  } catch (e: any) {
    toast.error(e.message || '创建分类失败')
  } finally {
    isCreatingCategory.value = false
  }
}

function openRenameCategory(cat: CategoryResponse) {
  renameCategoryId.value = cat.id
  renameCategorySourceType.value = cat.source_type
  renameCategoryDisplayName.value = cat.display_name || cat.source_type
  renameCategoryParentId.value = cat.parent_id
  showRenameCategoryModal.value = true
}

async function handleRenameCategory() {
  if (!renameCategoryId.value) return
  const name = renameCategoryDisplayName.value.trim()
  if (!name) {
    toast.error('分类名称不能为空')
    return
  }
  isRenamingCategory.value = true
  try {
    await updateCategory(renameCategoryId.value, name)
    showRenameCategoryModal.value = false
    await loadCategories()
    toast.success('分类更新成功')
  } catch (e: any) {
    toast.error(e.message || '更新失败')
  } finally {
    isRenamingCategory.value = false
  }
}

// ========== 移动栏目 ==========

const showMoveCategoryModal = ref(false)
const moveCategoryTargetId = ref<number | null>(null)
const moveCategoryTargetSourceType = ref<string | null>(null)
const moveCategoryTargetSubCategory = ref<string | null>(null)
const isMovingCategory = ref(false)

// 剪掉当前栏目节点（连带整个子树），目标列表自然排除自身与所有子孙
function pruneCategoryNode(nodes: CategoryTreeNode[], id: number): CategoryTreeNode[] {
  return nodes
    .filter(n => n.id !== id)
    .map(n => ({ ...n, children: pruneCategoryNode(n.children, id) }))
}

const moveCategoryTree = computed<CategoryTreeNode[]>(() =>
  renameCategoryId.value != null
    ? pruneCategoryNode(categoryTree.value, renameCategoryId.value)
    : categoryTree.value
)

function categoryPathById(id: number): string[] {
  const path: string[] = []
  let current = categories.value.find(c => c.id === id)
  while (current) {
    path.unshift(current.display_name || current.source_type)
    current = current.parent_id != null
      ? categories.value.find(c => c.id === current!.parent_id)
      : undefined
  }
  return path
}

// 编辑弹框中当前父栏目的完整路径
const renameCategoryPath = computed<string[]>(() =>
  renameCategoryParentId.value != null ? categoryPathById(renameCategoryParentId.value) : []
)

const moveCategoryTargetPath = computed<string[]>(() =>
  moveCategoryTargetId.value != null ? categoryPathById(moveCategoryTargetId.value) : []
)

function rootCategoryIdOf(id: number): number | null {
  let current = categories.value.find(c => c.id === id)
  while (current && current.parent_id != null) {
    current = categories.value.find(c => c.id === current!.parent_id)
  }
  return current ? current.id : null
}

// 跨顶级移动时后端会回填子树文档的 source_type，需警示
const isCrossTopMove = computed(() =>
  renameCategoryParentId.value != null &&
  moveCategoryTargetId.value != null &&
  rootCategoryIdOf(renameCategoryParentId.value) !== rootCategoryIdOf(moveCategoryTargetId.value)
)

const renameCategoryDocCount = computed(() => {
  const node = findCategoryNode(categoryTree.value, renameCategoryId.value ?? -1)
  return node ? node.document_count : 0
})

function openMoveCategory() {
  moveCategoryTargetId.value = null
  moveCategoryTargetSourceType.value = null
  moveCategoryTargetSubCategory.value = null
  showRenameCategoryModal.value = false
  showMoveCategoryModal.value = true
}

function handleMoveCategoryTargetSelect(cat: CategoryTreeNode) {
  moveCategoryTargetId.value = cat.id
  moveCategoryTargetSourceType.value = cat.parent_id === null
    ? cat.source_type
    : (cat.rootSourceType || cat.source_type)
  moveCategoryTargetSubCategory.value = cat.parent_id === null ? null : cat.source_type
}

async function confirmMoveCategory() {
  if (renameCategoryId.value == null || moveCategoryTargetId.value == null) return
  if (moveCategoryTargetId.value === renameCategoryParentId.value) {
    toast.warning('目标父栏目与当前父栏目相同，无需移动')
    return
  }
  isMovingCategory.value = true
  try {
    const result = await moveCategory(renameCategoryId.value, moveCategoryTargetId.value)
    showMoveCategoryModal.value = false
    await loadCategories()
    await loadDocuments()
    if (result.moved_documents && result.moved_documents > 0) {
      toast.success(`移动成功，${result.moved_documents} 篇文档已同步变更所属顶级栏目`)
    } else {
      toast.success('栏目移动成功')
    }
  } catch (e: any) {
    console.error('前端日志：移动栏目失败', e)
    toast.error(e.message || '移动栏目失败')
  } finally {
    isMovingCategory.value = false
  }
}

async function handleDeleteCategory(cat: CategoryResponse) {
  const node = findCategoryNode(categoryTree.value, cat.id)
  if (node && node.children.length > 0) {
    toast.error('该分类下存在子分类，请先删除子分类')
    return
  }
  if (!confirm(`删除分类"${cat.display_name || cat.source_type}"不会删除已上传的文档，确定删除？`)) return
  try {
    await deleteCategory(cat.id)
    if (selectedSourceType.value === cat.source_type || selectedSubCategory.value === cat.source_type) {
      selectedSourceType.value = null
      selectedSubCategory.value = null
      loadDocuments()
    }
    await loadCategories()
    toast.success('分类已删除')
  } catch (e: any) {
    toast.error(e.message || '删除分类失败')
  }
}

// ========== 文档操作 ==========

async function loadDocuments() {
  isLoading.value = true
  try {
    const result = await listDocuments(pageSize.value, (currentPage.value - 1) * pageSize.value, selectedSourceType.value || undefined, selectedSubCategory.value || undefined, effectiveIncludeSub.value)
    documents.value = result.items
    totalDocuments.value = result.total
    // 选中"全部"分类时，total 才是全部文档的总数
    if (!selectedSourceType.value) {
      totalAllDocuments.value = result.total
    }
  } catch (error: any) {
    console.error('前端日志：加载文档列表失败', error)
  } finally {
    isLoading.value = false
  }
}

const ALLOWED_EXTENSIONS = ['.docx', '.xlsx', '.pptx', '.pdf', '.txt', '.md', '.json', '.yaml', '.yml', '.log', '.csv', '.xml', '.ini', '.properties', '.conf', '.config']

function validateFile(file: File): { valid: boolean; error?: string } {
  const ext = '.' + file.name.split('.').pop()?.toLowerCase()
  if (!ALLOWED_EXTENSIONS.includes(ext)) {
    return { valid: false, error: `不支持的文件格式: ${ext}` }
  }
  if (file.size > MAX_FILE_SIZE) {
    return { valid: false, error: `文件过大，最大支持 ${MAX_FILE_SIZE_MB}MB` }
  }
  return { valid: true }
}

function handleFileSelect(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files && input.files.length > 0) {
    const newFiles: File[] = []
    const errors: string[] = []
    for (let i = 0; i < input.files.length; i++) {
      const file = input.files[i]
      const validation = validateFile(file)
      if (validation.valid) {
        if (!selectedFiles.value.some(f => f.name === file.name && f.size === file.size)) {
          newFiles.push(file)
        }
      } else {
        errors.push(`${file.name}: ${validation.error}`)
      }
    }
    if (newFiles.length > 0) {
      selectedFiles.value = [...selectedFiles.value, ...newFiles]
      uploadError.value = ''
      uploadErrors.value = []
    }
    if (errors.length > 0) {
      uploadError.value = errors.join('；')
    }
  }
}

function handleDrop(event: DragEvent) {
  isDragging.value = false
  if (event.dataTransfer?.files && event.dataTransfer.files.length > 0) {
    const newFiles: File[] = []
    const errors: string[] = []
    for (let i = 0; i < event.dataTransfer.files.length; i++) {
      const file = event.dataTransfer.files[i]
      const validation = validateFile(file)
      if (validation.valid) {
        if (!selectedFiles.value.some(f => f.name === file.name && f.size === file.size)) {
          newFiles.push(file)
        }
      } else {
        errors.push(`${file.name}: ${validation.error}`)
      }
    }
    if (newFiles.length > 0) {
      selectedFiles.value = [...selectedFiles.value, ...newFiles]
      uploadError.value = ''
      uploadErrors.value = []
    }
    if (errors.length > 0) {
      uploadError.value = errors.join('；')
    }
  }
}

function removeFile(index: number) {
  selectedFiles.value.splice(index, 1)
}

function clearFiles() {
  selectedFiles.value = []
  uploadError.value = ''
  uploadErrors.value = []
}

function handleCancelUpload() {
  clearFiles()
  showUploadModal.value = false
}

async function handleUpload() {
  if (selectedFiles.value.length === 0) return
  isUploading.value = true
  uploadError.value = ''
  uploadErrors.value = []

  const filesToUpload = [...selectedFiles.value]
  const totalFiles = filesToUpload.length
  const currentSourceType = uploadSourceType.value || undefined
  const currentSubCategory = uploadSubCategory.value || undefined

  uploadProgress.value = {
    total: totalFiles,
    current: 0,
    currentFileName: ''
  }

  const successResults: any[] = []
  const errorResults: { filename: string; error: string }[] = []

  try {
    for (let i = 0; i < filesToUpload.length; i++) {
      const file = filesToUpload[i]
      uploadProgress.value.current = i + 1
      uploadProgress.value.currentFileName = file.name

      try {
        const result = await uploadDocument(file, currentSourceType, currentSubCategory)
        successResults.push(result)
        await loadDocuments()
      } catch (error: any) {
        const errorMsg = error.response?.data?.error || error.message || '上传失败'
        errorResults.push({ filename: file.name, error: errorMsg })
      }
    }

    clearSearch()
    selectedFiles.value = []
    currentPage.value = 1
    await loadDocuments()
    await loadCategories()

    if (successResults.length > 0) {
      if (successResults.length === totalFiles) {
        toast.success('全部 ' + successResults.length + ' 个文件上传成功')
      } else {
        toast.success('' + successResults.length + ' 个文件上传成功')
      }
    }
    if (errorResults.length > 0) {
      // 保留弹框打开，让用户能看到每个文件的失败原因（错误详情渲染在弹框内）
      uploadErrors.value = errorResults
      toast.warning('' + errorResults.length + ' 个文件上传失败，详情见弹框')
    } else {
      showUploadModal.value = false
    }
  } catch (error: any) {
    const errorMsg = error.response?.data?.error || error.message || '上传失败'
    uploadError.value = errorMsg
    toast.error(errorMsg)
  } finally {
    isUploading.value = false
    uploadProgress.value = { total: 0, current: 0, currentFileName: '' }
  }
}

function handleDelete(doc: any) {
  documentToDelete.value = doc
  showDeleteConfirm.value = true
}

async function confirmDelete() {
  if (!documentToDelete.value) return
  isDeleting.value = true
  try {
    await deleteDocument(documentToDelete.value.id)
    showDeleteConfirm.value = false
    documentToDelete.value = null
    if (documents.value.length === 1 && currentPage.value > 1) {
      currentPage.value--
    }
    await loadDocuments()
    await loadCategories()
  } catch (error: any) {
    console.error('前端日志：删除文档失败', error)
    toast.error(error.response?.data?.error || '删除失败')
  } finally {
    isDeleting.value = false
  }
}

async function handleBatchDelete() {
  if (selectedArr.value.length === 0) return
  if (!confirm(`确定要删除选中的 ${selectedArr.value.length} 个文档吗？此操作不可恢复。`)) return
  isDeleting.value = true
  try {
    for (const docId of selectedArr.value) {
      await deleteDocument(docId)
    }
    clearSelection()
    await loadDocuments()
    await loadCategories()
    toast.success('批量删除成功')
  } catch (error: any) {
    console.error('前端日志：批量删除文档失败', error)
    toast.error(error.response?.data?.error || '批量删除失败')
  } finally {
    isDeleting.value = false
  }
}

// ========== 移动文档 ==========

function openMoveModal() {
  moveTargetSourceType.value = null
  moveTargetSubCategory.value = null
  showMoveModal.value = true
}

// 与左侧分类树选中语义一致：顶级分类 -> (source_type, null)；子分类 -> (rootSourceType, source_type)
function handleMoveCategorySelect(cat: CategoryTreeNode) {
  if (cat.parent_id === null) {
    moveTargetSourceType.value = cat.source_type
    moveTargetSubCategory.value = null
  } else {
    moveTargetSourceType.value = cat.rootSourceType || cat.source_type
    moveTargetSubCategory.value = cat.source_type
  }
}

// 移动弹框展示目标分类的完整路径（一级/二级/三级），从目标分类向上追溯父级
const moveCategoryPath = computed(() => {
  const target = moveTargetSubCategory.value || moveTargetSourceType.value
  if (!target) return []
  const path: string[] = []
  let current = categories.value.find(c => c.source_type === target)
  while (current) {
    path.unshift(current.display_name || current.source_type)
    current = current.parent_id != null
      ? categories.value.find(c => c.id === current!.parent_id)
      : undefined
  }
  return path
})

async function confirmMove() {
  if (!moveTargetSourceType.value || selectedArr.value.length === 0) return
  // 所选文档均已位于目标分类时无需移动
  const targetSub = moveTargetSubCategory.value ?? null
  const allAlreadyInTarget = documents.value
    .filter(d => selectedArr.value.includes(d.id))
    .every(d => d.source_type === moveTargetSourceType.value && (d.sub_category ?? null) === targetSub)
  if (allAlreadyInTarget) {
    toast.warning('所选文档已在目标分类中')
    return
  }
  isMoving.value = true
  try {
    const result = await moveDocuments(selectedArr.value, moveTargetSourceType.value, moveTargetSubCategory.value)
    showMoveModal.value = false
    clearSelection()
    await loadDocuments()
    await loadCategories()
    if (result.skipped > 0) {
      toast.success(`移动成功（${result.moved} 个），${result.skipped} 个已在目标分类`)
    } else {
      toast.success(`成功移动 ${result.moved} 个文档`)
    }
  } catch (error: any) {
    console.error('前端日志：移动文档失败', error)
    toast.error(error.message || '移动失败')
  } finally {
    isMoving.value = false
  }
}

function formatTime(isoString: string): string {
  if (!isoString) return '-'
  const date = new Date(isoString)
  const year = date.getFullYear()
  const month = date.getMonth() + 1
  const day = date.getDate()
  const hours = date.getHours().toString().padStart(2, '0')
  const minutes = date.getMinutes().toString().padStart(2, '0')
  return year + '-' + month + '-' + day + ' ' + hours + ':' + minutes
}

function getFileIconClass(fileType: string): string {
  const ext = fileType.toLowerCase()
  if (ext === 'pdf' || ext === '.pdf') return 'bg-danger-500'
  if (ext === 'docx' || ext === '.docx' || ext === 'doc' || ext === '.doc') return 'bg-primary-500'
  if (ext === 'xlsx' || ext === '.xlsx' || ext === 'xls' || ext === '.xls') return 'bg-success-500'
  if (ext === 'pptx' || ext === '.pptx' || ext === 'ppt' || ext === '.ppt') return 'bg-warning-500'
  return 'bg-gray-500'
}

function getFileIconClassByExt(filename: string): string {
  const ext = '.' + filename.split('.').pop()?.toLowerCase()
  return getFileIconClass(ext)
}

async function openDocument(docId: number, title: string) {
  try {
    await downloadDocument(docId, title)
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '文档下载失败')
  }
}

async function openChunkDetail(row: DocumentResponse) {
  chunkDocTitle.value = row.title
  chunkList.value = []
  showChunkModal.value = true
  isLoadingChunks.value = true
  try {
    const result = await getDocumentChunks(row.id)
    chunkList.value = result.chunks || []
  } catch (e) {
    console.error('获取分块详情失败:', e)
    toast.error('获取分块详情失败')
  } finally {
    isLoadingChunks.value = false
  }
}

// ========== 文档详情（元数据查看） ==========

// 常见溯源/元数据键的展示名；未知键按原键名显示，不做穷举维护
const META_LABELS: Record<string, string> = {
  original_url: '原文链接',
  external_id: '外部 ID',
  native_id: '记录原生 ID',
  source_code: '来源编码',
  run_id: '同步运行 ID',
  content_mode: '内容模式',
  pipeline_version: '管线版本',
  ingested_at: '入库时间',
  granularity: '知识粒度',
  granularity_overflow: '整条超限回退',
  author: '作者',
  publish_time: '发布时间',
}

interface MetaEntry {
  key: string
  label: string
  text: string
  multiline: boolean
}

function formatMetaValue(value: unknown): string {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

// raw_payload（api-ingest 原始记录，默认 ≤32KB）单独折叠展示，不混入键值列表
const metaEntries = computed<MetaEntry[]>(() => {
  const meta = docDetail.value?.metadata
  if (!meta) return []
  return Object.entries(meta)
    .filter(([key]) => key !== 'raw_payload')
    .map(([key, value]) => ({
      key,
      label: META_LABELS[key] || key,
      text: formatMetaValue(value),
      multiline: typeof value === 'object' && value !== null,
    }))
})

const rawPayloadText = computed(() => {
  const raw = docDetail.value?.metadata?.raw_payload
  if (raw === null || raw === undefined) return ''
  return formatMetaValue(raw)
})

async function openDocDetail(row: DocumentResponse) {
  // 过期响应守卫：快速切换文档时丢弃晚到的旧响应，避免标题与内容错配
  const requestId = ++docDetailRequestId
  docDetailTitle.value = row.title
  docDetail.value = null
  rawPayloadExpanded.value = false
  showDocDetailModal.value = true
  isLoadingDocDetail.value = true
  try {
    const result = await getDocumentDetail(row.id)
    if (requestId !== docDetailRequestId) return
    docDetail.value = result.document || null
  } catch (e) {
    if (requestId !== docDetailRequestId) return
    console.error('获取文档详情失败:', e)
    toast.error(e instanceof Error ? e.message : '获取文档详情失败')
  } finally {
    if (requestId === docDetailRequestId) {
      isLoadingDocDetail.value = false
    }
  }
}

async function copyToClipboard(text: string, label: string) {
  try {
    await navigator.clipboard.writeText(text)
    toast.success(`${label}已复制`)
  } catch {
    toast.error('复制失败')
  }
}

function truncateVector(vecText: string | null): string {
  if (!vecText) return ''
  const nums = vecText.replace(/^\[|\]$/g, '').split(',')
  if (nums.length <= 20) return vecText
  return '[' + nums.slice(0, 20).join(',') + ', ...] (' + nums.length + ' 维)'
}

async function handleLogout() {
  await tenantLogout()
  router.push(isTenantMode.value ? route.path.replace(/\/knowledge.*/, '') : '/')
}

onMounted(() => {
  loadAvailableSubagents()
  loadCategories()
  loadDocuments()
})
</script>
