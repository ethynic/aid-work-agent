<template>
  <div class="h-full flex flex-col bg-canvas">
    <AppHeader
      title="任务计划"
      :is-logged-in="tenantIsLoggedIn"
      :user="headerUser"
      @toggle-sidebar="toggleSidebar"
    />

    <!-- 顶部加载/错误态 -->
    <div v-if="loading && !task" class="flex-1 flex items-center justify-center">
      <div class="text-center text-gray-500">
        <svg class="w-8 h-8 mx-auto animate-spin" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
        </svg>
        <p class="mt-3 text-sm">正在加载任务…</p>
      </div>
    </div>
    <div v-else-if="error && !task" class="flex-1 flex items-center justify-center p-6">
      <div class="w-full max-w-md">
        <div class="bg-white border border-gray-200 rounded-lg p-6 text-center">
          <p class="text-danger-600 text-sm" role="alert">{{ error }}</p>
          <BaseButton class="mt-4" intent="secondary" size="sm" @click="router.push(`/t/${tenantId}/chat`)">返回工作台</BaseButton>
        </div>
      </div>
    </div>

    <!-- 任务视图主体（无聊天输入框，设计 §6） -->
    <div v-else-if="task" class="flex-1 min-h-0 overflow-y-auto">
      <div class="max-w-4xl mx-auto p-4 md:p-6 space-y-4">
        <!-- 头部 -->
        <div class="bg-white border border-gray-200 rounded-lg p-4 md:p-5">
          <div class="flex items-start gap-3">
            <button
              class="p-1.5 -mt-0.5 flex-shrink-0 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
              title="返回"
              @click="goBack"
            >
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
              </svg>
            </button>
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <h1 class="text-lg font-semibold text-gray-800 truncate">{{ task.name }}</h1>
                <BaseBadge :intent="statusIntent(task.status)" size="sm">{{ statusText(task) }}</BaseBadge>
              </div>
              <p class="mt-1 text-sm text-gray-500 line-clamp-2">{{ task.task_prompt }}</p>
              <div class="mt-3 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-2 text-sm">
                <div class="flex items-center gap-2 text-gray-600">
                  <svg class="w-4 h-4 flex-shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                  <span class="truncate">{{ describeSchedule(task) }}</span>
                </div>
                <div class="flex items-center gap-2 text-gray-600" v-if="task.next_due_at && task.status === 'active'">
                  <svg class="w-4 h-4 flex-shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 20A7 7 0 018.6 6.3C15 4.5 19 7 19 12h4a8 8 0 01-16 0" /></svg>
                  <span class="truncate">下次执行：{{ formatDateTime(task.next_due_at) }}</span>
                </div>
                <div class="flex items-center gap-2 text-gray-600">
                  <svg class="w-4 h-4 flex-shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" /></svg>
                  <span class="truncate">{{ profileLabel }}</span>
                </div>
                <div class="flex items-center gap-2 text-gray-600" v-if="task.last_run_at">
                  <svg class="w-4 h-4 flex-shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
                  <span class="truncate">上次执行：{{ formatDateTime(task.last_run_at) }}（{{ lastStatusText }}）</span>
                </div>
                <div class="flex items-center gap-2 text-gray-600">
                  <svg class="w-4 h-4 flex-shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" /></svg>
                  <span>已执行 {{ task.total_runs }} 次 · 成功 {{ task.success_count }} · 失败 {{ task.fail_count }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- §15 评审 P2：通知直达落地横幅（manual_rejected 带 notice_ref 时展示被拒原因） -->
        <div v-if="noticeBanner" class="bg-warning-50 border border-warning-200 rounded-lg p-3 flex items-start gap-2" role="alert">
          <svg class="w-5 h-5 flex-shrink-0 text-warning-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
          <div class="flex-1 min-w-0 text-sm text-warning-700">
            <p class="font-medium">{{ noticeBanner.title }}</p>
            <p class="mt-0.5 whitespace-pre-wrap">{{ noticeBanner.body }}</p>
          </div>
          <button class="text-warning-600 hover:text-warning-700 text-xs flex-shrink-0" @click="noticeBanner = null">知道了</button>
        </div>

        <!-- 操作区（按状态条件显示，设计 §6） -->
        <div class="bg-white border border-gray-200 rounded-lg p-3 md:p-4 flex flex-wrap items-center gap-2">
          <BaseButton v-if="task.status === 'active'" intent="secondary" size="sm" :disabled="actionBusy" @click="handlePause">暂停</BaseButton>
          <BaseButton v-if="task.status === 'paused'" intent="primary" size="sm" :disabled="actionBusy" @click="handleResume">恢复</BaseButton>
          <!-- §15：active 与因时间窗自动完成的循环任务可手动执行（不受时间窗限制，不改自动计划） -->
          <BaseButton v-if="canManualTrigger" intent="secondary" size="sm" :disabled="actionBusy" @click="handleTrigger">立即执行一次</BaseButton>
          <!-- §15（评审 P2）：仅 active/paused 可改期——后端对终态 409，按钮同步隐藏避免填表后才失败 -->
          <BaseButton v-if="task.status === 'active' || task.status === 'paused'" intent="secondary" size="sm" :disabled="actionBusy" @click="openReschedule">改期</BaseButton>
          <BaseButton intent="secondary" size="sm" :disabled="actionBusy" @click="openEdit">编辑</BaseButton>
          <!-- 仅恰好 1 个执行时提供无歧义的顶部取消（评审 P2-12）；多个时行内分别取消 -->
          <BaseButton v-if="activeRunners.length === 1" intent="danger-ghost" size="sm" :disabled="actionBusy" @click="handleCancelRunner(activeRunners[0].runner_id)">取消当前执行</BaseButton>
          <div class="flex-1"></div>
          <BaseButton intent="danger-ghost" size="sm" :disabled="actionBusy" @click="handleDelete">删除任务</BaseButton>
        </div>

        <!-- 当前执行横幅 -->
        <div v-if="activeRunners.length" class="bg-primary-50 border border-primary-200 rounded-lg p-3 flex items-center gap-3">
          <svg class="w-5 h-5 flex-shrink-0 text-primary-600 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
          </svg>
          <div class="flex-1 min-w-0 text-sm text-primary-800">
            <p>{{ runnerStatusLabel[activeRunners[0].status] || activeRunners[0].status }}{{ activeRunners.length > 1 ? `（${activeRunners.length} 个执行）` : '' }}，进度在下方执行记录中实时更新。</p>
            <p v-if="activeRunners.length > 1" class="mt-0.5 text-primary-700">有多个进行中的执行，请在下方执行记录中分别取消。</p>
            <p v-if="waitingText" class="mt-0.5 text-primary-700 truncate">{{ waitingText }}</p>
          </div>
          <BaseButton v-if="activeRunners.length === 1" intent="secondary" size="sm" :disabled="actionBusy" @click="handleCancelRunner(activeRunners[0].runner_id)">取消执行</BaseButton>
        </div>

        <!-- 执行记录列表（agent_runners 为执行事实，设计 §6） -->
        <div class="bg-white border border-gray-200 rounded-lg">
          <div class="px-4 py-3 border-b border-gray-100 flex items-center gap-2">
            <h2 class="text-sm font-medium text-gray-700">执行记录</h2>
            <BaseBadge intent="neutral" size="sm">{{ executions.length }}</BaseBadge>
            <div class="flex-1"></div>
            <button class="text-xs text-primary-600 hover:text-primary-700 flex items-center gap-1" :disabled="loadingRunners" @click="reloadRunners">
              <svg class="w-3.5 h-3.5" :class="{ 'animate-spin': loadingRunners }" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
              刷新
            </button>
          </div>
          <div v-if="loadingRunners && !executions.length" class="p-6 text-center text-sm text-gray-500">加载执行记录…</div>
          <div v-else-if="!executions.length" class="p-6 text-center text-sm text-gray-500">
            暂无执行记录，任务到期后自动执行；「立即执行一次」可手动触发。
          </div>
          <div v-else class="divide-y divide-gray-100">
            <div v-for="row in executions" :id="`runner-${row.runner_id}`" :key="row.runner_id" class="px-4 py-3 scroll-mt-24">
              <!-- 执行行：触发原因 / 时间 / 状态 / 费用 / attempt -->
              <div class="flex items-center gap-2 flex-wrap text-sm">
                <BaseBadge :intent="triggerIntent(row)" size="sm">{{ triggerLabel(row) }}</BaseBadge>
                <BaseBadge :intent="runnerStatusIntent(row.status)" size="sm">{{ runnerStatusLabel[row.status] || row.status }}</BaseBadge>
                <span class="text-gray-600">{{ formatDateTime(row.accepted_at) }}</span>
                <span v-if="row.finished_at" class="text-gray-400">→ {{ formatTime(row.finished_at) }}</span>
                <span v-if="row.attempt && row.attempt > 1" class="text-gray-400" title="自动恢复回合">第 {{ row.attempt }} 次恢复</span>
                <span v-if="typeof creditCost(row) === 'number'" class="text-gray-500">{{ creditCost(row) }} 积分</span>
                <span v-if="row.cancel_requested" class="text-warning-700 text-xs">取消核对中…</span>
                <div class="flex-1"></div>
                <!-- 行级取消：绑定该行 runner_id，消除「取消最新排队项」歧义（评审 P2-12） -->
                <button
                  v-if="!runnerTerminal(row.status)"
                  class="text-xs text-danger-600 hover:text-danger-700 disabled:opacity-50"
                  :disabled="actionBusy"
                  @click="handleCancelRunner(row.runner_id)"
                >取消</button>
                <button
                  v-if="hasExpandableDetail(row)"
                  class="text-xs text-primary-600 hover:text-primary-700"
                  @click="toggleExpand(row.runner_id)"
                >{{ expanded[row.runner_id] ? '收起' : '查看详情' }}</button>
              </div>
              <!-- 失败原因 -->
              <p v-if="row.status === 'failed'" class="mt-1.5 text-sm text-danger-600">
                {{ runnerErrorText(row) }}
              </p>
              <!-- 输出摘要 / 实时进度 -->
              <div v-if="executionBodyText(row) && !expanded[row.runner_id]" class="mt-1.5">
                <p class="text-sm text-gray-600 line-clamp-2 whitespace-pre-wrap">{{ executionSummary(row) }}</p>
                <p v-if="executionImages(row).length || executionDownloadableFiles(row).length" class="text-xs text-gray-400">
                  含 {{ executionImages(row).length }} 张图片、{{ executionDownloadableFiles(row).length }} 个文件，点击「查看详情」查看。
                </p>
              </div>
              <!-- 展开详情：完整文本 + 图片 + 下载文件 + 执行过程 -->
              <div v-if="expanded[row.runner_id]" class="mt-2 space-y-2">
                <div v-if="executionBodyText(row)" class="text-gray-700 leading-relaxed markdown-content prose-sm prose-slate max-w-none" v-html="renderedOutput(row)"></div>
                <ImageGallery v-if="executionImages(row).length" :images="executionImages(row)" />
                <!-- 下载文件卡片（复用聊天页 DownloadFileCard，评审：agent 生成的附件不可下载） -->
                <div v-if="executionDownloadableFiles(row).length" class="space-y-2">
                  <p class="text-xs text-gray-400">生成文件（点击下载）</p>
                  <!-- download-only：本页未挂载 AttachmentPreviewPanel，图片/Office 一律直接下载 -->
                  <DownloadFileCard v-for="file in executionDownloadableFiles(row)" :key="file.file_id" :file="file" download-only />
                </div>
                <details v-if="executionProgress(row).length" class="text-sm">
                  <summary class="cursor-pointer select-none text-xs text-gray-500">执行过程（{{ executionProgress(row).length }} 步）</summary>
                  <ul class="mt-1 space-y-0.5">
                    <li v-for="(item, index) in executionProgress(row)" :key="index" class="text-xs text-gray-500 whitespace-pre-wrap">{{ item.content }}</li>
                  </ul>
                </details>
              </div>
              <p v-else-if="!executionBodyText(row) && row.status === 'completed' && !executionImages(row).length" class="mt-1.5 text-sm text-gray-400">（本次执行无文本与图片输出）</p>
            </div>
            <!-- 按需加载更早（评审 P2-11：不再一次性拉全量） -->
            <div v-if="runnersHasMore" class="px-4 py-3 text-center">
              <BaseButton intent="secondary" size="sm" :disabled="loadingOlderRunners" @click="loadOlderRunners">
                {{ loadingOlderRunners ? '加载中…' : '加载更早的执行记录' }}
              </BaseButton>
            </div>
          </div>
        </div>

        <!-- 通知投递状态（设计 §8.4；事件四态：失败可重试 / 待展开 / 空 / 列表，评审 P2-13 + 二轮第 5 项） -->
        <div class="bg-white border border-gray-200 rounded-lg">
          <div class="px-4 py-3 border-b border-gray-100 flex items-center gap-2">
            <h2 class="text-sm font-medium text-gray-700">通知投递</h2>
            <span class="text-xs text-gray-400">当前规则：{{ notificationRuleLabel }}</span>
          </div>
          <div v-if="deliveriesState === 'loading' && !deliveries.length" class="p-6 text-center text-sm text-gray-500">正在加载通知记录…</div>
          <div v-else-if="deliveriesState === 'error'" class="p-6 text-center text-sm">
            <p class="text-danger-600" role="alert">通知记录加载失败，请稍后重试。</p>
            <BaseButton class="mt-3" intent="secondary" size="sm" @click="retryDeliveries">重试</BaseButton>
          </div>
          <div v-else-if="!deliveries.length" class="p-6 text-center text-sm text-gray-500">暂无通知记录</div>
          <div v-else class="divide-y divide-gray-100">
            <div v-for="event in deliveries" :key="event.notification_ref" class="px-4 py-2.5 text-sm">
              <div class="flex items-center gap-2 flex-wrap">
                <BaseBadge :intent="deliveryEventIntent(event.event_type)" size="sm">{{ deliveryEventLabel(event.event_type) || event.event_type }}</BaseBadge>
                <span class="text-gray-600">{{ formatDateTime(event.created_at) }}</span>
              </div>
              <div v-if="event.deliveries.length" class="mt-1 space-y-0.5">
                <p v-for="d in event.deliveries" :key="d.target_id" class="text-xs text-gray-500">
                  {{ deliveryChannelLabel(d) }} · {{ deliveryStateLabel(d.state) || d.state }}
                  <span v-if="d.attempts > 1">（第 {{ d.attempts }} 次尝试）</span>
                  <span v-if="d.error_class" class="text-danger-600"> · {{ d.error_class }}</span>
                </p>
              </div>
              <!-- 待展开（二轮评审第 5 项）：消息已生成但异步展开未完成，deliveries=[]
                   不代表未配置外发；expanded 且无 deliveries 才是"确认无外发" -->
              <p v-else-if="event.expansion_state === 'pending'" class="mt-1 text-xs text-gray-400">通知投递准备中…</p>
              <p v-else class="mt-1 text-xs text-gray-400">站内信（未配置外发目标）</p>
            </div>
            <p v-if="hasPendingDeliveries" class="px-4 py-2 text-xs text-gray-400">有投递进行中，页面打开期间约每 30 秒自动刷新。</p>
          </div>
        </div>
      </div>
    </div>

    <!-- 改期弹窗 -->
    <BaseModal v-model="rescheduleModal.show" title="修改调度时间" size="sm" accessible>
      <div class="space-y-3">
        <div>
          <label :for="`${uid}-schedule-type`" class="block text-sm text-gray-600 mb-1">调度类型</label>
          <BaseSelect :id="`${uid}-schedule-type`" v-model="rescheduleModal.scheduleType" size="sm">
            <option value="daily">每天</option>
            <option value="weekly">每周</option>
            <option value="monthly">每月</option>
            <option value="interval">间隔执行</option>
            <option value="once">一次性</option>
          </BaseSelect>
        </div>
        <div v-if="rescheduleModal.scheduleType === 'daily'" class="grid grid-cols-2 gap-3">
          <div>
            <label :for="`${uid}-daily-hour`" class="block text-sm text-gray-600 mb-1">小时</label>
            <BaseSelect :id="`${uid}-daily-hour`" v-model="rescheduleModal.hour" size="sm">
              <option v-for="h in 24" :key="h - 1" :value="String(h - 1)">{{ String(h - 1).padStart(2, '0') }} 时</option>
            </BaseSelect>
          </div>
          <div>
            <label :for="`${uid}-daily-minute`" class="block text-sm text-gray-600 mb-1">分钟</label>
            <BaseSelect :id="`${uid}-daily-minute`" v-model="rescheduleModal.minute" size="sm">
              <option v-for="m in 60" :key="m - 1" :value="String(m - 1)">{{ String(m - 1).padStart(2, '0') }} 分</option>
            </BaseSelect>
          </div>
        </div>
        <div v-if="rescheduleModal.scheduleType === 'weekly'" class="space-y-3">
          <div>
            <label :for="`${uid}-weekday`" class="block text-sm text-gray-600 mb-1">星期</label>
            <BaseSelect :id="`${uid}-weekday`" v-model="rescheduleModal.dayOfWeek" size="sm">
              <option value="mon">周一</option><option value="tue">周二</option><option value="wed">周三</option>
              <option value="thu">周四</option><option value="fri">周五</option><option value="sat">周六</option>
              <option value="sun">周日</option>
            </BaseSelect>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label :for="`${uid}-weekly-hour`" class="block text-sm text-gray-600 mb-1">小时</label>
              <BaseSelect :id="`${uid}-weekly-hour`" v-model="rescheduleModal.hour" size="sm">
                <option v-for="h in 24" :key="h - 1" :value="String(h - 1)">{{ String(h - 1).padStart(2, '0') }} 时</option>
              </BaseSelect>
            </div>
            <div>
              <label :for="`${uid}-weekly-minute`" class="block text-sm text-gray-600 mb-1">分钟</label>
              <BaseSelect :id="`${uid}-weekly-minute`" v-model="rescheduleModal.minute" size="sm">
                <option v-for="m in 60" :key="m - 1" :value="String(m - 1)">{{ String(m - 1).padStart(2, '0') }} 分</option>
              </BaseSelect>
            </div>
          </div>
        </div>
        <div v-if="rescheduleModal.scheduleType === 'monthly'" class="space-y-3">
          <div>
            <label :for="`${uid}-month-day`" class="block text-sm text-gray-600 mb-1">日期</label>
            <!-- 1-31 完整提供：无对应日期的月份由 cron 调度语义自然跳过（评审 P2-6） -->
            <BaseSelect :id="`${uid}-month-day`" v-model="rescheduleModal.day" size="sm">
              <option v-for="d in 31" :key="d" :value="String(d)">{{ d }} 日</option>
            </BaseSelect>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label :for="`${uid}-monthly-hour`" class="block text-sm text-gray-600 mb-1">小时</label>
              <BaseSelect :id="`${uid}-monthly-hour`" v-model="rescheduleModal.hour" size="sm">
                <option v-for="h in 24" :key="h - 1" :value="String(h - 1)">{{ String(h - 1).padStart(2, '0') }} 时</option>
              </BaseSelect>
            </div>
            <div>
              <label :for="`${uid}-monthly-minute`" class="block text-sm text-gray-600 mb-1">分钟</label>
              <BaseSelect :id="`${uid}-monthly-minute`" v-model="rescheduleModal.minute" size="sm">
                <option v-for="m in 60" :key="m - 1" :value="String(m - 1)">{{ String(m - 1).padStart(2, '0') }} 分</option>
              </BaseSelect>
            </div>
          </div>
        </div>
        <div v-if="rescheduleModal.scheduleType === 'interval'">
          <!-- §15：间隔只接受整数小时（不低于系统最短间隔），后端为最终校验 -->
          <label :for="`${uid}-interval-hours`" class="block text-sm text-gray-600 mb-1">间隔（整数小时）</label>
          <BaseInput :id="`${uid}-interval-hours`" v-model="rescheduleModal.intervalHours" type="number" min="1" step="1" size="sm" placeholder="如 2" />
          <p class="mt-1 text-xs text-gray-400">须为不低于 {{ limits?.min_interval_hours ?? 1 }} 的整数小时，不支持分钟或小数。</p>
          <p v-if="intervalHoursError" class="mt-1 text-xs text-danger-600">{{ intervalHoursError }}</p>
        </div>
        <!-- §15 时间窗：仅循环类型可设起止（once 隐藏）；可只填一端 -->
        <div v-if="rescheduleModal.scheduleType !== 'once'" class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label :for="`${uid}-window-start`" class="block text-sm text-gray-600 mb-1">有效期起（可选）</label>
            <input
              :id="`${uid}-window-start`"
              v-model="rescheduleModal.windowStart"
              type="datetime-local"
              step="1"
              class="w-full px-3 py-2 bg-gray-50 border border-gray-300 rounded-lg text-sm text-gray-800 focus:outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
            />
          </div>
          <div>
            <label :for="`${uid}-window-end`" class="block text-sm text-gray-600 mb-1">有效期止（可选）</label>
            <input
              :id="`${uid}-window-end`"
              v-model="rescheduleModal.windowEnd"
              type="datetime-local"
              step="1"
              class="w-full px-3 py-2 bg-gray-50 border border-gray-300 rounded-lg text-sm text-gray-800 focus:outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
            />
            <p class="mt-1 text-xs text-gray-400">含起点、不含终点；窗口结束后任务自动完成，仍可「立即执行一次」。</p>
          </div>
        </div>
        <div v-if="rescheduleModal.scheduleType === 'once'">
          <label :for="`${uid}-run-at`" class="block text-sm text-gray-600 mb-1">执行时间（须为未来时间）</label>
          <input
            :id="`${uid}-run-at`"
            v-model="rescheduleModal.runAt"
            type="datetime-local"
            class="w-full px-3 py-2 bg-gray-50 border border-gray-300 rounded-lg text-sm text-gray-800 focus:outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
          />
        </div>
        <p class="text-xs text-gray-400">仅调度规则变更会重算下次执行时间；任务内容不受影响。已暂停任务改期后经「恢复」生效。</p>
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <BaseButton intent="secondary" size="sm" @click="rescheduleModal.show = false">取消</BaseButton>
          <BaseButton size="sm" :disabled="actionBusy" @click="handleReschedule">{{ actionBusy ? '保存中…' : '确认修改' }}</BaseButton>
        </div>
      </template>
    </BaseModal>

    <!-- 编辑任务内容弹窗（不动调度，设计 §6 编辑与调度解耦） -->
    <BaseModal v-model="editModal.show" title="编辑任务" size="md" accessible>
      <div class="space-y-3">
        <div>
          <label :for="`${uid}-edit-name`" class="block text-sm text-gray-600 mb-1">任务名称</label>
          <BaseInput :id="`${uid}-edit-name`" v-model="editModal.name" size="sm" placeholder="任务名称" />
        </div>
        <div>
          <label :for="`${uid}-edit-prompt`" class="block text-sm text-gray-600 mb-1">任务定义（自包含任务描述）</label>
          <textarea
            :id="`${uid}-edit-prompt`"
            v-model="editModal.taskPrompt"
            rows="5"
            class="w-full px-3 py-2 bg-gray-50 border border-gray-300 rounded-lg text-sm text-gray-800 focus:outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
          ></textarea>
        </div>
        <div>
          <label :for="`${uid}-edit-attachment-input`" class="block text-sm text-gray-600 mb-1">附件（创建时固化的受权资源引用）</label>
          <div v-if="editModal.attachments.length || uploadingPlaceholders.length" class="space-y-1">
            <div v-for="(att, index) in editModal.attachments" :key="att.file_id" class="flex items-center gap-2 text-sm text-gray-600 bg-gray-50 rounded px-2.5 py-1.5">
              <svg class="w-4 h-4 flex-shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" /></svg>
              <span class="flex-1 truncate">{{ att.name }}</span>
              <button class="text-danger-600 hover:text-danger-700 text-xs" @click="editModal.attachments.splice(index, 1)">移除</button>
            </div>
            <!-- 上传中占位（评审二轮第 4 项）：不可移除的传输中条目；失败自动消失 -->
            <div v-for="ph in uploadingPlaceholders" :key="ph.key" class="flex items-center gap-2 text-sm text-gray-400 bg-gray-50 rounded px-2.5 py-1.5">
              <svg class="w-4 h-4 flex-shrink-0 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
              </svg>
              <span class="flex-1 truncate">{{ ph.name }}（上传中…）</span>
            </div>
          </div>
          <p v-else class="text-xs text-gray-400">无附件</p>
          <!-- 复用聊天上传能力（POST /api/upload，无会话绑定约束）：上传后组装 AttachmentRef（评审 P2-7） -->
          <div class="mt-2">
            <input :id="`${uid}-edit-attachment-input`" type="file" class="hidden" :disabled="attachmentUploading" @change="handleAddAttachment" />
            <BaseButton intent="secondary" size="sm" :disabled="attachmentUploading"
              @click="triggerAttachmentPick">{{ attachmentUploading ? '上传中…' : '添加附件' }}</BaseButton>
          </div>
        </div>
        <div>
          <label :for="`${uid}-edit-rule`" class="block text-sm text-gray-600 mb-1">通知规则</label>
          <BaseSelect :id="`${uid}-edit-rule`" v-model="editModal.notificationRule" size="sm" :disabled="rulesLoading">
            <option value="">默认（站内信 + 个人偏好渠道）</option>
            <option v-for="rule in taskRuleOptions" :key="rule.rule_id" :value="rule.rule_id">
              {{ ruleLabel(rule) }}
            </option>
          </BaseSelect>
          <p class="mt-1 text-xs text-gray-400">
            通知事件：任务完成 / 失败 / 暂停 / 需要处理（以所选规则的事件范围为准）；已生成事件不受后续修改影响。规则由租户管理员在通知规则页配置。
          </p>
        </div>
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <BaseButton intent="secondary" size="sm" @click="editModal.show = false">取消</BaseButton>
          <!-- 上传中禁用保存（占位项非合法 AttachmentRef，评审二轮第 4 项） -->
          <BaseButton size="sm" :disabled="actionBusy || attachmentUploading" @click="handleEditSave">{{ actionBusy ? '保存中…' : attachmentUploading ? '附件上传中…' : '保存' }}</BaseButton>
        </div>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
/**
 * 任务执行视图（定时任务 Runner 化 Phase 5，设计 §6）
 *
 * - 头部/操作区/执行记录/通知投递四区；无聊天输入框；
 * - 执行记录数据源 = Runner 网关 source='scheduled' 列表（agent_runners 执行事实），
 *   首页 20 条 + 「加载更早」按需分页（评审 P2-11）；
 * - 当前执行（queued/running/waiting 等非终态）经 RunnerClient.observe 订阅事件流实时刷新；
 * - 打开期间 10s 轻量轮询发现新 runner 并同步任务行（评审 P1-2）；
 * - 页面上下文 = tenantId+taskId（generation 隔离，评审 P1-1）：所有异步完成时校验
 *   上下文仍有效，慢响应/旧弹窗保存不再写入另一任务；
 * - 任务操作走 /api/scheduled-tasks（pause/resume/trigger/schedule/definition/delete）；
 * - 普通用户无回复/继续控制权限（矩阵），本视图不提供任何输入入口。
 */
import { computed, inject, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowReactive, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import ImageGallery from '@/components/ui/ImageGallery.vue'
import DownloadFileCard from '@/components/DownloadFileCard.vue'
import { RunnerClient, runnerObservationComplete, runnerTerminal, type RunnerView } from '@/api/runner'
import {
  describeSchedule, getScheduledTask, getScheduledTaskLimits, pauseScheduledTask,
  resumeScheduledTask, deleteScheduledTask, triggerScheduledTask,
  updateScheduledTaskSchedule, updateScheduledTaskDefinition, notifyScheduledTasksChanged,
  ScheduledTaskRequestError, SCHEDULED_PAUSE_REASON_LABELS, SCHEDULED_STATUS_LABELS,
  type ScheduledTask, type ScheduledTaskAttachment, type ScheduledTaskLimits,
  type ScheduledTaskScheduleType,
} from '@/api/scheduledTask'
import { notificationsAPI, type TaskDeliveryEvent, type TaskNotifyRule } from '@/api/notifications'
import { notificationRequest } from '@/notifications/api'
import { uploadFile } from '@/api/agent'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { useSubagentList } from '@/composables/useSubagentList'
import { renderMarkdown } from '@/utils/markdown'
import { progressMessages } from '@/utils/runnerMessages'
import type { ImageRef } from '@/types'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const { admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, getAuthHeader } = useTenantAuth()
const { availableSubagents, loadAvailableSubagents } = useSubagentList()
// 侧栏开关走 TenantLayout 提供的既有契约（AppHeader toggle-sidebar，评审 P2-10）
const toggleSidebar = inject<() => void>('toggleSidebar', () => {})

const tenantId = computed(() => String(route.params.tenant_id || ''))
const taskId = computed(() => String(route.params.task_id || ''))
/** 弹窗表单 id 前缀（多实例/同页多弹窗不冲突） */
const uid = 'task-plan'

const headerUser = computed(() => tenantAdmin.value ? {
  user_id: tenantAdmin.value.user_id, username: tenantAdmin.value.username,
} : null)

const task = ref<ScheduledTask | null>(null)
const loading = ref(true)
const error = ref('')
const actionBusy = ref(false)
const deliveries = ref<TaskDeliveryEvent[]>([])
const deliveriesState = ref<'loading' | 'error' | 'loaded'>('loading')
/** 调度约束阈值（§15：最短间隔/活跃上限即时提示，后端为最终校验） */
const limits = ref<ScheduledTaskLimits | null>(null)

/** §15 评审 P2：通知直达落地文案（manual_rejected 的 reason 等） */
const noticeBanner = ref<{ title: string; body: string } | null>(null)

/** §15：active 与因时间窗自动完成的循环任务可手动执行；once 终态不可 */
const canManualTrigger = computed(() => !!task.value && (task.value.status === 'active'
  || (task.value.status === 'completed' && task.value.schedule_type !== 'once' && !!task.value.ends_at)))

// ==================== 页面上下文隔离（评审 P1-1） ====================

/** 上下文 = tenantId+taskId 的当次快照；每次切换生成新对象，旧异步凭引用相等即失效 */
interface PageContext { tenantId: string; taskId: string }
let ctx: PageContext = { tenantId: '', taskId: '' }
let disposed = false
const isLive = (c: PageContext) => !disposed && c === ctx

function bumpContext(): PageContext {
  ctx = { tenantId: tenantId.value, taskId: taskId.value }
  return ctx
}

// ---- Runner 网关（source='scheduled' 只读 + cancel 专用端点） ----
const runnerClient = new RunnerClient()
/** 执行行唯一缓存（评审 P2-3 双缓存收敛）：键 runner_id，按 revision 接纳最新投影 */
const runners = shallowReactive(new Map<string, RunnerView>())
const expanded = reactive({} as Record<string, boolean>)

/** 首页条数（网关 limit 1-100；评审 P2-11 按需分页） */
const RUNNER_PAGE_LIMIT = 20
const loadingRunners = ref(false)
const loadingOlderRunners = ref(false)
const runnersHasMore = ref(false)
const runnersCursor = ref<string | null>(null)

const executions = computed<RunnerView[]>(() =>
  [...runners.values()].sort((a, b) => (a.accepted_at < b.accepted_at ? 1 : -1)))
const activeRunners = computed(() => executions.value.filter(row => !runnerTerminal(row.status)))

function headers(): Record<string, string> { return getAuthHeader() }

function statusIntent(status: string): 'success' | 'warning' | 'neutral' | 'info' {
  if (status === 'active') return 'success'
  if (status === 'paused') return 'warning'
  if (status === 'completed') return 'info'
  return 'neutral'
}
function statusText(row: ScheduledTask): string {
  const base = SCHEDULED_STATUS_LABELS[row.status] || row.status
  if (row.status === 'paused' && row.pause_reason) {
    const reason = SCHEDULED_PAUSE_REASON_LABELS[row.pause_reason]
    return reason ? `${base}（${reason}）` : base
  }
  return base
}
const lastStatusText = computed(() => {
  const value = task.value?.last_status
  if (!value) return '无记录'
  return runnerStatusLabel[value] || value
})

const profileLabel = computed(() => {
  const profile = task.value?.profile_id
  if (!profile || profile === 'main') return '主智能体'
  const matched = availableSubagents.value.find(agent =>
    agent.agent_id === profile || agent.subagent_type === profile || agent.instance_id === profile)
  return matched?.display_name || matched?.name || profile
})

const notificationRuleLabel = computed(() => {
  const ruleId = task.value?.notification_rule_id
  if (ruleId) {
    const rule = taskRuleOptions.value.find(item => item.rule_id === ruleId)
    return rule ? ruleLabel(rule) : `已选用规则（${ruleId.slice(0, 8)}…）`
  }
  // 旧列仅存量任务兜底（写入口已下线）：继续按原渠道投递，不在本页编辑
  if (task.value?.notification_target_id) return '已配置旧通知目标（继续按原渠道投递）'
  return '默认（站内信 + 个人偏好渠道）'
})

// ==================== 执行记录展示 ====================

const runnerStatusLabel: Record<string, string> = {
  queued: '排队中', running: '执行中', waiting: '等待中', paused: '已暂停',
  interrupted: '已中断', finalizing: '收尾中', completed: '已完成', failed: '失败', cancelled: '已取消',
}
const runnerErrorLabels: Record<string, string> = {
  LOCAL_EXECUTION_INTERRUPTED: '本机操作已中断，未收到结果。',
  CREDIT_BLOCKED: '积分不足', NO_CREDIT: '积分不足',
  PROFILE_FORBIDDEN: '数字员工不可用',
  TENANT_FORBIDDEN: '租户不可用或已过期', TENANT_UNAVAILABLE: '租户不可用或已过期', TENANT_EXPIRED: '租户不可用或已过期',
  RUNNER_ATTEMPT_EXPIRED: '执行超时中断',
  RUNNER_STORAGE_UNAVAILABLE: '执行记录暂不可用',
  SCHEDULED_TASK_NOT_ACCEPTABLE: '任务不在可执行状态',
  TASK_SESSION_CHAT_FORBIDDEN: '任务会话不接受对话输入',
}

function runnerStatusIntent(status: string): 'success' | 'danger' | 'warning' | 'info' | 'neutral' {
  if (status === 'completed') return 'success'
  if (status === 'failed') return 'danger'
  if (['waiting', 'paused', 'interrupted', 'finalizing'].includes(status)) return 'warning'
  if (['queued', 'running'].includes(status)) return 'info'
  return 'neutral'
}
function triggerLabel(row: RunnerView): string {
  return row.scheduled?.trigger === 'manual' ? '手动' : '定时'
}
function triggerIntent(row: RunnerView): 'primary' | 'neutral' {
  return row.scheduled?.trigger === 'manual' ? 'primary' : 'neutral'
}
function creditCost(row: RunnerView): number | undefined {
  const value = row.result?.credit_cost
  return typeof value === 'number' ? value : undefined
}
function runnerErrorText(row: RunnerView): string {
  const code = row.result?.error_code
  if (!code) return '执行失败'
  return runnerErrorLabels[code] || `执行失败（${code}）`
}
/** 执行输出主体：终态读 result.output，运行中读快照 output */
function executionBodyText(row: RunnerView): string {
  if (runnerTerminal(row.status)) return row.result?.output || ''
  return row.snapshot?.output || ''
}
function executionSummary(row: RunnerView): string {
  const text = executionBodyText(row).trim()
  return text.length > 200 ? text.slice(0, 200) + '…' : text
}
/** 输出图片（终态 result / 运行中快照，评审 P2-4），复用聊天页 ImageGallery 展示 */
function executionImages(row: RunnerView): ImageRef[] {
  return row.result?.images || row.snapshot?.images || []
}
/** 可下载文件（agent 生成的附件）：从 runner 公开快照读取 downloadableFiles */
interface DownloadableFile { file_id: string; file_name: string; file_size: number; download_url: string; mime_type?: string }
function executionDownloadableFiles(row: RunnerView): DownloadableFile[] {
  // 优先从公开快照的 downloadableFiles 读取（新执行的正常路径）
  const files = row.snapshot?.downloadableFiles || row.result?.downloadableFiles
  if (Array.isArray(files) && files.length > 0) return files as DownloadableFile[]
  // 兜底：从 progressMessages 的 tool_result 事件中提取（旧执行的回填路径——
  // 后端修复前 downloada bleFiles 未捕获，但 progressMessages 里有 result.files）
  const messages = row.snapshot?.progressMessages || row.result?.assistant_metadata?.progressMessages
  if (!Array.isArray(messages)) return []
  const extracted: DownloadableFile[] = []
  const seen = new Set<string>()
  for (const msg of messages) {
    if (msg?.type !== 'tool_result' || msg?.success !== true) continue
    const result = msg?.result
    if (!result || typeof result !== 'object') continue
    const rawFiles = (result as Record<string, unknown>).files
    if (!Array.isArray(rawFiles)) continue
    for (const raw of rawFiles) {
      if (!raw || typeof raw !== 'object') continue
      const f = raw as Record<string, unknown>
      const fileId = f.file_id
      if (typeof fileId !== 'string' || !fileId || seen.has(fileId)) continue
      if (f.visible === false) continue
      seen.add(fileId)
      extracted.push({
        file_id: fileId,
        file_name: String(f.file_name || f.download_file_name || '未命名文件'),
        file_size: Number(f.file_size) || 0,
        download_url: String(f.download_url || `/api/files/${fileId}/download`),
        mime_type: String(f.mime_type || ''),
      })
    }
  }
  return extracted
}
/** 执行过程消息（评审 P2-4）：复用 runnerMessages 的公开投影映射 */
function executionProgress(row: RunnerView) {
  const display: { progressMessages?: Record<string, unknown>[] } = { ...(row.result?.assistant_metadata || {}), ...row.snapshot }
  return progressMessages(display.progressMessages, Date.parse(row.accepted_at) || Date.now())
}
function hasExpandableDetail(row: RunnerView): boolean {
  return !!(executionBodyText(row) || executionImages(row).length || executionDownloadableFiles(row).length || executionProgress(row).length)
}
function renderedOutput(row: RunnerView): string {
  return renderMarkdown(executionBodyText(row))
}

function toggleExpand(runnerId: string) {
  expanded[runnerId] = !expanded[runnerId]
}
/** 等待中执行的等待说明（快照 waiting） */
const waitingText = computed(() => {
  for (const row of activeRunners.value) {
    const wait = row.snapshot?.waiting
    if (wait?.question) return wait.question
  }
  return ''
})

// ==================== 数据加载 ====================

async function loadTask(c: PageContext): Promise<void> {
  try {
    const value = await getScheduledTask(c.taskId)
    if (!isLive(c)) return
    task.value = value
    error.value = ''
  } catch (e) {
    if (!isLive(c)) return
    error.value = e instanceof ScheduledTaskRequestError && e.status === 404
      ? '任务不存在或已被删除。'
      : '任务加载失败，请稍后重试。'
  }
  if (isLive(c)) scheduleDiscovery(c)
}

/** 单缓存接纳规则（评审 P2-3；二轮修正）：以 view_revision（公开展示版本）为主
 * 判据——cancel_requested 等公开状态变更只递增 view_revision、不一定递增 revision，
 * 仅比 revision 会把 cancel_requested=true 的新投影误当旧值拒绝（回退缺陷）。
 * 同 view_revision 时以 revision 作次要确定性条件（与 RunnerClient.observe 的
 * 版本语义一致：view_revision >= 且 control_revision >= 才算新投影）。 */
function applyRunnerView(view: RunnerView) {
  const existing = runners.get(view.runner_id)
  if (existing && Number.isFinite(existing.view_revision)) {
    if (view.view_revision < existing.view_revision) return
    if (view.view_revision === existing.view_revision
      && Number.isFinite(existing.revision) && view.revision < existing.revision) return
  }
  runners.set(view.runner_id, view)
}

/**
 * 非终态执行订阅事件流（SSE 可用时事件推送，否则权威轮询）实时刷新。
 * 观察结束判定 = runnerObservationComplete（terminal && settled && 非 queued/claimed/
 * running 的 local_verification）：已 completed 但 settlement 未完成的行继续观察，
 * 费用后续自动更新（评审 P2-3）。
 */
function maybeObserve(c: PageContext, row: RunnerView) {
  if (runnerObservationComplete(row)) return
  runnerClient.observe(row.runner_id, headers(), view => {
    if (!isLive(c)) return
    const previous = runners.get(view.runner_id)
    applyRunnerView(view)
    if (previous && !runnerTerminal(previous.status) && runnerTerminal(view.status)) {
      // 统计与通知投递随后更新
      scheduleTerminalRefresh(c)
    }
  }, issue => {
    if (isLive(c)) console.warn('[TaskPlanView] runner 观察失败:', issue)
  })
}

async function loadRunners(c: PageContext, opts: { cursor?: string; append?: boolean } = {}): Promise<void> {
  const session = task.value?.session_id
  if (!session || !isLive(c)) return
  if (opts.append) loadingOlderRunners.value = true
  else loadingRunners.value = true
  try {
    const page = await runnerClient.list(session, headers(), opts.cursor, 'scheduled', RUNNER_PAGE_LIMIT)
    if (!isLive(c)) return
    for (const row of [...page.runners, ...page.active_runners]) applyRunnerView(row)
    runnersHasMore.value = page.has_more === true && !!page.next_cursor
    runnersCursor.value = runnersHasMore.value ? page.next_cursor : null
    for (const row of runners.values()) maybeObserve(c, row)
  } catch {
    if (isLive(c)) toast.error('执行记录加载失败，请稍后刷新。')
  } finally {
    if (isLive(c)) {
      loadingRunners.value = false
      loadingOlderRunners.value = false
    }
  }
}

function loadOlderRunners() {
  const c = ctx
  if (runnersCursor.value) void loadRunners(c, { cursor: runnersCursor.value, append: true })
}

function reloadRunners() {
  const c = ctx
  runners.clear()
  runnersHasMore.value = false
  runnersCursor.value = null
  void loadRunners(c)
}

let refreshTimer: ReturnType<typeof setTimeout> | undefined
function scheduleTerminalRefresh(c: PageContext) {
  clearTimeout(refreshTimer)
  refreshTimer = setTimeout(() => {
    if (!isLive(c)) return
    void loadTask(c)
    void loadDeliveries(c)
    // 统计/状态变化同步侧栏任务计划分组（评审 P2-5）
    notifyScheduledTasksChanged()
  }, 2000)
}

// ==================== 新执行发现轮询（评审 P1-2） ====================

const DISCOVERY_INTERVAL_MS = 10_000
const TRIGGER_PROBE_INTERVAL_MS = 2_000
const TRIGGER_PROBE_MAX = 8
let discoveryTimer: ReturnType<typeof setTimeout> | undefined
let triggerProbeTimer: ReturnType<typeof setTimeout> | undefined
let triggerProbesLeft = 0

/** 轮询窗口：任务可执行（active）或仍有非终态执行；否则停止（离开页面/卸载同样停止） */
function shouldDiscover(): boolean {
  return !!task.value && (task.value.status === 'active' || activeRunners.value.length > 0)
}

function scheduleDiscovery(c: PageContext) {
  clearTimeout(discoveryTimer)
  discoveryTimer = undefined
  if (!isLive(c) || !shouldDiscover()) return
  discoveryTimer = setTimeout(() => void pollDiscovery(c), DISCOVERY_INTERVAL_MS)
}

async function pollDiscovery(c: PageContext): Promise<void> {
  if (!isLive(c)) return
  if (shouldDiscover()) {
    const session = task.value?.session_id
    if (session) {
      try {
        const page = await runnerClient.list(session, headers(), undefined, 'scheduled', RUNNER_PAGE_LIMIT)
        if (!isLive(c)) return
        for (const row of [...page.runners, ...page.active_runners]) {
          applyRunnerView(row)
          maybeObserve(c, row)
        }
      } catch { /* 下轮再试，不打断周期 */ }
    }
    try {
      const value = await getScheduledTask(c.taskId)
      if (!isLive(c)) return
      const previous = task.value
      task.value = value
      if (previous && (previous.status !== value.status || previous.next_due_at !== value.next_due_at
        || previous.total_runs !== value.total_runs)) {
        notifyScheduledTasksChanged()
      }
    } catch { /* 任务行刷新失败不影响执行列表 */ }
  }
  scheduleDiscovery(c)
}

/** 手动触发后的短周期探测：2s×最多 8 次，发现新 runner 即停（评审 P1-2，替代固定 3s 猜测） */
function startTriggerProbe(c: PageContext) {
  triggerProbesLeft = TRIGGER_PROBE_MAX
  clearTimeout(triggerProbeTimer)
  const probe = async () => {
    if (!isLive(c) || triggerProbesLeft <= 0) return
    triggerProbesLeft -= 1
    const session = task.value?.session_id
    if (!session) return
    try {
      const page = await runnerClient.list(session, headers(), undefined, 'scheduled', RUNNER_PAGE_LIMIT)
      if (!isLive(c)) return
      let fresh = false
      for (const row of [...page.runners, ...page.active_runners]) {
        if (!runners.has(row.runner_id)) fresh = true
        applyRunnerView(row)
        maybeObserve(c, row)
      }
      if (fresh) {
        triggerProbesLeft = 0
        void refreshTaskRow(c)
        return
      }
    } catch { /* 继续探测 */ }
    triggerProbeTimer = setTimeout(() => void probe(), TRIGGER_PROBE_INTERVAL_MS)
  }
  triggerProbeTimer = setTimeout(() => void probe(), TRIGGER_PROBE_INTERVAL_MS)
}

async function refreshTaskRow(c: PageContext) {
  try {
    const value = await getScheduledTask(c.taskId)
    if (!isLive(c)) return
    task.value = value
  } catch { /* 非关键刷新 */ }
}

// ==================== 通知投递（四态 + 非终态投递轮询，评审 P2-13；二轮修正） ====================
// 事件级四态：有投递记录 / 待展开（expansion_state=pending，消息已生成但异步
// 展开未完成，不能断言"未配置外发"）/ 确认无外发（expanded 且无 deliveries）/
// 加载失败（区块级）。待展开与 pending/sending 投递都纳入 30s 自动刷新。
const hasPendingDeliveries = computed(() =>
  deliveries.value.some(event => event.expansion_state === 'pending'
    || event.deliveries.some(d => d.state === 'pending' || d.state === 'sending')))

let deliveriesTimer: ReturnType<typeof setInterval> | undefined

async function loadDeliveries(c: PageContext): Promise<void> {
  if (!deliveries.value.length) deliveriesState.value = 'loading'
  try {
    const summary = await notificationsAPI.taskDeliveries(c.taskId)
    if (!isLive(c)) return
    deliveries.value = summary?.events || []
    deliveriesState.value = 'loaded'
  } catch {
    // 无已有数据 → 显式错误 + 重试；刷新失败但已有数据 → 静默保留
    if (isLive(c) && !deliveries.value.length) deliveriesState.value = 'error'
  }
  syncDeliveriesTimer(c)
}

function retryDeliveries() {
  deliveries.value = []
  deliveriesState.value = 'loading'
  void loadDeliveries(ctx)
}

/** 存在非终态投递（pending/sending）且页面可见 → 30s 间隔刷新；全部终态/卸载/切页停止 */
function syncDeliveriesTimer(c: PageContext) {
  const need = isLive(c) && !document.hidden && hasPendingDeliveries.value
  if (need && deliveriesTimer === undefined) {
    deliveriesTimer = setInterval(() => {
      if (!isLive(c) || document.hidden) return
      if (!hasPendingDeliveries.value) { syncDeliveriesTimer(ctx); return }
      void loadDeliveries(ctx)
    }, 30_000)
  } else if (!need && deliveriesTimer !== undefined) {
    clearInterval(deliveriesTimer)
    deliveriesTimer = undefined
  }
}

function onVisibilityChange() {
  if (disposed || document.hidden) return
  // 可见性恢复：立即补一次投递刷新与新执行发现，缩短感知延迟
  if (deliveriesState.value === 'loaded' && hasPendingDeliveries.value) void loadDeliveries(ctx)
  if (shouldDiscover()) {
    clearTimeout(discoveryTimer)
    discoveryTimer = setTimeout(() => void pollDiscovery(ctx), 500)
  }
}

function deliveryEventLabel(eventType: string): string {
  const labels: Record<string, string> = {
    'task.completed': '任务完成', 'task.failed': '任务失败',
    'task.paused': '任务暂停', 'task.needs_action': '需要处理',
    'task.manual_rejected': '手动执行未成功',
  }
  return labels[eventType] || ''
}
function deliveryEventIntent(eventType: string): 'success' | 'danger' | 'warning' | 'neutral' {
  if (eventType === 'task.completed') return 'success'
  if (eventType === 'task.failed') return 'danger'
  if (eventType === 'task.paused' || eventType === 'task.needs_action'
    || eventType === 'task.manual_rejected') return 'warning'
  return 'neutral'
}
function deliveryChannelLabel(d: { channel: string; provider: string }): string {
  const labels: Record<string, string> = { web_push: '浏览器推送', wecom_bot: '企微机器人', dingtalk_bot: '钉钉机器人', feishu_bot: '飞书机器人' }
  return labels[d.provider] || labels[d.channel] || `${d.channel}/${d.provider}`
}
function deliveryStateLabel(state: string): string {
  const labels: Record<string, string> = { pending: '投递中', sending: '投递中', delivered: '已投递', failed: '投递失败' }
  return labels[state] || ''
}

// ==================== 页面装载与上下文重置（评审 P1-1） ====================

function stopTimers() {
  clearTimeout(refreshTimer); refreshTimer = undefined
  clearTimeout(discoveryTimer); discoveryTimer = undefined
  clearTimeout(triggerProbeTimer); triggerProbeTimer = undefined
  triggerProbesLeft = 0
  if (deliveriesTimer !== undefined) { clearInterval(deliveriesTimer); deliveriesTimer = undefined }
}

function resetPage() {
  runnerClient.detach()
  stopTimers()
  runners.clear()
  runnersHasMore.value = false
  runnersCursor.value = null
  loadingRunners.value = false
  loadingOlderRunners.value = false
  for (const key of Object.keys(expanded)) delete expanded[key]
  deliveries.value = []
  deliveriesState.value = 'loading'
  editModal.show = false
  rescheduleModal.show = false
  // 弹窗代际递增：重置后遗留的上传回调全部失效（评审二轮第 4 项）
  editModalGeneration += 1
  uploadingPlaceholders.value = []
  // 通知规则选项随页面上下文清空（评审二轮第 7 项：切租户/任务不残留）
  taskRuleOptions.value = []
  rulesLoading.value = false
  // 阈值随租户上下文重取（§15：上限含租户覆盖值，切租户不残留）
  limits.value = null
  actionBusy.value = false
  task.value = null
  error.value = ''
}

async function loadAll(c: PageContext) {
  loading.value = true
  try {
    // §15 阈值并行加载（失败/未完成不阻塞页面，表单回退默认提示值 1）
    if (!limits.value) {
      void getScheduledTaskLimits().then(value => {
        if (isLive(c)) limits.value = value
      }).catch(() => { /* 后端校验为准 */ })
    }
    await loadTask(c)
    if (!isLive(c)) return
    if (task.value) {
      void loadAvailableSubagents(true)
      // capabilities 发现（SSE 事件流订阅能力；失败降级为权威轮询，不影响只读展示）
      try {
        runnerClient.installCapabilities(await runnerClient.capabilities(headers()))
      } catch { /* 轮询兜底 */ }
      await Promise.all([loadRunners(c), loadDeliveries(c)])
      if (!isLive(c)) return
      // §15.5 通知直达：定位查询参数指定的执行行（不在首页 20 条时按 ID 单独读取）
      const focusRunnerId = typeof route.query.runner_id === 'string' ? route.query.runner_id : ''
      if (focusRunnerId) await focusRunnerFromQuery(c, focusRunnerId)
      // §15 评审 P2：notice_ref → 展示该通知文案（含手动执行被拒原因）
      const noticeRef = typeof route.query.notice_ref === 'string' ? route.query.notice_ref : ''
      if (noticeRef) void loadNoticeBanner(c, noticeRef)
      scheduleDiscovery(c)
    }
  } finally {
    if (isLive(c)) loading.value = false
  }
}

/** §15 评审 P2：按通知 ref 拉取站内信同款文案（经归属核验接口），校验
 * 资源确属当前任务后展示横幅；失败静默（不阻塞任务页）。 */
async function loadNoticeBanner(c: PageContext, noticeRef: string): Promise<void> {
  try {
    const result = await notificationRequest<{
      event_type: string
      resource?: { task_id?: string | null } | null
      title?: string
      body?: string
    }>(`/messages/${encodeURIComponent(noticeRef)}`, {}, headers())
    if (!isLive(c) || !result?.title || !result?.body) return
    if (result.resource?.task_id && result.resource.task_id !== c.taskId) return
    noticeBanner.value = { title: result.title, body: result.body }
  } catch { /* 通知已清理或无权读取：不展示横幅 */ }
}

/** §15.5 通知直达本次执行：按 runner_id 定位执行行。不在已加载列表（首页
 * 20 条之外）时经 Runner 单条读取接口获取，校验返回的 session_id 与任务
 * 会话一致后纳入列表、展开并滚动定位；无权限/不存在时如实反馈。 */
async function focusRunnerFromQuery(c: PageContext, runnerId: string): Promise<void> {
  if (!runners.has(runnerId)) {
    try {
      const view = await runnerClient.get(runnerId, headers())
      if (!isLive(c)) return
      if (view.session?.session_id !== task.value?.session_id) {
        toast.error('指定的执行记录不属于本任务。')
        return
      }
      applyRunnerView(view)
      maybeObserve(c, view)
    } catch {
      if (isLive(c)) toast.error('指定的执行记录不存在或暂不可用。')
      return
    }
  }
  if (!isLive(c)) return
  expanded[runnerId] = true
  await nextTick()
  document.getElementById(`runner-${runnerId}`)?.scrollIntoView({ block: 'center' })
}

// ==================== 任务操作 ====================

function actionErrorText(e: unknown, fallback: string): string {
  if (e instanceof ScheduledTaskRequestError) return e.detail || e.message || fallback
  return fallback
}

async function handlePause() {
  const c = ctx
  actionBusy.value = true
  try {
    await pauseScheduledTask(c.taskId)
    if (!isLive(c)) return
    toast.success('任务已暂停（已受理的执行会继续到结束）')
    notifyScheduledTasksChanged()
    await loadTask(c)
  } catch (e) {
    if (isLive(c)) toast.error(actionErrorText(e, '暂停失败，请稍后重试。'))
  } finally { if (isLive(c)) actionBusy.value = false }
}

async function handleResume() {
  const c = ctx
  actionBusy.value = true
  try {
    const result = await resumeScheduledTask(c.taskId)
    if (!isLive(c)) return
    // once 过宽限：恢复操作已将任务作废（expired）——如实展示回执（设计 §4.5）
    if (result?.expired) toast.warning(result.message || '该一次性任务已超过执行宽限，恢复操作已将其作废。')
    // §15：时间窗已结束：恢复操作已收尾（completed），不再自动执行
    else if (result?.completed) toast.warning(result.message || '该任务的执行时间窗已结束，恢复操作已将其收尾。')
    else toast.success('任务已恢复')
    notifyScheduledTasksChanged()
    await loadTask(c)
  } catch (e) {
    if (isLive(c)) toast.error(actionErrorText(e, '恢复失败，请稍后重试。'))
  } finally { if (isLive(c)) actionBusy.value = false }
}

async function handleTrigger() {
  const c = ctx
  actionBusy.value = true
  try {
    await triggerScheduledTask(c.taskId)
    if (!isLive(c)) return
    toast.success('已触发执行，稍后由调度循环提交，可在下方执行记录查看进度。')
    // 短周期探测新 runner 出现（2s×最多 8 次，出现即停），不依赖固定延迟猜测
    startTriggerProbe(c)
  } catch (e) {
    if (isLive(c)) toast.error(actionErrorText(e, '触发失败：任务不在可执行状态或已有待提交执行。'))
  } finally { if (isLive(c)) actionBusy.value = false }
}

async function handleDelete() {
  const c = ctx
  if (!window.confirm('确定删除该任务吗？任务会话与执行记录将一并删除，不可恢复。')) return
  actionBusy.value = true
  try {
    await deleteScheduledTask(c.taskId)
    if (!isLive(c)) return
    toast.success('任务已删除')
    notifyScheduledTasksChanged()
    router.push(`/t/${c.tenantId}/chat`)
  } catch (e) {
    // 409 = 有活跃 runner（删除守卫），引导稍后再试
    if (isLive(c)) toast.error(actionErrorText(e, '任务正在执行，请等当前执行结束后再试。'))
  } finally { if (isLive(c)) actionBusy.value = false }
}

/** 取消指定执行（行级/顶部唯一 active 入口共用）：runner cancel 专用端点（评审 P2-12） */
async function handleCancelRunner(runnerId: string) {
  const c = ctx
  actionBusy.value = true
  try {
    const updated = await runnerClient.cancel(runnerId, headers())
    if (!isLive(c)) return
    applyRunnerView(updated)
    toast.success('已请求取消，执行按取消核对语义收尾（状态稍后更新）。')
  } catch {
    if (isLive(c)) toast.error('取消请求未成功，请稍后重试。')
  } finally { if (isLive(c)) actionBusy.value = false }
}

// ==================== 改期 ====================

const rescheduleModal = reactive({
  show: false,
  scheduleType: 'daily' as ScheduledTaskScheduleType,
  // 时间字段按字符串绑定（BaseSelect 契约），提交时整数化校验
  hour: '9', minute: '0', dayOfWeek: 'mon', day: '1',
  /** §15：间隔只接受整数小时（数字字符串绑定），提交前即时校验 */
  intervalHours: '1', runAt: '',
  /** §15 时间窗（datetime-local 绑定，提交归一为 YYYY-MM-DD HH:MM:SS） */
  windowStart: '', windowEnd: '',
})

/** 间隔即时校验（后端为最终校验）；存量非整小时间隔时提示必须改输入，不静默替换 */
const intervalHoursError = computed(() => {
  const raw = rescheduleModal.intervalHours.trim()
  if (!raw) {
    const seconds = task.value?.interval_seconds
    if (rescheduleModal.scheduleType === 'interval' && seconds && seconds > 0 && seconds % 3600 !== 0) {
      return `原间隔 ${Math.round((seconds / 60) * 100) / 100} 分钟不再支持，请输入整数小时`
    }
    return ''
  }
  const value = Number(raw)
  const min = limits.value?.min_interval_hours ?? 1
  if (!Number.isInteger(value) || value < 1) return '间隔必须为不低于 1 的整数小时'
  if (value < min) return `不能低于当前最短间隔 ${min} 小时`
  return ''
})

/** datetime-local（YYYY-MM-DDTHH:mm[:ss]）↔ 后端契约（YYYY-MM-DD HH:MM:SS）。
 * 浏览器（及 jsdom）可能把值归一化带毫秒尾巴（如 :30.000）——先剥掉再校验。 */
function toBackendTime(value: string): string | null {
  const trimmed = (value || '').trim()
  if (!trimmed) return null
  const normalized = trimmed.replace('T', ' ').replace(/\.\d{1,3}$/, '')
  if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?$/.test(normalized)) return null
  return normalized.length === 16 ? normalized + ':00' : normalized
}

/** 回显保留秒数（评审 P2：slice(0,16) 曾把 08:00:30 截成 08:00 改变首点）；
 * 无秒的值保持分钟精度，提交时补 :00。 */
function toDatetimeLocal(value?: string | null): string {
  return (value || '').replace('T', ' ').replace(' ', 'T').slice(0, 19)
}

function openReschedule() {
  const current = task.value
  if (!current) return
  rescheduleModal.scheduleType = current.schedule_type
  const parts = (current.cron_expression || '').split(' ')
  // 0 点/0 分是合法取值——parseInt || 默认值 的 falsy 兜底会把 00:xx 预填成 09:xx，
  // 用户直接确认就会静默改期（旧视图遗留缺陷，此处收敛为 NaN 判定）
  const parsedHour = parts.length >= 2 ? parseInt(parts[1], 10) : NaN
  const parsedMinute = parts.length >= 2 ? parseInt(parts[0], 10) : NaN
  rescheduleModal.hour = Number.isInteger(parsedHour) ? String(parsedHour) : '9'
  rescheduleModal.minute = Number.isInteger(parsedMinute) ? String(parsedMinute) : '0'
  // APScheduler 编号（后端 _WEEKDAY_CRON_MAP 与 CronTrigger.from_crontab 实测一致）：0=周一 … 6=周日
  const cronDayNames: Record<string, string> = { '0': 'mon', '1': 'tue', '2': 'wed', '3': 'thu', '4': 'fri', '5': 'sat', '6': 'sun' }
  rescheduleModal.dayOfWeek = cronDayNames[parts[4] || ''] || 'mon'
  rescheduleModal.day = parts.length >= 3 ? String(parseInt(parts[2]) || 1) : '1'
  // §15：整数小时预填；存量非整小时间隔（如 4500s=75 分钟）不再支持——
  // 置空并提示必须输入整数小时，避免静默改写为新值（CR P1-3）
  const seconds = current.interval_seconds
  if (seconds && seconds > 0 && seconds % 3600 === 0) {
    rescheduleModal.intervalHours = String(seconds / 3600)
  } else {
    rescheduleModal.intervalHours = ''
  }
  rescheduleModal.runAt = current.run_at || ''
  rescheduleModal.windowStart = toDatetimeLocal(current.start_at)
  rescheduleModal.windowEnd = toDatetimeLocal(current.ends_at)
  rescheduleModal.show = true
}

function buildTimeConfig(): Record<string, unknown> {
  const type = rescheduleModal.scheduleType
  if (type === 'once') {
    // datetime-local → 后端契约 "YYYY-MM-DD HH:MM:SS"（北京时间）
    const value = (rescheduleModal.runAt || '').trim()
    if (!value) throw new Error('请选择一次性任务的执行时间')
    const normalized = value.replace('T', ' ')
    if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?$/.test(normalized)) throw new Error('执行时间格式不正确')
    return { run_at: normalized.length === 16 ? normalized + ':00' : normalized }
  }
  // §15 时间窗：循环类型可只填一端；起止同填时前端即时校验先后，后端为最终校验
  const config: Record<string, unknown> = {}
  const startAt = toBackendTime(rescheduleModal.windowStart)
  const endsAt = toBackendTime(rescheduleModal.windowEnd)
  if (rescheduleModal.windowStart.trim() && !startAt) throw new Error('有效期起格式不正确')
  if (rescheduleModal.windowEnd.trim() && !endsAt) throw new Error('有效期止格式不正确')
  if (startAt && endsAt && startAt >= endsAt) throw new Error('有效期起必须早于有效期止')
  if (startAt) config.start_at = startAt
  if (endsAt) config.ends_at = endsAt
  if (type === 'interval') {
    const raw = rescheduleModal.intervalHours.trim()
    if (!raw) throw new Error('请输入间隔小时数（须为整数小时）')
    const hours = Number(raw)
    const min = limits.value?.min_interval_hours ?? 1
    if (!Number.isInteger(hours) || hours < 1) throw new Error('间隔必须为整数小时（如 1、2、6）')
    if (hours < min) throw new Error(`间隔不能低于当前最短间隔 ${min} 小时`)
    config.interval_hours = hours
    return config
  }
  const hour = parseInt(rescheduleModal.hour, 10)
  const minute = parseInt(rescheduleModal.minute, 10)
  if (!Number.isInteger(hour) || hour < 0 || hour > 23) throw new Error('小时取值须为 0-23')
  if (!Number.isInteger(minute) || minute < 0 || minute > 59) throw new Error('分钟取值须为 0-59')
  if (type === 'weekly') return { ...config, day_of_week: rescheduleModal.dayOfWeek, hour, minute }
  if (type === 'monthly') {
    const day = parseInt(rescheduleModal.day, 10)
    if (!Number.isInteger(day) || day < 1 || day > 31) throw new Error('日期取值须为 1-31')
    return { ...config, day, hour, minute }
  }
  return { ...config, hour, minute }
}

