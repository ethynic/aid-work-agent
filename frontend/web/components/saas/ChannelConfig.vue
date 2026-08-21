<template>
  <div class="page-container bg-canvas">
    <!-- Header Bar -->
    <AppHeader
      title="渠道配置"
      :is-logged-in="effectiveIsLoggedIn"
      :user="effectiveUser"
      @toggle-sidebar="handleToggleSidebar"
      @logout="handleLogout"
    >
    </AppHeader>

    <!-- Main Content -->
    <div class="page-content p-6">
      <div class="page-toolbar">
        <div></div>
        <div class="page-toolbar-right">
          <BaseButton @click="openAddChannel">添加渠道</BaseButton>
        </div>
      </div>

    <div v-if="loading" class="text-center py-12 text-muted">加载中...</div>

    <!-- 渠道列表 -->
    <div v-else-if="channels.length > 0" class="space-y-4">
      <div v-for="(ch, index) in channels" :key="ch.config_id" class="bg-surface rounded-lg border border-default p-5">
        <div class="flex items-center justify-between mb-3">
          <div class="flex items-center gap-3">
            <span class="text-sm text-muted">{{ index + 1 }}</span>
            <span v-if="ch.name" class="text-sm font-medium text-default">{{ ch.name }}</span>
            <span class="text-sm text-default">{{ channelTypeLabel(ch.channel_type) }}</span>
            <BaseBadge :intent="ch.verified ? 'success' : 'warning'">
              {{ ch.verified ? '已验证' : '未验证' }}
            </BaseBadge>
            <BaseBadge v-if="ch.subagent_type" intent="info">
              🤖 {{ subagentTypeLabel(ch.subagent_type) }}
            </BaseBadge>
          </div>
          <div class="flex items-center gap-2">
            <BaseButton intent="ghost" size="sm" @click="handleVerify(ch.config_id)">验证连接</BaseButton>
            <BaseButton intent="ghost" size="sm" @click="editChannel(ch)">编辑</BaseButton>
            <BaseButton intent="danger-ghost" size="sm" @click="handleDelete(ch.config_id)">删除</BaseButton>
          </div>
        </div>
        <!-- 回调地址展示 -->
        <div v-if="tenant" class="bg-canvas rounded-lg p-3 text-sm">
          <span class="text-muted">回调地址：</span>
          <code class="text-primary-600 select-all font-mono">{{ getCallbackUrl(ch.channel_type, ch.config_id) }}</code>
          <BaseButton intent="ghost" size="sm" @click="copyUrl(getCallbackUrl(ch.channel_type, ch.config_id), ch.config_id)">
            {{ copied[ch.config_id] ? '已复制' : '复制' }}
          </BaseButton>
        </div>
      </div>
    </div>

    <div v-else class="text-center py-16 bg-surface rounded-lg border border-default">
      <div class="text-4xl mb-4">🔗</div>
      <p class="text-lg font-medium text-default mb-2">尚未配置任何渠道</p>
      <p class="text-sm text-muted mb-6">配置企业微信、钉钉或飞书后，员工即可在 IM 中与智能体对话</p>
      <BaseButton @click="openAddChannel">添加第一个渠道</BaseButton>
    </div>
    </div>

    <!-- ==================== 添加/编辑弹窗 ==================== -->
    <BaseModal
      v-model="showForm"
      :title="editingId ? '渠道配置 - 编辑' : '渠道配置 - 新增'"
      size="xl"
      :close-on-overlay="false"
      :mode="editingId ? 'edit' : 'create'"
      :content-class="{ 'modal-fullscreen': isFullscreen }"
    >
      <template #header-extra>
        <BaseButton intent="ghost" size="sm" class="modal-fullscreen-btn" title="全屏" @click="toggleFullscreen">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>
          </svg>
        </BaseButton>
      </template>

      <div>
        <!-- 操作按钮区（第一行） -->
        <div class="flex items-center gap-2 mb-3">
          <BaseButton @click="handleSubmit">保存</BaseButton>
          <BaseButton intent="secondary" @click="handleSaveAndClose">保存并关闭</BaseButton>
        </div>

        <p v-if="!editingId" class="text-sm text-muted mb-3">选择 IM 平台，然后填写应用凭证</p>

        <!-- 渠道类型选择 -->
        <div v-if="!editingId" class="grid grid-cols-8 gap-2 mb-3">
          <button
            v-for="ct in channelTypes" :key="ct.value"
            @click="form.channel_type = ct.value"
            class="flex flex-col items-center gap-1.5 py-3 px-2 rounded-lg border-2 transition-all cursor-pointer"
            :class="form.channel_type === ct.value ? 'border-primary-400 bg-primary-50' : 'border-default hover:border-hover'"
          >
            <span class="text-lg">{{ ct.icon }}</span>
            <span class="text-xs font-medium" :class="form.channel_type === ct.value ? 'text-primary-700' : 'text-default'">{{ ct.label }}</span>
          </button>
        </div>

        <!-- 渠道名称（用户自定义，用于区分同租户多个同类渠道） -->
        <div class="mb-3">
          <label class="text-sm text-muted mb-1 block">
            渠道名称 <span class="text-danger-500">*</span>
          </label>
          <BaseInput
            v-model="form.name"
            placeholder="如有多个同类渠道，建议填写有辨识度的名称便于区分"
            maxlength="50"
          />
        </div>

        <!-- 配置指引摘要 -->
        <div class="bg-warning-50 border border-warning-200 rounded-lg p-3 mb-3 text-sm text-warning-800">
          <p class="font-medium mb-1.5">{{ currentGuide.title }}</p>
          <ol class="list-decimal list-inside space-y-0.5 text-warning-700">
            <li v-for="(step, i) in currentGuide.steps" :key="i">{{ step }}</li>
          </ol>
          <a v-if="currentGuide.docUrl" :href="currentGuide.docUrl" target="_blank"
            class="inline-block mt-1.5 text-primary-600 hover:underline text-xs">
            前往 {{ channelTypeLabel(form.channel_type) }} 管理后台 &rarr;
          </a>
        </div>

        <!-- 表单字段（一行4个） -->
        <div class="grid grid-cols-4 gap-3">
          <!-- 企微个人号 RPA 特有：会话存档模式开关（第一期固定 server，client 禁用） -->
          <div v-if="form.channel_type === 'wecom_personal_rpa'" class="col-span-4">
            <label class="text-sm text-muted mb-1 block">会话存档拉取模式</label>
            <div class="flex items-center gap-4">
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="radio" value="server" v-model="form.config.listen_mode"
                  class="w-4 h-4 text-primary-600 focus:ring-primary-500" />
                <span class="text-sm text-default">服务端拉取（推荐）：凭证存服务端，企微推送回调</span>
              </label>
              <label class="flex items-center gap-2 cursor-not-allowed opacity-60" title="即将开放">
                <input type="radio" value="client" disabled
                  class="w-4 h-4 text-primary-600" />
                <span class="text-sm text-muted">客户端拉取（即将开放）</span>
              </label>
            </div>
            <p class="mt-1 text-xs text-warning-700">⚠️ 第一期仅支持服务端拉取模式。客户端拉取模式代码已就绪，前端暂未开放。</p>
          </div>

          <div v-for="field in channelFields" :key="field.key">
            <label class="text-sm text-muted mb-1 block">{{ field.label }}</label>
            <!-- file 类型：用 input[type=file] 上传，读为文本后存入 config -->
            <input
              v-if="field.type === 'file'"
              type="file"
              accept=".pem,.key,.txt"
              @change="(e: any) => handleFileUpload(field.key, e)"
              class="block w-full text-sm text-default file:mr-3 file:py-2 file:px-3 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-primary-50 file:text-primary-700 hover:file:bg-primary-100 cursor-pointer"
            />
            <BaseInput
              v-else
              v-model="form.config[field.key]"
              :placeholder="field.placeholder"
            />
            <BaseButton
              v-if="editingId && form.channel_type === 'wecom_personal_rpa' && field.key === 'external_contact_secret'"
              intent="danger-ghost"
              size="sm"
              class="mt-1"
              @click="form.config[field.key] = null"
            >
              清除已保存的客户联系 Secret
            </BaseButton>
            <p v-if="field.hint" class="mt-1 text-xs text-muted">{{ field.hint }}</p>
            <p v-if="field.type === 'file' && form.config[field.key]" class="mt-1 text-xs text-success-700">✓ 已上传</p>
            <!-- wecom_personal_rpa 私钥字段：附加「生成密钥对」按钮 -->
            <div
              v-if="field.key === 'private_key' && form.channel_type === 'wecom_personal_rpa' && editingId"
              class="mt-2"
            >
              <BaseButton
                intent="secondary"
                size="sm"
                :disabled="generatingKeypair"
                @click="handleGenerateKeypair"
              >
                {{ generatingKeypair ? '生成中...' : '🔑 生成密钥对' }}
              </BaseButton>
              <p class="mt-1 text-xs text-muted">
                点击自动生成 RSA 2048 密钥对。私钥自动加密保存到本系统，公钥将弹出供您上传到企业微信后台。
              </p>
            </div>
          </div>

          <!-- 关联数字员工（wecom_kf 渠道由客服账号级"绑定数字员工"决定，隐藏主表字段避免误导） -->
          <div v-if="form.channel_type !== 'wecom_kf'">
            <label class="text-sm text-muted mb-1 block">关联数字员工</label>
            <BaseSelect v-model="form.subagent_type">
              <option value="">不绑定</option>
              <option v-for="sa in availableSubagents" :key="sa" :value="sa">{{ subagentTypeLabel(sa) }} ({{ sa }})</option>
            </BaseSelect>
            <p class="mt-1 text-xs text-muted">选择该渠道消息由哪个数字员工处理</p>
          </div>
        </div>

        <!-- 微信客服特有：客服账号管理（编辑模式内嵌完整管理；新增模式仅提示） -->
        <div v-if="form.channel_type === 'wecom_kf'" class="mt-3 pt-3 border-t border-default">
          <template v-if="editingId">
            <div class="bg-canvas rounded-lg p-3">
              <div class="flex items-center justify-between mb-2">
                <span class="text-sm font-medium text-default">客服账号</span>
                <BaseButton intent="ghost" size="sm" @click="openKfAccountCreate({ config_id: editingId })">+ 添加客服账号</BaseButton>
              </div>
              <div v-if="(kfAccountMap[editingId] || []).length === 0" class="text-xs text-muted text-center py-2">
                尚未配置客服账号，点击右上角添加
              </div>
              <div v-else class="space-y-2">
                <div
                  v-for="acc in kfAccountMap[editingId]"
                  :key="acc.open_kfid"
                  class="bg-surface border border-default rounded-md px-3 py-2 flex items-center justify-between gap-2"
                >
                  <div class="flex items-center gap-3 min-w-0 flex-wrap">
                    <span class="text-sm font-medium text-default">{{ acc.name }}</span>
                    <span class="text-xs text-muted font-mono">{{ acc.open_kfid }}</span>
                    <span class="text-xs text-muted">归属：{{ tenantUserName(acc.tenant_user_id) }}</span>
                    <span v-if="acc.expire_at" class="text-xs text-muted">到期 {{ acc.expire_at }}</span>
                    <span class="text-xs text-muted">积分 {{ acc.credit_used }}<template v-if="acc.credit_limit > 0">/{{ acc.credit_limit }}</template></span>
                    <span class="text-xs text-muted">引流 {{ acc.referral_count }} 人</span>
                  </div>
                  <div class="flex items-center gap-1 flex-shrink-0">
                    <BaseButton intent="ghost" size="sm" @click="viewKfQr({ config_id: editingId }, acc)">二维码</BaseButton>
                    <BaseButton intent="ghost" size="sm" @click="openKfAccountEdit({ config_id: editingId }, acc)">编辑</BaseButton>
                    <BaseButton intent="danger-ghost" size="sm" @click="handleDeleteKfAccount({ config_id: editingId }, acc)">删除</BaseButton>
                  </div>
                </div>
              </div>
            </div>
          </template>
          <p v-else class="text-sm text-muted">
            微信客服账号请在保存渠道后，点击渠道的「编辑」按钮，在编辑弹窗中管理：系统自动调用企业微信 API 创建账号、生成推广二维码，并支持绑定归属用户、设置到期日期与积分上限。
          </p>
        </div>

        <!-- 微信客服特有：处理超时等待提示（渠道级，所有客服账号统一生效） -->
        <div v-if="form.channel_type === 'wecom_kf'" class="mt-3 pt-3 border-t border-default">
          <div class="bg-canvas rounded-lg p-3 space-y-3">
            <label class="flex items-center gap-2 text-sm text-default cursor-pointer">
              <input type="checkbox" v-model="wi.enabled" class="w-4 h-4 rounded border-primary-200 text-primary-600 focus:ring-primary-500" />
              <span class="font-medium">处理超时等待提示</span>
              <span class="text-xs text-muted">智能体处理超过 N 秒未回复时，先发送提示语</span>
            </label>
            <div v-if="wi.enabled" class="grid grid-cols-1 md:grid-cols-2 gap-3 pl-6">
              <div>
                <label class="text-sm text-muted mb-1 block">超时秒数</label>
                <BaseInput v-model="wi.delay_seconds" type="number" min="1" placeholder="15" />
                <p class="mt-1 text-xs text-muted">处理超过该秒数未回复时，向用户发送提示语</p>
              </div>
              <div>
                <label class="text-sm text-muted mb-1 block">提示语</label>
                <BaseInput v-model="wi.message" placeholder="我正在处理您的问题，可能需要几分钟，请稍等下。" />
              </div>
            </div>
          </div>
        </div>

        <!-- 回调地址提示 -->
        <div v-if="tenant" class="mt-3 bg-primary-50 border border-primary-200 rounded-lg p-3">
          <p class="text-sm font-medium text-primary-800 mb-1">回调地址</p>
          <p class="text-xs text-primary-600 mb-2">请将此地址填入 {{ channelTypeLabel(form.channel_type) }} 后台的「接收消息」配置中</p>
          <div class="flex items-center gap-2">
            <code class="flex-1 bg-surface px-3 py-2 rounded text-sm text-primary-700 font-mono select-all break-all">{{ getCallbackUrl(form.channel_type, editingId || undefined) }}</code>
            <BaseButton intent="ghost" size="sm" @click="copyUrl(getCallbackUrl(form.channel_type, editingId || undefined), 'form')">{{ copied['form'] ? '已复制' : '复制' }}</BaseButton>
          </div>
        </div>

        <div v-if="formError" class="mt-3 p-3 bg-danger-50 border border-danger-200 rounded-lg text-danger-600 text-sm">{{ formError }}</div>
      </div>
    </BaseModal>

    <!-- ==================== 客服账号弹窗（创建/编辑 + 二维码回显） ==================== -->
    <BaseModal
      v-model="showKfModal"
      :title="kfEditingOpenKfid ? '客服账号 - 编辑' : '客服账号 - 新增'"
      size="lg"
      :close-on-overlay="false"
    >
      <div class="space-y-4">
        <!-- 创建成功二维码回显 -->
        <div v-if="createdQrData" class="bg-success-50 border border-success-200 rounded-lg p-4 flex flex-col items-center">
          <p class="text-sm font-medium text-success-800 mb-2">客服账号创建成功！请下载二维码分享给归属用户</p>
          <p v-if="createdQrData.qr_title" class="text-sm font-medium text-default mb-2">{{ createdQrData.qr_title }}</p>
          <img :src="createdQrData.qr_data_url" alt="客服二维码" class="w-40 h-40 rounded-lg border border-default bg-white" />
          <p class="text-xs text-muted mt-2 break-all text-center">{{ createdQrData.contact_url }}</p>
          <div class="flex gap-2 mt-2">
            <BaseButton intent="secondary" size="sm" @click="copyUrl(createdQrData.contact_url, 'kfq')">{{ copied['kfq'] ? '已复制' : '复制链接' }}</BaseButton>
            <BaseButton size="sm" @click="downloadQr(createdQrData)">下载二维码</BaseButton>
            <BaseButton intent="ghost" size="sm" @click="resetKfFormForCreate">继续添加</BaseButton>
          </div>
        </div>

        <!-- 编辑模式：不可变字段只读展示 -->
        <div v-if="kfEditingOpenKfid" class="bg-canvas border border-default rounded-lg p-3 grid grid-cols-2 gap-3 text-xs">
          <div>
            <label class="text-muted block mb-0.5">open_kfid（不可改）</label>
            <span class="text-default font-mono">{{ kfForm.open_kfid }}</span>
          </div>
          <div>
            <label class="text-muted block mb-0.5">场景 scene（不可改）</label>
            <span class="text-default font-mono">{{ kfForm.scene }}</span>
          </div>
          <div class="col-span-2">
            <label class="text-muted block mb-0.5">客服链接（不可改）</label>
            <span class="text-default break-all">{{ kfForm.contact_url }}</span>
          </div>
        </div>

        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="text-sm text-muted mb-1 block">客服名称<span class="text-danger-500">*</span></label>
            <BaseInput v-model="kfForm.name" maxlength="16" placeholder="如：售前咨询" />
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">头像（可选）</label>
            <div class="flex items-center gap-2">
              <img v-if="kfForm.avatarPreview" :src="kfForm.avatarPreview" class="w-10 h-10 rounded-lg border border-default object-cover" />
              <input
                type="file"
                accept=".png,.jpg,.jpeg"
                @change="handleAvatarUpload"
                class="text-sm text-default file:mr-2 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-primary-50 file:text-primary-700 hover:file:bg-primary-100 cursor-pointer"
              />
            </div>
            <p class="mt-0.5 text-xs text-muted">不传则使用租户 Logo 或默认占位图</p>
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">归属用户 <span class="text-danger-500">*</span></label>
            <BaseSelect v-model="kfForm.tenant_user_id">
              <option value="">请选择用户</option>
              <option v-for="u in tenantUsers" :key="u.user_id" :value="u.user_id">{{ tenantUserName(u.user_id) }}</option>
            </BaseSelect>
            <p class="mt-0.5 text-xs text-muted">微信侧客户扫码后归属到该用户名下</p>
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">绑定数字员工 <span class="text-danger-500">*</span></label>
            <BaseSelect v-model="kfForm.subagent_type">
              <option value="">请选择数字员工</option>
              <option v-for="sa in availableSubagents" :key="sa" :value="sa">{{ subagentTypeLabel(sa) }} ({{ sa }})</option>
            </BaseSelect>
          </div>
          <div class="col-span-2">
            <label class="text-sm text-muted mb-1 block">欢迎语</label>
            <BaseInput v-model="kfForm.welcome_message" placeholder="您好，请问有什么可以帮您？" />
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">人工接待人员（企微 userid）</label>
            <BaseInput v-model="kfForm.servicer_userid_list" placeholder="zhangsan, lisi" />
            <p class="mt-0.5 text-xs text-muted">多个用逗号分隔</p>
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">转人工开关</label>
            <div class="flex items-center h-10">
              <input type="checkbox" v-model="kfForm.allow_agent_transfer" class="w-4 h-4 rounded border-primary-200 text-primary-600 focus:ring-primary-500" />
              <span class="ml-2 text-sm text-default">允许 Agent 主动转人工</span>
            </div>
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">到期日期</label>
            <input
              type="date"
              v-model="kfForm.expire_at"
              class="block w-full h-10 px-3 rounded-lg border border-default bg-surface text-sm text-default focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
            <p class="mt-0.5 text-xs text-muted">到期后该账号自动拦截，空=不限制</p>
          </div>
          <div>
            <label class="text-sm text-muted mb-1 block">积分上限</label>
            <BaseInput v-model="kfForm.credit_limit" type="number" min="0" placeholder="0=无上限" />
            <p class="mt-0.5 text-xs text-muted">0 表示不限制；超过上限自动拦截</p>
          </div>
          <div class="col-span-2">
            <label class="text-sm text-muted mb-1 block">二维码标题</label>
            <BaseInput v-model="kfForm.qr_title" maxlength="50" placeholder="如：爱定义 - 小蔡老师" />
            <p class="mt-0.5 text-xs text-muted">显示在二维码图片上方，便于区分不同归属用户</p>
          </div>
        </div>

        <p v-if="kfFormError" class="p-3 bg-danger-50 border border-danger-200 rounded-lg text-danger-600 text-sm">{{ kfFormError }}</p>
      </div>

      <template #footer>
        <BaseButton intent="secondary" @click="showKfModal = false">关闭</BaseButton>
        <BaseButton :disabled="kfSubmitting" @click="handleKfSubmit">
          {{ kfSubmitting ? '保存中...' : (kfEditingOpenKfid ? '保存修改' : '创建客服账号') }}
        </BaseButton>
      </template>
    </BaseModal>

    <!-- ==================== 查看二维码弹窗 ==================== -->
    <BaseModal v-model="showKfQrModal" title="客服二维码" size="sm">
      <div class="flex flex-col items-center gap-2">
        <p v-if="kfQrData.qr_title" class="text-sm font-medium text-default">{{ kfQrData.qr_title }}</p>
        <img v-if="kfQrData.qr_data_url" :src="kfQrData.qr_data_url" class="w-48 h-48 rounded-lg border border-default bg-white" />
        <p class="text-xs text-muted break-all text-center">{{ kfQrData.contact_url }}</p>
        <div class="flex gap-2">
          <BaseButton intent="secondary" size="sm" @click="copyUrl(kfQrData.contact_url, 'kfq2')">{{ copied['kfq2'] ? '已复制' : '复制链接' }}</BaseButton>
          <BaseButton size="sm" @click="downloadQr(kfQrData)">下载二维码</BaseButton>
        </div>
      </div>
      <template #footer>
        <BaseButton intent="secondary" @click="showKfQrModal = false">关闭</BaseButton>
      </template>
    </BaseModal>

    <!-- ==================== 公钥展示弹窗（生成密钥对后） ==================== -->
    <BaseModal
      v-model="showPublicKeyModal"
      title="公钥已生成 - 请上传到企业微信后台"
      size="lg"
      :close-on-overlay="false"
    >
      <div class="space-y-4">
        <div class="bg-warning-50 border border-warning-200 rounded-lg p-3 text-sm text-warning-800">
          <p class="font-medium mb-1">操作指引</p>
          <p class="text-warning-700">
            请将下方公钥内容粘贴到企业微信管理后台 → 管理工具 → 会话内容存档 → 密钥管理 → 设置公钥，然后点保存。
            私钥已自动保存到本系统，无需手动操作。
          </p>
        </div>
        <div>
          <div class="flex items-center justify-between mb-1">
            <label class="text-sm text-muted">公钥 PEM 文本（只读）</label>
            <BaseButton intent="ghost" size="sm" @click="copyPublicKey">
              {{ publicKeyCopied ? '已复制' : '复制公钥' }}
            </BaseButton>
          </div>
          <textarea
            ref="publicKeyTextareaRef"
            :value="publicKeyText"
            readonly
            rows="10"
            class="block w-full text-xs text-default font-mono bg-canvas border border-default rounded-lg p-3 resize-none focus:outline-none"
            @focus="($event.target as HTMLTextAreaElement).select()"
          />
        </div>
      </div>
      <template #footer>
        <BaseButton intent="primary" @click="showPublicKeyModal = false">我已上传，关闭</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, inject } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToast } from 'vue-toastification'
