<template>
  <div class="h-screen flex flex-col bg-canvas">
    <AppHeader title="企业微信代理任务" :is-logged-in="effectiveIsLoggedIn" :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar" @logout="tenantLogout">
      <template #menu-items="{ closeMenu }">
        <button class="w-full text-left px-4 py-2 text-sm text-default hover:bg-surface-hover" @click="goCenter(); closeMenu()">会话记录</button>
        <button class="w-full text-left px-4 py-2 text-sm text-default hover:bg-surface-hover" @click="reload(); closeMenu()">刷新</button>
      </template>
    </AppHeader>

    <main class="flex-1 overflow-y-auto p-4 md:p-6">
      <div class="max-w-6xl mx-auto space-y-4">
        <div>
          <p class="text-xs text-muted mb-1">工作台 / 代理任务 / 企业微信</p>
          <h1 class="text-xl font-semibold text-default m-0">企业微信代理任务</h1>
        </div>

        <!-- ==================== 列表视图 ==================== -->
        <div v-if="!detailId" class="bg-surface border border-default rounded-xl overflow-hidden">
          <div class="px-4 py-3 border-b border-default flex items-center gap-3 flex-wrap">
            <label class="text-xs text-muted flex items-center gap-2 cursor-pointer">
              <input type="checkbox" :checked="includeDeleted" data-testid="include-deleted" @change="toggleIncludeDeleted" />
              显示已删除任务（只读）
            </label>
            <div class="flex-1" />
            <BaseButton intent="primary" data-testid="create-proxy" @click="openCreate">＋ 新建代理任务</BaseButton>
          </div>

          <p v-if="listError" role="alert" class="mx-4 mt-3 text-sm text-danger-700">{{ listError }}</p>
          <p v-if="actionError" role="alert" class="mx-4 mt-3 text-sm text-danger-700">{{ actionError }}</p>

          <div class="table-scroll-wrapper">
            <BaseTable :columns="taskColumns" :data="tasks" row-key="id">
              <template #status="{ row }">
                <BaseBadge :intent="statusBadge(row as WecomProxyTask).intent">{{ statusBadge(row as WecomProxyTask).label }}</BaseBadge>
              </template>
              <template #title="{ row }">
                <span class="text-sm font-medium text-default">{{ row.title }}</span>
                <span v-if="row.stop_requested && row.status === 'running'" class="text-xs text-warning-700 ml-2">停止中：等待当前批次结束</span>
              </template>
              <template #active_target_count="{ row }">{{ row.active_target_count ?? 0 }}</template>
              <template #agent_id="{ row }">
                <span class="text-xs text-muted break-all">{{ agentName(row.agent_id) }}</span>
              </template>
              <template #last_checked_at="{ row }">
                <span v-if="row.last_checked_at">{{ formatTime(row.last_checked_at) }}</span>
                <span v-else class="text-xs text-muted">—</span>
              </template>
              <template #latest_seen_msg_id="{ row }">{{ row.latest_seen_msg_id ?? '—' }}</template>
              <template #idle_read_count="{ row }">{{ row.idle_read_count ?? 0 }}</template>
              <template #credit_cost="{ row }">{{ formatCredit(row.credit_cost) }}</template>
              <template #last_error="{ row }">
                <span v-if="row.last_error" class="block max-w-36 truncate text-xs text-warning-700" :title="row.last_error">{{ row.last_error }}</span>
                <span v-else class="text-xs text-muted">—</span>
              </template>
              <template #updated_at="{ row }">{{ formatTime(row.updated_at) }}</template>
              <template #actions="{ row }">
                <div class="flex items-center justify-center gap-1 whitespace-nowrap">
                  <BaseButton size="sm" intent="ghost" :data-testid="`detail-${row.id}`" @click="openDetail(row.id)">详情</BaseButton>
                  <BaseButton v-if="row.status === 'stopped'" size="sm" intent="ghost"
                    :disabled="actionPending(row.id)" @click="startTask(row as WecomProxyTask)">
                    {{ actionPending(row.id) && startingId === row.id ? '启动中…' : '启动' }}</BaseButton>
                  <BaseButton v-if="row.status === 'running'" size="sm" intent="ghost" :disabled="actionPending(row.id)" @click="askStop(row as WecomProxyTask)">停止</BaseButton>
                  <BaseButton v-if="row.status !== 'deleted'" size="sm" intent="ghost" :disabled="row.status === 'running' || actionPending(row.id)"
                    :title="row.status === 'running' ? '运行中不可编辑，请先停止' : ''"
                    @click="openEdit(row as WecomProxyTask)">编辑</BaseButton>
                  <BaseButton v-if="row.status === 'stopped'" size="sm" intent="danger-ghost"
                    :disabled="actionPending(row.id)" @click="askDelete(row as WecomProxyTask)">删除</BaseButton>
                </div>
              </template>
              <template #empty>
                <span v-if="loading">正在加载任务…</span>
                <span v-else-if="listError">加载失败</span>
                <span v-else>暂无代理任务。点击右上角“新建代理任务”创建；已删除任务可勾选上方开关只读查看。</span>
              </template>
            </BaseTable>
          </div>
          <div v-if="hasMoreTasks" class="px-4 py-3 border-t border-default text-center">
            <BaseButton intent="secondary" size="sm" :disabled="loading" data-testid="load-more-tasks" @click="loadMoreTasks">
              {{ loading ? '加载中…' : '加载更多任务' }}
            </BaseButton>
          </div>
        </div>

        <!-- ==================== 详情视图 ==================== -->
        <div v-else class="bg-surface border border-default rounded-xl overflow-hidden">
          <div class="px-4 py-3 border-b border-default flex items-center gap-3 flex-wrap">
            <BaseButton intent="secondary" data-testid="back-list" @click="closeDetail">返回列表</BaseButton>
            <div class="flex-1" />
            <BaseButton intent="ghost" :disabled="detailLoading" @click="loadDetail">刷新</BaseButton>
          </div>

          <p v-if="detailError" role="alert" class="mx-4 mt-3 text-sm text-danger-700">{{ detailError }}</p>
          <p v-if="detailLoading && !detailTask" class="mx-4 mt-3 text-sm text-muted">正在加载任务详情…</p>
          <p v-if="actionError" role="alert" class="mx-4 mt-3 text-sm text-danger-700">{{ actionError }}</p>

          <template v-if="detailTask">
            <div class="px-4 py-4 border-b border-default flex items-start gap-3 flex-wrap">
              <div class="flex-1 min-w-48">
                <div class="flex items-center gap-2 flex-wrap">
                  <h2 class="text-base font-semibold text-default m-0 break-words" data-testid="detail-title">{{ detailTask.title }}</h2>
                  <BaseBadge :intent="statusBadge(detailTask).intent">{{ statusBadge(detailTask).label }}</BaseBadge>
                  <span v-if="detailTask.stop_requested && detailTask.status === 'running'" class="text-xs text-warning-700">停止中：等待当前批次结束</span>
                </div>
                <div class="flex items-center gap-1 mt-2 flex-wrap">
                  <BaseButton v-if="detailTask.status === 'stopped'" size="sm" intent="secondary"
                    :disabled="actionPending(detailTask.id)" @click="startTask(detailTask)">启动</BaseButton>
                  <BaseButton v-if="detailTask.status === 'running'" size="sm" intent="secondary" :disabled="actionPending(detailTask.id)" @click="askStop(detailTask)">停止</BaseButton>
                  <BaseButton v-if="detailTask.status !== 'deleted'" size="sm" intent="secondary" :disabled="detailTask.status === 'running' || actionPending(detailTask.id)"
                    :title="detailTask.status === 'running' ? '运行中不可编辑，请先停止' : ''"
                    @click="openEdit(detailTask)">编辑</BaseButton>
                  <BaseButton v-if="detailTask.status === 'stopped'" size="sm" intent="danger-ghost"
                    :disabled="actionPending(detailTask.id)" @click="askDelete(detailTask)">删除</BaseButton>
                </div>
              </div>
            </div>

            <section class="px-4 py-4 border-b border-default" aria-label="任务信息">
              <h3 class="text-sm font-semibold text-default m-0 mb-3">任务信息</h3>
              <dl class="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2 text-sm m-0">
                <div class="flex gap-2"><dt class="text-muted shrink-0 w-24 m-0">任务编号</dt><dd class="m-0 text-default break-all">#{{ detailTask.id }}</dd></div>
                <div class="flex gap-2"><dt class="text-muted shrink-0 w-24 m-0">智能体</dt><dd class="m-0 text-default break-all">{{ agentName(detailTask.agent_id) }}</dd></div>
                <div class="flex gap-2"><dt class="text-muted shrink-0 w-24 m-0">设备</dt><dd class="m-0 text-default break-all">{{ shortSession(detailTask.device_id) }}</dd></div>
                <div class="flex gap-2"><dt class="text-muted shrink-0 w-24 m-0">企微账号</dt><dd class="m-0 text-default break-all">{{ detailTask.wecom_account_id }}</dd></div>
                <div class="flex gap-2"><dt class="text-muted shrink-0 w-24 m-0">创建时间</dt><dd class="m-0 text-default">{{ formatTime(detailTask.created_at) }}</dd></div>
                <div class="flex gap-2"><dt class="text-muted shrink-0 w-24 m-0">更新时间</dt><dd class="m-0 text-default">{{ formatTime(detailTask.updated_at) }}</dd></div>
                <div class="flex gap-2"><dt class="text-muted shrink-0 w-24 m-0">修订号</dt><dd class="m-0 text-default">{{ detailTask.revision }}</dd></div>
                <div v-if="detailTask.deleted_at" class="flex gap-2"><dt class="text-muted shrink-0 w-24 m-0">删除时间</dt><dd class="m-0 text-default">{{ formatTime(detailTask.deleted_at) }}</dd></div>
              </dl>
              <div class="mt-3">
                <p class="text-xs text-muted m-0 mb-1">任务指令（编辑后的新指令只用于新批次，运行中批次沿其冻结指令）</p>
                <p class="text-sm text-default m-0 whitespace-pre-wrap break-words rounded-lg border border-default bg-gray-50 px-3 py-2">{{ detailTask.instruction }}</p>
              </div>
              <p class="text-xs text-muted m-0 mt-3">Runtime、企微账号、智能体在创建后固定；更换任一项须另建任务。空闲读取（零回答的监控轮次）与回答积分按任务累计展示。</p>
            </section>

            <section class="px-4 py-4 border-b border-default" aria-label="会话目标">
              <h3 class="text-sm font-semibold text-default m-0 mb-3">会话目标（{{ activeTargets.length }} 个监控中）</h3>
              <div class="table-scroll-wrapper">
                <BaseTable :columns="targetColumns" :data="detailTask.targets || []" row-key="id">
                  <template #session="{ row }">
                    <span class="text-sm text-default">{{ targetName(row.session) }}</span>
                    <span class="block text-xs text-muted">{{ shortSession(row.session) }}</span>
                  </template>
                  <template #status="{ row }">
                    <BaseBadge :intent="targetBadge(row.status).intent">{{ targetBadge(row.status).label }}</BaseBadge>
                    <span v-if="row.status === 'halted' && row.halted_reason" class="block text-xs text-warning-700 mt-1 whitespace-pre-wrap">{{ row.halted_reason }}</span>
                  </template>
                  <template #baseline_origin="{ row }">{{ baselineLabel(row.baseline_origin) }}</template>
                  <template #processed_position="{ row }">{{ row.processed_position ?? '—' }}</template>
                  <template #latest_seen_msg_id="{ row }">{{ row.latest_seen_msg_id ?? '—' }}</template>
                  <template #idle_read_count="{ row }">{{ row.idle_read_count ?? 0 }}</template>
                  <template #wb1_cursor="{ row }">
                    <span v-if="row.wb1_cursor" class="break-all">{{ shortSession(row.wb1_cursor) }}</span>
                    <span v-else class="text-xs text-muted">未建基线（首次启动时建立）</span>
                  </template>
                  <template #empty>暂无会话目标。停止后可编辑添加。</template>
                </BaseTable>
              </div>
            </section>

            <section class="px-4 py-4" aria-label="批次记录">
              <h3 class="text-sm font-semibold text-default m-0 mb-3">批次记录（最近 {{ detailTask.batches?.length || 0 }} 条，新批次在上）</h3>
              <p v-if="!detailTask.batches?.length" class="text-sm text-muted py-3">
                暂无批次。启动后监控发现新消息才创建回答批次；空闲读取不创建批次。
              </p>
              <article v-for="batch in detailTask.batches" :key="batch.id" class="rounded-xl border border-default mb-3 overflow-hidden" :data-testid="`batch-${batch.id}`">
                <header class="px-4 py-3 bg-gray-50 border-b border-default flex items-start justify-between gap-3 flex-wrap">
                  <div class="min-w-0 text-xs text-muted">
                    <p class="m-0"><span class="text-sm text-default font-medium">{{ targetName(batch.session) }}</span> · 批次 #{{ batch.id }} · 冻结上界消息 {{ batch.batch_end_msg_id ?? '—' }}</p>
                    <p class="m-0 mt-1 break-all">Runner {{ batch.runner_id || '—' }} · 回答积分 {{ formatCredit(batch.credit_cost) }} · 创建 {{ formatTime(batch.created_at) }}<template v-if="batch.finished_at"> · 结束 {{ formatTime(batch.finished_at) }}</template></p>
                  </div>
                  <div class="shrink-0 flex items-center gap-2">
                    <BaseBadge v-if="batch.retry_count > 0" intent="warning">已重试 {{ batch.retry_count }} 次</BaseBadge>
                    <BaseBadge :intent="batchBadge(batch.status).intent">{{ batchBadge(batch.status).label }}</BaseBadge>
                  </div>
                </header>
                <div class="px-4 py-3">
                  <div v-if="batch.cli_error" class="rounded-lg border border-warning-200 bg-warning-50 px-3 py-2 text-sm text-warning-700 mb-3 break-all">
                    <p class="m-0">CLI 错误：{{ batch.cli_error }}</p>
                  </div>
                  <template v-if="batch.end_json">
                    <p v-if="batchNeedsReply(batch.end_json) === false && !batch.end_json.replies?.length" class="text-sm text-default m-0">
                      本批判定无需回复。
                    </p>
                    <div v-for="(reply, index) in batch.end_json.replies || []" :key="index" class="mb-3 last:mb-0">
                      <p class="text-xs text-muted m-0 mb-1">回复文本<template v-if="reply.inbound_msg_ids?.length">（覆盖消息 {{ reply.inbound_msg_ids.join('、') }}）</template></p>
                      <p class="text-sm text-default m-0 whitespace-pre-wrap break-words rounded-lg border border-default px-3 py-2">{{ reply.text || '—' }}</p>
                      <p class="text-xs text-muted m-0 mt-1 break-all">
                        本机客户端已记录发送<template v-if="reply.receipt_id"> · 回执 {{ reply.receipt_id }}</template><template v-if="reply.verification_detail?.message_id"> · 本地库消息 #{{ reply.verification_detail.message_id }}</template>
                      </p>
                    </div>
                    <div v-if="batch.end_json.unanswered?.length" class="rounded-lg border border-warning-200 bg-warning-50 px-3 py-2 text-sm text-warning-700">
                      <p class="m-0 mb-1 font-medium">未回复消息（{{ batch.end_json.unanswered.length }}）</p>
                      <p v-for="(item, index) in batch.end_json.unanswered" :key="index" class="m-0 break-all">
                        消息 #{{ item.msg_id ?? '—' }}：{{ unansweredReason(item) }}<template v-if="item.cli_error?.code"> · 错误码 {{ item.cli_error.code }}</template><template v-if="item.cli_error?.invocation_id"> · 调用 {{ item.cli_error.invocation_id }}</template>
                      </p>
                    </div>
                    <details class="mt-3 text-xs text-muted">
                      <summary class="cursor-pointer">完整结束 JSON</summary>
                      <pre class="m-0 mt-1 p-2 rounded-lg bg-gray-50 border border-default overflow-x-auto break-all whitespace-pre-wrap">{{ JSON.stringify(batch.end_json, null, 2) }}</pre>
                    </details>
                  </template>
                  <p v-else-if="!batch.cli_error" class="text-xs text-muted m-0">批次尚未产生结束记录。</p>
                </div>
              </article>
              <div v-if="batchesHasMore" class="text-center pt-1">
                <BaseButton intent="secondary" size="sm" :disabled="detailLoading" data-testid="load-more-batches" @click="loadMoreBatches">
                  {{ detailLoading ? '加载中…' : '加载更多批次' }}
                </BaseButton>
              </div>
            </section>
          </template>
        </div>
      </div>
    </main>

    <!-- ==================== 创建 / 编辑弹框 ==================== -->
    <BaseModal v-model="formOpen" :title="editingTask ? '编辑代理任务（已停止）' : '新建代理任务'" size="lg" accessible :show-fullscreen="false">
      <div class="space-y-4">
        <p v-if="!editingTask" class="text-xs text-muted m-0">
          创建时将对当前选定设备执行真实检查（doctor＋标定），全部通过才保存任务。Runtime、企微账号、智能体在创建后固定，更换任一项须另建任务。
        </p>
        <p v-else class="text-xs text-muted m-0">
          新增会话将读取当前最新消息水位作基线（该水位后续启动不覆盖）；移除会话的未决等待批次将结束且不再发送；保留会话沿用原位置。
        </p>
        <div>
          <label class="block text-xs font-medium text-muted mb-1" for="proxy-task-title">任务标题</label>
          <BaseInput id="proxy-task-title" v-model="formTitle" maxlength="100" placeholder="不超过 100 字" aria-label="任务标题" />
        </div>
        <div>
          <div class="flex items-center justify-between mb-1">
            <label class="text-xs font-medium text-muted">监控会话（可多选，1–50 个）</label>
            <BaseButton intent="secondary" size="sm" @click="openPicker">从本地目录选择会话</BaseButton>
          </div>
          <div v-if="formSessions.size" class="flex flex-wrap gap-2">
            <span v-for="session in [...formSessions]" :key="session" class="inline-flex items-center gap-1 rounded-lg border border-default px-2 py-1 text-xs text-default">
              {{ directoryName(session) || shortSession(session) }}
            </span>
          </div>
          <p v-else class="text-xs text-muted m-0">尚未选择会话。</p>
        </div>
        <div>
          <label class="block text-xs font-medium text-muted mb-1" for="proxy-task-agent">智能体（创建后固定）</label>
          <BaseSelect id="proxy-task-agent" v-model="formAgent" :disabled="!!editingTask" aria-label="选择智能体">
            <option value="">请选择智能体</option>
            <option v-for="item in agents" :key="item.agent_id" :value="item.agent_id">{{ agentLabel(item) }}</option>
          </BaseSelect>
          <p v-if="editingTask" class="text-xs text-muted m-0 mt-1">智能体创建后固定，编辑不可更换。</p>
          <p v-if="agentsError" role="alert" class="text-xs text-danger-700 m-0 mt-1">{{ agentsError }}</p>
        </div>
        <div>
          <label class="block text-xs font-medium text-muted mb-1" for="proxy-task-instruction">任务指令（不超过 5000 字；只并入本任务的回答）</label>
          <textarea id="proxy-task-instruction" v-model="formInstruction" rows="4" maxlength="5000"
            class="w-full text-sm rounded-lg border border-default bg-surface text-default px-3 py-2 outline-none focus:ring-1 focus:ring-primary-600"
            aria-label="任务指令" />
        </div>
        <p v-if="formError" role="alert" class="text-sm text-danger-700 m-0" data-testid="form-error">{{ formError }}</p>
      </div>
      <template #footer>
        <div class="flex items-center justify-between gap-2 w-full">
          <span class="text-xs text-muted">{{ submitting ? (editingTask ? '正在保存；新增会话需读取水位…' : '正在创建并执行设备与账号真实检查（doctor＋标定）…') : '' }}</span>
          <div class="flex gap-2">
            <BaseButton intent="secondary" :disabled="submitting" @click="formOpen = false">取消</BaseButton>
            <BaseButton :disabled="submitting" data-testid="submit-form" @click="submitForm">{{ submitting ? '提交中…' : editingTask ? '保存修改' : '创建任务' }}</BaseButton>
          </div>
        </div>
      </template>
    </BaseModal>

    <!-- ==================== 会话目录弹框（复用每日总结目录来源） ==================== -->
    <BaseModal v-model="pickerOpen" title="选择监控会话" size="lg" accessible :show-fullscreen="false">
      <div class="space-y-3">
        <p class="text-xs text-muted m-0">{{ directoryIdentity || '正在连接当前选定设备与企微账号…' }}。仅已登记会话可加入代理任务；未登记会话请先在会话记录页添加。</p>
        <div class="flex gap-2"><BaseInput v-model="directoryQuery" placeholder="搜索会话名称" maxlength="100" aria-label="搜索会话名称" @keyup.enter="searchDirectory" />
          <BaseButton intent="secondary" @click="searchDirectory">搜索</BaseButton></div>
        <p v-if="directoryError" role="alert" class="text-sm text-danger-700">{{ directoryError }}</p>
        <p v-if="loadingDirectory" class="text-sm text-muted">正在读取本地目录…</p>
        <div class="max-h-72 overflow-y-auto rounded-lg border border-default divide-y divide-default">
          <div v-for="item in directoryItems" :key="item.session" class="px-3 py-2 text-sm flex items-start gap-2">
            <input type="checkbox" class="mt-1" :checked="formSessions.has(item.session)" :disabled="!item.added"
              :aria-label="`选择${item.name || shortSession(item.session)}`" @change="toggleDirectory(item.session)" />
            <div class="flex-1 min-w-0"><div class="flex items-center gap-2"><span class="truncate text-default">{{ item.name || '未命名会话' }}</span>
              <span class="text-xs text-muted">{{ item.added ? '已登记' : '未登记' }} · {{ kindLabel(item.kind) }}</span></div>
              <span class="text-xs text-muted">{{ shortSession(item.session) }}<template v-if="item.kind !== 'group'"> · {{ item.peer_platform }}</template></span>
            </div>
          </div>
          <p v-if="!loadingDirectory && !directoryItems.length" class="text-sm text-muted px-3 py-5 m-0">当前目录没有匹配会话。</p>
        </div>
        <BaseButton v-if="directoryCursor" intent="secondary" :disabled="loadingDirectory" @click="loadDirectory(true)">加载更多</BaseButton>
      </div>
      <template #footer>
        <div class="flex items-center justify-between gap-2 w-full"><span class="text-xs text-muted">已选择 {{ formSessions.size }} 个会话</span>
          <BaseButton intent="secondary" @click="pickerOpen = false">完成选择</BaseButton></div>
      </template>
    </BaseModal>

    <!-- ==================== 操作确认（停止 / 删除） ==================== -->
    <BaseModal :model-value="!!confirmState" :title="confirmState?.title || '确认操作'" size="sm" accessible :show-fullscreen="false"
      @update:model-value="v => { if (!v) confirmState = null }">
      <p class="text-sm text-default m-0" data-testid="confirm-message">{{ confirmState?.message }}</p>
      <template #footer>
        <BaseButton intent="secondary" @click="confirmState = null">取消</BaseButton>
        <BaseButton :intent="confirmState?.intent || 'primary'" :disabled="actionPending(confirmState?.taskId || 0)" @click="runConfirmed">确认</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppHeader from '@/components/AppHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseTable from '@/components/ui/BaseTable.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import {
  createProxyTask, deleteProxyTask, editProxyTask, getProxyTask, listProxyTasks,
  proxyErrorText, startProxyTask, stopProxyTask,
  type ProxyBatchEndJson, type ProxyBatchUnanswered, type WecomProxyTask,
} from '@/api/wecomProxy'
import { listDirectory, type DirectoryConversation } from '@/api/wecomConversation'
import { listSubagents, type SubagentListItem } from '@/api/subagent'

