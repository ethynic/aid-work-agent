<template>
  <div class="page-container">
    <AppHeader
      title="租户管理"
      :is-logged-in="isLoggedIn"
      :user="admin"
      @toggle-sidebar="handleToggleSidebar"
    />

    <div class="page-content">
      <!-- 搜索区 + 操作按钮区 -->
      <div class="page-toolbar">
        <div class="page-toolbar-left">
          <BaseInput
            v-model="searchInput"
            size="sm"
            placeholder="搜索企业名称、租户代码、手机号"
            class="w-80"
            @keyup.enter="clientHandleSearch(searchInput)"
          />
          <BaseButton size="sm" @click="clientHandleSearch(searchInput)">搜索</BaseButton>
        </div>
        <div class="page-toolbar-right">
          <BaseButton @click="openAddDialog">新增租户</BaseButton>
        </div>
      </div>

      <!-- 表格 -->
      <div class="table-scroll-wrapper flex-1 min-h-0">
        <BaseTable :columns="columns" :data="displayTenants" row-key="tenant_id">
          <template #seq="{ index }">
            {{ seqNumber(index) }}
          </template>
          <template #tenant_code="{ row }">
            <a href="javascript:void(0)" @click="openEditDialog(row)" class="text-primary-600 hover:text-primary-700 hover:underline font-mono text-sm">
              {{ row.tenant_code || '-' }}
            </a>
          </template>
          <template #status="{ row }">
            <span
              :class="getStatusClass(row.status)"
              class="px-2 py-1 rounded-full text-xs font-medium"
            >
              {{ getStatusLabel(row.status) }}
            </span>
          </template>
          <template #expire_at="{ row }">
            <span v-if="row.expire_at" :class="getExpireStatusClass(row.expire_at)" class="text-sm">
              {{ formatExpireDate(row.expire_at) }}
            </span>
            <span v-else class="text-sm text-muted">永久有效</span>
          </template>
          <template #agent_count="{ row }">
            <span v-if="row.agent_count > 0"
              class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-success-100 text-success-700">
              {{ row.agent_count }} 个已授权
            </span>
            <span v-else
              class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-danger-100 text-danger-700">
              未授权
            </span>
          </template>
          <template #credit_balance="{ row }">
            <span
              :class="Number(row.credit_balance || 0) > 0 ? 'text-default' : 'text-muted'"
              class="text-sm tabular-nums"
              :title="formatCredit(row.credit_balance) + ' 积分'"
            >
              {{ formatCredit(row.credit_balance) }}
            </span>
          </template>
          <template #tenant_url="{ row }">
            <div class="flex items-center gap-2">
              <a :href="getTenantUrl(row.tenant_id)" target="_blank"
                class="text-primary-600 hover:text-primary-700 hover:underline text-sm">
                {{ getTenantUrl(row.tenant_id) }}
              </a>
              <button @click="copyTenantUrl(row.tenant_id)"
                class="p-1 text-muted hover:text-primary-600 hover:bg-primary-50 rounded transition-colors"
                title="复制网址">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
              </button>
            </div>
          </template>
          <template #actions="{ row }">
            <div class="flex justify-center gap-1">
              <BaseButton intent="ghost" size="sm" @click="openEditDialog(row)">编辑</BaseButton>
              <BaseButton intent="danger-ghost" size="sm" @click="handleDelete(row)">删除</BaseButton>
            </div>
          </template>
          <template #empty>暂无租户数据</template>
        </BaseTable>
      </div>

      <!-- 分页器 -->
      <BasePagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :total="total"
        :show-size-changer="true"
        @change="onPageChange"
      />
    </div>

    <!-- 新增/编辑弹窗 -->
    <BaseModal
      v-model="showFormDialog"
      :title="isEdit ? '编辑租户' : '新增租户'"
      size="xl"
      :mode="isEdit ? 'edit' : 'create'"
      :is-dirty="isFormDirty"
      :content-class="{ 'modal-fullscreen': isFullscreen }"
    >
      <template #header-extra>
        <button class="modal-fullscreen-btn" title="全屏" @click="toggleFullscreen">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>
          </svg>
        </button>
      </template>

      <!-- 标签页 -->
      <div class="mb-4 border-b border-default">
        <div class="flex gap-6">
          <button
            @click="activeTab = 'basic'"
            :class="[
              'pb-2 text-sm font-medium border-b-2 transition-colors',
              activeTab === 'basic'
                ? 'text-primary-600 border-primary-600'
                : 'text-muted border-transparent hover:text-default hover:border-hover'
            ]"
          >
            基本信息
          </button>
          <button
            @click="activeTab = 'agents'"
            :class="[
              'pb-2 text-sm font-medium border-b-2 transition-colors',
              activeTab === 'agents'
                ? 'text-primary-600 border-primary-600'
                : 'text-muted border-transparent hover:text-default hover:border-hover'
            ]"
          >
            数字员工授权
            <span v-if="selectedAgentIds.length > 0" class="ml-1 text-xs">({{ selectedAgentIds.length }})</span>
          </button>
          <button
            v-if="isEdit"
            @click="activeTab = 'migration'"
            :class="[
              'pb-2 text-sm font-medium border-b-2 transition-colors',
              activeTab === 'migration'
                ? 'text-primary-600 border-primary-600'
                : 'text-muted border-transparent hover:text-default hover:border-hover'
            ]"
          >
            数据迁移
          </button>
          <button
            v-if="isEdit"
            @click="activeTab = 'activation'"
            :class="[
              'pb-2 text-sm font-medium border-b-2 transition-colors',
              activeTab === 'activation'
                ? 'text-primary-600 border-primary-600'
                : 'text-muted border-transparent hover:text-default hover:border-hover'
            ]"
          >
            激活码
          </button>
        </div>
      </div>

      <!-- 基本信息标签页 -->
      <div v-show="activeTab === 'basic'" class="grid grid-cols-2 gap-x-6 gap-y-4">
        <div>
          <label class="text-sm text-muted mb-1 block">企业名称 <span class="text-danger-500">*</span></label>
          <input v-model="formData.company_name" type="text" placeholder="请输入企业名称" maxlength="100"
            class="w-full px-3 py-2 bg-surface-hover border border-hover rounded-lg text-default focus:outline-none focus:border-primary-400" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">租户代码 <span class="text-danger-500">*</span></label>
          <input v-model="formData.tenant_code" type="text" placeholder="4-8位字母数字" maxlength="8"
            @input="validateTenantCodeFormat"
            @blur="checkTenantCodeUnique"
            class="w-full px-3 py-2 bg-surface-hover border border-hover rounded-lg text-default focus:outline-none focus:border-primary-400" />
          <div class="text-xs text-danger-500 mt-1" v-if="tenantCodeError">{{ tenantCodeError }}</div>
          <p class="text-xs text-muted mt-1">4-8位字母数字组合，不区分大小写，创建后不可修改</p>
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">联系人</label>
          <input v-model="formData.contact_name" type="text" placeholder="请输入联系人姓名" maxlength="50"
            class="w-full px-3 py-2 bg-surface-hover border border-hover rounded-lg text-default focus:outline-none focus:border-primary-400" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">联系电话</label>
          <input v-model="formData.contact_phone" type="tel" placeholder="请输入联系电话" maxlength="20"
            class="w-full px-3 py-2 bg-surface-hover border border-hover rounded-lg text-default focus:outline-none focus:border-primary-400" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">初始管理员姓名</label>
          <input v-model="formData.initial_admin_name" type="text" placeholder="请输入管理员姓名" maxlength="50"
            class="w-full px-3 py-2 bg-surface-hover border border-hover rounded-lg text-default focus:outline-none focus:border-primary-400" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">初始管理员手机号</label>
          <input v-model="formData.initial_admin_phone" type="tel" placeholder="请输入11位手机号" maxlength="11"
            class="w-full px-3 py-2 bg-surface-hover border border-hover rounded-lg text-default focus:outline-none focus:border-primary-400" />
        </div>
        <div>
          <label class="text-sm text-muted mb-1 block">套餐</label>
          <select v-model="formData.plan"
            class="w-full px-3 py-2 bg-surface-hover border border-hover rounded-lg text-default focus:outline-none focus:border-primary-400">
            <option value="basic">基础版 (basic)</option>
            <option value="standard">标准版 (standard)</option>
            <option value="premium">高级版 (premium)</option>
          </select>
        </div>
        <div v-if="isEdit">
          <label class="text-sm text-muted mb-1 block">状态</label>
          <select v-model="formData.status"
            class="w-full px-3 py-2 bg-surface-hover border border-hover rounded-lg text-default focus:outline-none focus:border-primary-400">
            <option value="active">正常</option>
            <option value="suspended">停用</option>
            <option value="deactivated">已删除</option>
          </select>
        </div>
        <div v-if="isEdit">
          <label class="text-sm text-muted mb-1 block">到期日期</label>
          <input v-model="formData.expire_at" type="date" placeholder="不设置则永久有效"
            class="w-full px-3 py-2 bg-surface-hover border border-hover rounded-lg text-default focus:outline-none focus:border-primary-400" />
          <p class="text-xs text-muted mt-1">到期当天 23:59:59 前仍可登录，清空则永久有效</p>
        </div>
        <div v-if="isEdit">
          <label class="text-sm text-muted mb-1 block">积分余额</label>
          <input :value="formatCredit(currentTenant?.credit_balance)" type="text" disabled
            class="w-full px-3 py-2 bg-canvas border border-default rounded-lg text-muted cursor-not-allowed tabular-nums" />
          <p class="text-xs text-muted mt-1">只读字段，通过充值/计费扣减自动维护</p>
        </div>
      </div>

      <!-- 数字员工授权标签页 -->
      <div v-show="activeTab === 'agents'" class="overflow-y-auto" style="max-height: calc(90vh - 220px);">
        <div v-if="loadingAgents" class="text-center py-6 text-muted text-sm">加载中...</div>
        <div v-else-if="availableAgents.length === 0" class="text-center py-6 text-muted text-sm">暂无可用数字员工</div>
        <div v-else class="space-y-2 py-2">
          <div v-for="agent in availableAgents" :key="agent.agent_id" class="flex items-center p-2 hover:bg-canvas rounded">
            <input
              type="checkbox"
              :checked="isAgentAuthorized(agent.agent_id)"
              @change="toggleAgentSelection(agent.agent_id)"
              class="w-4 h-4 text-primary-600 border-hover rounded focus:ring-primary-500"
            />
            <div class="ml-3 flex-1">
              <div class="text-sm font-medium text-default">{{ agent.name }}</div>
              <div v-if="agent.description" class="text-xs text-muted">{{ agent.description }}</div>
            </div>
            <span class="ml-2 text-xs px-1.5 py-0.5 rounded"
              :class="agent.type === 'builtin' ? 'bg-info-100 text-info-700' : 'bg-success-100 text-success-700'">
              {{ agent.type === 'builtin' ? '内置' : '定制' }}
            </span>
            <button
              :disabled="!isAgentAuthorized(agent.agent_id)"
              @click="openEnvVarDialog(agent)"
              class="ml-2 text-xs px-2 py-1 rounded border border-primary-200 bg-primary-50 text-primary-700 hover:bg-primary-100 hover:border-primary-400 transition-colors disabled:border-default disabled:bg-surface disabled:text-muted disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-surface disabled:hover:border-default disabled:hover:text-muted"
              title="环境变量设置"
            >环境变量</button>
            <button
              :disabled="!isAgentAuthorized(agent.agent_id)"
              @click="openKnowledgeDialog(agent)"
              class="ml-1 text-xs px-2 py-1 rounded border border-success-200 bg-success-50 text-success-700 hover:bg-success-100 hover:border-success-400 transition-colors disabled:border-default disabled:bg-surface disabled:text-muted disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-surface disabled:hover:border-default disabled:hover:text-muted"
              title="知识库关联"
            >知识库</button>
            <button
              v-if="configSupportedAgents.includes(agent.agent_id)"
              :disabled="!isAgentAuthorized(agent.agent_id)"
              @click="openConfigFileDialog(agent)"
              class="ml-1 text-xs px-2 py-1 rounded border border-info-200 bg-info-50 text-info-700 hover:bg-info-100 hover:border-info-400 transition-colors disabled:border-default disabled:bg-surface disabled:text-muted disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-surface disabled:hover:border-default disabled:hover:text-muted"
              title="API 配置文件"
            >API 配置</button>
            <a
              v-if="isEdit"
              :href="isAgentAuthorized(agent.agent_id) ? getAgentChatUrl(currentTenant?.tenant_id, agent.agent_id) : undefined"
              target="_blank"
              :tabindex="isAgentAuthorized(agent.agent_id) ? 0 : -1"
              :aria-disabled="!isAgentAuthorized(agent.agent_id)"
              class="ml-1 text-xs px-2 py-1 rounded border border-primary-200 bg-primary-50 text-primary-700 hover:bg-primary-100 hover:border-primary-400 transition-colors aria-disabled:border-default aria-disabled:bg-surface aria-disabled:text-muted aria-disabled:opacity-40 aria-disabled:cursor-not-allowed aria-disabled:hover:bg-surface aria-disabled:hover:border-default aria-disabled:hover:text-muted"
              :title="isAgentAuthorized(agent.agent_id) ? getAgentChatUrl(currentTenant?.tenant_id, agent.agent_id) : '未授权数字员工不可访问入口链接'"
              @click="!isAgentAuthorized(agent.agent_id) && $event.preventDefault()"
            >入口链接</a>
          </div>
        </div>
      </div>

      <!-- 数据迁移标签页 -->
      <div v-show="activeTab === 'migration'" class="overflow-y-auto" style="max-height: calc(90vh - 220px);">
        <TenantMigration v-if="currentTenant" :tenant-id="currentTenant.tenant_id" />
      </div>

      <!-- 激活码标签页 -->
      <div v-show="activeTab === 'activation'" class="overflow-y-auto" style="max-height: calc(90vh - 220px);">
        <TenantActivationCodes v-if="currentTenant" :tenant-id="currentTenant.tenant_id" />
      </div>

      <div v-if="formError" class="mt-4 p-2 bg-danger-50 border border-danger-200 rounded text-danger-600 text-sm">{{ formError }}</div>

      <template #footer>
        <BaseButton intent="secondary" @click="showFormDialog = false">取消</BaseButton>
        <BaseButton :disabled="submitting" @click="handleSubmit">
          {{ submitting ? '处理中...' : '确认' }}
        </BaseButton>
      </template>
    </BaseModal>

    <!-- 环境变量弹窗 -->
    <div v-if="showEnvVarDialog" class="fixed inset-0 z-[60] flex items-center justify-center">
      <div class="absolute inset-0 bg-black/50" @click="showEnvVarDialog = false"></div>
      <div class="relative bg-white rounded-xl shadow-2xl w-full max-w-5xl mx-4 p-6">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-lg font-bold text-default">环境变量设置</h3>
          <span class="text-xs text-muted">{{ envVarAgentName }} · {{ currentTenant?.company_name }}</span>
        </div>

        <div class="text-xs text-muted mb-3">
          这些变量将在 {{ envVarAgentName }} 运行时注入为环境变量，供 http_api 工具中的 ${VAR_NAME} 引用。
        </div>

        <div v-if="loadingEnvVars" class="text-center py-6 text-muted text-sm">加载中...</div>
        <div v-else class="space-y-2 max-h-[60vh] overflow-y-auto">
          <div v-for="(item, idx) in envVarList" :key="idx" class="flex items-start gap-2">
            <input
              v-model="item.name"
              placeholder="变量名"
              class="flex-1 px-2 py-1.5 text-sm border border-default rounded focus:outline-none focus:border-primary-400 font-mono"
            />
            <input
              v-model="item.value"
              placeholder="变量值"
              class="flex-[2] px-2 py-1.5 text-sm border border-default rounded focus:outline-none focus:border-primary-400"
            />
            <input
              v-model="item.description"
              placeholder="说明"
              class="flex-1 px-2 py-1.5 text-sm border border-default rounded focus:outline-none focus:border-primary-400"
            />
            <button @click="envVarList.splice(idx, 1)" class="text-muted hover:text-danger-500 transition-colors px-1">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
          </div>
          <button
            @click="envVarList.push({ name: '', value: '', description: '' })"
            class="w-full py-1.5 text-sm text-primary-600 hover:text-primary-700 border border-dashed border-default rounded hover:border-primary-400 transition-colors"
          >+ 添加变量</button>
        </div>

        <div v-if="envVarError" class="mt-3 p-2 bg-danger-50 border border-danger-200 rounded text-danger-600 text-sm">{{ envVarError }}</div>

        <div class="flex gap-3 mt-6">
          <BaseButton intent="secondary" class="flex-1" @click="showEnvVarDialog = false">取消</BaseButton>
          <BaseButton :disabled="savingEnvVars" class="flex-1" @click="handleSaveEnvVars">
            {{ savingEnvVars ? '保存中...' : '保存' }}
          </BaseButton>
        </div>
      </div>
    </div>

    <!-- 知识库关联弹窗 -->
    <div v-if="showKnowledgeDialog" class="fixed inset-0 z-[60] flex items-center justify-center">
      <div class="absolute inset-0 bg-black/50" @click="showKnowledgeDialog = false"></div>
      <div class="relative bg-white rounded-xl shadow-2xl w-full max-w-3xl mx-4 p-6">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-lg font-bold text-default">知识库关联</h3>
          <span class="text-xs text-muted">{{ knowledgeAgentName }} · {{ currentTenant?.company_name }}</span>
        </div>

        <div class="text-xs text-muted mb-3">
          选择 {{ knowledgeAgentName }} 可以检索的知识库，关联后运行时会自动注入检索指引。
        </div>

        <div v-if="loadingKnowledge" class="text-center py-6 text-muted text-sm">加载中...</div>
        <div v-else-if="knowledgeCategories.length === 0" class="text-center py-6 text-muted text-sm">
          该租户暂无知识库分类，请先在知识库管理中创建分类并上传文档。
        </div>
        <div v-else class="space-y-2 max-h-[60vh] overflow-y-auto">
          <label v-for="cat in knowledgeCategories" :key="cat.source_type"
            class="flex items-start gap-2.5 p-2 rounded-lg hover:bg-canvas cursor-pointer transition-colors">
            <input type="checkbox"
              :value="cat.source_type"
              v-model="knowledgeSelectedTypes"
              class="mt-0.5 w-4 h-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500" />
            <div class="flex-1 min-w-0">
              <div class="text-sm font-medium text-default">{{ cat.display_name || cat.source_type }}</div>
              <div class="text-xs text-muted">{{ cat.source_type }} · {{ cat.document_count }} 篇文档</div>
            </div>
          </label>
        </div>

        <div v-if="knowledgeError" class="mt-3 p-2 bg-danger-50 border border-danger-200 rounded text-danger-600 text-sm">{{ knowledgeError }}</div>

        <div class="flex gap-3 mt-6">
          <BaseButton intent="secondary" class="flex-1" @click="showKnowledgeDialog = false">取消</BaseButton>
          <BaseButton :disabled="savingKnowledge" class="flex-1" @click="handleSaveKnowledge">
            {{ savingKnowledge ? '保存中...' : '保存' }}
          </BaseButton>
        </div>
      </div>
    </div>

    <!-- API 配置文件弹窗 -->
    <div v-if="showConfigFileDialog" class="fixed inset-0 z-[60] flex items-center justify-center">
      <div class="absolute inset-0 bg-black/50" @click="showConfigFileDialog = false"></div>
      <div class="relative bg-white rounded-xl shadow-2xl w-full max-w-lg mx-4 p-6">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-lg font-bold text-default">API 配置文件</h3>
          <span class="text-xs text-muted">{{ configFileAgentName }} · {{ currentTenant?.company_name }}</span>
        </div>

        <div class="text-xs text-muted mb-3">
          上传外部系统 API 配置文件（Markdown 格式）。该文件将供 {{ configFileAgentName }} 运行时读取，了解如何调用外部系统接口。
        </div>

        <!-- 已配置状态 -->
        <div v-if="configFileStatus?.configured" class="mb-4 p-3 bg-success-50 border border-success-200 rounded-lg">
          <div class="flex items-center justify-between">
            <div>
              <div class="text-sm text-success-700 font-medium">已配置</div>
              <div class="text-xs text-muted mt-0.5">
                {{ configFileStatus.filename }} · {{ ((configFileStatus.size ?? 0) / 1024).toFixed(1) }} KB
              </div>
            </div>
            <div class="flex gap-2">
              <button @click="handleDownloadConfigFile" class="text-xs px-3 py-1.5 bg-white border border-default rounded hover:border-primary-400 transition-colors">下载</button>
              <button @click="handleDeleteConfigFile" class="text-xs px-3 py-1.5 bg-white border border-danger-300 text-danger-600 rounded hover:bg-danger-50 transition-colors">删除</button>
            </div>
          </div>
        </div>
        <div v-else class="mb-4 p-3 bg-canvas border border-default rounded-lg">
          <div class="text-sm text-muted">未配置，请上传 API 配置文件</div>
        </div>

        <!-- 上传区域 -->
        <div class="border-2 border-dashed border-default rounded-lg p-4 text-center hover:border-primary-400 transition-colors cursor-pointer relative"
          @click="triggerConfigFileInput" @dragover.prevent @drop.prevent="handleConfigFileDrop">
          <input ref="configFileInputRef" type="file" accept=".md" class="hidden" @change="handleConfigFileSelect" />
          <div class="text-sm text-muted">点击或拖拽上传 .md 文件</div>
          <div class="text-xs text-muted mt-1">{{ configFileAgentName ? `${configFileAgentName}.md` : '配置文件' }}</div>
        </div>

        <div v-if="configFileUploading" class="mt-2 text-xs text-muted">上传中...</div>
        <div v-if="configFileError" class="mt-3 p-2 bg-danger-50 border border-danger-200 rounded text-danger-600 text-sm">{{ configFileError }}</div>

        <div class="flex gap-3 mt-6">
          <BaseButton intent="secondary" class="flex-1" @click="showConfigFileDialog = false">关闭</BaseButton>
        </div>
      </div>
    </div>

    <!-- 详情弹窗已移除，点击租户代码直接进入编辑页 -->
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, inject } from 'vue'
import { useToast } from 'vue-toastification'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { usePageContext } from '@/composables/usePageContext'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseTable, { type TableColumn } from '@/components/ui/BaseTable.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import { listTenants, createTenant, updateTenant, deleteTenant, type TenantFormData } from '@/api/saasTenant'
import { getAllAvailableAgents, getTenantAgentPermissions, setTenantAgentPermissions, getSubagentEnvVars, setSubagentEnvVars, getConfigFileStatus, uploadConfigFile, downloadConfigFile, deleteConfigFile, type AgentItem, type EnvVarItem, getSubagentKnowledgeSources, setSubagentKnowledgeSources, type KnowledgeSourceItem, listTenantKnowledgeCategories } from '@/api/saasPermissions'
import TenantMigration from '@/components/saas/TenantMigration.vue'
import TenantActivationCodes from '@/components/saas/TenantActivationCodes.vue'
import { TenantStatus, TenantStatusMap } from '@/api/enums'
import { formatCredit } from '@/utils/formatCredit'