import AppHeader from '@/components/AppHeader.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import { listChannels, createChannel, updateChannel, deleteChannel, verifyChannel, getAvailableSubagents, generateChannelKeypair, listTenantUsers, listKfAccounts, createKfAccount, updateKfAccount, deleteKfAccount } from '@/api/saasTenant'
import { useTenantAuth } from '@/composables/useTenantAuth'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const tenantId = computed(() => route.params.tenant_id as string)

const { tenant, admin: tenantAdmin, isLoggedIn: tenantIsLoggedIn, logout: tenantLogout } = useTenantAuth()

// 统一的登录状态检查
const effectiveIsLoggedIn = computed(() => tenantIsLoggedIn.value)

// 统一的用户信息
const effectiveUser = computed(() => {
  return tenantAdmin.value ? {
    user_id: tenantAdmin.value.user_id,
    username: tenantAdmin.value.username,
    phone: tenantAdmin.value.phone
  } : null
})

// 从 PortalLayout 注入侧边栏状态
const sidebarCollapsed = inject<{ value: boolean }>('sidebarCollapsed')
const toggleSidebarFn = inject<() => void>('toggleSidebar')

// 侧边栏折叠状态
const localSidebarCollapsed = ref(false)
const isSidebarCollapsed = computed({
  get: () => sidebarCollapsed?.value ?? localSidebarCollapsed.value,
  set: (val: boolean) => {
    if (sidebarCollapsed) {
      sidebarCollapsed.value = val
    } else {
      localSidebarCollapsed.value = val
    }
  }
})