async function handleReschedule() {
  const c = ctx
  actionBusy.value = true
  try {
    const timeConfig = buildTimeConfig()
    await updateScheduledTaskSchedule(c.taskId, {
      schedule_type: rescheduleModal.scheduleType, time_config: timeConfig,
    })
    if (!isLive(c)) return
    toast.success('调度时间已更新')
    notifyScheduledTasksChanged()
    rescheduleModal.show = false
    await loadTask(c)
  } catch (e) {
    if (!isLive(c)) return
    toast.error(e instanceof Error && !(e instanceof ScheduledTaskRequestError) ? e.message
      : actionErrorText(e, '改期失败，请检查时间配置。'))
  } finally { if (isLive(c)) actionBusy.value = false }
}

// ==================== 编辑（内容与调度解耦） ====================

const editModal = reactive({
  show: false,
  name: '',
  taskPrompt: '',
  attachments: [] as ScheduledTaskAttachment[],
  notificationRule: '',
})
const taskRuleOptions = ref<TaskNotifyRule[]>([])
const rulesLoading = ref(false)
/** 上传中的附件占位（评审二轮第 4 项）：key 唯一标识一次上传，成功后移除并入列表 */
const uploadingPlaceholders = ref<{ key: string; name: string }[]>([])
// “上传中”唯一事实来源 = 当前弹窗代际的占位列表（评审三轮第 1 项：
// 独立计数会被旧弹窗 finally 递减错位，占位按代际增删无此问题）
const attachmentUploading = computed(() => uploadingPlaceholders.value.length > 0)
/** 编辑弹窗代际：每次打开递增；上传回调携带并校验，关闭/重开后的旧回调丢弃（防污染） */
let editModalGeneration = 0