const toast = useToast()
const { isLoggedIn, admin } = useTenantAuth()

const toggleSidebarFn = inject<() => void>('toggleSidebar')
function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}

// 表格列定义
const columns: TableColumn[] = [
  { key: 'seq', label: '序号', width: '60px', thAlign: 'center' },
  { key: 'tenant_code', label: '租户代码', width: '140px' },
  { key: 'company_name', label: '企业名称', width: '180px' },
  { key: 'initial_admin_phone', label: '初始管理员手机号', width: '150px' },
  { key: 'status', label: '状态', width: '90px' },
  { key: 'expire_at', label: '到期日期', width: '120px' },
  { key: 'agent_count', label: '数字员工授权', width: '130px' },
  { key: 'credit_balance', label: '积分余额', width: '120px' },
  { key: 'tenant_url', label: '租户入口网址', width: '280px' },
  { key: 'actions', label: '操作', width: '140px', thAlign: 'center' },
]

const showFormDialog = ref(false)
const isEdit = ref(false)
const submitting = ref(false)
const formError = ref('')
const tenantCodeError = ref('')
const currentTenant = ref<any>(null)
const isFullscreen = ref(false)

function toggleFullscreen() {
  isFullscreen.value = !isFullscreen.value
}

// 脏数据检测
const originalFormData = ref<any>(null)
const isFormDirty = computed(() => {
  if (!isEdit.value || !originalFormData.value) return false
  return JSON.stringify(formData.value) !== JSON.stringify(originalFormData.value)
})