function handleToggleSidebar() {
  if (toggleSidebarFn) {
    toggleSidebarFn()
  } else {
    isSidebarCollapsed.value = !isSidebarCollapsed.value
  }
}

async function handleLogout() {
  await tenantLogout()
  router.push(`/t/${tenantId.value}/login`)
}
const loading = ref(true)
const channels = ref<any[]>([])
const availableSubagents = ref<string[]>([])
const showForm = ref(false)
const submitting = ref(false)
const formError = ref('')
const editingId = ref<string | null>(null)
const copied = ref<Record<string, boolean>>({})

// ==================== 微信客服账号管理 ====================

// config_id → 账号列表（来自 GET /api/saas/wecom-kf/accounts）
const kfAccountMap = ref<Record<string, any[]>>({})
const tenantUsers = ref<any[]>([])
const tenantUserMap = ref<Record<string, any>>({})

const showKfModal = ref(false)
const kfSubmitting = ref(false)
const kfFormError = ref('')
// 当前弹窗所属渠道 config_id（创建/编辑后刷新列表用）
const kfOwnerConfigId = ref<string | null>(null)
// 编辑中的 open_kfid；null 表示新增
const kfEditingOpenKfid = ref<string | null>(null)
// 创建成功后回显的二维码数据（仅新增模式）
const createdQrData = ref<any>(null)
// 查看已保存账号的二维码弹窗
const showKfQrModal = ref(false)
const kfQrData = ref<any>({ qr_title: '', name: '', contact_url: '', qr_data_url: '' })