const route = useRoute()
const router = useRouter()
const { admin, isLoggedIn, logout: tenantLogout } = useTenantAuth()
const toggleSidebarFn = inject<() => void>('toggleSidebar')
const effectiveIsLoggedIn = computed(() => isLoggedIn.value)
const effectiveUser = computed(() => admin.value ? { user_id: admin.value.user_id, username: admin.value.username, phone: admin.value.phone } : null)
function handleToggleSidebar() { toggleSidebarFn?.() }
function goCenter() { router.push({ name: 'tenant-wecom-conversations' }) }

// ==================== 智能体选项（租户有权限的智能体） ====================

const agents = ref<SubagentListItem[]>([])
const agentsError = ref('')
async function loadAgents() {
  agentsError.value = ''
  try {
    const res = await listSubagents()
    agents.value = Array.isArray(res.data) ? res.data : []
  } catch (error) { agentsError.value = `智能体列表读取失败：${errorText(error)}` }
}
function agentLabel(item: SubagentListItem): string {
  return item.display_name || item.instance_name || item.name || item.agent_id
}
function agentName(agentId: string): string {
  const item = agents.value.find(candidate => candidate.agent_id === agentId)
  return item ? `${agentLabel(item)}（${agentId}）` : agentId
}

// ==================== 任务列表 ====================

