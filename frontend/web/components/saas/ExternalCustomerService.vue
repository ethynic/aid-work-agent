<template>
  <div class="h-screen flex flex-col bg-gray-50">
    <AppHeader
      title="外部接待客户"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    />

    <!-- 顶部 Tab 切换 -->
    <div class="border-b border-default bg-surface px-6">
      <div class="flex gap-6">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          :class="[
            'py-3 text-sm font-medium border-b-2 transition-colors',
            activeTab === tab.key
              ? 'text-primary-600 border-primary-600'
              : 'text-muted border-transparent hover:text-default hover:border-hover'
          ]"
          @click="switchTab(tab.key)"
        >
          {{ tab.label }}
        </button>
      </div>
    </div>

    <!-- Tab1：客户对话记录 -->
    <div v-show="activeTab === 'chat'" class="flex-1 overflow-hidden flex relative">
      <!-- 左侧：客户列表 -->
      <div class="w-96 border-r border-default bg-surface flex flex-col">
        <!-- 引流员工筛选条件 -->
        <div v-if="referrerFilterName" class="flex items-center gap-2 px-4 py-2 bg-primary-50 border-b border-primary-200 text-sm text-primary-700">
          <span class="flex-1">{{ referrerFilterName }} 的引流客户</span>
          <BaseButton intent="ghost" size="sm" @click="clearReferrerFilter">清除</BaseButton>
        </div>
        <!-- 搜索栏 -->
        <div class="p-4 border-b border-default">
          <div class="flex gap-2">
            <BaseInput
              v-model="searchUsername"
              placeholder="搜索用户名"
              size="sm"
              clearable
              class="w-32"
              @keyup.enter="handleSearch"
            />
            <BaseSelect v-model="selectedKfId" size="sm" class="w-40">
              <option value="">全部客服账号</option>
              <option v-for="kf in kfAccounts" :key="kf.open_kfid" :value="kf.open_kfid">{{ kf.name }}</option>
            </BaseSelect>
            <BaseButton size="sm" @click="handleSearch">搜索</BaseButton>
          </div>
        </div>

        <!-- 客户列表 -->
        <div class="flex-1 overflow-y-auto">
          <div v-if="loadingUsers" class="flex items-center justify-center h-32">
            <span class="text-muted">加载中...</span>
          </div>
          <div v-else-if="userList.length === 0" class="flex items-center justify-center h-32">
            <span class="text-muted">暂无数据</span>
          </div>
          <div v-else>
            <div
              v-for="user in userList"
              :key="`${user.user_id}__${user.channel_type || ''}__${user.channel_chat_id || ''}`"
              class="p-4 border-b border-default cursor-pointer transition-all"
              :class="isSelectedCombo(user) ? 'bg-primary-50 border-l-4 border-l-primary-500' : 'hover:bg-surface-hover'"
              @click="selectUser(user)"
            >
              <div class="flex items-center gap-3">
                <img
                  :src="user.avatar_url || defaultAvatar"
                  class="w-12 h-12 rounded-full object-cover bg-gray-100 ring-2 ring-gray-200"
                  alt="头像"
                />
                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-2">
                    <span class="font-medium text-default truncate">{{ user.nickname || user.username || '未知用户' }}</span>
                    <span v-if="user.channel_type === 'wecom_kf' && user.kf_name" class="text-xs px-2 py-0.5 rounded-full bg-primary-50 text-primary-600 shrink-0 ml-auto">账号：{{ user.kf_name }}</span>
                  </div>
                  <div class="text-xs text-muted mt-1">
                    {{ formatSessionDateRange(user) }}
                  </div>
                  <!-- 引流信息，产品反馈说不重要，先隐藏 -->
                  <!--div v-if="user.referrer_name" class="text-xs text-primary-600 mt-0.5">
                    <div class="relative group inline-flex items-center gap-1">
                      <span class="cursor-default">由 {{ user.referrer_name }} 引流</span>
                      <svg class="w-3.5 h-3.5 text-primary-500 cursor-help shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="10" />
                        <path d="M12 16v-4M12 8h.01" />
                      </svg>
                      <div v-if="user.referral_time" class="hidden group-hover:block absolute left-0 bottom-full mb-1.5 z-10 w-max max-w-64 px-2 py-1.5 bg-gray-800 text-white text-xs rounded shadow-lg whitespace-normal">
                        {{ formatDate(user.referral_time) }} 首次访问 {{ user.referrer_name }} 的客服账号
                      </div>
                    </div>
                  </div-->
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 客户分页 -->
        <div class="p-3 border-t border-default">
          <BasePagination
            :total="userTotal"
            v-model:current-page="userPage"
            :page-size="userPageSize"
            :show-size-changer="false"
          />
        </div>
      </div>

      <!-- 右侧：聊天记录 -->
      <div class="flex-1 flex flex-col overflow-hidden">
        <!-- 聊天记录头部 -->
        <div v-if="selectedUser" class="p-4 border-b border-default bg-surface">
          <div class="flex items-center gap-3">
            <img
              :src="selectedUser.avatar_url || defaultAvatar"
              class="w-10 h-10 rounded-full object-cover bg-gray-100"
              alt="头像"
            />
            <div>
              <div class="flex items-center gap-2">
                <span class="font-medium text-default">{{ selectedUser.nickname || selectedUser.username || '未知用户' }}</span>
                <span v-if="selectedUser.channel_type === 'wecom_kf' && selectedUser.kf_name" class="text-xs px-2 py-0.5 rounded-full bg-primary-50 text-primary-600">账号：{{ selectedUser.kf_name }}</span>
              </div>
              <div class="text-xs text-muted">创建于 {{ formatDate(selectedUser.created_at) }}</div>
            </div>
          </div>
        </div>

        <!-- 消息区域 -->
        <div class="flex-1 overflow-y-auto p-4" ref="messageContainerRef">
          <div v-if="!selectedUserId" class="flex items-center justify-center h-full">
            <div class="text-center">
              <svg class="w-16 h-16 mx-auto text-gray-300 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
              </svg>
              <p class="text-muted">请选择左侧客户查看聊天记录</p>
            </div>
          </div>
          <div v-else-if="loadingMessages" class="flex items-center justify-center h-full">
            <span class="text-muted">加载中...</span>
          </div>
          <div v-else-if="messageList.length === 0" class="flex items-center justify-center h-full">
            <div class="text-center">
              <svg class="w-16 h-16 mx-auto text-gray-300 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
              </svg>
              <p class="text-muted">暂无聊天记录</p>
            </div>
          </div>
          <div v-else class="space-y-4">
            <div
              v-for="msg in visibleMessages"
              :key="msg.message_id"
              class="flex flex-col"
              :class="msg.role === 'user' ? 'items-end' : 'items-start'"
            >
              <!-- 撤回徽章：整条撤回 / 部分撤回 -->
              <div v-if="isRecalled(msg) || isPartiallyRecalled(msg)" class="mb-1 px-1">
                <BaseBadge :intent="isRecalled(msg) ? 'danger' : 'warning'">
                  {{ isRecalled(msg) ? '已撤回' : '部分已撤回' }}
                </BaseBadge>
              </div>
              <div class="max-w-[70%] rounded-2xl px-4 py-2 text-sm"
                :class="[
                  msg.role === 'user'
                    ? 'bg-primary-500 text-white rounded-br-sm'
                    : 'bg-white border border-default text-default rounded-bl-sm shadow-sm',
                  isRecalled(msg) ? 'opacity-60 line-through' : '',
                ]"
              >
                <!-- 用户消息：文本 -->
                <template v-if="msg.role === 'user' && msg.content && !hasUserAttachment(msg)">
                  <div class="whitespace-pre-wrap">{{ msg.content }}</div>
                </template>
                <!-- 用户消息：附件 -->
                <template v-if="msg.role === 'user' && hasUserAttachment(msg)">
                  <!-- 合并后的文本内容：只显示一次（避免每个语音附件都重复渲染） -->
                  <div
                    v-if="msg.content && msg.content !== '[语音消息]' && hasUserVoice(msg)"
                    class="text-xs opacity-80 mb-2 whitespace-pre-wrap"
                  >
                    {{ msg.content }}
                  </div>
                  <!-- 语音：自定义播放按钮（AMR 需前端解码，浏览器原生 <audio> 不支持），横向排列 -->
                  <div v-if="hasUserVoice(msg)" class="flex flex-wrap gap-2">
                    <button
                      v-for="att in getUserVoiceAttachments(msg)"
                      :key="att.media_id"
                      type="button"
                      :disabled="amrPlayer.isLoading(att.media_id)"
                      class="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-sm transition-colors disabled:opacity-60"
                      :class="amrPlayer.isPlaying(att.media_id)
                        ? 'bg-primary-500 text-white hover:bg-primary-600'
                        : 'bg-gray-100 text-default hover:bg-gray-200'"
                      @click="onPlayVoice(att)"
                    >
                      <span v-if="amrPlayer.isLoading(att.media_id)">加载中…</span>
                      <template v-else>
                        <span class="text-base leading-none">{{ amrPlayer.isPlaying(att.media_id) ? '⏸' : '▶' }}</span>
                        <span>{{ amrPlayer.isPlaying(att.media_id) ? '正在播放' : '点击播放' }}</span>
                        <span v-if="att.duration" class="text-xs opacity-80">{{ att.duration }}"</span>
                      </template>
                    </button>
                  </div>
                  <!-- 图片：预览 -->
                  <template v-for="att in getUserAttachments(msg)" :key="'img-' + att.media_id">
                    <div v-if="att.type === 'image'" class="space-y-1">
                      <img :src="getAttachmentDownloadUrl(att)" class="max-w-[240px] max-h-[240px] rounded-lg cursor-pointer" @click="previewImage(getAttachmentDownloadUrl(att))" alt="用户图片" />
                      <div class="text-xs opacity-80">{{ att.file_name }} · {{ formatFileSize(att.file_size) }}</div>
                    </div>
                  </template>
                  <!-- 视频：播放器 -->
                  <template v-for="att in getUserAttachments(msg)" :key="'vid-' + att.media_id">
                    <div v-if="att.type === 'video'" class="space-y-1">
                      <video controls :src="getAttachmentDownloadUrl(att)" class="max-w-[320px] max-h-[240px] rounded-lg" preload="metadata"></video>
                      <div class="text-xs opacity-80">{{ att.file_name }} · {{ formatFileSize(att.file_size) }}</div>
                    </div>
                  </template>
                  <!-- 文件：下载卡片 -->
                  <template v-for="att in getUserAttachments(msg)" :key="'file-' + att.media_id">
                    <div v-if="att.type === 'file'" class="mt-3">
                      <AttachmentCard :attachment="att" :download-url="getAttachmentDownloadUrl(att)" />
                    </div>
                  </template>
                </template>
                <!-- 用户消息：无内容也无附件 -->
                <template v-if="msg.role === 'user' && !msg.content && !hasUserAttachment(msg)">
                  <div class="text-xs opacity-80 italic">[非文本消息]</div>
                </template>
                <!-- AI 消息：文本 -->
                <template v-if="msg.role === 'assistant' && msg.content">
                  <div class="whitespace-pre-wrap">{{ msg.content }}</div>
                </template>
                <!-- AI 消息：可下载文件 -->
                <div v-if="msg.role === 'assistant' && getDownloadableFiles(msg).length > 0" class="mt-3 flex flex-wrap gap-2">
                  <DownloadFileCard
                    v-for="file in getDownloadableFiles(msg)"
                    :key="file.file_id"
                    :file="file"
                  />
                </div>
              </div>
              <div class="text-xs text-muted mt-1 px-1">
                {{ formatTime(msg.created_at) }}
              </div>
            </div>
          </div>
        </div>

        <!-- 消息分页 -->
        <div v-if="selectedUserId" class="p-3 border-t border-default bg-surface">
          <BasePagination
            :total="messageTotal"
            v-model:current-page="messagePage"
            :page-size="messagePageSize"
            :show-size-changer="false"
          />
        </div>
      </div>

      <!-- 附件预览面板：点击 DownloadFileCard 中的 PDF/HTML/图片/Markdown 会触发 -->
      <Transition name="slide">
        <div
          v-if="isPreviewOpen"
          class="absolute inset-y-0 right-0 z-50 w-full md:static md:w-auto md:z-auto md:flex-shrink-0"
        >
          <AttachmentPreviewPanel
            :attachment="previewAttachment"
            @close="closePreview"
          />
        </div>
      </Transition>
    </div>

    <!-- Tab2：引流统计 -->
    <div v-show="activeTab === 'stats'" class="flex-1 overflow-y-auto p-6 bg-canvas">
      <div v-if="statsLoading" class="text-center py-12 text-muted">加载中...</div>
      <template v-else>
        <!-- 日期段选择 -->
        <div class="flex flex-wrap items-center gap-4 mb-6 bg-surface border border-default rounded-lg p-4">
          <div class="flex gap-2">
            <BaseButton
              v-for="p in rangePresets"
              :key="p.key"
              :intent="activeRange === p.key ? 'primary' : 'ghost'"
              size="sm"
              @click="selectRange(p.key)"
            >
              {{ p.label }}
            </BaseButton>
          </div>
          <div v-if="activeRange === 'custom'" class="flex items-center gap-2">
            <input
              type="date"
              v-model="customStartDate"
              class="block h-8 px-2 rounded-lg border border-default bg-surface text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
            <span class="text-muted text-sm">至</span>
            <input
              type="date"
              v-model="customEndDate"
              class="block h-8 px-2 rounded-lg border border-default bg-surface text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
            <BaseButton size="sm" @click="applyCustomRange">查询</BaseButton>
          </div>
          <span class="ml-auto text-xs text-muted">过滤基准：客户扫码引流发生时间</span>
        </div>

        <!-- 统计卡片 -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
          <div class="bg-surface border border-default rounded-lg p-5">
            <div class="text-muted text-sm">总引流数</div>
            <div class="text-3xl font-bold text-default mt-1">{{ stats.total_referrals ?? 0 }}</div>
          </div>
          <div class="bg-surface border border-default rounded-lg p-5">
            <div class="text-muted text-sm">总对话消息数</div>
            <div class="text-3xl font-bold text-default mt-1">{{ stats.total_messages ?? 0 }}</div>
          </div>
        </div>

        <!-- 员工维度表格 -->
        <div class="bg-surface border border-default rounded-lg">
          <div class="px-5 py-3 border-b border-default font-medium text-default">员工引流统计</div>
          <div class="table-scroll-wrapper">
            <BaseTable :columns="referrerColumns" :data="stats.referrers || []" row-key="referrer_user_id">
              <template #referrer_name="{ row }">
                <span>{{ row.referrer_name || '已删除员工' }}</span>
              </template>
              <template #referral_count="{ row }">
                <BaseButton intent="ghost" size="sm" @click="drillIntoReferrer(row)">{{ row.referral_count }}</BaseButton>
              </template>
              <template #ratio="{ row }">{{ formatRatio(row.ratio) }}</template>
              <template #empty>暂无数据</template>
            </BaseTable>
          </div>
        </div>
      </template>
    </div>

    <!-- Tab3：留资线索 -->
    <div v-show="activeTab === 'leads'" class="flex-1 overflow-y-auto p-6 bg-canvas">
      <div v-if="leadsLoading" class="text-center py-12 text-muted">加载中...</div>
      <template v-else>
        <!-- 日期段选择 -->
        <div class="flex flex-wrap items-center gap-4 mb-6 bg-surface border border-default rounded-lg p-4">
          <div class="flex gap-2">
            <BaseButton
              v-for="p in rangePresets"
              :key="p.key"
              :intent="leadActiveRange === p.key ? 'primary' : 'ghost'"
              size="sm"
              @click="selectLeadRange(p.key)"
            >
              {{ p.label }}
            </BaseButton>
          </div>
          <div v-if="leadActiveRange === 'custom'" class="flex items-center gap-2">
            <input
              type="date"
              v-model="leadCustomStartDate"
              class="block h-8 px-2 rounded-lg border border-default bg-surface text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
            <span class="text-muted text-sm">至</span>
            <input
              type="date"
              v-model="leadCustomEndDate"
              class="block h-8 px-2 rounded-lg border border-default bg-surface text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
            <BaseButton size="sm" @click="applyLeadCustomRange">查询</BaseButton>
          </div>
          <span class="ml-auto text-xs text-muted">过滤基准：留资时间（created_at）</span>
        </div>

        <!-- 统计卡片 -->
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
          <div class="bg-surface border border-default rounded-lg p-5">
            <div class="text-muted text-sm">总留资数</div>
            <div class="text-3xl font-bold text-default mt-1">{{ leadStats.total_leads ?? 0 }}</div>
          </div>
          <div
            v-for="m in leadStats.by_contact_method || []"
            :key="m.contact_method"
            class="bg-surface border border-default rounded-lg p-5"
          >
            <div class="text-muted text-sm">{{ getMethodLabel(m.contact_method) }}</div>
            <div class="text-3xl font-bold text-default mt-1">{{ m.count }}</div>
            <div class="text-xs text-muted mt-1">占比 {{ formatRatio(m.ratio) }}</div>
          </div>
        </div>

        <!-- 按客服账号统计 -->
        <div class="bg-surface border border-default rounded-lg mb-6">
          <div class="px-5 py-3 border-b border-default font-medium text-default">按客服账号</div>
          <div class="table-scroll-wrapper">
            <BaseTable :columns="leadKfColumns" :data="leadStats.by_kf_account || []" row-key="channel_chat_id">
              <template #kf_account_name="{ row }">
                <span>{{ row.kf_account_name || row.channel_chat_id || '-' }}</span>
              </template>
              <template #ratio="{ row }">{{ formatRatio(row.ratio) }}</template>
              <template #empty>暂无数据</template>
            </BaseTable>
          </div>
        </div>

        <!-- 线索列表 -->
        <div class="bg-surface border border-default rounded-lg">
          <div class="px-5 py-3 border-b border-default flex flex-wrap items-center gap-3">
            <span class="font-medium text-default">留资线索</span>
            <div class="ml-auto flex items-center gap-2">
              <BaseSelect v-model="leadKfFilter" size="sm" class="w-40">
                <option value="">全部客服账号</option>
                <option v-for="kf in kfAccounts" :key="kf.open_kfid" :value="kf.open_kfid">{{ kf.name }}</option>
              </BaseSelect>
              <BaseSelect v-model="leadStageFilter" size="sm" class="w-32">
                <option value="">全部阶段</option>
                <option v-for="(s, key) in leadStageOptions" :key="key" :value="key">{{ s.label }}</option>
              </BaseSelect>
            </div>
          </div>
          <div class="table-scroll-wrapper">
            <BaseTable :columns="leadColumns" :data="leadList" row-key="lead_id">
              <template #seq="{ index }">{{ seqNumber(index) }}</template>
              <template #contact_method="{ row }">
                <BaseBadge :intent="row.contact_method === 'qr' ? 'info' : 'neutral'">{{ getMethodLabel(row.contact_method) }}</BaseBadge>
              </template>
              <template #phone="{ row }">
                <span :class="row.phone ? '' : 'text-muted'">{{ row.phone || '—' }}</span>
              </template>
              <template #stage="{ row }">
                <BaseBadge :intent="getStageInfo(row.stage).intent">{{ getStageInfo(row.stage).label }}</BaseBadge>
              </template>
              <template #created_at="{ row }">{{ formatDate(row.created_at) }}</template>
              <template #actions="{ row }">
                <BaseButton intent="ghost" size="sm" @click="openLeadDetail(row)">详情</BaseButton>
              </template>
              <template #empty>暂无留资线索</template>
            </BaseTable>
          </div>
          <div class="p-3 border-t border-default">
            <BasePagination
              :total="leadTotal"
              v-model:current-page="leadPage"
              :page-size="leadPageSize"
              :show-size-changer="false"
            />
          </div>
        </div>
      </template>
    </div>

    <!-- 留资线索详情弹框 -->
    <BaseModal v-model="leadDetailOpen" title="留资线索详情" size="md">
      <div v-if="leadDetail" class="space-y-3 text-sm">
        <div class="grid grid-cols-2 gap-4">
          <div>
            <div class="text-muted text-xs mb-1">客户姓名</div>
            <div class="text-default">{{ leadDetail.contact_name || '-' }}</div>
          </div>
          <div>
            <div class="text-muted text-xs mb-1">手机号</div>
            <div class="text-default">{{ leadDetail.phone || '-' }}</div>
          </div>
          <div>
            <div class="text-muted text-xs mb-1">留资方式</div>
            <div class="text-default">{{ getMethodLabel(leadDetail.contact_method) }}</div>
          </div>
          <div>
            <div class="text-muted text-xs mb-1">阶段</div>
            <div class="text-default">{{ getStageInfo(leadDetail.stage).label }}</div>
          </div>
          <div>
            <div class="text-muted text-xs mb-1">客服账号</div>
            <div class="text-default">{{ leadDetail.kf_account_name || '-' }}</div>
          </div>
          <div>
            <div class="text-muted text-xs mb-1">归属员工</div>
            <div class="text-default">{{ leadDetail.assignee_name || '-' }}</div>
          </div>
          <div class="col-span-2">
            <div class="text-muted text-xs mb-1">留资时间</div>
            <div class="text-default">{{ formatDate(leadDetail.created_at) }}</div>
          </div>
          <div class="col-span-2">
            <div class="text-muted text-xs mb-1">需求摘要</div>
            <div class="text-default whitespace-pre-wrap">{{ leadDetail.demand_summary || '-' }}</div>
          </div>
          <div class="col-span-2 flex items-center gap-2 pt-3 border-t border-default">
            <span class="text-muted text-xs">更新阶段</span>
            <BaseSelect v-model="leadDetailStage" size="sm" class="w-32">
              <option v-for="(s, key) in leadStageOptions" :key="key" :value="key">{{ s.label }}</option>
            </BaseSelect>
            <BaseButton size="sm" @click="saveLeadStage">保存</BaseButton>
          </div>
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="leadDetailOpen = false">关闭</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch, nextTick, inject } from 'vue'
import AppHeader from '@/components/AppHeader.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BasePagination from '@/components/ui/BasePagination.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import DownloadFileCard from '@/components/DownloadFileCard.vue'
import AttachmentPreviewPanel from '@/components/AttachmentPreviewPanel.vue'
import { listExternalUsers, getUserSessions, getSessionMessages, getReferralStats, listKfAccounts, getLeadStats, listLeads, getLeadDetail, updateLeadStage } from '@/api/externalCustomers'
import AttachmentCard from './AttachmentCard.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { useAmrPlayer } from '@/composables/useAmrPlayer'
// import { getUserSourceInfo } from '@/api/enums'
import { formatFileSize } from '@/utils/file'
import type { DownloadableFile } from '@/types'
import { useToast } from 'vue-toastification'
import { useAttachmentPreview } from '@/composables/useAttachmentPreview'