const kfForm = ref({
  name: '',
  tenant_user_id: '',
  subagent_type: '',
  welcome_message: '',
  servicer_userid_list: '',
  allow_agent_transfer: true,
  expire_at: '',
  credit_limit: '',
  qr_title: '',
  open_kfid: '',
  scene: '',
  contact_url: '',
  avatar_base64: '',
  avatarPreview: '',
})

function tenantUserName(userId: string | null | undefined): string {
  if (!userId) return '-'
  const u = tenantUserMap.value[userId]
  if (!u) return '已删除用户'
  return u.nickname || u.username || u.phone || userId
}

async function loadTenantUsers() {
  try {
    const res = await listTenantUsers(500)
    tenantUsers.value = res.users || []
    tenantUserMap.value = {}
    for (const u of tenantUsers.value) tenantUserMap.value[u.user_id] = u
  } catch (e) {
    console.error('加载用户列表失败:', e)
  }
}

async function loadKfAccounts() {
  try {
    const res = await listKfAccounts()
    const accounts = res.accounts || []
    // 后端 /accounts 已带 config_id，直接按归属渠道分组
    const map: Record<string, any[]> = {}
    for (const acc of accounts) {
      if (!acc.config_id) continue
      if (!map[acc.config_id]) map[acc.config_id] = []
      map[acc.config_id].push(acc)
    }
    kfAccountMap.value = map
  } catch (e) {
    console.error('加载客服账号失败:', e)
  }
}