const tasks = ref<WecomProxyTask[]>([])
const loading = ref(false)
const listError = ref('')
const actionError = ref('')
const includeDeleted = ref(false)
// 列表分页（offset 递增；has_more 按返回条数==limit 判定，与 include_deleted 叠加）
const LIST_PAGE_SIZE = 50
const hasMoreTasks = ref(false)
let listSeq = 0
const actionIds = ref(new Set<number>())
const startingId = ref<number | null>(null)
function actionPending(id: number): boolean { return actionIds.value.has(id) }
function markAction(id: number, on: boolean) {
  const next = new Set(actionIds.value)
  if (on) next.add(id); else next.delete(id)
  actionIds.value = next
}

const taskColumns = [
  { key: 'status', label: '状态', width: '92px' },
  { key: 'title', label: '标题' },
  { key: 'active_target_count', label: '监控会话数', width: '96px', tdAlign: 'center' as const },
  { key: 'agent_id', label: '智能体', width: '150px' },
  { key: 'last_checked_at', label: '最近检查', width: '150px' },
  { key: 'latest_seen_msg_id', label: '最新消息', width: '88px', tdAlign: 'center' as const },
  { key: 'idle_read_count', label: '空闲读取', width: '84px', tdAlign: 'center' as const },
  { key: 'credit_cost', label: '回答积分', width: '88px', tdAlign: 'center' as const },
  { key: 'last_error', label: '最近错误', width: '150px' },
  { key: 'updated_at', label: '最近更新', width: '150px' },
  { key: 'actions', label: '操作', width: '220px', tdAlign: 'center' as const },
]