const toast = useToast()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, init, isInitialized, logout: tenantLogout } = useTenantAuth()
const amrPlayer = useAmrPlayer()
const { previewAttachment, isPreviewOpen, closePreview } = useAttachmentPreview()
const toggleSidebarFn = inject<() => void>('toggleSidebar')
const messageContainerRef = ref<HTMLElement | null>(null)

// 响应式状态
const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)
const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone
  } : null
})

// 顶部 Tab 切换
const tabs = [
  { key: 'chat', label: '客户对话记录' },
  { key: 'stats', label: '引流统计' },
  { key: 'leads', label: '留资线索' },
]
const activeTab = ref<'chat' | 'stats' | 'leads'>('chat')

// 引流统计（Tab2）
const stats = ref<{ total_referrals: number; total_messages: number; referrers: any[] }>({ total_referrals: 0, total_messages: 0, referrers: [] })
const statsLoading = ref(false)
const activeRange = ref<'7d' | '30d' | 'custom'>('30d')
const customStartDate = ref('')
const customEndDate = ref('')
const rangePresets = [
  { key: '7d', label: '近7天' },
  { key: '30d', label: '近30天' },
  { key: 'custom', label: '自定义' },
]
const referrerColumns = [
  { key: 'referrer_name', label: '员工', tooltip: (row: any) => row.referrer_name || '已删除员工' },
  { key: 'referral_count', label: '引流客户数' },
  { key: 'ratio', label: '占比' },
]