function resetKfForm() {
  kfForm.value = {
    name: '',
    tenant_user_id: '',
    subagent_type: '',
    welcome_message: '',
    servicer_userid_list: '',
    allow_agent_transfer: true,
    expire_at: '',
    credit_limit: '',
    qr_title: '',
    open_kfid: '',
    scene: '',
    contact_url: '',
    avatar_base64: '',
    avatarPreview: '',
  }
}

function openKfAccountCreate(ch: any) {
  kfEditingOpenKfid.value = null
  kfOwnerConfigId.value = ch.config_id
  resetKfForm()
  createdQrData.value = null
  kfFormError.value = ''
  showKfModal.value = true
}

function openKfAccountEdit(ch: any, acc: any) {
  kfEditingOpenKfid.value = acc.open_kfid
  kfOwnerConfigId.value = ch.config_id
  kfForm.value = {
    name: acc.name || '',
    tenant_user_id: acc.tenant_user_id || '',
    subagent_type: acc.subagent_type || '',
    welcome_message: acc.welcome_message || '',
    servicer_userid_list: Array.isArray(acc.servicer_userid_list) ? acc.servicer_userid_list.join(', ') : (acc.servicer_userid_list || ''),
    allow_agent_transfer: acc.allow_agent_transfer !== false,
    expire_at: acc.expire_at || '',
    credit_limit: acc.credit_limit !== undefined && acc.credit_limit !== null ? String(acc.credit_limit) : '',
    qr_title: acc.qr_title || '',
    open_kfid: acc.open_kfid || '',
    scene: acc.scene || '',
    contact_url: acc.contact_url || '',
    avatar_base64: '',
    avatarPreview: '',
  }
  createdQrData.value = null
  kfFormError.value = ''
  showKfModal.value = true
}

function resetKfFormForCreate() {
  kfEditingOpenKfid.value = null
  resetKfForm()
  createdQrData.value = null
  kfFormError.value = ''
}

function handleAvatarUpload(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    const dataUrl = String(reader.result || '')
    kfForm.value.avatarPreview = dataUrl
    // 去掉 data:image/...;base64, 前缀，只保留 base64 串
    const commaIdx = dataUrl.indexOf(',')
    kfForm.value.avatar_base64 = commaIdx >= 0 ? dataUrl.slice(commaIdx + 1) : dataUrl
  }
  reader.onerror = () => { kfFormError.value = '头像读取失败' }
  reader.readAsDataURL(file)
}