async function loadList() {
  const seq = ++listSeq
  loading.value = true
  listError.value = ''
  try {
    const value = await listProxyTasks({ includeDeleted: includeDeleted.value,
      limit: LIST_PAGE_SIZE, offset: 0 })
    if (seq !== listSeq) return
    tasks.value = value.tasks || []
    hasMoreTasks.value = tasks.value.length === LIST_PAGE_SIZE
  } catch (error) {
    if (seq === listSeq) listError.value = proxyErrorText(error)
  } finally {
    if (seq === listSeq) loading.value = false
  }
}

/** 加载更多任务（offset 递增追加；include_deleted 沿用当前开关）。 */
async function loadMoreTasks() {
  if (loading.value || !hasMoreTasks.value) return
  const seq = ++listSeq
  loading.value = true
  listError.value = ''
  try {
    const value = await listProxyTasks({ includeDeleted: includeDeleted.value,
      limit: LIST_PAGE_SIZE, offset: tasks.value.length })
    if (seq !== listSeq) return
    tasks.value = [...tasks.value, ...(value.tasks || [])]
    hasMoreTasks.value = (value.tasks || []).length === LIST_PAGE_SIZE
  } catch (error) {
    if (seq === listSeq) listError.value = proxyErrorText(error)
  } finally {
    if (seq === listSeq) loading.value = false
  }
}