// 引流员工筛选（Tab1 客户列表按员工过滤，来自引流统计下钻）
const referrerFilterUserId = ref('')
const referrerFilterName = ref('')

// 搜索条件
const searchUsername = ref('')

// 客服账号筛选（下拉框）：管理员可见全部，普通用户由后端只返回自己负责的账号
const kfAccounts = ref<Array<{ open_kfid: string; name: string }>>([])
const selectedKfId = ref('')

// 客户列表
const userList = ref<any[]>([])
const userTotal = ref(0)
const userPage = ref(1)
const userPageSize = ref(20)
const loadingUsers = ref(false)

// 消息列表
const messageList = ref<any[]>([])
const messageTotal = ref(0)
const messagePage = ref(1)
const messagePageSize = ref(50)
const loadingMessages = ref(false)

// 过滤掉智能体中间消息（tool 角色、内容为空且仅含 tool_calls 的 assistant），只展示对用户可见的对话
const visibleMessages = computed(() =>
  messageList.value.filter(msg => {
    if (msg.role === 'tool') return false
    if (msg.role === 'assistant' && !msg.content) return false
    return true
  }),
)

// 撤回状态判断
// - 整条撤回：is_recalled === true
// - 部分撤回：metadata.recalled_part_msgids 非空（合并消息部分段被撤回，content 已重建）
const isRecalled = (msg: any) => msg.is_recalled === true
const isPartiallyRecalled = (msg: any) => {
  const recalled = msg.metadata?.recalled_part_msgids
  return Array.isArray(recalled) && recalled.length > 0
}

