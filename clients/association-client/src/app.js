/**
 * Renderer 应用逻辑 —— 通过 window.associationClient IPC 桥与主进程通信。
 */

// eslint-disable-next-line no-undef
const client = window.associationClient

// ============== 状态 ==============

const state = {
  config: null, // ClientConfig | null
  cliRunning: false,
  progressMap: new Map(), // association → { steps: [], status }
  totalConsumed: 0,
}

// ============== DOM 元素 ==============

const $ = (id) => document.getElementById(id)

const pages = {
  activation: $('page-activation'),
  collect: $('page-collect'),
}

// ============== 页面切换 ==============

function showPage(name) {
  Object.values(pages).forEach((p) => p.classList.add('hidden'))
  pages[name].classList.remove('hidden')
}

// ============== 初始化 ==============

async function init() {
  // 加载配置
  state.config = await client.config.load()
  if (state.config) {
    showPage('collect')
    await refreshCredits()
  } else {
    showPage('activation')
  }

  // 订阅 CLI 事件
  client.cli.onEvent(handleCliEvent)
  client.cli.onClose(handleCliClose)

  bindEvents()
}

// ============== 激活 ==============

async function handleActivate() {
  const code = $('activation-code').value.trim()
  const serverUrl = $('server-url').value.trim()
  const clientName = $('client-name').value.trim()

  const errEl = $('activation-error')
  errEl.classList.add('hidden')

  if (!code) {
    errEl.textContent = '请输入激活码'
    errEl.classList.remove('hidden')
    return
  }

  $('btn-activate').disabled = true
  $('btn-activate').textContent = '激活中...'

  try {
    const result = await client.cli.activate(code, serverUrl, clientName)
    if (!result.ok) {
      throw new Error(result.error || '激活失败')
    }
    const data = result.data
    // 保存配置
    await client.config.save({
      bindingId: data.binding_id,
      accessToken: data.access_token || '',
      tenantId: data.tenant_id,
      tenantName: data.tenant_name || '',
      serverUrl,
      activatedAt: new Date().toISOString(),
    })
    state.config = await client.config.load()
    showPage('collect')
    await refreshCredits()
  } catch (err) {
    errEl.textContent = err.message || '激活失败'
    errEl.classList.remove('hidden')
  } finally {
    $('btn-activate').disabled = false
    $('btn-activate').textContent = '激活'
  }
}

// ============== 积分 ==============

async function refreshCredits() {
  if (!state.config) return
  try {
    const result = await client.cli.getCredits(state.config.serverUrl, state.config.accessToken)
    if (result.ok && result.data) {
      const balance = parseFloat(result.data.balance || 0)
      $('credits-balance').textContent = balance.toFixed(2)
      $('credits-display').classList.remove('hidden')

      // 余额不足警告
      if (balance <= 0) {
        $('balance-warning').classList.remove('hidden')
        $('btn-start').disabled = true
      } else {
        $('balance-warning').classList.add('hidden')
        $('btn-start').disabled = false
      }
    }
  } catch (err) {
    console.error('查询积分失败:', err)
  }
}

// ============== 收集 ==============