function toggleIncludeDeleted() {
  includeDeleted.value = !includeDeleted.value
  void loadList()
}
function reload() {
  void loadList()
  if (detailId.value) void loadDetail()
}

function statusBadge(task: WecomProxyTask): { label: string; intent: 'success' | 'warning' | 'neutral' } {
  if (task.status === 'running') return { label: '运行中', intent: 'success' }
  if (task.status === 'deleted') return { label: '已删除', intent: 'neutral' }
  return { label: '已停止', intent: 'neutral' }
}

// ==================== 操作（启动 / 停止 / 删除） ====================

async function startTask(task: WecomProxyTask) {
  if (actionPending(task.id)) return
  markAction(task.id, true)
  startingId.value = task.id
  actionError.value = ''
  try {
    await startProxyTask(task.id)
    await reloadAfterAction(task.id)
  } catch (error) { actionError.value = proxyErrorText(error) }
  finally { markAction(task.id, false); startingId.value = null }
}

const confirmState = ref<{
  title: string
  message: string
  intent: 'primary' | 'danger'
  taskId: number
  action: () => Promise<void>
} | null>(null)

function askStop(task: WecomProxyTask) {
  confirmState.value = {
    title: '确认停止任务',
    message: `停止「${task.title}」后不再周期监控；当前正在执行的批次将结束或进入等待，之后任务变为已停止并可编辑。再次启动会从各会话保存的位置补读停顿期间的新消息。`,
    intent: 'primary',
    taskId: task.id,
    action: async () => {
      await stopProxyTask(task.id)
      await reloadAfterAction(task.id)
    },
  }
}