// 会话列表
const sessionList = ref<any[]>([])
const selectedSessionId = ref('')

// 选中状态（组合粒度：客户 × 渠道类型 × 客服账号）
const selectedUserId = ref('')
const selectedChannelChatId = ref('')
const selectedChannelType = ref('')
const selectedUser = ref<any>(null)

// 默认头像
const defaultAvatar = 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMjAiIGhlaWdodD0iMTIwIiB2aWV3Qm94PSIwIDAgMTIwIDEyMCI+PGNpcmNsZSBjeD0iNjAiIGN5PSI2MCIgcj0iNjAiIGZpbGw9IiMwN0MxNjAiLz48Y2lyY2xlIGN4PSI2MCIgY3k9IjQ0IiByPSIxNiIgZmlsbD0iI2ZmZiIvPjxlbGxpcHNlIGN4PSI2MCIgY3k9Ijg2IiByeD0iMjgiIHJ5PSIyMiIgZmlsbD0iI2ZmZiIvPjwvc3ZnPg=='

// 方法
function handleToggleSidebar() {
  if (toggleSidebarFn) toggleSidebarFn()
}

async function handleLogout() {
  await tenantLogout()
  const tenantRoot = window.location.pathname.match(/^\/t\/[^/]+/)?.[0]
  window.location.href = tenantRoot ? `${tenantRoot}/login` : '/portal/login'
}