// 数字员工授权标签页相关
const activeTab = ref<'basic' | 'agents' | 'migration' | 'activation'>('basic')
const availableAgents = ref<AgentItem[]>([])
const selectedAgentIds = ref<string[]>([])
const loadingAgents = ref(false)

// 环境变量弹窗
const showEnvVarDialog = ref(false)
const envVarAgentId = ref('')
const envVarAgentName = ref('')
const envVarList = ref<Array<{ name: string; value: string; description: string }>>([])
const loadingEnvVars = ref(false)
const savingEnvVars = ref(false)
const envVarError = ref('')

// 知识库关联弹窗
const showKnowledgeDialog = ref(false)
const knowledgeAgentId = ref('')
const knowledgeAgentName = ref('')
const knowledgeCategories = ref<{ source_type: string; display_name: string | null; document_count: number }[]>([])
const knowledgeSelectedTypes = ref<string[]>([])
const loadingKnowledge = ref(false)
const savingKnowledge = ref(false)
const knowledgeError = ref('')

// API 配置文件弹窗
const configSupportedAgents = ['after-sales', 'order-processing']
const showConfigFileDialog = ref(false)
const configFileAgentId = ref('')
const configFileAgentName = ref('')
const configFileStatus = ref<{ configured: boolean; filename?: string; size?: number } | null>(null)
const configFileUploading = ref(false)
const configFileError = ref('')
const configFileInputRef = ref<HTMLInputElement | null>(null)