function askDelete(task: WecomProxyTask) {
  confirmState.value = {
    title: '确认删除任务',
    message: `删除「${task.title}」后任务退出监控，其未决等待批次结束、不再发送。历史配置与批次结果仍可在本页只读查看（勾选“显示已删除任务”）；企微原聊天不受影响。`,
    intent: 'danger',
    taskId: task.id,
    action: async () => {
      await deleteProxyTask(task.id)
      await reloadAfterAction(task.id)
    },
  }
}

async function runConfirmed() {
  const current = confirmState.value
  confirmState.value = null
  if (!current) return
  markAction(current.taskId, true)
  actionError.value = ''
  try {
    await current.action()
  } catch (error) { actionError.value = proxyErrorText(error) }
  finally { markAction(current.taskId, false) }
}

/** 操作成功后刷新：列表必刷；操作对象在详情视图时同步重查详情。 */
async function reloadAfterAction(taskId: number) {
  void loadList()
  if (String(taskId) === detailId.value) await loadDetail()
}

// ==================== 详情（路由 query.task 同步） ====================

const detailId = computed(() => (typeof route.query.task === 'string' && /^\d+$/.test(route.query.task) ? route.query.task : ''))
const detailTask = ref<WecomProxyTask | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
// 批次分页（新批次在前；"加载更多批次"按 offset 追加渲染）
const BATCH_PAGE_SIZE = 50
const batchesHasMore = ref(false)
let detailSeq = 0

function openDetail(id: number) {
  router.push({ query: { ...route.query, task: String(id) } })
}
function closeDetail() {
  const query = { ...route.query }
  delete query.task
  router.push({ query })
}
async function loadDetail() {
  const id = detailId.value
  if (!id) return
  const seq = ++detailSeq
  detailLoading.value = true
  detailError.value = ''
  try {
    const value = await getProxyTask(Number(id), { batchLimit: BATCH_PAGE_SIZE })
    if (seq !== detailSeq || detailId.value !== id) return
    detailTask.value = value.task
    batchesHasMore.value = value.task.batches_has_more === true
  } catch (error) {
    if (seq === detailSeq) { detailTask.value = null; batchesHasMore.value = false; detailError.value = proxyErrorText(error) }
  } finally {
    if (seq === detailSeq) detailLoading.value = false
  }
}