async function handleSearch() {
  userPage.value = 1
  await loadUsers()
}

async function loadUsers() {
  loadingUsers.value = true
  try {
    const res = await listExternalUsers({
      username: searchUsername.value || undefined,
      referrer_user_id: referrerFilterUserId.value || undefined,
      channel_chat_id: selectedKfId.value || undefined,
      page: userPage.value,
      page_size: userPageSize.value,
    })
    if (res.success) {
      userList.value = res.users || []
      userTotal.value = res.total || 0

      // 默认选中第一个客户
      if (userList.value.length > 0 && !selectedUserId.value) {
        await selectUser(userList.value[0])
      }
    } else {
      toast.error(res.message || '获取客户列表失败')
    }
  } catch (e: any) {
    toast.error(e.message || '获取客户列表失败')
  } finally {
    loadingUsers.value = false
  }
}

async function loadKfAccounts() {
  try {
    const res = await listKfAccounts()
    if (res.success) {
      kfAccounts.value = res.kf_accounts || []
    }
  } catch (e: any) {
    toast.error(e.message || '获取客服账号列表失败')
  }
}

// 判断列表行是否为当前选中的组合（客户 × 渠道 × 客服账号 三元匹配）
function isSelectedCombo(user: any): boolean {
  return (
    selectedUserId.value === user.user_id &&
    selectedChannelChatId.value === (user.channel_chat_id || '') &&
    selectedChannelType.value === (user.channel_type || '')
  )
}