const defaultFormData: TenantFormData = {
  company_name: '',
  tenant_code: '',
  contact_name: '',
  contact_phone: '',
  initial_admin_name: '',
  initial_admin_phone: '',
  plan: 'basic',
  expire_at: '',
}

const formData = ref<TenantFormData & { status: string }>({ ...defaultFormData, status: 'active' })

// 分页和搜索
const searchInput = ref('')
const allTenants = ref<any[]>([])
const displayTenants = ref<any[]>([])
const total = ref(0)

const { currentPage, pageSize, seqNumber } =
  usePageContext(async () => {
    // 首次加载或刷新时从 API 获取
    if (allTenants.value.length === 0 || !allTenants.value.length) {
      await loadAllTenants()
    }
    applyFilterAndPagination()
  })

async function loadAllTenants() {
  // 拉一大页避免分页：本页是客户端分页/搜索，后端 list_tenants 默认 page_size=20 会导致切片最多只有 20 条
  const res = await listTenants({ page: 1, page_size: 1000 })
  if (res.success) {
    allTenants.value = res.tenants || []
  }
}

function applyFilterAndPagination() {
  let filtered = allTenants.value
  const kw = searchInput.value.trim().toLowerCase()
  if (kw) {
    filtered = allTenants.value.filter(t =>
      (t.company_name && t.company_name.toLowerCase().includes(kw)) ||
      (t.tenant_code && t.tenant_code.toLowerCase().includes(kw)) ||
      (t.initial_admin_phone && t.initial_admin_phone.includes(kw))
    )
  }
  total.value = filtered.length
  const start = (currentPage.value - 1) * pageSize.value
  displayTenants.value = filtered.slice(start, start + pageSize.value)
}