function downloadQr(data: any) {
  if (!data?.qr_data_url) return
  const a = document.createElement('a')
  a.href = data.qr_data_url
  a.download = `${data.qr_title || data.name || 'kf_qr'}.png`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

function viewKfQr(_ch: any, acc: any) {
  kfQrData.value = {
    qr_title: acc.qr_title || '',
    name: acc.name || '',
    contact_url: acc.contact_url || '',
    qr_data_url: acc.qr_data_url || '',
  }
  showKfQrModal.value = true
}

async function handleKfSubmit() {
  const name = (kfForm.value.name || '').trim()
  if (!name) { kfFormError.value = '请填写客服名称'; return }
  if (!kfForm.value.tenant_user_id) { kfFormError.value = '请选择用户'; return }

  const payload: Record<string, any> = {
    name,
    tenant_user_id: kfForm.value.tenant_user_id,
    subagent_type: kfForm.value.subagent_type || undefined,
    welcome_message: kfForm.value.welcome_message || undefined,
    allow_agent_transfer: kfForm.value.allow_agent_transfer,
    expire_at: kfForm.value.expire_at || undefined,
    credit_limit: kfForm.value.credit_limit === '' ? 0 : Number(kfForm.value.credit_limit) || 0,
    qr_title: kfForm.value.qr_title || undefined,
  }
  if (kfForm.value.avatar_base64) payload.avatar_base64 = kfForm.value.avatar_base64
  if (kfForm.value.servicer_userid_list) {
    payload.servicer_userid_list = kfForm.value.servicer_userid_list.split(/[,，]/).map((s: string) => s.trim()).filter(Boolean)
  }

  kfSubmitting.value = true
  kfFormError.value = ''
  try {
    if (kfEditingOpenKfid.value) {
      // 换绑确认：历史 first-touch 归因不变，仅影响后续新扫码
      const ownerAccounts = kfAccountMap.value[kfOwnerConfigId.value || ''] || []
      const original = ownerAccounts.find((a: any) => a.open_kfid === kfEditingOpenKfid.value)
      if (original && original.tenant_user_id !== payload.tenant_user_id) {
        if (!confirm('之前已扫码的客户仍归属原用户，新扫码的客户会归属新用户。确定换绑吗？')) {
          kfSubmitting.value = false
          return
        }
      }
      await updateKfAccount(kfEditingOpenKfid.value, payload)
      toast.success('客服账号已更新')
      showKfModal.value = false
    } else {
      const res = await createKfAccount(payload)
      createdQrData.value = {
        open_kfid: res.open_kfid,
        name: res.name || '',
        qr_title: res.qr_title || '',
        contact_url: res.contact_url,
        qr_data_url: res.qr_data_url,
      }
      toast.success('客服账号创建成功')
    }
    await loadKfAccounts()
  } catch (e: any) {
    kfFormError.value = e.message || '操作失败'
  } finally {
    kfSubmitting.value = false
  }
}

async function handleDeleteKfAccount(_ch: any, acc: any) {
  if (!confirm(`确定删除客服账号「${acc.name}」吗？删除后已发放的二维码将失效，客户将无法继续通过该二维码进入会话。`)) return
  try {
    await deleteKfAccount(acc.open_kfid)
    toast.success('删除成功')
    await loadKfAccounts()
  } catch (e: any) {
    toast.error(e.message || '删除失败')
  }
}

const form = ref<{ channel_type: string; name: string; config: Record<string, any>; subagent_type: string }>({
  channel_type: 'wecom',
  name: '',
  config: {},
  subagent_type: ''
})

// ==================== 微信客服处理超时等待提示（渠道级配置） ====================
const DEFAULT_WAITING_MESSAGE = '我正在处理您的问题，可能需要几分钟，请稍等下。'
// delay_seconds 用 string 存储（BaseInput modelValue 为 string），提交时转 number
const wi = reactive({ enabled: true, delay_seconds: '15', message: '' })

function resetWaitingIndicator() {
  wi.enabled = true
  wi.delay_seconds = '15'
  wi.message = ''
}

function loadWaitingIndicator(cfg: Record<string, any> | undefined) {
  const w = cfg || {}
  wi.enabled = w.enabled !== false
  const d = Number(w.delay_seconds)
  wi.delay_seconds = Number.isFinite(d) && d > 0 ? String(d) : '15'
  wi.message = String(w.message || '').trim() || ''
}

const isFullscreen = ref(false)
const formInitialSnapshot = ref<Record<string, any>>({})

function toggleFullscreen() {
  isFullscreen.value = !isFullscreen.value
}

function closeModal() {
  showForm.value = false
  isFullscreen.value = false
  formInitialSnapshot.value = {}
}

// ==================== 渠道类型定义 ====================

const channelTypes = [
  { value: 'wecom', label: '企业微信', icon: '' },
  { value: 'wecom_kf', label: '企业微信客服', icon: '' },
  { value: 'wecom_personal_rpa', label: '企微个人号RPA', icon: '' },
  { value: 'dingtalk', label: '钉钉', icon: '' },
  { value: 'feishu', label: '飞书', icon: '' },
]

function channelTypeLabel(type: string) {
  const map: Record<string, string> = {
    wecom: '企业微信',
    wecom_kf: '企业微信客服',
    wecom_personal_rpa: '企微个人号RPA（会话存档）',
    dingtalk: '钉钉',
    feishu: '飞书',
  }
  return map[type] || type
}

// ==================== 字段定义（含获取位置说明） ====================

const channelFieldMap: Record<string, { key: string; label: string; placeholder: string; hint?: string; location: string; type?: 'text' | 'file' }[]> = {
  wecom: [
    { key: 'corp_id', label: '企业 ID (CorpID)', placeholder: 'ww...', hint: '以 ww 开头的字符串', location: '「我的企业」→「企业信息」' },
    { key: 'agent_id', label: '应用 AgentId', placeholder: '1000002', location: '「应用管理」→ 应用详情页' },
    { key: 'secret', label: '应用 Secret', placeholder: '', hint: '点击「查看」获取', location: '「应用管理」→ 应用详情页' },
    { key: 'token', label: '回调 Token', placeholder: '', hint: '设置 API 接收时自行设定或随机生成', location: '「接收消息」→「设置 API 接收」' },
    { key: 'encoding_aes_key', label: 'EncodingAESKey', placeholder: '43 字符', hint: '点击「随机获取」，43 字符 Base64', location: '「接收消息」→「设置 API 接收」' },
  ],
  wecom_kf: [
    { key: 'corp_id', label: '企业 ID (CorpID)', placeholder: 'ww...', hint: '以 ww 开头的字符串', location: '「我的企业」→「企业信息」' },
    { key: 'secret', label: '应用 Secret', placeholder: '', hint: '自建应用的 Secret（微信客服无独立 Secret）', location: '「应用管理」→ 自建应用详情页' },
    { key: 'token', label: '回调 Token', placeholder: '', hint: '设置 API 接收时自行设定或随机生成', location: '「微信客服」→「API」→ 回调配置' },
    { key: 'encoding_aes_key', label: 'EncodingAESKey', placeholder: '43 字符', hint: '点击「随机获取」，43 字符 Base64', location: '「微信客服」→「API」→ 回调配置' },
  ],
  // 企业微信个人号 RPA：服务端拉取模式（第一期唯一可选）
  // listen_mode 单选固定为 server（client 选项禁用灰显「即将开放」）
  // private_key 用 file input 上传 .pem 文件，读为文本后与其他字段一起提交
  wecom_personal_rpa: [
    { key: 'corp_id', label: '企业 ID (CorpID)', placeholder: 'ww...', hint: '以 ww 开头的字符串', location: '「我的企业」→「企业信息」' },
    { key: 'archive_secret', label: '会话存档 Secret', placeholder: '', hint: '会话存档专用 Secret（与自建应用 Secret 不同）', location: '「管理后台」→「会话内容存档」→「API 基本信息」' },
    { key: 'external_contact_secret', label: '客户联系 Secret', placeholder: '', hint: '用于把 wm/wo 外部联系人 ID 解析为企微可搜索姓名；未配置时不会用 ID 尝试发送', location: '「客户与上下游」→「客户联系」→「API」' },
    { key: 'private_key', label: 'RSA 私钥', placeholder: '点击上传 .pem 文件', hint: '上传后会以文本形式保存（加密存储）', location: '「会话内容存档」→「生成密钥对」下载私钥', type: 'file' },
    { key: 'token', label: '回调 Token', placeholder: '', hint: '企微后台「接收消息服务器」生成', location: '「会话内容存档」→「接收消息服务器」' },
    { key: 'encoding_aes_key', label: 'EncodingAESKey', placeholder: '43 字符', hint: '点击「随机获取」，43 字符 Base64', location: '「会话内容存档」→「接收消息服务器」' },
  ],
  dingtalk: [
    { key: 'app_key', label: 'App Key', placeholder: '', location: '「基础信息」页面' },
    { key: 'app_secret', label: 'App Secret', placeholder: '', location: '「基础信息」页面' },
    // 钉钉回调签名只用 AppSecret 做 HmacSHA256(timestamp, AppSecret)，无消息体加密，
    // 不需要 Token / EncodingAESKey（与企微/飞书不同）。后端 adapter 也忽略这两个字段。
  ],
  feishu: [
    { key: 'app_id', label: 'App ID', placeholder: 'cli_...', location: '「凭证与基础信息」页面' },
    { key: 'app_secret', label: 'App Secret', placeholder: '', location: '「凭证与基础信息」页面' },
    { key: 'verification_token', label: 'Verification Token', placeholder: '', location: '「事件与回调」' },
    { key: 'encrypt_key', label: 'Encrypt Key', placeholder: '32 字符', location: '「事件与回调」' },
  ],
}

const channelFields = computed(() => channelFieldMap[form.value.channel_type] || [])

// ==================== 配置指引（简短版，弹窗内） ====================

const quickGuideMap: Record<string, { title: string; steps: string[]; docUrl: string }> = {
  wecom: {
    title: '企业微信接入步骤',
    steps: [
      '前往企业微信管理后台 →「应用管理」→「创建应用」',
      '记录 CorpID、AgentId、Secret',
      '在应用详情页找到「接收消息」→ 点击「设置 API 接收」',
      '将下方回调地址填入 URL 栏，生成 Token 和 EncodingAESKey',
      '先在此页面保存凭证，再到企业微信后台点击保存完成验证',
    ],
    docUrl: 'https://work.weixin.qq.com/wework_admin/frame',
  },
  wecom_kf: {
    title: '企业微信客服接入步骤',
    steps: [
      '前往企业微信管理后台 →「应用管理」→「微信客服」→ 确认已开启',
      '创建自建应用，记录 CorpID 和 Secret（不需要 AgentId）',
      '在「微信客服」→「通过 API 管理」中开启并授权自建应用',
      '创建至少一个客服账号',
      '将下方回调地址填入「微信客服」→「API」→ 回调配置',
      '先在此页面保存凭证，再到企业微信后台点击保存完成验证',
    ],
    docUrl: 'https://work.weixin.qq.com/wework_admin/frame',
  },
  wecom_personal_rpa: {
    title: '企业微信个人号 RPA 接入步骤（服务端拉取模式）',
    steps: [
      '前往企业微信管理后台 →「管理后台」→「会话内容存档」→ 开通功能',
      '在「会话内容存档 → API 基本信息」记录 CorpID 和会话存档 Secret',
      '在「客户联系 → API」配置可读取客户详情的 Secret，用于解析外部联系人姓名',
      '在「会话内容存档 → 密钥管理」生成密钥对，下载 RSA 私钥 .pem 文件',
      '在「会话内容存档 → 接收消息服务器」配置回调地址（下方 URL）+ Token + EncodingAESKey',
      '先在此页面保存所有凭证（含 RSA 私钥），再到企业微信后台点击保存完成验证',
      '保存后点「验证连接」自测 5 步链路：access_token / 拉取 / RSA 解密 / 自测验签',
    ],
    docUrl: 'https://developer.work.weixin.qq.com/document/path/91360',
  },
  dingtalk: {
    title: '钉钉接入步骤',
    steps: [
      '前往钉钉开放平台 →「开发者后台」→ 创建应用',
      '启用「机器人」能力',
      '在「事件与回调」中添加 im.message.receive_v1 事件',
      '将下方回调地址填入 HTTP 回调配置',
      '记录 AppKey、AppSecret（钉钉回调签名只用 AppSecret，不需要 Token / EncodingAESKey）',
    ],
    docUrl: 'https://open.dingtalk.com/',
  },
  feishu: {
    title: '飞书接入步骤',
    steps: [
      '前往飞书开放平台 →「开发者后台」→ 创建企业自建应用',
      '启用「机器人」能力',
      '在「事件与回调」中添加 im.message.receive_v1 事件',
      '将下方回调地址填入请求地址',
      '记录 App ID、App Secret、Verification Token、Encrypt Key',
    ],
    docUrl: 'https://open.feishu.cn/',
  },
}

const currentGuide = computed(() => quickGuideMap[form.value.channel_type] || { title: '', steps: [], docUrl: '' })

// ==================== 回调地址 ====================

function getCallbackUrl(channelType: string, configId?: string): string {
  const base = window.location.origin
  if (tenant.value) {
    if (configId) {
      return `${base}/t/${tenant.value.tenant_id}/${channelType}/callback/${configId}`
    }
    return `${base}/t/${tenant.value.tenant_id}/${channelType}/callback/{config_id}`
  }
  return `${base}/${channelType}/callback`
}

function subagentTypeLabel(type: string): string {
  // 将目录名转为人可读标签
  const map: Record<string, string> = {
    'travel-consultant': '旅游咨询顾问',
    'trade-specialist': '外贸获客智能体',
    'contract-archive-review': '合同档案审查',
  }
  return map[type] || type
}

function copyUrl(url: string, id?: string) {
  navigator.clipboard.writeText(url).then(() => {
    if (id) {
      copied.value[id] = true
      setTimeout(() => { copied.value[id] = false }, 2000)
    }
  })
}

// ==================== 操作 ====================

function openAddChannel() {
  editingId.value = null
  form.value = { channel_type: 'wecom', name: '', config: {}, subagent_type: '' }
  formError.value = ''
  formInitialSnapshot.value = JSON.parse(JSON.stringify(form.value))
  resetWaitingIndicator()
  isFullscreen.value = false
  showForm.value = true
}

function editChannel(ch: any) {
  editingId.value = ch.config_id
  form.value = { channel_type: ch.channel_type, name: ch.name || '', config: { ...ch.config }, subagent_type: ch.subagent_type || '' }
  formError.value = ''
  formInitialSnapshot.value = JSON.parse(JSON.stringify(form.value))
  loadWaitingIndicator(ch.config?.waiting_indicator)
  isFullscreen.value = false
  showForm.value = true
}

// 处理 RSA 私钥文件上传：读取 .pem 文件内容为文本存入 config
function handleFileUpload(fieldKey: string, event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    form.value.config[fieldKey] = String(reader.result || '')
  }
  reader.onerror = () => {
    formError.value = `文件读取失败：${file.name}`
  }
  reader.readAsText(file)
}