async function selectUser(user: any) {
  selectedUserId.value = user.user_id
  selectedChannelChatId.value = user.channel_chat_id || ''
  selectedChannelType.value = user.channel_type || ''
  selectedUser.value = user
  messageList.value = []
  messageTotal.value = 0
  messagePage.value = 1
  sessionList.value = []
  selectedSessionId.value = ''
  amrPlayer.stopAll()
  await loadUserSessions()
}

async function loadUserSessions() {
  if (!selectedUserId.value) return
  try {
    // 组合粒度：仅 wecom_kf 按客服账号（channel_chat_id）拆分过滤；
    // 其它渠道组合为「客户 × 渠道」折叠行，channel_chat_id 不传，避免 NULL-or-empty
    // 过滤漏掉非空 channel_chat_id（如 wecom_personal_rpa 的 conversation_id）的会话。
    const isKfCombo = selectedChannelType.value === 'wecom_kf'
    const res = await getUserSessions({
      user_id: selectedUserId.value,
      channel_type: selectedChannelType.value || undefined,
      channel_chat_id: isKfCombo ? selectedChannelChatId.value : undefined,
      page: 1,
      page_size: 100,
    })
    if (res.success) {
      sessionList.value = res.sessions || []
      // 默认选中第一个会话
      if (sessionList.value.length > 0) {
        selectedSessionId.value = sessionList.value[0].session_id
        await loadSessionMessages()
      }
    } else {
      toast.error(res.message || '获取会话列表失败')
    }
  } catch (e: any) {
    toast.error(e.message || '获取会话列表失败')
  }
}

async function loadSessionMessages() {
  if (!selectedSessionId.value) return
  loadingMessages.value = true
  try {
    const res = await getSessionMessages({
      session_id: selectedSessionId.value,
      page: messagePage.value,
      page_size: messagePageSize.value,
    })
    if (res.success) {
      messageList.value = res.messages || []
      messageTotal.value = res.total || 0
      // 滚动到顶部
      nextTick(() => {
        if (messageContainerRef.value) {
          messageContainerRef.value.scrollTop = 0
        }
      })
    } else {
      toast.error(res.message || '获取聊天记录失败')
    }
  } catch (e: any) {
    toast.error(e.message || '获取聊天记录失败')
  } finally {
    loadingMessages.value = false
  }
}

// ==================== 引流统计（Tab2） ====================

function switchTab(key: string) {
  activeTab.value = key as 'chat' | 'stats' | 'leads'
  if (key === 'stats') {
    loadReferralStats()
  } else if (key === 'leads') {
    loadLeadStats()
    loadLeads()
  }
}