// 客户端过滤和分页

async function clientHandleSearch(keyword?: string) {
  if (keyword !== undefined) {
    searchInput.value = keyword
  }
  currentPage.value = 1
  applyFilterAndPagination()
}

function onPageChange(page: number, size: number) {
  pageSize.value = size
  currentPage.value = page
  applyFilterAndPagination()
}

async function clientRefresh() {
  await loadAllTenants()
  applyFilterAndPagination()
}

function formatExpireDate(dateStr: string | undefined) {
  if (!dateStr) return '永久有效'
  const date = new Date(dateStr)
  return date.toLocaleDateString('zh-CN')
}

function getExpireStatusClass(dateStr: string | undefined): string {
  if (!dateStr) return 'text-default'

  const expireDate = new Date(dateStr)
  const now = new Date()
  const diffDays = Math.ceil((expireDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24))

  if (diffDays < 0) return 'text-danger-600 font-medium'
  if (diffDays < 15) return 'text-warning-600 font-medium'
  return 'text-default'
}

function openAddDialog() {
  isEdit.value = false
  formData.value = { ...defaultFormData, status: 'active' }
  originalFormData.value = null
  formError.value = ''
  activeTab.value = 'basic'
  selectedAgentIds.value = []
  loadingAgents.value = true
  getAllAvailableAgents().then(res => {
    if (res.success && res.data) {
      availableAgents.value = res.data
    }
  }).catch(e => {
    console.error('加载数字员工授权失败:', e)
  }).finally(() => {
    loadingAgents.value = false
  })
  showFormDialog.value = true
}