/** 加载更多批次：按当前已取条数递增 offset，追加渲染。 */
async function loadMoreBatches() {
  const id = detailId.value
  if (!id || detailLoading.value || !batchesHasMore.value) return
  const seq = ++detailSeq
  detailLoading.value = true
  detailError.value = ''
  try {
    const offset = detailTask.value?.batches?.length || 0
    const value = await getProxyTask(Number(id), { batchLimit: BATCH_PAGE_SIZE, batchOffset: offset })
    if (seq !== detailSeq || detailId.value !== id) return
    const current = detailTask.value
    if (current) {
      current.batches = [...(current.batches || []), ...(value.task.batches || [])]
    }
    batchesHasMore.value = value.task.batches_has_more === true
  } catch (error) {
    if (seq === detailSeq) detailError.value = proxyErrorText(error)
  } finally {
    if (seq === detailSeq) detailLoading.value = false
  }
}
watch(detailId, id => {
  detailTask.value = null
  batchesHasMore.value = false
  if (id) void loadDetail()
})

const activeTargets = computed(() => (detailTask.value?.targets || []).filter(item => item.status === 'active'))

const targetColumns = [
  { key: 'session', label: '会话' },
  { key: 'status', label: '状态', width: '150px' },
  { key: 'baseline_origin', label: '基线来源', width: '100px' },
  { key: 'processed_position', label: '已处理位置', width: '100px', tdAlign: 'center' as const },
  { key: 'latest_seen_msg_id', label: '最新消息', width: '96px', tdAlign: 'center' as const },
  { key: 'idle_read_count', label: '空闲读取', width: '84px', tdAlign: 'center' as const },
  { key: 'wb1_cursor', label: 'wb1 游标', width: '160px' },
]

function targetBadge(status: string): { label: string; intent: 'success' | 'warning' | 'neutral' } {
  if (status === 'active') return { label: '监控中', intent: 'success' }
  if (status === 'halted') return { label: '已停住', intent: 'warning' }
  return { label: '已移除', intent: 'neutral' }
}
function baselineLabel(origin: string | null): string {
  if (origin === 'initial') return '创建初选'
  if (origin === 'edit_join_read') return '编辑加入'
  return '未建基线'
}
function batchBadge(status: string): { label: string; intent: 'success' | 'warning' | 'primary' | 'neutral' } {
  const labels: Record<string, { label: string; intent: 'success' | 'warning' | 'primary' | 'neutral' }> = {
    pending: { label: '排队中', intent: 'neutral' },
    running: { label: '回答中', intent: 'primary' },
    waiting: { label: '等待恢复', intent: 'warning' },
    reconcile_hold: { label: '待对账', intent: 'warning' },
    succeeded: { label: '已完成', intent: 'success' },
    failed: { label: '失败', intent: 'warning' },
    ended_by_removal: { label: '因移除目标结束', intent: 'neutral' },
    ended_by_delete: { label: '因删除任务结束', intent: 'neutral' },
  }
  return labels[status] || { label: status, intent: 'neutral' }
}
function batchNeedsReply(endJson: ProxyBatchEndJson): boolean | undefined {
  return typeof endJson.needs_reply === 'boolean' ? endJson.needs_reply : undefined
}
const UNANSWERED_REASON_LABELS: Record<string, string> = {
  send_failed: '发送失败',
  send_unknown: '发送结果未知',
}
function unansweredReason(item: ProxyBatchUnanswered): string {
  const reason = typeof item.reason === 'string' ? item.reason : ''
  return UNANSWERED_REASON_LABELS[reason] || reason || '未成功回复'
}

// ==================== 创建 / 编辑表单 ====================

const formOpen = ref(false)
const formTitle = ref('')
const formInstruction = ref('')
const formAgent = ref('')
const formSessions = ref(new Set<string>())
const editingTask = ref<WecomProxyTask | null>(null)
const submitting = ref(false)
const formError = ref('')

function openCreate() {
  editingTask.value = null
  formTitle.value = ''
  formInstruction.value = ''
  formAgent.value = ''
  formSessions.value = new Set()
  formError.value = ''
  if (!agents.value.length) void loadAgents()
  formOpen.value = true
}

/** 停止后编辑：预填标题/指令/会话；智能体固定不可换。列表投影不含 targets，先查详情。 */
async function openEdit(task: WecomProxyTask) {
  if (task.status !== 'stopped') return
  actionError.value = ''
  let source = task
  if (!source.targets) {
    try {
      source = (await getProxyTask(task.id)).task
    } catch (error) {
      actionError.value = `读取任务详情失败：${proxyErrorText(error)}`
      return
    }
  }
  editingTask.value = source
  formTitle.value = source.title
  formInstruction.value = source.instruction
  formAgent.value = source.agent_id
  formSessions.value = new Set((source.targets || [])
    .filter(item => item.status === 'active' || item.status === 'halted')
    .map(item => item.session))
  formError.value = ''
  if (!agents.value.length) void loadAgents()
  formOpen.value = true
}