/** 通知规则选项展示：规则名 + 外发渠道摘要（无目标=仅站内信）；渠道名与规则页 PROVIDER_OPTIONS 同口径 */
const PROVIDER_LABELS: Record<string, string> = {
  feishu_bot: '飞书群机器人',
  dingtalk_bot: '钉钉群机器人',
  wecom_bot: '企微群机器人',
  smtp: '邮件',
}
function ruleLabel(rule: TaskNotifyRule): string {
  const channels = rule.targets.map(t => PROVIDER_LABELS[t.provider] || t.provider).join('、')
  return channels ? `${rule.name}（${channels}）` : `${rule.name}（仅站内信）`
}

async function openEdit() {
  const current = task.value
  if (!current) return
  // 弹窗代际递增：旧弹窗遗留的上传回调全部失效（评审二轮第 4 项）
  editModalGeneration += 1
  uploadingPlaceholders.value = []
  editModal.name = current.name
  editModal.taskPrompt = current.task_prompt
  editModal.attachments = (current.attachments || []).map(att => ({ ...att }))
  editModal.notificationRule = current.notification_rule_id || ''
  editModal.show = true
  // 通知规则每次打开都重新查询（评审二轮第 7 项）：租户/登录切换后不残留旧
  // 租户选项；加载中下拉禁用（BaseSelect :disabled="rulesLoading"）
  const c = ctx
  taskRuleOptions.value = []
  rulesLoading.value = true
  try {
    const value = await notificationsAPI.taskRules()
    if (!isLive(c)) return
    taskRuleOptions.value = value?.rules || []
  } catch {
    if (isLive(c)) taskRuleOptions.value = []
  } finally {
    if (isLive(c)) rulesLoading.value = false
  }
}