async function openEditDialog(tenant: any) {
  isEdit.value = true
  currentTenant.value = tenant
  formData.value = {
    company_name: tenant.company_name,
    tenant_code: tenant.tenant_code || '',
    contact_name: tenant.contact_name || '',
    contact_phone: tenant.contact_phone || '',
    initial_admin_name: tenant.initial_admin_name || '',
    initial_admin_phone: tenant.initial_admin_phone || '',
    plan: tenant.plan,
    status: String(tenant.status),
    expire_at: tenant.expire_at ? tenant.expire_at.split('T')[0].split(' ')[0] : '',
  }
  originalFormData.value = { ...formData.value }
  activeTab.value = 'basic'
  selectedAgentIds.value = []
  loadingAgents.value = true
  try {
    const [agentsRes, permissionsRes] = await Promise.all([
      getAllAvailableAgents(),
      getTenantAgentPermissions(tenant.tenant_id),
    ])
    if (agentsRes.success && agentsRes.data) {
      availableAgents.value = agentsRes.data
    }
    if (permissionsRes.success && permissionsRes.data) {
      // 只保留在 availableAgents 中存在的 agent_id，避免已删除/禁用的数字员工影响计数
      const allSelectedIds = permissionsRes.data.agent_ids || []
      const availableAgentIds = availableAgents.value.map(a => a.agent_id)
      selectedAgentIds.value = allSelectedIds.filter(id => availableAgentIds.includes(id))
    }
  } catch (e) {
    console.error('加载数字员工授权失败:', e)
  } finally {
    loadingAgents.value = false
  }
  formError.value = ''
  showFormDialog.value = true
}