function parseAssociations(text) {
  return text
    .split(/[,\n，；;]/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
}

async function handleStart() {
  const text = $('associations-input').value.trim()
  const associations = parseAssociations(text)
  if (associations.length === 0) {
    alert('请输入至少一个协会名称')
    return
  }

  let outputPath = $('output-path').value.trim()
  if (!outputPath) {
    alert('请选择输出文件')
    return
  }

  // 重置 UI
  state.progressMap.clear()
  state.totalConsumed = 0
  $('progress-list').innerHTML = ''
  $('log-stream').innerHTML = ''
  $('progress-section').classList.remove('hidden')
  $('log-section').classList.remove('hidden')
  $('result-section').classList.add('hidden')
  $('btn-start').classList.add('hidden')
  $('btn-stop').classList.remove('hidden')

  state.cliRunning = true

  await client.cli.collect(associations, outputPath, state.config.serverUrl, state.config.accessToken)
}

function handleStop() {
  client.cli.kill()
}

// ============== CLI 事件处理 ==============

function handleCliEvent(evt) {
  switch (evt.event) {
    case 'start':
      appendLog('INFO', `开始收集 ${evt.associations?.length || 0} 个协会`)
      break
    case 'progress':
      handleProgressEvent(evt)
      break
    case 'billing':
      handleBillingEvent(evt)
      break
    case 'log':
      appendLog(evt.level || 'INFO', evt.message || '', evt.association || '')
      break
    case 'error':
      appendLog('ERROR', `[${evt.association || ''}] ${evt.message || evt.error_code}`)
      if (evt.session_fatal) {
        appendLog('ERROR', '会话致命错误，任务终止')
      }
      break
    case 'complete':
      handleCompleteEvent(evt)
      break
  }
}

function handleProgressEvent(evt) {
  const name = evt.association || ''
  if (!name) return

  let item = state.progressMap.get(name)
  if (!item) {
    item = { steps: [], status: 'running', element: null }
    state.progressMap.set(name, item)
    item.element = createProgressItem(name)
    $('progress-list').appendChild(item.element)
  }

  if (evt.status === 'success') {
    item.status = 'success'
  } else if (evt.status === 'failed') {
    item.status = 'failed'
  }

  if (evt.step && evt.message) {
    item.steps.push({ step: evt.step, message: evt.message, status: evt.status })
  }

  updateProgressItem(item, name)
}

function handleBillingEvent(evt) {
  state.totalConsumed += parseFloat(evt.credit_cost || 0)
  // 更新余额显示
  if (evt.balance_after != null) {
    $('credits-balance').textContent = parseFloat(evt.balance_after).toFixed(2)
    if (parseFloat(evt.balance_after) <= 0) {
      $('balance-warning').classList.remove('hidden')
    }
  }
  // 在对应协会的进度项追加消耗
  const name = evt.association || ''
  if (name) {
    const item = state.progressMap.get(name)
    if (item) {
      const stepsEl = item.element.querySelector('.progress-steps')
      const costSpan = document.createElement('div')
      costSpan.className = 'progress-step'
      costSpan.innerHTML = `<span class="step-cost">消耗 ${parseFloat(evt.credit_cost || 0).toFixed(2)} 积分</span>`
      stepsEl.appendChild(costSpan)
    }
  }
}

function handleCompleteEvent(evt) {
  state.cliRunning = false
  $('btn-start').classList.remove('hidden')
  $('btn-stop').classList.add('hidden')

  const summary = evt.summary || {}
  $('result-summary').innerHTML = `
    <div class="result-stat success"><div class="stat-num">${summary.complete || 0}</div><div class="stat-label">完整成功</div></div>
    <div class="result-stat partial"><div class="stat-num">${summary.partial || 0}</div><div class="stat-label">部分成功</div></div>
    <div class="result-stat failed"><div class="stat-num">${summary.failed || 0}</div><div class="stat-label">失败</div></div>
  `
  $('result-section').classList.remove('hidden')
  appendLog('INFO', `收集完成，共消耗 ${parseFloat(evt.total_consumed || 0).toFixed(2)} 积分`)

  // 保存输出路径供打开
  state.lastOutput = evt.output
  refreshCredits()
}

function handleCliClose(code) {
  state.cliRunning = false
  $('btn-start').classList.remove('hidden')
  $('btn-stop').classList.add('hidden')
  if (code !== 0 && code !== 2) {
    appendLog('ERROR', `CLI 进程退出（代码 ${code}）`)
  }
}

// ============== 进度项 UI ==============

function createProgressItem(name) {
  const div = document.createElement('div')
  div.className = 'progress-item'
  div.innerHTML = `
    <div class="progress-icon running">●</div>
    <div class="progress-body">
      <div class="progress-name">${escapeHtml(name)}</div>
      <div class="progress-steps"></div>
    </div>
  `
  return div
}

function updateProgressItem(item, name) {
  const icon = item.element.querySelector('.progress-icon')
  icon.className = `progress-icon ${item.status}`
  icon.textContent = item.status === 'success' ? '✓' : item.status === 'failed' ? '✗' : '●'

  const stepsEl = item.element.querySelector('.progress-steps')
  // 只追加最新的步骤（避免重绘）
  const lastStep = item.steps[item.steps.length - 1]
  if (lastStep && !stepsEl.querySelector(`[data-step="${lastStep.step}"]`)) {
    const stepDiv = document.createElement('div')
    stepDiv.className = 'progress-step'
    stepDiv.dataset.step = lastStep.step
    const statusIcon = lastStep.status === 'success' ? '✅' : lastStep.status === 'failed' ? '❌' : '🔄'
    stepDiv.textContent = `${statusIcon} ${lastStep.message}`
    stepsEl.appendChild(stepDiv)
  }
}

// ============== 日志 ==============

function appendLog(level, message, association = '') {
  const stream = $('log-stream')
  const time = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  const line = document.createElement('div')
  line.className = 'log-line'
  const prefix = association ? `[${association}] ` : ''
  line.innerHTML = `<span class="log-time">[${time}]</span> <span class="log-level-${level.toLowerCase()}">[${level}]</span> ${escapeHtml(prefix + message)}`
  stream.appendChild(line)
  stream.scrollTop = stream.scrollHeight
}

// ============== 工具 ==============

function escapeHtml(text) {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}

// ============== 事件绑定 ==============

function bindEvents() {
  $('btn-activate').addEventListener('click', handleActivate)
  $('btn-start').addEventListener('click', handleStart)
  $('btn-stop').addEventListener('click', handleStop)
  $('btn-refresh-credits').addEventListener('click', refreshCredits)
  $('btn-select-output').addEventListener('click', handleSelectOutput)
  $('btn-open-result').addEventListener('click', () => {
    if (state.lastOutput) client.system.openPath(state.lastOutput)
  })
  $('btn-new-task').addEventListener('click', () => {
    $('progress-section').classList.add('hidden')
    $('log-section').classList.add('hidden')
    $('result-section').classList.add('hidden')
    $('associations-input').value = ''
  })
}

async function handleSelectOutput() {
  const defaultName = `协会收集结果_${new Date().toISOString().slice(0, 10)}.xlsx`
  const result = await client.system.selectOutputFile(defaultName)
  if (result) {
    $('output-path').value = result
  }
}

// 启动
init()
