<template>
  <div class="min-h-full bg-surface text-default transition-colors duration-200">
    <!-- Header -->
    <div class="px-6 pt-6 pb-4">
      <div class="flex items-center justify-between mb-6">
        <div>
          <h1 class="text-xl font-bold tracking-tight" style="font-family: 'Noto Sans SC', 'DM Sans', sans-serif;">
            销售人员管理
          </h1>
          <p class="text-sm text-muted mt-0.5">销售团队与分配规则管理</p>
        </div>
        <div class="flex items-center gap-3">
          <button
            @click="showRuleModal = true"
            class="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg border border-default bg-canvas text-muted hover:bg-surface-hover transition-colors"
          >
            新增规则
          </button>
          <button
            @click="openAddRepModal"
            class="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg bg-primary-600 text-white hover:brightness-110 transition-all shadow-sm"
          >
            <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15"/></svg>
            添加销售
          </button>
        </div>
      </div>
    </div>

    <!-- Sales Reps Section -->
    <div class="px-6 pb-4">
      <h2 class="text-sm font-semibold text-muted uppercase tracking-wider mb-3">销售人员</h2>
      <div v-if="loading" class="text-center py-12 text-muted">
        <div class="flex items-center justify-center gap-2">
          <svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
          加载中...
        </div>
      </div>
      <div v-else-if="reps.length === 0" class="text-center py-12 text-muted">暂无销售人员</div>
      <div v-else class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <div
          v-for="rep in reps"
          :key="rep.rep_id"
          class="rounded-xl border border-default bg-canvas p-4 hover:border-primary-300 transition-colors"
        >
          <div class="flex items-start justify-between mb-3">
            <div>
              <p class="font-medium text-default">{{ rep.name }}</p>
              <p class="text-xs text-muted mt-0.5">{{ rep.department || '-' }} / {{ roleLabels[rep.role] || rep.role }}</p>
            </div>
            <span
              class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium"
              :class="rep.is_active ? 'bg-success-100 text-success-700' : 'bg-canvas text-muted'"
            >
              {{ rep.is_active ? '活跃' : '停用' }}
            </span>
          </div>
          <div class="grid grid-cols-2 gap-2 text-xs text-muted mb-3">
            <div>
              <span class="text-muted">负责区域:</span> {{ rep.region || '-' }}
            </div>
            <div>
              <span class="text-muted">活跃线索:</span>
              <span class="font-medium" :class="rep.active_lead_count >= rep.max_leads ? 'text-danger-500' : 'text-default'">
                {{ rep.active_lead_count }}/{{ rep.max_leads }}
              </span>
            </div>
          </div>
          <div v-if="rep.skills && rep.skills.length > 0" class="flex flex-wrap gap-1 mb-3">
            <span
              v-for="skill in rep.skills"
              :key="skill"
              class="px-1.5 py-0.5 rounded text-[10px] bg-surface border border-default text-muted"
            >
              {{ skill }}
            </span>
          </div>
          <div class="flex items-center justify-end gap-1 border-t border-default pt-3">
            <button
              @click="openEditRepModal(rep)"
              class="text-xs px-2 py-1 rounded border border-default text-muted hover:bg-surface-hover transition-colors"
            >
              编辑
            </button>
            <button
              v-if="rep.is_active"
              @click="handleDeactivateRep(rep)"
              class="text-xs px-2 py-1 rounded border border-danger-200 text-danger-600 hover:bg-danger-50 transition-colors"
            >
              停用
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Assign Rules Section -->
    <div class="px-6 pb-6">
      <h2 class="text-sm font-semibold text-muted uppercase tracking-wider mb-3">分配规则</h2>
      <div v-if="rules.length === 0" class="text-center py-8 text-muted">暂无分配规则</div>
      <div v-else class="rounded-xl border border-default overflow-hidden bg-canvas">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-default bg-surface/50">
              <th class="text-left px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">规则名称</th>
              <th class="text-left px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">类型</th>
              <th class="text-left px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">优先级</th>
              <th class="text-left px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">状态</th>
              <th class="text-left px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">自动分配</th>
              <th class="text-right px-4 py-3 font-medium text-muted text-xs uppercase tracking-wider">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="rule in rules"
              :key="rule.rule_id"
              class="border-b border-default last:border-0 hover:bg-surface-hover transition-colors"
            >
              <td class="px-4 py-3 text-default">{{ rule.name }}</td>
              <td class="px-4 py-3">
                <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-info-100 text-info-700">
                  {{ ruleTypeLabels[rule.rule_type] || rule.rule_type }}
                </span>
              </td>
              <td class="px-4 py-3 text-muted tabular-nums">{{ rule.priority }}</td>
              <td class="px-4 py-3">
                <span
                  class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium"
                  :class="rule.is_active ? 'bg-success-100 text-success-700' : 'bg-canvas text-muted'"
                >
                  {{ rule.is_active ? '启用' : '停用' }}
                </span>
              </td>
              <td class="px-4 py-3 text-muted">{{ rule.auto_assign ? '是' : '否' }}</td>
              <td class="px-4 py-3 text-right">
                <button
                  @click="handleToggleRule(rule)"
                  class="text-xs px-2 py-1 rounded border border-default text-muted hover:bg-surface-hover transition-colors"
                >
                  {{ rule.is_active ? '停用' : '启用' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Add/Edit Rep Modal -->
    <div
      v-if="showRepModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      @click.self="showRepModal = false"
    >
      <div class="bg-canvas rounded-xl border border-default shadow-2xl w-full max-w-[500px] max-h-[85vh] overflow-y-auto mx-4">
        <div class="flex items-center justify-between px-6 py-4 border-b border-default">
          <h2 class="text-base font-bold text-default">{{ editingRep ? '编辑销售人员' : '添加销售人员' }}</h2>
          <button @click="showRepModal = false" class="text-muted hover:text-default transition-colors">
            <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>
        <div class="px-6 py-4 space-y-4">
          <div>
            <label class="block text-xs text-muted mb-1">系统用户ID <span class="text-danger-500">*</span></label>
            <input v-model="repForm.user_id" :disabled="!!editingRep" class="w-full text-sm rounded-lg border border-default bg-surface text-default px-3 py-2 outline-none focus:ring-1 focus:ring-primary-600" placeholder="输入用户ID" />
          </div>
          <div>
            <label class="block text-xs text-muted mb-1">姓名 <span class="text-danger-500">*</span></label>
            <input v-model="repForm.name" class="w-full text-sm rounded-lg border border-default bg-surface text-default px-3 py-2 outline-none focus:ring-1 focus:ring-primary-600" placeholder="输入姓名" />
          </div>
          <div class="grid grid-cols-2 gap-4">
            <div>
              <label class="block text-xs text-muted mb-1">部门</label>
              <input v-model="repForm.department" class="w-full text-sm rounded-lg border border-default bg-surface text-default px-3 py-2 outline-none focus:ring-1 focus:ring-primary-600" placeholder="部门" />
            </div>
            <div>
              <label class="block text-xs text-muted mb-1">角色</label>
              <select v-model="repForm.role" class="w-full text-sm rounded-lg border border-default bg-surface text-default px-3 py-2 outline-none focus:ring-1 focus:ring-primary-600">
                <option value="sales">销售</option>
                <option value="manager">经理</option>
                <option value="director">总监</option>
              </select>
            </div>
          </div>
          <div class="grid grid-cols-2 gap-4">
            <div>
              <label class="block text-xs text-muted mb-1">最大线索配额</label>
              <input v-model.number="repForm.max_leads" type="number" class="w-full text-sm rounded-lg border border-default bg-surface text-default px-3 py-2 outline-none focus:ring-1 focus:ring-primary-600" />
            </div>
            <div>
              <label class="block text-xs text-muted mb-1">负责区域</label>
              <input v-model="repForm.region" class="w-full text-sm rounded-lg border border-default bg-surface text-default px-3 py-2 outline-none focus:ring-1 focus:ring-primary-600" placeholder="如：华东" />
            </div>
          </div>
        </div>
        <div class="px-6 py-4 border-t border-default flex justify-end gap-2">
          <button @click="showRepModal = false" class="text-sm px-4 py-2 rounded-lg border border-default text-muted hover:bg-surface-hover transition-colors">取消</button>
          <button @click="handleSaveRep" class="text-sm px-4 py-2 rounded-lg bg-primary-600 text-white hover:brightness-110 transition-all">保存</button>
        </div>
      </div>
    </div>

    <!-- Add Rule Modal -->
    <div
      v-if="showRuleModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      @click.self="showRuleModal = false"
    >
      <div class="bg-canvas rounded-xl border border-default shadow-2xl w-full max-w-[500px] max-h-[85vh] overflow-y-auto mx-4">
        <div class="flex items-center justify-between px-6 py-4 border-b border-default">
          <h2 class="text-base font-bold text-default">新增分配规则</h2>
          <button @click="showRuleModal = false" class="text-muted hover:text-default transition-colors">
            <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>
        <div class="px-6 py-4 space-y-4">
          <div>
            <label class="block text-xs text-muted mb-1">规则名称 <span class="text-danger-500">*</span></label>
            <input v-model="ruleForm.name" class="w-full text-sm rounded-lg border border-default bg-surface text-default px-3 py-2 outline-none focus:ring-1 focus:ring-primary-600" placeholder="如：华东区负载均衡" />
          </div>
          <div class="grid grid-cols-2 gap-4">
            <div>
              <label class="block text-xs text-muted mb-1">规则类型 <span class="text-danger-500">*</span></label>
              <select v-model="ruleForm.rule_type" class="w-full text-sm rounded-lg border border-default bg-surface text-default px-3 py-2 outline-none focus:ring-1 focus:ring-primary-600">
                <option value="load_balance">负载均衡</option>
                <option value="round_robin">轮询分配</option>
                <option value="region_based">按区域分配</option>
                <option value="skill_based">按技能分配</option>
              </select>
            </div>
            <div>
              <label class="block text-xs text-muted mb-1">优先级</label>
              <input v-model.number="ruleForm.priority" type="number" class="w-full text-sm rounded-lg border border-default bg-surface text-default px-3 py-2 outline-none focus:ring-1 focus:ring-primary-600" />
            </div>
          </div>
          <div>
            <label class="flex items-center gap-2 text-sm text-muted">
              <input v-model="ruleForm.auto_assign" type="checkbox" class="rounded" />
              自动分配
            </label>
          </div>
        </div>
        <div class="px-6 py-4 border-t border-default flex justify-end gap-2">
          <button @click="showRuleModal = false" class="text-sm px-4 py-2 rounded-lg border border-default text-muted hover:bg-surface-hover transition-colors">取消</button>
          <button @click="handleSaveRule" class="text-sm px-4 py-2 rounded-lg bg-primary-600 text-white hover:brightness-110 transition-all">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { followupAPI, type SalesRep, type AssignRule } from '@/api/followup'

// --- 常量 ---
const roleLabels: Record<string, string> = { sales: '销售', manager: '经理', director: '总监' }
const ruleTypeLabels: Record<string, string> = {
  load_balance: '负载均衡',
  round_robin: '轮询分配',
  region_based: '按区域分配',
  skill_based: '按技能分配',
  manual: '手动分配',
}

// --- 响应式状态 ---
const reps = ref<SalesRep[]>([])
const rules = ref<AssignRule[]>([])
const loading = ref(false)
const showRepModal = ref(false)
const showRuleModal = ref(false)
const editingRep = ref<SalesRep | null>(null)

const repForm = ref({
  user_id: '',
  name: '',
  department: '',
  role: 'sales',
  max_leads: 50,
  region: '',
})

const ruleForm = ref({
  name: '',
  rule_type: 'load_balance',
  priority: 0,
  auto_assign: true,
})

// --- 数据加载 ---
async function loadReps() {
  loading.value = true
  try {
    const res = await followupAPI.listReps()
    reps.value = res.items || []
  } catch (e) {
    console.error('加载销售人员失败:', e)
  } finally {
    loading.value = false
  }
}

async function loadRules() {
  try {
    const res = await followupAPI.listAssignRules()
    rules.value = res.items || []
  } catch (e) {
    console.error('加载分配规则失败:', e)
  }
}

// --- 销售人员操作 ---
function openAddRepModal() {
  editingRep.value = null
  repForm.value = { user_id: '', name: '', department: '', role: 'sales', max_leads: 50, region: '' }
  showRepModal.value = true
}

function openEditRepModal(rep: SalesRep) {
  editingRep.value = rep
  repForm.value = {
    user_id: rep.user_id,
    name: rep.name,
    department: rep.department || '',
    role: rep.role,
    max_leads: rep.max_leads,
    region: rep.region || '',
  }
  showRepModal.value = true
}

async function handleSaveRep() {
  if (!repForm.value.user_id || !repForm.value.name) {
    alert('请填写用户ID和姓名')
    return
  }
  try {
    if (editingRep.value) {
      await followupAPI.updateRep(editingRep.value.rep_id, {
        name: repForm.value.name,
        department: repForm.value.department || undefined,
        role: repForm.value.role,
        max_leads: repForm.value.max_leads,
        region: repForm.value.region || undefined,
      })
    } else {
      await followupAPI.createRep({
        user_id: repForm.value.user_id,
        name: repForm.value.name,
        department: repForm.value.department || undefined,
        role: repForm.value.role,
        max_leads: repForm.value.max_leads,
        region: repForm.value.region || undefined,
      })
    }
    showRepModal.value = false
    loadReps()
  } catch (e) {
    console.error('保存销售人员失败:', e)
    alert('保存失败')
  }
}

async function handleDeactivateRep(rep: SalesRep) {
  if (!confirm(`确定停用 ${rep.name}？`)) return
  try {
    await followupAPI.updateRep(rep.rep_id, { is_active: false })
    loadReps()
  } catch (e) {
    console.error('停用失败:', e)
  }
}

// --- 分配规则操作 ---
async function handleSaveRule() {
  if (!ruleForm.value.name) {
    alert('请填写规则名称')
    return
  }
  try {
    await followupAPI.createAssignRule({
      name: ruleForm.value.name,
      rule_type: ruleForm.value.rule_type,
      priority: ruleForm.value.priority,
      auto_assign: ruleForm.value.auto_assign,
    })
    showRuleModal.value = false
    ruleForm.value = { name: '', rule_type: 'load_balance', priority: 0, auto_assign: true }
    loadRules()
  } catch (e) {
    console.error('保存规则失败:', e)
    alert('保存失败')
  }
}

async function handleToggleRule(rule: AssignRule) {
  try {
    await followupAPI.updateAssignRule(rule.rule_id, { is_active: !rule.is_active })
    loadRules()
  } catch (e) {
    console.error('切换规则状态失败:', e)
  }
}

onMounted(() => {
  loadReps()
  loadRules()
})
</script>