function isAgentAuthorized(agentId: string) {
  return selectedAgentIds.value.includes(agentId)
}

function toggleAgentSelection(agentId: string) {
  const index = selectedAgentIds.value.indexOf(agentId)
  if (index >= 0) {
    selectedAgentIds.value.splice(index, 1)
  } else {
    selectedAgentIds.value.push(agentId)
  }
}

async function openEnvVarDialog(agent: AgentItem) {
  if (!currentTenant.value) return
  envVarAgentId.value = agent.agent_id
  envVarAgentName.value = agent.name
  envVarError.value = ''
  envVarList.value = []
  showEnvVarDialog.value = true
  loadingEnvVars.value = true
  try {
    const res = await getSubagentEnvVars(currentTenant.value.tenant_id, agent.agent_id)
    if (res.success && res.data) {
      envVarList.value = res.data.map((v: EnvVarItem) => ({
        name: v.var_name,
        value: v.var_value || '',
        description: v.description || '',
      }))
    }
    if (envVarList.value.length === 0) {
      envVarList.value = [{ name: '', value: '', description: '' }]
    }
  } catch (e) {
    console.error('加载环境变量失败:', e)
    envVarList.value = [{ name: '', value: '', description: '' }]
  } finally {
    loadingEnvVars.value = false
  }
}

async function handleSaveEnvVars() {
  if (!currentTenant.value) return
  const vars = envVarList.value.filter(v => v.name.trim())
  const names = vars.map(v => v.name.trim())
  if (new Set(names).size !== names.length) {
    envVarError.value = '变量名不能重复'
    return
  }
  savingEnvVars.value = true
  envVarError.value = ''
  try {
    const res = await setSubagentEnvVars(
      currentTenant.value.tenant_id,
      envVarAgentId.value,
      vars.map(v => ({ name: v.name.trim(), value: v.value, description: v.description || undefined })),
    )
    if (res.success) {
      toast.success('环境变量保存成功')
      showEnvVarDialog.value = false
    } else {
      envVarError.value = res.message || '保存失败'
    }
  } catch (e: any) {
    envVarError.value = e.message || '保存失败'
  } finally {
    savingEnvVars.value = false
  }
}

async function openKnowledgeDialog(agent: AgentItem) {
  if (!currentTenant.value) return
  knowledgeAgentId.value = agent.agent_id
  knowledgeAgentName.value = agent.name
  knowledgeError.value = ''
  knowledgeSelectedTypes.value = []
  showKnowledgeDialog.value = true
  loadingKnowledge.value = true
  try {
    const [catRes, srcRes] = await Promise.all([
      listTenantKnowledgeCategories(currentTenant.value.tenant_id),
      getSubagentKnowledgeSources(currentTenant.value.tenant_id, agent.agent_id),
    ])
    knowledgeCategories.value = catRes.items || []
    if (srcRes.success && srcRes.data) {
      knowledgeSelectedTypes.value = srcRes.data.map((s: KnowledgeSourceItem) => s.source_type)
    }
  } catch (e) {
    console.error('加载知识库关联失败:', e)
  } finally {
    loadingKnowledge.value = false
  }
}

async function handleSaveKnowledge() {
  if (!currentTenant.value) return
  savingKnowledge.value = true
  knowledgeError.value = ''
  try {
    const sources: KnowledgeSourceItem[] = knowledgeSelectedTypes.value.map(st => {
      const cat = knowledgeCategories.value.find(c => c.source_type === st)
      return { source_type: st, display_name: cat?.display_name || st }
    })
    const res = await setSubagentKnowledgeSources(
      currentTenant.value.tenant_id,
      knowledgeAgentId.value,
      sources,
    )
    if (res.success) {
      toast.success('知识库关联保存成功')
      showKnowledgeDialog.value = false
    } else {
      knowledgeError.value = res.error || '保存失败'
    }
  } catch (e: any) {
    knowledgeError.value = e.message || '保存失败'
  } finally {
    savingKnowledge.value = false
  }
}

async function openConfigFileDialog(agent: AgentItem) {
  if (!currentTenant.value) return
  configFileAgentId.value = agent.agent_id
  configFileAgentName.value = agent.name
  configFileError.value = ''
  configFileStatus.value = null
  showConfigFileDialog.value = true
  try {
    const status = await getConfigFileStatus(currentTenant.value.tenant_id, agent.agent_id)
    configFileStatus.value = status as any
  } catch (e) {
    console.error('获取配置文件状态失败:', e)
  }
}

function triggerConfigFileInput() {
  configFileInputRef.value?.click()
}

function handleConfigFileSelect(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) doUploadConfigFile(file)
  input.value = ''
}

function handleConfigFileDrop(event: DragEvent) {
  const file = event.dataTransfer?.files?.[0]
  if (file) doUploadConfigFile(file)
}