function dateStr(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function computeRangeDates(key: '7d' | '30d') {
  const today = new Date()
  const start = new Date(today)
  if (key === '7d') start.setDate(today.getDate() - 6)
  else start.setDate(today.getDate() - 29)
  return { start_date: dateStr(start), end_date: dateStr(today) }
}

function selectRange(key: string) {
  activeRange.value = key as '7d' | '30d' | 'custom'
  if (key === 'custom') return
  loadReferralStats()
}

function applyCustomRange() {
  if (!customStartDate.value || !customEndDate.value) {
    toast.error('请选择起止日期')
    return
  }
  if (customStartDate.value > customEndDate.value) {
    toast.error('起始日期不能晚于结束日期')
    return
  }
  loadReferralStats()
}

async function loadReferralStats() {
  statsLoading.value = true
  try {
    let params: { start_date?: string; end_date?: string } = {}
    if (activeRange.value === 'custom') {
      params = { start_date: customStartDate.value || undefined, end_date: customEndDate.value || undefined }
    } else {
      params = computeRangeDates(activeRange.value)
    }
    const res = await getReferralStats(params)
    if (res.success) {
      stats.value = {
        total_referrals: res.total_referrals || 0,
        total_messages: res.total_messages || 0,
        referrers: res.referrers || [],
      }
    } else {
      toast.error(res.message || '获取引流统计失败')
    }
  } catch (e: any) {
    toast.error(e.message || '获取引流统计失败')
  } finally {
    statsLoading.value = false
  }
}

function formatRatio(ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined) return '-'
  return `${ratio}%`
}

// 下钻：点击员工行 → 切回客户对话记录 Tab，客户列表按该员工过滤
function drillIntoReferrer(row: any) {
  referrerFilterUserId.value = row.referrer_user_id || ''
  referrerFilterName.value = row.referrer_name || '该员工'
  userPage.value = 1
  selectedUserId.value = ''
  selectedChannelChatId.value = ''
  selectedChannelType.value = ''
  selectedUser.value = null
  sessionList.value = []
  selectedSessionId.value = ''
  messageList.value = []
  messageTotal.value = 0
  messagePage.value = 1
  activeTab.value = 'chat'
  loadUsers()
}

function clearReferrerFilter() {
  referrerFilterUserId.value = ''
  referrerFilterName.value = ''
  userPage.value = 1
  loadUsers()
}

// ==================== 留资线索（Tab3） ====================

// 留资方式与阶段展示映射（与后端 LEAD_STAGES 保持一致）
const leadStageOptions: Record<string, { label: string; intent: any }> = {
  new: { label: '待跟进', intent: 'info' },
  contacting: { label: '跟进中', intent: 'warning' },
  converted: { label: '已转化', intent: 'success' },
  abandoned: { label: '已放弃', intent: 'neutral' },
}
function getStageInfo(stage: string | null | undefined): { label: string; intent: any } {
  return leadStageOptions[stage || ''] || { label: stage || '未知', intent: 'neutral' }
}
function getMethodLabel(method: string | null | undefined): string {
  if (method === 'phone') return '手机号'
  if (method === 'qr') return '顾问微信'
  return method || '-'
}

const leadsLoading = ref(false)
const leadStats = ref<{ total_leads: number; by_contact_method: any[]; by_kf_account: any[] }>({
  total_leads: 0,
  by_contact_method: [],
  by_kf_account: [],
})
const leadActiveRange = ref<'7d' | '30d' | 'custom'>('30d')
const leadCustomStartDate = ref('')
const leadCustomEndDate = ref('')
const leadKfFilter = ref('')
const leadStageFilter = ref('')

const leadList = ref<any[]>([])
const leadTotal = ref(0)
const leadPage = ref(1)
const leadPageSize = ref(20)

const leadDetail = ref<any>(null)
const leadDetailOpen = ref(false)
const leadDetailStage = ref('')

function leadRangeParams(): { start_date?: string; end_date?: string } {
  if (leadActiveRange.value === 'custom') {
    return { start_date: leadCustomStartDate.value || undefined, end_date: leadCustomEndDate.value || undefined }
  }
  return computeRangeDates(leadActiveRange.value)
}

function selectLeadRange(key: string) {
  leadActiveRange.value = key as '7d' | '30d' | 'custom'
  if (key === 'custom') return
  leadPage.value = 1
  loadLeadStats()
  loadLeads()
}

function applyLeadCustomRange() {
  if (!leadCustomStartDate.value || !leadCustomEndDate.value) {
    toast.error('请选择起止日期')
    return
  }
  if (leadCustomStartDate.value > leadCustomEndDate.value) {
    toast.error('起始日期不能晚于结束日期')
    return
  }
  leadPage.value = 1
  loadLeadStats()
  loadLeads()
}

async function loadLeadStats() {
  try {
    const res = await getLeadStats(leadRangeParams())
    if (res.success) {
      leadStats.value = {
        total_leads: res.total_leads || 0,
        by_contact_method: res.by_contact_method || [],
        by_kf_account: res.by_kf_account || [],
      }
    } else {
      toast.error(res.message || '获取留资统计失败')
    }
  } catch (e: any) {
    toast.error(e.message || '获取留资统计失败')
  }
}

async function loadLeads() {
  leadsLoading.value = true
  try {
    const res = await listLeads({
      ...leadRangeParams(),
      channel_chat_id: leadKfFilter.value || undefined,
      stage: leadStageFilter.value || undefined,
      page: leadPage.value,
      page_size: leadPageSize.value,
    })
    if (res.success) {
      leadList.value = res.leads || []
      leadTotal.value = res.total || 0
    } else {
      toast.error(res.message || '获取留资线索列表失败')
    }
  } catch (e: any) {
    toast.error(e.message || '获取留资线索列表失败')
  } finally {
    leadsLoading.value = false
  }
}

function seqNumber(index: number): number {
  return (leadPage.value - 1) * leadPageSize.value + index + 1
}

async function openLeadDetail(row: any) {
  try {
    const res = await getLeadDetail(row.lead_id)
    if (res.success) {
      leadDetail.value = res.lead
      leadDetailStage.value = res.lead?.stage || ''
      leadDetailOpen.value = true
    } else {
      toast.error(res.message || '获取线索详情失败')
    }
  } catch (e: any) {
    toast.error(e.message || '获取线索详情失败')
  }
}