function triggerAttachmentPick() {
  const input = document.getElementById(`${uid}-edit-attachment-input`) as HTMLInputElement | null
  input?.click()
}

function removeUploadPlaceholder(key: string) {
  const index = uploadingPlaceholders.value.findIndex(item => item.key === key)
  if (index !== -1) uploadingPlaceholders.value.splice(index, 1)
}

/** 添加附件（评审 P2-7；二轮第 4 项回归修复）：复用聊天上传端点 POST /api/upload。
 * 上传期间列表显示占位项；保存按钮在上传完成前禁用；回调校验弹窗代际——
 * 关闭/重开弹窗后旧回调丢弃；失败移除占位并提示。 */
async function handleAddAttachment(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  const c = ctx
  const generation = editModalGeneration
  const placeholder = { key: `upload-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, name: file.name }
  uploadingPlaceholders.value.push(placeholder)
  try {
    const uploaded = await uploadFile(file, headers())
    // 弹窗代际校验：关闭后重开弹窗，旧回调不得写入新弹窗的附件列表
    if (!isLive(c) || generation !== editModalGeneration || !editModal.show) return
    if (!editModal.attachments.some(att => att.file_id === uploaded.file_id)) {
      editModal.attachments.push({
        file_id: uploaded.file_id,
        name: uploaded.name,
        type: uploaded.type,
        mime_type: uploaded.mime_type,
        size: uploaded.size,
      })
    }
  } catch (e) {
    if (isLive(c)) toast.error(e instanceof Error ? e.message : '附件上传失败，请稍后重试。')
  } finally {
    // 占位移除与"上传中"判定同源：旧弹窗的上传只清自己代际的占位，
    // 新弹窗的占位不受影响（评审三轮第 1 项：finally 曾递减新弹窗计数
    // 错误解禁保存）。占位列表本身即上传中的唯一事实来源。
    if (generation === editModalGeneration) removeUploadPlaceholder(placeholder.key)
  }
}

async function handleEditSave() {
  const c = ctx
  if (!editModal.name.trim() || !editModal.taskPrompt.trim()) {
    toast.error('任务名称与任务定义不能为空白')
    return
  }
  // 附件仍在传输中不得保存（占位项不是合法 AttachmentRef，评审二轮第 4 项）
  if (attachmentUploading.value) {
    toast.error('附件上传中，请等待上传完成后再保存。')
    return
  }
  // 保存前校验上下文仍有效且任务身份一致（评审 P1-1：切换后遗留弹窗不得写入另一任务）
  if (!isLive(c) || task.value?.task_id !== c.taskId) {
    editModal.show = false
    return
  }
  actionBusy.value = true
  try {
    // 通知规则 diff（评审 P2-9）：仅在规则实际变更时提交该字段，
    // 未变更的保存不再被「规则已失效」错误阻塞
    const previousRule = task.value?.notification_rule_id || ''
    const nextRule = editModal.notificationRule
    const ruleChanged = nextRule !== previousRule
    const updated = await updateScheduledTaskDefinition(c.taskId, {
      name: editModal.name.trim(),
      task_prompt: editModal.taskPrompt.trim(),
      attachments: editModal.attachments,
      // F4 清空语义：从有规则切回默认时显式 clear（与设置规则互斥）
      ...(ruleChanged && nextRule ? { notification_rule_id: nextRule } : {}),
      ...(ruleChanged && !nextRule && previousRule ? { clear_notification_rule: true } : {}),
    })
    if (!isLive(c)) return
    task.value = updated
    toast.success('任务已更新（调度时间不受影响）')
    notifyScheduledTasksChanged()
    editModal.show = false
  } catch (e) {
    if (isLive(c)) toast.error(actionErrorText(e, '保存失败，请稍后重试。'))
  } finally { if (isLive(c)) actionBusy.value = false }
}

// ==================== 工具与生命周期 ====================

function goBack() {
  if (window.history.length > 1) router.back()
  else router.push(`/t/${tenantId.value}/chat`)
}

function formatDateTime(value?: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hours = String(date.getHours()).padStart(2, '0')
  const minutes = String(date.getMinutes()).padStart(2, '0')
  return `${month}-${day} ${hours}:${minutes}`
}
function formatTime(value?: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

onMounted(() => {
  document.addEventListener('visibilitychange', onVisibilityChange)
  void loadAll(bumpContext())
})
onBeforeUnmount(() => {
  disposed = true
  stopTimers()
  runnerClient.detach()
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
// 页面上下文 = tenantId+taskId（评审 P1-1）：任一变化即整体重置再装载——
// 同 taskId 换租户同样重载；旧上下文的慢响应/弹窗/定时器全部失效
watch([tenantId, taskId], () => {
  if (!tenantId.value || !taskId.value) return
  resetPage()
  void loadAll(bumpContext())
})
</script>

<style scoped>
.markdown-content :deep(> :first-child) { margin-top: 0; }
.markdown-content :deep(> :last-child) { margin-bottom: 0; }
</style>