async function doUploadConfigFile(file: File) {
  if (!currentTenant.value) return
  if (!file.name.endsWith('.md')) {
    configFileError.value = '仅支持 .md 文件'
    return
  }
  configFileUploading.value = true
  configFileError.value = ''
  try {
    const res = await uploadConfigFile(currentTenant.value.tenant_id, configFileAgentId.value, file)
    if (res.success) {
      toast.success('配置文件上传成功')
      const status = await getConfigFileStatus(currentTenant.value.tenant_id, configFileAgentId.value)
      configFileStatus.value = status as any
    }
  } catch (e: any) {
    configFileError.value = e.message || '上传失败'
  } finally {
    configFileUploading.value = false
  }
}

async function handleDownloadConfigFile() {
  if (!currentTenant.value) return
  try {
    await downloadConfigFile(currentTenant.value.tenant_id, configFileAgentId.value)
  } catch (e: any) {
    toast.error(e.message || '下载失败')
  }
}

async function handleDeleteConfigFile() {
  if (!currentTenant.value) return
  if (!confirm('确定删除配置文件？')) return
  try {
    const res = await deleteConfigFile(currentTenant.value.tenant_id, configFileAgentId.value)
    if (res.success) {
      toast.success('配置文件已删除')
      configFileStatus.value = null
    }
  } catch (e: any) {
    toast.error(e.message || '删除失败')
  }
}

function validateTenantCodeFormat() {
  const code = formData.value.tenant_code || ''
  if (!code) {
    tenantCodeError.value = ''
    return true
  }
  if (!/^[A-Za-z0-9]{4,8}$/.test(code)) {
    tenantCodeError.value = '租户代码必须是4-8位字母数字组合'
    return false
  }
  tenantCodeError.value = ''
  return true
}

async function checkTenantCodeUnique() {
  if (!isEdit.value) {
    const code = (formData.value.tenant_code || '').toUpperCase()
    if (!code || !/^[A-Z0-9]{4,8}$/.test(code)) {
      return
    }
  }
}

async function handleSubmit() {
  if (!formData.value.company_name?.trim()) {
    formError.value = '请填写企业名称'
    return
  }
  if (!isEdit.value && !formData.value.tenant_code?.trim()) {
    formError.value = '请填写租户代码'
    return
  }
  if (!isEdit.value && !validateTenantCodeFormat()) {
    formError.value = '租户代码格式无效'
    return
  }
  submitting.value = true
  formError.value = ''
  try {
    let result
    let createdTenantId = null
    if (isEdit.value && currentTenant.value) {
      result = await updateTenant(currentTenant.value.tenant_id, formData.value)
    } else {
      result = await createTenant(formData.value)
      if (result.success && result.tenant?.tenant_id) {
        createdTenantId = result.tenant.tenant_id
      }
    }
    if (!result.success) {
      formError.value = result.message || result.error || '操作失败'
      submitting.value = false
      return
    }
    const targetTenantId = isEdit.value ? currentTenant.value?.tenant_id : createdTenantId
    if (targetTenantId) {
      await setTenantAgentPermissions(targetTenantId, selectedAgentIds.value)
    }
    if (result.message) {
      toast.success(result.message)
    }
    showFormDialog.value = false
    await clientRefresh()
  } catch (e: any) {
    formError.value = e.message || '操作失败'
  } finally {
    submitting.value = false
  }
}

async function handleDelete(tenant: any) {
  if (!confirm(`确定要删除租户 "${tenant.company_name}" 吗？删除后将无法恢复。`)) return
  try {
    await deleteTenant(tenant.tenant_id)
    await clientRefresh()
  } catch (e: any) {
    toast.error(e.message || '删除失败')
  }
}

function getStatusLabel(status: number | string | undefined): string {
  if (status === undefined || status === null) return '未知'
  const strStatus = String(status)
  const info = TenantStatusMap[strStatus as TenantStatus]
  return info?.label ?? '未知'
}

function getStatusClass(status: number | string | undefined): string {
  if (status === undefined || status === null) return 'bg-gray-100 text-gray-600'
  const strStatus = String(status)
  const info = TenantStatusMap[strStatus as TenantStatus]
  if (info?.color === 'green') return 'bg-success-100 text-success-700'
  if (info?.color === 'red') return 'bg-danger-100 text-danger-700'
  return 'bg-gray-100 text-gray-600'
}

function getTenantUrl(tenantId: string): string {
  return `${window.location.origin}/t/${tenantId}`
}

function getAgentChatUrl(tenantId: string, agentId: string): string {
  if (agentId === 'main') {
    return `${window.location.origin}/t/${tenantId}/chat`
  }
  return `${window.location.origin}/t/${tenantId}/chat/${agentId}`
}

async function copyTenantUrl(tenantId: string) {
  const url = getTenantUrl(tenantId)
  try {
    await navigator.clipboard.writeText(url)
    toast.success('网址已复制到剪贴板')
  } catch (e) {
    const textarea = document.createElement('textarea')
    textarea.value = url
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
    toast.success('网址已复制到剪贴板')
  }
}

onMounted(() => {
  loadAllTenants().then(() => applyFilterAndPagination())
})
</script>