async function loadChannels() {
  loading.value = true
  try {
    const res = await listChannels()
    channels.value = res.channels || []
    await loadKfAccounts()
  } catch (e) {
    console.error('加载渠道列表失败:', e)
  } finally {
    loading.value = false
  }
}

async function loadAvailableSubagents() {
  try {
    const res = await getAvailableSubagents()
    availableSubagents.value = res.subagents || []
  } catch (e) {
    console.error('加载数字员工列表失败:', e)
  }
}

async function handleSubmit() {
  submitting.value = true
  formError.value = ''
  try {
    // 渠道名称必填校验
    const trimmedName = (form.value.name || '').trim()
    if (!trimmedName) {
      formError.value = '请填写渠道名称'
      submitting.value = false
      return
    }
    const payload: Record<string, any> = {
      name: trimmedName,
      config: { ...form.value.config },
      subagent_type: form.value.subagent_type || undefined
    }
    // wecom_personal_rpa：第一期强制 listen_mode='server'（前端禁用 client，
    // 此处兜底防止用户通过 DevTools 改单选值提交 client）
    if (form.value.channel_type === 'wecom_personal_rpa') {
      payload.config.listen_mode = 'server'
    }
    // wecom_kf：写入处理超时等待提示（渠道级配置）
    if (form.value.channel_type === 'wecom_kf') {
      payload.config.waiting_indicator = wi.enabled
        ? {
            enabled: true,
            delay_seconds: Number(wi.delay_seconds) > 0 ? Number(wi.delay_seconds) : 15,
            message: wi.message || DEFAULT_WAITING_MESSAGE,
          }
        : { enabled: false }
    }
    if (editingId.value) {
      await updateChannel(editingId.value, payload as any)
    } else {
      await createChannel({ channel_type: form.value.channel_type, name: trimmedName, config: payload.config, subagent_type: payload.subagent_type } as any)
    }
    closeModal()
    await loadChannels()
  } catch (e: any) {
    formError.value = e.message || '保存失败'
  } finally {
    submitting.value = false
  }
}