async function saveLeadStage() {
  if (!leadDetail.value) return
  if (leadDetailStage.value === leadDetail.value.stage) return
  try {
    const res = await updateLeadStage(leadDetail.value.lead_id, leadDetailStage.value)
    if (res.success) {
      toast.success('线索阶段已更新')
      leadDetailOpen.value = false
      loadLeads()
      loadLeadStats()
    } else {
      toast.error(res.message || '更新线索阶段失败')
    }
  } catch (e: any) {
    toast.error(e.message || '更新线索阶段失败')
  }
}

const leadColumns = [
  { key: 'seq', label: '序号', width: '60px' },
  { key: 'contact_name', label: '客户姓名' },
  { key: 'phone', label: '手机号' },
  { key: 'contact_method', label: '留资方式' },
  { key: 'kf_account_name', label: '客服账号', tooltip: (row: any) => row.kf_account_name || row.channel_chat_id || '-' },
  { key: 'assignee_name', label: '归属员工', tooltip: (row: any) => row.assignee_name || '-' },
  { key: 'stage', label: '阶段' },
  { key: 'created_at', label: '留资时间' },
  { key: 'actions', label: '操作' },
]
const leadKfColumns = [
  { key: 'kf_account_name', label: '客服账号', tooltip: (row: any) => row.kf_account_name || row.channel_chat_id || '-' },
  { key: 'count', label: '留资数' },
  { key: 'ratio', label: '占比' },
]

// 筛选变更 → 重置到第一页重新加载
watch([leadKfFilter, leadStageFilter], () => {
  leadPage.value = 1
  loadLeads()
})
watch(leadPage, () => loadLeads())

function formatDate(dateStr: string | null): string {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  const datePart = date.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
  const timePart = date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  })
  return `${datePart} ${timePart}`
}

/**
 * 格式化会话日期区间
 *
 * 规则：
 *  - 同时有首次会话时间（first_session_at）与最近会话时间（last_session_at）：
 *      "[创建日期] ~ [更新日期]"
 *  - 只有最近会话时间：仅显示更新时间
 *  - 都没有：返回空字符串
 *
 * 后端 UserDB.list_external_users 在 channel_sessions 上做了 MIN(created_at) /
 * MAX(updated_at) 聚合，因此 first_session_at / last_session_at 字段含义即
 * "该用户最早一次渠道会话的创建时间"和"该用户最近一次渠道会话的更新时间"。
 */
function formatSessionDateRange(user: any): string {
  const first = user?.first_session_at
  const last = user?.last_session_at

  if (first && last) {
    return `${formatDate(first)} ~ ${formatDate(last)}`
  }
  if (last) {
    return formatDate(last)
  }
  if (first) {
    return formatDate(first)
  }
  return ''
}

function formatTime(timeStr: string | null): string {
  if (!timeStr) return ''
  const date = new Date(timeStr)
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function getDownloadableFiles(msg: any): DownloadableFile[] {
  return msg?.metadata?.downloadableFiles || []
}

/**
 * 获取用户消息的附件列表
 */
function getUserAttachments(msg: any): any[] {
  return msg?.attachments || []
}

/**
 * 判断用户消息是否有附件
 */
function hasUserAttachment(msg: any): boolean {
  return getUserAttachments(msg).length > 0
}

/**
 * 获取用户消息中的语音附件列表
 */
function getUserVoiceAttachments(msg: any): any[] {
  return getUserAttachments(msg).filter(att => att.type === 'voice')
}

/**
 * 判断用户消息是否包含语音附件
 */
function hasUserVoice(msg: any): boolean {
  return getUserVoiceAttachments(msg).length > 0
}

/**
 * 构建附件下载 URL
 */
function getAttachmentDownloadUrl(att: any): string {
  if (!att.local_path || !selectedSessionId.value) return ''
  // local_path 格式: data/attachments/{session_id}/{filename}
  const parts = att.local_path.split('/')
  const filename = parts[parts.length - 1]
  const params = new URLSearchParams({
    session_id: selectedSessionId.value,
    filename,
  })
  return `/api/saas/external-customers/attachments/download?${params.toString()}`
}

/**
 * 播放/暂停语音（前端解码 AMR）
 */
async function onPlayVoice(att: any) {
  const url = getAttachmentDownloadUrl(att)
  if (!url) return
  try {
    await amrPlayer.toggle(att.media_id, url)
  } catch (e: any) {
    toast.error(`语音播放失败: ${e?.message || e}`)
  }
}

/**
 * 图片预览（新窗口打开）
 */
function previewImage(url: string) {
  window.open(url, '_blank')
}

// 监听分页
watch(userPage, () => loadUsers())
watch(messagePage, () => {
  if (selectedSessionId.value) {
    loadSessionMessages()
  }
})

// 客服账号下拉框切换 → 重新加载客户列表
watch(selectedKfId, () => handleSearch())

// 初始化
onMounted(async () => {
  if (!isInitialized.value) {
    await init()
  }
  await loadKfAccounts()
  await loadUsers()
})
</script>

<style scoped>
.slide-enter-active,
.slide-leave-active {
  transition: all 0.3s ease;
}
.slide-enter-from,
.slide-leave-to {
  transform: translateX(100%);
  opacity: 0;
}
</style>