function validateForm(): string {
  if (!formTitle.value.trim()) return '请填写任务标题'
  if (formTitle.value.trim().length > 100) return '任务标题不超过 100 字'
  if (!formSessions.value.size) return '请至少选择 1 个监控会话（最多 50 个）'
  if (formSessions.value.size > 50) return '监控会话最多 50 个'
  if (!editingTask.value && !formAgent.value) return '请选择智能体'
  if (!formInstruction.value.trim()) return '请填写任务指令'
  if (formInstruction.value.trim().length > 5000) return '任务指令不超过 5000 字'
  return ''
}

async function submitForm() {
  if (submitting.value) return
  const invalid = validateForm()
  if (invalid) { formError.value = invalid; return }
  submitting.value = true
  formError.value = ''
  try {
    if (editingTask.value) {
      const existing = (editingTask.value.targets || [])
        .filter(item => item.status === 'active' || item.status === 'halted')
        .map(item => item.session)
      const add = [...formSessions.value].filter(item => !existing.includes(item))
      const remove = existing.filter(item => !formSessions.value.has(item))
      await editProxyTask(editingTask.value.id, {
        title: formTitle.value.trim(),
        instruction: formInstruction.value.trim(),
        ...(add.length ? { add_sessions: add } : {}),
        ...(remove.length ? { remove_sessions: remove } : {}),
      })
    } else {
      const agent = agents.value.find(item => item.agent_id === formAgent.value)
      await createProxyTask({
        title: formTitle.value.trim(),
        instruction: formInstruction.value.trim(),
        agent_id: formAgent.value,
        sessions: [...formSessions.value],
        ...(agent?.instance_id ? { instance_id: agent.instance_id } : {}),
      })
    }
    formOpen.value = false
    await reloadAfterAction(editingTask.value?.id || 0)
  } catch (error) {
    formError.value = proxyErrorText(error)
  } finally {
    submitting.value = false
  }
}

// ==================== 会话目录（复用每日总结目录来源） ====================

const pickerOpen = ref(false)
const directoryQuery = ref('')
const appliedDirectoryQuery = ref('')
const directoryItems = ref<DirectoryConversation[]>([])
const directoryCursor = ref<string | null>(null)
const directoryIdentity = ref('')
const directorySource = ref('')
const directoryError = ref('')
const loadingDirectory = ref(false)
const directoryNames = ref<Record<string, string>>({})
let directorySeq = 0

function directoryName(session: string): string {
  return directoryNames.value[session] || ''
}
function targetName(session: string): string {
  return directoryName(session) || shortSession(session)
}
function openPicker() {
  pickerOpen.value = true
  directoryQuery.value = ''
  appliedDirectoryQuery.value = ''
  directoryItems.value = []
  directoryCursor.value = null
  directoryIdentity.value = ''
  directorySource.value = ''
  directoryError.value = ''
  void loadDirectory(false)
}
function searchDirectory() { void loadDirectory(false) }
async function loadDirectory(more: boolean) {
  if (more && !directoryCursor.value) return
  const seq = ++directorySeq
  const cursor = more ? directoryCursor.value || undefined : undefined
  if (!more) {
    appliedDirectoryQuery.value = directoryQuery.value.trim()
    directoryItems.value = []
    directoryCursor.value = null
  }
  loadingDirectory.value = true
  directoryError.value = ''
  try {
    const page = await listDirectory({ query: appliedDirectoryQuery.value, cursor, limit: 30 })
    if (seq !== directorySeq || !pickerOpen.value) return
    const source = `${page.device_id}|${page.self_user_id}|${page.source_generation}`
    if (more && directorySource.value !== source) {
      directoryItems.value = []
      directoryCursor.value = null
      throw new Error('设备或企微账号已变化，请重新搜索')
    }
    directorySource.value = source
    directoryIdentity.value = `当前设备 ${shortSession(page.device_id)} · 账号 ${page.self_user_id}`
    const names = { ...directoryNames.value }
    for (const item of page.conversations) if (item.name) names[item.session] = item.name
    directoryNames.value = names
    directoryItems.value = more ? [...directoryItems.value, ...page.conversations] : page.conversations
    directoryCursor.value = page.next_cursor
  } catch (error) { if (seq === directorySeq) directoryError.value = errorText(error) }
  finally { if (seq === directorySeq) loadingDirectory.value = false }
}
function toggleDirectory(session: string) {
  const next = new Set(formSessions.value)
  if (next.has(session)) next.delete(session); else next.add(session)
  formSessions.value = next
}

// ==================== 通用工具 ====================

function shortSession(session: string): string { return session.length > 16 ? `${session.slice(0, 8)}…${session.slice(-6)}` : session }
function kindLabel(kind: string) { return kind === 'group' ? '群聊' : kind === 'single' ? '单聊' : kind }
function formatTime(value?: string | null): string { return value ? new Date(value).toLocaleString('zh-CN') : '—' }
/** 回答积分（批次/列表投影数值，两位小数；缺省展示 —）。 */
function formatCredit(value?: number | null): string {
  return value == null ? '—' : Number(value).toFixed(2)
}
function errorText(error: unknown): string { return error instanceof Error ? error.message : String(error) }

onMounted(() => {
  void loadList()
  void loadAgents()
  // 深链进入详情（?task=id）时首查详情
  if (detailId.value) void loadDetail()
})
onUnmounted(() => {
  listSeq++; detailSeq++; directorySeq++
})
</script>