async function handleSaveAndClose() {
  await handleSubmit()
}

async function handleVerify(configId: string) {
  try {
    const res = await verifyChannel(configId)
    if (res.verified) {
      toast.success('验证通过！渠道凭证有效。')
    } else {
      toast.error('验证失败: ' + (res.message || '请检查凭证配置是否正确'))
    }
    await loadChannels()
  } catch (e: any) {
    toast.error(e.message || '验证失败')
  }
}

// ==================== 生成 RSA 密钥对（wecom_personal_rpa 服务端拉取模式） ====================

const generatingKeypair = ref(false)
const showPublicKeyModal = ref(false)
const publicKeyText = ref('')
const publicKeyCopied = ref(false)
const publicKeyTextareaRef = ref<HTMLTextAreaElement | null>(null)

async function handleGenerateKeypair() {
  if (!editingId.value) {
    toast.error('请先保存配置后再生成密钥对')
    return
  }
  if (!confirm('确定要生成新的密钥对吗？如果之前已生成过，将覆盖旧私钥。')) return

  generatingKeypair.value = true
  try {
    const res = await generateChannelKeypair(editingId.value)
    if (res.success && res.public_key_raw) {
      // 用原始 PEM 文本（含真实换行）展示在 textarea 中
      publicKeyText.value = res.public_key_raw
      publicKeyCopied.value = false
      showPublicKeyModal.value = true
      // 同步更新表单中的 private_key 字段状态（实际入库的是加密后的密文，前端拿不到明文，
      // 这里仅用占位提示用户「私钥已由系统保存」，避免空字段让用户以为没保存）
      form.value.config.private_key = '***（已由系统生成并加密保存）'
      toast.success('密钥对已生成，私钥已加密保存')
    } else {
      toast.error(res.message || '生成密钥对失败')
    }
  } catch (e: any) {
    toast.error(e.message || '生成密钥对失败')
  } finally {
    generatingKeypair.value = false
  }
}

async function copyPublicKey() {
  if (!publicKeyText.value) return
  try {
    await navigator.clipboard.writeText(publicKeyText.value)
    publicKeyCopied.value = true
    setTimeout(() => { publicKeyCopied.value = false }, 2000)
  } catch {
    // 降级：选中文本供用户手动 Ctrl+C
    publicKeyTextareaRef.value?.focus()
    publicKeyTextareaRef.value?.select()
    toast.info('请按 Ctrl+C 复制选中的公钥文本')
  }
}

async function handleDelete(configId: string) {
  if (!confirm('确定要删除此渠道配置吗？删除后对应渠道将无法接收消息。')) return
  try {
    await deleteChannel(configId)
    toast.success('删除成功')
    await loadChannels()
  } catch (e: any) {
    toast.error(e.message || '删除失败')
  }
}

onMounted(() => {
  loadChannels()
  loadAvailableSubagents()
  loadTenantUsers()
})
</script>
